**English** · [日本語](BE-XXXX-ios-system-alert-locale-determinism-ja.md)

# BE-XXXX — Make the iOS system-alert button selector deterministic under a non-English Simulator locale

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ios-system-alert-locale-determinism.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) |
<!-- /BE-METADATA -->

## Introduction

`handleSystemAlert` ([BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md))
taps a button on an iOS SpringBoard permission prompt — the "Allow Notifications" dialog, for
instance — by its visible label, and that label is deterministic only on a Simulator whose system
language happens to be English. Bajutsu already pins one process's language for exactly this
reason: the `locale` config field (`bajutsu/config/schema.py`, default `en_US`) passes
`-AppleLocale`/`-AppleLanguages` as the target app's own launch arguments, so the app's own strings
resolve to a known value regardless of the host machine's ambient settings
(`bajutsu/simctl.py`'s `locale_args`). SpringBoard is a separate process that Bajutsu never
launches, so those launch arguments never reach it, and its alert buttons render in whichever
language the Simulator's own system setting happens to be. A scenario naming `label: "Allow"`
therefore works only by accident, on whichever Simulators happen to carry an English system
language, and fails outright the moment it runs on one that does not — which is not a
hypothetical: a Simulator's system language is whatever it happens to be set to on the machine
that created it, and nothing rules out that being Japanese on a Japanese-speaking contributor's own
Mac.

This item closes that gap two ways. The primary contribution extends the determinism `locale`
already gives the app to SpringBoard itself: force the Simulator's system language to a known value
on every cold spawn, so `label`/`labelMatches` resolve exactly as `locale` predicts on every machine
that runs the scenario, and the app and SpringBoard never disagree on which language is showing. The
supporting contribution adds a small, locale-keyed label lookup for the two prompts this item scopes
to first — notification authorization and App Tracking Transparency (ATT), the two prompts
[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md)'s
permission-state presets cannot reach — so an author who only means "grant" or "deny" never has to
hand-type the localized text a pinned locale renders.

## Motivation

The system-alert guard's vision path (`bajutsu/agents/alerts.py`) tolerates a localized button
because it reads the screenshot's meaning rather than its exact text, but `handleSystemAlert` is
built the opposite way on purpose: [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)
resolves the button through a native accessibility query specifically to keep the model off the
step's outcome, in service of prime directive 1 (AI investigates, never judges). That
determinism is only as good as its input. `bajutsu/scenario/models/actions.py`'s
`HandleSystemAlert` accepts exactly the label-based `Selector` fields — `label`, `labelMatches`,
`index` — because a SpringBoard alert button carries no app-assigned identifier, trait, or value,
only its visible text; there is no locale-independent handle to fall back on once the literal label
stops matching.

The one shipped example already carries this fragility. `demos/showcase/scenarios/permission_system_alert.yaml`
grants the notification prompt with `handleSystemAlert: { sel: { label: "Allow" }, timeout: 10 }`,
and that literal string is the only thing standing between the step and an
`AmbiguousSelector`-style failure on a Simulator whose system language is not English. Nothing in
the runner today reports which language rendered the alert, so the failure a contributor sees is an
unqualified "no button matched", with no signal pointing at the Simulator's language as the cause.

[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) pre-sets
permission state through `simctl privacy` for the prompts backed by the Transparency, Consent, and
Control (TCC) database, but notification authorization is not a TCC service and ATT has no `simctl`
toggle at all — both were the concrete motivation
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
gives for a reactive dismisser, and both are exactly the prompts this item's label lookup targets
first. Today, `dismissAlerts.instruction` is free text the vision locator interprets, so it already
tolerates a localized button the way the rest of the vision path does. But
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
itself proposes evolving that same field into a deterministic ordered list of candidate labels an
author supplies — and once that ships, the list carries the identical assumption that the labels
named are the ones SpringBoard renders, inheriting this item's gap rather than solving it. A fix at
the SpringBoard-language layer therefore serves `handleSystemAlert` today and BE-0315's proposed
native path once it lands, without either needing to solve the same problem twice.

## Detailed design

The work divides into pinning the Simulator's system language, documenting the resulting contract,
adding the locale-keyed convenience lookup, and verifying both on a real Simulator.

1. **Resolve the Simulator's system language deterministically on every cold spawn, and gate warm
   reuse on it matching.** `XcuitestEnvironment.start` already forces a cold respawn instead of
   reusing a warm runner when a scenario needs `erase`
   (`bajutsu/platform_lifecycle/environments/xcuitest.py`); extend that same condition so a resolved
   locale that differs from the one already pinned on a warm runner forces a cold respawn too, rather
   than being served by a runner whose SpringBoard is still rendering a previous locale. The value to
   compare and pin follows the same precedence `_launch_params` already resolves for the app's own
   launch arguments: `pre.locale or eff.locale`, the per-scenario `Preconditions.locale` override
   falling back to the target's `locale` config field. On a cold spawn, extend `_prepare_simulator` to
   write the Simulator's system-wide language and region after `boot`, using the same technique
   `xcodebuild`-based screenshot tools already rely on: `xcrun simctl spawn <udid> defaults write
   -globalDomain AppleLanguages -array <language>` and the matching `AppleLocale` write. A `simctl
   spawn` command needs an already-booted device to run at all, so this write only reaches the
   Simulator once SpringBoard is already up and rendering whichever language it booted with, and an
   already-running process does not pick up a `-globalDomain` write live — so the sequence must shut
   the Simulator down and boot it once more, letting the second boot's SpringBoard start fresh against
   the newly written value before the existing install/permission steps proceed. This adds one extra
   boot cycle to a cold spawn or a locale-triggered respawn: a bounded, known cost paid once per spawn,
   not on every polling tick, so it still costs nothing on the runner's per-tick budget that
   [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)'s
   detailed design is careful to protect. The reboot is the part unit 4 must confirm actually renders
   SpringBoard in the new language on a real Simulator, since nothing here is observable
   off-Simulator.
2. **Document the resulting contract.** Once unit 1 lands, `label`/`labelMatches` resolve
   identically on every machine that runs the same `locale` — a scenario author who writes
   `label: "Allow"` under `locale: en_US` gets that exact behavior on CI, on a teammate's Mac, and on
   a Japanese-speaking contributor's own Simulator alike, because SpringBoard's language is no longer
   a fact of the host machine. State this explicitly in `docs/configuration.md`'s description of
   `locale` and in `handleSystemAlert`'s own documentation, so an author who was previously guessing
   at label text now has a stated guarantee to write scenarios against, rather than a happy accident
   to discover by reading this item.
3. **Add a locale-keyed label lookup for the two prompts BE-0276 cannot reach, additive to the
   existing selector fields.** For notification authorization and ATT — the two prompts this item
   scopes unit 3 to, because they are exactly the ones
   [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md)'s
   TCC-backed presets cannot prevent — add a lookup keyed by `locale` that resolves the pair of
   button labels ("grant" and "deny") SpringBoard renders for that prompt under that locale, so
   `handleSystemAlert`'s existing `label`/`labelMatches`/`index` fields keep working exactly as they
   do today for every other alert, while a scenario about one of these two prompts can express intent
   ("grant", "deny") instead of transcribing whichever locale's text happens to render. This is
   deliberately narrow: the table only ever needs to cover these two prompts, so it never grows into
   an open-ended translation of arbitrary SpringBoard text, and its correctness is independently
   checkable by booting the pinned `locale` once and reading what SpringBoard shows, rather than
   trusted on faith. A locale or prompt the table does not cover keeps working the way it does
   today — the literal `label`/`labelMatches` a scenario supplies, unlocalized.
4. **Verify on a real Simulator.** Boot two Simulators pinned to different `locale` values (for
   example `en_US` and `ja_JP`), run the same `handleSystemAlert` scenario against each, and confirm
   both dismiss the prompt deterministically after the reboot unit 1 performs — proving the write and
   the extra boot actually change what SpringBoard renders, not just what a preference file holds —
   and that unit 3's lookup resolves the label it predicts. Then, within one runner, run a scenario at
   one locale, immediately run a second scenario overriding `Preconditions.locale` to a different
   value, and confirm the mismatch forces a cold respawn rather than reusing the first scenario's warm
   runner with its stale language, proving unit 1's warm-reuse gate and not only its cold-spawn write.
   A Simulator's rendered alert text cannot be proven by the off-Simulator gate, so this unit is what
   turns units 1–3 from a plausible design into a demonstrated one, mirroring the on-device
   verification unit both
   [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) and
   [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
   already carry for their own native-tap surfaces.

## Alternatives considered

- **A locale-keyed label dictionary alone, without pinning the Simulator's system language.**
  Rejected as the sole mechanism: it would need to track Apple's exact operating-system-supplied
  button text across every locale Bajutsu might run under and every iOS version Apple ships,
  indefinitely, for a problem this item can instead make disappear at the source by controlling the
  Simulator's language the same way `locale` already controls the app's. Kept as the narrow,
  additive unit 3 once unit 1 removes the open-ended part of the maintenance burden.
- **Position-based (`index`) selection as the sole locale-agnostic mechanism.** Rejected: it rests
  on the unverified assumption that SpringBoard orders a prompt's buttons identically across every
  locale, including right-to-left languages, and an index carries no readable meaning at the
  scenario level — `index: 1` does not say "the destructive button" the way `"Don't Allow"` does.
  `index` keeps its existing role as `handleSystemAlert`'s last-resort disambiguator
  ([BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)) rather than
  taking on a second one.
- **Route every non-English-locale run through the AI vision guard.** Rejected: the vision guard
  exists precisely so a screenshot's meaning survives localization, but routing every localized run
  through it would reintroduce the latency and credential dependence
  [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
  exists to remove from the common path, and prime directive 1 keeps a model call off any signal
  that decides whether a step passed.
- **Leave `locale` app-scoped only and require an author to hand-write each target's localized
  label.** This is today's status quo, and it is exactly the gap this item exists to close: two
  teams running the identical open-source demo scenario against Simulators with different ambient
  system languages get silently different results today, driven by developer-machine state Bajutsu
  never surfaces anywhere.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — resolve the Simulator's system language deterministically on every cold spawn (write +
      reboot so SpringBoard actually renders it), and gate warm reuse on the resolved locale matching
      the one already pinned, mirroring `pre.erase`'s existing cold-respawn condition in
      `XcuitestEnvironment.start`.
- [ ] Unit 2 — document the resulting contract for `label`/`labelMatches` in `docs/configuration.md`
      and `handleSystemAlert`'s own documentation.
- [ ] Unit 3 — add a locale-keyed label lookup for notification authorization and ATT (the two
      prompts BE-0276 cannot reach), additive to `label`/`labelMatches`/`index`, never a replacement.
- [ ] Unit 4 — on-device verification: two Simulators pinned to different `locale` values dismiss the
      same `handleSystemAlert` scenario deterministically after unit 1's reboot, and a mid-runner
      locale override forces a cold respawn rather than a stale warm reuse.

## References

- [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py) —
  `HandleSystemAlert`, whose `sel` accepts only the label-based `Selector` fields this item's gap
  affects.
- [`bajutsu/simctl.py`](../../bajutsu/simctl.py) — `locale_args`, the app-scoped launch-argument
  mechanism this item extends to the Simulator's system language.
- [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py) —
  `Preconditions.locale`, the per-scenario override unit 1 must resolve against
  `bajutsu/config/schema.py`'s target-level `locale` field, matching the `pre.locale or eff.locale`
  precedence `_launch_params` already follows.
- [`bajutsu/platform_lifecycle/environments/xcuitest.py`](../../bajutsu/platform_lifecycle/environments/xcuitest.py)
  — `XcuitestEnvironment.start`'s existing `pre.erase` cold-respawn condition, which unit 1's
  locale-mismatch gate mirrors; `_prepare_simulator`, the device-prep sequence unit 1 extends with the
  write-and-reboot; and `_launch_params`, the precedence unit 1 reuses.
- [`demos/showcase/scenarios/permission_system_alert.yaml`](../../demos/showcase/scenarios/permission_system_alert.yaml)
  — the shipped scenario whose literal `label: "Allow"` already carries the fragility this item
  closes.
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) —
  `handleSystemAlert`, the step whose selector this item makes locale-safe.
- [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
  — the native reactive guard whose proposed `instruction` candidate-label list will inherit this
  item's fix once it ships.
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) — the
  preset-permission-state item this item's label lookup complements for the two prompts TCC cannot
  reach.
