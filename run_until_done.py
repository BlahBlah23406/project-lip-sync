"""Run an episode to completion, relaunching if it dies. Detached; returns immediately.

    python run_until_done.py <VIDEO_ID> [<VIDEO_ID> ...]

This is the command to use for the three episodes. It is only safe because the
pipeline is resumable: every relaunch picks up exactly where the last one stopped
(cached video, cached translation, per-segment TTS clips), so a retry costs
nothing but the segments that were actually missing.

It supervises: run the pipeline, wait, and if it exits without reaching the `done`
phase, run it again -- up to MAX_ATTEMPTS. Edge TTS throttling is the usual reason
a run dies, and it clears after a cool-off, so we back off between attempts.

Videos are processed strictly one at a time (parallel runs would just deepen the
throttling that causes the failures in the first place).
"""
import json
import os
import subprocess
import sys
import time

os.chdir(os.path.dirname(os.path.abspath(__file__)))

MAX_ATTEMPTS = 8
COOLDOWN = 120  # seconds between attempts -- lets an Edge TTS throttle expire


def phase_of(video_id: str) -> str | None:
    p = os.path.join("output", video_id, ".work", "progress.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("phase")
    except Exception:
        return None


def is_done(video_id: str) -> bool:
    """Done means: the pipeline said so AND a manifest with real coverage exists."""
    if phase_of(video_id) != "done":
        return False
    man = os.path.join("output", video_id, "manifest.json")
    if not os.path.exists(man):
        return False
    try:
        with open(man, encoding="utf-8") as f:
            return json.load(f).get("coverage", 0) >= 0.5
    except Exception:
        return False


def run_once(video_id: str, log_path: str) -> int:
    """Run the pipeline in the foreground of THIS supervisor (which is itself detached).

    PIPELINE_SUPERVISED is the pipeline's proof that it was started by a durable
    launcher and not from inside an agent tool call (which would be SIGKILLed).
    """
    env = {**os.environ, "PIPELINE_SUPERVISED": "1"}
    with open(log_path, "ab") as log:
        proc = subprocess.run(
            [sys.executable, "-u", "run_pipeline.py", video_id],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
        )
    return proc.returncode


def process(video_id: str) -> bool:
    if is_done(video_id):
        print(f"[{video_id}] already complete -- skipping", flush=True)
        return True

    for attempt in range(1, MAX_ATTEMPTS + 1):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        log_path = os.path.join("logs", f"{video_id}-{stamp}-try{attempt}.log")
        print(f"[{video_id}] attempt {attempt}/{MAX_ATTEMPTS} -> {log_path}", flush=True)

        rc = run_once(video_id, log_path)

        if is_done(video_id):
            print(f"[{video_id}] COMPLETE on attempt {attempt}", flush=True)
            return True

        crash = os.path.join("output", video_id, ".work", "crash.txt")
        reason = ""
        if os.path.exists(crash):
            reason = open(crash, encoding="utf-8", errors="replace").read()[:400]
        print(f"[{video_id}] attempt {attempt} ended rc={rc} at phase "
              f"{phase_of(video_id)}. {reason}", flush=True)

        if attempt < MAX_ATTEMPTS:
            print(f"[{video_id}] cooling off {COOLDOWN}s before resuming...", flush=True)
            time.sleep(COOLDOWN)

    print(f"[{video_id}] GAVE UP after {MAX_ATTEMPTS} attempts", flush=True)
    return False


def main() -> int:
    ids = sys.argv[1:]
    if not ids:
        print("usage: python run_until_done.py <VIDEO_ID> [<VIDEO_ID> ...]")
        return 2

    os.makedirs("logs", exist_ok=True)
    results = {}
    for video_id in ids:
        results[video_id] = process(video_id)

    print("\n=== supervisor summary ===", flush=True)
    for video_id, ok in results.items():
        print(f"  {video_id}: {'COMPLETE' if ok else 'FAILED'}", flush=True)
    print("Now run:  python verify_output.py", flush=True)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
