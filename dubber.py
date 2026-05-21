import os
import asyncio
import yt_dlp
import edge_tts


def download_video(video_id: str, out_dir: str) -> str:
    """Download best quality video (with audio) from YouTube. Returns path to .mp4 file."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_path = os.path.join(out_dir, f"{video_id}_video.mp4")
    opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": out_path,
        "quiet": True,
        "merge_output_format": "mp4",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return out_path


def generate_segment_tts(text: str, out_path: str, rate: str = None):
    """
    Render Bangla text to speech using Microsoft Edge TTS.
    Exclusively uses 'bn-BD-PradeepNeural' (Bangladeshi Male).
    Optional native rate control (e.g. rate="+20%").
    """
    import time
    max_retries = 5
    backoff_factor = 2

    for attempt in range(max_retries):
        try:
            if rate:
                communicate = edge_tts.Communicate(text, "bn-BD-PradeepNeural", rate=rate)
            else:
                communicate = edge_tts.Communicate(text, "bn-BD-PradeepNeural")
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            if loop.is_running():
                future = asyncio.run_coroutine_threadsafe(communicate.save(out_path), loop)
                future.result()
            else:
                loop.run_until_complete(communicate.save(out_path))

            # Verify that file exists and has content
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return
            else:
                raise ValueError("Generated audio file is empty or missing")

        except Exception as e:
            print(f"Edge TTS synthesis attempt {attempt + 1} failed for text '{text}': {e}")
            if attempt == max_retries - 1:
                raise e
            sleep_time = backoff_factor ** attempt
            time.sleep(sleep_time)

