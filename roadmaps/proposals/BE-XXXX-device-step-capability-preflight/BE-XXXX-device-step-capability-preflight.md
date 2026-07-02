**English** · [日本語](BE-XXXX-device-step-capability-preflight-ja.md)

# BE-XXXX — Preflight-gate device-control steps by capability

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-device-step-capability-preflight.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Platform expansion (Android / Web / Flutter) |
<!-- /BE-METADATA -->

## Introduction

Device-control steps (`push`, `clearKeychain`, `setLocation`, and their siblings) carry simctl
semantics into the shared scenario vocabulary and still fail only at runtime on a backend without
a real device environment — exactly the "late failure" problem BE-0082 already fixed for gestures
and visual assertions. This proposal extends the same preflight check to gate device-control steps
by capability, so an unsupported step fails before any device work starts.

## Motivation

`bajutsu/orchestrator/types.py:45-59` defines `DeviceControl`, a Protocol of simctl-backed
operations (`set_location`, `push`, `clear_keychain`, `clear_clipboard`, `set_clipboard`,
`home`, `foreground`, `override_status_bar`, `clear_status_bar`) that the runner injects when a
real device environment backs the run, and leaves `None` when it doesn't (the fake driver, or
parallel runs that don't pin a single device). Every device-control step handler in
`bajutsu/orchestrator/actions/handlers/device.py` (e.g. `_do_set_location`, `_do_push`,
`_do_clear_keychain`) calls `_need_control(control, "<name>")`
(`bajutsu/orchestrator/actions/_registry.py:72-78`), which raises `base.UnsupportedAction` — but
only at the moment that step actually executes.

This is precisely the failure mode BE-0082 (the capability preflight check) was built to eliminate
for gestures and visual assertions: "a scenario whose last step needed an unsupported capability ran
every earlier step on a device first, then failed late" (`bajutsu/capability_preflight.py:1-9`).
BE-0082's `unsupported()` gates `pinch`/`rotate` on `MULTI_TOUCH` and a `visual` assertion on
`SCREENSHOT`, both declared on `Driver.CAPABILITIES`, but device-control steps have no capability
token at all — they are gated by whether `DeviceControl` happens to be wired up at runtime, which
the preflight (a pure function of `scenario` and the driver's `capabilities()`,
`bajutsu/capability_preflight.py:109-122`) cannot see.

The gap is latent today because idb always provides a real `DeviceControl` and Playwright-backed
scenarios rarely use device-control steps, but it becomes a forcing function the moment Android
(BE-0007) or a wider Web surface lands: a scenario authored against one target that uses `push` or
`setLocation` will run every prior step on the new backend and only then discover, mid-run, that the
construct has no equivalent there. Severity: Medium — not an active failure on the two shipped
backends, but a predictable one that compounds with each new backend and directly regresses prime
directive 2 ("fail loudly", the very concern BE-0082 was written to close).

## Detailed design

1. **Add capability tokens for device-control steps.** Extend `bajutsu/drivers/base.py`'s
   `Capability` class with a token for simctl-style device control (e.g.
   `DEVICE_CONTROL = "deviceControl"`), following the existing pattern (`MULTI_TOUCH`,
   `WEBVIEW`). A single shared token is enough if every device-control step is an all-or-nothing
   capability per backend (idb has all of them via `DeviceControl`; a backend either supports the
   family or doesn't) — the existing `DeviceControl` Protocol already groups them as one unit for
   this reason. If a future backend supports only a subset, split into per-operation tokens instead;
   that decision can be deferred to whichever backend first needs partial support.
2. **Declare the capability on backends that support it.** Add the new token to idb's `CAPABILITIES`
   frozenset (`bajutsu/drivers/idb.py:326-328`) since idb backs a real `DeviceControl`; Playwright's
   `CAPABILITIES` (`bajutsu/drivers/playwright.py:566-576`) does not gain it, matching today's
   reality that Playwright scenarios have no `DeviceControl` wired in.
3. **Extend `capability_preflight.py`'s requirement table.** Add a `_Requirement` entry (or one per
   device-control step kind, depending on the token granularity chosen in step 1) to `_REQUIREMENTS`
   in `bajutsu/capability_preflight.py`, with a `locations` function that walks the step tree
   (reusing `_walk_steps`) for steps where `step.set_location`, `step.push`, `step.clear_keychain`,
   etc. are not `None`. This makes `unsupported()` report every device-control step location that
   needs a capability the target backend doesn't declare — before any device work starts, exactly
   like the existing `pinch`/`rotate`/`visual` entries.
4. **Keep the runtime `_need_control` check as a safety net, not the primary gate.** The preflight
   check becomes the mechanism that fails a scenario deterministically and early; the existing
   `UnsupportedAction` from `_need_control` remains as a defense-in-depth check for the case where
   capabilities say a backend supports device control but the specific run's environment (fake
   driver, or a parallel run with no pinned device) doesn't wire one up — a capability, not
   environment, concern the preflight is not meant to resolve.

This directly serves prime directive 2 (fail loudly, before device work) and prime directive 3
(app-agnostic): the fix lives entirely in the capability/preflight abstraction, not in any
per-target config, and it is a forcing function for Android — the same table gates whichever
device-control operations the Android backend does or doesn't implement, with zero runner changes.

## Alternatives considered

- **Leave the gap and rely on `_need_control`'s runtime failure.** Cheapest, but reproduces the
  exact "ran every earlier step, then failed late" problem BE-0082 already eliminated for gestures —
  inconsistent coverage of the same failure class is worse than no preflight at all, since it teaches
  users the preflight is unreliable.
- **Fold device-control capability into the existing `MULTI_TOUCH`/`SCREENSHOT`-style ad hoc
  requirements without a dedicated token.** Rejected — device control is a materially different
  concern (simctl-backed OS operations, not gesture/rendering capabilities of the actuator itself);
  reusing an unrelated token would misname the requirement and make `unsupported()`'s error messages
  misleading.
- **Give every device-control operation its own fine-grained capability token from the start.**
  More precise, but speculative before any backend needs partial support — idb supports the whole
  `DeviceControl` surface as one unit today, so a single shared token matches the current backend
  reality; splitting later (when a real backend needs it) is a small, additive change to the
  requirement table, not a breaking one.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add a device-control capability token to `Capability` in `bajutsu/drivers/base.py`
- [ ] Declare the token on backends that provide a real `DeviceControl` (idb)
- [ ] Extend `capability_preflight.py`'s `_REQUIREMENTS` to gate device-control steps
- [ ] Keep `_need_control`'s runtime check as a defense-in-depth fallback, not the primary gate

No PR has landed yet.

## References

- `bajutsu/orchestrator/types.py:45-59` — the `DeviceControl` Protocol grouping simctl-backed
  operations
- `bajutsu/orchestrator/actions/_registry.py:72-78` — `_need_control`, today's only (runtime) gate
- `bajutsu/orchestrator/actions/handlers/device.py` — the device-control step handlers
- `bajutsu/capability_preflight.py:1-9,109-122` — BE-0082's preflight check and its `unsupported()`
  entry point, which this proposal extends
- `bajutsu/drivers/base.py` — `Capability`, where the new token is added
- `bajutsu/drivers/idb.py:326-328`, `bajutsu/drivers/playwright.py:566-576` — the backends'
  `CAPABILITIES` declarations
- Related: BE-0082 (capability preflight check), BE-0035 (device control primitives), BE-0007
  (Android backend)
- Originates from the 2026-07-02 codebase-analysis report (design).
