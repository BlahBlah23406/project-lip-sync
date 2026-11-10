class WhisperHelper {
  Future<void> init() async {
    throw UnsupportedError(
        'On-device speech transcription requires Android or iOS.');
  }

  Future<String?> transcribe(String path, String language) async {
    throw UnsupportedError(
        'On-device speech transcription requires Android or iOS.');
  }
}
