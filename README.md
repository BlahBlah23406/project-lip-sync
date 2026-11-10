# End-to-End Bangla Lecture Dubbing & Crowdsourced Translation Platform

An integrated system for translating, dubbing, and quality-checking English Islamic educational lectures into natural, synchronized Bangla (বাংলা).

The platform combines two complementary subsystems:
1. **Automated Dubbing Pipeline (`pipeline/`)**: A robust, unattended Python pipeline that handles video downloading, transcription, LLM translation with strict syllable-timing constraints, speech synthesis, formant-preserved audio mixing, and automated deterministic quality verification.
2. **Crowdsource Translation Platform (`crowdsource-translation/`)**: A multi-platform Flutter application (Android, iOS, Web) allowing native speakers and domain reviewers to transcribe, translate, record reference audio, and review translations sentence-by-sentence.

---

## How the Two Halves Form a Complete System

Translating and dubbing long-form educational lectures (20–60 minutes) into Bangla presents unique challenges:
- **Timing synchronization**: Spoken Bangla typically requires more syllables than English to express equivalent concepts. Over-compressing leads to unnatural speedups, while under-compressing breaks video-speech alignment.
- **Sacred vocabulary & honorifics**: Islamic lectures frequently use Arabic phrases, Quranic citations, and religious honorifics (e.g., ﷺ, SWT, RA) that automated translators often omit, secularize, or mispronounce.
- **Audio naturalness**: Synthetic speech must blend seamlessly with background room tone and maintain vocal formants without high-pitch distortion.

The architecture solves this through a closed-loop human-in-the-loop workflow:

```
                  ┌──────────────────────────────────────────────┐
                  │           Source English Video (YouTube)     │
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼
                  ┌──────────────────────────────────────────────┐
                  │          Automated Dubbing Pipeline          │
                  │   1. Transcript Extraction & Clustering      │
                  │   2. Honorific Protection (__SACRED_N__)     │
                  │   3. Syllable-budgeted LLM Translation       │
                  │   4. Edge-TTS Speech Synthesis               │
                  │   5. Formant-Preserving Audio Mixing         │
                  └──────────────┬───────────────────────────────┘
                                 │
                   Evaluated by Automatic QA Checker
                   (`quality_tester.py`: Grade A-F)
                                 │
                 ┌───────────────┴───────────────┐
                 │                               │
       High Score (>=75%)              Low Score or Flagged
                 │                               │
                 ▼                               ▼
      ┌──────────────────────┐        ┌──────────────────────────────────────┐
      │ Published Dubbed VOD │        │  Export Difficult/Failed Sentences   │
      └──────────────────────┘        └──────────────────┬───────────────────┘
                                                         │
                                                         ▼
                                      ┌──────────────────────────────────────┐
                                      │  Crowdsource Translation App (Flutter│
                                      │   - Sentence-by-sentence context     │
                                      │   - Voice recording & Vosk ASR       │
                                      │   - ML Kit handwriting input         │
                                      │   - Peer review & approval queue     │
                                      └──────────────────┬───────────────────┘
                                                         │
                                    Human-Verified Gold Standard
                                    (Exported via Admin Portal)
                                                         │
                                                         ▼
                                      ┌──────────────────────────────────────┐
                                      │ Model Ground-Truth & Exemplar Update │
                                      │ (Refines translator prompts & terms) │
                                      └──────────────────────────────────────┘
```

### Integration Workflow & Data Transfer

1. **Extraction & Seeding**: The pipeline extracts lecture transcripts into discrete time-coded sentences (`captions.py`). These segments can be imported into Cloud Firestore to seed the crowdsource translation queue with context (surrounding sentences and model suggestions).
2. **Crowdsourced Refinement & Review**: Contributors translate assigned sentences using the mobile or web app. Reviewers inspect submissions in the review screen, either approving them or requesting specific corrections.
3. **Feedback into Pipeline**: Verified translations and audio recordings are exported through the web admin portal as structured JSON. These human-verified pairs serve as domain exemplars for the pipeline's LLM translation prompts and pronunciation guides for TTS.
4. **Current Integration Status**: 
   - *Automated in repository*: Automated end-to-end dubbing, honorific pre/post-processing, audio mixing with dynamic ducking, and deterministic QA scoring.
   - *Manual transfer points*: Exporting reviewed datasets from the Firestore Admin UI (`/admin`) and importing lecture batches into Firestore are performed via administrative scripts and JSON datasets, maintaining decoupled operational safety between server jobs and the live mobile backend.

---

## Subsystem 1: Automated Dubbing Pipeline (`pipeline/`)

The pipeline runs on Python 3.10+ and FFmpeg, designed for unattended execution on server instances (e.g., Code-47).

### Key Architectural Features

- **Resumable Checkpointing**: Every stage checkpoints intermediate files under `output/<video_id>/.work/`. If interrupted or restarted, the pipeline resumes from the last completed segment without repeating expensive operations.
- **Syllable-Budgeted Translation (`translator.py`)**:
  - Calculates speech duration budgets per segment (target: ~3.5 spoken syllables per second).
  - Pre-processes religious honorifics and sacred terms into `__SACRED_N__` tokens before LLM translation, restoring them accurately in Bengali script afterwards.
  - Automatically reprompts for concise phrasing if a translation exceeds the allotted time slot.
- **Edge-TTS with Bounded Network Retries (`dubber.py`)**: Synthesizes natural speech using regional Bangla voices (`bn-BD-PradeepNeural`, `bn-BD-NabanitaNeural`) with exponential backoff to handle upstream throttling.
- **Formant-Preserved Audio Mixing (`mixer.py`)**:
  - Employs Rubberband with `formant=preserved:transients=smooth` to prevent chipmunk distortion when tempo adjustments are required.
  - Generates deterministic gain envelopes for background audio ducking (preserving natural room tone during speech pauses).
