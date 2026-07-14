"""Independently verify that the Bangla dub lines up with the original English.

    python verify_timing.py <VIDEO_ID> [--start 300] [--dur 180] [--model small]

This is the check the user asked for: transcribe each language SEPARATELY, with a tool
that knows nothing about our pipeline, and see whether the two line up in time. It exists
because every number the pipeline reported about itself was healthy while the shipped Ep3
was 18.7 seconds out of sync -- self-reported coverage and speed said nothing about
ALIGNMENT, so nothing caught it. A dub can only be trusted if something outside the mixer
has listened to it.

Two independent measurements, strongest first:

  1. ALIGNMENT DRIFT (energy cross-correlation).  No transcription, no model, no opinion:
     take the speech-energy envelope of the original English and of the Bangla voice track,
     and find the time shift that best lines them up -- in each successive sub-window. If
     the dub is aligned, the best shift is ~0 everywhere. If it drifts, the shift MARCHES
     (this is what -18.7s looked like: 0s at the start, -19s at the end). This is the
     number that decides pass/fail.

  2. TRANSCRIPT COMPARISON (Whisper).  Transcribe the English original and the Bangla dub
     independently and print them side by side, so a human can see that the Bangla line
     about a subject arrives when the English line about that subject does.

Exit code 0 = aligned, 1 = misaligned.
"""
import argparse
import os
import subprocess
import sys
import wave

import numpy as np

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from ffmpeg_paths import FFMPEG
from mixer import SR

# A dub whose alignment wanders by more than this is audibly out of sync.
MAX_ABS_SHIFT = 1.0      # seconds: worst acceptable |shift| in any sub-window
MAX_DRIFT_SPAN = 0.75    # seconds: worst acceptable SPREAD of shift across the window
                         # (a marching shift is the drift bug's signature)

ENV_RATE = 100           # energy-envelope samples per second


def extract(src: str, dst: str, start: float, dur: float) -> str:
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-ss", str(start), "-t", str(dur),
         "-i", src, "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", dst],
        check=True,
    )
    return dst


def envelope(wav_path: str) -> np.ndarray:
    """Speech-energy envelope at ENV_RATE Hz, normalised. Language-agnostic on purpose."""
    with wave.open(wav_path, "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)
    data /= 32768.0

    step = SR // ENV_RATE
    n = len(data) // step
    rms = np.sqrt(np.mean(data[: n * step].reshape(n, step) ** 2, axis=1))

    # Compress the dynamic range: we care WHEN there is speech, not how loud it is. This
    # is what lets an English envelope be compared with a Bangla one at all.
    env = np.log1p(rms * 100.0)
    env -= env.mean()
    sd = env.std()
    return env / sd if sd > 0 else env


def best_shift(a: np.ndarray, b: np.ndarray, max_shift_s: float = 25.0) -> tuple[float, float]:
    """The shift (seconds) that best aligns envelope b onto a, and the correlation there.

    Positive = the dub is LATE (b must move earlier to match a).
    """
    max_lag = int(max_shift_s * ENV_RATE)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]

    best, best_r = 0, -2.0
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x, y = a[lag:], b[: n - lag]
        else:
            x, y = a[: n + lag], b[-lag:]
        if len(x) < ENV_RATE * 5:      # need >=5s of overlap to mean anything
            continue
        r = float(np.dot(x, y) / len(x))
        if r > best_r:
            best_r, best = r, lag
    return best / ENV_RATE, best_r


