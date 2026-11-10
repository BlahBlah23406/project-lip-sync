import 'package:flutter/material.dart';
import 'package:flutter/gestures.dart';

import '../services/handwriting_recognizer.dart';

/// A finger/stylus drawing surface for writing a translation by hand.
///
/// Pure Flutter, no extra dependency: strokes are captured as point lists and
/// painted with a [CustomPainter]. The captured ink is handed to
/// [onStrokesChanged] so the parent can pass it to a [HandwritingRecognizer]
/// when one is available.
class HandwritingPad extends StatefulWidget {
  const HandwritingPad({
    super.key,
    this.onStrokesChanged,
    this.height = 220,
  });

  final ValueChanged<List<InkStroke>>? onStrokesChanged;
  final double height;

  @override
  State<HandwritingPad> createState() => HandwritingPadState();
}

class HandwritingPadState extends State<HandwritingPad> {
  final List<InkStroke> _strokes = [];
  List<Offset>? _current;
  List<int>? _currentTimes;
  int? _activePointer;

  List<InkStroke> get strokes => List.unmodifiable(_strokes);

  bool get isEmpty => _strokes.isEmpty && _current == null;

  void clear() {
    setState(() {
      _strokes.clear();
      _current = null;
      _currentTimes = null;
      _activePointer = null;
    });
    widget.onStrokesChanged?.call(strokes);
  }

  void undo() {
    if (_strokes.isEmpty) return;
    setState(() => _strokes.removeLast());
    widget.onStrokesChanged?.call(strokes);
  }

  void _start(Offset position, int time) {
    setState(() {
      _current = [position];
      _currentTimes = [time];
    });
  }

  void _extend(Offset position, int time) {
    if (_current == null) return;
    setState(() {
      _current!.add(position);
      _currentTimes!.add(time);
    });
  }

  void _end() {
    if (_current == null) return;
    setState(() {
      _strokes.add(InkStroke(List<Offset>.from(_current!),
          timestamps: List<int>.from(_currentTimes!)));
      _current = null;
      _activePointer = null;
      _currentTimes = null;
    });
    widget.onStrokesChanged?.call(strokes);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          height: widget.height,
          decoration: BoxDecoration(
            color: theme.colorScheme.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: theme.colorScheme.outlineVariant),
          ),
          clipBehavior: Clip.antiAlias,
          child: Stack(
            children: [
              if (isEmpty)
                Center(
                  child: Text(
                    'Write the Bangla translation here',
                    style: theme.textTheme.bodyMedium
                        ?.copyWith(color: theme.colorScheme.outline),
                  ),
                ),
              RawGestureDetector(
                gestures: {
                  // Ink owns the pointer immediately, including dots and
                  // vertical strokes inside the surrounding scrolling page.
                  EagerGestureRecognizer: GestureRecognizerFactoryWithHandlers<
                      EagerGestureRecognizer>(
                    EagerGestureRecognizer.new,
                    (instance) {},
                  ),
                },
                child: Listener(
                  behavior: HitTestBehavior.opaque,
                  onPointerDown: (event) {
                    if (_activePointer != null) return;
                    _activePointer = event.pointer;
                    _start(event.localPosition, event.timeStamp.inMilliseconds);
                  },
                  onPointerMove: (event) {
                    if (event.pointer == _activePointer) {
                      _extend(
                          event.localPosition, event.timeStamp.inMilliseconds);
                    }
                  },
                  onPointerUp: (event) {
                    if (event.pointer == _activePointer) _end();
                  },
                  onPointerCancel: (event) {
                    if (event.pointer == _activePointer) _end();
                  },
                  child: CustomPaint(
                    painter: _InkPainter(
                      strokes: _strokes,
                      active: _current,
                      color: theme.colorScheme.onSurface,
                    ),
                    size: Size.infinite,
                  ),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 8),
        Row(
          mainAxisAlignment: MainAxisAlignment.end,
          children: [
            TextButton.icon(
              onPressed: _strokes.isEmpty ? null : undo,
              icon: const Icon(Icons.undo),
              label: const Text('Undo'),
            ),
            const SizedBox(width: 8),
            TextButton.icon(
              onPressed: isEmpty ? null : clear,
              icon: const Icon(Icons.clear),
              label: const Text('Clear'),
            ),
          ],
        ),
      ],
    );
  }
}

class _InkPainter extends CustomPainter {
  _InkPainter(
      {required this.strokes, required this.active, required this.color});

  final List<InkStroke> strokes;
  final List<Offset>? active;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 3
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round
      ..style = PaintingStyle.stroke;

    for (final stroke in strokes) {
      _paintPoints(canvas, stroke.points, paint);
    }
    if (active != null) {
      _paintPoints(canvas, active!, paint);
    }
  }

  void _paintPoints(Canvas canvas, List<Offset> points, Paint paint) {
    if (points.isEmpty) return;
    if (points.length == 1) {
      // A tap with no drag: render the dot the contributor expects to see.
      canvas.drawCircle(points.first, paint.strokeWidth / 2,
          paint..style = PaintingStyle.fill);
      paint.style = PaintingStyle.stroke;
      return;
    }
    final path = Path()..moveTo(points.first.dx, points.first.dy);
    for (final point in points.skip(1)) {
      path.lineTo(point.dx, point.dy);
    }
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant _InkPainter oldDelegate) => true;
}
