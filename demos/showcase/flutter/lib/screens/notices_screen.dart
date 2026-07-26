import 'package:flutter/material.dart';

import '../accessibility.dart';
import '../model.dart';
import 'common.dart';

/// Tab: Notices (SPEC §5.5). A plain vertical list of 20 static notices — intentionally longer than
/// one screen, so the bottom rows start off-screen and reaching `notice.row.20` is the canonical
/// scroll-to-element target. Because a `ListView` builds its children lazily, an off-screen row is
/// not in the accessibility tree until scrolled into view — the culling behavior BE-0008 verifies.
/// Tapping a row pushes its detail on the root navigator.
class NoticesScreen extends StatelessWidget {
  const NoticesScreen({super.key, required this.model});

  final AppModel model;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Notices')),
      body: ListView(
        children: [
          for (final notice in model.notices)
            aid(
              'notice.row.${notice.id}',
              ListTile(
                title: Text(notice.title),
                onTap: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => NoticeDetailScreen(model: model, id: notice.id)),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// Notice Detail, pushed by tapping a Notices row (SPEC §5.5).
class NoticeDetailScreen extends StatelessWidget {
  const NoticeDetailScreen({super.key, required this.model, required this.id});

  final AppModel model;
  final int id;

  @override
  Widget build(BuildContext context) {
    final notice = model.notice(id);
    return Scaffold(
      appBar: detailAppBar(notice?.title ?? 'Notice $id'),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            aid('notice.detail.title', Text(notice?.title ?? 'Notice $id', style: Theme.of(context).textTheme.titleLarge)),
            aid('notice.detail.body', Text(notice?.body ?? '')),
          ],
        ),
      ),
    );
  }
}
