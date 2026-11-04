**English** · [日本語](BE-0269-ios-alert-guard-early-wait-intervention-ja.md)

# BE-0269 — Speed up the system-alert guard's intervention during wait steps

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0269](BE-0269-ios-alert-guard-early-wait-intervention.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0269") |
| Implementing PR | [#1122](https://github.com/bajutsu-e2e/bajutsu/pull/1122) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

The system-alert guard (`bajutsu/alerts.py`, `SystemAlertGuard.dismiss`) clears an OS-level prompt,
such as iOS's "Save Password?". Idb's app-scoped accessibility query cannot see such a prompt,
so the prompt blocks the run without any signal.

Today the guard runs after a step has already failed outright.
For a `wait` step, the entire `timeout` budget elapses before the guard can intervene. The budget is commonly 10–30
seconds. On the smoke CI lane it is at least the `BAJUTSU_MIN_WAIT_TIMEOUT` floor of 20 seconds
(BE-0231). The delay applies even when the blocking alert appeared seconds into the wait.

This proposal checks for a blocking alert on a much shorter, independent cadence inside the wait's own
poll loop. The guard then acts as soon as the alert is detectable, with no need to wait out the budget.

## Motivation

`bajutsu/orchestrator/loop.py:550` calls the alert guard (`on_blocked`) under `if not ok`.
That call happens after `_run_step_body` has already returned failure. For a `wait` step,
`_wait()` (`bajutsu/orchestrator/waits.py:72`) reaches that failure at its `deadline`.
The step's full `timeout` determines the `deadline`.

Take a scenario with `wait: {for: ..., timeout: 20}` that triggers a "Save Password" prompt two
seconds in. The wait polls a collapsed element tree for the remaining 18 seconds or so. Only then does
anyone ask the guard to look at the screen.

Every system alert during a run costs close to the full wait timeout in pure latency. The cost
compounds across a scenario (or a suite) with more than one system prompt. It inflates local and CI
run time with no gain in correctness, because the outcome is identical whether the guard fires at 2s
or 18s, only slower.

The fix must not defeat prime directive 1 (AI never judges pass/fail) or prime directive 2
(determinism first, no fixed `sleep`). The eventual step outcome still has to come from the existing
deterministic condition check in `_wait()`. Any AI-vision call the guard makes has to stay rare and
bounded.

The guard therefore cannot fire on every 0.05s poll tick (`_POLL` in
`bajutsu/orchestrator/waits.py:14`). `ClaudeAlertLocator.locate` (`bajutsu/alerts.py:163`) does a real
network round trip, which is far too slow and costly at that cadence.

## Detailed design

1. **A cheap, deterministic pre-check, reusing the existing detector.** A blocking
   SpringBoard-level prompt has an observable signature. idb's accessibility query is scoped to the
   foreground app, so a system alert collapses the queried element tree to a bare window with no
   actionable content.

   The repo already computes exactly this in `shows_app_ui(elements) -> bool` (`bajutsu/elements.py`).
   The function is more careful than "a single window node". Its docstring counts a screen as app UI
   when any non-`application` element carries an `identifier` *or* a `label`. Label- and
   coordinate-driven apps without accessibility identifiers (the showcase `-noax` variants) are
   therefore not mistaken for a blocked screen, which was "the bug that made the guard fire every
   turn".

   Every screen-polling wait branch (`for`, `gone`, `screenChanged`, and `settled`) already calls
   `driver.query()` on each poll tick and holds the result. The `_wait_settled` docstring
   (`bajutsu/orchestrator/waits.py:142`) already names a screen "covered by a system alert" as one it
   never treats as settled. The pre-check is therefore `not shows_app_ui(elements)` on the
   already-fetched tree, at zero extra query cost. Reusing the existing detector, instead of adding a
   new one, keeps the two detection paths from drifting apart.

   The `request` (network `WaitRequest`) branch is deliberately out of scope. It polls observed network
   traffic, not the screen, so the signal does not apply to it. The pre-check is the deterministic
   trigger. It never decides pass/fail, only whether asking the guard to look is worthwhile.
2. **Debounce before acting.** A single collapsed poll can be a transient render frame, not a real
   alert. The signature must therefore hold for a short run of consecutive polls before the wait treats
   the screen as "likely blocked". This mirrors the `_SETTLE_POLLS` pattern that `_wait_settled` uses
   (`bajutsu/orchestrator/waits.py:15,142`). The added latency is bounded to a small, fixed number of
   poll intervals, well under a second at `_POLL = 0.05`, and does not become an open-ended new sleep.
3. **Invoke the guard mid-wait, then resume the same wait.** An optional guard callback is threaded
   into `_wait()` and its `_wait_settled` helper, so the `settled` branch is covered too. Today the
   guard callback exists only at the step-retry level in `loop.py:550`. Once the debounced heuristic
   fires, `_wait()` calls the guard directly, in place, instead of waiting for `deadline`.

   After a successful dismiss, polling for the *original* condition resumes against the *same*
   `deadline`. The early intervention changes only when recovery is attempted, not the timeout budget
   that governs when the step may fail.

   The record path already has this shape: `clear_blocking` in `record.py` polls, checks
   `not shows_app_ui`, invokes the guard, and retries. The crawl path reuses that loop through the
   wiring in `bajutsu/cli/commands/crawl.py`. The work here brings the established loop to the
   deterministic `run`/`wait` path, without inventing a new one.
4. **Cap the intervention rate.** `ClaudeAlertLocator.locate` is a real AI-vision call, so the wait
   caps how often it can fire: a minimum cooldown between attempts, a small maximum number of attempts
   per wait, or both. A persistent false-positive collapse, or a dismiss that does not clear the prompt,
   then cannot turn the poll loop into a hot AI-call loop. Once the cap is hit, `_wait()` falls back to
   today's behavior. It keeps polling to `deadline` and lets the existing end-of-step guard call at
   `loop.py:550` take one more shot.
5. **Scope to `wait` steps behind an existing opt-in.** The guard already runs only when alert
   dismissal is enabled (`--dismiss-alerts`, through the guard factory in
   `bajutsu/cli/commands/run.py:324`). This change moves only *when*, within an already-enabled guard's
   lifecycle, the guard gets its first chance to look. It introduces no new default-on AI call for
   scenarios that do not opt in.

## Alternatives considered

- **Shorten `_POLL` and call the AI-vision guard on every tick.** Rejected. `_POLL` governs the
  condition-check cadence for every wait, including waits with no alert risk at all. A real Claude
  vision call at about 20 Hz is cost-prohibitive and far slower than the poll interval it would be
  nested inside. The hot path would become latency-bound by the AI backend instead of by the app under
  test.
- **A purely time-based periodic guard call (for example every 2s), with no tree inspection.** This is
  simpler, but it wastes AI calls on waits whose tree never shows the collapse signature. The
  deterministic pre-check this proposal makes is strictly cheaper, because it reads already-fetched
  data. The pre-check also matches the failure signature that `alerts.py` itself documents. It is
  therefore preferred over a blind timer.
- **Ask idb for alert presence directly.** No such signal exists. By design (per the `alerts.py`
  docstring), idb's accessibility query is app-scoped, so SpringBoard-level prompts are invisible to
  it. The collapsed-tree signature is the best available proxy, not a workaround for a richer API that
  this repo chose not to use.

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

### Log

- [#1122](https://github.com/bajutsu-e2e/bajutsu/pull/1122) — implemented all four units in `bajutsu/orchestrator/waits.py` (the `_AlertGuardGate`
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
