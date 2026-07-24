import 'package:flutter/material.dart';

import '../accessibility.dart';

/// BE-0232: the two-finger gesture screen, the Flutter twin of the iOS `GestureView` and the Compose
/// `GestureScreen`. Pinch and rotate are the one class of actuation a single-touch backend cannot
/// perform; each target flips its mirrored a11y value once its gesture is recognized, so the shared
/// `gestures_multitouch` run can assert the actuation landed rather than merely not erroring. Reached
/// only when `SHOWCASE_GESTURES` is set, so the normal five-tab app never renders it. A flat,
/// scroll-free column keeps both targets always on-screen (a two-finger target needs full room).
class GestureScreen extends StatelessWidget {
  const GestureScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: SafeArea(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            _GestureTarget(label: 'Pinch me', id: 'log.pinch', pressedLabel: 'pinched', channel: _Channel.zoom),
            SizedBox(height: 24),
            _GestureTarget(label: 'Rotate me', id: 'log.rotate', pressedLabel: 'rotated', channel: _Channel.rotation),
          ],
        ),
      ),
    );
  }
}

enum _Channel { zoom, rotation }

/// A generous, opaque hit area. `onScaleUpdate` reports scale and rotation together; [channel]
/// picks the one this target cares about, so a pure pinch does not trip the rotate target and vice
/// versa. The id tags the hit area and its `.value` twin mirrors the result — the same aid + value
/// pairing the Log tab uses (SPEC §8), so the shared scenario reads it unchanged.
class _GestureTarget extends StatefulWidget {
  const _GestureTarget({required this.label, required this.id, required this.pressedLabel, required this.channel});

  final String label;
  final String id;
  final String pressedLabel;
  final _Channel channel;

  @override
  State<_GestureTarget> createState() => _GestureTargetState();
}

class _GestureTargetState extends State<_GestureTarget> {
  bool _fired = false;

  @override
  Widget build(BuildContext context) {
    final value = _fired ? widget.pressedLabel : 'idle';
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        aid(
          widget.id,
          GestureDetector(
            onScaleUpdate: (details) {
              final recognized = widget.channel == _Channel.zoom ? details.scale != 1.0 : details.rotation != 0.0;
              if (recognized && !_fired) setState(() => _fired = true);
            },
            child: Container(
              width: 280,
              height: 120,
              alignment: Alignment.center,
              color: Theme.of(context).colorScheme.surfaceContainerHighest,
              child: Text(widget.label),
            ),
          ),
        ),
        aidValue('${widget.id}.value', value, Text(value)),
      ],
    );
  }
}
