import 'dart:io';
import 'dart:typed_data';

Future<Uint8List> readRecordingBytes(String path) async {
  final file = File(path);
  final size = await file.length();
  if (size <= 44) throw StateError('The recording contains no audio.');
  if (size > 20 * 1024 * 1024) {
    throw StateError('The recording exceeds the 20 MB upload limit.');
  }
  final bytes = await file.readAsBytes();
  if (String.fromCharCodes(bytes.take(4)) != 'RIFF' ||
      String.fromCharCodes(bytes.skip(8).take(4)) != 'WAVE') {
    throw StateError('The saved recording is not a WAV file.');
  }
  return bytes;
}
