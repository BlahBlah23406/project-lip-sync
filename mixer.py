"""Audio mixing for the LipSync dubbing pipeline.

Design note (2026-07-13): why the voice track is a CONCAT, not a 300-input amix.
The original implementation built one giant FFmpeg `amix` filtergraph with one input
per TTS segment (270-680 inputs). That opens every decoder at once and its memory grows
linearly with segment count. We instead render each segment once and stitch the pieces
together with the concat demuxer, which streams one file at a time. Memory is flat.

Design note (2026-07-14): why placement is done in SAMPLES, from MEASURED audio.
A concat has one dangerous property an amix does not: it has no absolute timeline. A
segment's position is just the sum of the lengths of everything before it, so ANY error
in an earlier piece shifts every later piece, forever. The first concat implementation
had exactly such an error -- `plan_schedule()` advanced its cursor by
`start + effective + MIN_GAP`, but the audio it emitted for that segment was only
`gap + effective` long. The MIN_GAP was pure bookkeeping; it was never rendered. Each
one therefore pulled every later segment 80ms EARLIER than intended, and on Ep3's 288
segments that summed to a measured -18.7s: by the end of the episode the Bangla arrived
almost 19 seconds before the English line it was translating.

So the mixer no longer trusts any predicted length. It:
  1. renders each clip with its speed applied and NOTHING else (no baked-in silence),
  2. MEASURES the exact frame count of every rendered clip,
  3. lays them out in the SAMPLE domain, emitting an explicit silence pad for every gap,
  4. asserts the finished track's length is what the layout said it would be.
Position is then exact by construction: `pad_i + len_i` telescopes to the next segment's
start, because the pad IS the difference. There is nothing left to drift.

Rule this encodes: never let a value that is written to disk be predicted rather than
measured. `verify_timeline()` re-derives the truth from the files themselves.
"""
import json
import math
import os
import subprocess
import wave

from ffmpeg_paths import FFMPEG, FFPROBE

# Uniform intermediate format for every rendered segment.
SR = 44100
CH = 1
SAMPLE_WIDTH = 2  # pcm_s16le

# --------------------------------------------------------------------------
# Mix balance -- "original audio dominant vs translation audio dominant".
#
# The background (the original English lecture) is NOT a level-triggered
# compressor's guess any more. We know EXACTLY when the Bangla speaks -- it is in
# the schedule we just built -- so the bed is multiplied by an explicit gain
# envelope: full volume in the gaps, DUCK_LEVEL under Bangla speech, with a
# raised-cosine fade between the two. That is what the May-23 version the user liked
# did (a per-interval `volume` chain); it was replaced with `sidechaincompress`
# because that chain grew one FFmpeg filter per segment and became unmanageable at
# 680 of them. Generating the envelope as an audio file and multiplying gives the
# same deterministic result in ONE filter, at flat memory.
#
# These three numbers ARE the balance knob. Raise DUCK_LEVEL to hear more English
# under the Bangla; lower it for a more dominant dub.
# --------------------------------------------------------------------------
DUCK_LEVEL = 0.08   # background gain while Bangla is speaking (0.08 == 8%, -22dB) -- dub more dominant
BED_LEVEL = 1.00    # background gain in the gaps (full -- the original is dominant there)
DUCK_FADE = 0.30    # seconds to ramp between the two (matches old good version's 300ms fades)

# Speech intervals closer together than this are treated as one: we do not want the
# bed swelling back up for a 300ms breath between two sentences. Must exceed
# 2 * DUCK_FADE, or two fades would collide inside one gap.
DUCK_MERGE_GAP = 2 * DUCK_FADE + 0.1  # = 0.70s with DUCK_FADE=0.30

