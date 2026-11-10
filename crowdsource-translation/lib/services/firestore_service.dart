import 'package:cloud_firestore/cloud_firestore.dart';
import '../models/daily_quota.dart';
import '../models/sentence.dart';
import '../models/translation.dart';
import '../models/user_translation.dart';

class FirestoreService {
  FirestoreService({FirebaseFirestore? database})
      : _db = database ?? FirebaseFirestore.instance;

  final FirebaseFirestore _db;

  // Collection References
  // Collection References
  CollectionReference get _sentencesRef => _db.collection('sentences');
  CollectionReference get _translationsRef => _db.collection('translations');

  DocumentReference _quotaRef(String userId, String dayKey) =>
      _db.collection('users').doc(userId).collection('dailyQuota').doc(dayKey);

  // Add a new sentence (Admin)
  Future<void> addSentence(String text) async {
    await _sentencesRef.add({
      'text': text,
      'translationCount': 0,
      'createdAt': FieldValue.serverTimestamp(),
      'transcription': '',
      'audioUrl': '',
    });
  }

  // Batch add sentences (Admin)
  Future<void> batchAddSentences(List<String> sentences) async {
    const int batchSize = 500;
    for (var i = 0; i < sentences.length; i += batchSize) {
      final batch = _db.batch();
      final chunk = sentences.skip(i).take(batchSize);

      for (final text in chunk) {
        final docRef = _sentencesRef.doc();
        batch.set(docRef, {
          'text': text,
          'translationCount': 0,
          'createdAt': FieldValue.serverTimestamp(),
          'transcription': '',
          'audioUrl': '',
        });
      }

      await batch.commit();
    }
  }

  // Get stream of sentences (Admin/Web)
  Stream<List<Sentence>> getSentences() {
    return _sentencesRef.snapshots().map((snapshot) {
      return snapshot.docs.map((doc) {
        final data = doc.data() is Map
            ? Map<String, dynamic>.from(doc.data() as Map)
            : <String, dynamic>{};
        return Sentence.fromMap(data, doc.id);
      }).toList();
    });
  }

  // Get a single sentence to translate (Mobile)
  // For MVP, we can just get random or first few.
  // Ideally, we'd query for sentences with low translationCount.
  Future<List<Sentence>> getSentencesToTranslate() async {
    // Get sentences with less than 5 translations. Over-fetch and drop the
    // ones the cleanup flagged lowQuality ("Allah.") in the client, so this
    // needs no new composite index.
    QuerySnapshot snapshot = await _sentencesRef
        .where('translationCount', isLessThan: 5)
        .limit(30)
        .get();

    return snapshot.docs
        .map((doc) {
          final data = doc.data() is Map
              ? Map<String, dynamic>.from(doc.data() as Map)
              : <String, dynamic>{};
          return MapEntry(data, doc.id);
        })
        .where((entry) => entry.key['lowQuality'] != true)
        .take(10)
        .map((entry) => Sentence.fromMap(entry.key, entry.value))
        .toList();
  }

  /// One sentence by id, or null when an admin has since deleted it.
  Future<Sentence?> getSentence(String sentenceId) async {
    final snap = await _sentencesRef.doc(sentenceId).get();
    if (!snap.exists) return null;
    final data = snap.data() is Map
        ? Map<String, dynamic>.from(snap.data() as Map)
        : <String, dynamic>{};
    return Sentence.fromMap(data, snap.id);
  }

  /// Every sentence that has at least one translation (Admin dataset export).
  /// The single-field index on translationCount covers this, so it reads only
  /// translated docs instead of the whole pool.
  Future<List<Sentence>> getTranslatedSentences() async {
    final snapshot =
        await _sentencesRef.where('translationCount', isGreaterThan: 0).get();
    return snapshot.docs.map((doc) {
      final data = doc.data() is Map
          ? Map<String, dynamic>.from(doc.data() as Map)
          : <String, dynamic>{};
      return Sentence.fromMap(data, doc.id);
    }).toList();
  }

  /// Admin-only stream; legacy rows without reviewStatus are pending.
  Stream<List<UserTranslation>> watchReviewQueue() =>
      _translationsRef.snapshots().map((snapshot) => snapshot.docs
          .map((doc) => UserTranslation.fromMap(
              Map<String, dynamic>.from(doc.data() as Map), doc.id))
          .toList()
        ..sort((a, b) => a.createdAt.compareTo(b.createdAt)));

