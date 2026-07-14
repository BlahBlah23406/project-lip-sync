"""Verify a delivered dub is real -- run this BEFORE sending anything to the user.

    python verify_output.py            # check every video in output/
    python verify_output.py <VIDEO_ID>

On 2026-07-13 three "dubbed episodes" were delivered that were actually 3-minute
placeholder TTS of 20-minute episodes: a poisoned transcript cache made the
pipeline dub a test fixture, and nothing ever compared the result against the
source. This script is that missing comparison.

Checks per video:
  1. dubbed MP3 and MP4 exist and are non-trivial
  2. dubbed audio covers >= 50% of the source video's duration   <-- the real test
  3. the MP4's audio track is as long as its video track
  4. the dub is not byte-identical in length to another episode's (placeholder tell)

Exit code 0 = all good, 1 = at least one video failed.
"""
import json
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from mixer import probe_duration_safe
from ffmpeg_paths import FFPROBE
import subprocess

OUTPUT_DIR = "output"
MIN_COVERAGE = 0.5


def stream_duration(path: str, stream: str) -> float:
    """Duration of a specific stream ('v:0' or 'a:0'), 0.0 if absent."""
    proc = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", stream,
         "-show_entries", "stream=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip().splitlines()[0])
    except Exception:
        return 0.0


def check(video_id: str) -> tuple[bool, list[str], float]:
    d = os.path.join(OUTPUT_DIR, video_id)
    mp3 = os.path.join(d, f"{video_id}_dubbed.mp3")
    mp4 = os.path.join(d, f"{video_id}_dubbed.mp4")
    problems = []

    if not os.path.exists(mp3):
        return False, [f"missing {mp3}"], 0.0
    if not os.path.exists(mp4):
        problems.append(f"missing {mp4}")

    dub_len = probe_duration_safe(mp3)
    if dub_len <= 0:
        return False, ["dubbed MP3 is unreadable or empty"], 0.0

    # Source length: prefer the work-dir video, else the delivered MP4's video track.
    src = os.path.join(d, ".work", "video.mp4")
    src_len = probe_duration_safe(src) if os.path.exists(src) else 0.0
    if src_len <= 0 and os.path.exists(mp4):
        src_len = stream_duration(mp4, "v:0")

    if src_len <= 0:
        problems.append("could not determine source video length -- cannot verify coverage")
        return False, problems, dub_len

    coverage = dub_len / src_len
    if coverage < MIN_COVERAGE:
        problems.append(
            f"dub is {dub_len / 60:.1f} min but source is {src_len / 60:.1f} min "
            f"({coverage:.0%} coverage) -- THIS LOOKS LIKE A PLACEHOLDER"
        )

    if os.path.exists(mp4):
        v = stream_duration(mp4, "v:0")
        a = stream_duration(mp4, "a:0")
        if a > 0 and v > 0 and a < v * MIN_COVERAGE:
            problems.append(
                f"MP4 audio track ({a / 60:.1f} min) is far shorter than its video "
                f"({v / 60:.1f} min) -- most of the episode is silent"
            )

    man = os.path.join(d, "manifest.json")
    if os.path.exists(man):
        with open(man, encoding="utf-8") as f:
            m = json.load(f)
        print(f"  manifest: {m.get('segments_mixed')} segments mixed, "
              f"{m.get('coverage', 0):.0%} coverage recorded")

    print(f"  dub {dub_len / 60:.1f} min / source {src_len / 60:.1f} min "
          f"= {coverage:.0%} coverage")
    return not problems, problems, dub_len


def main() -> int:
    if len(sys.argv) > 1:
        ids = [sys.argv[1]]
    else:
        # Leading underscore = housekeeping dir (e.g. the quarantined fakes), not a video.
        ids = sorted(
            d for d in os.listdir(OUTPUT_DIR)
            if os.path.isdir(os.path.join(OUTPUT_DIR, d)) and not d.startswith("_")
        )
    if not ids:
        print("nothing to verify")
        return 0

    results = {}
    failed = []
    for vid in ids:
        print(f"\n=== {vid} ===")
        ok, problems, dub_len = check(vid)
        results[vid] = dub_len
        for p in problems:
            print(f"  FAIL: {p}")
        if ok:
            print("  PASS")
        else:
            failed.append(vid)

    # Identical dub lengths across different episodes = placeholder audio.
    lengths = {}
    for vid, dur in results.items():
        if dur > 0:
            lengths.setdefault(round(dur, 1), []).append(vid)
    for dur, vids in lengths.items():
        if len(vids) > 1:
            print(f"\nSUSPICIOUS: {', '.join(vids)} all have identical dub length "
                  f"({dur}s). Different episodes cannot dub to the same length -- "
                  f"these are almost certainly placeholders.")
            failed.extend(v for v in vids if v not in failed)

    print(f"\n{'=' * 50}")
    if failed:
        print(f"FAILED: {', '.join(sorted(set(failed)))}")
        print("Do NOT deliver these. Rerun: python launch_pipeline.py <VIDEO_ID> --fresh")
        return 1
    print(f"All {len(ids)} output(s) verified real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
