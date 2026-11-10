// This is a basic Flutter widget test.
//
// To perform an interaction with a widget in your test, use the WidgetTester
// utility in the flutter_test package. For example, you can send tap and scroll
// gestures. You can also use WidgetTester to find child widgets in the widget
// tree, read text, and verify that the values of widget properties are correct.

import 'package:flutter_test/flutter_test.dart';

import 'package:crowdsource_translation/models/daily_quota.dart';
import 'package:crowdsource_translation/models/sentence.dart';
import 'package:crowdsource_translation/models/translation.dart';
import 'package:crowdsource_translation/models/user_translation.dart';

void main() {
  test('Sentence.fromMap handles null values safely', () {
    final sentence = Sentence.fromMap(null, 'doc1');
    expect(sentence.id, 'doc1');
    expect(sentence.text, '');
    expect(sentence.translationCount, 0);
  });

  test('UserTranslation.fromMap handles null values safely', () {
    final ut = UserTranslation.fromMap(null, 'doc2');
    expect(ut.id, 'doc2');
    expect(ut.sentenceId, '');
    expect(ut.sentenceText, '');
    expect(ut.userId, '');
  });

  test('Translation.fromMap handles null values safely', () {
    final t = Translation.fromMap(null, 'doc3');
    expect(t.id, 'doc3');
    expect(t.audioUrl, '');
    expect(t.transcription, '');
  });

  test('DailyQuota.fromMap handles null values safely', () {
    final q = DailyQuota.fromMap(null, '2026-08-26');
    expect(q.dayKey, '2026-08-26');
    expect(q.used, 0);
  });
}
