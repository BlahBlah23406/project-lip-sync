import contextlib
import json
import os
import re
import threading
import time
from youtube_transcript_api import YouTubeTranscriptApi

# Optional Whisper fallback for when YouTube transcript API is rate-limited/blocked.
# faster-whisper is installed user-scope and uses the tiny.en model for speed.
FASTER_WHISPER_MODEL = None


def _get_whisper_model():
    """Lazily load the faster-whisper tiny.en model once per process."""
    global FASTER_WHISPER_MODEL
    if FASTER_WHISPER_MODEL is None:
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from faster_whisper import WhisperModel
        FASTER_WHISPER_MODEL = WhisperModel("tiny.en", device="cpu", compute_type="int8")
    return FASTER_WHISPER_MODEL


def extract_video_id(url: str) -> str:
    patterns = [
        r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from URL: {url}")


def _cache_path(video_id: str) -> str:
    cache_dir = os.path.join(os.path.dirname(__file__), "transcript_cache")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"{video_id}.json")


def _load_cached(video_id: str) -> list[dict] | None:
    path = _cache_path(video_id)
    if os.path.exists(path):
        print(f"  Using cached transcript from {path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_cached(video_id: str, segments: list[dict]) -> None:
    path = _cache_path(video_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)
    print(f"  Cached transcript to {path}")


def _download_audio(video_id: str) -> str:
    """Download best audio to output/<id>/.work/audio.mp3 using yt-dlp."""
    import yt_dlp

    import ytdlp_runtime
    work_dir = os.path.join(os.path.dirname(__file__), "output", video_id, ".work")
    os.makedirs(work_dir, exist_ok=True)
    out_path = os.path.join(work_dir, "audio")
    audio_file = out_path + ".mp3"
    if os.path.exists(audio_file) and os.path.getsize(audio_file) > 100000:
        print(f"  Reusing existing audio from {audio_file}")
        return audio_file

    opts = {
        "format": "bestaudio/best",
        "outtmpl": out_path + ".%(ext)s",
        "quiet": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }
        ],
    }
    url = f"https://www.youtube.com/watch?v={video_id}"
    # The Whisper fallback downloads real audio bytes, so it needs the JS
    # runtime for the same reason the video download does. See ytdlp_runtime.
    ytdlp_runtime.apply(opts)
    with _heartbeat_ticker(f"captions download {video_id}"):
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    if not os.path.exists(audio_file):
        raise FileNotFoundError(f"Audio download did not produce {audio_file}")
    return audio_file


_heartbeat_cb = None


def set_heartbeat_cb(cb) -> None:
    """Register a watchdog heartbeat callback to pulse during transcription."""
    global _heartbeat_cb
    _heartbeat_cb = cb


@contextlib.contextmanager
def _heartbeat_ticker(note: str, interval: float = 15.0):
    """Periodically pulse heartbeat while a long blocking operation is running."""
    stop_event = threading.Event()
    if _heartbeat_cb:
        _heartbeat_cb(note)

        def _ticker():
            while not stop_event.wait(interval):
                if _heartbeat_cb:
                    _heartbeat_cb(note)

        t = threading.Thread(target=_ticker, daemon=True)
        t.start()
        try:
            yield
        finally:
            stop_event.set()
    else:
        yield


def _transcribe_with_whisper(audio_path: str) -> list[dict]:
    """Run faster-whisper tiny.en on an audio file and return transcript segments."""
    model = _get_whisper_model()
    print(f"  Transcribing {audio_path} with faster-whisper tiny.en ...")
    start = time.time()
    with _heartbeat_ticker("whisper transcribing"):
        segments, info = model.transcribe(
            audio_path,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        result = []
        for seg in segments:
            if _heartbeat_cb:
                _heartbeat_cb(f"whisper {seg.start:.0f}s")
            result.append(
                {
                    "text": seg.text.strip(),
                    "start": seg.start,
                    "duration": max(0.0, seg.end - seg.start),
                }
            )
    print(f"  Whisper transcription completed in {time.time() - start:.1f}s ({len(result)} segments)")
    return result


def fetch_transcript_via_whisper(video_id: str) -> list[dict]:
    """Fallback transcript retrieval: download audio and transcribe locally."""
    cached = _load_cached(video_id)
    if cached is not None:
        return cached

    audio_path = _download_audio(video_id)
    segments = _transcribe_with_whisper(audio_path)
    _save_cached(video_id, segments)
    return segments


def fetch_transcript(video_id: str) -> list[dict]:
    """
    Returns a list of segments: [{text, start, duration}, ...]
    Tries the local cache first, then YouTube transcript API, then
    faster-whisper audio transcription as a last resort.
    """
    cached = _load_cached(video_id)
    if cached is not None:
        return cached

    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
        raw = fetched.to_raw_data()
    except Exception as yt_exc:
        print(f"  YouTube transcript API failed ({type(yt_exc).__name__}); trying fallback Whisper transcription...")
        return fetch_transcript_via_whisper(video_id)

    result = [{"text": seg["text"], "start": seg["start"], "duration": seg["duration"]} for seg in raw]
    _save_cached(video_id, result)
    return result


def clean_caption_loops(text: str) -> str:
    """Collapse severe hallucinated repetition loops from YouTube auto-captions."""
    if not text:
        return ""
    # Collapse 3+ repeated single words (with optional commas/spaces, e.g. 'ema, ema, ema')
    text = re.sub(r'(\b\w+\b)(?:[,\s]+\1){2,}', r'\1', text, flags=re.IGNORECASE)
    # Collapse 3+ repeated short phrases (e.g. 'I\'m alive I\'m alive I\'m alive')
    text = re.sub(r'(\b[\w\s\']+\b)(?:\s+\1){2,}', r'\1', text, flags=re.IGNORECASE)
    return text.strip()


def cluster_segments(segments: list[dict], max_gap: float = 0.4, max_duration: float = 8.0) -> list[dict]:
    """
    Consolidates consecutive transcript segments that are very close to each other.
    This gives the translator a larger character budget and makes the synthesized speech flow naturally.
    """
    if not segments:
        return []

    clustered = []
    current = segments[0].copy()
    current["text"] = clean_caption_loops(current.get("text", ""))

    for next_seg in segments[1:]:
        next_text = clean_caption_loops(next_seg.get("text", ""))
        current_end = current["start"] + current["duration"]
        gap = next_seg["start"] - current_end
        combined_duration = (next_seg["start"] + next_seg["duration"]) - current["start"]

        if gap <= max_gap and combined_duration <= max_duration:
            current["text"] = (current["text"].strip() + " " + next_text.strip()).strip()
            current["duration"] = combined_duration
        else:
            if current["text"]:
                clustered.append(current)
            current = next_seg.copy()
            current["text"] = next_text

    if current["text"]:
        clustered.append(current)
    return clustered

