import 'package:crowdsource_translation/models/sentence.dart';
import 'package:crowdsource_translation/models/translation.dart';
import 'package:crowdsource_translation/services/firestore_service.dart';
import 'package:fake_cloud_firestore/fake_cloud_firestore.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  late FakeFirebaseFirestore db;
  late FirestoreService service;
  const sourceId = 'review-source';
  setUp(() async {
    db = FakeFirebaseFirestore();
    service = FirestoreService(database: db);
    await db.collection('sentences').doc(sourceId).set({
      'text': 'Peace be with you.',
      'translationCount': 0,
      'translations': [],
    });
    await service.submitUserTranslation(
        userId: 'translator',
        sentence: Sentence(id: sourceId, text: 'Peace be with you.'),
        transcription: 'আপনার শান্তি হোক।',
        inputMethod: TranslationInputMethod.text);
  });

  test('review persists, reaches contributor, and redo requires review again',
      () async {
    final item = (await service.watchReviewQueue().first).single;
    expect(item.reviewStatus, 'pending');
    await service.reviewTranslation(
        item, 'reviewer', 'changesRequested', 'Use a natural greeting.');
    final history =
        (await service.watchMyTranslations('translator').first).single;
    expect(history.reviewStatus, 'changesRequested');
    expect(history.reviewNote, 'Use a natural greeting.');
    await service.submitUserTranslation(
        userId: 'translator',
        sentence: Sentence(id: sourceId, text: 'Peace be with you.'),
        transcription: 'আসসালামু আলাইকুম।',
        inputMethod: TranslationInputMethod.text,
        redoOf: item.id);
    final revised = (await service.watchReviewQueue().first).single;
    expect(revised.reviewStatus, 'pending');
    expect(revised.reviewNote, isEmpty);
    expect(revised.revision, 1);
    await expectLater(
        service.reviewTranslation(item, 'reviewer', 'approved', ''),
        throwsStateError);
    await service.reviewTranslation(revised, 'reviewer', 'approved', '');
    expect(
        (await service.watchMyTranslations('translator').first)
            .single
            .reviewStatus,
        'approved');
  });

  test('rejects missing feedback and duplicate review', () async {
    final item = (await service.watchReviewQueue().first).single;
    await expectLater(
        service.reviewTranslation(item, 'reviewer', 'changesRequested', ''),
        throwsArgumentError);
    await service.reviewTranslation(item, 'reviewer', 'approved', '');
    await expectLater(service.reviewTranslation(item, 'other', 'approved', ''),
        throwsStateError);
    final data =
        (await db.collection('translations').doc(item.id).get()).data()!;
    expect(data['reviewedBy'], 'reviewer');
  });
}