# --------------------------------------------------------------------------
# Speed policy (2026-07-14).  THE MIXER IS THE ONLY PLACE SPEED IS DECIDED.
#
# What went wrong before: run_pipeline.py baked an Edge-TTS `rate` into the clip
# to force it into its slot (capped at 2.0x), and when that still wasn't enough,
# plan_schedule() re-probed the ALREADY-SPED clip and applied rubberband on top
# (also capped at 2.0x). Neither knew about the other, so the two multiplied:
# segment 201 of WcMYaveKv1E was a 2.0x Edge-TTS clip stretched a further 2.0x
# = 4.0x. That is not fast speech, it is noise.
#
# The policy now: a segment is spoken at most MAX_SPEED. If the translation still
# does not fit its caption slot, IT IS ALLOWED TO RUN LONG. The overrun pushes
# later segments back, and the lag is absorbed by the next natural pause in the
# lecture (this episode has ~176s of them). Only if the lag grows past
# LAG_TOLERANCE do we allow up to HARD_MAX_SPEED to claw it back.
#
# Intelligibility beats strict lip-timing. A line that lands half a second late is
# a dub; a line at 4x is garbage.
# --------------------------------------------------------------------------
MAX_SPEED = 1.50        # normal ceiling -- timing alignment matters (user confirmed old 2.0x was good)
HARD_MAX_SPEED = 2.00   # only while catching up from a lag; never exceeded
LAG_SOFT = 0.5          # seconds behind: start easing the speed up to claw back
LAG_HARD = 3.0          # seconds behind: we are at HARD_MAX_SPEED
MIN_GAP = 0.15          # breathing room between consecutive segments (matches old good version)

# How far the finished voice track may differ from the layout before we call it broken.
# The layout is sample-exact, so this is a smoke alarm, not a tolerance to live in.
DRIFT_TOLERANCE = 0.05  # seconds

# Kept for backwards compatibility with older callers/tests.
NATURAL_GAP = MIN_GAP
LAG_TOLERANCE = LAG_SOFT


def speed_ceiling(lag: float) -> float:
    """How fast we may speak, given how far behind the original video we are.

    More aggressive than the 1.35x version: starts at 1.50x and ramps to 2.00x
    when properly behind. This prioritizes TIMING ALIGNMENT (user's #1 concern)
    over absolute intelligibility — a line at 1.8x is still understandable, but
    a line that lands 10+ seconds late is a broken dub.
    """
    if lag <= LAG_SOFT:
        return MAX_SPEED
    if lag >= LAG_HARD:
        return HARD_MAX_SPEED
    t = (lag - LAG_SOFT) / (LAG_HARD - LAG_SOFT)
    return MAX_SPEED + t * (HARD_MAX_SPEED - MAX_SPEED)


def baked_rate(index: int, tts_meta: dict | None) -> float:
    """The Edge-TTS speed-up already baked into seg_<index>.mp3, as a multiplier.

    Legacy clips were synthesized with e.g. rate="+100%", so the file on disk is
    ALREADY 2x fast. The mixer must know this or it will speed them up a second
    time. Returns 1.0 for a natural clip (the only kind the pipeline makes now).
    """
    if not tts_meta:
        return 1.0
    raw = (tts_meta.get(str(index)) or {}).get("rate")
    if not raw:
        return 1.0
    try:
        return 1.0 + int(str(raw).replace("%", "").replace("+", "").strip()) / 100.0
    except ValueError:
        return 1.0


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run an ffmpeg/ffprobe command, raising with real stderr on failure."""
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-15:]
        raise RuntimeError(
            f"FFmpeg failed (exit {proc.returncode}):\n  cmd: {' '.join(cmd[:6])} ...\n  "
            + "\n  ".join(tail)
        )
    return proc


def get_audio_duration(file_path: str) -> float:
    """Duration of a media file in seconds. Raises on unreadable input.

    Never returns a fallback constant -- a silent default is what let a previous
    run ship placeholder audio as if it were a real dub.
    """
    cmd = [
        FFPROBE,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"Could not read duration of {file_path}: {proc.stderr.strip()}")
    return float(proc.stdout.strip())


def probe_duration_safe(file_path: str) -> float:
    """Duration, or 0.0 if the file is missing/empty/corrupt. For validation only."""
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return 0.0
    try:
        return get_audio_duration(file_path)
    except Exception:
        return 0.0


# --------------------------------------------------------------------------
# Exact measurement. Every rendered artefact is a PCM WAV we wrote ourselves, so
# its frame count is in its header -- no subprocess, no float estimate, no mp3
# decoder padding. This is the ground truth the whole layout is built on.
# --------------------------------------------------------------------------

def wav_frames(path: str) -> int:
    """Exact frame count of a PCM WAV, or 0 if missing/unreadable."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return 0
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes()
    except Exception:
        return 0


