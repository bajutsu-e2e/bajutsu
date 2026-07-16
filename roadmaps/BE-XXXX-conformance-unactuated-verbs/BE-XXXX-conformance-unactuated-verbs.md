**English** · [日本語](BE-XXXX-conformance-unactuated-verbs-ja.md)

# BE-XXXX — Extend the driver conformance contract to unactuated Driver operations

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-conformance-unactuated-verbs.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Driver & backend architecture |
| Related | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance.md), [BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps.md), [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md) |
<!-- /BE-METADATA -->

## Introduction

The driver conformance contract ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md))
runs one backend-agnostic spec against every backend — `FakeDriver` on the Linux gate, and idb /
XCUITest / adb / Playwright on-device ([BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance.md)
added the adb lane). The contract today pins tap, label-and-trait resolution, the multi-touch and
`selectOption` capability promises, and the condition-wait semantics. Several `Driver` Protocol
operations sit outside it, actuated by no real backend in any lane: the text-editing family
(`delete_text`, `select_all`, `copy_selection`) and `tap_point`. This item extends the contract to
cover those operations, so the one spec proves them on every backend at once.

## Motivation

The text-editing steps ([BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps.md))
shipped, and each backend's command construction is unit-tested with a mocked subprocess. What no
lane does is actuate `delete_text` / `select_all` / `copy_selection` against a real device or
browser: the round-trip from a step to an observed field change is never exercised. `tap_point` is
in the same position, with a real command test only on XCUITest and Playwright and no lane that actuates it —
and it is the foundation of the alert-dismissal path (the vision-located coordinate tap that
[BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md) relies on), so leaving it
unobserved on-device is a real risk.

The contract is the cheapest place to close this gap: one spec runs against five backends, so a
single test body adds coverage everywhere rather than one showcase scenario per backend. The
capability model already gives the contract its shape — a backend that declares the capability
must actuate, and one that does not must raise `UnsupportedAction` loudly rather than silently
no-op. The text-editing operations and `tap_point` extend that same pattern.

One constraint shapes the work. The on-device conformance harnesses realize a requested screen as
a list of identifier-bearing buttons, which is enough for tap and resolution tests but not for
text editing: exercising `delete_text` or `select_all` needs a real editable text field on the
screen. So the contract addition comes with a small extension to each platform's conformance
screen (the iOS `ConformanceView`, the Compose `ConformanceScreen`, and the web harness's rendered
document) to present an editable field and a known-frame element.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Enumerate the operation contract.** State the invariants the new operations must satisfy, grounded in the
  `Driver` Protocol. Text editing: a backend that actuates the operations completes `type_text` then
  `select_all` then `copy_selection` without `UnsupportedAction`, and `delete_text` reduces the
  field's reported length; one that does not — idb, which raises `select_all` / `copy_selection`
  unconditionally today — raises `UnsupportedAction`. Unlike `MULTI_TOUCH` and `SELECT_OPTION`,
  there is no `Capability` token for text-editing select/copy yet, so a first sub-decision is
  whether the contract asserts the actuate-versus-raise behavior directly or a new capability is
  introduced (and added to each backend's `CAPABILITIES`) to gate it the same way; today adb,
  Playwright, and XCUITest actuate select/copy while idb raises. `tap_point`: a coordinate tap on
  an element of known frame has the same observable effect as a semantic tap on it.
- **Extend the three conformance screens.** Add an editable text field and a known-frame element to
  the iOS `ConformanceView`, the Compose `ConformanceScreen`, and the web harness document, keeping
  the readiness marker contract intact.
- **Add the contract test bodies.** Write the new invariants once in `tests/driver_conformance.py`,
  so pytest collects them against every backend subclass.
- **Wire realization into the on-device harnesses.** Extend each harness's screen-realization
  channel (the iOS spec-file write, the Android intent reseed, the web `set_content`) so the field
  and the known-frame element are present before the contract body runs.
- **Confirm capability declarations match behavior.** Check that each backend's declared
  capabilities agree with which operations it actuates versus refuses, so the contract's
  promise-versus-behavior check stays honest.

## Alternatives considered

- **Per-backend actuation scenarios in the showcase instead of the contract.** Authoring a
  text-editing / `tap_point` scenario per backend would duplicate the same intent four times and
  invite per-backend drift — exactly what the conformance suite exists to prevent. The contract is
  the point: one spec, every backend.
- **Leave the text-editing operations at command-construction unit tests only.** The mocked-subprocess
  tests prove the argv / HTTP / key-combination a backend builds, but never that the real device
  performs the edit. A capability the tool advertises but no lane observes is a promise without a
  check.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Enumerate the operation contract (text-editing round-trip, `tap_point`, actuate-vs-raise per backend, and whether to add a text-editing capability).
- [ ] Extend the iOS / Compose / web conformance screens with an editable field and a known-frame element.
- [ ] Add the new contract test bodies to `tests/driver_conformance.py`.
- [ ] Wire screen realization into the on-device harnesses.
- [ ] Confirm capability declarations match actuate-versus-raise behavior per backend.

## References

- [BE-0114 — Driver conformance suite for backend-agnostic behavior](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)
- [BE-0270 — Driver conformance for the adb backend on-device](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance.md)
- [BE-0265 — Text-editing steps: select, clear, delete, copy](../BE-0265-text-editing-steps/BE-0265-text-editing-steps.md)
- [BE-0269 — Speed up the system-alert guard's intervention during wait steps](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md)
- `tests/driver_conformance.py`, `bajutsu/drivers/fake.py`
