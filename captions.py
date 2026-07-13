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
        # --- LOCAL TRANSCRIPT CACHE (per-video) ---
        import json
        import os
        cache_dir = os.path.join(os.path.dirname(__file__), "transcript_cache")
        cache_path = os.path.join(cache_dir, f"{video_id}.json")
        if os.path.exists(cache_path):
            print(f"  Using cached transcript from {cache_path}")
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        # -----------------------------------------
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

    result = [{"text": seg["text"], "start": seg["start"], "duration": seg["duration"]} for seg in raw]

    # Save to per-video cache
    os.makedirs(cache_dir, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"  Cached transcript to {cache_path}")

    return result


def cluster_segments(segments: list[dict], max_gap: float = 0.4, max_duration: float = 8.0) -> list[dict]:
    """
    Consolidates consecutive transcript segments that are very close to each other.
    This gives the translator a larger character budget and makes the synthesized speech flow naturally.
    """
    if not segments:
        return []

    clustered = []
    current = segments[0].copy()

    for next_seg in segments[1:]:
        current_end = current["start"] + current["duration"]
        gap = next_seg["start"] - current_end
        combined_duration = (next_seg["start"] + next_seg["duration"]) - current["start"]

        if gap <= max_gap and combined_duration <= max_duration:
            current["text"] = current["text"].strip() + " " + next_seg["text"].strip()
            current["duration"] = combined_duration
        else:
            clustered.append(current)
            current = next_seg.copy()

    clustered.append(current)
    return clustered

