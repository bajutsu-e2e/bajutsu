import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../accessibility.dart';
import '../model.dart';
import '../native.dart';

/// Tab: Permissions (SPEC §5.4 / §7) — the OS-integration screen. It owns the two deliberate OS
/// prompts (notifications + location), the alert-guard fixture, plus a System section: an in-app
/// clipboard round-trip the driver can drive and assert. Nothing here runs at launch; the prompts
/// fire only on taps.
class PermissionsScreen extends StatelessWidget {
  const PermissionsScreen({super.key, required this.model});

  final AppModel model;

  @override
  Widget build(BuildContext context) {
    final titleStyle = Theme.of(context).textTheme.titleMedium;
    return Scaffold(
      appBar: AppBar(title: const Text('Permissions')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Notifications', style: titleStyle),
            aid(
              'perm.requestNotif',
              TextButton(
                onPressed: () async => model.notifStatus = await requestNotif(),
                child: const Text('Request Notifications'),
              ),
            ),
            aidValue('perm.notif.value', model.notifStatus, Text('Notifications: ${model.notifStatus}')),
            // A positive condition the run can wait for once granted.
            if (model.notifStatus == 'authorized') aid('perm.notif.authorized', const Text('Granted')),
            Text('Location', style: titleStyle),
            aid(
              'perm.requestLocation',
              TextButton(
                onPressed: () async => model.locationStatus = await requestLocation(),
                child: const Text('Request Location'),
              ),
            ),
            aidValue('perm.location.value', model.locationStatus, Text('Location: ${model.locationStatus}')),
            // Clipboard round-trip (SPEC §5.4): Copy writes a known string, Paste reads it back into
            // sys.paste.value — pasteboard state the driver's app-scoped query cannot otherwise see.
            Text('System', style: titleStyle),
            aid(
              'sys.copy',
              TextButton(
                onPressed: () => Clipboard.setData(const ClipboardData(text: 'bajutsu-clip')),
                child: const Text('Copy'),
              ),
            ),
            aid(
              'sys.paste',
              TextButton(
                onPressed: () async {
                  final data = await Clipboard.getData(Clipboard.kTextPlain);
                  model.pasted = data?.text ?? '';
                },
                child: const Text('Paste'),
              ),
            ),
            aidValue('sys.paste.value', model.pasted, Text('Pasted: ${model.pasted}')),
          ],
        ),
      ),
    );
  }
}
