**English** · [日本語](BE-0276-scenario-permission-state-ja.md)

# BE-0276 — Declarative per-scenario permission state (simctl privacy / pm grant)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0276](BE-0276-scenario-permission-state.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0276") |
| Implementing PR | [#1129](https://github.com/bajutsu-e2e/bajutsu/pull/1129) |
| Topic | Scenario authoring features |
| Related | [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md) |
| Origin | Maestro |
<!-- /BE-METADATA -->

## Introduction

A scenario-level, declarative way to set OS permission state **before the app launches** — grant
or revoke a permission up front so the runtime prompt never appears. On iOS this drives
`simctl privacy`; on Android it drives `pm grant` / `pm revoke`. It is a deterministic,
AI-free complement to the existing vision **alert guard** ([`dismissAlerts`](../../docs/scenarios.md)):
pre-set the permission you know about, and there is no prompt to dismiss.

## Motivation

Permission prompts (location, camera, contacts, …) are SpringBoard/system UI — out-of-process, so
idb's app-scoped query cannot see or tap them. Today iOS handles them **only** through the vision
alert guard: on a blocked step, `run` screenshots the screen, asks Claude where to tap, and clears
the prompt ([`bajutsu/alerts.py`](../../bajutsu/alerts.py)). That is the *one* AI path left in `run`,
and it is the right tool only when the prompt is unexpected. When the permission is known ahead of
time, pre-setting its state is strictly better:

- **It removes an LLM call from the deterministic path.** Pre-granting is a plain `simctl` /
  `adb` side effect with a machine-checkable result — no screenshot, no model, no `ANTHROPIC_API_KEY`.
  This *reduces* reliance on the alert guard rather than adding a second AI surface (prime directive 1).
- **It makes the state, not just the tap, deterministic.** The alert guard can only react to a
  prompt that appears; it cannot *revoke* a permission to exercise a denied-path flow, nor guarantee
  the app starts from a known permission state. Pre-setting can do both.
- **It is per-scenario.** One scenario tests the request/grant flow itself (permission left
  unset, alert guard or a real tap grants it); a sibling scenario wants the permission already
  granted so it can get straight to the feature under test. That distinction is a property of the
  scenario, so it belongs in the scenario file.

**Competitive context (Maestro).** Maestro ships `setPermissions` out of the box, and
[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)
already flagged it as a table-stakes parity gap while shipping the rest of the device-state family.
This item closes that gap. The differentiator is *how*: a single cross-platform declarative field
that each backend maps to its native mechanism, staying deterministic and app-agnostic.

**Cross-platform footing.** Android already pre-grants runtime permissions via a **config-level**
`grantPermissions` list applied at lease time
([BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md), call site
`bajutsu/platform_lifecycle/environments/android.py`, backed by `Android.grant_permissions` in
`bajutsu/adb.py`). That is per-app, not per-scenario, and Android-only. This item
introduces one **per-scenario** surface that unifies both platforms, so the same field works whether
the target is the iOS Simulator or the Android emulator.

## Detailed design

A new **scenario-level field** (declarative, applied once before launch — the same shape as
`dismissAlerts`, not a mid-flow step). Proposed surface (final names settle on adoption):

```yaml
scenario:
  name: "profile — camera already granted"
  permissions:
    camera: grant
    location: grant
    contacts: revoke
  steps:
    - ...
```

Each entry is `<service>: grant | revoke`. The field is applied by the runner **before the app
process starts**, so the permission state is in place before the app's first request — which is the
only point at which pre-granting can prevent the prompt.

### Shared vocabulary → backend-native mapping

`<service>` is a small **backend-agnostic vocabulary** (e.g. `location`, `camera`, `microphone`,
`contacts`, `photos`, `calendar`, `notifications`). Each backend maps a service to its native
identifier and declares which it can honor; an unmapped service **fails preflight cleanly** (below),
never at runtime.

- **iOS** → `simctl privacy <udid> <grant|revoke> <tcc-service> <bundle>`, mapping e.g.
  `location → location`, `camera → camera`, `contacts → contacts`.
- **Android** → `pm grant|revoke <package> <android.permission.*>`, reusing the plumbing behind the
  existing config-level `grantPermissions`.

### Coverage honesty — notifications

`simctl privacy` has **no notifications service** (iOS notification authorization is not part of TCC —
Transparency, Consent, and Control, the database backing iOS's privacy permissions). So the
iOS backend declares `notifications` **unsupported**: a scenario that lists it fails preflight with a
clear message pointing to `dismissAlerts` as the path for the notification prompt. Android's
`POST_NOTIFICATIONS` *is* a runtime permission (`pm grant`, API 33+), so Android supports it. The
shared vocabulary is honest about this asymmetry rather than pretending to a uniform surface it
cannot deliver.

### Work breakdown (MECE)

1. **Scenario schema + vocabulary** — add the `permissions` field (map of `service → grant|revoke`),
   parse and validate it, define the shared service enum. Reject an unknown service or action at
   parse time.
2. **Capability token + preflight** — add a `deviceControl.permissions` token following
   [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md);
   map the field to it in `bajutsu/capability_preflight.py`, and gate each requested *service* so an
   unsupported service (e.g. `notifications` on iOS) is named individually in the aggregated
   preflight message ([BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md)).
3. **iOS backend** — a pure command builder for `simctl privacy` (like the existing `setLocation` /
   `push` builders), executed through the injectable `RunFn`; the service→TCC map; advertise the
   supported services (all but `notifications`).
4. **Android backend** — map to `pm grant|revoke`, reusing the `grantPermissions` mechanism
   (`Android.grant_permissions` in `bajutsu/adb.py`, called from
   `bajutsu/platform_lifecycle/environments/android.py`); advertise the supported services
   (including `notifications`).
5. **Apply-before-launch wiring** — invoke the field in the run-loop / lease path before the app
   process starts, once per scenario. No fixed sleep; the effect is synchronous with the command's
   exit.
6. **codegen** — no app-level XCUITest / Espresso equivalent, so codegen emits a labeled `// TODO`
   naming the field, consistent with
   [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md) and BE-0052.
7. **Docs + fixture** — document the field in [`docs/scenarios.md`](../../docs/scenarios.md) and its
   Japanese mirror, the DSL grammar, and add a showcase scenario that pre-grants a permission the
   Permissions tab would otherwise prompt for.
8. **Tests** — schema parse/validate; preflight over a subset-advertising backend (supported service
   passes, unsupported named and failed fast); command builders verified against a fake `RunFn`;
   fail-clean on an unknown service.

### Prime directives preserved

- **Determinism.** A deterministic device mutation with a machine-checkable result — no
  `sleep`-to-settle, no LLM. The run/CI gate stays AI-free, and this path *removes* an LLM call the
  alert guard would otherwise make.
- **App-agnostic.** No per-app code. The service vocabulary and mapping live in the tool/backends;
  the per-scenario choice of which permission to set lives in the scenario, never in app code.
- **Fail fast.** An unsupported service is caught in preflight, not part-way through a run.

## Alternatives considered

- **Config-level only (mirror Android's `grantPermissions`).** Rejected as the primary shape: the
  grant-vs-unset choice differs *between scenarios* of the same app (one tests the request flow, one
  wants it pre-granted), so a per-app config value cannot express it. The existing Android config
  field remains as an app-wide default; this per-scenario field layers on top.
- **Extend `dismissAlerts` to tap "Allow".** `dismissAlerts: { instruction: "tap Allow" }` already
  exists, but it is the AI vision path (non-deterministic, needs a key), reacts only *after* the
  prompt appears, and cannot revoke or establish a known pre-launch state. Rejected — it would keep
  the LLM on the path this item is meant to take it off.
- **Per-app launch env / debug deeplink.** Approximating the state from inside the app pushes the
  burden onto every target app and breaks app-agnosticism — the same reasoning BE-0052 used to reject
  it. Launch env stays available for genuinely app-specific setup.
- **iOS-only primitive.** Considered, but the device-control layer is backend-agnostic by design and
  Android already has the underlying `pm grant` mechanism, so a single cross-platform field is the
  smaller long-term surface than two divergent ones.
- **An imperative `setPermissions` step (mid-flow).** Deferred. TCC/permission state must be set
  before the app's first request, so the pre-launch declarative field is the load-bearing form. A
  mid-flow step (e.g. to revoke and re-exercise a denied path within one scenario) is a possible
  future extension, not part of this item.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Scenario schema + shared service vocabulary (`permissions` field; parse/validate).
- [x] Capability token `deviceControl.permissions` + per-service preflight mapping.
- [x] iOS backend — `simctl privacy` command builder, service→TCC map, advertise supported services.
- [x] Android backend — `pm grant|revoke` mapping reusing `grantPermissions`, advertise services.
- [x] Apply-before-launch wiring in the run-loop / lease path.
- [x] codegen labeled TODO for the field.
- [x] Docs (scenarios.md + ja, DSL grammar) and a showcase fixture.
- [x] Tests — schema, subset-advertising preflight, command builders, fail-clean on unknown service.

Log:

- [#1129](https://github.com/bajutsu-e2e/bajutsu/pull/1129) — Implemented the `permissions`
  scenario field end to end: schema + vocabulary validation, per-service capability tokens
  (`deviceControl.permissions.<service>`) and preflight gating, `simctl privacy` / `pm grant`|`pm
  revoke` command builders (atomic — validated before any device mutation), apply-before-launch
  wiring across all five platform environments (with a runtime `UnsupportedAction` backstop on
  `fake`/`web`), codegen TODOs, bilingual docs, a showcase fixture, and tests. Also wired the
  showcase fixture's `location` scenario into on-device CI on both platforms: `ios-e2e.yml`'s
  `xcuitest (multi-touch)` job now also runs `permission.yaml` with `--exclude ai` on the XCUITest
  backend — `permission.yaml` taps the "Permissions" tab bar item, which idb can never resolve (it
  collapses the tab bar into one opaque group), so it needs the same forced-XCUITest treatment as
  that job's multi-touch scenario; the excluded scenario needs the vision `dismissAlerts` guard on
  iOS, so it stays out of the deterministic gate per prime directive 1. `android-e2e.yml`'s
  `smoke (adb)` job already ran both scenarios in the same file unchanged (adb's tab bar has no
  such limitation).

## References

Closes the `setPermissions` parity gap noted in
[BE-0052 — Device-state primitives](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md).
Builds on the per-operation capability tokens from
[BE-0212 — Split the coarse deviceControl capability](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md)
and the preflight gate from
[BE-0128 — Preflight-gate device-control steps](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md).
The Android side reuses the `grantPermissions` / `pm grant` mechanism from
[BE-0210 — Android actuation fidelity](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md).
Complements the vision alert guard (`bajutsu/alerts.py`, [`dismissAlerts`](../../docs/scenarios.md)).
[DESIGN §6.1](../../DESIGN.md), `bajutsu/orchestrator/actions/handlers/device.py`
