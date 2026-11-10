import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:crowdsource_translation/models/daily_quota.dart';
import 'package:crowdsource_translation/models/sentence.dart';
import 'package:crowdsource_translation/models/translation.dart';
import 'package:crowdsource_translation/services/firestore_service.dart';
import 'package:fake_cloud_firestore/fake_cloud_firestore.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  late FakeFirebaseFirestore db;
  late FirestoreService service;
  final sentence = Sentence(id: 'source-42', text: 'A whole sentence.');
  Future<String> submit({String? redoOf, String text = 'বাংলা'}) =>
      service.submitUserTranslation(
          userId: 'contributor',
          sentence: sentence,
          transcription: text,
          inputMethod: TranslationInputMethod.text,
          redoOf: redoOf);
  setUp(() async {
    db = FakeFirebaseFirestore();
    service = FirestoreService(database: db);
    await db.collection('sentences').doc(sentence.id).set({
      'text': sentence.text,
      'translationCount': 0,
      'translations': [],
    });
  });

  test('submission and redo keep quota, history and export consistent',
      () async {
    final id = await submit();
    await submit(redoOf: id, text: 'সংশোধন');
    final source =
        (await db.collection('sentences').doc(sentence.id).get()).data()!;
    final history = (await db.collection('translations').doc(id).get()).data()!;
    final quota = await db
        .collection('users')
        .doc('contributor')
        .collection('dailyQuota')
        .doc(DailyQuota.keyFor(DateTime.now()))
        .get();
    expect(source['translationCount'], 1);
    expect(source['text'], sentence.text);
    expect(source['translations'], hasLength(1));
    expect(source['translations'][0]['id'], id);
    expect(source['translations'][0]['transcription'], 'সংশোধন');
    expect(history['transcription'], 'সংশোধন');
    expect(history['revision'], 1);
    expect(quota.data()!['count'], 1);
  });

  test('retrying the same draft does not charge quota or duplicate export',
      () async {
    Future<String> retry() => service.submitUserTranslation(
          userId: 'contributor',
          sentence: sentence,
          transcription: 'বাংলা',
          inputMethod: TranslationInputMethod.voice,
          submissionId: 'same-draft',
        );
    expect(await retry(), 'same-draft');
    expect(await retry(), 'same-draft');
    expect((await db.collection('translations').get()).docs, hasLength(1));
    final source =
        (await db.collection('sentences').doc(sentence.id).get()).data()!;
    expect(source['translationCount'], 1);
    expect(source['translations'], hasLength(1));
    final quota = await db
        .collection('users')
        .doc('contributor')
        .collection('dailyQuota')
        .doc(DailyQuota.keyFor(DateTime.now()))
        .get();
    expect(quota.data()!['count'], 1);
  });

  test('quota failure leaves sentence and history untouched', () async {
    await db
        .collection('users')
        .doc('contributor')
        .collection('dailyQuota')
        .doc(DailyQuota.keyFor(DateTime.now()))
        .set({'count': 10});
    await expectLater(submit(), throwsA(isA<DailyLimitReachedException>()));
    expect((await db.collection('translations').get()).docs, isEmpty);
    expect(
        (await db.collection('sentences').doc(sentence.id).get())
            .data()!['translationCount'],
        0);
  });

  test('missing source does not consume quota or save half a submission',
      () async {
    await db.collection('sentences').doc(sentence.id).delete();
    await expectLater(submit(), throwsStateError);
    expect((await db.collection('translations').get()).docs, isEmpty);
    expect(
        (await db
                .collection('users')
                .doc('contributor')
                .collection('dailyQuota')
                .get())
            .docs,
        isEmpty);
  });

  test('legacy redo preserves other translations and source id', () async {
    await db.collection('translations').doc('old').set({
      'userId': 'contributor',
      'sentenceId': sentence.id,
      'transcription': 'old text',
      'audioUrl': '',
      'revision': 0,
    });
    await db.collection('sentences').doc(sentence.id).update({
      'translationCount': 2,
      'translations': [
        {
          'transcription': 'old text',
          'audioUrl': '',
          'timestamp': Timestamp.now()
        },
        {'transcription': 'someone else', 'audioUrl': ''},
      ],
    });
    await submit(redoOf: 'old');
    final source =
        (await db.collection('sentences').doc(sentence.id).get()).data()!;
    expect(source['translations'], hasLength(2));
    expect(source['translations'][0]['transcription'], 'বাংলা');
    expect(source['translations'][1]['transcription'], 'someone else');
    expect(source['translationCount'], 2);
  });
}
