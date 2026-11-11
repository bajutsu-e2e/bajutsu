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

This item adds a [scenario](../../docs/glossary.md#scenario-authoring)-level, declarative way to set OS permission state **before the app
launches**. A scenario grants or revokes a permission up front, so the runtime prompt never appears.
On iOS the field drives `simctl privacy`, and on Android it drives `pm grant` / `pm revoke`. The
field is a deterministic, AI-free complement to the existing vision **alert guard**
([`dismissAlerts`](../../docs/scenarios.md)). When the scenario pre-sets a permission it knows about,
no prompt is left to dismiss.

## Motivation

Permission prompts (location, camera, contacts, and so on) are SpringBoard or system UI. They live
out of process, so idb's app-scoped query can neither see nor tap them. Today iOS handles them
**solely** through the vision alert guard. On a blocked step, `run` screenshots the screen, asks
Claude where to tap, and clears the prompt ([`bajutsu/alerts.py`](../../bajutsu/alerts.py)). That is
the *one* AI path left in `run`, and it is the right tool only when the prompt is unexpected. When a
scenario knows the permission ahead of time, pre-setting its state is strictly better, for three
reasons:

- **It removes an LLM call from the deterministic path.** Pre-granting is a plain `simctl` / `adb`
  side effect with a machine-checkable result, with no screenshot, no model, and no
  `ANTHROPIC_API_KEY`. The field *reduces* reliance on the alert guard and adds no second AI surface
  (prime directive 1).
- **It makes the state, not just the tap, deterministic.** The alert guard can only react to a prompt
  that appears. It cannot *revoke* a permission to exercise a denied-path flow, and it cannot
  guarantee that the app starts from a known permission state. Pre-setting can do both.
- **It is per-scenario.** One scenario tests the request and grant flow itself, with the permission
  left unset so the alert guard or a real tap grants it. A sibling scenario wants the permission
  already granted. That scenario then reaches the feature under test directly. That distinction is a property of
  the scenario, so it belongs in the scenario file.

**Competitive context (Maestro).** Maestro ships `setPermissions` out of the box.
[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)
already flagged the missing field as a table-stakes parity gap while shipping the rest of the
device-state family. This item closes the gap, and the differentiator is *how*: a single
cross-platform declarative field that each [backend](../../docs/glossary.md#driver-backend-actuator-platform) maps to its native mechanism, staying
deterministic and app-agnostic.

**Cross-platform footing.** Android already pre-grants runtime permissions through a **config-level**
`grantPermissions` list applied at lease time
([BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md); call site
`bajutsu/platform_lifecycle/environments/android.py`, backed by `Android.grant_permissions` in
`bajutsu/adb.py`). That list is per-app, not per-scenario, and Android-only. This item introduces
one **per-scenario** surface that unifies both platforms, so the same field works whether the target
is the iOS Simulator or the Android emulator.

## Detailed design

The design adds a new **scenario-level field**. The field is declarative and applied once before
launch, the same shape as `dismissAlerts` and not a mid-flow step. The proposed surface follows, and
the final names settle on adoption:

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

Each entry is `<service>: grant | revoke`. The runner applies the field **before the app process
starts**, so the permission state is in place before the app's first request. That is the only point
at which pre-granting can prevent the prompt.

### Shared vocabulary and the backend-native mapping

`<service>` comes from a small **backend-agnostic vocabulary** (for example `location`, `camera`,
`microphone`, `contacts`, `photos`, `calendar`, `notifications`). Each backend maps a service to its
native identifier and declares which services it can honor. An unmapped service **fails preflight
cleanly** (see below) and never fails at runtime.

- **iOS** maps to `simctl privacy <udid> <grant|revoke> <tcc-service> <bundle>`, for example
  `location → location`, `camera → camera`, and `contacts → contacts`.
- **Android** maps to `pm grant|revoke <package> <android.permission.*>`, reusing the plumbing behind
  the existing config-level `grantPermissions`.

### Notifications and the limit of coverage

`simctl privacy` has **no notifications service**. iOS notification authorization is not part of
Transparency, Consent, and Control (TCC), the database that backs iOS's privacy permissions. The iOS
backend declares `notifications` **unsupported** for that reason. A scenario that lists `notifications`
fails preflight with a clear message that points to `dismissAlerts` as the path for the notification
prompt. Android's `POST_NOTIFICATIONS` *is* a runtime permission (`pm grant`, API 33+), so Android
supports the service. The shared vocabulary states this asymmetry openly and does not pretend to a
uniform surface it cannot deliver.

### Work breakdown (MECE)

The eight units are mutually exclusive and collectively exhaustive (MECE).

1. **Scenario schema + vocabulary.** Add the `permissions` field (a map of `service → grant|revoke`),
   parse and validate it, and define the shared service enum. Reject an unknown service or action at
   parse time.
2. **Capability token + preflight.** Add a `deviceControl.permissions` token following
   [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md).
   Map the field to the token in `bajutsu/capability_preflight.py`, and gate each requested
   *service*, so the aggregated preflight message names an unsupported service individually (for
   example `notifications` on iOS;
   [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md)).
3. **iOS backend.** Write a pure command builder for `simctl privacy`, like the existing
   `setLocation` and `push` builders, executed through the injectable `RunFn`. Add the service→TCC
   map, and advertise the supported services (all but `notifications`).
4. **Android backend.** Map to `pm grant|revoke`, reusing the `grantPermissions` mechanism
   (`Android.grant_permissions` in `bajutsu/adb.py`, called from
   `bajutsu/platform_lifecycle/environments/android.py`). Advertise the supported services,
   including `notifications`.
5. **Apply-before-launch wiring.** Invoke the field in the run-loop and lease path before the app
   process starts, once per scenario. No fixed sleep is needed, because the effect is synchronous
   with the command's exit.
6. **codegen.** XCUITest and Espresso have no app-level equivalent, so codegen emits a labeled
   `// TODO` naming the field, consistent with
   [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md) and BE-0052.
7. **Docs + fixture.** Document the field in [`docs/scenarios.md`](../../docs/scenarios.md), its
   Japanese mirror, and the DSL grammar. Add a showcase scenario. It pre-grants a permission the
   Permissions tab would otherwise prompt for.
8. **Tests.** Cover schema parse and validate; preflight over a backend that advertises a subset
   (a supported service passes, an unsupported one is named and fails fast); command builders
   verified against a fake `RunFn`; and an unknown service that fails cleanly.

### Prime directives preserved

- **Determinism.** The field performs a deterministic device mutation with a machine-checkable
  result, with no `sleep`-to-settle and no LLM. The run/CI gate stays AI-free, and the path
  *removes* an LLM call that the alert guard would otherwise make.
- **App-agnostic.** No per-app code is involved. The service vocabulary and mapping live in the
  tool and backends. The per-scenario choice of which permission to set lives in the scenario, never
  in app code.
- **Fail fast.** Preflight catches an unsupported service, so no unsupported service is first
  discovered part-way through a run.

## Alternatives considered

- **Config-level only (mirror Android's `grantPermissions`).** Rejected as the primary shape. The
  grant-or-unset choice differs *between scenarios* of the same app (one tests the request flow, one
  wants the permission pre-granted), so a per-app config value cannot express it. The existing
  Android config field remains as an app-wide default, and the per-scenario field layers on top.
- **Extend `dismissAlerts` to tap "Allow".** Rejected. `dismissAlerts: { instruction: "tap Allow" }`
  already exists, but it is the AI vision path: non-deterministic, needing a key, reacting only
  *after* the prompt appears, and unable to revoke or establish a known pre-launch state. Extending
  it would keep the LLM on the path this item is meant to take it off.
- **Per-app launch env or debug deeplink.** Rejected. Approximating the state from inside the app
  pushes the burden onto every target app and breaks app-agnosticism, the same reasoning BE-0052 used
  to reject it. Launch env stays available for genuinely app-specific setup.
- **iOS-only primitive.** Considered, but the device-control layer is backend-agnostic by design, and
  Android already has the underlying `pm grant` mechanism. A single cross-platform field is a
  smaller long-term surface than two divergent ones.
- **An imperative `setPermissions` step (mid-flow).** Deferred. Permission state (TCC on iOS) must
  be set before the app's first request, so the pre-launch declarative field is the load-bearing
  form. A mid-flow step, for example to revoke and re-exercise a denied path within one scenario, is
  a possible future extension and not part of this item.

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
