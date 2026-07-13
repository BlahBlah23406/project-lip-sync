"""
Standalone script to run the LipSync dubbing pipeline on test video H3hijSGhdlo.
Outputs: dubbed audio (MP3) + translation transcript (JSON) to the output/ directory.
"""
import os
import sys
import json
import time
import shutil
import tempfile

# Ensure we're in the right directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from captions import extract_video_id, fetch_transcript, cluster_segments
from translator import translate_segments, retranslate_shorter, count_bangla_syllables
from dubber import download_video, generate_segment_tts
from mixer import build_dubbed_audio, mux_video_with_dubbed_audio, get_audio_duration

VIDEO_ID = "H3hijSGhdlo"
OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

print(f"=== LipSync Dubbing Pipeline ===")
print(f"Video ID: {VIDEO_ID}")
print(f"Started at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
start_time = time.time()

# Step 1: Fetch transcript
print("\n[1/6] Fetching transcript...")
raw_segments = fetch_transcript(VIDEO_ID)
segments = cluster_segments(raw_segments)
print(f"  Got {len(segments)} segments")

# Step 2: Translate
print("\n[2/6] Translating to Bangla...")
segments, in_tokens, out_tokens = translate_segments(segments)
# Preserve original English text for reprompting
for seg, orig in zip(segments, cluster_segments(raw_segments)):
    seg["original_text"] = orig.get("text", "")
print(f"  Translated {len(segments)} segments (in:{in_tokens} out:{out_tokens} tokens)")

# Step 3: Pre-process segments (trim overlaps)
print("\n[3/6] Pre-processing segments...")
trimmed = []
for i, seg in enumerate(segments):
    t = seg.copy()
    if i < len(segments) - 1:
        max_dur = segments[i + 1]["start"] - seg["start"]
        if max_dur > 0:
            t["duration"] = min(seg["duration"], max_dur)
        else:
            t["duration"] = 0.1
    trimmed.append(t)
segments = trimmed

# Step 4: Download video & generate TTS
print("\n[4/6] Downloading video and generating TTS...")
work_dir = tempfile.mkdtemp(prefix="dub_pipeline_")
seg_dir = os.path.join(work_dir, "segments")
os.makedirs(seg_dir)

video_path = download_video(VIDEO_ID, work_dir)

# Extract original audio
import ffmpeg
original_audio_path = os.path.join(work_dir, "original_audio.mp3")
try:
    ffmpeg.input(video_path).output(original_audio_path, acodec="libmp3lame").run(overwrite_output=True, quiet=True)
    print("  Original audio extracted.")
except Exception as e:
    print(f"  Warning: Could not extract original audio: {e}")
    original_audio_path = None

total = len(segments)
for i, seg in enumerate(segments):
    out_path = os.path.join(seg_dir, f"seg_{i:04d}.mp3")
    
    if not seg.get("text") or not seg["text"].strip():
        continue
    if seg.get("is_arabic_quote"):
        continue

    speaker_id = seg.get("speaker", "SPEAKER_A")
    generate_segment_tts(seg["text"], out_path, speaker=speaker_id)

    # Determine available time window
    if i < len(segments) - 1:
        available_time = max(seg["duration"], segments[i + 1]["start"] - seg["start"])
    else:
        available_time = seg["duration"]

    actual_duration = get_audio_duration(out_path)
    if actual_duration > available_time:
        rate_factor = actual_duration / available_time
        if rate_factor > 1.02:
            if rate_factor > 1.3:
                max_syllables = int(available_time * 3.5)
                actual_syllables = count_bangla_syllables(seg["text"])
                print(f"  Segment {i}: rate={rate_factor:.2f}x > 1.3 -> reprompting for ~{max_syllables} syllables (current {actual_syllables})...")
                shorter_text = retranslate_shorter(seg, max_syllables)
                if shorter_text != seg["text"] and count_bangla_syllables(shorter_text) <= int(max_syllables * 1.2):
                    seg["text"] = shorter_text
                    generate_segment_tts(seg["text"], out_path, speaker=speaker_id)
                    actual_duration = get_audio_duration(out_path)
                    if actual_duration > available_time:
                        rate_factor = actual_duration / available_time
                        rate_factor = min(rate_factor, 2.0)
                    else:
                        rate_factor = 1.0

            if rate_factor > 1.02:
                rate_factor = min(rate_factor, 2.0)
                pct = int((rate_factor - 1.0) * 100)
                rate_str = f"+{pct}%"
                generate_segment_tts(seg["text"], out_path, rate=rate_str, speaker=speaker_id)

    if (i + 1) % 5 == 0 or (i + 1) == total:
        print(f"  TTS progress: {i + 1}/{total}")

# Step 5: Mix
print("\n[5/6] Mixing dubbed audio track...")
total_duration = max(s["start"] + s["duration"] for s in segments)
dubbed_audio = os.path.join(work_dir, "dubbed_audio.mp3")
build_dubbed_audio(segments, seg_dir, original_audio_path, total_duration, dubbed_audio)

# Step 6: Save outputs
print("\n[6/6] Saving outputs...")
audio_out = os.path.join(OUTPUT_DIR, f"{VIDEO_ID}_dubbed.mp3")
shutil.copy(dubbed_audio, audio_out)
print(f"  Dubbed audio: {audio_out}")

# Mux video
out_video = os.path.join(OUTPUT_DIR, f"{VIDEO_ID}_dubbed.mp4")
mux_video_with_dubbed_audio(video_path, dubbed_audio, out_video)
print(f"  Dubbed video: {out_video}")

# Save transcript JSON
transcript_out = os.path.join(OUTPUT_DIR, f"{VIDEO_ID}_transcript.json")
with open(transcript_out, "w", encoding="utf-8") as f:
    json.dump({
        "video_id": VIDEO_ID,
        "segments": segments,
        "tokens": {"input": in_tokens, "output": out_tokens},
        "time_taken": round(time.time() - start_time, 2)
    }, f, ensure_ascii=False, indent=2)
print(f"  Transcript: {transcript_out}")

# Cleanup
shutil.rmtree(work_dir, ignore_errors=True)

elapsed = time.time() - start_time
print(f"\n=== Pipeline complete in {elapsed:.1f}s ===")
print(f"Output files in: {OUTPUT_DIR}/")