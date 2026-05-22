import os
import subprocess
import ffmpeg


def get_audio_duration(file_path: str) -> float:
    """Get the duration of an audio file in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Error checking duration of {file_path}: {e}")
        return 3.0  # Fallback


def build_dubbed_audio(segments: list[dict], seg_dir: str, original_audio_path: str, total_duration: float, out_path: str):
    """
    Mixes per-segment TTS audio files into a single audio track, resolving overlaps
    by shifting start times and dynamically adjusting playback speed.
    """
    # Slice classical Arabic quotes
    for i, seg in enumerate(segments):
        if seg.get("is_arabic_quote") and original_audio_path and os.path.exists(original_audio_path):
            seg_file = os.path.join(seg_dir, f"seg_{i:04d}_orig.mp3")
            ss_time = seg["start"]
            dur = seg["duration"]
            try:
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-ss", str(ss_time), "-t", str(dur),
                        "-i", original_audio_path, "-acodec", "copy", seg_file
                    ],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True
                )
            except Exception as e:
                print(f"Error slicing original Arabic quote for segment {i}: {e}")

    # Compute non-overlapping start times
    current_time = 0.0
    natural_gap = 0.15  # 150ms pause between segments
    
    # Store pre-calculated details for the mixing pass
    precalculated = []

    for i, seg in enumerate(segments):
        # Determine the target segment audio file
        seg_file = os.path.join(seg_dir, f"seg_{i:04d}.mp3")
        if seg.get("is_arabic_quote") and original_audio_path and os.path.exists(os.path.join(seg_dir, f"seg_{i:04d}_orig.mp3")):
            seg_file = os.path.join(seg_dir, f"seg_{i:04d}_orig.mp3")

        if not os.path.exists(seg_file) or os.path.getsize(seg_file) == 0:
            continue

        # Get actual audio duration
        actual_duration = get_audio_duration(seg_file)

        # Available time before next segment starts
        if i < len(segments) - 1:
            available_time = max(seg["duration"], segments[i + 1]["start"] - seg["start"])
        else:
            available_time = seg["duration"]

        # Calculate speedup if actual audio exceeds available slot
        rate = 1.0
        if actual_duration > available_time:
            rate = actual_duration / available_time
            rate = min(rate, 2.0)  # Limit speedup to 2.0x
            if rate <= 1.02:
                rate = 1.0

        effective_duration = actual_duration / rate

        # Avoid overlaps by shifting start times if necessary
        target_start = seg["start"]
        if target_start < current_time:
            actual_start = current_time
        else:
            actual_start = target_start

        # Track end time of the current segment
        current_time = actual_start + effective_duration + natural_gap

        precalculated.append({
            "seg_file": seg_file,
            "actual_start": actual_start,
            "rate": rate
        })

    # Mix sequential streams over silence using FFmpeg
    # Make sure silence track is long enough to cover shifted timings
    new_total_duration = max(total_duration, current_time)

    silence = ffmpeg.input(
        "anullsrc=r=44100:cl=mono",
        f="lavfi",
        t=new_total_duration,
    )
    delayed_inputs = [silence]

    for item in precalculated:
        audio_stream = ffmpeg.input(item["seg_file"]).audio

        # Apply speed filter if rate > 1.02
        if item["rate"] > 1.02:
            audio_stream = audio_stream.filter("atempo", item["rate"])

        # Delay audio in milliseconds
        ms = int(item["actual_start"] * 1000)
        delayed = audio_stream.filter("adelay", f"{ms}|{ms}")
        delayed_inputs.append(delayed)

    if len(delayed_inputs) == 1:
        raise RuntimeError("No segment audio files found to mix.")

    # Mix all streams over silence
    mixed = ffmpeg.filter(
        delayed_inputs,
        "amix",
        inputs=len(delayed_inputs),
        normalize=0,
        duration="first",
    )
    
    # Save the output audio track
    ffmpeg.output(mixed, out_path, ar=44100).run(overwrite_output=True, quiet=True)


def mux_video_with_dubbed_audio(video_path: str, audio_path: str, out_path: str):
    """Mux the video with the dubbed audio track (no video re-encoding)."""
    v = ffmpeg.input(video_path).video
    a = ffmpeg.input(audio_path).audio
    ffmpeg.output(
        v, a, out_path,
        vcodec="copy",
        acodec="aac",
        audio_bitrate="192k",
    ).run(overwrite_output=True, quiet=True)
