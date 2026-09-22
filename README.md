<p align="center">
  <img src="static/logo.png" alt="Project Lip Sync Logo" width="200" />
</p>

# Project Lip Sync

[![Checks](https://github.com/BlahBlah23406/project-lip-sync/actions/workflows/checks.yml/badge.svg)](https://github.com/BlahBlah23406/project-lip-sync/actions/workflows/checks.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An end-to-end pipeline that dubs English lecture videos into Bangla — transcript to
translation to synthesized speech to a remixed audio track — and a watcher that runs it
unattended against a list of YouTube channels.

The hard part is not any single step. It is finishing a 25–60 minute job, on a consumer
machine, without a human watching, and knowing whether the result is actually good.

## How it works

Seven phases, each one checkpointed to `output/<video_id>/.work/`:

| # | Phase | Output |
|---|-------|--------|
| 1 | Fetch transcript | `transcript.json` |
| 2 | Translate (LLM, batched) | `translated.json` |
| 3 | Download source video | `video.mp4` |
| 4 | Extract original audio | `original_audio.mp3` |
| 5 | Bangla TTS, per segment | `segments/seg_NNNN.mp3` |
| 6 | Mix and align | `dubbed_audio.mp3` |
| 7 | Publish | `output/<video_id>/` |

Every phase skips work already on disk, so a killed run loses at most the one segment it
was mid-way through. Rerunning the identical command resumes.

## Design decisions worth knowing

**Resumability is the architecture, not a feature.** An earlier version kept everything in
a `tempfile.mkdtemp()`, so a killed run lost the downloaded video, the paid-for
translation, and hundreds of rendered clips. Retries restarted from zero and could never
outrun the timeout.

**Every network call is bounded.** `edge_tts` has no timeout, and the free endpoint stalls
the websocket rather than erroring when it throttles a long burst. Unbounded, that
presents as a hang, not a failure — the run holds its lock, looks alive, and nothing
retries it. A stall watchdog dumps every thread's stack and exits non-zero if no phase
reports progress.

**One component decides speed.** Speeding up speech to fit a caption slot used to happen in
two places that could not see each other, and the factors multiplied into unintelligible
audio. The mixer is now the only authority; a segment that does not fit is allowed to run
long, and the lag is absorbed by natural pauses in the lecture.

**Ducking is computed, not guessed.** The schedule already knows exactly when Bangla
speaks, so the English bed is multiplied by an explicit gain envelope rendered to an audio
file — one ffmpeg filter, flat memory, deterministic — instead of a sidechain compressor
inferring it.

**Output is verified, not assumed.** Coverage, speed, and alignment are checked before a
run is called done. Coverage alone will happily report success for audio nobody can hear,
so alignment is measured separately.

## Requirements

- Python 3.10+
- ffmpeg on `PATH`
- An API key for an OpenAI/Ollama-compatible endpoint, or an Anthropic key

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in your key
```

## Usage

Dub one video:

```bash
python run_pipeline.py <VIDEO_ID>
```

For full-length episodes, launch detached so the run outlives the shell that started it:

```bash
python launch_pipeline.py <VIDEO_ID>
python check_progress.py <VIDEO_ID>
```

Web UI (paste a YouTube URL, watch progress, download the result):

```bash
uvicorn main:app --reload        # http://127.0.0.1:8000
```

## Unattended operation

`watcher.py` polls the channels in `watcher_config.json` and dubs new uploads on its own.

```bash
python watcher.py bootstrap      # mark the current feed as seen; run once
python watcher.py check          # one poll + reconcile pass — this is what you schedule
python watcher.py status         # what is queued, running, done, deferred, skipped
```

It is a polled state machine, not a daemon: each invocation does one reconcile pass and
exits, with all state in `watcher_state.json`. A long-lived process dies at reboot, at
logoff, and at any caller's timeout, and then nobody notices for a week. A scheduled
20-second script cannot rot the same way, and a missed tick costs nothing — the next tick
re-derives everything from disk.

Videos that are live or premiering are deferred and re-probed on a backoff until they
settle into a normal VOD, rather than being skipped permanently.

## Layout

| File | Role |
|------|------|
| `run_pipeline.py` | The pipeline; phase orchestration, checkpointing, watchdog |
| `watcher.py` | Channel polling, filtering, scheduling, reconciliation |
| `mixer.py` | Placement, speed policy, ducking, mux |
| `translator.py` | Batched translation, length budgeting, backend selection |
| `dubber.py` | Edge TTS with bounded retries |
| `captions.py` | Transcript fetch and segment clustering |
| `main.py` | FastAPI app and web UI |
| `verify_output.py`, `verify_timing.py` | Coverage and alignment audits |
