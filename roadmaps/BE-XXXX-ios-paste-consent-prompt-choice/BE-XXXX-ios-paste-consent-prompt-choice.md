**English** · [日本語](BE-XXXX-ios-paste-consent-prompt-choice-ja.md)

# BE-XXXX — Extend the iOS system-alert prompt/choice table to the paste-consent prompt

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ios-paste-consent-prompt-choice.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a third prompt, `paste`, to the locale-independent `prompt` / `choice` lookup
[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
built for `handleSystemAlert`
([BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)) — the iOS
system alert guarding a cross-process read of `UIPasteboard.general`'s content and offering to allow
or deny it. BE-0320 scoped its lookup to two prompts on purpose — notification authorization and App
Tracking Transparency (ATT) — because those were the two prompts
[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md)'s pre-launch
permission presets cannot pre-answer. The paste-consent prompt belongs to that same family: no
`simctl privacy` service, and no other pre-launch toggle, can pre-answer it either, so a scenario
that means to grant or deny it today has only the literal, locale-fragile
`sel: { label: "Allow Paste" }` to reach for. This item's contribution is twofold: closing that
label-lookup gap the same way BE-0320 closed it for the first two prompts, and adding the first
showcase fixture that exercises a genuine cross-process paste — the exact case bajutsu's own
Permissions tab today routes around.

## Motivation

Real paste flows read content that something outside the app under test wrote — a coupon code
copied from Notes, a link shared from Safari, or, inside a bajutsu scenario itself,
[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)'s
`setClipboard`, which seeds the Simulator's pasteboard from outside the app process entirely. iOS
meets exactly that case with a system alert: since iOS 16, reading pasteboard content that a
different process last wrote triggers a consent dialog offering to allow or deny the read, unless
the source is the same app. A scenario that seeds the pasteboard from outside the app and then reads
it back inside the app under test runs straight into this prompt.

No pre-launch toggle answers it ahead of time. BE-0276's permission presets write Transparency,
Consent, and Control (TCC) state through `simctl privacy` before the app process starts, but the
paste-consent prompt is not a TCC service any more than notification authorization or ATT
are — [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
names exactly those two as the prompts `simctl privacy` cannot reach, and the paste-consent prompt
belongs to the same class. The only known deterministic mechanism is therefore the one BE-0315 and
BE-0316 already built for the other two: a native tap on the SpringBoard alert, resolved through the
same `resolve_unique` discipline every other selector already follows.

That mechanism has been generically capable of clearing this prompt since BE-0316 shipped.
`bajutsu/agents/alerts.py` already names "Allow Paste" among the examples its vision guard
recognizes, and a scenario can already write `handleSystemAlert: { sel: { label: "Allow Paste" } }`
or `systemAlertHandling: { instruction: ["Allow Paste"] }` today. But BE-0320 pins the Simulator's
system language specifically because `handleSystemAlert`'s label match is exact, so a literal
English string resolves only on an English-locale Simulator — the identical fragility BE-0320 closed
for notifications and ATT by resolving `grant` and `deny` from the Simulator's own shipped strings
instead of a hand-transcribed literal. The paste-consent prompt inherits that fragility today,
unclosed.

Nothing has actually exercised even that literal path.
[`demos/showcase/ios/swiftui/Sources/PermissionsView.swift`](../../demos/showcase/ios/swiftui/Sources/PermissionsView.swift)'s
own comment states plainly that reading a pasteboard seeded by another process trips this prompt,
and the showcase's System section avoids it on purpose: its `Copy` and `Paste` buttons
write and read within the same app, so the round-trip "reads back silently" rather than surface the
alert this item targets. An author who wants to prove a genuine cross-process paste — the case
BE-0052's own `setClipboard` exists for — has no demonstrated scenario to build from today, native or
otherwise.

## Detailed design

The work divides into four units: extending the locale-keyed lookup, building a showcase fixture
around the existing Permissions tab, verifying both against a real Simulator, and updating the docs
once that verification lands — the verification unit deliberately first-class, since this proposal
has not confirmed whether BE-0052's `setClipboard` actually triggers the alert the way a genuine
cross-app paste does.

1. **Extend `bajutsu/scenario/system_alerts.py`'s prompt table with a third entry.** The
   `SystemAlertPrompt` literal gains `"paste"` alongside today's `"notifications"` and `"tracking"`;
   the `_Prompts` `TypedDict` gains a matching `paste` key so a half-filled entry stays a type error,
   exactly as it already does for the other two; and `_LABELS["paste"]` holds the `grant` / `deny`
   label pair per language subtag. The module's own docstring names the exact framework and
   `Localizable.strings` key each of today's two prompts was transcribed from
   (`UserNotificationsServer.framework` and `TCC.framework`); the implementing session must locate the
   paste-consent alert's equivalent shipped strings the same way before writing the third entry,
   rather than guessing at the wording, and unit 3 below is where that lookup gets confirmed against
   a real Simulator. No other part of the module changes: `system_alert_label`'s resolution, the
   `UncoveredSystemAlertLocale` failure mode, and `covered_languages` all already generalize to a
   third prompt with no change to their bodies. `HandleSystemAlert`
   (`bajutsu/scenario/models/actions.py`) needs no schema change beyond widening the type it already
   imports; `sel` / `label` / `labelMatches` / `index` keep working unchanged for every alert this
   table does not name, exactly as they do today for the two it already covers.
2. **A showcase scenario that reaches the alert from outside the app, reusing today's fixture rather
   than adding new app UI.** The Permissions tab's existing `sys.paste` button already reads
   `UIPasteboard.general.string` unconditionally
   ([`PermissionsView.swift`](../../demos/showcase/ios/swiftui/Sources/PermissionsView.swift),
   [`PermissionsController.swift`](../../demos/showcase/ios/uikit/Sources/PermissionsController.swift)),
   with no dependency on the tab's own `Copy` button having run first, so seeding the pasteboard
   through `setClipboard` instead of `Copy` is enough to make that same button trip the cross-process
   prompt — no new app-side control, and no BajutsuKit change, only a new scenario file:

   ```yaml
   - name: grant the paste-consent prompt with handleSystemAlert
     steps:
       - setClipboard: { text: "bajutsu-cross-clip" }                    # write from outside the app (BE-0052)
       - tap: { label: "Permissions", traits: [button] }
       - scroll: { to: { id: [sys.paste.value, sys_paste_value] }, direction: down }
       - tap: { id: [sys.paste, sys_paste] }                              # the app reads UIPasteboard.general.string
       - handleSystemAlert: { prompt: paste, choice: grant, timeout: 10 } # accept the consent prompt
     expect:
       - value: { sel: { id: [sys.paste.value, sys_paste_value] }, equals: "bajutsu-cross-clip" }
   ```

   Following [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)'s
   precedent, this scenario stays in its own file rather than joining the existing `system.yaml` or
   `permission.yaml`, and reuses BE-0316's own `systemalert` tag: Android's `smoke (adb)` job runs a
   fixed scenario list that never names this file, and the tag already keeps such a fixture out of
   the local bulk `run-swiftui` / `run-uikit` runs that do not reset per-scenario device state. Wire
   it into CI the same way BE-0316 did: name the new file as an entry in the `run (xcuitest)` job's
   single `scenarios:` list, since a tag alone adds nothing to a CI job that names every scenario
   file explicitly — and since that list orders its permission scenarios last for device-state
   neutrality, the new entry belongs there too.
3. **Verify on a real Simulator, and name the fallback if verification does not hold.** Two facts
   here are unconfirmed, and neither is provable off-Simulator: whether `simctl pbcopy`'s write is
   attributed as "another process" strongly enough for iOS to raise the consent prompt at all — a
   write made through the Simulator's own control channel is not the same code path as one app
   handing pasteboard content to another — and what exact button strings iOS's pasteboard consent
   alert ships for the languages this table means to cover. The implementing session must boot a
   Simulator, run the sequence unit 2 proposes, and confirm the prompt actually appears before
   transcribing its labels into unit 1's table. If `setClipboard` does not trigger the prompt, unit
   2's fixture needs a genuine second process to write the pasteboard instead — a heavier fixture (a
   second demo target, or some other cross-process write outside bajutsu's own device-control
   channel) that this item leaves as an open design question for that implementing session, mirroring
   how [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)
   itself carved `setTimezone` and `shake` into their own deferred items once triage found no reliable
   mechanism, rather than shipping a step that only appears to work. While that Simulator is already
   booted, the same session should also cheaply probe whether the "Paste from Other Apps" preference
   (see *Alternatives considered* below) can be pre-set from outside the app — for instance with a
   `defaults write` against the target app's identifier — and report back if it can, rather than leave
   this item's rejection of a preventive mechanism unexamined.
4. **Docs.** Add `paste` beside `notifications` and `tracking` in
   [`docs/scenarios.md`](../../docs/scenarios.md#naming-the-intent-instead-of-the-text)'s
   "Naming the intent instead of the text" section and its Japanese mirror, once unit 3 confirms the
   labels — the same two limits that section already states (Simulator-only pin; the reactive guard's
   default labels stay English) apply to this third prompt unchanged, so neither needs restating from
   scratch.

## Alternatives considered

- **Widen the table to arbitrary SpringBoard alerts, not one named prompt at a time.**
  Rejected, mirroring BE-0316's own rejection of "cover every SpringBoard alert, not only a
  permission prompt": an open-ended translation table would need to track Apple's exact
  operating-system-supplied text for every alert and every locale indefinitely, the same
  maintenance burden BE-0320 rejected for its own two prompts. Adding one named, independently
  verifiable prompt at a time keeps every entry checkable against a real Simulator rather than
  trusted on faith.
- **Rely solely on the generic `sel: { label: "Allow Paste" }` literal (today's status quo).**
  Rejected as the only answer: it inherits BE-0320's exact locale fragility — a literal English
  string resolves only on an English-locale Simulator — for a prompt real scenarios will want to
  grant, the precise gap BE-0320 closed for notifications and ATT.
- **Build a dedicated two-app fixture instead of `setClipboard`.** A second demo target that
  writes the pasteboard as a genuinely separate process would sidestep unit 3's open question about
  whether `simctl pbcopy` is attributed as "another process" strongly enough to raise the prompt.
  Deferred as the fallback unit 3 already names, rather than adopted up front: it is a heavier
  fixture, and `setClipboard` is worth trying first since it needs no new demo target if it works.
- **Prevent the prompt entirely, the way BE-0276 prevents a TCC-backed permission prompt.**
  Rejected for the same reason BE-0315 rejected it for notifications and ATT: since iOS 16.1,
  Settings does ship a per-app "Paste from Other Apps" preference (Ask / Allow / Deny) that governs
  exactly this alert, but what remains unknown is whether anything can pre-set that preference from
  outside the app on a Simulator, the way BE-0320 pre-sets `AppleLanguages`. Until that is confirmed,
  a reactive tap is the only mechanism known to work today. A future item can revisit prevention if
  pre-setting turns out to be possible.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — extend `bajutsu/scenario/system_alerts.py` with a third prompt, `paste` (schema,
      `_Prompts`/`_LABELS`, `HandleSystemAlert`'s widened literal).
- [ ] Unit 2 — a showcase scenario that seeds the pasteboard with `setClipboard` and reads it back
      through the existing Permissions tab, tagged and wired into CI like BE-0316's fixture.
- [ ] Unit 3 — on-device verification that `setClipboard` triggers the prompt at all, and
      transcription of its real button strings; name a fallback fixture if it does not.
- [ ] Unit 4 — docs: add `paste` to `docs/scenarios.md`'s "Naming the intent instead of the text"
      section and its Japanese mirror.

## References

- [BE-0320 — Make the iOS system-alert button selector deterministic under a non-English Simulator locale](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md) —
  the `prompt` / `choice` lookup this item extends with a third prompt.
- [BE-0316 — Explicit mid-flow step for iOS permission-prompt alerts](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) —
  `handleSystemAlert`, and the precedent for rejecting an open-ended, all-alerts widening in favor of
  one named prompt at a time.
- [BE-0315 — Make the reactive alert guard deterministic and native, reusing BE-0316's SpringBoard path](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md) —
  the native dismiss path this item's fixture exercises, and the precedent for a prompt outside TCC's
  reach needing a reactive rather than a preventive mechanism.
- [BE-0052 — Device-state primitives: timezone, clipboard, shake](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md) —
  `setClipboard`, the cross-process pasteboard write this item's fixture builds on, and the precedent
  for naming a mechanism's feasibility as open rather than assuming it.
- [BE-0276 — Declarative per-scenario permission state](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) —
  the pre-launch TCC preset this prompt falls outside, same as notifications and ATT.
- [`bajutsu/scenario/system_alerts.py`](../../bajutsu/scenario/system_alerts.py) — the locale-keyed
  lookup this item's unit 1 extends.
- [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py) —
  `HandleSystemAlert`, whose `prompt` field widens to the new literal.
- [`demos/showcase/scenarios/permission_system_alert.yaml`](../../demos/showcase/scenarios/permission_system_alert.yaml) —
  the fixture-file and CI-wiring precedent unit 2 follows.
- [`demos/showcase/ios/swiftui/Sources/PermissionsView.swift`](../../demos/showcase/ios/swiftui/Sources/PermissionsView.swift),
  [`demos/showcase/ios/uikit/Sources/PermissionsController.swift`](../../demos/showcase/ios/uikit/Sources/PermissionsController.swift) —
  the existing `sys.paste` / `sys.paste.value` controls unit 2 reuses without any app-code change.