  /// Review exactly the revision displayed; concurrent edits require a refresh.
  Future<void> reviewTranslation(UserTranslation item, String reviewerId,
      String status, String note) async {
    if (!['approved', 'changesRequested'].contains(status)) {
      throw ArgumentError('Invalid review decision.');
    }
    if (status == 'changesRequested' && note.trim().isEmpty) {
      throw ArgumentError('Explain the changes needed.');
    }
    final ref = _translationsRef.doc(item.id);
    await _db.runTransaction((txn) async {
      final snap = await txn.get(ref);
      final data = snap.data() as Map<String, dynamic>?;
      if (data == null ||
          (data['revision'] ?? 0) != item.revision ||
          data['transcription'] != item.transcription ||
          (data['reviewStatus'] ?? 'pending') != 'pending') {
        throw StateError('This submission changed. Refresh before reviewing.');
      }
      txn.update(ref, {
        'reviewStatus': status,
        'reviewNote': note.trim(),
        'reviewedBy': reviewerId,
        'reviewedAt': FieldValue.serverTimestamp(),
      });
    });
  }

  // Save a translation
  Future<void> saveTranslation(
      String sentenceId, String audioUrl, String transcription) async {
    final translation = Translation(
      id: DateTime.now()
          .millisecondsSinceEpoch
          .toString(), // Generate a simple ID
      audioUrl: audioUrl,
      transcription: transcription,
      timestamp: DateTime.now(),
    );

    // Update the sentence document:
    // 1. Add the translation to the 'translations' array
    // 2. Increment the translation count
    await _sentencesRef.doc(sentenceId).update({
      'translations': FieldValue.arrayUnion([translation.toMap()]),
      'translationCount': FieldValue.increment(1),
      'audioUrl': audioUrl,
      'transcription': transcription,
    });
  }

  // Update a sentence (Admin)
  Future<void> updateSentence(String sentenceId, String newText) async {
    await _sentencesRef.doc(sentenceId).update({
      'text': newText,
    });
  }

  // Delete a sentence (Admin)
  Future<void> deleteSentence(String sentenceId) async {
    await _sentencesRef.doc(sentenceId).delete();
  }

  // Get translations for a specific sentence (Admin)
  // DEPRECATED: Translations are now embedded in the Sentence object.
  // This method is removed.

  /// Today's assignment usage for [userId].
  ///
  /// The document is created lazily by [submitUserTranslation], so a missing
  /// document is the normal "nothing used yet" state rather than an error.
  Stream<DailyQuota> watchDailyQuota(String userId) {
    final dayKey = DailyQuota.keyFor(DateTime.now());
    return _quotaRef(userId, dayKey).snapshots().map(
      (snap) {
        final data = snap.data() is Map
            ? Map<String, dynamic>.from(snap.data() as Map)
            : null;
        return DailyQuota.fromMap(data, dayKey);
      },
    );
  }

  /// Everything [userId] has submitted, newest first.
  ///
  /// Deliberately filtered on `userId` only and sorted in Dart: adding
  /// `orderBy('updatedAt')` to the query would need a composite index that is
  /// not in `firestore.indexes.json`, and the miss surfaces as a runtime
  /// failure on the contributor's phone rather than at build time. The result
  /// set is one contributor's own work, so sorting it client side is cheap.
  Stream<List<UserTranslation>> watchMyTranslations(String userId) {
    return _translationsRef
        .where('userId', isEqualTo: userId)
        .snapshots()
        .map((snapshot) {
      final items = snapshot.docs.map((doc) {
        final data = doc.data() is Map
            ? Map<String, dynamic>.from(doc.data() as Map)
            : <String, dynamic>{};
        return UserTranslation.fromMap(data, doc.id);
      }).toList();
      items.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
      return items;
    });
  }

