import json
import os
import shutil
import tempfile
import uuid
from dotenv import load_dotenv

load_dotenv()

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Optional

from captions import extract_video_id, fetch_transcript, cluster_segments
from translator import translate_segments, retranslate_shorter, count_bangla_syllables
from feedback_store import append_feedback, get_feedback_file_path, iter_feedback

app = FastAPI(title="Islamic Lecture Subtitle Translator")

# In-memory cache: video_id -> translated segments
_cache: dict[str, list[dict]] = {}

# Dubbing jobs tracking
_dub_jobs: dict[str, dict] = {}

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)


class TranslateRequest(BaseModel):
    url: str


class DubRequest(BaseModel):
    url: str


class SegmentIssue(BaseModel):
    segment_index: int
    segment_start: float = 0.0
    category: str
    comment: str = ""


class EvaluationRequest(BaseModel):
    video_id: str
    overall_rating: int = Field(..., ge=1, le=5)
    lip_sync_rating: int = Field(..., ge=1, le=5)
    audio_clarity_rating: int = Field(..., ge=1, le=5)
    sync_issue: Optional[str] = None  # none / minor / major
    segment_issues: List[SegmentIssue] = []
    general_comment: str = ""
    improvement_suggestion: str = ""


# --- Subtitle Translation ---

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


