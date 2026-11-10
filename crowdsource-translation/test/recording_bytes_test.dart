import 'dart:io';
import 'dart:typed_data';

import 'package:crowdsource_translation/utils/recording_bytes_mobile.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  late Directory dir;
  setUp(() async =>
      dir = await Directory.systemTemp.createTemp('recording-test'));
  tearDown(() async => dir.delete(recursive: true));

  test('WAV bytes are preserved for upload', () async {
    final bytes = Uint8List(100);
    bytes.setAll(0, 'RIFF'.codeUnits);
    bytes.setAll(8, 'WAVE'.codeUnits);
    final file = await File('${dir.path}/voice.wav').writeAsBytes(bytes);
    expect(await readRecordingBytes(file.path), bytes);
  });

  test('empty, invalid and oversized recordings are rejected before upload',
      () async {
    final file = File('${dir.path}/voice.wav');
    await file.writeAsBytes(Uint8List(44));
    await expectLater(readRecordingBytes(file.path), throwsStateError);
    await file.writeAsBytes(Uint8List(100));
    await expectLater(readRecordingBytes(file.path), throwsStateError);
    final handle = await file.open(mode: FileMode.write);
    await handle.truncate(20 * 1024 * 1024 + 1);
    await handle.close();
    await expectLater(readRecordingBytes(file.path), throwsStateError);
  });
}