- **Automatic Deterministic QA Checker (`quality_tester.py`)**:
  - **Content Quality**: Evaluates Bengali script purity, unhandled Latin characters, formatting cleanliness, duration markers, and repetition artifacts.
  - **Audio & Timing Quality**: Checks caption synchronization, on-time segment ratios, speed ceiling compliance, clipping, and RMS volume.
  - Computes a composite 0–100 quality score, assigns a letter grade (A–F), and gates output publication.

### Quick Start: Pipeline

```bash
cd pipeline

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env with your LLM API keys (Anthropic or Ollama endpoint)

# Run dubbing pipeline on a video ID
python run_pipeline.py <VIDEO_ID>

# Run automated QA evaluation on output
python quality_tester.py <VIDEO_ID>

# Run web review interface
uvicorn main:app --reload --port 8000
```

### Running Pipeline Unit Tests

```bash
pytest tests/ -v
```

---

## Subsystem 2: Crowdsource Translation Platform (`crowdsource-translation/`)

The crowdsourcing application is built using Flutter (Dart) and Firebase, targeting Android, iOS, and Web.

### Key Capabilities

- **Context-Aware Translation**: Displays 2 preceding and 2 succeeding sentences alongside model-suggested Bangla text to provide linguistic context.
- **Multimodal Inputs**:
  - **Voice Recording**: Captures 16 kHz mono WAV audio, validated and uploaded directly to Firebase Storage.
  - **On-Device Speech Recognition**: Offline Bangla ASR via Vosk / Sherpa-ONNX streaming Zipformer neural network.
  - **Handwriting Recognition**: On-device digital ink recognition powered by Google ML Kit.
  - **Direct Text Input**: Keyboard input with native script support.
- **Review & Verification Screen**: Dedicated queue for verified reviewers to approve, reject, or comment on submitted translations.
- **Admin Management & Export**: Web console (`/admin`) for tracking contributor quotas, importing new lecture corpora, and exporting reviewed datasets.
- **Security & Concurrency**: Firestore security rules enforcing user-bound audio uploads, quota limits, and atomic submission batching.

### Quick Start: Crowdsource App

```bash
cd crowdsource-translation

# Install Flutter dependencies
flutter pub get

# Download required on-device ASR model files into assets/models/bn-zipformer/
# (See assets/models/README.md for download instructions)

# Configure Firebase
flutterfire configure

# Run on Web
flutter run -d chrome

# Run on connected mobile device
flutter run
```

### Running Crowdsource App Tests

```bash
cd crowdsource-translation
flutter test
```

---

## Repository Structure

```
.
├── pipeline/                             # Automated dubbing pipeline
│   ├── captions.py                       # Caption fetching and gap-based clustering
│   ├── check_progress.py                 # Pipeline progress reporting
│   ├── dubber.py                         # Edge-TTS voice generation with retries
│   ├── feedback_store.py                 # User review and rating persistence
│   ├── ffmpeg_paths.py                   # Cross-platform FFmpeg path resolution
│   ├── launch_pipeline.py                # Detached background process launcher
│   ├── main.py                           # FastAPI web service & Plaza UI endpoints
│   ├── mixer.py                          # Time-stretching, formant preservation & ducking
│   ├── quality_tester.py                 # Deterministic QA scorer & letter grading
│   ├── requirements.txt                  # Python dependencies
│   ├── run_pipeline.py                   # Master pipeline orchestrator
│   ├── run_until_done.py                 # Watchdog loop for complete runs
│   ├── sentence_cleanup.py               # Text normalization and token filters
│   ├── test_runner.py                    # Pipeline integration test runner
│   ├── translator.py                     # Syllable-budgeted LLM translation & sacred terms
│   ├── verify_output.py                  # Final media output verification
│   ├── verify_timing.py                  # Independent Whisper timing drift audit
│   ├── watcher.py                        # Automated YouTube channel polling state machine
│   ├── watcher_config.json               # Channel watch list and limits
│   ├── ytdlp_runtime.py                  # yt-dlp JS runtime environment setup
│   ├── static/                           # Web UI assets (HTML/JS/CSS)
│   ├── tests/                            # Pytest suite (audio quality, timing, captions)
│   ├── .env.example                      # Sample configuration for API keys
│   └── README.md                         # Pipeline technical documentation
│
├── crowdsource-translation/              # Flutter crowdsourcing mobile & web app
│   ├── android/                          # Android project configuration & Gradle scripts
│   ├── assets/                           # App assets (branding, model definitions)
│   ├── ios/                              # iOS project configuration & CocoaPods
│   ├── lib/                              # Application source code
│   │   ├── main.dart                     # App entry point & routing
│   │   ├── firebase_options.dart         # Generated Firebase client options
│   │   ├── models/                       # Domain models (Sentence, Translation, Quota)
│   │   ├── screens/                      # Screens (Home, Translation, Review, Admin)
│   │   ├── services/                     # Firestore, Auth, Storage, ML Kit services
│   │   ├── utils/                        # Audio helpers, speech recognition bridge
│   │   └── widgets/                      # Custom widgets (HandwritingPad, etc.)
│   ├── linux/, macos/, windows/, web/    # Desktop and web platform shells
│   ├── test/                             # Flutter test suite (widget, unit, rules)
│   ├── firestore.rules                   # Cloud Firestore security rules
│   ├── storage.rules                     # Firebase Storage security rules
│   ├── pubspec.yaml                      # Flutter package definition
│   └── README.md                         # Crowdsource platform documentation
│
├── .gitignore                            # Root gitignore (credentials, models, builds)
├── LICENSE                               # MIT License
└── README.md                             # Top-level project architecture & overview
```

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
