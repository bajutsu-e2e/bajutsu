**English** · [日本語](BE-0320-ios-system-alert-locale-determinism-ja.md)

# BE-0320 — Make the iOS system-alert button selector deterministic under a non-English Simulator locale

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0320](BE-0320-ios-system-alert-locale-determinism.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0320") |
| Implementing PR | [#1397](https://github.com/bajutsu-e2e/bajutsu/pull/1397) |
| Topic | Platform support |
| Related | [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) |
<!-- /BE-METADATA -->

## Introduction

`handleSystemAlert` ([BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md))
taps a button on an iOS SpringBoard permission prompt by the button's visible label. The "Allow
Notifications" dialog is one example. The label is deterministic only on a Simulator whose system
language happens to be English.

Bajutsu already pins the language of one process for exactly this reason. The `locale` config field
(`bajutsu/config/schema.py`, default `en_US`) passes `-AppleLocale` and `-AppleLanguages` as the
target app's own launch arguments (`locale_args` in `bajutsu/simctl.py`). The app's own strings then
resolve to a known value, regardless of the ambient settings of the host machine.

SpringBoard is a separate process that Bajutsu never launches, so those launch arguments never reach
SpringBoard. Its alert buttons render in whichever language the Simulator's own system setting
carries. A scenario that names `label: "Allow"` therefore works by accident, on whichever Simulators
carry an English system language. The scenario fails outright on a Simulator that does not.

The failure is not hypothetical. A Simulator's system language is whatever the machine that created
the Simulator had set. Nothing rules out Japanese on the Mac of a Japanese-speaking contributor.

This item closes the gap in two ways.

- **Primary contribution.** The item extends the determinism that `locale` already gives the app to
  SpringBoard itself. The item forces the Simulator's system language to a known value on every cold
  spawn. Then `label` and `labelMatches` resolve exactly as `locale` predicts on every machine that
  runs the scenario. The app and SpringBoard never disagree on which language is showing.
- **Supporting contribution.** The item adds a small, locale-keyed label lookup. The lookup covers
  the two prompts that the item scopes to first: notification authorization and App Tracking
  Transparency (ATT). The permission-state presets of
  [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) cannot reach
  those two prompts. An author who means "grant" or "deny" then never has to hand-type the localized
  text that a pinned locale renders.

## Motivation

The vision path of the system-alert guard (`bajutsu/agents/alerts.py`) tolerates a localized button.
The path reads the meaning of the screenshot instead of the exact text. `handleSystemAlert` is built
the opposite way on purpose.
[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) resolves the
button through a native accessibility query, to keep the model off the outcome of the step. The
design serves prime directive 1 (AI investigates, never judges).

That determinism is only as good as the input. `HandleSystemAlert` in
`bajutsu/scenario/models/actions.py` accepts the label-based `Selector` fields: `label`,
`labelMatches`, and `index`. A SpringBoard alert button carries no app-assigned identifier, trait, or
value. The button carries its visible text. Once the literal label stops matching, no
locale-independent handle remains to fall back on.

The one shipped example already carries this fragility.
`demos/showcase/scenarios/permission_system_alert.yaml` grants the notification prompt with
`handleSystemAlert: { sel: { label: "Allow" }, timeout: 10 }`. That literal string is the one thing
standing between the step and an `AmbiguousSelector`-style failure on a Simulator whose system
language is not English.

The runner today does not report which language rendered the alert. A contributor therefore sees an
unqualified "no button matched". Nothing points at the Simulator's language as the cause.

[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) pre-sets
permission state through `simctl privacy`. That covers the prompts backed by the Transparency,
Consent, and Control (TCC) database. Notification authorization is not a TCC service, and ATT has
no `simctl` toggle at all. Both prompts were the concrete motivation that
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
gives for a reactive dismisser. Both are the prompts that this item's label lookup targets first.

Today, `dismissAlerts.instruction` is free text that the vision locator interprets. The field
already tolerates a localized button, as the rest of the vision path does. BE-0315 itself, however,
proposes evolving that same field into a deterministic ordered list of candidate labels that an
author supplies. Once that ships, the list carries the identical assumption that the named labels
are the ones SpringBoard renders. The list inherits the gap of this item instead of solving it.

A fix at the SpringBoard-language layer serves `handleSystemAlert` today. The fix also serves the
native path that BE-0315 proposes, once the path lands. Neither feature needs to solve the same
problem twice.

## Detailed design

The work divides into four parts: pinning the Simulator's system language, documenting the resulting
contract, adding the locale-keyed convenience lookup, and verifying both on a real Simulator.

1. **Resolve the Simulator's system language deterministically on every cold spawn, and gate warm
   reuse on it matching.**

   `XcuitestEnvironment.start` already forces a cold respawn, instead of reusing a warm runner, when
   a scenario needs `erase` (`bajutsu/platform_lifecycle/environments/xcuitest.py`). Extend that
   condition. A resolved locale that differs from the locale already pinned on a warm runner also
   forces a cold respawn. A runner whose SpringBoard still renders a previous locale must not serve
   the scenario.

   The value to compare and pin follows the precedence that `_launch_params` already resolves for the
   app's own launch arguments: `pre.locale or eff.locale`. The per-scenario `Preconditions.locale`
   override falls back to the target's `locale` config field.

   On a cold spawn, extend `_prepare_simulator` to write the Simulator's system-wide language and
   region after `boot`. The write uses the technique that `xcodebuild`-based screenshot tools already
   rely on: `xcrun simctl spawn <udid> defaults write -globalDomain AppleLanguages -array <language>`
   and the matching `AppleLocale` write.

   A `simctl spawn` command needs an already-booted device. The write therefore reaches the Simulator
   once SpringBoard is already up and rendering whichever language it booted with. An already-running
   process does not pick up a `-globalDomain` write live. The sequence must shut the Simulator down
   and boot it once more. The second boot lets SpringBoard start fresh against the newly written
   value, before the existing install and permission steps proceed.

   The reboot adds one boot cycle to a cold spawn or a locale-triggered respawn. The cost is bounded
   and known, and each spawn pays the cost once, not on every polling tick. The cost therefore takes
   nothing from the runner's per-tick budget, which the detailed design of
   [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
   is careful to protect.

   Unit 4 must confirm that the reboot actually renders SpringBoard in the new language on a real
   Simulator, since nothing here is observable off-Simulator.
2. **Document the resulting contract.** Once unit 1 lands, `label` and `labelMatches` resolve
   identically on every machine that runs the same `locale`. A scenario author who writes
   `label: "Allow"` under `locale: en_US` gets that exact behavior on CI, on a teammate's Mac, and on
   the Simulator of a Japanese-speaking contributor alike. SpringBoard's language is no longer a fact
   of the host machine.

   State the contract explicitly in the description of `locale` in `docs/configuration.md` and in the
   documentation of `handleSystemAlert`. An author who previously guessed at label text then has a
   stated guarantee to write scenarios against, instead of a happy accident to discover by reading
   this item.
3. **Add a locale-keyed label lookup for the two prompts BE-0276 cannot reach, additive to the
   existing selector fields.**

   Unit 3 scopes the lookup to notification authorization and ATT. The TCC-backed presets of
   [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) cannot
   prevent those two prompts. The lookup is keyed by `locale`. The lookup resolves the pair of button
   labels ("grant" and "deny") that SpringBoard renders for the prompt under that locale.

   The existing `label`, `labelMatches`, and `index` fields of `handleSystemAlert` keep working
   exactly as they do today for every other alert. A scenario about one of the two prompts can
   express intent ("grant", "deny") instead of transcribing whichever locale's text happens to
   render.

   The scope is deliberately narrow. The table only ever needs to cover these two prompts, so the
   table never grows into an open-ended translation of arbitrary SpringBoard text. An author can check
   the correctness of the table independently, by booting the pinned `locale` once and reading what
   SpringBoard shows, instead of trusting the table on faith.

   A locale or prompt that the table does not cover keeps working the way it does today: the literal
   `label` or `labelMatches` that a scenario supplies, unlocalized.
4. **Verify on a real Simulator.** The verification has three parts.
   - Boot two Simulators pinned to different `locale` values (for example `en_US` and `ja_JP`). Run
     the same `handleSystemAlert` scenario against each. Confirm that both dismiss the prompt
     deterministically after the reboot of unit 1. The result proves that the write and the extra
     boot change what SpringBoard renders, not merely what a preference file holds.
   - Confirm that the lookup of unit 3 resolves the label it predicts.
   - Within one runner, run a scenario at one locale. Immediately run a second scenario that
     overrides `Preconditions.locale` to a different value. Confirm that the mismatch forces a cold
     respawn instead of reusing the warm runner of the first scenario with its stale language. The
     check proves the warm-reuse gate of unit 1, not merely the cold-spawn write.

   The off-Simulator gate cannot prove the rendered alert text of a Simulator. This unit turns
   units 1–3 from a plausible design into a demonstrated one. The unit mirrors the on-device
   verification unit that both
   [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) and
   [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
   already carry for their own native-tap surfaces.

## Alternatives considered

- **A locale-keyed label dictionary alone, without pinning the Simulator's system language.**
  Rejected as the sole mechanism. The dictionary would need to track the exact button text that
  Apple's operating system supplies, across every locale Bajutsu might run under and every iOS
  version Apple ships, indefinitely. This item can instead make the problem disappear at the source,
  by controlling the Simulator's language the same way `locale` already controls the language of the
  app. The dictionary stays as the narrow, additive unit 3, once unit 1 removes the open-ended part
  of the maintenance burden.
- **Position-based (`index`) selection as the sole locale-agnostic mechanism.** Rejected. The
  approach rests on an unverified assumption: that SpringBoard orders the buttons of a prompt
  identically across every locale, including right-to-left languages. An index also carries no
  readable meaning at the scenario level. The setting `index: 1` does not say "the destructive
  button" the way `"Don't Allow"` does. `index` keeps its existing role as the last-resort
  disambiguator of `handleSystemAlert`
  ([BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)) and takes on
  no second role.
- **Route every non-English-locale run through the AI vision guard.** Rejected. The vision guard
  exists so that the meaning of a screenshot survives localization. Routing every localized run
  through the guard would reintroduce the latency and the credential dependence that
  [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
  exists to remove from the common path. Prime directive 1 also keeps a model call off any signal
  that decides whether a step passed.
- **Leave `locale` app-scoped and require an author to hand-write each target's localized label.**
  This is today's status quo, and the status quo is the gap this item exists to close. Two teams that
  run the identical open-source demo scenario against Simulators with different ambient system
  languages get silently different results today. Developer-machine state that Bajutsu never
  surfaces anywhere drives the difference.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — resolve the Simulator's system language deterministically on every cold spawn (write +
      reboot so SpringBoard actually renders it), and gate warm reuse on the resolved locale matching
      the one already pinned, mirroring `pre.erase`'s existing cold-respawn condition in
      `XcuitestEnvironment.start`. The write is skipped when the device already reads back the
      resolved locale, so the common case costs one read rather than a second boot cycle.
- [x] Unit 2 — document the resulting contract for `label`/`labelMatches` in `docs/configuration.md`
      and `handleSystemAlert`'s own documentation.
- [x] Unit 3 — add a locale-keyed label lookup for notification authorization and ATT (the two
      prompts BE-0276 cannot reach), additive to `label`/`labelMatches`/`index`, never a replacement.
      Reached through `prompt` + `choice` in place of `sel`; an uncovered language fails the step
      loudly rather than tapping a guessed button.
- [x] Unit 4 — on-device verification: two Simulators pinned to different `locale` values dismiss the
      same `handleSystemAlert` scenario deterministically after unit 1's reboot, and a mid-runner
      locale override forces a cold respawn rather than a stale warm reuse.

Verified on a real Simulator (iOS 26.5, Xcode 26):

- The write plus reboot changes what SpringBoard *renders*, not merely what a preference file holds.
  Pinning `ja_JP` and rebooting turns the home screen Japanese (`マップ` / `設定` / `検索`), and
  pinning `en_US` turns the home screen back. Re-pinning the same locale writes nothing and skips the
  extra boot.
- `demos/showcase/scenarios/permission_system_alert.yaml` now names `prompt: notifications` and
  `choice: grant`. The scenario passes at `en_US` (tapping `Allow`) and at `ja_JP` (tapping `許可`).
- The warm-reuse gate holds. Two scenarios at one locale in a single run leave **one** cold-spawn
  runner log. The same pair at two locales leaves **two** logs. The locale change forced the cold
  respawn, so the second scenario did not run on the SpringBoard language of the first.
- Unrelated to this item, iOS suppresses a repeat notification-authorization prompt for the same
  bundle id shortly after the first prompt is answered. Back-to-back runs of that scenario can
  therefore find no prompt. A control run at a single locale reproduced the suppression on the
  *first* scenario, so the cause is an OS behavior, not a locale effect.
- The values of the lookup are Apple's own, read from the strings that the Simulator runtime ships.
  For notifications, the strings are `PERMISSION_ALERT_ALLOW` and `PERMISSION_ALERT_DENY` in
  `UserNotificationsServer.framework`. For ATT, the strings are
  `REQUEST_ACCESS_{ALLOW,DENY}_kTCCServiceUserTracking` in `TCC.framework`. A reader can re-check the
  values of a future runtime the same way, instead of trusting them.

This item leaves two limits open. Both limits are documented in
[`docs/scenarios.md`](../../docs/scenarios.md#naming-the-intent-instead-of-the-text). Both are worth
a follow-up item, not a widening of this one:

- **The pin is Simulator-only.** `simctl` cannot write the preferences of a physical device. A target
  on `xcuitest.deviceType: device` (BE-0238) gets no pin, and the `prompt` / `choice` form would
  resolve a label that nothing guarantees is on screen. Closing the limit properly means teaching the
  runner which environments pin. The runner could then withhold the locale and let the step fail
  with that reason. The change crosses the platform-lifecycle protocol and is wider than the lane of
  this item.
- **The default dismissive labels of `systemAlertHandling` are English literals.** Pinning a
  non-English `locale` now makes `DEFAULT_DISMISSIVE_LABELS` stop matching. The native path of the
  reactive guard reports `unhandled` and falls back to the vision guard, where the path used to tap
  natively. Routing those defaults through the locale-keyed table of this item is the natural fix.
  The fix belongs with the surface of
  [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md),
  not here.

## References

- [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py): `HandleSystemAlert`,
  whose `sel` accepts the label-based `Selector` fields alone, which are the fields this item's gap
  affects.
- [`bajutsu/simctl.py`](../../bajutsu/simctl.py): `locale_args`, the app-scoped launch-argument
  mechanism that this item extends to the Simulator's system language.
- [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py):
  `Preconditions.locale`, the per-scenario override that unit 1 must resolve against the
  target-level `locale` field in `bajutsu/config/schema.py`. The resolution matches the
  `pre.locale or eff.locale` precedence that `_launch_params` already follows.
- [`bajutsu/platform_lifecycle/environments/xcuitest.py`](../../bajutsu/platform_lifecycle/environments/xcuitest.py):
  the existing `pre.erase` cold-respawn condition in `XcuitestEnvironment.start`, which the
  locale-mismatch gate of unit 1 mirrors. Also `_prepare_simulator`, the device-prep sequence that
  unit 1 extends with the write-and-reboot, and `_launch_params`, the precedence that unit 1 reuses.
- [`demos/showcase/scenarios/permission_system_alert.yaml`](../../demos/showcase/scenarios/permission_system_alert.yaml):
  the shipped scenario whose literal `label: "Allow"` already carries the fragility that this item
  closes.
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md):
  `handleSystemAlert`, the step whose selector this item makes locale-safe.
- [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md):
  the native reactive guard whose proposed `instruction` candidate-label list will inherit the fix of
  this item once the list ships.
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md): the
  preset-permission-state item that the label lookup of this item complements for the two prompts TCC
  cannot reach.
