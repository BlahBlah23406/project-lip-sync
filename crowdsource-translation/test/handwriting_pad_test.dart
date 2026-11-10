import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:crowdsource_translation/widgets/handwriting_pad.dart';

void main() {
  testWidgets('a tap produces an ink dot that can be undone', (tester) async {
    final key = GlobalKey<HandwritingPadState>();
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: HandwritingPad(key: key)),
    ));
    await tester.tapAt(
        tester.getTopLeft(find.byType(HandwritingPad)) + const Offset(70, 60));
    await tester.pump();
    expect(key.currentState!.strokes, hasLength(1));
    expect(key.currentState!.strokes.single.points, hasLength(1));
    expect(key.currentState!.strokes.single.isEmpty, isFalse);
    expect(key.currentState!.strokes.single.timestamps, hasLength(1));
    await tester.tap(find.text('Undo'));
    await tester.pump();
    expect(key.currentState!.isEmpty, isTrue);
  });

  testWidgets('vertical ink stays on the pad inside a scrolling page',
      (tester) async {
    final key = GlobalKey<HandwritingPadState>();
    final scroll = ScrollController();
    addTearDown(scroll.dispose);
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
          body: ListView(controller: scroll, children: [
        const SizedBox(height: 100),
        HandwritingPad(key: key),
        const SizedBox(height: 1000),
      ])),
    ));
    final start =
        tester.getTopLeft(find.byType(HandwritingPad)) + const Offset(70, 60);
    final gesture = await tester.startGesture(start);
    await gesture.moveBy(const Offset(0, 60));
    await gesture.up();
    await tester.pump();
    expect(key.currentState!.strokes, hasLength(1));
    expect(key.currentState!.strokes.single.points.length, greaterThan(1));
    expect(scroll.offset, 0);
  });
}
