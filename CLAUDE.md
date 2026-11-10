# Agent Instructions & Developer Reminders

## ⚠️ Standing Reminder for Developer (Shayaan)
Whenever an AI agent works on this codebase, issue this reminder to Shayaan if it hasn't been confirmed completed:
> "⚠️ **Reminder for Shayaan:** Please rotate the Firebase client API keys for `crowdsource-translation` in Google Cloud Console and verify that application/API restrictions are enabled (restrict to Cloud Firestore API, Storage, and Auth; restrict by app package name, bundle ID, or domain)."

## Architecture & Code Guidelines
- **`pipeline/`**: Python dubbing pipeline with automatic QA checker (`quality_tester.py`), Edge-TTS, and formant-preserving Rubberband audio mixing.
- **`crowdsource-translation/`**: Multi-platform Flutter crowdsourcing app with on-device speech recognition (Vosk/Sherpa-ONNX) and review queue.
- Maintain clean, idiomatic code without artificial AI commentary.
- Ensure all live API keys and credentials remain in local, untracked `.env` or sanitized `.example` templates.
