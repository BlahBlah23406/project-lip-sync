"""Automatic deterministic quality tester for LipSync dubbed videos.

Evaluates dubbed video quality across two primary dimensions:
1. Content Quality:
   - Bangla language script purity (detects untranslated English or leaked prompts)
   - Formatting cleanliness (detects leaked speaker tags, duration markers, markdown)
   - Speech density & completeness (detects placeholder/corrupted YouTube captions)
   - Repetition & hallucination checks (detects auto-caption loop artifacts)

2. Audio & Timing Quality:
   - Timing alignment and caption synchronization (on-time ratio, offset limits)
   - Speech speed and intelligibility (natural rate ratio, speed ceiling adherence)
   - Audio coverage and stream completeness vs original source video
   - Audio level integrity (true peak, clipping detection, RMS volume presence)

Produces an overall score (0-100), letter grade (A-F), pass/fail verdict, and
detailed diagnostic metrics. Attached directly to manifest.json for web review
and automatic regeneration gating.

Usage:
    python quality_tester.py <VIDEO_ID>
    python quality_tester.py --all
    python quality_tester.py <VIDEO_ID> --threshold 75 --regenerate-if-below
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from typing import Any

from ffmpeg_paths import FFMPEG, FFPROBE

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
DEFAULT_THRESHOLD = float(os.getenv("QUALITY_THRESHOLD", "75.0"))


def _clean_text(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def get_audio_levels(audio_path: str) -> dict[str, float]:
    """Extract RMS level and max volume using ffmpeg volumedetect filter."""
    if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        return {"max_volume": -99.0, "mean_volume": -99.0, "clipping": False}
    try:
        cmd = [
            FFMPEG, "-v", "info", "-ss", "10", "-t", "60", "-i", audio_path,
            "-af", "volumedetect", "-vn", "-sn", "-dn",
            "-f", "null", "/dev/null"
        ]
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        output = proc.stderr
        max_vol_m = re.search(r"max_volume:\s*(-?[\d\.]+)\s*dB", output)
        mean_vol_m = re.search(r"mean_volume:\s*(-?[\d\.]+)\s*dB", output)
        max_vol = float(max_vol_m.group(1)) if max_vol_m else 0.0
        mean_vol = float(mean_vol_m.group(1)) if mean_vol_m else -20.0
        return {
            "max_volume": max_vol,
            "mean_volume": mean_vol,
            "clipping": max_vol >= 0.0,
        }
    except Exception:
        return {"max_volume": -1.5, "mean_volume": -20.0, "clipping": False}


def evaluate_content(segments: list[dict], video_duration: float, manifest: dict | None = None) -> dict[str, Any]:
    """Evaluate translation and transcript content quality deterministically."""
    if not segments:
        return {
            "score": 0.0,
            "bangla_script_ratio": 0.0,
            "cleanliness_score": 0.0,
            "repetition_score": 0.0,
            "speech_density": 0.0,
            "density_score": 0.0,
            "segment_completion_score": 0.0,
            "warnings": ["No segments found in transcript"],
        }

    warnings = []
    total_alpha = 0
    bangla_alpha = 0
    clean_defects = 0
    words = []
    total_speech_duration = 0.0

    # Defect patterns that should never be spoken out loud by TTS
    defect_regex = re.compile(
        r"\[SPEAKER_[A-D]\]|"
        r"\[QUOTE_ARABIC\]|"
        r"\[\d+(?:\.\d+)?s\]|"
        r"^\s*\d+[\.\)]\s*|"
        r"\*\*|"
        r"`|"
        r"^(?:Translation|Bangla|অনুবাদ|বাংলা)\s*:\s*",
        re.IGNORECASE
    )

    for i, seg in enumerate(segments):
        text = _clean_text(seg.get("text", ""))
        dur = float(seg.get("duration", 0.0))
        total_speech_duration += dur
        seg_words = text.split()
        words.extend(seg_words)

        for ch in text:
            if ch.isalpha():
                total_alpha += 1
                if "\u0980" <= ch <= "\u09FF":
                    bangla_alpha += 1

        # Check for leaked artifacts
        if defect_regex.search(text):
            clean_defects += 1

    # 1. Bangla Script Purity (Weight: 30%)
    bangla_ratio = (bangla_alpha / total_alpha) if total_alpha > 0 else 0.0
    if bangla_ratio >= 0.85:
        purity_score = 100.0
    elif bangla_ratio >= 0.50:
        purity_score = 50.0 + (bangla_ratio - 0.50) / 0.35 * 50.0
    else:
        purity_score = max(0.0, bangla_ratio / 0.50 * 50.0)

    if bangla_ratio < 0.80:
        warnings.append(f"Bangla script purity is low ({bangla_ratio:.1%}); English or foreign text may have leaked into dub")

    # 2. Formatting Cleanliness (Weight: 20%)
    defect_rate = clean_defects / len(segments)
    cleanliness_score = max(0.0, 100.0 - defect_rate * 250.0)
    if clean_defects > 0:
        warnings.append(f"{clean_defects} segment(s) contain leaked formatting tokens/tags")

    # 3. Repetition / Hallucination Detection (Weight: 20%)
    # Checks for consecutive identical words or short phrase stutter loops
    rep_pairs = sum(1 for i in range(len(words) - 1) if words[i].lower() == words[i + 1].lower())
    rep_ratio = rep_pairs / max(len(words), 1)
    rep_score = max(0.0, 100.0 - (rep_ratio * 400.0))
    if rep_ratio > 0.03:
        warnings.append(f"High word repetition detected ({rep_ratio:.1%}), possible caption loop artifact")

    # 4. Speech Density & Coverage (Weight: 20%)
    effective_dur = video_duration if video_duration > 0 else total_speech_duration
    speech_density = total_speech_duration / effective_dur if effective_dur > 0 else 0.0
    if speech_density >= 0.60:
        density_score = 100.0
    elif speech_density >= 0.30:
        density_score = 60.0 + (speech_density - 0.30) / 0.30 * 40.0
    else:
        density_score = max(0.0, speech_density / 0.30 * 60.0)

    if speech_density < 0.35:
        warnings.append(f"Speech density is unusually low ({speech_density:.1%}); transcript may be sparse or hallucinated")

    # 5. Segment Completion (Weight: 10%)
    failed_segs = manifest.get("segments_failed", 0) if manifest else 0
    failed_ratio = failed_segs / len(segments) if segments else 0.0
    completion_score = max(0.0, 100.0 - failed_ratio * 500.0)
    if failed_segs > 0:
        warnings.append(f"{failed_segs} segment(s) failed TTS synthesis")

    raw_content_score = (
        0.30 * purity_score +
        0.20 * cleanliness_score +
        0.20 * rep_score +
        0.20 * density_score +
        0.10 * completion_score
    )

    # Scale score down if speech density is critically low (< 0.35)
    density_scale = min(1.0, max(0.2, speech_density / 0.40)) if speech_density < 0.35 else 1.0
    final_content_score = round(raw_content_score * density_scale, 1)

    return {
        "score": final_content_score,
        "bangla_script_ratio": round(bangla_ratio, 3),
        "cleanliness_score": round(cleanliness_score, 1),
        "repetition_score": round(rep_score, 1),
        "speech_density": round(speech_density, 3),
        "density_score": round(density_score, 1),
        "segment_completion_score": round(completion_score, 1),
        "defective_segments": clean_defects,
        "warnings": warnings,
    }


def evaluate_audio(manifest: dict, audio_path: str | None = None) -> dict[str, Any]:
    """Evaluate audio synchronization, speed, duration coverage, and volume."""
    warnings = []
    timing = manifest.get("timing", {})
    speed = manifest.get("speed", {})
    total_segments = manifest.get("segment_count") or timing.get("segments", 1)

    # 1. Alignment / Timing (Weight: 40%)
    on_time = timing.get("on_time", total_segments)
    on_time_ratio = on_time / total_segments if total_segments else 1.0
    min_offset = timing.get("min_offset", 0.0)
    max_offset = timing.get("max_offset", 0.0)
    late_1s = timing.get("late_over_1s", 0)
    late_3s = timing.get("late_over_3s", 0)

    if min_offset < -0.01:
        align_score = 0.0
        warnings.append(f"FATAL: Negative offset detected ({min_offset:.2f}s) - dub speaks early")
    else:
        align_score = 100.0
        align_score -= (1.0 - on_time_ratio) * 35.0
        align_score -= late_1s * 3.0
        align_score -= late_3s * 12.0
        if max_offset > 1.0:
            align_score -= min(30.0, (max_offset - 1.0) * 15.0)
        align_score = max(0.0, min(100.0, align_score))

    if late_3s > 0:
        warnings.append(f"{late_3s} segment(s) more than 3.0s late")
    elif late_1s > 2:
        warnings.append(f"{late_1s} segment(s) more than 1.0s late")

    # 2. Speed & Intelligibility (Weight: 35%)
    mean_speed = speed.get("mean_speed", 1.0)
    max_speed = speed.get("max_speed", 1.0)
    natural_count = speed.get("natural", total_segments)
    natural_ratio = natural_count / total_segments if total_segments else 1.0
    unintelligible = speed.get("unintelligible", 0)

    if unintelligible > 0:
        speed_score = 0.0
        warnings.append(f"FATAL: {unintelligible} segment(s) exceed intelligibility threshold (>2.1x speed)")
    else:
        speed_score = 100.0
        if mean_speed > 1.10:
            speed_score -= min(25.0, (mean_speed - 1.10) * 150.0)
        if max_speed > 1.50:
            speed_score -= min(35.0, (max_speed - 1.50) * 100.0)
        speed_score = max(0.0, min(100.0, speed_score))

    if max_speed > 1.60:
        warnings.append(f"Peak speech speed reaches {max_speed:.2f}x (above ideal 1.50x cap)")

    # 3. Duration Coverage (Weight: 15%)
    coverage = manifest.get("coverage", 1.0)
    if coverage >= 0.90:
        cov_score = 100.0
    elif coverage >= 0.50:
        cov_score = 50.0 + (coverage - 0.50) / 0.40 * 50.0
    else:
        cov_score = 0.0
        warnings.append(f"FATAL: Dub coverage is too low ({coverage:.0%})")

    # 4. Audio Signal Levels (Weight: 10%)
    level_score = 100.0
    levels = {}
    if audio_path and os.path.exists(audio_path):
        levels = get_audio_levels(audio_path)
        if levels.get("clipping"):
            level_score -= 20.0
            warnings.append("Audio digital clipping detected (max_volume >= 0.0 dB)")
        if levels.get("mean_volume", -20.0) < -38.0:
            level_score -= 30.0
            warnings.append(f"Audio level is unusually quiet (mean volume: {levels.get('mean_volume')} dB)")

    audio_score = round(
        0.40 * align_score +
        0.35 * speed_score +
        0.15 * cov_score +
        0.10 * level_score, 1
    )

    return {
        "score": audio_score,
        "on_time_ratio": round(on_time_ratio, 3),
        "mean_offset": timing.get("mean_offset", 0.0),
        "max_offset": max_offset,
        "late_over_1s": late_1s,
        "late_over_3s": late_3s,
        "natural_speed_ratio": round(natural_ratio, 3),
        "mean_speed": mean_speed,
        "max_speed": max_speed,
        "coverage": coverage,
        "levels": levels,
        "warnings": warnings,
    }


def evaluate_video(
    video_id_or_dir: str,
    update_manifest: bool = True,
    threshold: float | None = None
) -> dict[str, Any]:
    """Run complete deterministic quality evaluation on a video output directory."""
    if os.path.isdir(video_id_or_dir):
        video_dir = video_id_or_dir
        video_id = os.path.basename(video_dir.rstrip("/\\"))
    else:
        video_id = video_id_or_dir
        video_dir = os.path.join(OUTPUT_DIR, video_id)

    if threshold is None:
        threshold = DEFAULT_THRESHOLD

    manifest_path = os.path.join(video_dir, "manifest.json")
    transcript_path = os.path.join(video_dir, f"{video_id}_transcript.json")
    audio_path = os.path.join(video_dir, f"{video_id}_dubbed.mp3")

    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Missing manifest.json at {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8-sig") as f:
        manifest = json.load(f)

    segments = []
    if os.path.exists(transcript_path):
        with open(transcript_path, "r", encoding="utf-8-sig") as f:
            t_data = json.load(f)
            segments = t_data.get("segments", [])
    elif "segments" in manifest:
        segments = manifest["segments"]

    video_duration = (
        manifest.get("source_video_seconds") or
        manifest.get("dubbed_audio_seconds") or
        0.0
    )

    content_res = evaluate_content(segments, video_duration, manifest)
    audio_res = evaluate_audio(manifest, audio_path)

    all_warnings = content_res.get("warnings", []) + audio_res.get("warnings", [])

    # Fatal conditions that immediately fail a dub regardless of composite score
    fatal = (
        manifest.get("timing", {}).get("min_offset", 0.0) < -0.01 or
        manifest.get("speed", {}).get("unintelligible", 0) > 0 or
        manifest.get("coverage", 1.0) < 0.50 or
        content_res["bangla_script_ratio"] < 0.60 or
        content_res["speech_density"] < 0.25
    )

    overall_score = round(0.45 * content_res["score"] + 0.55 * audio_res["score"], 1)
    passed = bool((overall_score >= threshold) and not fatal)

    if overall_score >= 90.0:
        grade = "A"
    elif overall_score >= 80.0:
        grade = "B"
    elif overall_score >= 70.0:
        grade = "C"
    elif overall_score >= 60.0:
        grade = "D"
    else:
        grade = "F"

    quality_record = {
        "overall_score": overall_score,
        "composite_score": overall_score,
        "score": overall_score,
        "content_score": content_res["score"],
        "audio_score": audio_res["score"],
        "grade": grade,
        "passed": passed,
        "threshold": threshold,
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "metrics": {
            "bangla_script_ratio": content_res["bangla_script_ratio"],
            "cleanliness_score": content_res["cleanliness_score"],
            "repetition_score": content_res["repetition_score"],
            "speech_density": content_res["speech_density"],
            "segment_completion": content_res["segment_completion_score"],
            "coverage": audio_res["coverage"],
            "on_time_ratio": audio_res["on_time_ratio"],
            "mean_offset": audio_res["mean_offset"],
            "max_offset": audio_res["max_offset"],
            "late_over_1s": audio_res["late_over_1s"],
            "late_over_3s": audio_res["late_over_3s"],
            "natural_speed_ratio": audio_res["natural_speed_ratio"],
            "mean_speed": audio_res["mean_speed"],
            "max_speed": audio_res["max_speed"],
        },
        "warnings": all_warnings,
    }

    if update_manifest:
        manifest["quality"] = quality_record
        tmp_path = f"{manifest_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, manifest_path)

    return quality_record


def evaluate_all(output_root: str = OUTPUT_DIR, threshold: float | None = None) -> dict[str, dict]:
    """Evaluate quality for all completed video folders under output/."""
    results = {}
    if not os.path.exists(output_root):
        return results

    for name in sorted(os.listdir(output_root)):
        d = os.path.join(output_root, name)
        if not os.path.isdir(d) or name.startswith(("_", ".")):
            continue
        if not os.path.exists(os.path.join(d, "manifest.json")):
            continue
        try:
            res = evaluate_video(d, update_manifest=True, threshold=threshold)
            results[name] = res
        except Exception as e:
            print(f"Error evaluating {name}: {e}")

    return results


def print_report(video_id: str, q: dict[str, Any]) -> None:
    status_str = "PASS" if q["passed"] else "FAIL"
    print(f"\n=== Quality Report: {video_id} ===")
    print(f"  Overall Score: {q['overall_score']:.1f}/100 [Grade {q['grade']}] -> {status_str} (Threshold: {q['threshold']})")
    print(f"  Content Quality: {q['content_score']:.1f}/100")
    print(f"    - Bangla Script Ratio: {q['metrics']['bangla_script_ratio']:.1%}")
    print(f"    - Speech Density:      {q['metrics']['speech_density']:.1%}")
    print(f"    - Text Cleanliness:    {q['metrics']['cleanliness_score']:.1f}/100")
    print(f"    - Repetition Score:    {q['metrics']['repetition_score']:.1f}/100")
    print(f"  Audio Quality:   {q['audio_score']:.1f}/100")
    print(f"    - Timing On-time:      {q['metrics']['on_time_ratio']:.1%}")
    print(f"    - Max Offset:          {q['metrics']['max_offset']:+.2f}s (mean {q['metrics']['mean_offset']:+.2f}s)")
    print(f"    - Speed:               mean {q['metrics']['mean_speed']:.2f}x, max {q['metrics']['max_speed']:.2f}x ({q['metrics']['natural_speed_ratio']:.1%} natural)")
    print(f"    - Duration Coverage:   {q['metrics']['coverage']:.1%}")
    if q["warnings"]:
        print("  Warnings:")
        for w in q["warnings"]:
            print(f"    ! {w}")
    print("=" * 45)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video_id", nargs="?", help="Video ID or path to check")
    parser.add_argument("--all", action="store_true", help="Evaluate all videos in output/")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="Quality pass threshold")
    parser.add_argument("--regenerate-if-below", action="store_true", help="Trigger regeneration if score is below threshold")
    args = parser.parse_args()

    if args.all:
        print(f"Evaluating all videos in {OUTPUT_DIR} (Threshold: {args.threshold})...\n")
        results = evaluate_all(threshold=args.threshold)
        passed_count = sum(1 for r in results.values() if r["passed"])
        print(f"{'VIDEO ID':12} | {'SCORE':7} | {'GRADE':5} | {'STATUS':6} | {'CONTENT':7} | {'AUDIO':7}")
        print("-" * 55)
        for vid, r in results.items():
            st = "PASS" if r["passed"] else "FAIL"
            print(f"{vid:12} | {r['overall_score']:5.1f}   | {r['grade']:5} | {st:6} | {r['content_score']:5.1f}   | {r['audio_score']:5.1f}")
        print(f"\nSummary: {passed_count}/{len(results)} videos passed quality threshold.")
        return 0 if passed_count == len(results) else 1

    if not args.video_id:
        parser.error("Specify a video_id or --all")

    q = evaluate_video(args.video_id, update_manifest=True, threshold=args.threshold)
    print_report(args.video_id, q)

    if not q["passed"] and args.regenerate_if_below:
        print(f"\nQuality {q['overall_score']} is below threshold {args.threshold}. Triggering regeneration...")
        cmd = [sys.executable, "launch_pipeline.py", args.video_id, "--fresh", "--supervise"]
        subprocess.run(cmd)

    return 0 if q["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
