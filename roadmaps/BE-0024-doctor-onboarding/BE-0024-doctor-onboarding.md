**English** · [日本語](BE-0024-doctor-onboarding-ja.md)

# BE-0024 — doctor / onboarding

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0024](BE-0024-doctor-onboarding.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0024") |
| Implementing PR | [#236](https://github.com/bajutsu-e2e/bajutsu/pull/236), [#337](https://github.com/bajutsu-e2e/bajutsu/pull/337), [#361](https://github.com/bajutsu-e2e/bajutsu/pull/361), [#405](https://github.com/bajutsu-e2e/bajutsu/pull/405), [#406](https://github.com/bajutsu-e2e/bajutsu/pull/406), [#407](https://github.com/bajutsu-e2e/bajutsu/pull/407), [#408](https://github.com/bajutsu-e2e/bajutsu/pull/408) |
| Topic | doctor / onboarding |
<!-- /BE-METADATA -->

## Introduction

The doctor feasibility gate (the CLI (command-line interface) suite + a check for a booted Simulator) is implemented ([architecture.md](../../docs/architecture.md#implementation-status)). This item originally ran as a placeholder that absorbed small doctor / onboarding improvements as they came up. That practice has ended: new onboarding improvements, of any size, are now proposed as their own BE items instead of being added here. The Progress section below records the candidates that shipped while this item served as that catch-all.

## Motivation

The first thing that decides whether someone succeeds with Bajutsu is whether their *setup* is
sound: the right CLIs (command-line interface tools) installed, a Simulator booted, and an app
whose screens carry enough accessibility ids to be addressable. Bajutsu already answers both
halves of that — `preflight.runnability` checks the environment (Xcode's `xcrun`, the backend's
CLIs, a booted Simulator) and `doctor.score` grades a screen's convention readiness (id coverage,
namespace conformance, duplicate ids) into Ready / Partial / Blocked. Together they are the
onboarding surface: the checks that catch a problem *before* a run fails for a confusing reason.

Onboarding is not a one-time feature, though — it accretes. Each time a new way to get stuck
surfaces (a missing dependency, an unhelpful default, a check that would have saved someone an
hour), the fix usually belongs in `doctor`/`preflight` rather than in a feature of its own. This
item exists so those small, related improvements have a home and an ID to land against, instead of
each spawning a throwaway proposal.

## Detailed design

This is a deliberate **placeholder** topic, not a single concrete spec. The implemented
onboarding surface is already documented in [architecture.md](../../docs/architecture.md#implementation-status):
`preflight.py` (the environment runnability gate) and `doctor.py` (the convention score). The
principle for this item is how *new* candidates get added, not a feature to build now:

- A new onboarding/doctor candidate is a small, focused improvement to the diagnose-before-you-run
  surface — a new runnability check, a clearer remedy string, a sharper `doctor` signal, a better
  first-run default. It is recorded here as a sub-item with its own short rationale, rather than
  promoted to a standalone BE proposal, when it is too small to justify one on its own.
- A candidate is **in scope** when it helps a user discover and fix a setup or convention problem
  *before* a run fails confusingly. It is **out of scope** — and should become its own BE item —
  when it changes the run loop, adds a driver capability, or otherwise reaches beyond diagnosis.
- Every candidate inherits the prime directives unchanged: the checks stay deterministic and
  LLM-free (`doctor.score` is "AI is not involved" by construction), and per-app specifics (id
  namespaces, backend) come from config, keeping the checks app-agnostic.

When a candidate grew large enough to deserve its own design discussion, it graduated to a
dedicated BE item and was removed from here — that was the standing rule while this item served as
the catch-all. The catch-all itself is now retired: every future onboarding improvement, large or
small, gets its own BE item from the start.

### Shipped candidates

- **`doctor` covers the web (Playwright) backend.** The runnability gate (`preflight.py`) was
  iOS-shaped — it always required Xcode's `xcrun` and a booted Simulator and never checked the
  Playwright runtime, so `bajutsu doctor` for a web target demanded the wrong tools and then crashed
  resolving a simctl udid. The gate now branches by backend family: the web backend checks the
  Playwright package and its Chromium browser (with `uv sync --extra web` / `playwright install
  chromium` remedies, no Xcode/Simulator), and `doctor` navigates a fresh browser to the target's
  `baseUrl` and scores that page. The convention score itself was already backend-agnostic (it
  reads each element's id and traits), so the web backend's `data-testid` ids score unchanged.
- **A screen with no actionable elements is graded `Blocked`, not `Ready`** ([#337](https://github.com/bajutsu-e2e/bajutsu/pull/337)).
  `doctor.score` derived id coverage as `1.0` when there were no actionable elements, so a blank,
  not-yet-loaded, or wrong screen graded `Ready` — a confusing false-positive that told a first-run
  user their setup was sound when `doctor` had in fact found nothing to test. The score now treats
  "no actionable elements" as `Blocked` and `render` names the likely cause ("is the app on the
  expected screen and fully loaded?"), so the signal points at the real problem. The check stays
  deterministic and LLM-free.
- **`doctor` checks the target's backend config before probing tools or a device** ([#361](https://github.com/bajutsu-e2e/bajutsu/pull/361)).
  `doctor` resolved the target and went straight to probing tools / a device, so a target carrying
  the wrong field for its selected backend (an iOS target with only a `baseUrl`, a web target with
  only a `bundleId`) surfaced as a confusing downstream launch/navigate failure, and an iOS target
  missing `bundleId` was never checked up front. Config parsing rejects a target with *neither*
  field, but not the wrong one for the backend it runs on. A new `preflight.config_checks` verifies
  the selected backend's required field is present (web → `baseUrl`, iOS → `bundleId`) with a remedy
  naming the target, and `doctor` runs it first — failing fast with a fixable checklist instead of a
  doomed probe. The check stays deterministic and LLM-free.
- **Web trait/role mapping expanded from 6 to 20 entries** ([#405](https://github.com/bajutsu-e2e/bajutsu/pull/405)).
  The Playwright backend's `_ROLE_MAP` only covered `a`, `button`, `input`, and `textarea`, and
  critically mapped text inputs to a `textbox` trait that was not in `ACTIONABLE_TRAITS` — so all
  web text inputs were invisible to `doctor.score`. The map now covers checkbox, radio, switch,
  select/combobox/listbox, option/menuitem, searchbox, spinbutton, and textarea (as `textView`),
  and the existing text-input mapping was fixed to `textField` (which `ACTIONABLE_TRAITS` includes).
  The scoring logic itself is unchanged.
- **`doctor` id-coverage thresholds are configurable** ([#406](https://github.com/bajutsu-e2e/bajutsu/pull/406)).
  `OK_COVERAGE` (0.9) and `FAIL_COVERAGE` (0.7) were hardcoded constants; teams with many
  decorative elements that legitimately lack test IDs had no way to adjust grading. A new
  `defaults.doctor` config section (`idCoverageOk` / `idCoverageFail`) threads through to
  `score()`, validated at load time (both in [0, 1], ok >= fail). Existing configs are unchanged
  (the hardcoded values remain the defaults). The check stays deterministic and LLM-free.
- **`doctor --scenario` checks capability compatibility before a run** ([#407](https://github.com/bajutsu-e2e/bajutsu/pull/407)).
  A user could pass all `doctor` checks and then fail mid-run because the scenario used a
  capability the backend lacked (e.g. `pinch` on idb without `multiTouch`). A new `--scenario`
  option on the CLI `doctor` command loads the scenario YAML and runs `capability_preflight` against
  the backend's static capabilities, reporting each unsupported construct with a human-readable
  scenario path (e.g. `step 3 > if > then[0]: pinch needs 'multiTouch'`). The check is pure — no
  device needed. Note: `use` component expansion is not applied (it requires config context), so
  capabilities introduced only through expansion are not detected.
- **Web UI exposes preflight checks via `POST /api/doctor`** ([#408](https://github.com/bajutsu-e2e/bajutsu/pull/408)).
  The web UI (`serve/`) had no onboarding surface — users could only diagnose setup problems via the
  CLI or MCP tool. A new `POST /api/doctor` endpoint runs config validation and tool runnability
  checks for a given target, returning structured JSON (`{ok, checks[], target, backend}`). It omits
  the live screen score (which needs a device connection the web UI user may not have yet) — the
  whole point is to diagnose setup issues before attempting a run. Wired in both transports (stdlib
  handler + FastAPI).

## Alternatives considered

**Fold each onboarding idea into an existing item.** Some candidates do have a natural home — a
runnability check for a new backend belongs with that backend's work. But many are cross-cutting
(a clearer remedy string, a first-run default) with no single owner; without a catch-all they fall
through the cracks. A standalone placeholder gives them somewhere to land.

**Open a fresh BE proposal for every onboarding tweak.** Rejected as too heavy. Most of these are
one-line diagnostics or message improvements; a full proposal per change would bury small,
obviously-good fixes under process. This item lets them accumulate cheaply and graduate only the
ones that genuinely warrant their own design.

**Write no proposal and just improve `doctor` ad hoc.** Rejected: the roadmap is the project's
shared memory. Without an ID, the rationale for each onboarding improvement is lost, and there is
no single place to see what has been considered. A visible placeholder is the lightest way to keep
that history.

## Progress

> This item ran as a catch-all receptacle for small doctor / onboarding improvements; that practice
> has ended. The list below is the completed record of candidates shipped while it served that
> role; see "Shipped candidates" under *Detailed design* for the full rationale behind each one.

- [x] `doctor` covers the web (Playwright) backend ([#236](https://github.com/bajutsu-e2e/bajutsu/pull/236))
- [x] A screen with no actionable elements is graded `Blocked`, not `Ready` ([#337](https://github.com/bajutsu-e2e/bajutsu/pull/337))
- [x] `doctor` checks the target's backend config before probing tools or a device ([#361](https://github.com/bajutsu-e2e/bajutsu/pull/361))
- [x] Web trait/role mapping expanded from 6 to 20 entries ([#405](https://github.com/bajutsu-e2e/bajutsu/pull/405))
- [x] `doctor` id-coverage thresholds are configurable ([#406](https://github.com/bajutsu-e2e/bajutsu/pull/406))
- [x] `doctor --scenario` checks capability compatibility before a run ([#407](https://github.com/bajutsu-e2e/bajutsu/pull/407))
- [x] Web UI exposes preflight checks via `POST /api/doctor` ([#408](https://github.com/bajutsu-e2e/bajutsu/pull/408))

## References

[architecture.md](../../docs/architecture.md#implementation-status)
