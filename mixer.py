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
    Combine per-segment TTS audio files into a single track by placing each
    at its original timestamp over a silence base.
    
    If a generated segment's audio is longer than the available time before
    the next segment starts, it is dynamically sped up using ffmpeg's
    'atempo' filter to prevent overlapping playback.

    segments: list of {start, duration, text, speaker, is_arabic_quote}
    total_duration: length of the original video in seconds
    original_audio_path: path to the original full audio track extracted from YouTube
    out_path: where to write the final mixed .mp3
    """
    silence = ffmpeg.input(
        "anullsrc=r=44100:cl=mono",
        f="lavfi",
        t=total_duration,
    )
    delayed_inputs = [silence]

    for i, seg in enumerate(segments):
        seg_file = os.path.join(seg_dir, f"seg_{i:04d}.mp3")
        
        # If it is an Arabic quote recitation, slice it directly from the original lecture audio!
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
                seg_file = os.path.join(seg_dir, f"seg_{i:04d}.mp3")

        if not os.path.exists(seg_file):
            continue

        # Determine available time window (gap until next segment starts, or segment duration)
        if i < len(segments) - 1:
            available_time = max(seg["duration"], segments[i + 1]["start"] - seg["start"])
        else:
            available_time = seg["duration"]

        actual_duration = get_audio_duration(seg_file)
        audio_stream = ffmpeg.input(seg_file).audio

        # Dynamically speed up if audio is longer than available time
        if actual_duration > available_time:
            rate = actual_duration / available_time
            rate = min(rate, 2.0)  # Limit speedup to 2.0x to preserve legibility
            if rate > 1.02:
                audio_stream = audio_stream.filter("atempo", rate)

        ms = int(seg["start"] * 1000)
        delayed = audio_stream.filter("adelay", f"{ms}|{ms}")
        delayed_inputs.append(delayed)

    if len(delayed_inputs) == 1:
        raise RuntimeError("No segment audio files found to mix.")

    mixed = ffmpeg.filter(
        delayed_inputs,
        "amix",
        inputs=len(delayed_inputs),
        normalize=0,
        duration="first",
    )
    ffmpeg.output(mixed, out_path, ar=44100).run(overwrite_output=True, quiet=True)


def mux_video_with_dubbed_audio(video_path: str, audio_path: str, out_path: str):
    """
    Replace the video's original audio track with the dubbed Bangla audio.
    Video stream is copied as-is (no re-encoding). Audio is encoded to AAC.
    """
    v = ffmpeg.input(video_path).video
    a = ffmpeg.input(audio_path).audio
    ffmpeg.output(
        v, a, out_path,
        vcodec="copy",
        acodec="aac",
        audio_bitrate="192k",
    ).run(overwrite_output=True, quiet=True)
