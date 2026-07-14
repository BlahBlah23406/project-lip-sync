import asyncio
import os
import random
import time

import edge_tts
import yt_dlp


def download_video(video_id: str, out_dir: str) -> str:
    """Download best quality video (with audio) from YouTube. Returns path to .mp4 file."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_path = os.path.join(out_dir, f"{video_id}_video.mp4")
    opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": out_path,
        "quiet": True,
        "merge_output_format": "mp4",
        "socket_timeout": 60,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return out_path


VOICES = {
    "SPEAKER_A": "bn-BD-PradeepNeural",   # Bangladeshi Male (default)
    "SPEAKER_B": "bn-BD-NabanitaNeural",  # Bangladeshi Female
    "SPEAKER_C": "bn-IN-BashkarNeural",   # Indian Male (Bangla)
    "SPEAKER_D": "bn-IN-TanishaaNeural",  # Indian Female (Bangla)
}

# --------------------------------------------------------------------------
# Why this file has timeouts everywhere (2026-07-13)
# --------------------------------------------------------------------------
# The pipeline was reported as "SIGKILLed during TTS on long episodes". It was
# not. A py-spy dump of the stuck process (PID 55972) showed the main thread
# parked here, idle, forever:
#
#   _poll (asyncio\windows_events.py:774)
#   run_until_complete (asyncio\base_events.py:678)
#   generate_segment_tts (dubber.py:59)
#
# edge_tts.Communicate.save() has NO timeout. Microsoft's free TTS endpoint
# throttles a client that fires hundreds of requests in a row (exactly what a
# 268- or 680-segment episode does). When it throttles, the websocket stalls
# instead of erroring: save() blocks forever, no exception is raised, and the
# retry loop below never fires. The run then sits idle until the host process
# manager kills the stuck tree -- and THAT kill is the "SIGKILL" that was being
# chased. Short test runs (~32 segments) never burst hard enough to get
# throttled, which is why only real episodes died.
#
# The rule this file now enforces: every network call is bounded. A stall
# becomes a retryable timeout, and a throttle becomes a backoff -- never a hang.
TTS_TIMEOUT = float(os.getenv("TTS_TIMEOUT", "45"))    # seconds per attempt
TTS_MAX_RETRIES = int(os.getenv("TTS_MAX_RETRIES", "6"))
TTS_PACING = float(os.getenv("TTS_PACING", "0"))       # optional delay between calls


async def _save_with_timeout(text: str, voice: str, rate: str, out_path: str) -> None:
    kwargs = {"rate": rate} if rate else {}
    communicate = edge_tts.Communicate(text, voice, **kwargs)
    await asyncio.wait_for(communicate.save(out_path), timeout=TTS_TIMEOUT)


def generate_segment_tts(text: str, out_path: str, rate: str = None, speaker: str = "SPEAKER_A"):
    """Render Bangla text to speech using Microsoft Edge TTS.

    Bounded and resumable:
      * every attempt is capped at TTS_TIMEOUT seconds -- a throttled/stalled
        websocket raises TimeoutError instead of hanging the whole pipeline;
      * a throttle backs off progressively (it needs a real cooling-off period,
        not an instant retry);
      * audio is written to a temp file and renamed into place, so a killed or
        failed attempt can never leave a truncated .mp3 that the resume logic
        would mistake for a finished segment.
    """
    voice = VOICES.get(speaker.upper(), "bn-BD-PradeepNeural")
    tmp_path = f"{out_path}.part"

    last_error = None
    for attempt in range(TTS_MAX_RETRIES):
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

            # asyncio.run() creates a fresh event loop and closes it properly.
            # The old code reused a module-level loop via the deprecated
            # get_event_loop(), which leaked a ThreadPoolExecutor per run.
            asyncio.run(_save_with_timeout(text, voice, rate, tmp_path))

            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                raise ValueError("Edge TTS produced an empty audio file")

            os.replace(tmp_path, out_path)  # atomic: the clip appears complete or not at all

            if TTS_PACING:
                time.sleep(TTS_PACING)
            return

        except (asyncio.TimeoutError, TimeoutError) as e:
            # Almost always throttling. Back off hard before trying again.
            last_error = e
            wait = min(60.0, 5.0 * (2 ** attempt)) + random.uniform(0, 3)
            print(f"  TTS attempt {attempt + 1}/{TTS_MAX_RETRIES} timed out after "
                  f"{TTS_TIMEOUT:.0f}s (endpoint likely throttling); "
                  f"backing off {wait:.0f}s", flush=True)
        except Exception as e:
            last_error = e
            wait = min(30.0, 2.0 ** attempt) + random.uniform(0, 2)
            preview = text[:60].replace("\n", " ")
            print(f"  TTS attempt {attempt + 1}/{TTS_MAX_RETRIES} failed for "
                  f"'{preview}...': {e!r}; retrying in {wait:.0f}s", flush=True)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        if attempt < TTS_MAX_RETRIES - 1:
            time.sleep(wait)

    raise RuntimeError(
        f"Edge TTS failed after {TTS_MAX_RETRIES} attempts (last error: {last_error!r}). "
        f"Text: {text[:80]!r}"
    ) from last_error
