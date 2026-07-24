import 'package:flutter/services.dart';

/// Reads the `SHOWCASE_*` launch-env hooks (SPEC §3) the way each platform delivers them.
///
/// The channel is the single seam that hides the platform difference from the Dart app: on iOS
/// the launch environment XCUITest sets arrives as the process environment (`ProcessInfo`), on
/// Android `launchEnv` arrives as intent extras (BE-0007). The native `AppDelegate` /
/// `MainActivity` read their side and hand back one uniform `Map<String, String>`, so the app
/// reads launch env identically on both backends — the same app-agnostic contract the rest of
/// the system keeps.
const MethodChannel _channel = MethodChannel('showcase/launch');

/// The launch-env map, or an empty map when nothing was injected (a bare `flutter run`).
Future<Map<String, String>> readLaunchEnv() async {
  final result = await _channel.invokeMapMethod<String, String>('launchEnv');
  return result ?? const <String, String>{};
}
