import 'dart:io';
import 'dart:isolate';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa;

/// On-device Bangla speech recognition with the Vosk streaming Zipformer
/// (alphacep/vosk-model-small-streaming-bn) running in sherpa-onnx.
///
/// Replaced whisper.cpp "small" (which was really Whisper *tiny*): on 40 FLEURS
/// Bangla clips tiny scored 96.6% WER / 47.4% CER, this model 16.2% / 5.0% at
/// roughly a tenth of the decode time. The encoder is int8 (MatMul weights only;
/// quantising every op destroyed accuracy), so the model ships at ~29 MB.
class SpeechHelper {
  // Bump when the bundled model files change so stale copies are replaced.
  static const _modelVersion = 'bn-zipformer-vosk-2026-02-09-int8';
  static const _assetDir = 'assets/models/bn-zipformer';
  static const _files = ['encoder.onnx', 'decoder.onnx', 'joiner.onnx', 'tokens.txt'];

  String? _modelDir;

  Future<void> init() async {
    final base = await getApplicationSupportDirectory();
    final dir = Directory('${base.path}/$_modelVersion');
    await dir.create(recursive: true);
    for (final name in _files) {
      final file = File('${dir.path}/$name');
      final data = await rootBundle.load('$_assetDir/$name');
      if (await file.exists() && await file.length() == data.lengthInBytes) continue;
      await file.writeAsBytes(
        data.buffer.asUint8List(data.offsetInBytes, data.lengthInBytes),
        flush: true,
      );
    }
    // Fail init loudly rather than "succeeding" and returning empty text later.
    sherpa.initBindings();
    _modelDir = dir.path;
  }

  /// Transcribes a 16 kHz mono WAV. [language] is kept for interface
  /// compatibility; this model is Bangla-only.
  Future<String?> transcribe(String path, String language) async {
    final modelDir = _modelDir;
    if (modelDir == null) return null;
    try {
      final text = await Isolate.run(() => _decode(modelDir, path));
      return text.trim();
    } catch (e) {
      debugPrint('Speech transcription error: $e');
      return null;
    }
  }
}

// Runs in a background isolate: FFI handles cannot cross isolates, so the
// bindings and recognizer are created here and freed before returning.
String _decode(String modelDir, String wavPath) {
  sherpa.initBindings();
  final wave = sherpa.readWave(wavPath);
  if (wave.samples.isEmpty) {
    throw Exception('could not read audio: $wavPath');
  }
  final recognizer = sherpa.OnlineRecognizer(
    sherpa.OnlineRecognizerConfig(
      model: sherpa.OnlineModelConfig(
        transducer: sherpa.OnlineTransducerModelConfig(
          encoder: '$modelDir/encoder.onnx',
          decoder: '$modelDir/decoder.onnx',
          joiner: '$modelDir/joiner.onnx',
        ),
        tokens: '$modelDir/tokens.txt',
        numThreads: 2,
        debug: false,
      ),
      enableEndpoint: false,
    ),
  );
  final stream = recognizer.createStream();
  try {
    stream.acceptWaveform(samples: wave.samples, sampleRate: wave.sampleRate);
    // Trailing silence flushes the last words out of the streaming encoder.
    stream.acceptWaveform(
      samples: Float32List((wave.sampleRate * 0.5).round()),
      sampleRate: wave.sampleRate,
    );
    stream.inputFinished();
    while (recognizer.isReady(stream)) {
      recognizer.decode(stream);
    }
    return recognizer.getResult(stream).text;
  } finally {
    stream.free();
    recognizer.free();
  }
}
