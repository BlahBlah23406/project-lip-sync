import 'translation.dart';

class Sentence {
  final String id;
  final String text;
  final int translationCount;
  final String? audioUrl;
  final String? transcription;
  final List<Translation> translations;

  /// The lines spoken just before and after this one in the source video.
  /// A word often has several Bangla renderings and only the surrounding talk
  /// says which one fits, so contributors see these and the dataset keeps them.
  final String? contextBefore;
  final String? contextAfter;

  /// Where the sentence came from (LipSync video id) and what the dubbing
  /// model produced for that segment, kept so the dataset pairs the model's
  /// attempt with the human correction.
  final String? sourceVideoId;
  final String? modelBangla;
  final bool lowQuality;

  Sentence({
    required this.id,
    required this.text,
    this.translationCount = 0,
    this.audioUrl,
    this.transcription,
    this.translations = const [],
    this.contextBefore,
    this.contextAfter,
    this.sourceVideoId,
    this.modelBangla,
    this.lowQuality = false,
  });

  bool get hasContext =>
      (contextBefore ?? '').isNotEmpty || (contextAfter ?? '').isNotEmpty;

  factory Sentence.fromMap(Map<String, dynamic>? data, String documentId) {
    if (data == null) {
      return Sentence(id: documentId, text: '');
    }
    return Sentence(
      id: documentId,
      text: data['text']?.toString() ?? '',
      translationCount: (data['translationCount'] as num?)?.toInt() ?? 0,
      audioUrl: data['audioUrl']?.toString(),
      transcription: data['transcription']?.toString(),
      translations: (data['translations'] as List<dynamic>?)
              ?.map((t) => Translation.fromMap(
                  t is Map ? Map<String, dynamic>.from(t) : <String, dynamic>{}, ''))
              .toList() ??
          [],
      contextBefore: data['contextBefore']?.toString(),
      contextAfter: data['contextAfter']?.toString(),
      sourceVideoId: data['sourceVideoId']?.toString(),
      modelBangla: data['modelBangla']?.toString(),
      lowQuality: data['lowQuality'] == true,
    );
  }

  Map<String, dynamic> toMap() {
    return {
      'text': text,
      'translationCount': translationCount,
      'audioUrl': audioUrl,
      'transcription': transcription,
      'translations': translations.map((t) => t.toMap()).toList(),
    };
  }
}
