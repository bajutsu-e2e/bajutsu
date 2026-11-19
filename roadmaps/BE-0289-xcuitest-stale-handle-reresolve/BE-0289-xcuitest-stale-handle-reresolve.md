**English** · [日本語](BE-0289-xcuitest-stale-handle-reresolve-ja.md)

# BE-0289 — Make the XCUITest channel re-resolve a stale actuation handle before failing

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0289](BE-0289-xcuitest-stale-handle-reresolve.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0289") |
| Implementing PR | [#1266](https://github.com/bajutsu-e2e/bajutsu/pull/1266) |
| Topic | Platform support |
| Related | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) |
<!-- /BE-METADATA -->

## Introduction

The `xcuitest (multi-touch)` on-device end-to-end (E2E) job flakes on the first tap of a scenario. The tap fails with `element vanished (stale handle)` even though the target element is present on the screen. The scenario and the app under test are not at fault.

The XCUITest backend resolves an element and actuates it in two separate round-trips to the on-device runner. The accessibility tree of the app may be re-snapshotted between the two round-trips, as it routinely is while a freshly foregrounded screen settles. The per-snapshot handle that the first round-trip minted is then stale by the time the second round-trip uses it.

This item proposes to close that resolve-then-actuate gap without weakening the deterministic verdict:

- On a `stale` reply, re-query the runner.
- Re-actuate only when the same selector still resolves to a single element.
- Fail loudly, exactly as today, the moment the selector does not resolve to a single element.

The change stays inside the Python channel of the xcuitest backend. It touches only the runner, the retry seam, and the deterministic verdict.

## Motivation

The flake reproduces on the required `E2E (iOS)` gate. In run 29630685037, the second scenario of the `xcuitest (multi-touch)` job ([`permission.yaml`](../../demos/showcase/scenarios/permission.yaml)) failed on its very first step:

```
step 0 (tap): element vanished (stale handle): {'label': 'Permissions', 'traits': ['button']}
FAIL  runs/20260718-044659/manifest.json
```

The step taps the "Permissions" tab-bar button, a fixed element of the launch screen of the app. The button does not vanish. The handle went stale, not the element. The cause is a two-phase address.

The XCUITest backend splits one tap into two round-trips against the element snapshot of the runner:

1. `_resolve_handle` sends `GET /elements`. It resolves the selector to a unique element on the Python side. It reads back the opaque per-snapshot handle that the runner minted for that element.
2. `tap` then sends `POST /tap {handle}`.

The runner keys each handle to one snapshot version. Suppose the accessibility tree is re-snapshotted between the query and the tap. The earlier handle then no longer maps to a live element. The runner returns `status: "stale"`, and the driver raises the vanished-element error. A freshly foregrounded screen re-snapshots repeatedly while it settles, so the race lands on the first tap of a scenario.

The log lines before the failed step place the cause in launch-time settling, not in a runner defect. For roughly sixty seconds before the failed step, the driver logged `runner channel GET /health failed ... [Errno 61] Connection refused`. During that time, `await_ready` polled the loopback server of the runner. This poll is a bounded condition wait, not a fixed sleep. The tap fired only once the runner answered.

The runner was therefore alive and answering when it reported `stale`. The tap never reached any element, because the runner returns `stale` before it actuates. Nothing on the screen had disappeared.

The flake is expensive in three ways:

- A required aggregator check (`E2E (iOS)`) that fails at random erodes trust in the gate. A maintainer cannot tell the flake apart from a real regression, so the flake invites either a reflexive re-run or a wasted investigation.
- Every re-run burns metered macOS-runner minutes. The job builds the app, boots a Simulator, and brings up the runner before it even reaches the first tap.
- The `element vanished` message actively misleads. It reads as though the "Permissions" element is missing. The real event is a snapshot-version race on a button that is present the whole time. An investigator hunts a disappearance that never happened.

No existing mechanism covers the stale race, and none was meant to:

- The transient-retry seam ([BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md)) absorbs a transport blip within one channel call. A `stale` reply is a decoded outcome, not a transport failure. The seam deliberately never retries it, because retrying an outcome is the flakiness-by-absorption that [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) rejects.
- The readiness and timeout work ([BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)) stopped the runner from being declared ready on the wrong screen. It also gave a slow write a longer bounded window. Neither change touched the resolve-then-actuate gap.
- The sibling flake ([BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)) is a different failure class. There the runner crashes and stops answering. Here the runner is alive and correctly answers `stale`.

Closing the stale race needs its own mechanism.

## Detailed design

The work splits into four units:

- Unit 1 is the driver change.
- Unit 2 keeps the change honest under the determinism directives.
- Unit 3 proves the change off-device.
- Unit 4 keeps the documentation in step.

The units are ordered by dependency, but they can land as separate pull requests.

**Unit 1 — A stale-gated re-resolution retry in the driver channel.** The handle-based actuation path (`_actuate`, fed by the `_resolve_handle` seam) lives in [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py). In that path, treat a `stale` reply as a trigger to re-query, not as an immediate vanished-element error. Re-run `_resolve_handle(sel)`:

- The selector may still resolve to a single element. The driver then re-mints the handle and re-issues the actuation, up to a small bounded number of attempts.
- The selector may resolve to zero or to many elements, which raises `ElementNotFound` or `AmbiguousSelector`. The driver then fails with that outcome immediately and spends no further attempts.

The re-resolution is the condition that separates two cases. In a snapshot-version race, the element is still there. In a genuine disappearance, it is not. The gate therefore never taps whatever happens to match.

Every handle-based actuation (`tap`, `double_tap`, `long_press`, `pinch`, and `rotate`) routes through the seam and inherits the retry. The raw-coordinate `tap_point` carries no handle to go stale, so it is untouched.

The attempt bound is a *dedicated* stale-retry constant. It is held separate from the transport-retry `_MAX_ATTEMPTS` and `_BACKOFF_BASE_SECONDS` of BE-0207, even though it starts at the same values: three attempts, with a 0.5s then 1.0s exponential backoff. The two loops bound different things. One bounds a screen settling between `_resolve_handle` and `_actuate`. The other bounds a transport blip inside `_with_retry`. A later re-tune of one loop, such as loosening the transport budget for a slower CI runner, must not silently move the other. The whole loop stays bounded at a few seconds, not sub-second. A genuinely gone element is not retried for long.

On the retry path alone, the re-resolution relaxes the backend's "act on exactly the element resolved" guarantee. The proposal states this trade openly. A re-resolved unique match proves that *an* element matches the selector. It does not prove that the match is the *same* element the first round-trip found. A screen that changed between the two round-trips, such as a dismissed modal, could make a different element the sole match for the selector. The retry would then act on that different element.

The trade is acceptable, and narrower than it first looks:

- Resolution stays on the Python side. The purpose of the invariant is to keep resolution authority out of the runner, where an ambiguous predicate could silently pick one of several matches. That purpose is untouched.
- The retry acts only on a *unique* Python-side match, never on one of several.
- Re-resolving at the moment of acting is exactly what idb does, because idb resolves and taps in one step. The retry brings the stale handling of XCUITest into line with the other backends. It does not invent a looser rule.

A stricter variant stays open to `implement-be` should the selector-only match prove too permissive. The variant re-actuates only when the re-resolved element also matches the identity attributes of the original element, not the selector alone.

**Unit 2 — Preserve determinism, and never double-actuate.** A re-issued tap after a `stale` reply is safe in a way that a re-issued tap after a *timeout* is not. BE-0218 turned on the same distinction. The runner returns `stale` before it touches the element: `store.lookup(handle:)` reports `.stale`, and the handler returns without actuating, in [`BajutsuKit/Sources/BajutsuRunner/Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift). A `stale` reply is therefore definitive evidence that no actuation occurred, and re-sending cannot apply the gesture twice. A timeout, by contrast, leaves delivery unknown. That is why BE-0218 refused to retry a write past its deadline.

Determinism is otherwise untouched:

- No large language model (LLM) enters the path.
- No fixed `sleep` is added. The re-query round-trip is the condition. The between-attempt backoff is the dedicated stale-retry backoff of Unit 1, not a settle delay.
- A genuine `stale` disappearance, an ambiguous re-resolution, and an exhausted attempt budget all stay loud failures, as they are today.

**Unit 3 — Prove the change off-device.** Cover the seam with unit tests against a scripted transport, on the fast gate with no Simulator. Four cases cover the behavior:

- A `stale` reply followed once by `ok`, with the selector still resolving uniquely, recovers.
- A persistent `stale`, with the selector still resolving, exhausts the bound and fails loudly.
- A `stale` whose re-query no longer resolves the selector fails immediately as `ElementNotFound`, spending no extra attempts.
- A `stale` whose re-query resolves ambiguously fails as `AmbiguousSelector` and never actuates.

The four cases pin both halves of the gate: a snapshot race recovers, while a real disappearance or an ambiguity still fails. A later change then cannot quietly turn the honest gate into an absorbing one.

**Unit 4 — Keep the documentation in step (BE-0113).** The module docstring in [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) today states that a stale handle "surfaces as the same vanished-element error idb would raise". Update the docstring to describe the re-resolution retry and the exact condition under which a stale handle still fails. Update [`DESIGN.md`](../../DESIGN.md) and [`docs/architecture.md`](../../docs/architecture.md) if either describes the stale semantics, so that the prose account matches the behavior.

## Alternatives considered

**Retry a stale actuation unconditionally, without the re-resolution gate.** Dropping the gate would absorb a genuine disappearance into a silent retry. An example is an element that the navigation of a prior step removed. This is the flakiness-by-absorption that BE-0049 forbids. The re-resolution gate keeps the retry honest, because it re-actuates only when the element demonstrably still exists.

**Add a fixed settle delay after launch, before the first tap.** A fixed `sleep` that ignores the condition violates prime directive 2 outright. The delay would also only narrow the race window, not close it. A settle slower than the fixed budget stales the handle anyway. The re-resolution retry closes the race regardless of how long the screen takes to settle.

**Move resolution into the runner, collapsing the two round-trips.** Letting the runner resolve the selector at actuation time would remove the snapshot gap. But resolution on the Python side is the core invariant of the backend. Resolution authority stays in Python, never in a runner-side predicate that could match one of several elements. The stale retry re-resolves on the Python side, and only on a unique match (the trade that *Detailed design* states in full). The retry therefore keeps the authority in Python. Moving resolution into the runner would surrender the authority. It would trade the stale race for the ambiguity that the deterministic core exists to prevent.

**Quarantine or job-retry the check in CI.** Marking `xcuitest (multi-touch)` allowed-to-fail, or wrapping the job in a retry, would turn the check green without touching the race. BE-0287 rejects the same option for the sibling flake, for the same reasons: it hides a real race behind a green check, and it keeps burning macOS minutes on every silent retry.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — A stale-gated re-resolution retry in `_actuate` / the `_resolve_handle` seam, over
      every handle-based actuation (`tap`, `double_tap`, `long_press`, `pinch`, `rotate`); the
      raw-coordinate `tap_point` untouched.
- [x] Unit 2 — Determinism preserved: re-issuing a `stale` write cannot double-actuate (the runner
      returns `stale` before it actuates), no LLM, no fixed sleep, and genuine `stale` / ambiguity /
      exhaustion stay loud failures.
- [x] Unit 3 — Off-device unit tests for the four cases (recover once, exhaust, immediate
      `ElementNotFound`, immediate `AmbiguousSelector`).
- [x] Unit 4 — Documentation aligned (BE-0113): the `xcuitest.py` docstring, and `DESIGN.md` /
      `docs/architecture.md` where either describes the stale semantics.

## References

- [BE-0207 — Make the XCUITest runner channel robust to transient timeouts](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md):
  the transient-blip retry seam whose bounded-backoff pattern this item mirrors with its own dedicated
  constant, and the outcome-versus-transport split that keeps a decoded `stale` off the transport retry.
- [BE-0218 — Stabilize the E2E Simulator gate](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md):
  the readiness and per-method-timeout work on the same gate, and the delivered-write distinction this
  item's no-double-actuation argument turns on.
- [BE-0287 — XCUITest runner-channel resilience under multi-touch actuation](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md):
  the sibling flake on the same job — a mid-run runner crash — which this item's stale-race twin
  complements rather than duplicates.
- [BE-0049 — Determinism and flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md):
  the determinism stance the re-resolution gate stays consistent with (tolerate a snapshot race, never
  absorb a real outcome).
- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md):
  the backend whose resolve-Python-side / actuate-by-handle addressing produces the two-round-trip gap.
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py): `_resolve_handle`, `_actuate`,
  `_with_retry`, and `await_ready` — the resolve/actuate seam this item hardens.
- [`BajutsuKit/Sources/BajutsuRunner/Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift):
  `handleTap` and `handleGesture`, both of which return `stale` before actuating — the anchor for the
  no-double-actuation argument, covering `pinch` and `rotate` too.
- [`demos/showcase/scenarios/permission.yaml`](../../demos/showcase/scenarios/permission.yaml):
  the scenario whose first tap exposed the flake.
- [`.github/workflows/ios-e2e.yml`](../../.github/workflows/ios-e2e.yml): the `E2E (iOS)` gate this
  item stabilizes.
