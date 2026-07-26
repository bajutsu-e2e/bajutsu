import 'package:flutter/material.dart';

/// A pushed detail screen's leading back control, carrying the OS-convention identifier
/// `BackButton`.
///
/// The native apps get this id for free from `UINavigationController`; Flutter draws its own
/// navigation chrome, so the app sets the identifier itself — the same app-side id cooperation the
/// whole Flutter convention rests on (BE-0008). It is unconditional (present in the `-noax` build
/// too), because `BackButton` is the platform's back-navigation convention the iOS backend drives
/// (`base.OS_BACK_BUTTON`), not one of the app's own SPEC §8 identifiers. On Android the system
/// back key pops the route, so this control is the iOS path; both call [Navigator.maybePop].
class BackControl extends StatelessWidget {
  const BackControl({super.key});

  @override
  Widget build(BuildContext context) {
    return Semantics(
      identifier: 'BackButton',
      button: true,
      label: 'Back',
      child: BackButton(onPressed: () => Navigator.of(context).maybePop()),
    );
  }
}

/// A plain app bar for a pushed detail: the title carries no id (SPEC §5.1 — a nav-bar title is not
/// an addressable element), and the leading control is the [BackControl].
AppBar detailAppBar(String title) => AppBar(
      automaticallyImplyLeading: false,
      leading: const BackControl(),
      title: Text(title),
    );
