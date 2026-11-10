import 'dart:ui';

import 'package:flutter/foundation.dart';
import 'package:google_mlkit_digital_ink_recognition/google_mlkit_digital_ink_recognition.dart'
    as ml;

/// One continuous stroke, including timestamps captured from pointer events.
class InkStroke {
  final List<Offset> points;
  final List<int> timestamps;

  const InkStroke(this.points, {this.timestamps = const []});

  bool get isEmpty => points.isEmpty;
}

abstract class HandwritingRecognizer {
  bool get isAvailable;
  Future<String?> recognise(List<InkStroke> strokes,
      {String languageTag = 'bn'});
  Future<void> close() async {}
}

class UnavailableHandwritingRecognizer extends HandwritingRecognizer {
  @override
  bool get isAvailable => false;

  @override
  Future<String?> recognise(List<InkStroke> strokes,
          {String languageTag = 'bn'}) async =>
      null;
}

/// Downloads the Bangla model once, then recognises ink on device.
class OnDeviceHandwritingRecognizer extends HandwritingRecognizer {
  final _manager = ml.DigitalInkRecognizerModelManager();
  ml.DigitalInkRecognizer? _recognizer;

  @override
  bool get isAvailable =>
      !kIsWeb &&
      (defaultTargetPlatform == TargetPlatform.android ||
          defaultTargetPlatform == TargetPlatform.iOS);

  @override
  Future<String?> recognise(List<InkStroke> strokes,
      {String languageTag = 'bn'}) async {
    if (!isAvailable) {
      throw UnsupportedError(
          'Handwriting recognition requires Android or iOS.');
    }
    if (strokes.every((stroke) => stroke.isEmpty)) return null;
    if (!await _manager.isModelDownloaded(languageTag)) {
      final downloaded =
          await _manager.downloadModel(languageTag, isWifiRequired: false);
      if (!downloaded) {
        throw StateError('The handwriting model could not download.');
      }
    }
    if (_recognizer?.languageCode != languageTag) {
      await _recognizer?.close();
      _recognizer = ml.DigitalInkRecognizer(languageCode: languageTag);
    }
    final ink = ml.Ink();
    var fallbackTime = 0;
    for (final stroke in strokes.where((stroke) => !stroke.isEmpty)) {
      final nativeStroke = ml.Stroke();
      for (var i = 0; i < stroke.points.length; i++) {
        final point = stroke.points[i];
        nativeStroke.points.add(ml.StrokePoint(
          x: point.dx,
          y: point.dy,
          t: stroke.timestamps.length == stroke.points.length
              ? stroke.timestamps[i]
              : fallbackTime++,
        ));
      }
      ink.strokes.add(nativeStroke);
    }
    final candidates = await _recognizer!.recognize(ink);
    return candidates.isEmpty ? null : candidates.first.text.trim();
  }

  @override
  Future<void> close() async {
    await _recognizer?.close();
    _recognizer = null;
  }
}
