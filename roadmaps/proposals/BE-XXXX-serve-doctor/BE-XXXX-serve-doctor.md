**English** · [日本語](BE-XXXX-serve-doctor-ja.md)

# BE-XXXX — Doctor readiness panel in the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-doctor.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Track | [Proposals](../../README.md#proposals) |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->

## Introduction

Surface `doctor` — the pre-run runnability gate and the screen convention score — in the `serve`
Web UI, so a user can answer "is my environment ready, and is my app addressable?" in the browser
*before* a run fails for a confusing reason. The checks are deterministic and AI-free by
construction; the UI only shells out to the existing command.

## Motivation

The first thing that decides whether a run succeeds is the setup: the right CLIs installed, a
Simulator booted, and an app whose screens carry enough accessibility ids to be addressable.
Bajutsu already answers both halves on the CLI — `preflight.runnability` checks the environment
(Xcode's `xcrun`, the backend's CLIs, a booted Simulator) and `doctor.score` grades a screen's
convention readiness (id coverage, namespace conformance, duplicate ids) into Ready / Partial /
Blocked (`bajutsu/preflight.py`, `bajutsu/doctor.py`). But the Web UI exposes none of it. It lists
simulators (`GET /api/simulators`), yet a user can still pick a target, press Run, and get a
confusing failure that a one-line "Blocked: no booted Simulator" or "Partial: 3 controls on this
screen have no id" would have pre-empted. The browser is where a new user starts, so it is exactly
where this diagnose-before-you-run surface belongs.

## Detailed design

Tier-1, read-only; the UI only shells out to the existing checks.

- **A readiness panel** reachable on its own and surfaced as a pre-run check in the Record and
  Replay forms. It posts to `POST /api/doctor` (`{target, udid?, backend?}`), which runs the check
  as a serve job (reusing the existing job / stream machinery) and returns two parts: the
  **runnability** result (each required CLI present? a Simulator booted?) with the existing remedy
  strings, and the **convention score** (Ready / Partial / Blocked) with the per-namespace id gaps
  `doctor.score` already computes.
- **Deterministic and read-only.** `doctor.score` is "AI is not involved" by construction and
  `preflight` is environment inspection; nothing here computes a verdict or touches a run.
- **Platform-aware**, like the rest of the UI. The runnability half is iOS-specific (a booted
  Simulator), so for a web target the panel shows the browser / runtime checks the Playwright
  backend needs and hides the simulator controls (the UI already branches on the selected backend).
- **App-agnostic.** The id namespaces and the backend come from config (`targets.<name>`), so the
  score's denominator is *declared*, never hard-coded.

## Alternatives considered

* **Leave doctor CLI-only.** Rejected: the people most helped by a readiness check — new users
  setting up — are the least likely to run a CLI diagnostic first; the browser is their entry point.
* **Show only the runnability gate, not the convention score.** Rejected as half the value: "my
  environment is fine but my app isn't addressable" is the more common and more confusing failure,
  and the score already exists.
* **Fold this into [BE-0024](../../implemented/BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)
  instead of a new item.** BE-0024 was a placeholder that absorbed small, CLI-side doctor /
  onboarding improvements; that catch-all practice has since ended, and the item now stands
  Implemented. A Web UI surface for doctor is a distinct, sizeable surface that was never in scope
  for that catch-all, so it stands as its own item and references BE-0024 rather than living inside
  it.

## References

* `bajutsu/doctor.py`, `bajutsu/preflight.py`, `bajutsu/cli/commands/doctor.py` — the checks this
  surfaces.
* `bajutsu/serve/` — the job plumbing reused; `GET /api/simulators` — the existing device-listing
  endpoint this panel sits beside.
* [BE-0024 — doctor / onboarding](../../implemented/BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)
  — the implemented CLI-side doctor / onboarding checks this Web UI surface complements.
* [BE-0011 — Local web UI (`bajutsu serve`)](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md),
  [BE-0072 — Responsive serve Web UI](../../implemented/BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md)
  — the UI this extends and the small-screen layout it inherits.
* [configuration.md](../../../docs/configuration.md) — the `doctor` score and runnability gate;
  [CLAUDE.md](../../../CLAUDE.md), [DESIGN §2](../../../DESIGN.md) — determinism first; the checks
  stay AI-free.
