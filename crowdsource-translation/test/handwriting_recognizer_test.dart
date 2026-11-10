import 'package:crowdsource_translation/services/handwriting_recognizer.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('google_mlkit_digital_ink_recognizer');
  final calls = <MethodCall>[];
  setUp(() {
    debugDefaultTargetPlatformOverride = TargetPlatform.android;
    calls.clear();
  });
  tearDown(() {
    debugDefaultTargetPlatformOverride = null;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  test('downloads Bangla model and preserves dots and pointer timestamps',
      () async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      calls.add(call);
      if (call.method == 'vision#manageInkModels') {
        return call.arguments['task'] == 'check' ? false : 'success';
      }
      if (call.method == 'vision#startDigitalInkRecognizer') {
        return [
          {'text': 'বাংলা', 'score': 0.0}
        ];
      }
      return null;
    });
    final recognizer = OnDeviceHandwritingRecognizer();
    expect(
        await recognizer.recognise([
          const InkStroke([Offset(20, 30)], timestamps: [120]),
          const InkStroke([Offset(40, 50), Offset(45, 55)],
              timestamps: [200, 220]),
        ]),
        'বাংলা');
    expect(calls[0].arguments['model'], 'bn');
    expect(calls[1].arguments['task'], 'download');
    final ink = calls[2].arguments['ink'];
    expect(ink['strokes'], hasLength(2));
    expect(ink['strokes'][0]['points'], [
      {'x': 20.0, 'y': 30.0, 't': 120}
    ]);
    expect(ink['strokes'][1]['points'][1]['t'], 220);
    await recognizer.close();
    expect(calls.last.method, 'vision#closeDigitalInkRecognizer');
  });

  test('download errors reach the screen instead of returning empty text',
      () async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      if (call.arguments['task'] == 'check') return false;
      throw PlatformException(code: 'DOWNLOAD_FAILED');
    });
    expect(
        OnDeviceHandwritingRecognizer().recognise([
          const InkStroke([Offset(1, 1)], timestamps: [0]),
        ]),
        throwsA(isA<PlatformException>()));
  });
}
