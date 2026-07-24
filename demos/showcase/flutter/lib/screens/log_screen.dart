import 'dart:async';

import 'package:flutter/material.dart';

import '../accessibility.dart';
import '../model.dart';
import '../net.dart';

/// Tab: Log (SPEC §5.3) — a training-log composer exercising every input control, dedicated gesture
/// targets, and the four modal styles (bottom sheet, full-screen cover, custom action-sheet overlay,
/// auto-dismissing toast). Each control mirrors its result to an a11y value so a scenario can assert
/// it. All persistent state lives on [AppModel] so it survives a tab switch.
class LogScreen extends StatefulWidget {
  const LogScreen({super.key, required this.model});

  final AppModel model;

  @override
  State<LogScreen> createState() => _LogScreenState();
}

class _LogScreenState extends State<LogScreen> {
  static const _segments = ['one', 'two', 'three'];
  late final TextEditingController _note = TextEditingController(text: widget.model.logNote);
  Timer? _toastTimer;
  // When the previous tap on the double-tap target landed, for the manual detector below.
  DateTime? _lastDoubleTapCandidate;

  @override
  void dispose() {
    _note.dispose();
    _toastTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final model = widget.model;
    return Stack(
      children: [
        Scaffold(
          appBar: AppBar(title: const Text('Log')),
          body: ListView(
            padding: const EdgeInsets.all(16),
            children: [
              aid(
                'log.note',
                TextField(
                  controller: _note,
                  onChanged: (v) => model.logNote = v,
                  minLines: 3,
                  maxLines: 5,
                  decoration: const InputDecoration(labelText: 'Note', border: OutlineInputBorder()),
                ),
              ),
              const SizedBox(height: 8),
              // Stepper: the increment button carries log.count; the value mirrors to log.count.value.
              Row(
                children: [
                  TextButton(onPressed: model.decrementLogCount, child: const Text('−')),
                  aid('log.count', TextButton(onPressed: model.incrementLogCount, child: const Text('Count +'))),
                ],
              ),
              aidValue('log.count.value', '${model.logCount}', Text('Count: ${model.logCount}')),
              // A button-backed toggle; `selected` reflects state, value mirrors on/off.
              aidSelected(
                'log.intense',
                model.logIntense,
                TextButton(
                  onPressed: () => model.logIntense = !model.logIntense,
                  child: Text(model.logIntense ? '☑ Intense' : '☐ Intense'),
                ),
              ),
              aidValue('log.intense.value', model.logIntense ? 'on' : 'off', Text(model.logIntense ? 'Intense' : 'Easy')),
              aid(
                'log.submit',
                TextButton(
                  onPressed: () async {
                    model.logStatus = 'loading';
                    final result = await Net.postLog(model.httpBase, model.logNote, model.logCount, model.logIntense);
                    model.logStatus = result;
                    if (result == 'done') {
                      model.appendLogRow();
                      _showToast(model);
                    }
                  },
                  child: const Text('Submit'),
                ),
              ),
              aidValue('log.status', model.logStatus, Text('Status: ${model.logStatus}')),
              // Modals — the four presentation styles.
              aid('log.openFilter', TextButton(onPressed: () => _openFilter(model), child: const Text('Open Filter'))),
              aid('log.openGallery', TextButton(onPressed: () => _openGallery(), child: const Text('Open Gallery'))),
              aid('log.openDelete', TextButton(onPressed: () => _openDelete(model), child: const Text('Open Delete'))),
              aidValue('log.dialog.value', model.logDialogResult, Text('Dialog: ${model.logDialogResult}')),
              // Gesture targets: a long-press and a double-tap, each mirroring its result.
              aid(
                'log.longpress',
                GestureDetector(
                  onLongPress: () => model.logLongPressed = true,
                  child: const Padding(padding: EdgeInsets.symmetric(vertical: 8), child: Text('Long-press me')),
                ),
              ),
              aidValue('log.longpress.value', model.logLongPressed ? 'pressed' : 'idle', Text(model.logLongPressed ? 'pressed' : 'idle')),
              aid(
                'log.doubletap',
                GestureDetector(
                  // A manual double-tap detector with a generous window, not `onDoubleTap`. The
                  // backends actuate a double-tap as two discrete taps (adb spawns two `input tap`
                  // processes; XCUITest two events), and adb's inter-tap gap can exceed Flutter's
                  // strict ~300 ms `onDoubleTap` timeout, so the native gesture would silently miss.
                  // Counting two taps inside 800 ms fires deterministically for both backends.
                  onTap: _onDoubleTapTargetTap,
                  child: const Padding(padding: EdgeInsets.symmetric(vertical: 8), child: Text('Double-tap me')),
                ),
              ),
              aidValue('log.doubletap.value', '${model.logDoubleTaps}', Text('Double-taps: ${model.logDoubleTaps}')),
              // A button-backed segmented control; the selected button carries `selected`.
              Row(
                children: [
                  for (final choice in _segments)
                    aidSelected(
                      'log.segment.$choice',
                      model.logSegment == choice,
                      TextButton(
                        onPressed: () => model.logSegment = choice,
                        child: Text(model.logSegment == choice ? '● ${_cap(choice)}' : _cap(choice)),
                      ),
                    ),
                ],
              ),
              aidValue('log.segment.value', model.logSegment, Text('Segment: ${model.logSegment}')),
              for (final n in model.logRows) aid('log.row.$n', Text('Entry $n')),
            ],
          ),
        ),
        // The transient toast (~1.2 s auto-dismiss → exercises `wait until gone`).
        if (model.logShowToast)
          Positioned(
            top: 60,
            left: 0,
            right: 0,
            child: Center(
              child: aid(
                'log.toast',
                Material(
                  color: Theme.of(context).colorScheme.inverseSurface,
                  shape: const StadiumBorder(),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                    child: Text('Saved', style: TextStyle(color: Theme.of(context).colorScheme.onInverseSurface)),
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }

  // Two taps within the window count as one double-tap (see the target above). `DateTime.now()` is
  // the on-device wall clock — fine at app runtime — used only to measure the gap between taps.
  void _onDoubleTapTargetTap() {
    final now = DateTime.now();
    final previous = _lastDoubleTapCandidate;
    if (previous != null && now.difference(previous) < const Duration(milliseconds: 800)) {
      widget.model.bumpDoubleTaps();
      _lastDoubleTapCandidate = null;
    } else {
      _lastDoubleTapCandidate = now;
    }
  }

  void _showToast(AppModel model) {
    model.logShowToast = true;
    _toastTimer?.cancel();
    _toastTimer = Timer(const Duration(milliseconds: 1200), () => model.logShowToast = false);
  }

  /// Bottom sheet with detents (SPEC §5.3).
  void _openFilter(AppModel model) {
    showModalBottomSheet<void>(
      context: context,
      builder: (ctx) => Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            aid('log.sheet.title', const Text('Filter')),
            aid('log.sheet.apply', TextButton(onPressed: () => Navigator.of(ctx).pop(), child: const Text('Apply'))),
            aid('log.sheet.close', TextButton(onPressed: () => Navigator.of(ctx).pop(), child: const Text('Close'))),
          ],
        ),
      ),
    );
  }

  /// Full-screen cover (SPEC §5.3).
  void _openGallery() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        fullscreenDialog: true,
        builder: (ctx) => Scaffold(
          body: SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  aid('log.cover.title', const Text('Gallery')),
                  aid('log.cover.close', TextButton(onPressed: () => Navigator.of(ctx).pop(), child: const Text('Close'))),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  /// Action sheet: a custom overlay of plain buttons (SPEC §5.3), result mirrored to log.dialog.value.
  void _openDelete(AppModel model) {
    showGeneralDialog<void>(
      context: context,
      barrierDismissible: true,
      barrierLabel: 'delete',
      barrierColor: const Color(0x33000000),
      pageBuilder: (ctx, _, _) => Center(
        child: Material(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                aid('log.dialog.title', const Text('Delete entry')),
                aid('log.dialog.archive', TextButton(onPressed: () { model.logDialogResult = 'archive'; Navigator.of(ctx).pop(); }, child: const Text('Archive'))),
                aid('log.dialog.delete', TextButton(onPressed: () { model.logDialogResult = 'delete'; Navigator.of(ctx).pop(); }, child: const Text('Delete'))),
                aid('log.dialog.cancel', TextButton(onPressed: () => Navigator.of(ctx).pop(), child: const Text('Cancel'))),
              ],
            ),
          ),
        ),
      ),
    );
  }

  static String _cap(String s) => s[0].toUpperCase() + s.substring(1);
}