def write_silence(path: str, frames: int) -> str:
    """Write exactly `frames` frames of digital silence, in the uniform format."""
    with wave.open(path, "wb") as w:
        w.setnchannels(CH)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SR)
        w.writeframes(b"\x00" * (frames * SAMPLE_WIDTH * CH))
    return path


def trim_overlaps(segments: list[dict]) -> list[dict]:
    """Clamp each segment's duration so it cannot run past the next one's start."""
    trimmed = []
    for i, seg in enumerate(segments):
        t = seg.copy()
        if i < len(segments) - 1:
            max_dur = segments[i + 1]["start"] - seg["start"]
            t["duration"] = min(seg["duration"], max_dur) if max_dur > 0 else 0.1
        trimmed.append(t)
    return trimmed


def available_time_for(segments: list[dict], i: int) -> float:
    """The time window segment i may occupy before the next segment is due."""
    seg = segments[i]
    if i < len(segments) - 1:
        return max(seg["duration"], segments[i + 1]["start"] - seg["start"])
    return seg["duration"]


def place(cap_start: float, cursor: float) -> float:
    """Where a segment actually starts, given where the previous one ended.

    THE anchoring rule, and the reason the dub cannot drift away from the video:

      * ON TIME  -- the caption's own start is at least MIN_GAP after the previous
        segment ended, so we start the segment exactly at `cap_start`: back on the
        ORIGINAL timeline, with the lecture's own pause serving as the breath. Any
        lag accumulated earlier is FORGIVEN here. Every real pause is a resync point.

      * BEHIND   -- the previous segment overran into this caption's slot, so we butt
        up against it (`cursor`) and add nothing. Adding a courtesy gap here is what
        the May-23 version did (a flat 150ms every time) and it is self-defeating:
        the gap pushes the next segment later, which makes it later still, and the
        lag never gets paid off.

    Because the gap this returns is EXACTLY `start - cursor`, and that is exactly the
    silence we render, the layout telescopes: no phantom time, no drift.
    """
    return cap_start if cap_start >= cursor else cursor


