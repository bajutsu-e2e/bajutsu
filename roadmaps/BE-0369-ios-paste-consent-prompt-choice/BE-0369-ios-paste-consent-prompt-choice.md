**English** · [日本語](BE-0369-ios-paste-consent-prompt-choice-ja.md)

# BE-0369 — Extend the iOS system-alert prompt/choice table to the paste-consent prompt

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0369](BE-0369-ios-paste-consent-prompt-choice.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0369") |
| Implementing PR | [#1652](https://github.com/bajutsu-e2e/bajutsu/pull/1652), [#1670](https://github.com/bajutsu-e2e/bajutsu/pull/1670) |
| Topic | Platform support |
| Related | [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a third prompt, `paste`, to a lookup. The lookup maps a `prompt` / `choice` pair to a
button label, whatever the Simulator's locale.
[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
built it for `handleSystemAlert`
([BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)).

The paste-consent prompt is an iOS system alert. It guards a cross-process read of
`UIPasteboard.general`'s content. It offers to allow or deny the read.

BE-0320 scoped its lookup to two prompts on purpose. They are notification authorization and App
Tracking Transparency (ATT). Those were the two prompts that
[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md)'s pre-launch
permission presets cannot pre-answer.

The paste-consent prompt belongs to that same family. No `simctl privacy` service reaches it. No
other pre-launch toggle is known to reach it either.

One question stays open. Unit 3 probes whether something outside the app can pre-set the "Paste from
Other Apps" preference. Meanwhile, a scenario has only the literal `sel: { label: "Allow Paste" }` to
grant or deny the prompt. That selector is locale-fragile.

This item's contribution is twofold:

1. It closes that label-lookup gap the same way BE-0320 closed it for the first two prompts.
2. It adds the first showcase fixture that exercises a genuine cross-process paste. Bajutsu's own
   Permissions tab today routes around exactly that case.

## Motivation

Real paste flows read content that something outside the app under test wrote. Examples are a coupon
code copied from Notes and a link shared from Safari. A bajutsu scenario creates the same case with
[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)'s
`setClipboard`, which seeds the Simulator's pasteboard from outside the app process entirely.

iOS meets that case with a system alert. Since iOS 16, reading content that another process last
wrote triggers a consent dialog. The dialog offers to allow or deny the read. It does not appear when
the source is the same app.

A scenario can seed the pasteboard from outside the app. Reading it back inside the app under test
then runs straight into this prompt.

No pre-launch toggle is known to answer the prompt ahead of time. BE-0276's presets write
Transparency, Consent, and Control (TCC) state with `simctl privacy`. They write it before the app
process starts. The paste-consent prompt falls outside `simctl privacy`'s reach, as do two other
prompts:

- Notification authorization is not a TCC service at all.
- ATT is TCC-backed as `kTCCServiceUserTracking`, yet it has no `simctl` toggle.

[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
names exactly those two as the prompts that `simctl privacy` cannot reach. The paste-consent prompt
belongs to the same class.

One deterministic mechanism is known. BE-0315 and BE-0316 built it for the other two prompts. It is a
native tap on the SpringBoard alert. The tap resolves through the same `resolve_unique` discipline
that every other selector follows.

That mechanism has been generically capable of clearing this prompt since BE-0316 shipped.
`bajutsu/agents/alerts.py` already lists "Allow Paste" among its vision guard's examples. A scenario
can already write either of these today:

- `handleSystemAlert: { sel: { label: "Allow Paste" } }`
- `systemAlertHandling: { instruction: ["Allow Paste"] }`

BE-0320 pins the Simulator's system language, because `handleSystemAlert`'s label match is exact. A
literal English string resolves only on an English-locale Simulator.

BE-0320 closed this fragility for notifications and ATT. It resolved `grant` and `deny` from the
Simulator's own shipped strings. It did not use a hand-transcribed literal. The paste-consent prompt
still has the fragility.

Nothing has actually exercised even the literal path.
[`demos/showcase/ios/swiftui/Sources/PermissionsView.swift`](../../demos/showcase/ios/swiftui/Sources/PermissionsView.swift)
states in its own comment that reading a pasteboard seeded by another process trips this prompt.

The showcase's System section avoids the prompt on purpose. Its `Copy` and `Paste` buttons write and
read within the same app. The round-trip "reads back silently". It never surfaces the alert this item
targets.

An author who wants to prove a genuine cross-process paste has no scenario to build from today. That
is the case BE-0052's own `setClipboard` exists for.

## Detailed design

The work divides into five units:

1. Extend the locale-keyed lookup.
2. Build a showcase fixture around the existing Permissions tab.
3. Verify both against a real Simulator.
4. Update the docs once that verification lands.
5. Cover the new entry in the fast suite.

Verification is a unit of its own on purpose. This proposal has not confirmed one fact: whether
BE-0052's `setClipboard` triggers the alert like a genuine cross-app paste.

1. **Extend `bajutsu/scenario/system_alerts.py`'s prompt table with a third entry.**
   - The `SystemAlertPrompt` literal gains `"paste"` alongside today's `"notifications"` and
     `"tracking"`.
   - The `_Prompts` `TypedDict` gains a matching `paste` key. A half-filled entry stays a type error,
     exactly as it already does for the other two.
   - `_LABELS["paste"]` holds the `grant` / `deny` label pair per language subtag.

   The module's own docstring names a framework and a `Localizable.strings` key for each of today's
   two prompts. Those are `UserNotificationsServer.framework` and `TCC.framework`. The strings were
   copied from them.

   The implementer must find the paste-consent alert's shipped strings the same way. This comes before
   writing the third entry, and the implementer must not guess at the wording. Unit 3 below confirms
   the lookup against a real Simulator.

   Three pieces of prose each name "the two prompts" explicitly:

   - The module docstring.
   - The comment above `SystemAlertPrompt`.
   - `HandleSystemAlert`'s own docstring in `bajutsu/scenario/models/actions.py`.

   Nothing in `make check` pins that prose against the `Literal`. The same change rewrites all three.
   Otherwise unit 1 ships a module whose own docstring contradicts its type.

   No other part of the module's behavior changes. `system_alert_label`'s resolution, the
   `UncoveredSystemAlertLocale` failure mode, and `covered_languages` already generalize to a third
   prompt with no change to their bodies.

   `HandleSystemAlert` (`bajutsu/scenario/models/actions.py`) needs no schema change. Its imported type
   widens, and nothing else moves. `sel` / `label` / `labelMatches` / `index` keep working for
   every alert this table does not name.
2. **A showcase scenario that reaches the alert from outside the app.** It reuses today's fixture
   rather than adding new app UI. The Permissions tab's existing `sys.paste` button already reads
   `UIPasteboard.general.string` unconditionally
   ([`PermissionsView.swift`](../../demos/showcase/ios/swiftui/Sources/PermissionsView.swift),
   [`PermissionsController.swift`](../../demos/showcase/ios/uikit/Sources/PermissionsController.swift)).
   The read does not depend on the tab's own `Copy` button having run first. Seeding the pasteboard
   through `setClipboard` instead of `Copy` makes that same button trip the cross-process prompt.
   The change needs no new app-side control and no BajutsuKit change. It needs a new scenario file:

   ```yaml
   - name: grant the paste-consent prompt with handleSystemAlert
     tags: [systemalert]              # the tag that keeps it out of the local bulk runs
     systemAlertHandling: false       # with the guard off, a failed tap fails the run (BE-0316's rationale)
     preconditions:
       launchEnv: { SHOWCASE_UITEST: "1" }
     steps:
       - setClipboard: { text: "bajutsu-cross-clip" }                    # write from outside the app (BE-0052)
       - tap: { label: "Permissions", traits: [button] }
       - scroll: { to: { id: [sys.paste.value, sys_paste_value] }, direction: down }
       - tap: { id: [sys.paste, sys_paste] }                              # the app reads UIPasteboard.general.string
       - handleSystemAlert: { prompt: paste, choice: grant, timeout: 10 } # accept the consent prompt
       # The app publishes the pasted value only after its read returns, out of process from the
       # alert — so condition-wait for it rather than race the assertion (prime directive 2).
       - wait: { for: { id: [sys.paste.value, sys_paste_value], value: "bajutsu-cross-clip" }, timeout: 10 }
     expect:
       - value: { sel: { id: [sys.paste.value, sys_paste_value] }, equals: "bajutsu-cross-clip" }
   ```

   [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) set the
   precedent. This scenario stays in its own file, not in the existing `system.yaml` or
   `permission.yaml`. It reuses BE-0316's own `systemalert` tag:

   - Android's `smoke (adb)` job runs a fixed scenario list that never names this file.
   - The tag already keeps such a fixture out of the local bulk `run-swiftui` / `run-uikit` runs, which
     do not reset per-scenario device state.

   Wire the scenario into CI by naming the new file in a job's `scenarios:` list. A tag alone adds
   nothing to a CI job that names every scenario file explicitly.

   Land it first in the **non-gating** `actuation (xcuitest)` job. That job is the lane's landing pad
   for on-device coverage that has not yet earned a place in the gating `run` job (BE-0218). It is also
   where `device.yaml`'s own `setClipboard` already runs.

   Promote the scenario into `run`'s list after unit 3 has proven that the prompt appears. Place it
   after that list's permission scenarios, for device-state neutrality.

   Landing in `run` first would rest the required `E2E` gate on an unverified mechanism. An absent prompt would time the `handleSystemAlert` step out. Every unrelated PR would
   then have a red required check.

   Either way, the same change updates the receiving job's header comments. Those comments count the
   files and scenario documents that the job runs.
3. **Verify on a real Simulator, and name the fallback if verification does not hold.** Two facts
   are unconfirmed. Neither is provable off-Simulator:
   - Whether `simctl pbcopy`'s write counts as "another process" strongly enough for iOS to raise the
     consent prompt at all. A write through the Simulator's own control channel is not the same code
     path as one app handing pasteboard content to another.
   - Which exact button strings iOS's pasteboard consent alert ships for the languages this table
     means to cover.

   The implementer must boot a Simulator and run the sequence that unit 2 proposes. The implementer
   must confirm that the prompt actually appears. Only then should the labels go into unit 1's table.

   Suppose `setClipboard` does not trigger the prompt. Unit 2's fixture then needs a genuine second
   process to write the pasteboard. That is a heavier fixture: a second demo target, or some other
   cross-process write outside bajutsu's own device-control channel. This item leaves it as an open
   design question for the implementer.

   [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)
   took the same stance. It carved `setTimezone` and `shake` into their own deferred items once triage
   found no reliable mechanism. It did not ship a step that only appears to work.

   While that Simulator is already booted, the implementer should also probe cheaply whether the
   "Paste from Other Apps" preference (see *Alternatives considered* below) can be pre-set from
   outside the app. A `defaults write` against the target app's identifier is one way to probe. The
   implementer should report back if the preference can be pre-set. Otherwise this item's rejection
   of a preventive mechanism stays unexamined.
4. **Docs.** Add `paste` beside `notifications` and `tracking` in two places, once unit 3 confirms the
   labels:
   - [`docs/scenarios.md`](../../docs/scenarios.md#naming-the-intent-instead-of-the-text)'s "Naming the
     intent instead of the text" section.
   - [`docs/dsl-grammar.md`](../../docs/dsl-grammar.md)'s `handleSystemAlert` production, which spells
     the field out as `prompt: notifications|tracking`.

   Update both files' Japanese mirrors as well. Nothing in `make check` pins that grammar against the
   models. A docs unit scoped to `scenarios.md` alone would leave a reference that silently omits
   `paste`. It would read as though the schema rejected `paste`.

   `docs/scenarios.md` already states two limits. One is the Simulator-only language pin. The other is
   the reactive guard's still-English default labels. Both apply to this third prompt unchanged, so
   neither needs restating from scratch.
5. **Tests.** Cover `paste` in
   [`tests/scenario/test_system_alerts.py`](../../tests/scenario/test_system_alerts.py).
   That file names today's prompts literally. `test_every_covered_language_answers_both_choices`
   iterates `("notifications", "tracking")`. The label expectations are a hand-written `parametrize`
   list.

   Suppose an entry is added to `_LABELS` without touching that file. The gate stays green. The
   half-filled-entry guard never looks at the new prompt. That guard keeps `deny` from resolving while
   `grant` raises a `KeyError` mid-run.

   Unit 3's on-device verification cannot stand in for this unit. `make check` runs on Linux with no
   Simulator.

## Alternatives considered

- **Widen the table to arbitrary SpringBoard alerts, not one named prompt at a time.** Rejected,
  mirroring BE-0316's own rejection of "cover every SpringBoard alert, not only a permission prompt".
  An open-ended translation table would track Apple's exact system-supplied text for every alert and
  every locale, indefinitely. BE-0320 rejected that same maintenance burden for its own two prompts.
  Adding one named, independently verifiable prompt at a time keeps every entry checkable against a
  real Simulator rather than trusted on faith.
- **Rely solely on the generic `sel: { label: "Allow Paste" }` literal (today's status quo).**
  Rejected as the only answer. The literal inherits BE-0320's exact locale fragility. A literal
  English string resolves only on an English-locale Simulator. Real scenarios will want to grant this
  prompt. It is the precise gap BE-0320 closed for notifications and ATT.
- **Build a dedicated two-app fixture instead of `setClipboard`.** A second demo target would write
  the pasteboard as a genuinely separate process. That would sidestep unit 3's open question. The
  question is whether `simctl pbcopy` counts as "another process" strongly enough to raise the
  prompt. Deferred as the fallback that unit 3 already names, rather than adopted up front. It is a
  heavier fixture. `setClipboard` is worth trying first, since it needs no new demo target if it
  works.
- **Prevent the prompt entirely, the way BE-0276 prevents a TCC-backed permission prompt.** Rejected,
  for the same reason BE-0315 rejected it for notifications and ATT. Since iOS 16.1, Settings does
  ship a per-app "Paste from Other Apps" preference (Ask / Allow / Deny). That preference governs
  exactly this alert. What remains unknown is whether anything can pre-set it from outside the app on
  a Simulator, the way BE-0320 pre-sets `AppleLanguages`. Until that is confirmed, a reactive tap is
  the only mechanism known to work today. A future item can revisit prevention if pre-setting turns
  out to be possible.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — extend `bajutsu/scenario/system_alerts.py` with a third prompt, `paste` (schema,
      `_Prompts`/`_LABELS`, `HandleSystemAlert`'s widened literal), rewriting the module docstring,
      the `SystemAlertPrompt` comment, and `HandleSystemAlert`'s docstring, which each say "two prompts".
- [x] Unit 2 — `demos/showcase/scenarios/paste_system_alert.yaml`, seeding the pasteboard with
      `setClipboard` and reading it back through the existing Permissions tab, tagged `systemalert`
      like BE-0316's fixture and landed on the non-gating `actuation (xcuitest)` job. Promotion into
      the gating `run` list is deliberately left to a follow-up, so the lane's first cross-process
      paste earns its stability on CI's own hosts before a required check rests on it (BE-0218).
      The fixture needed one change the design did not foresee — see unit 3.
      A follow-up widened the fixture to four scenarios — `grant` and `deny` under the target's
      locale, then the same pair under `locale: ja_JP` — since one granting scenario left the deny
      label of either language, and both Japanese labels, checked by unit 5's table alone. The deny
      half took one more showcase change: a refused read returns nil, so the apps publish `(none)`
      rather than `""`, which is what gives that half a positive condition to wait for instead of a
      field indistinguishable from one never read.
- [x] Unit 3 — verified on a booted Simulator (iPhone 17, iOS 26.5), against both the SwiftUI and the
      UIKit showcase app, under `en_US` and `ja_JP`.
- [x] Unit 4 — docs: `paste` added to `docs/scenarios.md`'s "Naming the intent instead of the text"
      section, `docs/dsl-grammar.md`'s `handleSystemAlert` production, and `docs/ci.md`'s `actuation`
      job description, plus all three Japanese mirrors. `demos/showcase/SPEC.md` (both languages)
      records the off-the-main-thread read unit 3 forced.
- [x] Unit 5 — tests: `paste` covered in `tests/scenario/test_system_alerts.py`. The half-filled-entry
      guard now derives its prompts from `SystemAlertPrompt` itself rather than a hand-written tuple,
      so a fourth prompt cannot be added to `_LABELS` while that guard keeps checking only three.

### What unit 3 found

Unit 3 settled three facts that the proposal left open. It also found one fact the proposal did not
anticipate.

- **`setClipboard` does raise the prompt.** iOS attributes a `simctl pbcopy` write to
  `CoreSimulatorBridge`, another process. The app's own read therefore raises `"Showcase SwiftUI" would
  like to paste from "CoreSimulatorBridge"`. The heavier two-app fallback fixture is not needed. iOS
  asks for consent on every read, so the fixture is repeatable on one device.
- **The button strings** come from `DragUI.framework/<lang>.lproj/Localizable.strings`
  (`PASTE_AUTHORIZATION_BUTTON_ALLOW` / `_DENY`). That is a third framework beside the two the module
  already cites. The strings are `Allow Paste` / `Don’t Allow Paste` and `ペーストを許可` /
  `ペーストを許可しない`. They are identical under the iOS 18.6 and 26.5 runtimes and across `en` /
  `en_GB` / `en_AU`. The English deny label carries the typographic apostrophe (U+2019) that this
  lookup exists to get right.
- **The preference cannot be pre-set from outside the app.** iOS records the consent in TCC as
  `kTCCServicePasteboard`, but `simctl privacy` exposes no toggle for that service. ATT has the same
  shape. Writing the Simulator's `TCC.db` directly did not suppress the prompt either, and the system
  restored the row. *Alternatives considered*'s rejection of a preventive mechanism stands, now on
  measurement rather than on an open question.
- **The synchronous read deadlocks the fixture the design proposed.** `UIPasteboard.general.string`
  blocks its caller for as long as the prompt is up. The showcase read on the main thread, so
  XCUITest's tap on `sys.paste` never returned. The run diagnosed a mid-run runner crash, and the
  `handleSystemAlert` step that would have answered the prompt could never run.

  The fix is one change per showcase app: read off the main thread and publish the value when the read
  returns. The design's own `wait` step already assumed that behavior. `system.yaml` gains the
  matching condition wait, since its in-app round-trip is now asynchronous too.

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

