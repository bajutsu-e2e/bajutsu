**English** · [日本語](BE-0148-serve-doctor-ja.md)

# BE-0148 — Doctor readiness panel in the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0148](BE-0148-serve-doctor.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0148") |
| Implementing PR | [#704](https://github.com/bajutsu-e2e/bajutsu/pull/704) |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->

## Introduction

Surface `doctor` in the `serve` Web UI. `doctor` is the pre-run runnability gate plus the screen
convention score. A user then answers "is my environment ready, and is my app addressable?" in the
browser *before* a run fails for a confusing reason. The checks are deterministic and AI-free by
construction; the UI only shells out to the existing command.

## Motivation

The setup decides first whether a run succeeds. A run needs the right CLIs, a booted Simulator, and
screens that carry enough accessibility ids to be addressable.

Bajutsu already answers both halves on the CLI (`bajutsu/preflight.py`, `bajutsu/doctor.py`):

- `preflight.runnability` checks the environment: Xcode's `xcrun`, the backend's CLIs, and a booted
  Simulator.
- `doctor.score` grades a screen's convention readiness into Ready, Partial, or Blocked. The inputs
  are id coverage, namespace conformance, and duplicate ids.

The Web UI exposes none of it. It lists simulators (`GET /api/simulators`), yet a user can still pick
a target, press Run, and get a confusing failure. A one-line "Blocked: no booted Simulator" or
"Partial: 3 controls on this screen have no id" would have pre-empted that failure. The browser is
where a new user starts, so it is exactly where this diagnose-before-you-run surface belongs.

## Detailed design

Tier-1, read-only; the UI only shells out to the existing checks.

- **A readiness panel**, reachable on its own and surfaced as a pre-run check in the Record and
  Replay forms. It posts to `POST /api/doctor` (`{target, udid?, backend?}`). The endpoint runs the
  check as a serve job, reusing the existing job and stream machinery. It returns two parts:
  - the **runnability** result (each required CLI present? a Simulator booted?), with the existing
    remedy strings
  - the **convention score** (Ready / Partial / Blocked), with the per-namespace id gaps
    `doctor.score` already computes
- **Deterministic and read-only.** `doctor.score` is "AI is not involved" by construction, and
  `preflight` is environment inspection. The panel computes no verdict and touches no run.
- **Platform-aware**, like the rest of the UI. The runnability half is iOS-specific (a booted
  Simulator). For a web target, the panel shows the browser and runtime checks the
  Playwright backend needs, and hides the simulator controls. The UI already branches on the selected
  backend.
- **App-agnostic.** The id namespaces and the backend come from config (`targets.<name>`), so the
  score's denominator is *declared*, never hard-coded.

## Alternatives considered

* **Leave doctor CLI-only.** Rejected: the people a readiness check helps most are new users setting
  up, and they are the least likely to run a CLI diagnostic first. The browser is their entry point.
* **Show the runnability gate alone, not the convention score.** Rejected as half the value. "My
  environment is fine but my app isn't addressable" is the more common and more confusing failure,
  and the score already exists.
* **Fold this into [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)
  instead of a new item.** BE-0024 was a placeholder that absorbed small, CLI-side doctor and
  onboarding improvements. That catch-all practice has since ended, and the item now stands
  Implemented. A Web UI surface for doctor is a distinct, sizeable surface that was never in scope
  for the catch-all. It stands as its own item and references BE-0024 rather than living inside it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add the `POST /api/doctor` endpoint (`{target, udid?, backend?}`) returning the runnability
      result and the convention score
- [x] Add the readiness panel, reachable as a pre-run check in Record and Replay
- [x] Make the panel platform-aware (the checks return backend-appropriate; simulator-only form
      controls stay hidden for a web target)

The `POST /api/doctor` endpoint and its runnability half landed earlier under BE-0024; this item
completed it: the endpoint now takes `{udid?, backend?}`, reports the booted-Simulator check, and
adds the convention score (queried in-process only when the environment is runnable — the CLI's
"never a device it can't reach" guard), and a readiness panel surfaces both in the Record and
Replay forms. The score is a synchronous in-process query rather than a spawned job: it mirrors the
existing `doctor_check` and audit operations and keeps the check AI-free and deterministic.

- [#704](https://github.com/bajutsu-e2e/bajutsu/pull/704) — complete the doctor Web UI surface (endpoint score + readiness panel).

## References

* `bajutsu/doctor.py`, `bajutsu/preflight.py`, `bajutsu/cli/commands/doctor.py` — the checks this
  surfaces.
* `bajutsu/serve/` — the job plumbing reused; `GET /api/simulators` — the existing device-listing
  endpoint this panel sits beside.
* [BE-0024 — doctor / onboarding](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)
  — the implemented CLI-side doctor / onboarding checks this Web UI surface complements.
* [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md),
  [BE-0072 — Responsive serve Web UI](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md)
  — the UI this extends and the small-screen layout it inherits.
* [configuration.md](../../docs/configuration.md) — the `doctor` score and runnability gate;
  [CLAUDE.md](../../CLAUDE.md), [DESIGN §2](../../DESIGN.md) — determinism first; the checks
  stay AI-free.
