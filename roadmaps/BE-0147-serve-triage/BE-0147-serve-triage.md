**English** · [日本語](BE-0147-serve-triage-ja.md)

# BE-0147 — Triage failed runs in the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0147](BE-0147-serve-triage.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0147") |
| Implementing PR | [#703](https://github.com/bajutsu-e2e/bajutsu/pull/703) |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->

## Introduction

Bring the `triage` failure investigator into the `serve` Web UI. When a run fails in the Replay or
History view, the user diagnoses it and previews a proposed fix in the browser, instead of dropping
to a terminal.

Heuristic triage is the default and is fully deterministic. The Claude (`--ai`) path is opt-in and
acts as an investigator alone. Applying a fix is an explicit, diff-previewed human action. No LLM
enters the gate, and a run's verdict is never recomputed.

## Motivation

The browser is where a failure is first seen. The Replay view streams the run and embeds its red
report. Yet the UI stops exactly there: it shows *that* a scenario failed but offers no next step
toward understanding *why*.

The capability already exists on the CLI. `bajutsu triage` assembles a failed run's context: the
failing step, its assertion, and the captured element tree, screenshot, and network. It hands that
context to a `TriageAgent`. The default is the rule-based `HeuristicTriageAgent`
(`bajutsu/triage.py`). With `--ai`, the Claude-backed agent also reads the failure screenshot
(`bajutsu/claude_triage.py`).

An agent can propose a *structured* fix (`renameId` / `addIndex` / `raiseTimeout`). `--apply` /
`--write` patches the scenario source, diff-previewed and opt-in. `--rerun` confirms the patch.

Today all of that is terminal-only. A person looking at the failed report in the browser has to copy
the run id, switch to a shell, and re-run the investigation by hand. Surfacing triage where the
failure is already on screen closes that loop. That is the single most common thing a user wants after
a red run.

## Detailed design

Tier-1, advisory; the UI only shells out to the existing command.

- **Entry point.** A "Triage" action on a failed run, in the Replay report and the History list,
  posts to a new `POST /api/triage` (`{runId, scenario, ai?}`). The endpoint runs triage as a serve
  *job*. It reuses the job, SSE log-stream, and cancel machinery that the run, record, and crawl
  actions already use (`bajutsu/serve/jobs.py`, the `/api/jobs/{id}/events` stream).
- **Result.** The panel renders the diagnosis: the root-cause summary and the implicated step or
  assertion. When the agent proposes a structured fix, the panel shows it as a **diff preview**
  against the scenario source, the same diff `--apply` prints on the CLI. Applying is a separate,
  explicit click. It writes through the existing scenario save path (`POST /api/scenario`, already
  validated by `load_scenario_file`). An optional "apply & re-run" chains into the existing run job.
- **AI is opt-in and an investigator alone.** Heuristic triage is the default and deterministic. The
  `--ai` path uses the configured AI provider exactly as `record`, `crawl`, and the alert guard do.
  The Settings modal's provider and key selection already exists. When no provider is configured,
  the AI toggle is disabled with a hint, and heuristic triage still works.
- **App-agnostic.** Triage operates over the run directory's stored artifacts and the scenario
  source resolved from config (`targets.<name>`). Nothing in the tool is per-app.

The design respects the prime directives by construction:

- The verdict came from the deterministic run and is read back, never recomputed.
- AI, when used, only investigates.
- The fix is a proposal the human accepts, never an automatic edit.

## Alternatives considered

* **Leave triage CLI-only (status quo).** The failure is shown in the browser, but the investigation
  must move to a terminal. Rejected: the report view is the lowest-friction place to ask "why did
  this fail?", and the job, stream, and save plumbing to host it already exists.
* **Auto-apply the proposed fix.** Rejected: auto-applying collides with the self-healing guard rule
  (AI proposes, the human opts in —
  [BE-0039](../BE-0039-self-healing-propose-optin/BE-0039-self-healing-propose-optin.md))
  and risks silently "making the test laxer"
  ([BE-0023](../BE-0023-self-healing-guards/BE-0023-self-healing-guards.md)). The fix
  is always a diff the user accepts.
* **Run triage inline in the request rather than as a job.** Rejected: AI triage can take many
  seconds, and it must be cancelable and streamable like the other long actions. Reusing the job
  model keeps the UX consistent and the server responsive.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add the `POST /api/triage` endpoint (`{runId, scenario, ai?}`), running triage as a serve job
      that reuses the existing job / SSE / cancel machinery
- [x] Add the "Triage" action on a failed run's Replay report and History list, with a diff preview
      for any proposed fix
- [x] Wire the AI (`--ai`) path as opt-in, defaulting to the deterministic heuristic agent

- [#703](https://github.com/bajutsu-e2e/bajutsu/pull/703) — Ship BE-0147: `triage --json` writes a machine-readable result into the run dir; a new
  `POST /api/triage` runs it as a serve job (reusing the job / SSE / cancel machinery); the Replay
  report and History list gain a "Triage" action with a diff preview; Apply writes the fix through the
  validated `POST /api/scenario` save path. AI is opt-in and only investigates; the heuristic agent is
  the default and no LLM touches the verdict.

## References

* `bajutsu/triage.py`, `bajutsu/claude_triage.py`, `bajutsu/cli/commands/triage.py` — the
  investigator this surfaces.
* `bajutsu/serve/` (`jobs.py`, `operations.py`, `handler.py`) — the job / stream / save plumbing
  reused.
* [BE-0021 — AI triage](../BE-0021-ai-triage/BE-0021-ai-triage.md),
  [BE-0022 — structured fixes](../BE-0022-update-structured-fixes/BE-0022-update-structured-fixes.md),
  [BE-0039 — propose + opt-in apply](../BE-0039-self-healing-propose-optin/BE-0039-self-healing-propose-optin.md),
  [BE-0023 — guards against making tests laxer](../BE-0023-self-healing-guards/BE-0023-self-healing-guards.md)
  — the triage feature and its self-healing guards this UI surface reuses.
* [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)
  — the UI this extends;
  [BE-0072 — Responsive serve Web UI](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md)
  — the small-screen layout the panel inherits.
* [CLAUDE.md](../../CLAUDE.md), [DESIGN §2](../../DESIGN.md) — AI never judges; determinism
  first. Triage stays advisory and the verdict is read back, never recomputed.
