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

The system-alert guard in `agents/alerts.py` stops a live AI operation from acting blindly into an
unexpected system dialog on a real device, such as a permission prompt or a crash sheet. Two callers
use it:

- `record` and `crawl`, which share one `_build_alert_guard` (`bajutsu/cli/_shared.py`).
- The deterministic `run --dismiss-alerts` path (`bajutsu/cli/commands/run.py`).

Every test that exercises the guard supplies a hand-built `AlertDecision`, or a canned `FakeBlock`
tool-use response with coordinates the test author typed in. No test uses a real screenshot judged by
a real model.

This item adds a real-model check of the guard's actual job. Given a genuine alert on a genuine screen,
does Claude locate the dismiss control correctly?

## Motivation

`StubLocator` and `FakeBackend(FakeBlock("resolve_alert", ...))` in `tests/test_alerts.py` prove that
the guard's code plumbs whatever `AlertDecision` it receives through to an action. That is a real and
useful check of the wiring.

The wiring check proves nothing about the guard's actual safety claim. The claim is that a real
vision-capable call, looking at a real alert dialog captured from a real device, reliably lands on the
correct dismiss coordinates. The alternative is the coordinates of a destructive "Delete" button next
to the dismiss control.

A wrong real answer here is not a cosmetic bug. The guard would fail at the one thing it exists to
prevent. No test in the current suite would catch the failure, because none asks a real model to look
at a real alert.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **A real-alert fixture set.** Capture screenshots of real system alerts on the showcase app. A
  permission prompt is the minimum, and a crash or error sheet is welcome if reproducible. Include at
  least one dialog with a nearby destructive control. The fixture set can then distinguish "found *a*
  button" from "found the *correct* button."
- **A key-gated live verification test.** For each fixture, call the guard's real vision path with a
  real credential. Assert that the returned coordinates land inside the correct dismiss control's
  frame. Returning a decision is not enough.
- **Land as a non-gating signal first.** Following the precedent in
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md), wire
  the live test into a CI lane that touches the guard's callers. Run it as signal before considering it
  a required check.
  - This item verifies the guard's real accuracy and nothing else.
  - It does not change where the guard sits (Tier 1, live AI operation).
  - It does not put the guard anywhere near the deterministic `run` verdict (prime directive 1).

## Alternatives considered

- **Trust the unit tests, since the guard's plumbing is correct.** Correct plumbing guarantees that the
  guard acts on whatever `AlertDecision` it is handed. It says nothing about whether a real model
  reliably produces the *correct* decision when it looks at a real alert, and that is the actual
  safety property at stake.
- **Treat the safety gap as already covered by `record`'s general live-usage testing.** No such testing
  exists today. `record` itself has no real-model CI coverage, so this item has no existing net to ride
  on.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Capture a real-alert fixture set from the showcase app, including a destructive-control case.
- [x] Add a key-gated live test asserting the guard locates the correct dismiss control.
- [x] Wire it into CI as a non-gating signal.

**Log**

- Captured all four fixtures from the showcase SwiftUI app on a booted iPhone 17 Simulator (iOS 26.5).
  They are committed under `tests/fixtures/be0308/`.
  - The notification prompt.
  - The location prompt, with three stacked choices where only the bottom one refuses.
  - iOS's cross-process paste consent.
  - The app's own delete confirmation, whose red `Delete` sits between `Archive` and `Cancel`.
- The delete confirmation is the destructive-control case, and it is the sharper form of the
  requirement. The guard must report no prompt at all, because the button it would reach for deletes.
- `tests/test_alert_fixtures_ondevice.py` captures the fixtures. It reads each button's frame from the
  device rather than measuring pixels by eye.
  - The three OS prompts come from the same accessibility query that `handleSystemAlert` resolves
    against (BE-0316).
  - The app-owned dialog comes from the app's own element tree.
  - The capture refuses to write a fixture whose expected button the device did not report. An OS or
    locale change then fails the capture instead of quietly relabelling the ground truth.
  - The capture is a manual, local step that no CI job runs.
- Added the key-gated live test (`tests/test_real_model_alerts.py`). It is wired into
  [`ai-smoke.yml`](../../.github/workflows/ai-smoke.yml) as a second non-gating job, `accuracy
  (system-alert guard)`, following the BE-0282 precedent.
  - The job draws the same boundary as the existing job: `workflow_dispatch` only, and the
    `ai-smoke` Environment. A fork-triggered run therefore never sees the credential.
  - The live test needs no Simulator. The dialogs are committed captures, so only the model call is
    live.
- Three deterministic layers keep the live test honest on every gate, with no credential.
  - Over synthetic dialogs, stub locators prove that the assertion accepts an answer on a dismiss
    control. They also prove that it rejects an answer on the granting button beside it, on the
    destructive button of a dialog the app owns, and on nothing at all.
  - Over each committed capture, an answer on every recorded *wrong* button must be rejected. Aiming
    at the recorded dismiss control instead would prove nothing, since a frame always contains its own
    centre.
  - One test pins the fixture's own normalized-to-point mapping against `SystemAlertGuard.dismiss`'s
    own arithmetic. The verification then measures the product rather than itself.
  - No credential-free layer can catch a capture whose recorded dismiss control names the wrong button.
    Only looking at the screenshot settles that, which is the live layer's job.
- Ran the live layer against a real model through the `claude-code` provider (BE-0176). The locator
  answered every capture correctly.
  - Notification prompt: it returned (126.6, 517.8) against a `Don’t Allow` centre of (127, 518),
    inside half a point.
  - Location prompt: it returned (200.7, 574.1), the bottom one of three stacked choices. This case
    separates finding *a* button from finding the *correct* one.
  - Paste consent: it returned (200.7, 481.0), inside `Don’t Allow Paste`.
  - The app's own delete confirmation: it reported no prompt at all, so it never reached for the red
    `Delete`.
  - The guard's vision is therefore sound on these dialogs. What is not sound is the actuation path
    below it, recorded next.
- Scoped the assertion to the locator's answer, not to the guard's on-device tap. Two defects surfaced
  while capturing. Both sit in the guard's *actuation* path rather than its vision, and both are
  outside this item's subject. Each is recorded here and left for its own item rather than fixed under
  this one.
  - **Screen-size mapping.** `SystemAlertGuard.dismiss` maps the model's normalized answer through
    `screen_size_from_elements(driver.query())`. BE-0326 already established that this overshoots the
    screen whenever a lazy list keeps buffered off-screen rows in the tree. Measured against the
    showcase app, it reports (418, 2456) where the real screen is (402, 874). A perfectly correct
    answer for the notification prompt's `Don’t Allow` button then moves from (127, 518) to
    (132, 1456), off the bottom of the screen. `Driver.viewport()` is the source BE-0326 introduced for
    exactly this, and the guard does not use it.
  - **SpringBoard tap.** Independently, an app-scoped XCUITest coordinate tap cannot press a SpringBoard
    prompt's button at all. A tap at (5, 5), nowhere near either button, cleared the notification
    prompt and left the app reporting `authorized`, the prompt's granting default. An untouched prompt
    stayed up through fifteen polls. The interaction itself, not the coordinate, resolves the prompt.
  - Whenever the vision guard fires against a SpringBoard prompt on this toolchain, the outcome is
    therefore the prompt's default button. That is the opposite of the guard's documented
    least-destructive default, and the report still records the button the model chose. The
    deterministic native path (BE-0315 / BE-0316) is unaffected.

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
