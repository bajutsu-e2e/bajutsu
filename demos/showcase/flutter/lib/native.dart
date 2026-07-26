import 'package:flutter/services.dart';

/// The two deliberate OS-alert requests (SPEC §5.4 / §7), bridged to the native side.
///
/// Requesting notification / location authorization raises an out-of-process system alert — the
/// canonical fixture for the run's vision alert guard / `dismissAlerts`. Flutter cannot raise these
/// from Dart, so `AppDelegate` (iOS) and `MainActivity` (Android) do it over this channel and hand
/// back the resulting status string, which the Permissions screen mirrors to `perm.*.value`. The
/// channel is separate from `showcase/launch` so the launch read and the permission requests stay
/// independently testable.
const MethodChannel _channel = MethodChannel('showcase/native');

/// Raise the notification-authorization prompt; resolves to `authorized` / `denied` once answered.
Future<String> requestNotif() async => await _channel.invokeMethod<String>('requestNotif') ?? 'denied';

/// Raise the location-authorization prompt; resolves to `authorizedWhenInUse` / `denied`.
Future<String> requestLocation() async => await _channel.invokeMethod<String>('requestLocation') ?? 'denied';