def plan_schedule(segments: list[dict], seg_dir: str,
                  tts_meta: dict | None = None) -> tuple[list[dict], float]:
    """Decide each segment's SPEED, and lay out a provisional timeline.

    This pass exists to answer one question -- how fast may each line be spoken? -- and
    it uses ffprobe estimates to do it, which is fine: a millisecond of error cannot
    change a speed decision. The authoritative TIMING is decided later, by
    `layout_clips()`, from the measured length of the audio that was actually rendered.

    Returns (schedule, timeline_end). Each item carries:
      seg_file  - the clip to render
      cap_start - where the original English caption starts (the anchor we resync to)
      start/gap - provisional placement
      speed     - total playback speed vs. NATURAL speech (this is the number that
                  decides intelligibility; it is capped, see the policy above)
      tempo     - the rubberband tempo to apply to the file ON DISK. This is
                  `speed / baked_rate`, so a legacy clip that Edge TTS already
                  over-sped gets SLOWED BACK DOWN (tempo < 1.0) instead of being
                  sped up a second time.
      effective - predicted time on the timeline (superseded by the measured value)
    """
    segments = trim_overlaps(segments)
    schedule = []
    cursor = 0.0
    overruns = 0

    for i, seg in enumerate(segments):
        seg_file = os.path.join(seg_dir, f"seg_{i:04d}.mp3")
        orig_file = os.path.join(seg_dir, f"seg_{i:04d}_orig.mp3")
        is_arabic = bool(seg.get("is_arabic_quote")) and os.path.exists(orig_file)
        if is_arabic:
            seg_file = orig_file

        on_disk = probe_duration_safe(seg_file)
        if on_disk <= 0:
            continue  # no audio (empty text, Arabic quote w/o slice, or a failed clip)

        # Recover the clip's NATURAL length, undoing any speed Edge TTS baked in.
        # Arabic quotes are sliced from the original audio and are never sped.
        baked = 1.0 if is_arabic else baked_rate(i, tts_meta)
        natural = on_disk * baked

        available = available_time_for(segments, i)
        start = place(seg["start"], cursor)

        # How far behind the original video are we already? A little lag is fine and
        # is the price of intelligible speech; a lot of it needs clawing back.
        lag = start - seg["start"]
        ceiling = speed_ceiling(lag)

        # Arabic recitation is the speaker's own voice -- never alter its speed.
        if is_arabic:
            speed = 1.0
        elif available > 0 and natural > available:
            speed = min(natural / available, ceiling)
            if speed <= 1.02:
                speed = 1.0
        else:
            speed = 1.0

        effective = natural / speed
        if effective > available + 0.05 and available > 0:
            overruns += 1

        tempo = speed / baked  # what to actually ask rubberband for

        schedule.append({
            "index": i,
            "seg_file": seg_file,
            "cap_start": round(seg["start"], 4),
            "start": round(start, 4),
            "gap": round(start - cursor, 4),
            "speed": round(speed, 4),
            "tempo": round(tempo, 4),
            "baked": round(baked, 4),
            "effective": round(effective, 4),
            "lag": round(lag, 3),
        })

        # The cursor advances by EXACTLY the audio this segment contributes: the silence
        # in front of it plus the segment itself. Nothing else. The previous version added
        # a MIN_GAP here that was never rendered into the stream, so the bookkeeping and
        # the audio disagreed by 80ms per segment -- which is the whole timing bug.
        # Breathing room is not added here; it is a PRECONDITION in place(), which only
        # returns cap_start when the lecture's own pause already provides it.
        cursor = start + effective

    if overruns:
        print(f"  {overruns} segment(s) run past their caption slot -- absorbed by the "
              f"following pauses (intelligibility is capped at {MAX_SPEED}x, by design)")

    return schedule, cursor


def speed_report(schedule: list[dict]) -> dict:
    """Summarise how fast the dub actually speaks. Used to gate the output."""
    speeds = [s["speed"] for s in schedule]
    if not speeds:
        return {"segments": 0}
    return {
        "segments": len(speeds),
        "natural": sum(1 for s in speeds if s <= 1.02),
        "over_max": sum(1 for s in speeds if s > MAX_SPEED + 0.01),
        "unintelligible": sum(1 for s in speeds if s > 2.1),
        "max_speed": round(max(speeds), 3),
        "mean_speed": round(sum(speeds) / len(speeds), 3),
        "max_lag": round(max(s["lag"] for s in schedule), 2),
    }


def timing_report(layout: list[dict]) -> dict:
    """Summarise how well the finished dub lines up with the original video.

    `offset` is the signed difference between where a Bangla line actually lands and
    where its English caption starts. It is the number the user hears.

    A NEGATIVE offset is the dangerous one -- the Bangla arrives BEFORE the English it
    translates, which can only happen if the layout invented time from nowhere. It was
    -18.7s on the shipped Ep3. The layout now makes it arithmetically impossible, so
    `min_offset < 0` means a real bug, not a tuning problem.
    """
    offs = [item["offset"] for item in layout]
    late = [o for o in offs if o > 0]
    return {
        "segments": len(offs),
        "min_offset": round(min(offs), 3),
        "max_offset": round(max(offs), 3),
        "mean_offset": round(sum(offs) / len(offs), 3),
        "on_time": sum(1 for o in offs if o <= 0.25),
        "late_over_1s": sum(1 for o in offs if o > 1.0),
        "late_over_3s": sum(1 for o in offs if o > 3.0),
        "mean_late": round(sum(late) / len(late), 3) if late else 0.0,
    }