def transcribe(wav_path: str, language: str, model_name: str) -> list[dict]:
    import whisper
    model = whisper.load_model(model_name)
    result = model.transcribe(wav_path, language=language, verbose=False)
    return [
        {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
        for s in result["segments"]
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("--start", type=float, default=300.0, help="window start, seconds")
    ap.add_argument("--dur", type=float, default=180.0, help="window length, seconds")
    ap.add_argument("--model", default="small", help="whisper model (tiny/base/small/medium)")
    ap.add_argument("--no-whisper", action="store_true",
                    help="drift measurement only (fast; no transcription)")
    args = ap.parse_args()

    vid = args.video_id
    work = os.path.join("output", vid, ".work")
    english = os.path.join(work, "original_audio.mp3")
    bangla = os.path.join(work, "voice_track.wav")     # the dub voice, without the bed
    dubbed = os.path.join("output", vid, f"{vid}_dubbed.mp3")

    for p in (english, bangla):
        if not os.path.exists(p):
            print(f"FATAL: missing {p}. Run the pipeline for {vid} first.")
            return 2

    tmp = os.path.join(work, "verify")
    os.makedirs(tmp, exist_ok=True)
    en_wav = extract(english, os.path.join(tmp, "en.wav"), args.start, args.dur)
    bn_wav = extract(bangla, os.path.join(tmp, "bn.wav"), args.start, args.dur)

    print(f"=== Timing verification: {vid} ===")
    print(f"window: {args.start:.0f}s -> {args.start + args.dur:.0f}s "
          f"({args.dur / 60:.1f} min)")
    print(f"english : {english}")
    print(f"bangla  : {bangla}  (the dub's voice track -- no background bed)\n")

    # ---------------------------------------------------------------- 1. drift
    en_env = envelope(en_wav)
    bn_env = envelope(bn_wav)

    overall, r = best_shift(en_env, bn_env)
    print("[1] ALIGNMENT DRIFT  (speech-energy cross-correlation -- no transcription)")
    print(f"  best overall shift : {overall:+.2f}s   (correlation {r:.3f})")
    print("  positive = the Bangla runs LATE; negative = it runs EARLY (the -18.7s bug)\n")

    # The single most important measurement: does the shift MARCH across the window?
    # A constant offset is a mix-level nit. A growing one means the dub is drifting away
    # from the video and will be unlistenable by the end.
    chunks = 6
    per = len(en_env) // chunks
    shifts = []
    print(f"  {'sub-window':>18}  {'shift':>8}  {'corr':>6}")
    for i in range(chunks):
        a = en_env[i * per:(i + 1) * per]
        b = bn_env[i * per:(i + 1) * per]
        if len(a) < ENV_RATE * 10:
            continue
        s, rr = best_shift(a, b, max_shift_s=25.0)
        shifts.append(s)
        t0 = args.start + i * per / ENV_RATE
        t1 = args.start + (i + 1) * per / ENV_RATE
        print(f"  {t0:7.0f}s -{t1:6.0f}s  {s:+8.2f}s  {rr:6.3f}")

    span = max(shifts) - min(shifts) if shifts else 0.0
    worst = max(abs(s) for s in shifts) if shifts else 0.0
    print(f"\n  worst |shift|      : {worst:.2f}s   (limit {MAX_ABS_SHIFT:.2f}s)")
    print(f"  shift SPREAD       : {span:.2f}s   (limit {MAX_DRIFT_SPAN:.2f}s)")
    print("  A spread near zero means the dub holds its alignment across the window.")
    print("  A marching shift means it is drifting away from the video.\n")

    ok = worst <= MAX_ABS_SHIFT and span <= MAX_DRIFT_SPAN

    # ---------------------------------------------------- 2. transcript compare
    if not args.no_whisper:
        print(f"[2] INDEPENDENT TRANSCRIPTION  (whisper '{args.model}', CPU -- slow)")
        print("  transcribing the ENGLISH original...", flush=True)
        en_seg = transcribe(en_wav, "en", args.model)
        print("  transcribing the BANGLA dub...", flush=True)
        bn_seg = transcribe(bn_wav, "bn", args.model)

        print(f"\n  english lines: {len(en_seg)}   bangla lines: {len(bn_seg)}\n")
        print("  Each English line, and the Bangla line that lands nearest it in time.")
        print("  If the dub is aligned, the gap is small and does NOT grow down the page.\n")
        print(f"  {'EN @':>8}  {'BN @':>8}  {'gap':>7}  english / bangla")
        print(f"  {'-' * 8}  {'-' * 8}  {'-' * 7}  {'-' * 50}")

        gaps = []
        for e in en_seg:
            if not bn_seg:
                break
            near = min(bn_seg, key=lambda b: abs(b["start"] - e["start"]))
            gap = near["start"] - e["start"]
            gaps.append(gap)
            t_en = args.start + e["start"]
            t_bn = args.start + near["start"]
            print(f"  {t_en:8.1f}  {t_bn:8.1f}  {gap:+7.2f}  {e['text'][:48]}")
            print(f"  {'':8}  {'':8}  {'':7}  {near['text'][:48]}")

        if gaps:
            mean_gap = sum(gaps) / len(gaps)
            print(f"\n  mean gap {mean_gap:+.2f}s, worst {max(gaps, key=abs):+.2f}s")
            print("  (Whisper segments the two languages differently, so a sub-second gap")
            print("   is normal. What matters is that it does not GROW.)")

    print("\n" + "=" * 60)
    if ok:
        print("PASS -- the dub holds its alignment with the original across the window.")
        return 0
    print("FAIL -- the dub does NOT stay aligned with the original.")
    if span > MAX_DRIFT_SPAN:
        print(f"  The shift moves by {span:.2f}s across the window: it is DRIFTING, which")
        print("  means the voice track's timeline disagrees with the video's. Check")
        print("  mixer.layout_clips() -- every segment's `offset` must be >= 0 and small.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
