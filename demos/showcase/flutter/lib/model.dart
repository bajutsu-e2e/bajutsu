import 'package:flutter/foundation.dart';

/// A catalog horse. `id` is data-derived and drives the `*.row.<id>` identifiers.
class Horse {
  const Horse(this.id, this.name);
  final int id;
  final String name;
}

/// A stable notice. Twenty are seeded; `id` drives the `notice.row.<id>` identifiers.
class Notice {
  const Notice(this.id, this.title, this.body);
  final int id;
  final String title;
  final String body;
}

/// The five tabs, left to right (SPEC §5).
enum ShowcaseTab { stable, search, log, notices, permissions }

/// App state plus the launch-env hooks Bajutsu drives (SPEC §3), read once at launch.
///
/// The catalog is fixed at five horses — there is no launch-env seed knob (BE-0079): a scenario
/// observes the app's own data, it cannot inject a state. All mutable UI state lives on the model
/// (a single [ChangeNotifier]) rather than in each screen's local state, so it survives a tab
/// switch — the tab body is rebuilt on every switch, matching the iOS `TabView` and the Compose
/// twin (whose hoisted state serves the same purpose).
class AppModel extends ChangeNotifier {
  AppModel(Map<String, String> env)
      : gesturesMode = env['SHOWCASE_GESTURES'] != null,
        apiURL = env['SHOWCASE_API_URL'] ?? 'https://example.com',
        httpBase = env['SHOWCASE_HTTP_BASE'] ?? 'https://httpbin.org';

  /// Two-finger gesture mode (BE-0232): when the `SHOWCASE_GESTURES` launch env is set, the whole
  /// five-tab UI is swapped for the flat, scroll-free pinch/rotate screen, so the shared
  /// `gestures_multitouch` scenario can drive its targets without depending on scroll. Mirrors the
  /// iOS and Compose `gesturesMode`.
  final bool gesturesMode;

  // Networking config (SPEC §3, §6).
  final String apiURL;
  final String httpBase;

  // The fixed catalog (Stable + Search both filter this) and the seeded notices — the latter is
  // intentionally longer than one screen so the bottom rows start off-screen (SPEC §5.5).
  final List<Horse> horses = List.generate(5, (i) => Horse(i + 1, 'Horse ${i + 1}'));
  final List<Notice> notices =
      List.generate(20, (i) => Notice(i + 1, 'Notice ${i + 1}', 'Details for stable notice number ${i + 1}.'));

  // Tab selection. Details (Horse, Notice) are pushed on the root Navigator, so the cross-backend
  // `back` step pops them; no per-tab navigation state lives here.
  ShowcaseTab _selectedTab = ShowcaseTab.stable;
  ShowcaseTab get selectedTab => _selectedTab;
  set selectedTab(ShowcaseTab value) => _set(() => _selectedTab = value);

  // Stable tab
  String _stableStatus = 'idle';
  String get stableStatus => _stableStatus;
  set stableStatus(String value) => _set(() => _stableStatus = value);

  // Horse Detail (one open at a time; reset when a new row is pushed).
  bool _horseFavorite = false;
  bool get horseFavorite => _horseFavorite;
  set horseFavorite(bool value) => _set(() => _horseFavorite = value);
  String _horseStatus = 'idle';
  String get horseStatus => _horseStatus;
  set horseStatus(String value) => _set(() => _horseStatus = value);

  // Search tab
  String _searchQuery = '';
  String get searchQuery => _searchQuery;
  set searchQuery(String value) => _set(() => _searchQuery = value);

  // Log tab
  String _logNote = '';
  String get logNote => _logNote;
  set logNote(String value) => _set(() => _logNote = value);
  int _logCount = 1;
  int get logCount => _logCount;
  bool _logIntense = false;
  bool get logIntense => _logIntense;
  set logIntense(bool value) => _set(() => _logIntense = value);
  String _logSegment = 'one';
  String get logSegment => _logSegment;
  set logSegment(String value) => _set(() => _logSegment = value);
  String _logStatus = 'idle';
  String get logStatus => _logStatus;
  set logStatus(String value) => _set(() => _logStatus = value);
  final List<int> logRows = <int>[];
  bool _logLongPressed = false;
  bool get logLongPressed => _logLongPressed;
  set logLongPressed(bool value) => _set(() => _logLongPressed = value);
  int _logDoubleTaps = 0;
  int get logDoubleTaps => _logDoubleTaps;
  String _logDialogResult = 'none';
  String get logDialogResult => _logDialogResult;
  set logDialogResult(String value) => _set(() => _logDialogResult = value);
  bool _logShowToast = false;
  bool get logShowToast => _logShowToast;
  set logShowToast(bool value) => _set(() => _logShowToast = value);

  // Permissions tab
  String _notifStatus = 'notDetermined';
  String get notifStatus => _notifStatus;
  set notifStatus(String value) => _set(() => _notifStatus = value);
  String _locationStatus = 'notDetermined';
  String get locationStatus => _locationStatus;
  set locationStatus(String value) => _set(() => _locationStatus = value);
  String _pasted = '';
  String get pasted => _pasted;
  set pasted(String value) => _set(() => _pasted = value);

  List<Horse> horsesMatching(String query) => query.isEmpty
      ? horses
      : horses.where((h) => h.name.toLowerCase().contains(query.toLowerCase())).toList();

  Horse? horse(int id) => horses.cast<Horse?>().firstWhere((h) => h!.id == id, orElse: () => null);
  Notice? notice(int id) => notices.cast<Notice?>().firstWhere((n) => n!.id == id, orElse: () => null);

  /// Increment the log count (capped 0..99), the affordance `log.count` taps.
  void incrementLogCount() => _set(() => _logCount = _logCount < 99 ? _logCount + 1 : _logCount);
  void decrementLogCount() => _set(() => _logCount = _logCount > 0 ? _logCount - 1 : _logCount);

  void bumpDoubleTaps() => _set(() => _logDoubleTaps++);

  void appendLogRow() => _set(() => logRows.add((logRows.isEmpty ? 0 : logRows.last) + 1));

  /// Reset the Horse Detail's per-detail state so a newly pushed horse starts clean.
  void resetHorseDetail() => _set(() {
        _horseFavorite = false;
        _horseStatus = 'idle';
      });

  void _set(VoidCallback mutate) {
    mutate();
    notifyListeners();
  }
}
