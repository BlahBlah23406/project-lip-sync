import 'package:cloud_firestore/cloud_firestore.dart';

import 'translation.dart';

/// A contributor's own translation, stored as a document in the top level
/// `translations` collection.
///
/// The authoritative copy of a translation stays embedded in the sentence
/// document (that is what the admin export reads). This collection is a
/// per-contributor index over the same data so that "show me what I have
/// translated, let me redo one" is a single indexed query instead of a scan of
/// every sentence.
///
/// `sentenceText` is denormalised on purpose: the history list has to render
/// even when the sentence has since been edited or deleted by an admin.
class UserTranslation {
  final String id;
  final String sentenceId;
  final String sentenceText;
  final String userId;
  final String transcription;
  final String audioUrl;
  final TranslationInputMethod inputMethod;
  final DateTime createdAt;
  final DateTime updatedAt;

  /// 0 for the first submission, incremented on every redo.
  final int revision;

  /// Optional note explaining a word choice; see [Translation.note].
  final String note;
  final String reviewStatus;
  final String reviewNote;

  UserTranslation({
    required this.id,
    required this.sentenceId,
    required this.sentenceText,
    required this.userId,
    required this.transcription,
    required this.audioUrl,
    required this.inputMethod,
    required this.createdAt,
    required this.updatedAt,
    this.revision = 0,
    this.note = '',
    this.reviewStatus = 'pending',
    this.reviewNote = '',
  });

  bool get hasBeenRedone => revision > 0;

  factory UserTranslation.fromMap(
      Map<String, dynamic>? data, String documentId) {
    if (data == null) {
      return UserTranslation(
        id: documentId,
        sentenceId: '',
        sentenceText: '',
        userId: '',
        transcription: '',
        audioUrl: '',
        inputMethod: TranslationInputMethod.voice,
        createdAt: DateTime.fromMillisecondsSinceEpoch(0),
        updatedAt: DateTime.fromMillisecondsSinceEpoch(0),
      );
    }
    return UserTranslation(
      id: documentId,
      sentenceId: data['sentenceId']?.toString() ?? '',
      sentenceText: data['sentenceText']?.toString() ?? '',
      userId: data['userId']?.toString() ?? '',
      transcription: data['transcription']?.toString() ?? '',
      audioUrl: data['audioUrl']?.toString() ?? '',
      inputMethod:
          TranslationInputMethod.fromName(data['inputMethod']?.toString()),
      createdAt: timestampToDate(data['createdAt']),
      updatedAt: timestampToDate(data['updatedAt'] ?? data['createdAt']),
      revision: (data['revision'] as num?)?.toInt() ?? 0,
      note: data['note']?.toString() ?? '',
      reviewStatus: data['reviewStatus']?.toString() ?? 'pending',
      reviewNote: data['reviewNote']?.toString() ?? '',
    );
  }

  Map<String, dynamic> toMap() {
    return {
      'sentenceId': sentenceId,
      'sentenceText': sentenceText,
      'userId': userId,
      'transcription': transcription,
      'audioUrl': audioUrl,
      'inputMethod': inputMethod.name,
      'createdAt': Timestamp.fromDate(createdAt),
      'updatedAt': Timestamp.fromDate(updatedAt),
      'revision': revision,
      'reviewStatus': reviewStatus,
      'reviewNote': reviewNote,
      if (note.isNotEmpty) 'note': note,
    };
  }
}
