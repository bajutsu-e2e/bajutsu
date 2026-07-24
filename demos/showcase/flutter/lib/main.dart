import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';

import 'accessibility.dart';
import 'launch.dart';
import 'model.dart';
import 'screens/gesture_screen.dart';
import 'screens/log_screen.dart';
import 'screens/notices_screen.dart';
import 'screens/permissions_screen.dart';
import 'screens/search_screen.dart';
import 'screens/stable_screen.dart';

/// The proposal's lazy-semantics fallback (BE-0008), gated so on-device verification can settle
/// whether a backend's accessibility connection triggers semantics on its own before it is baked in.
///
/// Flutter builds its semantics tree lazily — only once an accessibility client connects or the app
/// calls `ensureSemantics()`. Android's UI Automator connects as an accessibility service and
/// triggers it; whether the iOS backend's accessibility access does is the open question. Build with
/// `--dart-define=ENSURE_SEMANTICS=true` to force the tree on at launch regardless.
const bool kEnsureSemantics = bool.fromEnvironment('ENSURE_SEMANTICS');

// Held for the process lifetime: releasing the handle would let Flutter tear the semantics tree down.
// ignore: unused_element
SemanticsHandle? _semanticsHandle;

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (kEnsureSemantics) {
    _semanticsHandle = SemanticsBinding.instance.ensureSemantics();
  }
  final env = await readLaunchEnv();
  runApp(ShowcaseApp(model: AppModel(env), uiTest: env['SHOWCASE_UITEST'] != null));
}

/// The showcase Flutter app (SPEC). A five-tab main UI with no auth gate — the app launches
/// straight into the tabs, always on Stable. `SHOWCASE_GESTURES` swaps the whole UI for the flat
/// pinch/rotate screen (BE-0232), mirroring the iOS and Compose twins.
class ShowcaseApp extends StatelessWidget {
  const ShowcaseApp({super.key, required this.model, required this.uiTest});

  final AppModel model;
  final bool uiTest;

  @override
  Widget build(BuildContext context) {
    // SHOWCASE_UITEST disables animations so condition waits stay tight (SPEC §3): strip the route
    // transition so a pushed detail is on-screen the instant the tap lands, with no fade to race.
    final transitions = uiTest
        ? const PageTransitionsTheme(
            builders: {
              TargetPlatform.iOS: _NoTransitionsBuilder(),
              TargetPlatform.android: _NoTransitionsBuilder(),
            },
          )
        : const PageTransitionsTheme();
    return MaterialApp(
      title: 'Showcase Flutter',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(useMaterial3: true, pageTransitionsTheme: transitions),
      home: model.gesturesMode ? const GestureScreen() : RootScreen(model: model),
    );
  }
}

/// A route transition that shows the destination immediately — no fade or slide (SPEC §3).
class _NoTransitionsBuilder extends PageTransitionsBuilder {
  const _NoTransitionsBuilder();

  @override
  Widget buildTransitions<T>(
    PageRoute<T> route,
    BuildContext context,
    Animation<double> animation,
    Animation<double> secondaryAnimation,
    Widget child,
  ) =>
      child;
}

/// The five-tab main UI (SPEC §5). An [IndexedStack] keeps every tab's element subtree alive across
/// switches, the Flutter analog of the iOS `TabView`; the tabs that push a detail (Stable, Notices)
/// do so on the root [Navigator], so the cross-backend `back` step pops them.
class RootScreen extends StatelessWidget {
  const RootScreen({super.key, required this.model});

  final AppModel model;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: model,
      builder: (context, _) {
        return Scaffold(
          body: IndexedStack(
            index: model.selectedTab.index,
            children: [
              StableScreen(model: model),
              SearchScreen(model: model),
              LogScreen(model: model),
              NoticesScreen(model: model),
              PermissionsScreen(model: model),
            ],
          ),
          bottomNavigationBar: Material(
            elevation: 3,
            child: SafeArea(
              child: Row(
                children: [
                  _TabItem(model: model, tab: ShowcaseTab.stable, id: 'stable', icon: Icons.home, label: 'Stable'),
                  _TabItem(model: model, tab: ShowcaseTab.search, id: 'search', icon: Icons.search, label: 'Search'),
                  _TabItem(model: model, tab: ShowcaseTab.log, id: 'log', icon: Icons.create, label: 'Log'),
                  _TabItem(model: model, tab: ShowcaseTab.notices, id: 'notice', icon: Icons.notifications, label: 'Notices'),
                  _TabItem(model: model, tab: ShowcaseTab.permissions, id: 'perm', icon: Icons.lock, label: 'Permissions'),
                ].map((t) => Expanded(child: t)).toList(),
              ),
            ),
          ),
        );
      },
    );
  }
}

/// A bottom-nav item.
///
/// A hand-built bar rather than Material's `NavigationBar`, because that widget sets the semantics
/// label to `"Search\nTab 2 of 5"`, breaking the exact `{ label: "Search", traits: [button] }`
/// selector the shared scenarios cross tabs by. `excludeSemantics: true` drops the icon/text child
/// semantics so the node carries exactly the tab name, the button trait, and (on the current tab)
/// the selected trait — the native tab-bar contract. The namespace-root id (`stable`, `search`, …)
/// surfaces only in the a11y build so the one literal `id` also selects the tab (SPEC §5,
/// `scenarios/tabs.yaml`); the `-noax` twin crosses by label + button alone.
class _TabItem extends StatelessWidget {
  const _TabItem({required this.model, required this.tab, required this.id, required this.icon, required this.label});

  final AppModel model;
  final ShowcaseTab tab;
  final String id;
  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    final selected = model.selectedTab == tab;
    return Semantics(
      identifier: kAccessible ? id : null,
      label: label,
      button: true,
      selected: selected,
      container: true,
      excludeSemantics: true,
      child: InkWell(
        onTap: () => model.selectedTab = tab,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, color: selected ? Theme.of(context).colorScheme.primary : null),
              Text(label, style: Theme.of(context).textTheme.labelSmall),
            ],
          ),
        ),
      ),
    );
  }
}
