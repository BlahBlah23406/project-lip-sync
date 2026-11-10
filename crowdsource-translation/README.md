# Crowdsource Translation Platform

A multi-platform Flutter application (Android, iOS, Web) designed to crowdsource human translations, audio recordings, and reviews for English-to-Bangla Islamic lectures.

## Overview

The crowdsourcing application enables volunteers and editors to contribute and verify translations at sentence-level granularity. It acts as the human-in-the-loop review and quality assurance layer that feeds ground truth translations into the automated dubbing pipeline.

### Key Capabilities

- **Sentence-by-Sentence Translation**: Contributors translate English lecture transcript segments with preceding/following sentence context and model-suggested Bangla text.
- **Multimodal Input**:
  - Voice recording (WAV format, 16 kHz mono) uploaded to Firebase Storage.
  - On-device Bangla Speech-to-Text (ASR) via Vosk / Sherpa-ONNX streaming Zipformer model.
  - Handwriting recognition with on-device Google ML Kit Digital Ink recognition.
  - Keyboard text input.
- **Review & Quality Control**:
  - Review queue for editors to inspect submitted translations and audio.
  - Approval and change-request workflows with reviewer feedback.
- **Administrative Portal**:
  - Sentence assignment and dataset management.
  - Bulk import of lecture segments and JSON dataset export.
  - Daily translation quota tracking and contributor stats.

## Architecture

- **Frontend**: Flutter (Dart) supporting mobile and responsive web interfaces.
- **Backend**: Firebase Authentication, Cloud Firestore (atomic batch writes, security rules), and Firebase Storage.
- **On-Device Inference**:
  - Vosk / `sherpa_onnx` streaming Zipformer for offline Bangla speech transcription.
  - Google ML Kit Digital Ink for Bangla handwriting recognition.

## Project Structure

```
crowdsource-translation/
├── android/               # Android native configuration and Gradle build
├── ios/                   # iOS native workspace and Pod configuration
├── web/                   # Web platform assets and PWA manifest
├── lib/
│   ├── main.dart          # Application entrypoint
│   ├── firebase_options.dart # Firebase configuration
│   ├── models/            # Domain models (Sentence, UserTranslation, DailyQuota)
│   ├── screens/           # UI screens (Home, Translation, Review, Admin, Login)
│   ├── services/          # Firebase Auth, Firestore, ML Kit, Audio upload
│   ├── utils/             # Cross-platform audio bytes, speech helper, download helpers
│   └── widgets/           # Reusable widgets (HandwritingPad, etc.)
├── test/                  # Unit and integration tests
├── firestore.rules        # Security rules for Cloud Firestore
├── storage.rules          # Security rules for Firebase Storage
└── pubspec.yaml           # Flutter dependencies and asset declarations
```

## Getting Started

### Prerequisites

- Flutter SDK (>= 3.3.0)
- Firebase Project with Firestore and Storage enabled

### Setup

1. Configure Firebase:
   ```bash
   flutterfire configure
   ```
2. Download required on-device ASR models as documented in `assets/models/README.md`.
3. Install dependencies:
   ```bash
   flutter pub get
   ```

### Running

- Web:
  ```bash
  flutter run -d chrome
  ```
- Mobile (connected Android or iOS device/emulator):
  ```bash
  flutter run
  ```

### Running Tests

```bash
flutter test
```