# --- Dubbing Pipeline ---

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
            # Preserve original English text for potential reprompting
            for seg, orig in zip(segments, clustered):
                seg["original_text"] = orig.get("text", "")
            _cache[video_id] = segments
            cached = False

        # Pre-process segments to trim overlaps
        trimmed_segments = []
        for i, seg in enumerate(segments):
            trimmed_seg = seg.copy()
            if i < len(segments) - 1:
                max_dur = segments[i + 1]["start"] - seg["start"]
                if max_dur > 0:
                    trimmed_seg["duration"] = min(seg["duration"], max_dur)
                else:
                    trimmed_seg["duration"] = 0.1
            trimmed_segments.append(trimmed_seg)
        segments = trimmed_segments

        # Download video
        _set_job(job_id, progress="Downloading video…")
        video_path = download_video(video_id, work_dir)

        # Extract original audio track using FFmpeg
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
            
            # Skip empty text segments
            if not seg.get("text") or not seg["text"].strip():
                continue

            # Skip TTS for classical Arabic quotes (sliced from original audio)
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
                    # If speedup exceeds 1.3x, reprompt LLM for a shorter translation first
                    if rate_factor > 1.3:
                        # Target ~3.5 syllables per second of available time
                        max_syllables = int(available_time * 3.5)
                        actual_syllables = count_bangla_syllables(seg["text"])
                        print(f"  Segment {i}: rate={rate_factor:.2f}x > 1.3 -> reprompting for <={max_syllables} syllables (current {actual_syllables})...")
                        shorter_text = retranslate_shorter(seg, max_syllables)
                        if shorter_text != seg["text"] and count_bangla_syllables(shorter_text) <= int(max_syllables * 1.2):
                            seg["text"] = shorter_text
                            # Regenerate TTS with the shorter text at normal rate first
                            generate_segment_tts(seg["text"], out_path, speaker=speaker_id)
                            actual_duration = get_audio_duration(out_path)
                            # Recalculate rate factor after reprompt
                            if actual_duration > available_time:
                                rate_factor = actual_duration / available_time
                                rate_factor = min(rate_factor, 2.0)
                            else:
                                rate_factor = 1.0

                    if rate_factor > 1.02:
                        rate_factor = min(rate_factor, 2.0)
                        pct = int((rate_factor - 1.0) * 100)
                        rate_str = f"+{pct}%"
                        # Re-generate with native TTS rate speedup
                        generate_segment_tts(seg["text"], out_path, rate=rate_str, speaker=speaker_id)

            if (i + 1) % 5 == 0 or (i + 1) == total:
                _set_job(job_id, progress=f"Generating Bangla audio ({i + 1}/{total})…")

        # Calculate total video duration
        total_duration = max(s["start"] + s["duration"] for s in segments)

        _set_job(job_id, progress="Mixing dubbed audio track…")
        dubbed_audio = os.path.join(work_dir, "dubbed_audio.mp3")
        build_dubbed_audio(segments, seg_dir, original_audio_path, total_duration, dubbed_audio)

        _set_job(job_id, progress="Muxing final video…")
        out_filename = f"{video_id}_dubbed.mp4"
        out_path = os.path.join(OUTPUT_DIR, out_filename)
        mux_video_with_dubbed_audio(video_path, dubbed_audio, out_path)

        # Copy dubbed audio track for web playback
        audio_out_filename = f"{video_id}_dubbed.mp3"
        audio_out_path = os.path.join(OUTPUT_DIR, audio_out_filename)
        shutil.copy(dubbed_audio, audio_out_path)

        # --- Plaza manifest: persist per-video subdir so /api/plaza finds it ---
        video_dir = os.path.join(OUTPUT_DIR, video_id)
        os.makedirs(video_dir, exist_ok=True)
        video_artifact = f"{video_id}_dubbed.mp4"
        audio_artifact = f"{video_id}_dubbed.mp3"
        video_dir_video_path = os.path.join(video_dir, video_artifact)
        video_dir_audio_path = os.path.join(video_dir, audio_artifact)
        shutil.copy(out_path, video_dir_video_path)
        shutil.copy(audio_out_path, video_dir_audio_path)

        end_time = time.time()
        time_taken = round(end_time - start_time, 2)
        tokens = {
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "total_tokens": in_tokens + out_tokens,
            "cached": cached
        }

        # Segment-level transcript sidecar: this is what /api/transcript/{video_id}
        # serves to the plaza watch view for Bangla subtitles.
        transcript_artifact = f"{video_id}_transcript.json"
        transcript_path = os.path.join(video_dir, transcript_artifact)
        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "video_id": video_id,
                    "segments": segments,
                    "tokens": {"input": in_tokens, "output": out_tokens},
                    "time_taken": time_taken,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        manifest_path = os.path.join(video_dir, "manifest.json")
        manifest = {
            "video_id": video_id,
            "title": video_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "elapsed_seconds": time_taken,
            "source_video_seconds": total_duration,
            "dubbed_audio_seconds": total_duration,
            "segment_count": len(segments),
            "segments": segments,
            "tokens": {
                "input": in_tokens,
                "output": out_tokens,
            },
            "artifacts": {
                "dubbed_video": video_artifact,
                "dubbed_audio": audio_artifact,
                "transcript": transcript_artifact,
            },
            "files": {
                "dubbed_video": os.path.getsize(video_dir_video_path),
                "dubbed_audio": os.path.getsize(video_dir_audio_path),
                "transcript": os.path.getsize(transcript_path),
            },
        }
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        _set_job(
            job_id,
            status="done",
            progress="Complete!",
            url=f"/output/{video_id}/{video_artifact}",
            audio_url=f"/output/{video_id}/{audio_artifact}",
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


@app.get("/output/{video_id}/{filename}")
def download_output_nested(video_id: str, filename: str):
    """Serve dubbed files from per-video output subdirectories."""
    safe_video_id = os.path.basename(video_id)
    safe_filename = os.path.basename(filename)
    path = os.path.join(OUTPUT_DIR, safe_video_id, safe_filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Output file not found.")
    if filename.endswith(".mp3"):
        return FileResponse(path, media_type="audio/mpeg", filename=safe_filename)
    return FileResponse(path, media_type="video/mp4", filename=safe_filename)


@app.get("/output/{filename}")
def download_output_flat(filename: str):
    """Backward compatibility for older flat output files."""
    safe_filename = os.path.basename(filename)
    path = os.path.join(OUTPUT_DIR, safe_filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Output file not found.")
    if filename.endswith(".mp3"):
        return FileResponse(path, media_type="audio/mpeg", filename=safe_filename)
    return FileResponse(path, media_type="video/mp4", filename=safe_filename)


def _scan_output_for_manifests():
    results = []
    if not os.path.isdir(OUTPUT_DIR):
        return results
    for entry in os.listdir(OUTPUT_DIR):
        video_dir = os.path.join(OUTPUT_DIR, entry)
        if not os.path.isdir(video_dir):
            continue
        manifest_path = os.path.join(video_dir, "manifest.json")
        if not os.path.exists(manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8-sig") as f:
                manifest = json.load(f)
        except Exception:
            continue
        video_id = manifest.get("video_id") or entry
        artifacts = manifest.get("artifacts", {})
        files = manifest.get("files", {})
        video_file = artifacts.get("dubbed_video")
        audio_file = artifacts.get("dubbed_audio")
        title = manifest.get("title")
        if not title:
            title = video_id
        duration = manifest.get("source_video_seconds") or manifest.get("dubbed_audio_seconds")
        created_at = manifest.get("created_at")
        item = {
            "video_id": video_id,
            "title": title,
            # Set by watcher.py when it publishes an auto-dubbed upload. Absent
            # for videos dubbed by hand before the watcher existed.
            "channel": manifest.get("channel"),
            "published_at": manifest.get("published_at"),
            "source_url": manifest.get("source_url"),
            "thumbnail": manifest.get("thumbnail"),
            "duration_seconds": duration,
            "created_at": created_at,
            "video_url": f"/output/{video_id}/{video_file}" if video_file else None,
            "audio_url": f"/output/{video_id}/{audio_file}" if audio_file else None,
            "manifest": manifest,
            "file_sizes": {
                "video": files.get("dubbed_video"),
                "audio": files.get("dubbed_audio"),
            },
        }
        results.append(item)
    results.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return results


@app.get("/api/plaza")
def plaza_metadata():
    return {"videos": _scan_output_for_manifests()}


@app.get("/api/plaza/{video_id}")
def plaza_video_metadata(video_id: str):
    for item in _scan_output_for_manifests():
        if item["video_id"] == video_id:
            return item
    raise HTTPException(status_code=404, detail="Video not found in plaza.")


@app.get("/api/transcript/{video_id}")
def transcript_json(video_id: str):
    """Serve the segment-level transcript for a plaza video."""
    safe_video_id = os.path.basename(video_id)
    video_dir = os.path.join(OUTPUT_DIR, safe_video_id)
    if not os.path.isdir(video_dir):
        raise HTTPException(status_code=404, detail="Video not found in plaza.")

    # Prefer the transcript filename recorded in the manifest artifacts, if any.
    manifest_path = os.path.join(video_dir, "manifest.json")
    transcript_filename = f"{safe_video_id}_transcript.json"
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8-sig") as f:
                manifest = json.load(f)
            artifacts = manifest.get("artifacts", {})
            if artifacts.get("transcript"):
                transcript_filename = artifacts["transcript"]
        except Exception:
            pass

    transcript_path = os.path.join(video_dir, transcript_filename)
    if not os.path.exists(transcript_path):
        raise HTTPException(status_code=404, detail="Transcript not found for this video.")

    try:
        with open(transcript_path, "r", encoding="utf-8-sig") as f:
            transcript = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read transcript: {e}")

    return {"video_id": safe_video_id, "segments": transcript.get("segments", [])}


@app.get("/plaza", response_class=HTMLResponse)
def serve_plaza():
    plaza_path = os.path.join("static", "plaza.html")
    if not os.path.exists(plaza_path):
        raise HTTPException(status_code=404, detail="Plaza page not found.")
    with open(plaza_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# --- Human Evaluation Feedback ---

@app.post("/api/feedback")
async def submit_feedback(req: EvaluationRequest):
    try:
        record = append_feedback(req.model_dump())
        return {"status": "ok", "id": record["id"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save feedback: {e}")


@app.get("/api/feedback")
def list_feedback():
    return list(iter_feedback())


@app.get("/api/feedback/file")
def feedback_file_info():
    path = get_feedback_file_path()
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


# --- Health & Static Files ---

@app.get("/api/health")
def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/{full_path:path}")
def serve_frontend(full_path: str):  # noqa: ARG001
    return FileResponse("static/index.html")