  /// Records a translation against both the sentence and the contributor.
  ///
  /// Returns the id of the `translations` document. When [redoOf] is supplied
  /// the existing document is revised in place and the daily allowance is NOT
  /// charged - redoing your own work is free, which is the whole point of the
  /// history screen.
  ///
  /// Throws [DailyLimitReachedException] when a *new* submission would exceed
  /// [kDailyAssignmentLimit].
  Future<String> submitUserTranslation({
    required String userId,
    required Sentence sentence,
    required String transcription,
    required TranslationInputMethod inputMethod,
    String audioUrl = '',
    String? redoOf,
    String? submissionId,
    String note = '',
  }) async {
    final now = DateTime.now();
    final dayKey = DailyQuota.keyFor(now);

    if (transcription.trim().isEmpty) {
      throw ArgumentError('A translation is required.');
    }
    final docRef = redoOf == null
        ? _translationsRef.doc(submissionId)
        : _translationsRef.doc(redoOf);
    final sentenceRef = _sentencesRef.doc(sentence.id);
    final quotaRef = _quotaRef(userId, dayKey);

    // Read first, then commit the quota, history and export copy together.
    // Any failure rolls everything back, so retry cannot charge a lost write.
    await _db.runTransaction((txn) async {
      final sentenceSnap = await txn.get(sentenceRef);
      final sentenceData = sentenceSnap.data() as Map<String, dynamic>?;
      final embedded = (sentenceData?['translations'] as List? ?? [])
          .map((entry) => Map<String, dynamic>.from(entry as Map))
          .toList();
      final timestamp = Timestamp.fromDate(now);
      final translation = {
        'id': docRef.id,
        'audioUrl': audioUrl,
        'transcription': transcription.trim(),
        'timestamp': timestamp,
        'inputMethod': inputMethod.name,
        if (note.trim().isNotEmpty) 'note': note.trim(),
      };

      if (redoOf != null) {
        final previousSnap = await txn.get(docRef);
        final previous = previousSnap.data() as Map<String, dynamic>?;
        if (previous == null ||
            previous['userId'] != userId ||
            previous['sentenceId'] != sentence.id) {
          throw StateError(
              'This translation cannot be revised by this contributor.');
        }
        var index = embedded.indexWhere((entry) => entry['id'] == redoOf);
        if (index < 0) {
          // Older clients wrote no embedded id. Only repair an unambiguous
          // match; never replace another contributor's identical translation.
          final matches = embedded
              .asMap()
              .entries
              .where((entry) =>
                  entry.value['transcription'] == previous['transcription'] &&
                  entry.value['audioUrl'] == previous['audioUrl'])
              .toList();
          if (matches.length == 1) index = matches.single.key;
          if (matches.length > 1) {
            throw StateError(
                'This older translation needs an admin to reconcile its export copy.');
          }
        }
        if (sentenceSnap.exists) {
          if (index >= 0) {
            embedded[index] = {...embedded[index], ...translation};
          } else {
            embedded.add(translation);
          }
          txn.update(sentenceRef, {
            'translations': embedded,
            'audioUrl': audioUrl,
            'transcription': transcription.trim(),
          });
        }
        txn.update(docRef, {
          'transcription': transcription.trim(),
          'audioUrl': audioUrl,
          'inputMethod': inputMethod.name,
          'note': note.trim(),
          'updatedAt': timestamp,
          'revision': FieldValue.increment(1),
          'reviewStatus': 'pending',
          'reviewNote': '',
          'reviewedBy': FieldValue.delete(),
          'reviewedAt': FieldValue.delete(),
        });
      } else {
        if (!sentenceSnap.exists) {
          throw StateError('This sentence is no longer available.');
        }
        final savedSnap = await txn.get(docRef);
        if (savedSnap.exists) {
          final saved = savedSnap.data() as Map<String, dynamic>;
          if (saved['userId'] != userId ||
              saved['sentenceId'] != sentence.id ||
              saved['transcription'] != transcription.trim() ||
              saved['audioUrl'] != audioUrl) {
            throw StateError(
                'This submission was already saved. Open history to revise it.');
          }
          return;
        }
        final quotaSnap = await txn.get(quotaRef);
        final quota = quotaSnap.data() as Map<String, dynamic>?;
        final used = (quota?['count'] as num?)?.toInt() ?? 0;
        if (used >= kDailyAssignmentLimit) {
          throw DailyLimitReachedException(used: used);
        }
        txn.set(quotaRef, {
          'count': used + 1,
          'dayKey': dayKey,
          'updatedAt': timestamp,
        });
        txn.set(
            docRef,
            UserTranslation(
              id: docRef.id,
              sentenceId: sentence.id,
              sentenceText: sentence.text,
              userId: userId,
              transcription: transcription.trim(),
              audioUrl: audioUrl,
              inputMethod: inputMethod,
              createdAt: now,
              updatedAt: now,
              note: note.trim(),
            ).toMap());
        txn.update(sentenceRef, {
          'translations': [...embedded, translation],
          'translationCount': FieldValue.increment(1),
          'audioUrl': audioUrl,
          'transcription': transcription.trim(),
        });
      }
    });
    return docRef.id;
  }
}
