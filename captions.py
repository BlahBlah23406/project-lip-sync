import re
from youtube_transcript_api import YouTubeTranscriptApi


def extract_video_id(url: str) -> str:
    patterns = [
        r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from URL: {url}")


def fetch_transcript(video_id: str) -> list[dict]:
    """
    Returns a list of segments: [{text, start, duration}, ...]
    Tries English first via fetch(), falls back to list() for auto-generated tracks.
    """
    api = YouTubeTranscriptApi()

    try:
        fetched = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
        raw = fetched.to_raw_data()
    except Exception:
        # Fall back to listing available transcripts
        transcript_list = api.list(video_id)
        try:
            transcript_obj = transcript_list.find_manually_created_transcript(["en"])
        except Exception:
            transcript_obj = transcript_list.find_generated_transcript(["en"])
        fetched = transcript_obj.fetch()
        raw = fetched.to_raw_data()

    return [{"text": seg["text"], "start": seg["start"], "duration": seg["duration"]} for seg in raw]
