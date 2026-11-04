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

Bajutsu already ships the doctor feasibility gate ([architecture.md](../../docs/architecture.md#implementation-status)). The gate consists of the command-line interface (CLI) tool suite plus a check for a booted Simulator.

This item originally ran as a placeholder that absorbed small doctor and onboarding improvements as they came up. That practice has ended. Every new onboarding improvement, of any size, is now proposed as its own BE item. The Progress section below records the candidates that shipped while this item served as the catch-all.

## Motivation

Whether someone succeeds with Bajutsu depends first on whether their *setup* is sound. Three conditions make it sound:

- The right command-line tools exist on the machine.
- A Simulator has booted.
- The app's screens carry enough accessibility ids to be addressable.

Bajutsu already answers both halves of that question:

- `preflight.runnability` checks the environment: Xcode's `xcrun`, the backend's tools, and a booted Simulator.
- `doctor.score` grades a screen's convention readiness (id coverage, namespace conformance, duplicate ids) as Ready, Partial, or Blocked.

Together they form the onboarding surface: the checks that catch a problem *before* a run fails for a confusing reason.

Onboarding is not a one-time feature. It accretes. A new way to get stuck surfaces from time to time: a missing dependency, an unhelpful default, or a check that would have saved someone an hour. The fix typically belongs in `doctor` or `preflight`, not in a feature of its own. This item gives those small, related improvements a home and an ID to land against. Without it, each improvement would spawn a throwaway proposal.

## Detailed design

This is a deliberate **placeholder** topic, not a single concrete spec. The implemented onboarding surface is already documented in [architecture.md](../../docs/architecture.md#implementation-status). It has two parts: `preflight.py` (the environment runnability gate) and `doctor.py` (the convention score). The principle for this item is how *new* candidates get added. It is not a feature to build now.

- A new onboarding or doctor candidate is a small, focused improvement to the diagnose-before-you-run surface. Examples are a new runnability check, a clearer remedy string, a sharper `doctor` signal, and a better first-run default. A candidate too small to justify a standalone BE proposal goes here as a sub-item with its own short rationale.
- A candidate is **in scope** when it helps a user discover and fix a setup or convention problem *before* a run fails confusingly. It is **out of scope** when it reaches beyond diagnosis: it changes the run loop, adds a driver capability, or the like. An out-of-scope candidate becomes its own BE item.
- Every candidate inherits the prime directives unchanged. The checks stay deterministic and LLM-free (`doctor.score` is "AI is not involved" by construction). Per-app specifics (id namespaces, backend) come from config, which keeps the checks app-agnostic.

While this item served as the catch-all, one rule stood. A candidate that grew large enough for its own design discussion graduated to a dedicated BE item. This item then dropped the candidate. The catch-all itself is now retired. Every future onboarding improvement, large or small, gets its own BE item from the start.

### Shipped candidates

- **`doctor` covers the web (Playwright) backend.** The runnability gate (`preflight.py`) was iOS-shaped. It always required Xcode's `xcrun` and a booted Simulator, and it never checked the Playwright runtime. As a result, `bajutsu doctor` for a web target demanded the wrong tools and then crashed while resolving a simctl udid.

  The gate now branches by backend family. The web backend checks the Playwright package and its Chromium browser, with `uv sync --extra web` and `playwright install chromium` as remedies. It needs no Xcode or Simulator. `doctor` navigates a fresh browser to the target's `baseUrl` and scores that page. The convention score itself was already backend-agnostic, because it reads each element's id and traits. The web backend's `data-testid` ids therefore score unchanged.
- **A screen with no actionable elements is graded `Blocked`, not `Ready`** ([#337](https://github.com/bajutsu-e2e/bajutsu/pull/337)). `doctor.score` derived id coverage as `1.0` when there were no actionable elements. A blank, not-yet-loaded, or wrong screen therefore graded `Ready`. That confusing false positive told a first-run user that the setup was sound, when `doctor` had in fact found nothing to test.

  The score now treats "no actionable elements" as `Blocked`. `render` names the likely cause ("is the app on the expected screen and fully loaded?"), so the signal points at the real problem. The check stays deterministic and LLM-free.
- **`doctor` checks the target's backend config before probing tools or a device** ([#361](https://github.com/bajutsu-e2e/bajutsu/pull/361)). `doctor` resolved the target and went straight to probing tools or a device. A target carrying the wrong field for its selected backend then surfaced as a confusing downstream launch or navigate failure. Two examples are an iOS target with only a `baseUrl` and a web target with only a `bundleId`. An iOS target missing `bundleId` was never checked up front. Config parsing rejects a target with *neither* field, but not one with the wrong field for the backend it runs on.

  A new `preflight.config_checks` verifies that the selected backend's required field is present (web: `baseUrl`, iOS: `bundleId`). The remedy names the target. `doctor` runs the check first, so it fails fast with a fixable checklist instead of a doomed probe. The check stays deterministic and LLM-free.
- **Web trait/role mapping expanded from 6 to 20 entries** ([#405](https://github.com/bajutsu-e2e/bajutsu/pull/405)). The Playwright backend's `_ROLE_MAP` covered only `a`, `button`, `input`, and `textarea`. Worse, it mapped text inputs to a `textbox` trait that was not in `ACTIONABLE_TRAITS`, so `doctor.score` could not see any web text input.

  The map now covers checkbox, radio, switch, select/combobox/listbox, option/menuitem, searchbox, spinbutton, and textarea (as `textView`). The fix also moved the existing text-input mapping to `textField`, which `ACTIONABLE_TRAITS` includes. The scoring logic itself did not change.
- **`doctor` id-coverage thresholds are configurable** ([#406](https://github.com/bajutsu-e2e/bajutsu/pull/406)). `OK_COVERAGE` (0.9) and `FAIL_COVERAGE` (0.7) were constants in the code. A team with many decorative elements that legitimately lack test ids had no way to adjust the grading.

  A new `defaults.doctor` config section (`idCoverageOk` and `idCoverageFail`) threads through to `score()`. Load time validates the values: both lie in [0, 1], and ok >= fail. Existing configs are unchanged, because the hardcoded values remain the defaults. The check stays deterministic and LLM-free.
- **`doctor --scenario` checks capability compatibility before a run** ([#407](https://github.com/bajutsu-e2e/bajutsu/pull/407)). A user could pass all `doctor` checks and still fail mid-run, because the scenario used a capability that the backend lacked (for example, `pinch` on idb without `multiTouch`).

  A new `--scenario` option on the CLI `doctor` command loads the scenario YAML and runs `capability_preflight` against the backend's static capabilities. Each unsupported construct is reported with a human-readable scenario path (for example, `step 3 > if > then[0]: pinch needs 'multiTouch'`). The check is pure and needs no device. `use` component expansion is not applied, because it requires config context. A capability introduced only through expansion is therefore not detected.
- **Web UI exposes preflight checks via `POST /api/doctor`** ([#408](https://github.com/bajutsu-e2e/bajutsu/pull/408)). The web UI (`serve/`) had no onboarding surface. Users could diagnose setup problems only through the CLI or the MCP tool.

  A new `POST /api/doctor` endpoint runs config validation and tool runnability checks for a given target. It returns structured JSON (`{ok, checks[], target, backend}`). It omits the live screen score, which needs a device connection that the web UI user may not have yet. The endpoint exists to diagnose setup issues before a run is attempted. Both transports serve it (the stdlib handler and FastAPI).

## Alternatives considered

**Fold each onboarding idea into an existing item.** Some candidates do have a natural home. A runnability check for a new backend belongs with that backend's work. Many candidates, however, are cross-cutting and have no single owner, such as a clearer remedy string or a first-run default. Without a catch-all, they fall through the cracks. A standalone placeholder gives them somewhere to land.

**Open a fresh BE proposal for every onboarding tweak.** Rejected as too heavy. Most of these tweaks are one-line diagnostics or message improvements. A full proposal per change would bury small, obviously good fixes under process. This item lets them accumulate cheaply. Only the ones that warrant their own design graduate.

**Write no proposal and improve `doctor` ad hoc.** Rejected: the roadmap is the project's shared memory. Without an ID, the rationale for each onboarding improvement is lost, and no single place shows what has been considered. A visible placeholder is the lightest way to keep that history.

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
