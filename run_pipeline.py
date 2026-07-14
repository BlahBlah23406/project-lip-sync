"""Run the LipSync dubbing pipeline for one YouTube video ID.

    python run_pipeline.py <VIDEO_ID> [--fresh]

Resumable by design (2026-07-13 rewrite). The old version kept all of its work in
a `tempfile.mkdtemp()` directory, so when the host process manager SIGKILLed a
long run every artifact died with it -- the downloaded video, the paid-for
translation, and hundreds of rendered TTS clips. Each retry restarted from zero
and could never outrun the timeout.

Now every expensive artifact is checkpointed under output/<video_id>/.work/ and
each phase skips work that is already on disk. A killed run loses at most the one
segment it was mid-way through; rerunning the exact same command resumes.

Phases (each one is skippable on resume):
  1 transcript  -> .work/transcript.json
  2 translate   -> .work/translated.json      (the expensive LLM step)
  3 download    -> .work/video.mp4
  4 extract     -> .work/original_audio.mp3
  5 tts         -> .work/segments/seg_NNNN.mp3  (+ tts_meta.json, per segment)
  6 mix         -> .work/dubbed_audio.mp3
  7 publish     -> output/<video_id>/*.mp3|.mp4|.json

For long episodes launch it detached so it does not die with the agent's tool
call:  python launch_pipeline.py <VIDEO_ID>
"""
import faulthandler
import hashlib
import json
import os
import shutil
import sys
import time
import traceback

# Force UTF-8 stdout/stderr on Windows so Bangla text does not crash cp1252.
#
# MUST use reconfigure(), not `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`.
# The wrapper form silently re-introduces block buffering on top of `python -u`,
# because a fresh TextIOWrapper defaults to write_through=False. Every log this
# pipeline wrote between 2026-07-13 12:00 and 13:09 was 0 bytes for exactly that
# reason: the output sat in an 8KB buffer and died with the process. line_buffering
# guarantees each print() reaches the log file immediately, even if we are killed
# one line later.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from captions import fetch_transcript, cluster_segments
from translator import translate_segments, retranslate_shorter, count_bangla_syllables
from dubber import download_video, generate_segment_tts
from mixer import (
    build_dubbed_audio,
    get_audio_duration,
    probe_duration_safe,
    trim_overlaps,
    available_time_for,
    MAX_SPEED,
)
from ffmpeg_paths import FFMPEG

# A finished dub must cover at least this fraction of the source video. This is
# the guard that would have caught the 2026-07-13 incident, where a poisoned
# transcript cache produced 3-minute "dubs" of 20-minute episodes.
MIN_COVERAGE = 0.5

# How much a clip may overflow its caption slot before we pay an LLM call to
# shorten the line. Below this the mixer's speed-up is imperceptible.
OVERFLOW_TRIGGER = 1.10

# Delivery speed of the Edge-TTS Bangla voice, in the units that
# count_bangla_syllables() produces (that counter over-counts true phonological
# syllables, so this number is NOT the ~4/sec of human Bangla -- it is calibrated
# against the counter). Measured 2026-07-14 over the 142 naturally-spoken clips of
# WcMYaveKv1E: median 7.00, mean 6.77. Re-measure with _measure_syllable_rate.py
# if the voice ever changes.
SYLLABLES_PER_SEC = 7.0


# --------------------------------------------------------------------------
# durable state helpers
# --------------------------------------------------------------------------

