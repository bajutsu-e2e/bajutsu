**English** · [日本語](BE-0289-xcuitest-stale-handle-reresolve-ja.md)

# BE-0289 — Make the XCUITest channel re-resolve a stale actuation handle before failing

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0289](BE-0289-xcuitest-stale-handle-reresolve.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0289") |
| Implementing PR | _(pending)_ |
| Topic | Platform support |
| Related | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) |
<!-- /BE-METADATA -->

## Introduction

The `xcuitest (multi-touch)` on-device end-to-end (E2E) job flakes on the first tap of a scenario,
failing with `element vanished (stale handle)` even though the target element is present on the
screen. The failure is not a defect in the scenario or in the app under test. The XCUITest backend
resolves an element and actuates it in two separate round-trips to the on-device runner, and when the
app's accessibility tree is re-snapshotted between the two round-trips — as it routinely is while a
freshly foregrounded screen settles — the per-snapshot handle the first round-trip minted is stale by
the time the second round-trip uses it. This item proposes to close that resolve-then-actuate gap
without weakening the deterministic verdict: on a `stale` reply, re-query the runner and re-actuate
only when the same selector still resolves to a single element, and fail loudly — exactly as today —
the moment the selector does not. The change stays inside the xcuitest backend's Python channel; the
runner, the retry seam, and the deterministic verdict are the only surfaces the change touches.

## Motivation

The flake reproduces on the required `E2E (iOS)` gate. In run 29630685037, the `xcuitest
(multi-touch)` job's second scenario ([`permission.yaml`](../../demos/showcase/scenarios/permission.yaml))
failed on its very first step:

```
step 0 (tap): element vanished (stale handle): {'label': 'Permissions', 'traits': ['button']}
FAIL  runs/20260718-044659/manifest.json
```

The "Permissions" tab-bar button the step taps is a fixed element of the app's launch screen, so the
button does not vanish. What went stale is the handle, not the element. The mechanism is a two-phase
address.

The XCUITest backend splits one tap into two round-trips against the runner's element snapshot.
`_resolve_handle` first sends `GET /elements`, resolves the selector to a unique element on the
Python side, and reads back the opaque per-snapshot handle the runner minted for that element. `tap`
then sends `POST /tap {handle}`. The runner keys each handle to one snapshot version, so when the
accessibility tree is re-snapshotted between the query and the tap, the earlier handle no longer maps
to a live element: the runner returns `status: "stale"`, and the driver raises the vanished-element
error. A freshly foregrounded screen re-snapshots repeatedly as the screen settles, so the first tap
of a scenario is where the race lands.

The log lines before the failed step place the cause in launch-time settling rather than in a runner
defect. For roughly sixty seconds before the failed step, the driver logged `runner channel GET
/health failed ... [Errno 61] Connection refused` while `await_ready` polled the runner's loopback
server — a bounded condition wait, not a fixed sleep — and the tap fired only once the runner
answered. The runner was therefore alive and answering when the runner reported `stale`. The tap was
never delivered to any element, because the runner returns `stale` before the runner actuates, and
nothing on the screen had actually disappeared.

The flake is expensive in three ways. A required aggregator check (`E2E (iOS)`) that fails at random
erodes trust in the gate, and, because a maintainer cannot tell the flake apart from a real
regression, invites either a reflexive re-run or a wasted investigation. Every re-run burns metered
macOS-runner minutes on a job that builds the app, boots a Simulator, and brings up the runner before
the job even reaches the first tap. Worst of all, the `element vanished` message actively misleads:
the message reads as though the "Permissions" element is missing, when the real event is a
snapshot-version race on a button that is present the whole time — so an investigator hunts a
disappearance that never happened.

No existing mechanism covers the stale-race, and none was meant to. The transient-retry seam
([BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md))
absorbs a transport blip within one channel call, but a `stale` reply is a decoded outcome rather
than a transport failure, so the seam deliberately never retries the reply — retrying an outcome is
the flakiness-by-absorption
[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) rejects. The
readiness and timeout work
([BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md))
stopped the runner being declared ready on the wrong screen and gave a slow write a longer bounded
window, but neither change touched the resolve-then-actuate gap. The sibling flake
([BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md))
is a different failure class: there the runner crashes and stops answering, whereas here the runner is
alive and correctly answers `stale`. Closing the stale-race needs its own mechanism.

## Detailed design

The work splits into four units. Unit 1 is the driver change; Unit 2 keeps the change honest under
the determinism directives; Unit 3 proves the change off-device; Unit 4 keeps the documentation in
step. The units are ordered by dependency but can land as separate pull requests.

