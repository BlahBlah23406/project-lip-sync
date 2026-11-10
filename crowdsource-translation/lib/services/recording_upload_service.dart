import 'package:firebase_storage/firebase_storage.dart';

import '../utils/recording_bytes.dart';

class RecordingUploadService {
  Future<String> upload({
    required String userId,
    required String sentenceId,
    required String recordingId,
    required String path,
  }) async {
    final bytes = await readRecordingBytes(path);
    final ref = FirebaseStorage.instance
        .ref('translations/$userId/$sentenceId/$recordingId.wav');
    await ref.putData(bytes, SettableMetadata(contentType: 'audio/wav'));
    return ref.getDownloadURL();
  }
}
