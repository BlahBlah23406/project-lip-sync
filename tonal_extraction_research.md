# Lightweight On-Device Tonal / Pitch Contour Extraction for Bengali Audio

## Goal
Identify models and pipelines that can extract pitch contours and tonal/prosodic features **locally on a mobile device** (Android/iOS) without sending raw audio to a cloud server. The output will feed LipSync’s dubbing quality pipeline (intonation matching, sentence-splicing timing, and quality alerts).

## Evaluation Criteria
| Criterion | Why it matters |
|---|---|
| **On-device inference** | No cloud round-trip; works offline and preserves privacy. |
| **Small footprint** | Total model + runtime under ~50 MB on device. |
| **Low latency** | <100 ms for a typical 5–10 s sentence on a mid-range phone. |
| **CPU-only friendly** | Should not require a GPU/DSP exclusive to flagship devices. |
| **Bengali suitability** | Handles voicing, fricatives, and nasals common in Bangla; pitch range ~75–300 Hz for adult voices. |
| **License / deployability** | Permissive license for redistribution in a Flutter app. |

## Candidate Models

### 1. SPICE (Self-Supervised Pitch Estimation)
- **Source:** TensorFlow Hub (`tfhub.dev/google/spice/2`)
- **Type:** Lightweight CNN trained with self-supervision (harmonic distances).
- **Pros:** Very small (~1 MB), designed for mobile/browser, returns pitch in Hz and an uncertainty flag.
- **Cons:** TensorFlow Lite dependency; less accurate on noisy speech than CREPE; pitch output is quantized to 32 bins.
- **Fit:** Good if we already use TFLite elsewhere; marginal for high-quality dubbing.

### 2. CREPE / torchcrepe / onnxcrepe
- **Source:** `maxrmorrison/torchcrepe`, `yqzhishen/onnxcrepe`, `Scicom-intl/crepe-onnx`
- **Type:** Deep neural pitch tracker (classification over 360 pitch bins).
- **Variants:** `tiny` (~1.8 MB) and `full` (~7 MB).
- **Pros:** State-of-the-art accuracy on monophonic speech; `onnxcrepe` runs on ONNX Runtime, which already ships with `sherpa_onnx` in the crowdsource app; tiny model is competitive.
- **Cons:** Pitch tracking only (no voiced/unvoiced flag without post-processing); tiny variant trades a little accuracy for speed.
- **Fit:** **Top recommendation** — reuse ONNX Runtime, proven mobile path, and tiny model fits footprint budget.

### 3. PyWorld / WORLD Vocoder
- **Source:** `JeremyCCHsu/Python-Wrapper-for-World-Vocoder`
- **Type:** Classic signal-processing vocoder (Dio → Harvest → StoneMask for pitch, plus spectral envelope and aperiodicity).
- **Pros:** No neural network; tiny code footprint; gives pitch, spectral envelope, and aperiodicity in one pass; CPU-only and fast.
- **Cons:** Lower accuracy on very noisy audio; parameter tuning (frame period, f0 floor/ceiling) needed for Bengali.
- **Fit:** Good baseline or fallback; can run in Dart via FFI to a small C++ library, or pre-process on server.

### 4. Parselmouth (Praat in Python)
- **Source:** `praat/praat` / `YannickJadoul/Parselmouth`
- **Type:** Praat’s robust pitch/acoustic analysis.
- **Pros:** Gold-standard phonetic analysis; many tonal parameters (pitch, intensity, formants, jitter, shimmer).
- **Cons:** Heavy binary (~>50 MB), not mobile-friendly; Python-only.
- **Fit:** Server-side benchmarking only, not on-device.

## Recommended Architecture
1. **Primary on-device extractor:** `onnxcrepe` tiny model running on ONNX Runtime (already a dependency after the Vosk/sherpa_onnx migration). It gives a smooth f0 contour per sentence.
2. **Offline/QA reference:** `PyWorld` Harvest + StoneMask for server-side ground-truth pitch, used to validate on-device contours and tune error thresholds.
3. **Output format:** Per-sentence JSON array of `{time_ms, f0_hz, voiced}` at 10 ms hop. Store alongside dubbed segment for intonation comparison.

## Next Steps
- Export `crepe-tiny.onnx` and bundle it in `assets/models/pitch/crepe-tiny.onnx` (~2 MB).
- Add a Dart isolate that runs ONNX Runtime pitch inference on recorded/dubbed audio, mirroring the `speech_helper` isolate pattern.
- Compare on-device CREPE contour against PyWorld on 20 representative Bengali sentences; set a tolerance threshold for dubbing quality alerts.

## References
- `yqzhishen/onnxcrepe` — https://github.com/yqzhishen/onnxcrepe
- `Scicom-intl/crepe-onnx` — https://huggingface.co/Scicom-intl/crepe-onnx
- `maxrmorrison/torchcrepe` — https://github.com/maxrmorrison/torchcrepe
- `JeremyCCHsu/Python-Wrapper-for-World-Vocoder` — https://github.com/JeremyCCHsu/Python-Wrapper-for-World-Vocoder
- SPICE on TensorFlow Hub — https://tfhub.dev/google/spice/2


## Verification boundary (2026-11-01)

This is a substantive architecture survey, not a measured mobile benchmark. The footprint, latency, model-size and accuracy statements above have not been independently reproduced on the target phone. In particular, treat the <100 ms target as a proposed acceptance criterion; validate SPICE output representation/model size and CREPE variant sizes against the exact exported artifact before implementation. Existing sherpa_onnx packaging does not by itself prove that an arbitrary CREPE model can use its bundled runtime through the current Flutter API. Confirm runtime access, export compatibility, redistribution licenses and CPU profiling before selecting a deployment candidate. Bengali is not generally a lexical tone language; the intended feature is prosodic F0/intonation, including voiced/unvoiced confidence, rather than lexical tone classification. WORLD output is a comparison reference, not human-annotated ground truth. No on-device results are claimed by this document.