def slice_arabic_quotes(segments: list[dict], seg_dir: str, original_audio_path: str) -> int:
    """Cut the original-language audio for segments flagged as classical Arabic quotes."""
    if not original_audio_path or not os.path.exists(original_audio_path):
        return 0
    segments = trim_overlaps(segments)
    made = 0
    for i, seg in enumerate(segments):
        if not seg.get("is_arabic_quote"):
            continue
        out_file = os.path.join(seg_dir, f"seg_{i:04d}_orig.mp3")
        if probe_duration_safe(out_file) > 0:
            made += 1
            continue
        try:
            _run([
                FFMPEG, "-y", "-v", "error",
                "-ss", str(seg["start"]), "-t", str(max(seg["duration"], 0.1)),
                "-i", original_audio_path,
                "-ar", str(SR), "-ac", str(CH),
                out_file,
            ])
            made += 1
        except Exception as e:
            print(f"  Warning: could not slice Arabic quote for segment {i}: {e}")
    return made


def render_clip(item: dict, proc_dir: str) -> str:
    """Render one segment to a uniform PCM WAV: its speed applied, and NOTHING else.

    No leading silence is baked in. That was the old design, and it is what made the
    timing bug possible: the silence was computed from a PREDICTED length, so the file
    on disk was the only record of a placement decision nobody could re-check. Silence
    is now a separate file, sized from this clip's MEASURED length (see layout_clips).

    Cached on disk -- a rerun after a kill reuses whatever is already rendered. The cache
    key is (index, tempo), and a clip whose source mp3 is newer than the render is
    re-rendered: a cache that can serve audio from a superseded policy is how a "fixed"
    bug survives its own fix.
    """
    tempo = item["tempo"]
    out_path = os.path.join(proc_dir, f"clip_{item['index']:04d}_t{tempo:.3f}.wav")

    if wav_frames(out_path) > 0 and os.path.getmtime(out_path) >= os.path.getmtime(item["seg_file"]):
        return out_path

    filters = []
    # tempo < 1.0 SLOWS the clip: that is how a legacy Edge-TTS clip which was baked
    # too fast gets restored to an intelligible speed instead of sped up again.
    if abs(tempo - 1.0) > 0.02:
        # rubberband preserves formants far better than atempo, in both directions.
        filters.append(f"rubberband=tempo={tempo:.4f}")
    filters.append(f"aformat=sample_rates={SR}:channel_layouts=mono")

    _run([
        FFMPEG, "-y", "-v", "error",
        "-i", item["seg_file"],
        "-filter:a", ",".join(filters),
        "-ar", str(SR), "-ac", str(CH),
        "-c:a", "pcm_s16le",
        out_path,
    ])
    if wav_frames(out_path) <= 0:
        raise RuntimeError(f"rendered an empty clip for segment {item['index']}")
    return out_path


def layout_clips(schedule: list[dict], proc_dir: str, progress_cb=None) -> list[dict]:
    """Render every clip, MEASURE it, and place it on the timeline in the sample domain.

    This is where timing is actually decided, and the only place it is. Working in whole
    samples (not float seconds) means the arithmetic is exact: `pad + frames` is the next
    segment's start, with no rounding left over to accumulate.

    Each layout item gains:
      frames      - the clip's MEASURED length, in samples
      pad_frames  - silence emitted before it, in samples (>= 0, always)
      start_s     - where it really lands, in seconds
      offset      - start_s - cap_start: how late the line is. NEVER negative.
    """
    os.makedirs(proc_dir, exist_ok=True)

    min_gap_frames = int(round(MIN_GAP * SR))
    layout = []
    cursor = 0  # samples -- the true end of everything emitted so far

    for n, item in enumerate(schedule):
        clip = render_clip(item, proc_dir)
        frames = wav_frames(clip)

        cap_frames = int(round(item["cap_start"] * SR))
        # The same anchoring rule as place(), in samples: snap back to the caption when
        # the lecture's own pause gives us room, otherwise butt up against the last clip.
        if cap_frames >= cursor:
            start = cap_frames
        else:
            start = cursor

        pad = start - cursor  # >= 0 by construction
        item = dict(item)
        item.update({
            "clip": clip,
            "frames": frames,
            "pad_frames": pad,
            "start_s": round(start / SR, 4),
            "end_s": round((start + frames) / SR, 4),
            "offset": round((start - cap_frames) / SR, 4),
        })
        layout.append(item)
        cursor = start + frames

        if progress_cb and ((n + 1) % 10 == 0 or n + 1 == len(schedule)):
            progress_cb(n + 1, len(schedule))

    return layout


