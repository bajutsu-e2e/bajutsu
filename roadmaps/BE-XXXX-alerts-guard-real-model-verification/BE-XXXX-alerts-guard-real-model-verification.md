**English** · [日本語](BE-XXXX-alerts-guard-real-model-verification-ja.md)

# BE-XXXX — Real-model verification of the system-alert guard

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-alerts-guard-real-model-verification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Verification & coverage |
| Related | [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md), [BE-0295](../BE-0295-record-crawl-real-model-verification/BE-0295-record-crawl-real-model-verification.md) |
<!-- /BE-METADATA -->

## Introduction

`agents/alerts.py`'s system-alert guard exists to stop a live AI operation from acting
blindly into an unexpected system dialog (a permission prompt, a crash sheet) on a real device —
it backs not only `record` and `crawl` — which share one `_build_alert_guard`
(`bajutsu/cli/_shared.py`) — but also the deterministic `run --dismiss-alerts` path (`bajutsu/cli/commands/run.py`).
Every test that exercises it supplies a hand-built `AlertDecision` or a canned `FakeBlock` tool-use
response with coordinates the test author typed in — never a real screenshot judged by a real model.
This item adds a real-model check of the guard's actual job: given a genuine alert on a genuine
screen, does Claude locate the dismiss control correctly.

## Motivation

`tests/test_alerts.py`'s `StubLocator` and `FakeBackend(FakeBlock("resolve_alert", ...))` prove the
guard's code correctly plumbs whatever `AlertDecision` it receives through to an action — a real and
useful check of the wiring. It proves nothing about the guard's actual safety claim: that a real
vision-capable call, looking at a real alert dialog captured from a real device, reliably lands on
the correct dismiss coordinates rather than, say, the coordinates of a destructive "Delete" button
next to it. A wrong real answer here is not a cosmetic bug; it is the guard failing at the one thing
it exists to prevent, and no test in the current suite would catch it, because none ever asks a real
model to look at a real alert.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **A real-alert fixture set.** Capture screenshots of real system alerts on the showcase app
  (a permission prompt at minimum; a crash/error sheet if reproducible), covering at least one
  dialog with a nearby destructive control, so the fixture set can distinguish "found *a* button"
  from "found the *correct* button."
- **A key-gated live verification test.** For each fixture, call the guard's real vision path with a
  real credential and assert the returned coordinates land inside the correct dismiss control's
  frame — not merely that a decision was returned.
- **Land as a non-gating signal first.** Following the precedent in
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md),
  wire the live test into a CI lane touching the guard's callers as signal before considering it a required
  check. This item verifies the guard's real accuracy only; it does not change where the guard sits
  (Tier 1, live AI operation) or put it anywhere near the deterministic `run` verdict
  (prime directive 1).

## Alternatives considered

- **Trust the unit tests, since the guard's plumbing is correct.** Correct plumbing guarantees the
  guard acts on whatever `AlertDecision` it is handed; it says nothing about whether a real model
  reliably produces the *correct* decision when looking at a real alert, which is the actual safety
  property at stake.
- **Treat the safety gap as already covered by `record`'s general live-usage testing.** No such testing exists today —
  `record` itself has no real-model CI coverage — so there is no existing net this item could ride on.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Capture a real-alert fixture set from the showcase app, including a destructive-control case.
- [ ] Add a key-gated live test asserting the guard locates the correct dismiss control.
- [ ] Wire it into CI as a non-gating signal.

## References

- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- [BE-0295 — Real-model verification of the record and crawl propose loops](../BE-0295-record-crawl-real-model-verification/BE-0295-record-crawl-real-model-verification.md)
- `bajutsu/agents/alerts.py`, `tests/test_alerts.py` (`StubLocator`, `FakeBackend`/`FakeBlock`)