**Unit 1 — A stale-gated re-resolution retry in the driver channel.** In the handle-based actuation
path (`_actuate`, fed by the `_resolve_handle` seam) of
[`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py), treat a `stale` reply as a trigger
to re-query rather than as an immediate vanished-element error. Re-run `_resolve_handle(sel)`: when
the selector still resolves to a single element, re-mint the handle and re-issue the actuation, up to
a small bounded number of attempts; when the selector resolves to zero or to many elements — raising
`ElementNotFound` or `AmbiguousSelector` — fail with that outcome immediately, spending no further
attempts. The re-resolution is the condition that separates a snapshot-version race, in which the
element is still there, from a genuine disappearance, in which the element is not, so the gate never
taps whatever happens to match. Every handle-based actuation (`tap`, `double_tap`, `long_press`,
`pinch`, and `rotate`) routes through the seam and so inherits the retry; the raw-coordinate
`tap_point` carries no handle to go stale and is untouched. The attempt bound is a *dedicated*
stale-retry constant, held separate from BE-0207's transport-retry `_MAX_ATTEMPTS` /
`_BACKOFF_BASE_SECONDS` even though it starts at the same values (three attempts, a 0.5s then 1.0s
exponential backoff): the two loops bound different things — a screen settling between
`_resolve_handle` and `_actuate` versus a transport blip inside `_with_retry` — so a later re-tune of
one, such as loosening the transport budget for a slower CI runner, must not silently move the other.
The whole loop stays bounded at a few seconds — not sub-second — and a genuinely gone element is not
retried for long.

The re-resolution relaxes, on the retry path alone, the backend's "act on exactly the element
resolved" guarantee, and the proposal states the trade rather than hiding the trade. A re-resolved
unique match proves that *an* element matches the selector, not that the match is the *same* element
the first round-trip found: a screen that changed between the two round-trips — a dismissed modal,
say — could make a different element the sole match for the selector, and the retry would then act on
that different element. The trade is acceptable, and narrower than the trade first looks. Resolution
stays on the Python side, so the invariant's purpose — keeping resolution authority out of the
runner, where an ambiguous predicate could silently pick one of several matches — is untouched; the
retry acts only on a *unique* Python-side match, never one of several; and re-resolving at the moment
of acting is exactly what idb does, since idb resolves and taps in one step, so the retry brings
XCUITest's stale handling into line with the other backends rather than inventing a looser rule. A
stricter variant — re-actuate only when the re-resolved element also matches the original element's
identity attributes, not the selector alone — stays open to `implement-be` should the selector-only
match prove too permissive.

**Unit 2 — Preserve determinism, and never double-actuate.** A re-issued tap after a `stale` reply is
safe in a way a re-issued tap after a *timeout* is not — the distinction BE-0218 turned on. The runner
returns `stale` before the runner touches the element (`store.lookup(handle:)` reports `.stale` and
the handler returns without actuating, in
[`BajutsuKit/Sources/BajutsuRunner/Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift)),
so a `stale` reply is definitive evidence that no actuation occurred, and re-sending cannot apply the
gesture twice. A timeout, by contrast, leaves delivery unknown, which is why BE-0218 refused to retry
a write past its deadline. Determinism is otherwise untouched: no large language model (LLM) enters
the path, no fixed `sleep` is added (the re-query round-trip is the condition, and the between-attempt
backoff is the dedicated stale-retry backoff of Unit 1, not a settle delay), and a genuine `stale`
disappearance, an
ambiguous re-resolution, and an exhausted attempt budget all stay the loud failures the failures are
today.

**Unit 3 — Prove the change off-device.** Cover the seam with unit tests against a scripted transport,
on the fast gate with no Simulator: a `stale` reply once followed by `ok`, with the selector still
resolving uniquely, recovers; a persistent `stale` with the selector still resolving exhausts the
bound and fails loudly; a `stale` whose re-query no longer resolves the selector fails immediately as
`ElementNotFound`, spending no extra attempts; and a `stale` whose re-query resolves ambiguously fails
as `AmbiguousSelector` and never actuates. The four cases pin both halves of the gate — that a
snapshot race recovers, and that a real disappearance or ambiguity still fails — so a later change
cannot quietly turn the honest gate into an absorbing one.

**Unit 4 — Keep the documentation in step (BE-0113).** The module docstring in
[`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) today states that a stale handle
"surfaces as the same vanished-element error idb would raise"; update the docstring to describe the
re-resolution retry and the exact condition under which a stale handle still fails. Update
[`DESIGN.md`](../../DESIGN.md) and [`docs/architecture.md`](../../docs/architecture.md) if either
describes the stale semantics, so the prose account matches the behavior.

## Alternatives considered

**Retry a stale actuation unconditionally, without the re-resolution gate.** Dropping the gate would
absorb a genuine disappearance — an element that a prior step's navigation removed — into a silent
retry, the flakiness-by-absorption BE-0049 forbids. The re-resolution gate is exactly what keeps the
retry honest: it re-actuates only when the element demonstrably still exists.

**Add a fixed settle delay after launch, before the first tap.** A fixed `sleep` that ignores the
condition violates prime directive 2 outright, and the delay would only narrow the race window rather
than close the window — a settle slower than the fixed budget stales the handle anyway. The
re-resolution retry closes the race regardless of how long the screen takes to settle.

**Move resolution into the runner, collapsing the two round-trips.** Letting the runner resolve the
selector at actuation time would remove the snapshot gap, but resolution on the Python side is the
backend's core invariant: resolution authority stays in Python, never in a runner-side predicate that
could match one of several elements. The stale retry re-resolves on the Python side and only on a
unique match (the trade *Detailed design* states in full), so the retry keeps that authority in
Python; moving resolution into the runner would surrender the authority, trading the stale-race for
the ambiguity the deterministic core exists to prevent.

**Quarantine or job-retry the check in CI.** Marking `xcuitest (multi-touch)` allowed-to-fail, or
wrapping the job in a retry, would turn the check green without touching the race — the same reasons
BE-0287 rejects the option for the sibling flake: it hides a real race behind a green check, and it
keeps burning macOS minutes on every silent retry.

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
