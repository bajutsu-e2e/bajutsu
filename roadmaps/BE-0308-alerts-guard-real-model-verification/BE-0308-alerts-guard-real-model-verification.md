**English** · [日本語](BE-0308-alerts-guard-real-model-verification-ja.md)

# BE-0308 — Real-model verification of the system-alert guard

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0308](BE-0308-alerts-guard-real-model-verification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0308") |
| Implementing PR | [#1668](https://github.com/bajutsu-e2e/bajutsu/pull/1668) |
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

- [x] Capture a real-alert fixture set from the showcase app, including a destructive-control case.
- [x] Add a key-gated live test asserting the guard locates the correct dismiss control.
- [x] Wire it into CI as a non-gating signal.

**Log**

- Captured all four fixtures from the showcase SwiftUI app on a booted iPhone 17 Simulator (iOS 26.5)
  and committed them under `tests/fixtures/be0308/`: the notification prompt, the location prompt
  (three stacked choices, only the bottom one refusing), iOS's cross-process paste consent, and the
  app's own delete confirmation, whose red `Delete` sits between `Archive` and `Cancel`. That last
  one is the destructive-control case, and it is the sharper form of the requirement: the guard must
  report no prompt at all, because the button it would reach for deletes. `tests/test_alert_fixtures_ondevice.py`
  captures them, reading each button's frame from the device rather than measuring pixels by eye:
  the three OS prompts from the same accessibility query `handleSystemAlert` resolves against
  (BE-0316), the app-owned dialog from the app's own element tree. It also refuses to write a
  fixture whose expected button the device did not report, so an OS or locale change fails the
  capture instead of quietly relabelling the ground truth. It is a manual, local step that no CI
  job runs.
- Added the key-gated live test (`tests/test_real_model_alerts.py`) and wired it into
  [`ai-smoke.yml`](../../.github/workflows/ai-smoke.yml) as a second non-gating job, `accuracy
  (system-alert guard)` — the BE-0282 precedent, and the same `workflow_dispatch`-only,
  `ai-smoke`-Environment boundary the existing job draws, so a fork-triggered run never sees the
  credential. The live test needs no Simulator: the dialogs are committed captures, so only the
  model call is live. Three deterministic layers keep it honest on every gate, with no credential.
  Over synthetic dialogs, stub locators prove the assertion accepts an answer on a dismiss control
  and rejects one on the granting button beside it, on the destructive button of a dialog the app
  owns, and on nothing at all. Over each committed capture, an answer on every recorded *wrong*
  button must be rejected — aiming at the recorded dismiss control instead would prove nothing,
  since a frame always contains its own centre. And one test pins the fixture's own
  normalized-to-point mapping against `SystemAlertGuard.dismiss`'s own arithmetic, so the
  verification measures the product rather than itself. What no credential-free layer can catch is a
  capture whose recorded dismiss control names the wrong button: only looking at the screenshot
  settles that, which is the live layer's job.
- Scoped the assertion to the locator's answer, not to the guard's on-device tap. Two defects
  surfaced while capturing, both in the guard's *actuation* path rather than its vision, and both
  outside this item's subject; each is recorded here and left for its own item rather than fixed
  under this one. First, `SystemAlertGuard.dismiss` maps the model's normalized answer through
  `screen_size_from_elements(driver.query())`, which BE-0326 already established overshoots the
  screen whenever a lazy list keeps buffered off-screen rows in the tree: measured against the
  showcase app it reports (418, 2456) where the real screen is (402, 874), which moves a perfectly
  correct answer for the notification prompt's `Don’t Allow` button from (127, 518) to (132, 1456),
  off the bottom of the screen. `Driver.viewport()` is the source BE-0326 introduced for exactly
  this, and the guard does not use it. Second, and independently, an app-scoped XCUITest coordinate
  tap cannot press a SpringBoard prompt's button at all: a tap at (5, 5), nowhere near either
  button, cleared the notification prompt and left the app reporting `authorized` — the prompt's
  granting default — while an untouched prompt stayed up through fifteen polls, so the interaction
  itself, not the coordinate, resolves the prompt. Whenever the vision guard fires against a
  SpringBoard prompt on this toolchain the outcome is therefore the prompt's default button, the
  opposite of the guard's documented least-destructive default, and the report still records the
  button the model chose. The deterministic native path (BE-0315 / BE-0316) is unaffected.

## References

- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- [BE-0295 — Real-model verification of the record and crawl propose loops](../BE-0295-record-crawl-real-model-verification/BE-0295-record-crawl-real-model-verification.md)
- [BE-0316 — Explicit mid-flow step for iOS permission-prompt alerts](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)
  — its SpringBoard accessibility query supplies the captured fixtures' ground-truth frames
- [BE-0326 — The `scroll` action: scroll until an element appears](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md)
  — it introduced `Driver.viewport()`, the true screen bounds a scrollable element tree cannot supply
- `bajutsu/agents/alerts.py`, `tests/test_alerts.py` (`StubLocator`, `FakeBackend`/`FakeBlock`)
- `tests/test_alert_fixtures_ondevice.py`, `tests/test_real_model_alerts.py`,
  `tests/alert_fixture_support.py`, `tests/fixtures/be0308/`
