**English** · [日本語](BE-0269-ios-alert-guard-early-wait-intervention-ja.md)

# BE-0269 — Speed up the system-alert guard's intervention during wait steps

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0269](BE-0269-ios-alert-guard-early-wait-intervention.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0269") |
| Implementing PR | _pending_ |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

The system-alert guard (`bajutsu/alerts.py`, `SystemAlertGuard.dismiss`) clears an OS-level
prompt (e.g. iOS's "Save Password?") that idb's app-scoped accessibility query cannot see and
that therefore silently blocks a run. Today the guard only runs once a step has already failed
outright — for a `wait` step that means the *entire* `timeout` budget (commonly 10–30s, and at
least the `BAJUTSU_MIN_WAIT_TIMEOUT` floor of 20s on the smoke CI lane per BE-0231) elapses
before the guard gets a chance to intervene, even when the blocking alert appeared seconds into
the wait. This proposes checking for a blocking alert on a much shorter, independent cadence
inside the wait's own poll loop, so the guard fires as soon as the alert is detectable instead
of only after the whole wait has already been wasted.

## Motivation

`bajutsu/orchestrator/loop.py:550` wires the alert guard (`on_blocked`) so it is invoked only
`if not ok` — i.e. only after `_run_step_body` has already returned failure. For a `wait` step,
`_wait()` (`bajutsu/orchestrator/waits.py:72`) reaches that failure only at its `deadline`, built
from the step's full `timeout`. Concretely: a scenario with `wait: {for: ..., timeout: 20}` that
happens to trigger a "Save Password?" prompt two seconds in will sit polling a collapsed element
tree for the remaining ~18 seconds before the guard is even asked to look at the screen. Every
such alert during a run pays close to the full wait timeout in pure latency, which compounds
across a scenario (or a suite) with more than one system prompt and inflates local and CI run
time for no correctness benefit — the outcome is identical whether the guard fires at 2s or 18s,
only slower.

The fix must not defeat prime directive 1 (AI never judges pass/fail) or prime directive 2
(determinism first, no fixed `sleep`): the eventual step outcome still has to come from the
existing deterministic condition check in `_wait()`, and any AI-vision call the guard makes has
to stay rare and bounded — it cannot become a call fired on every 0.05s poll tick
(`_POLL` in `bajutsu/orchestrator/waits.py:14`), since `ClaudeAlertLocator.locate`
(`bajutsu/alerts.py:163`) does a real network round trip and is far too slow and costly to run at
that cadence.

## Detailed design

1. **A cheap, deterministic pre-check, reusing the existing detector.** A blocking
   SpringBoard-level prompt has an observable signature: idb's accessibility query is scoped to
   the foreground app, so a system alert collapses the queried element tree to a bare window with
   no actionable content. The repo already computes exactly this — `shows_app_ui(elements) -> bool`
   (`bajutsu/elements.py`) — and it is deliberately more careful than "a single window node": its
   docstring counts a screen as app UI when any non-`application` element carries an `identifier`
   *or* a `label`, so label/coordinate-driven apps without accessibility identifiers (the showcase
   `-noax` variants) are not mistaken for a blocked screen — "the bug that made the guard fire
   every turn". Every screen-polling wait branch — `for`/`gone`/`screenChanged` and `settled`
   (whose `_wait_settled` docstring already names a screen "covered by a system alert" as one it
   never treats as settled, `bajutsu/orchestrator/waits.py:142`) — already calls `driver.query()`
   on each poll tick and holds the result, so the pre-check is just `not shows_app_ui(elements)` on
   that already-fetched tree, at zero extra query cost. Reusing the existing detector rather than
   adding a fresh one keeps the two detection paths from drifting apart. The `request` (network
   `WaitRequest`) branch is deliberately out of scope: it polls observed network traffic, not the
   screen, so the signal does not apply to it. This is the deterministic trigger; it never itself
   decides pass/fail, only whether it's worth asking the guard to look.
2. **Debounce before acting.** A single collapsed poll can be a transient render frame, not a
   real alert, so require the signature to hold for a short run of consecutive polls before
   treating it as "likely blocked" — mirroring the existing `_SETTLE_POLLS` pattern used by
   `_wait_settled` (`bajutsu/orchestrator/waits.py:15,142`). This bounds the added latency to a
   small, fixed number of poll intervals (well under a second at `_POLL = 0.05`), not the fraction
   of a second doubling into an open-ended new sleep.
3. **Invoke the guard mid-wait, then resume the same wait.** Thread an optional guard callback
   into `_wait()` (and its `_wait_settled` helper, so the `settled` branch is covered too) — today
   the guard callback exists only at the `loop.py:550` step-retry level — so that once the debounced
   heuristic fires, `_wait()` calls it directly, in place, instead of
   waiting for `deadline`. On a successful dismiss, polling for the *original* condition resumes
   against the *same* `deadline` — the guard's early intervention changes only when recovery is
   attempted, not the timeout budget that still governs when the step is allowed to fail. This is
   the same shape `record.py`'s `clear_blocking` already uses on the record path (poll → `not
   shows_app_ui` → invoke the guard → retry), and the crawl path reuses it via
   `bajutsu/cli/commands/crawl.py`'s wiring; the work here is to bring that established loop to the
   deterministic `run`/`wait` path rather than to invent a new one.
4. **Cap the intervention rate.** Because `ClaudeAlertLocator.locate` is a real AI-vision call, cap
   how often it can fire within one wait (a minimum cooldown between attempts, and/or a small max-
   attempts-per-wait ceiling) so a persistent false-positive collapse — or a dismiss that doesn't
   actually clear the prompt — cannot turn the poll loop into a hot AI-call loop. Once the cap is
   hit, `_wait()` falls back to today's behavior: keep polling to `deadline` and let the existing
   `loop.py:550` end-of-step guard call take one more shot.
5. **Scope to `wait` steps behind an existing opt-in.** The guard already only runs when alert
   dismissal is enabled (`--dismiss-alerts` / the guard factory in
   `bajutsu/cli/commands/run.py:324`); this change only moves *when*, within an already-enabled
   guard's lifecycle, it gets its first chance to look — it introduces no new default-on AI call
   for scenarios that don't opt in.

## Alternatives considered

- **Just shorten `_POLL` and call the AI-vision guard on every tick.** Rejected: `_POLL` governs
  the condition-check cadence for every wait, including ones with no alert risk at all, and
  running a real Claude vision call at ~20 Hz is both cost-prohibitive and far slower than the
  poll interval it would be nested inside — it would make the hot path latency-bound by the AI
  backend instead of by the app under test.
- **A purely time-based periodic guard call (e.g. every 2s), no tree inspection.** Simpler, but
  wastes AI calls on waits whose tree never shows the collapse signature at all — the deterministic
  pre-check this proposes is strictly cheaper (it's already-fetched data) and matches the failure
  signature `alerts.py` itself documents, so it is preferred over a blind timer.
- **Ask idb for alert presence directly.** No such signal exists: idb's accessibility query is
  app-scoped by design (per `alerts.py`'s docstring), so SpringBoard-level prompts are invisible to
  it outright — the collapsed-tree signature is the best available proxy, not a workaround for a
  richer API this repo chose not to use.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — deterministic pre-check reusing the existing `shows_app_ui` (`bajutsu/elements.py`)
      on the already-fetched poll result across the screen-polling branches that a system alert can
      *stall* — `for`/`screenChanged`/`settled`. Narrowed from the proposal's list: `gone` is **not**
      guarded (a collapsed tree already satisfies "gone" and returns at once, so no timeout is
      wasted — guarding it would mean redefining "gone" to reject a blank screen, out of scope), and
      the network `request` branch remains out of scope.
- [x] Unit 2 — debounce the heuristic over a short run of consecutive polls before acting
      (`_GUARD_DEBOUNCE_POLLS`, mirroring `_SETTLE_POLLS`).
- [x] Unit 3 — thread a guard callback into `_wait()` (and `_wait_settled`) so a debounced hit
      triggers the guard in place and polling resumes against the original `deadline`.
- [x] Unit 4 — cooldown (`_GUARD_COOLDOWN`) and max-attempts (`_GUARD_MAX_ATTEMPTS`) cap on guard
      invocations within one wait; on exhaustion it logs once and falls back to the wait's timeout.

**Log**

- _pending_ — implemented all four units in `bajutsu/orchestrator/waits.py` (the `_AlertGuardGate`
  gate + threading `on_blocked`/`alerts` through `_wait`/`_wait_settled`) and
  `bajutsu/orchestrator/loop.py` (wiring; the end-of-step retry deliberately does not re-arm the
  mid-wait guard, bounding a step's AI-vision calls at `_GUARD_MAX_ATTEMPTS` + 1). Covered by
  `tests/orchestrator/test_waits.py`.

## References

- [`bajutsu/alerts.py`](../../bajutsu/alerts.py) — `SystemAlertGuard`, `ClaudeAlertLocator`, and
  the module docstring describing the collapsed-tree failure signature.
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — `_wait`, `_POLL`,
  `_adaptive_sleep`, `_wait_settled` (the `_SETTLE_POLLS` debounce precedent).
- [`bajutsu/elements.py`](../../bajutsu/elements.py) — `shows_app_ui`, the existing collapsed-tree
  detector this proposal reuses (prior art; more careful than a bare "single window node").
- [`bajutsu/record.py`](../../bajutsu/record.py) — `clear_blocking`, the record-path
  poll → `not shows_app_ui` → invoke-guard → retry loop whose shape Unit 3 brings to the
  `run`/`wait` path; the crawl path reuses it via
  [`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py).
- [`bajutsu/orchestrator/loop.py:550`](../../bajutsu/orchestrator/loop.py) — the current
  end-of-step-only `on_blocked` wiring this proposal moves earlier for `wait` steps.
- [BE-0231](../BE-0231-smoke-idb-first-wait-settling/BE-0231-smoke-idb-first-wait-settling.md) —
  the `BAJUTSU_MIN_WAIT_TIMEOUT` floor that makes the current worst case at least ~20s on the
  smoke CI lane.
- [BE-0118](../BE-0118-wait-for-contract-unification/BE-0118-wait-for-contract-unification.md) —
  the `wait_for` polling contract this proposal's `_wait()` change builds on.
