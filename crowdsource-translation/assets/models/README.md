# Speech Recognition Models

This directory contains assets for on-device Bangla speech recognition (ASR) using `sherpa_onnx`.

## Vosk / Sherpa-ONNX Zipformer Bangla Model

The streaming Zipformer model files are placed under `assets/models/bn-zipformer/`:
- `tokens.txt` (vocabulary tokens, tracked in repository)
- `encoder.onnx` (encoder neural network, ~26 MB)
- `decoder.onnx` (decoder neural network, ~2 MB)
- `joiner.onnx` (joiner neural network, ~1 MB)

Due to file size constraints, binary model weights (`.onnx`) are omitted from version control.

### Setup Instructions

1. Obtain the quantized Bangla streaming Zipformer model from the sherpa-onnx model repository:
   - Model: `sherpa-onnx-streaming-zipformer-bn-vosk-2026-02-09`
2. Place `encoder.onnx`, `decoder.onnx`, and `joiner.onnx` into `assets/models/bn-zipformer/`.
3. Verify that `pubspec.yaml` contains `- assets/models/bn-zipformer/`.
4. Run `flutter pub get`.
