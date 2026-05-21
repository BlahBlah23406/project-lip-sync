import os
import shutil
import tempfile
import uuid
from dotenv import load_dotenv

load_dotenv()

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from captions import extract_video_id, fetch_transcript, cluster_segments
from translator import translate_segments

app = FastAPI(title="Islamic Lecture Subtitle Translator")

# In-memory cache: video_id -> list of translated segments
_cache: dict[str, list[dict]] = {}

# Dubbing jobs: job_id -> {status, progress, url, error}
_dub_jobs: dict[str, dict] = {}

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)


class TranslateRequest(BaseModel):
    url: str


class DubRequest(BaseModel):
    url: str


# ── Subtitle translation ──────────────────────────────────────────────────────

@app.post("/api/translate")
async def translate(req: TranslateRequest):
    try:
        video_id = extract_video_id(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if video_id in _cache:
        return {"video_id": video_id, "segments": _cache[video_id], "cached": True}

    try:
        raw_segments = fetch_transcript(video_id)
        segments = cluster_segments(raw_segments)
    except Exception as e:
        msg = str(e)
        if "NoTranscriptFound" in msg or "Could not retrieve" in msg:
            raise HTTPException(
                status_code=404,
                detail="No English captions found for this video. Try a video that has English subtitles enabled.",
            )
        if "VideoUnavailable" in msg:
            raise HTTPException(status_code=404, detail="Video not found or unavailable.")
        raise HTTPException(status_code=500, detail=f"Failed to fetch transcript: {msg}")

    if not segments:
        raise HTTPException(status_code=404, detail="Transcript is empty.")

    try:
        translated, _, _ = translate_segments(segments)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Translation failed: {str(e)}")

    _cache[video_id] = translated
    return {"video_id": video_id, "segments": translated, "cached": False}


# ── Dubbing pipeline ──────────────────────────────────────────────────────────

def _set_job(job_id: str, **kwargs):
    _dub_jobs[job_id].update(kwargs)


def run_dub_pipeline(job_id: str, video_id: str):
    import time
    from dubber import (
        download_video, generate_segment_tts,
    )
    from mixer import build_dubbed_audio, mux_video_with_dubbed_audio, get_audio_duration

    work_dir = tempfile.mkdtemp(prefix=f"dub_{job_id}_")
    seg_dir = os.path.join(work_dir, "segments")
    os.makedirs(seg_dir)

    start_time = time.time()
    in_tokens = 0
    out_tokens = 0
    cached = False

    try:
        _set_job(job_id, status="processing", progress="Fetching and translating captions…")
        if video_id in _cache:
            segments = _cache[video_id]
            cached = True
        else:
            raw = fetch_transcript(video_id)
            clustered = cluster_segments(raw)
            segments, in_tokens, out_tokens = translate_segments(clustered)
            _cache[video_id] = segments
            cached = False

        # Download video first so we can extract the original audio track
        _set_job(job_id, progress="Downloading video…")
        video_path = download_video(video_id, work_dir)

        # Extract the original audio track from the downloaded video using FFmpeg
        _set_job(job_id, progress="Extracting original audio track…")
        original_audio_path = os.path.join(work_dir, "original_audio.mp3")
        try:
            import ffmpeg
            ffmpeg.input(video_path).output(original_audio_path, acodec="libmp3lame").run(overwrite_output=True, quiet=True)
        except Exception as e:
            print(f"Error extracting original audio: {e}")
            original_audio_path = None

        total = len(segments)
        _set_job(job_id, progress=f"Generating Bangla audio (0/{total})…")
        for i, seg in enumerate(segments):
            out_path = os.path.join(seg_dir, f"seg_{i:04d}.mp3")
            
            # Skip Edge TTS generation for classical Arabic quotes (we slice them from original audio instead)
            if seg.get("is_arabic_quote"):
                continue

            speaker_id = seg.get("speaker", "SPEAKER_A")
            generate_segment_tts(seg["text"], out_path, speaker=speaker_id)

            # Determine available time window (gap until next segment starts, or segment duration)
            if i < len(segments) - 1:
                available_time = max(seg["duration"], segments[i + 1]["start"] - seg["start"])
            else:
                available_time = seg["duration"]

            actual_duration = get_audio_duration(out_path)
            if actual_duration > available_time:
                rate_factor = actual_duration / available_time
                if rate_factor > 1.02:
                    rate_factor = min(rate_factor, 2.0)  # limit speedup to 2.0x (which is +100%)
                    pct = int((rate_factor - 1.0) * 100)
                    rate_str = f"+{pct}%"
                    # Re-generate with native SSML speedup!
                    generate_segment_tts(seg["text"], out_path, rate=rate_str, speaker=speaker_id)

            if (i + 1) % 5 == 0 or (i + 1) == total:
                _set_job(job_id, progress=f"Generating Bangla audio ({i + 1}/{total})…")

        # Total video duration = last segment's end time
        total_duration = max(s["start"] + s["duration"] for s in segments)

        _set_job(job_id, progress="Mixing dubbed audio track…")
        dubbed_audio = os.path.join(work_dir, "dubbed_audio.mp3")
        build_dubbed_audio(segments, seg_dir, original_audio_path, total_duration, dubbed_audio)

        _set_job(job_id, progress="Muxing final video…")
        out_filename = f"{video_id}_dubbed.mp4"
        out_path = os.path.join(OUTPUT_DIR, out_filename)
        mux_video_with_dubbed_audio(video_path, dubbed_audio, out_path)

        # Save the dubbed audio track separately as well for synchronized web playback!
        audio_out_filename = f"{video_id}_dubbed.mp3"
        audio_out_path = os.path.join(OUTPUT_DIR, audio_out_filename)
        shutil.copy(dubbed_audio, audio_out_path)

        end_time = time.time()
        time_taken = round(end_time - start_time, 2)
        tokens = {
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "total_tokens": in_tokens + out_tokens,
            "cached": cached
        }

        _set_job(
            job_id,
            status="done",
            progress="Complete!",
            url=f"/output/{out_filename}",
            audio_url=f"/output/{audio_out_filename}",
            segments=segments,
            tokens=tokens,
            time_taken=time_taken,
            video_id=video_id,
        )

    except Exception as e:
        _set_job(job_id, status="error", progress="Failed.", error=str(e))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/api/dub")
async def dub(req: DubRequest, background_tasks: BackgroundTasks):
    try:
        video_id = extract_video_id(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    job_id = str(uuid.uuid4())
    _dub_jobs[job_id] = {"status": "pending", "progress": "Queued…", "url": None, "error": None}
    background_tasks.add_task(run_dub_pipeline, job_id, video_id)
    return {"job_id": job_id}


@app.get("/api/dub/status/{job_id}")
def dub_status(job_id: str):
    job = _dub_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@app.get("/output/{filename}")
def download_output(filename: str):
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Output file not found.")
    if filename.endswith(".mp3"):
        return FileResponse(path, media_type="audio/mpeg", filename=filename)
    return FileResponse(path, media_type="video/mp4", filename=filename)


# ── Health + static files ─────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/{full_path:path}")
def serve_frontend(full_path: str):  # noqa: ARG001
    return FileResponse("static/index.html")