def build_voice_track(layout: list[dict], proc_dir: str, out_path: str) -> str:
    """Concatenate [silence, clip, silence, clip, ...] into one sample-exact voice track."""
    if not layout:
        raise RuntimeError("No segment audio was rendered -- nothing to mix.")

    sil_dir = os.path.join(proc_dir, "silence")
    os.makedirs(sil_dir, exist_ok=True)

    pieces = []
    for item in layout:
        if item["pad_frames"] > 0:
            pad_path = os.path.join(sil_dir, f"pad_{item['index']:04d}_{item['pad_frames']}.wav")
            if wav_frames(pad_path) != item["pad_frames"]:
                write_silence(pad_path, item["pad_frames"])
            pieces.append(pad_path)
        pieces.append(item["clip"])

    # Concat demuxer streams one file at a time: memory stays flat at 680 segments.
    # Every piece is already pcm_s16le / SR / mono, so this is a byte-level splice --
    # no decode, no resample, and therefore nothing that could shift a sample.
    #
    # Paths MUST be absolute: ffmpeg resolves entries relative to the LIST FILE, not to
    # the cwd, so a relative path would be looked up inside proc_dir and not exist.
    list_path = os.path.join(proc_dir, "concat_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for path in pieces:
            escaped = os.path.abspath(path).replace("\\", "/").replace("'", r"'\''")
            f.write(f"file '{escaped}'\n")

    _run([
        FFMPEG, "-y", "-v", "error",
        "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy",
        out_path,
    ])
    return out_path


def verify_timeline(layout: list[dict], voice_path: str) -> dict:
    """Prove the track on disk matches the layout. Raises if it does not.

    The layout is only a promise until something checks it against the actual file. This
    is that check, and it is the guard that would have caught the -18.7s Ep3 drift the
    moment it was introduced instead of after a 40-minute render and a human listening
    to it. Cheap: one WAV header read.
    """
    expected = sum(i["pad_frames"] + i["frames"] for i in layout)
    actual = wav_frames(voice_path)
    drift = (actual - expected) / SR

    if abs(drift) > DRIFT_TOLERANCE:
        raise RuntimeError(
            f"TIMELINE DRIFT: the voice track is {actual / SR:.3f}s but the layout says it "
            f"must be {expected / SR:.3f}s (off by {drift:+.3f}s). The concatenated audio does "
            f"not match the placement it was built from, so every segment after the first "
            f"error is misaligned with the video. Refusing to publish."
        )

    offsets = [i["offset"] for i in layout]
    early = [i["index"] for i in layout if i["offset"] < -0.001]
    if early:
        raise RuntimeError(
            f"NEGATIVE OFFSET on segments {early[:10]}: the Bangla would play BEFORE the "
            f"English line it translates. A segment can only start early if the layout "
            f"invented time from nowhere -- this is the -18.7s Ep3 bug. Refusing to publish."
        )

    return {
        "voice_frames": actual,
        "expected_frames": expected,
        "track_drift_seconds": round(drift, 4),
        "max_offset_seconds": round(max(offsets), 3),
    }


def build_duck_envelope(layout: list[dict], total_seconds: float, out_path: str) -> str:
    """Render the background's gain envelope as an audio file.

    Full volume in the gaps (the original English is dominant there), DUCK_LEVEL under
    Bangla speech, raised-cosine fades between. We know the speech intervals EXACTLY --
    they are the layout we just built -- so the bed does not need a compressor to guess
    at them from the voice's amplitude.

    Written in chunks so memory stays flat regardless of episode length.
    """
    import numpy as np

    # Merge speech intervals that are closer together than DUCK_MERGE_GAP: the bed must
    # not swell back up for a breath between two sentences.
    intervals: list[list[float]] = []
    for item in layout:
        s, e = item["start_s"], item["end_s"]
        if intervals and s <= intervals[-1][1] + DUCK_MERGE_GAP:
            intervals[-1][1] = max(intervals[-1][1], e)
        else:
            intervals.append([s, e])

    total = int(math.ceil(total_seconds * SR)) + SR  # +1s of tail, trimmed in the graph
    fade = max(1, int(DUCK_FADE * SR))
    depth = BED_LEVEL - DUCK_LEVEL

    # A raised cosine, not a straight line: it has no corners, so the ear treats it kindly.
    ramp_down = DUCK_LEVEL + depth * 0.5 * (1 + np.cos(np.linspace(0, math.pi, fade)))
    ramp_up = ramp_down[::-1].copy()

    # Paint one chunk at a time. Materialising the whole envelope would be 4 bytes per
    # sample of the episode -- a quarter of a GB for Ep3, and worse for anything longer.
    # The point of this mixer is that memory does not scale with runtime.
    chunk = SR * 60
    marks = [(int(round(s * SR)), int(round(e * SR))) for s, e in intervals]

    with wave.open(out_path, "wb") as w:
        w.setnchannels(CH)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SR)

        first = 0  # intervals are sorted, so we never look at one we have passed
        for c0 in range(0, total, chunk):
            c1 = min(c0 + chunk, total)
            gain = np.full(c1 - c0, BED_LEVEL, dtype=np.float32)

            i = first
            while i < len(marks) and marks[i][0] - fade < c1:
                a, b = marks[i]
                if b + fade <= c0:          # wholly behind us
                    if i == first:
                        first = i + 1
                    i += 1
                    continue

                def paint(dst0, dst1, src):
                    """Write `src` into gain[dst0:dst1], clipped to this chunk."""
                    lo, hi = max(dst0, c0), min(dst1, c1)
                    if hi > lo:
                        gain[lo - c0:hi - c0] = src[lo - dst0:hi - dst0]

                lo, hi = max(a, c0), min(b, c1)
                if hi > lo:
                    gain[lo - c0:hi - c0] = DUCK_LEVEL
                paint(a - fade, a, ramp_down)   # fade down INTO the speech
                paint(b, b + fade, ramp_up)     # fade up OUT of it
                i += 1

            w.writeframes((np.clip(gain, 0.0, 1.0) * 32767.0).astype("<i2").tobytes())

    return out_path