def write_json_atomic(path: str, data) -> None:
    """Write JSON via a temp file + rename, so a SIGKILL mid-write cannot corrupt it."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_json(path: str):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def text_hash(text: str, speaker: str) -> str:
    return hashlib.sha1(f"{speaker}::{text}".encode("utf-8")).hexdigest()[:16]


class Progress:
    """Heartbeat file so anyone can poll a detached run without holding the process."""

    def __init__(self, path: str, video_id: str):
        self.path = path
        self.video_id = video_id
        self.started = time.time()

    def update(self, phase: str, done: int = 0, total: int = 0, note: str = "") -> None:
        beat(f"{phase} {done}/{total}" if total else phase)
        write_json_atomic(self.path, {
            "video_id": self.video_id,
            "phase": phase,
            "done": done,
            "total": total,
            "percent": round(100.0 * done / total, 1) if total else None,
            "note": note,
            "pid": os.getpid(),
            "elapsed_seconds": round(time.time() - self.started, 1),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })


# --------------------------------------------------------------------------
# setup
# --------------------------------------------------------------------------

args = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = {a for a in sys.argv[1:] if a.startswith("--")}
VIDEO_ID = args[0] if args else "H3hijSGhdlo"
FRESH = "--fresh" in flags

VIDEO_OUTPUT_DIR = os.path.join("output", VIDEO_ID)
WORK_DIR = os.path.join(VIDEO_OUTPUT_DIR, ".work")
SEG_DIR = os.path.join(WORK_DIR, "segments")

if FRESH and os.path.exists(WORK_DIR):
    print(f"--fresh: discarding {WORK_DIR}")
    shutil.rmtree(WORK_DIR, ignore_errors=True)

for d in (VIDEO_OUTPUT_DIR, WORK_DIR, SEG_DIR):
    os.makedirs(d, exist_ok=True)

# --------------------------------------------------------------------------
# durable crash reporting
# --------------------------------------------------------------------------
# A traceback that only ever reaches a killed process's stdout buffer does not
# exist. Anything that kills this run -- a Python exception, or a hard fault like
# a segfault/stack overflow -- must leave a readable file behind.
#   .work/crash.txt      last fatal error (deleted on a clean start)
#   .work/faulthandler.log  native-level fault traces
CRASH_PATH = os.path.join(WORK_DIR, "crash.txt")
if os.path.exists(CRASH_PATH):
    os.remove(CRASH_PATH)

_fault_log = open(os.path.join(WORK_DIR, "faulthandler.log"), "w")
faulthandler.enable(file=_fault_log)


def _record_crash(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        with open(CRASH_PATH, "w", encoding="utf-8") as f:
            f.write(f"crashed at {time.strftime('%Y-%m-%dT%H:%M:%S')}\n\n{text}")
    except Exception:
        pass
    sys.stderr.write(text)
    sys.stderr.flush()


sys.excepthook = _record_crash


# --------------------------------------------------------------------------
# stall watchdog
# --------------------------------------------------------------------------
# Defence in depth for the 2026-07-13 root cause. The TTS call is now bounded
# (see dubber.py), but ffmpeg, yt-dlp and the translator LLM are all network- or
# subprocess-bound and could stall the same way. A hang is the WORST failure mode
# here: the run holds its lock, makes no progress, and looks alive, so nothing
# retries it -- it just waits to be killed from outside, which is precisely the
# bug that was mistaken for an OOM SIGKILL.
#
# So: if no phase reports progress for STALL_TIMEOUT, dump every thread's stack
# and die non-zero. All work is checkpointed, so re-running resumes; a loud,
# resumable death beats a silent infinite wait.
import threading

STALL_TIMEOUT = float(os.getenv("STALL_TIMEOUT", "1200"))  # 20 min of zero progress

_last_beat = time.time()
_beat_note = "starting"


def beat(note: str = "") -> None:
    """Tell the watchdog we are still making forward progress."""
    global _last_beat, _beat_note
    _last_beat = time.time()
    if note:
        _beat_note = note


def _watchdog() -> None:
    while True:
        time.sleep(30)
        idle = time.time() - _last_beat
        if idle < STALL_TIMEOUT:
            continue
        msg = (
            f"STALL: no progress for {idle / 60:.1f} min "
            f"(last checkpoint: {_beat_note}).\n"
            f"The run is hung, not working. Stacks of every thread follow in "
            f"faulthandler.log. Re-run the same command to resume from the last "
            f"checkpoint.\n"
        )
        try:
            with open(CRASH_PATH, "w", encoding="utf-8") as f:
                f.write(f"stalled at {time.strftime('%Y-%m-%dT%H:%M:%S')}\n\n{msg}")
            faulthandler.dump_traceback(file=_fault_log)
            _fault_log.flush()
        except Exception:
            pass
        sys.stderr.write(msg)
        sys.stderr.flush()
        os._exit(75)  # hard exit: a hung thread may be un-joinable


threading.Thread(target=_watchdog, daemon=True, name="stall-watchdog").start()

TRANSCRIPT_PATH = os.path.join(WORK_DIR, "transcript.json")
TRANSLATED_PATH = os.path.join(WORK_DIR, "translated.json")
VIDEO_PATH = os.path.join(WORK_DIR, "video.mp4")
ORIG_AUDIO_PATH = os.path.join(WORK_DIR, "original_audio.mp3")
TTS_META_PATH = os.path.join(WORK_DIR, "tts_meta.json")
DUBBED_AUDIO_PATH = os.path.join(WORK_DIR, "dubbed_audio.mp3")
MANIFEST_PATH = os.path.join(VIDEO_OUTPUT_DIR, "manifest.json")


# --------------------------------------------------------------------------
# single-run lock
# --------------------------------------------------------------------------
# Two pipelines on the same work dir race on segments/ and tts_meta.json and can
# leave half-written clips behind. This actually happened on 2026-07-13 (a retry
# loop and a manual launch ran at once), so refuse to start rather than corrupt.

import atexit
import subprocess

LOCK_PATH = os.path.join(WORK_DIR, "pid")


def _pid_alive(pid: int) -> bool:
    """True only if `pid` is a LIVE pipeline process.

    Windows recycles PIDs aggressively. A bare `tasklist | grep <pid>` check would
    happily mistake some unrelated new process that inherited the number for a
    running pipeline, and then refuse to start forever -- a self-inflicted
    deadlock. So confirm the command line really is this script.
    """
    ps = (
        f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | "
        "ForEach-Object { $_.CommandLine }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception:
        return False  # can't prove it's alive -> don't block the run
    return "run_pipeline.py" in out


# --------------------------------------------------------------------------
# Refuse to run in the foreground of an agent tool call.
#
# A tool call is SIGKILLed at its timeout and takes its whole process tree with
# it. TTS survives that (it is checkpointed per clip) but the MIX phase restarts
# from segment 0 every time -- so a foreground run can never finish, no matter how
# many times it is retried. That is precisely how 2026-07-13 was lost: the same
# episode was relaunched in the foreground over and over, resuming TTS instantly
# and then dying mid-mix, forever.
#
# The durable launchers set PIPELINE_SUPERVISED=1. Nothing else may start us.
# --------------------------------------------------------------------------
if os.environ.get("PIPELINE_SUPERVISED") != "1" and "--foreground" not in sys.argv:
    sys.exit(
        "REFUSING TO START: run_pipeline.py must be launched detached, not from a tool call.\n"
        "A tool call gets SIGKILLed at its timeout; the mix phase then restarts from zero,\n"
        "so the episode can NEVER finish. Launch it durably instead -- it survives the tool\n"
        "call and resumes from the last checkpoint:\n\n"
        f"  python launch_pipeline.py {VIDEO_ID} --supervise\n\n"
        f"Then poll (do not wait on it):  python check_progress.py {VIDEO_ID}\n\n"
        "(--foreground overrides this, for interactive debugging only.)"
    )

if os.path.exists(LOCK_PATH):
    try:
        holder = int(open(LOCK_PATH).read().strip())
    except Exception:
        holder = None
    if holder and holder != os.getpid() and _pid_alive(holder):
        sys.exit(
            f"REFUSING TO START: another pipeline (PID {holder}) is already working on "
            f"{VIDEO_ID}.\nPoll it instead:  python check_progress.py {VIDEO_ID}\n"
            f"If you are certain it is dead:  python _stop_runs.py"
        )

with open(LOCK_PATH, "w") as _f:
    _f.write(str(os.getpid()))


@atexit.register
def _release_lock():
    try:
        if os.path.exists(LOCK_PATH) and int(open(LOCK_PATH).read().strip()) == os.getpid():
            os.remove(LOCK_PATH)
    except Exception:
        pass


progress = Progress(os.path.join(WORK_DIR, "progress.json"), VIDEO_ID)
start_time = time.time()

print("=== LipSync Dubbing Pipeline (resumable) ===")
print(f"Video ID:   {VIDEO_ID}")
print(f"Work dir:   {WORK_DIR}")
print(f"Output dir: {VIDEO_OUTPUT_DIR}")
print(f"PID:        {os.getpid()}")
print(f"Started:    {time.strftime('%Y-%m-%d %H:%M:%S')}")


# --------------------------------------------------------------------------
# [1/7] transcript
# --------------------------------------------------------------------------
print("\n[1/7] Transcript...")
progress.update("transcript")

raw_segments = read_json(TRANSCRIPT_PATH)
if raw_segments:
    print(f"  Resumed {len(raw_segments)} raw segments from checkpoint")
else:
    raw_segments = fetch_transcript(VIDEO_ID)
    write_json_atomic(TRANSCRIPT_PATH, raw_segments)
    print(f"  Fetched {len(raw_segments)} raw segments")

if not raw_segments:
    sys.exit("FATAL: transcript is empty.")

transcript_span = max(s["start"] + s["duration"] for s in raw_segments)
print(f"  Transcript spans {transcript_span / 60:.1f} min")

base_segments = cluster_segments(raw_segments)
print(f"  Clustered into {len(base_segments)} segments")


# --------------------------------------------------------------------------
# [2/7] translate  (the expensive step -- never redo it)
# --------------------------------------------------------------------------
print("\n[2/7] Translating to Bangla...")
progress.update("translate", total=len(base_segments))

cached = read_json(TRANSLATED_PATH)
if cached and len(cached.get("segments", [])) == len(base_segments):
    segments = cached["segments"]
    in_tokens = cached.get("tokens", {}).get("input", 0)
    out_tokens = cached.get("tokens", {}).get("output", 0)
    print(f"  Resumed {len(segments)} translated segments from checkpoint (0 tokens spent)")
else:
    segments, in_tokens, out_tokens = translate_segments(base_segments)
    for seg, orig in zip(segments, base_segments):
        seg["original_text"] = orig.get("text", "")
    write_json_atomic(TRANSLATED_PATH, {
        "video_id": VIDEO_ID,
        "segments": segments,
        "tokens": {"input": in_tokens, "output": out_tokens},
    })
    print(f"  Translated {len(segments)} segments (in:{in_tokens} out:{out_tokens} tokens)")

# Clamp durations so no segment overruns the next one's start.
segments = trim_overlaps(segments)


# --------------------------------------------------------------------------
# [3/7] download video
# --------------------------------------------------------------------------
print("\n[3/7] Video...")
progress.update("download")

if probe_duration_safe(VIDEO_PATH) > 0:
    print(f"  Resumed cached video ({os.path.getsize(VIDEO_PATH) / 1e6:.0f} MB)")
else:
    print("  Downloading...")
    downloaded = download_video(VIDEO_ID, WORK_DIR)
    if os.path.abspath(downloaded) != os.path.abspath(VIDEO_PATH):
        shutil.move(downloaded, VIDEO_PATH)
    print(f"  Downloaded ({os.path.getsize(VIDEO_PATH) / 1e6:.0f} MB)")

video_duration = get_audio_duration(VIDEO_PATH)
print(f"  Video is {video_duration / 60:.1f} min")

# Anti-placeholder guard: a transcript that covers only a sliver of the video means
# the wrong/poisoned transcript is cached. Fail loudly rather than dub 3 minutes
# of a 20-minute episode and call it done.
coverage = transcript_span / video_duration if video_duration else 0
if coverage < MIN_COVERAGE:
    sys.exit(
        f"FATAL: transcript covers only {coverage:.0%} of the video "
        f"({transcript_span:.0f}s of {video_duration:.0f}s).\n"
        f"The cached transcript for {VIDEO_ID} is probably wrong (a test fixture?).\n"
        f"Delete transcript_cache/{VIDEO_ID}.json and rerun with --fresh."
    )
print(f"  Transcript covers {coverage:.0%} of the video - OK")

# YouTube's auto-captions can emit phantom cues PAST the end of the video. WcMYaveKv1E
# is 1371s long but its transcript runs to 1412s: 8 cues start after the video has already
# ended. They were being translated, synthesized, mixed -- and then silently thrown away by
# the `-shortest` mux, while inflating the coverage metric to a healthy-looking 103%. A
# metric that reports success for audio nobody can hear is exactly the kind that hid the
# 2026-07-13 fake dubs. Drop them, and say so.
# Segment starts are sorted, so the unreachable cues are always a trailing suffix. We
# TRUNCATE rather than filter: a segment's index IS its clip filename (seg_%04d.mp3) and
# its tts_meta key, so removing one from the middle would silently re-point every later
# segment at the wrong audio. Truncating a suffix cannot do that.
unreachable = [i for i, s in enumerate(segments) if s["start"] >= video_duration]
if unreachable:
    cut = min(unreachable)
    if unreachable != list(range(cut, len(segments))):
        sys.exit(
            f"FATAL: captions past the video end are not a contiguous suffix "
            f"({unreachable}). Segment indices are clip filenames, so dropping from the "
            f"middle would mis-assign audio. Refusing to guess."
        )
    print(f"  WARNING: {len(unreachable)} caption(s) start after the video ends "
          f"({segments[cut]['start']:.1f}s >= {video_duration:.1f}s) -- they cannot be "
          f"heard and are dropped.")
    segments = segments[:cut]
    for s in segments:  # clamp a final cue that merely overruns the end
        if s["start"] + s["duration"] > video_duration:
            s["duration"] = max(0.1, video_duration - s["start"])
    transcript_span = max(s["start"] + s["duration"] for s in segments)
    print(f"  {len(segments)} segments remain, spanning {transcript_span:.1f}s")


# --------------------------------------------------------------------------
# [4/7] extract original audio
# --------------------------------------------------------------------------
print("\n[4/7] Original audio...")
progress.update("extract_audio")

if probe_duration_safe(ORIG_AUDIO_PATH) > 0:
    print("  Resumed cached original audio")
else:
    import subprocess
    proc = subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", VIDEO_PATH,
         "-vn", "-acodec", "libmp3lame", "-ar", "44100", "-ac", "1", ORIG_AUDIO_PATH],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if proc.returncode != 0 or probe_duration_safe(ORIG_AUDIO_PATH) <= 0:
        print(f"  Warning: could not extract original audio: {proc.stderr.strip()[:300]}")
        ORIG_AUDIO_PATH = None
    else:
        print("  Extracted")


# --------------------------------------------------------------------------
# [5/7] TTS  (resumable per segment)
# --------------------------------------------------------------------------
print("\n[5/7] Generating TTS...")

tts_meta = read_json(TTS_META_PATH) or {}
total = len(segments)
generated = 0
skipped = 0
resumed = 0

# A single segment that Edge TTS refuses even after its full backoff ladder must not
# throw away a 25-minute run. Record it and keep going -- but count it, and fail hard
# at the end if too many died, so a quietly-degraded dub can never ship.
failed: list[int] = []
MAX_FAILED_FRACTION = 0.05

for i, seg in enumerate(segments):
    # Beat every segment, not every 5th: one segment can now legitimately take a
    # few minutes if Edge TTS is throttling us through its backoff ladder, and the
    # watchdog must not mistake a slow-but-working retry for a stall.
    beat(f"tts {i + 1}/{total}")

    out_path = os.path.join(SEG_DIR, f"seg_{i:04d}.mp3")
    speaker = seg.get("speaker", "SPEAKER_A")

    if not seg.get("text") or not seg["text"].strip():
        skipped += 1
        continue
    if seg.get("is_arabic_quote"):
        skipped += 1  # the original Arabic audio is sliced in later, not synthesized
        continue

    src_hash = text_hash(seg["text"], speaker)
    meta = tts_meta.get(str(i))

    # Resume: trust an existing clip only if it is real audio, was produced from this
    # exact source text, AND is NATURAL speech. A clip with a baked-in `rate` is a
    # legacy artefact of the old fit-by-speeding-up logic (up to +100%, unintelligible
    # on its own before the mixer even touched it). Those must be re-synthesized, not
    # reused -- otherwise the quality bug survives every "resumable" re-run.
    if (meta and meta.get("src_hash") == src_hash and not meta.get("rate")
            and probe_duration_safe(out_path) > 0):
        seg["text"] = meta.get("final_text", seg["text"])
        resumed += 1
        if (i + 1) % 25 == 0 or (i + 1) == total:
            print(f"  TTS {i + 1}/{total} (resumed {resumed}, new {generated})")
            progress.update("tts", i + 1, total, f"resumed={resumed} new={generated}")
        continue

    try:
        generate_segment_tts(seg["text"], out_path, speaker=speaker)

        # Fit the clip into its slot by SHORTENING THE TEXT -- never by speeding up
        # the speech. Speed is decided once, later, by the mixer (see the policy at
        # the top of mixer.py). This function used to bake an Edge-TTS `rate` of up
        # to +100% into the clip; the mixer, unable to see that, then sped the same
        # clip up AGAIN, and the two multiplied to as much as 4.0x. Clips are now
        # always synthesized at NATURAL speed, and `rate` is always None.
        available = available_time_for(segments, i)
        actual = get_audio_duration(out_path)
        applied_rate = None  # invariant: clips on disk are natural speech

        if available > 0 and actual > available * OVERFLOW_TRIGGER:
            # Budget in the units of count_bangla_syllables(). Measured on 142 natural
            # Edge-TTS Bangla clips from WcMYaveKv1E: the voice delivers ~7.0 of these
            # units per second (median). The old budget of 3.5/sec asked for text twice
            # as short as it needed to be; the LLM could not hit it, the result was
            # thrown away, and the original long line got brute-force sped up instead.
            max_syllables = int(available * SYLLABLES_PER_SEC * MAX_SPEED)
            before = count_bangla_syllables(seg["text"])
            print(f"  Segment {i}: {actual / available:.2f}x too long -> reprompting for "
                  f"<={max_syllables} syllables (now {before})", flush=True)

            shorter = retranslate_shorter(seg, max_syllables)
            # Accept ANY strictly shorter line. The old code demanded the reprompt land
            # under budget*1.2 and DISCARDED it otherwise -- so a line that came back 30%
            # shorter (a real win) was thrown away in favour of speeding up the long one.
            if shorter and shorter != seg["text"] and count_bangla_syllables(shorter) < before:
                candidate = out_path + ".cand.mp3"
                generate_segment_tts(shorter, candidate, speaker=speaker)
                cand_dur = probe_duration_safe(candidate)
                if 0 < cand_dur < actual:
                    os.replace(candidate, out_path)
                    seg["text"] = shorter
                    actual = cand_dur
                    print(f"    -> {before} to {count_bangla_syllables(shorter)} syllables, "
                          f"clip {cand_dur:.2f}s (slot {available:.2f}s)", flush=True)
                elif os.path.exists(candidate):
                    os.remove(candidate)

            # Whatever is left over is NOT fixed here. If the line still overflows, the
            # mixer speeds it up to at most MAX_SPEED and lets the remainder run long.

        if probe_duration_safe(out_path) <= 0:
            raise RuntimeError("produced no audio")

    except Exception as e:
        # Do NOT kill the run. Drop any partial file so the resume logic cannot mistake
        # it for a good clip, record the failure, and move on.
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        tts_meta.pop(str(i), None)
        failed.append(i)
        print(f"  Segment {i} FAILED after all retries ({e!r}) -- continuing without it "
              f"[{len(failed)} failed so far]", flush=True)
        write_json_atomic(TTS_META_PATH, tts_meta)
        continue

    # Checkpoint after EVERY segment. The clip on disk is the durable artifact; this
    # records which source text produced it so a resume can trust it.
    tts_meta[str(i)] = {
        "src_hash": src_hash,
        "final_text": seg["text"],
        "rate": applied_rate,
        "speaker": speaker,
    }
    write_json_atomic(TTS_META_PATH, tts_meta)
    generated += 1

    if (i + 1) % 5 == 0 or (i + 1) == total:
        print(f"  TTS {i + 1}/{total} (resumed {resumed}, new {generated})")
        progress.update("tts", i + 1, total, f"resumed={resumed} new={generated}")

print(f"  TTS done: {generated} generated, {resumed} resumed, {skipped} skipped (empty/Arabic), "
      f"{len(failed)} failed")

voiced = generated + resumed
if voiced == 0:
    sys.exit("FATAL: no TTS audio was produced.")

# Tolerate a few dropouts; refuse to ship an audibly gutted dub.
if failed:
    speakable = voiced + len(failed)
    frac = len(failed) / speakable
    print(f"  {len(failed)} segment(s) failed permanently: {failed}")
    if frac > MAX_FAILED_FRACTION:
        sys.exit(
            f"FATAL: {len(failed)} of {speakable} speakable segments ({frac:.0%}) failed TTS, "
            f"over the {MAX_FAILED_FRACTION:.0%} limit.\n"
            f"The dub would have audible holes. Edge TTS is probably throttling hard --\n"
            f"wait a few minutes and rerun to fill only the missing segments:\n"
            f"  python launch_pipeline.py {VIDEO_ID}\n"
            f"(Everything already rendered is kept; only the {len(failed)} gaps are retried.)"
        )
    print(f"  {frac:.1%} failed, under the {MAX_FAILED_FRACTION:.0%} limit - continuing")


# --------------------------------------------------------------------------
# [6/7] mix
# --------------------------------------------------------------------------
print("\n[6/7] Mixing dubbed audio...")
progress.update("mix", 0, voiced)

stats = build_dubbed_audio(
    segments,
    SEG_DIR,
    ORIG_AUDIO_PATH,
    total_duration=max(transcript_span, video_duration),
    out_path=DUBBED_AUDIO_PATH,
    work_dir=WORK_DIR,
    progress_cb=lambda d, t: progress.update("mix", d, t, "rendering segments"),
    # The mixer must know what speed (if any) Edge TTS baked into each clip, or it
    # will speed an already-fast clip up a second time. This is the wiring that
    # makes the 4.0x compounding bug structurally impossible.
    tts_meta=tts_meta,
)
print(f"  Mixed {stats['segments_mixed']} segments -> {stats['output_seconds'] / 60:.1f} min")

# Anti-ALIGNMENT guard. Coverage and speed both looked healthy on the dub that shipped
# 18.7s out of sync, because neither of them measures alignment. This one does: `offset`
# is how late each Bangla line lands relative to the English caption it translates.
# A NEGATIVE offset means the dub plays EARLY, which the layout makes arithmetically
# impossible -- so if it ever appears, the mixer is broken, not merely badly tuned.
timing = stats["timing"]
if timing["min_offset"] < -0.01:
    sys.exit(
        f"FATAL: {timing['segments']} segments, worst EARLY offset "
        f"{timing['min_offset']:.2f}s. The dub would play before the audio it translates. "
        f"Refusing to publish. Work dir kept: {WORK_DIR}"
    )
print(f"  Alignment: {timing['on_time']}/{timing['segments']} lines within 0.25s of their "
      f"caption, {timing['late_over_3s']} more than 3s late, worst {timing['max_offset']:+.2f}s")

# Anti-placeholder guard #2: the finished track must actually cover the episode.
out_coverage = stats["output_seconds"] / video_duration if video_duration else 0
if out_coverage < MIN_COVERAGE:
    sys.exit(
        f"FATAL: dubbed audio is {stats['output_seconds']:.0f}s but the video is "
        f"{video_duration:.0f}s ({out_coverage:.0%} coverage).\n"
        f"Refusing to publish a placeholder-length dub. Work dir kept: {WORK_DIR}"
    )
print(f"  Dub covers {out_coverage:.0%} of the video - OK")


# --------------------------------------------------------------------------
# [7/7] publish
# --------------------------------------------------------------------------
print("\n[7/7] Publishing outputs...")
progress.update("publish")

audio_out = os.path.join(VIDEO_OUTPUT_DIR, f"{VIDEO_ID}_dubbed.mp3")
video_out = os.path.join(VIDEO_OUTPUT_DIR, f"{VIDEO_ID}_dubbed.mp4")
transcript_out = os.path.join(VIDEO_OUTPUT_DIR, f"{VIDEO_ID}_transcript.json")

shutil.copy(DUBBED_AUDIO_PATH, audio_out)
print(f"  Audio: {audio_out}")

from mixer import mux_video_with_dubbed_audio
mux_video_with_dubbed_audio(VIDEO_PATH, DUBBED_AUDIO_PATH, video_out)
print(f"  Video: {video_out}")

write_json_atomic(transcript_out, {
    "video_id": VIDEO_ID,
    "segments": segments,
    "tokens": {"input": in_tokens, "output": out_tokens},
    "time_taken": round(time.time() - start_time, 2),
})
print(f"  Transcript: {transcript_out}")

elapsed = round(time.time() - start_time, 2)
manifest = {
    "video_id": VIDEO_ID,
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "elapsed_seconds": elapsed,
    "source_video_seconds": round(video_duration, 2),
    "dubbed_audio_seconds": stats["output_seconds"],
    "coverage": round(out_coverage, 3),
    "segment_count": len(segments),
    "segments_voiced": voiced,
    "segments_mixed": stats["segments_mixed"],
    "segments_failed": len(failed),
    "failed_indices": failed,
    # Durable proof of ALIGNMENT, not just of length. A manifest that only records
    # coverage is how a dub that was 18.7s out of sync passed every check it had.
    "timing": stats["timing"],
    "timeline": stats["timeline"],
    "speed": stats["speed"],
    "tokens": {"input": in_tokens, "output": out_tokens},
    "artifacts": {
        "dubbed_audio": os.path.basename(audio_out),
        "dubbed_video": os.path.basename(video_out),
        "transcript": os.path.basename(transcript_out),
    },
    "files": {
        "dubbed_audio": os.path.getsize(audio_out),
        "dubbed_video": os.path.getsize(video_out),
        "transcript": os.path.getsize(transcript_out),
    },
}
write_json_atomic(MANIFEST_PATH, manifest)
print(f"  Manifest: {MANIFEST_PATH}")

progress.update("done", voiced, voiced, f"{out_coverage:.0%} coverage")
print(f"\n=== Complete in {elapsed / 60:.1f} min ===")
print(f"Outputs in: {VIDEO_OUTPUT_DIR}/")
print(f"Work dir kept for resume/debug: {WORK_DIR}  (safe to delete)")
