"""Poll a detached pipeline run without holding the process open.

    python check_progress.py            # all videos
    python check_progress.py <VIDEO_ID> # one video

Prints the current phase, percentage, whether the process is still alive, and the
last few log lines. Safe to call from a heartbeat -- it returns instantly.
"""
import json
import os
import subprocess
import sys
import time

# The log contains Bangla text and symbols like the warning sign. Windows' default
# cp1252 stdout raises UnicodeEncodeError on those, which crashed this poller --
# the one tool you need most while a run is in trouble. Never let the monitoring
# tool be less robust than the thing it monitors.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.chdir(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = "output"


def is_alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
    ).stdout
    return str(pid) in out


def latest_log(video_id: str) -> str | None:
    log_dir = "logs"
    if not os.path.isdir(log_dir):
        return None
    logs = sorted(
        (f for f in os.listdir(log_dir) if f.startswith(f"{video_id}-")),
        reverse=True,
    )
    return os.path.join(log_dir, logs[0]) if logs else None


def report(video_id: str) -> None:
    work = os.path.join(OUTPUT_DIR, video_id, ".work")
    prog_path = os.path.join(work, "progress.json")

    print(f"\n=== {video_id} ===")

    if not os.path.exists(prog_path):
        print("  no run recorded yet")
        return

    with open(prog_path, encoding="utf-8") as f:
        p = json.load(f)

    pid = p.get("pid")
    alive = is_alive(pid) if pid else False
    age = time.time() - os.path.getmtime(prog_path)

    pct = f"{p['percent']}%" if p.get("percent") is not None else "-"
    print(f"  phase:    {p.get('phase')}  {p.get('done')}/{p.get('total')}  ({pct})")
    print(f"  note:     {p.get('note') or '-'}")
    print(f"  elapsed:  {p.get('elapsed_seconds', 0) / 60:.1f} min")
    print(f"  updated:  {age:.0f}s ago")

    if p.get("phase") == "done":
        print("  status:   COMPLETE")
    elif alive:
        print(f"  status:   RUNNING (pid {pid})")
    elif age > 300:
        print(f"  status:   DEAD (pid {pid} gone, no update for {age / 60:.0f} min)")
        print(f"            -> resume with: python launch_pipeline.py {video_id}")
    else:
        print(f"  status:   process {pid} not found (may be starting or just died)")

    manifest = os.path.join(OUTPUT_DIR, video_id, "manifest.json")
    if os.path.exists(manifest):
        with open(manifest, encoding="utf-8") as f:
            m = json.load(f)
        print(f"  output:   {m.get('dubbed_audio_seconds', 0) / 60:.1f} min dub, "
              f"{m.get('coverage', 0):.0%} coverage of source")

    log = latest_log(video_id)
    if log:
        lines = open(log, encoding="utf-8", errors="replace").read().splitlines()
        tail = [l for l in lines if l.strip()][-4:]
        if tail:
            print(f"  log ({log}):")
            for line in tail:
                print(f"    | {line}")


def main() -> int:
    if len(sys.argv) > 1:
        report(sys.argv[1])
        return 0

    if not os.path.isdir(OUTPUT_DIR):
        print("no output directory yet")
        return 0

    ids = [
        d for d in os.listdir(OUTPUT_DIR)
        if os.path.isdir(os.path.join(OUTPUT_DIR, d, ".work"))
    ]
    if not ids:
        print("no runs found")
        return 0
    for vid in sorted(ids):
        report(vid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