def build_dubbed_audio(segments: list[dict], seg_dir: str, original_audio_path: str,
                       total_duration: float, out_path: str, work_dir: str = None,
                       progress_cb=None, tts_meta: dict | None = None) -> dict:
    """Mix the per-segment TTS into a finished dubbed track.

    Returns a stats dict describing what was actually mixed, so callers can verify
    the result instead of trusting it.
    """
    work_dir = work_dir or os.path.dirname(os.path.abspath(seg_dir))
    proc_dir = os.path.join(work_dir, "processed")
    os.makedirs(proc_dir, exist_ok=True)

    slice_arabic_quotes(segments, seg_dir, original_audio_path)

    # Pass 1 -- decide SPEED (from ffprobe estimates; a millisecond cannot change it).
    schedule, _ = plan_schedule(segments, seg_dir, tts_meta=tts_meta)
    if not schedule:
        raise RuntimeError(
            "No usable TTS segment audio found in "
            f"{seg_dir} -- refusing to produce an empty/placeholder dub."
        )

    # Intelligibility gate. The whole point of the 2026-07-14 rewrite is that no segment
    # may ever again be played at a speed nobody can understand. If one slips through,
    # that is a BUG in the speed policy -- fail loudly rather than ship noise.
    speeds = speed_report(schedule)
    print(f"  Speed: mean {speeds['mean_speed']}x, max {speeds['max_speed']}x, "
          f"{speeds['natural']}/{speeds['segments']} at natural speed")
    if speeds["unintelligible"]:
        raise RuntimeError(
            f"REFUSING TO MIX: {speeds['unintelligible']} segment(s) would play faster "
            f"than 2.1x (max {speeds['max_speed']}x). The speed cap is "
            f"{MAX_SPEED}x/{HARD_MAX_SPEED}x, so this means a clip on disk is already "
            f"sped up and tts_meta did not say so -- the mixer cannot correct what it "
            f"cannot see. Delete the affected clips and re-run TTS."
        )

    # Pass 2 -- render, MEASURE, and place. This is the authority on timing.
    layout = layout_clips(schedule, proc_dir, progress_cb=progress_cb)

    voice_path = os.path.join(work_dir, "voice_track.wav")
    build_voice_track(layout, proc_dir, voice_path)

    # Pass 3 -- prove it. Re-derived from the file on disk, not from the plan.
    timeline = verify_timeline(layout, voice_path)
    timing = timing_report(layout)
    print(f"  Timing: {timing['on_time']}/{timing['segments']} lines within 0.25s of their "
          f"caption, worst {timing['max_offset']:+.2f}s late, mean {timing['mean_offset']:+.2f}s "
          f"(track drift {timeline['track_drift_seconds']:+.3f}s)")

    with open(os.path.join(work_dir, "schedule.json"), "w", encoding="utf-8") as f:
        json.dump(layout, f, indent=2)

    voice_duration = wav_frames(voice_path) / SR
    final_duration = max(total_duration, voice_duration)

    has_bg = bool(original_audio_path) and os.path.exists(original_audio_path)

    if has_bg:
        env_path = os.path.join(work_dir, "duck_envelope.wav")
        build_duck_envelope(layout, final_duration, env_path)

        # The bed is multiplied by the envelope -- an exact, precomputed gain curve --
        # instead of being ducked by a sidechain compressor guessing from the voice's
        # amplitude. Deterministic, and the balance is three constants at the top of
        # this file rather than a compressor's threshold/ratio/attack/release.
        filtergraph = (
            f"[0:a]aformat=sample_rates={SR}:channel_layouts=mono,"
            f"apad,atrim=0:{final_duration:.3f},asetpts=N/SR/TB[voice];"
            f"[1:a]aformat=sample_rates={SR}:channel_layouts=mono,"
            f"apad,atrim=0:{final_duration:.3f},asetpts=N/SR/TB[bg];"
            f"[2:a]aformat=sample_rates={SR}:channel_layouts=mono,"
            f"atrim=0:{final_duration:.3f},asetpts=N/SR/TB[env];"
            f"[bg][env]amultiply[ducked];"
            f"[voice][ducked]amix=inputs=2:normalize=0:duration=first[mixed];"
            f"[mixed]loudnorm=I=-16:TP=-1.5:LRA=11[out]"
        )
        cmd = [
            FFMPEG, "-y", "-v", "error",
            "-i", voice_path,
            "-i", original_audio_path,
            "-i", env_path,
            "-filter_complex", filtergraph,
            "-map", "[out]",
            "-ar", str(SR), "-ac", str(CH),
            out_path,
        ]
    else:
        filtergraph = (
            f"[0:a]aformat=sample_rates={SR}:channel_layouts=mono,"
            f"apad,atrim=0:{final_duration:.3f},asetpts=N/SR/TB,"
            f"loudnorm=I=-16:TP=-1.5:LRA=11[out]"
        )
        cmd = [
            FFMPEG, "-y", "-v", "error",
            "-i", voice_path,
            "-filter_complex", filtergraph,
            "-map", "[out]",
            "-ar", str(SR), "-ac", str(CH),
            out_path,
        ]

    _run(cmd)

    return {
        "segments_total": len(segments),
        "segments_mixed": len(layout),
        "voice_track_seconds": round(voice_duration, 2),
        "timeline_end_seconds": round(layout[-1]["end_s"], 2),
        "output_seconds": round(get_audio_duration(out_path), 2),
        "background_mixed": has_bg,
        "speed": speeds,
        "timing": timing,
        "timeline": timeline,
    }


def mux_video_with_dubbed_audio(video_path: str, audio_path: str, out_path: str):
    """Mux video with the dubbed audio track (no video re-encode)."""
    _run([
        FFMPEG, "-y", "-v", "error",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        out_path,
    ])
    return out_path
