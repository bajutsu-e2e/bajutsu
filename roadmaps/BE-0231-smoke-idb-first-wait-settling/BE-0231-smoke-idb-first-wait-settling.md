**English** · [日本語](BE-0231-smoke-idb-first-wait-settling-ja.md)

# BE-0231 — Harden the E2E first-wait against Simulator settling flake

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0231](BE-0231-smoke-idb-first-wait-settling.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0231") |
| Implementing PR | [#952](https://github.com/bajutsu-e2e/bajutsu/pull/952) |
| Topic | On-device validation (M1 close-out) |
<!-- /BE-METADATA -->

## Introduction

The `smoke (idb)` continuous-integration (CI) lane intermittently fails at the very first
scenario step. The showcase smoke scenario's opening `wait` — for the first list row
(`stable.row.1`) — times out after 10 seconds before any interaction happens, even though the
Simulator booted, the app launched, and the post-launch readiness gate declared the app ready.
The failure is byte-for-byte identical across occurrences and clears on rerun: on PR #936 the
lane failed three times and only went green on the fourth attempt.

This item proposes to **root-cause and harden that first wait** so the lane is green on its
first attempt without manual reruns. It deliberately does **not** propose automatic retry of a
failed lane — the roadmap lists that under *Not adopting* because it hides flakiness and is in
tension with determinism-first. The fix here is to make the wait itself robust to a cold CI
Simulator's settling, not to paper over the timeout by running again.

## Motivation

Every rerun of the `smoke (idb)` lane costs roughly seven minutes of Simulator boot, build, and
run, and — more importantly — erodes trust in the gate. A lane that is green only after three
reruns trains contributors to reflexively re-run red checks, which is exactly the habit
determinism-first exists to avoid: a green that needed four tries is not a dependable signal.

The flake is environmental, not a product defect. On PR #936 the change under test was pure
read-only Python aggregation that touches no runner, driver, wait logic, or device path, and the
sibling `conformance (idb + xcuitest)` lane — the same idb backend against the same Simulator —
was green throughout. So the cause lives in the smoke lane's *own* startup timing on a
cold-booted CI Simulator, independent of the code under test.

Prior work hardened adjacent layers but not this one:

- **BE-0218** made `_await_ready` namespace-aware, so SpringBoard's off-namespace icons no longer
  satisfy the readiness gate. That fixed a *readiness-gate* misfire.
- **BE-0087** added projection-based settle and transient-empty backoff to the idb driver's
  *actuation* path.
- **BE-0207** added transient-retry to the XCUITest channel; **BE-0088** moved Simulator boot off
  the critical path.

The gap this item targets is the **handoff between the readiness gate and the scenario's first
`wait`**: `_await_ready` can declare the app ready (an in-namespace element has appeared) a moment
before the specific element the scenario immediately waits on — the first list row — has rendered
and settled on a slow cold-boot Simulator. The 10-second first wait then races that render and
occasionally loses. The remedy must stay condition-based (no fixed `sleep`, prime directive 2) and
app-agnostic (any per-target difference lives in config, prime directive 3).

## Detailed design

The work is deterministic diagnosis first, then targeted hardening, then a machine-checkable
acceptance. Each unit is independently shippable.

1. **Make the failure diagnosable from artifacts, not from reruns.** On a first-wait timeout,
   capture enough state to tell *which* of the candidate causes fired: the element tree at
   timeout, whether the readiness gate had passed and on which signal, and the time at which the
   awaited element (if ever) became queryable. Stamp it with the existing BE-0049 run provenance
   (scenario hash, tool version, git revision) so a rerun-to-green no longer discards the evidence.
   This unit decides, from data, between the three hypotheses below rather than guessing.

2. **Tighten the readiness → first-wait handoff.** If the diagnosis shows the readiness gate
   declares ready before the content the scenario waits on is present, close that gap: either point
   the showcase target's `readyWhen` at the first content-bearing element the scenario needs, or
   strengthen namespace readiness to require a content element rather than any in-namespace node.
   The change is config-first where possible (a target's `readyWhen`) and touches shared readiness
   logic only if the gap is general.

3. **Make the first `wait` resilient to a mid-transition empty tree.** Confirm the scenario-level
   `wait_for` tolerates a transient-empty or mid-transition query the way BE-0087 made the actuation
   path tolerate one — a first poll landing on an empty tree during the launch transition must not
   quietly consume the wait budget. Extend the transient-empty backoff to this path if it does not
   already cover it.

4. **Right-size the first-wait budget for a cold CI Simulator.** If the diagnosis shows the render
   genuinely takes longer than 10 seconds on a cold-booted CI Simulator (versus a warm local one),
   make the wait budget configurable per target/environment rather than a hardcoded constant — still
   condition-based, so a fast environment returns as soon as the element appears and only a slow one
   spends the larger budget. No fixed `sleep` is introduced.

5. **Prove it stays green.** The acceptance is machine-checkable: the `smoke (idb)` lane passes on
   its first attempt across a bounded number of consecutive CI runs with no manual rerun. This is a
   deterministic check on the gate itself, not an LLM judgment.

## Alternatives considered

- **Automatic retry (with reboot) of the failed lane.** Rejected as the spine of this item. The
  roadmap already lists *Automatic retry of failed tests* under *Not adopting*: it hides flakiness
  and is in tension with determinism-first. If such a mechanism is ever adopted it must be
  quarantine-only, loud (never silently masking an assertion failure), and provenance-stamped so a
  masked flake stays visible to BE-0220's cross-run triage — a separate, carefully-scoped item, not
  this one.
- **Blindly raising the global wait timeout.** Rejected: it masks the root cause and slows every
  run everywhere. Unit 4 raises the budget only where a diagnosis shows genuine cold-boot variance,
  and only as a condition-based, per-environment bound.
- **A fixed `sleep` warm-up after boot.** Rejected outright — prime directive 2 forbids fixed
  sleeps; waits are condition-based only.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — make the first-wait timeout diagnosable from artifacts (tree + readiness signal + provenance).
- [x] Unit 2 — tighten the readiness → first-wait handoff (`readyWhen` / content-aware readiness).
- [x] Unit 3 — make the first `wait` resilient to a mid-transition empty tree.
- [x] Unit 4 — right-size the first-wait budget for a cold CI Simulator (config, condition-based).
- [ ] Unit 5 — prove the lane stays green on first attempt across consecutive CI runs.

Log (oldest first):

- [#952](https://github.com/bajutsu-e2e/bajutsu/pull/952) — Units 2–4: point the `showcase-swiftui` target's `readyWhen` at the first Stable row
  (`stable.row.1`), the element the smoke scenario's opening `wait` needs, so `_await_ready` no longer
  returns on some other in-namespace node and lets the first step race a not-yet-rendered row on a
  cold-boot CI Simulator (Unit 2); and lock in that the scenario `for`-wait treats an empty first poll
  as "not yet, keep polling" rather than consuming its budget (Unit 3). Unit 2 alone did not hold:
  the flake reproduced on this PR's own `smoke (idb)` run with the identical
  `wait timeout: for stable.row.1 (10.0s)`, so the render genuinely exceeds the ~20s the gate's 10s
  plus the scenario's 10s allow. Unit 4 raises the wait floor on the `smoke (idb)` lane via
  `BAJUTSU_MIN_WAIT_TIMEOUT=20` (the pre-existing knob the Android e2e lane already opts into),
  giving the row ~30s from launch — condition-based, so a fast render returns at once and no fixed
  sleep is introduced. Config-first (prime directive 3), condition-based (prime directive 2), no LLM
  on the verdict path (prime directive 1). Unit 5 (CI stays green on first attempt) is observed on the
  `smoke (idb)` lane after this lands.

## References

- [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
  — namespace-aware readiness gate (the adjacent readiness fix).
- [BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle.md)
  — projection-based settle and transient-empty backoff on the actuation path.
- [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md)
  — idempotency-classified transient retry on the XCUITest channel.
- [BE-0088](../BE-0088-overlap-simulator-boot/BE-0088-overlap-simulator-boot.md)
  — Simulator boot overlapped with the build.
- [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
  — run provenance stamping and the stability ladder (reused for diagnosis).
- [BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md)
  — product-side cross-run flaky triage (distinct: post-execution analysis, not CI-lane infra).
- `.github/workflows/e2e.yml` — the `smoke (idb)` job definition.
- `bajutsu/platform_lifecycle.py` (`_await_ready`) — the post-launch readiness gate.
- `bajutsu/drivers/idb.py` — the idb settle / `wait_for` / transient-empty backoff.
- [DESIGN.md](../../DESIGN.md) §2, §3.1, §5 — determinism-first, the Tier1/Tier2 boundary, the stability ladder.
- PR #936 — the occurrence that motivated this item (four attempts to green).
