import 'package:cloud_firestore/cloud_firestore.dart';

/// How a contributor entered a translation.
///
/// The [name] of each value is what gets written to Firestore, so these
/// identifiers are storage format: renaming one orphans existing documents.
/// [label] is the display string and is free to change.
enum TranslationInputMethod {
  voice('Spoken'),
  text('Typed'),
  handwriting('Handwritten');

  const TranslationInputMethod(this.label);

  final String label;

  /// Parses the value stored in Firestore.
  ///
  /// Unknown or missing values fall back to [voice]: every submission made
  /// before this field existed was a voice recording.
  static TranslationInputMethod fromName(String? name) {
    for (final method in values) {
      if (method.name == name) return method;
    }
    return voice;
  }
}

/// Normalises whatever Firestore hands back for a timestamp field into a
/// [DateTime].
///
/// It is usually a [Timestamp], but a document read from the local cache
/// before its `serverTimestamp()` write has landed carries `null`, and
/// documents written by the older client stored raw epoch milliseconds. All
/// three have to render in the history list rather than throw.
DateTime timestampToDate(Object? value) {
  if (value is Timestamp) return value.toDate();
  if (value is DateTime) return value;
  if (value is int) return DateTime.fromMillisecondsSinceEpoch(value);
  return DateTime.fromMillisecondsSinceEpoch(0);
}

class Translation {
  final String id;
  // final String sentenceId; // Removed as it's now embedded
  final String audioUrl;
  final String transcription;
  final DateTime timestamp;

  /// Optional contributor note on a word choice ("used ইবাদত, not পূজা:
  /// this is about worship of Allah"). Empty for older submissions.
  final String note;

  /// Storage name of the [TranslationInputMethod]; empty for older rows.
  final String inputMethod;

  Translation({
    required this.id,
    // required this.sentenceId,
    required this.audioUrl,
    required this.transcription,
    required this.timestamp,
    this.note = '',
    this.inputMethod = '',
  });

  factory Translation.fromMap(Map<String, dynamic>? data, String documentId) {
    if (data == null) {
      return Translation(
        id: documentId,
        audioUrl: '',
        transcription: '',
        timestamp: DateTime.fromMillisecondsSinceEpoch(0),
      );
    }
    return Translation(
      id: data['id']?.toString() ?? documentId,
      audioUrl: data['audioUrl']?.toString() ?? '',
      transcription: data['transcription']?.toString() ?? '',
      timestamp: timestampToDate(data['timestamp']),
      note: data['note']?.toString() ?? '',
      inputMethod: data['inputMethod']?.toString() ?? '',
    );
  }

  Map<String, dynamic> toMap() {
    return {
      if (id.isNotEmpty) 'id': id,
      'audioUrl': audioUrl,
      'transcription': transcription,
      'timestamp': Timestamp.fromDate(timestamp),
      if (note.isNotEmpty) 'note': note,
      if (inputMethod.isNotEmpty) 'inputMethod': inputMethod,
    };
  }
}
