**English** · [日本語](BE-0099-webhook-run-notifications-ja.md)

# BE-0099 — Webhook notifications for run results

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0099](BE-0099-webhook-run-notifications.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0099") |
| Implementing PR | [#414](https://github.com/bajutsu-e2e/bajutsu/pull/414) |
| Topic | Integration with external services |
| Origin | Integration with external services |
<!-- /BE-METADATA -->

## Introduction

After a `run` finishes and its verdict is fixed, post a structured summary of the run to a
webhook configured in the target config — Slack first, so a team channel gets a "✅ 12 passed" /
"❌ login failed" message the moment a run completes. The webhook fires **after** the deterministic
verdict, transports the already-computed result, and never participates in pass/fail: no LLM, no
effect on the exit code, a delivery failure logged but never able to fail the run. The message is
built from a **format-neutral summary model** derived from `manifest.json`
([BE-0068](../../implemented/BE-0068-regenerable-reports/BE-0068-regenerable-reports.md)); Slack is
its first renderer, with generic signed JSON POST / Teams / Discord left as later renderers on the
same model. Each sink subscribes to the events it cares about — completion (every run / failures
only / verdict flips) and, opt-in, run start — and the message links back to the hosted report when
the run knows its `serve` base URL.

## Motivation

The roadmap currently parks this whole area under **Not adopting**: *"Scheduling / Slack /
TestRail integration — the domain of the CI / notification layer."* That call holds for a run
inside a CI pipeline: GitHub Actions already turns the exit code, `junit.xml`, and the check
annotations ([BE-0003](../../implemented/BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md))
into whatever Slack message the team has wired up. The CI layer owns notification, and Bajutsu
should not reimplement it.

What that call missed is every run that happens **outside** a CI pipeline, where there is no
wrapper to parse the exit code and no notification layer at all:

1. **Runs launched from a hosted `serve`.** When `serve` runs on a shared host
   ([BE-0015](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
   [BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)), a run
   started from the Web UI button is not in anyone's CI. The result is complete on the server, but
   the person who kicked it off has closed the tab — there is no path to tell them it finished, or
   broke. This is the gap that most clearly is *not* "the CI layer's job", because there is no CI
   layer.
2. **Scheduled and ad-hoc runs.** A nightly run on a Mac mini, a manual `bajutsu run` before a
   release — neither sits in a pipeline whose platform sends notifications.
3. **Long autonomous crawls.** A `crawl`
   ([BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md))
   can run for many minutes; a "started" / "finished" ping is the difference between watching it and
   walking away.

The bounded reframing that keeps the original principle intact: Bajutsu does not become a CI
orchestrator. It gains a thin, deterministic **outbound transport** that takes the run summary it
already computes and POSTs it to a URL. The "notification business logic" — channel routing, alert
escalation, dashboards — still belongs to the receiving layer; Bajutsu just delivers the event.
Two facts make this cheap and safe to build: the run's canonical model already exists as
`manifest.json` (BE-0068), so the payload is a projection of existing data, not new bookkeeping;
and the delivery is a pure post-verdict side effect, so it sits entirely outside the
determinism-first contract by construction.

## Detailed design

### Configuration

A new top-level `notify:` list in the config (one entry per sink, so a run can fan out to several
channels), with an optional per-target override under `targets.<name>.notify` — per the
app-agnostic directive, *which* webhook a target uses is config, not code:

```yaml
notify:
  - format: slack                              # the first (and, in this item, only) renderer
    url: ${SLACK_WEBHOOK_URL}                   # secret-sourced, never inlined
    on: [failure, change]                       # which events this sink wants
    targets: [checkout, login]                  # optional: only these scenarios/tags (reuses BE-0034)
```

`url` is resolved through the existing secret machinery
([BE-0032](../../implemented/BE-0032-secret-variables/BE-0032-secret-variables.md)) so the webhook
URL — itself a credential for an Incoming Webhook — never lands in the config file or any artifact.

### When it fires (the event model)

Each sink declares the events it wants in `on:`. All four are first-class; `failure` is the
default when `on:` is omitted, since "ping me only when something breaks" is the dominant case:

| Event | Fires when | Notes |
|---|---|---|
| `failure` | the run's verdict is `ok == false` | the default; lowest noise |
| `change` / `recovery` | this run's verdict differs from the previous run for the same source | red→green or green→red; needs prior-run lookup |
| `always` | every run, pass or fail | dashboards / audit channels |
| `start` | the run begins | opt-in; for long crawls — pairs with a later completion event |

`change` / `recovery` reads the previous run's verdict for the same scenario source from run
history (`runs/` locally, or the hosted run store). When no prior run exists, the first run is
treated as a change so the baseline is announced rather than silently swallowed.

### What it sends (the summary model)

A format-neutral `RunNotification` summary, projected from `manifest.json`, deliberately **bounded**
so a 200-scenario run does not produce a 200-line message:

- **Run identity** — `runId`, tool version, `sourceName`, `backend` / `engine`, trigger source
  (`cli` / `serve` / `crawl`), and the git provenance (branch, commit) when the config came from a
  git source ([BE-0044](../../implemented/BE-0044-scenario-provenance/BE-0044-scenario-provenance.md),
  [BE-0063](../../implemented/BE-0063-git-config-source/BE-0063-git-config-source.md)).
- **Verdict** — overall `ok`, counts (total / passed / failed / skipped), total duration.
- **Scenario rollup** — failures are listed (name, duration, first line of the failure, the failing
  step); passes are collapsed to a count. The failure list is capped, with an "and N more" tail, so
  the message stays within a chat card.
- **Report link** — a `reportUrl` to the hosted report, included **only when the run knows its
  `serve` base URL** (a hosted/served run). A purely local `bajutsu run` omits it rather than
  emitting a dead `file://` link; the report still travels by the existing `--zip` export
  ([BE-0060](../../implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)).
- **Failure evidence pointers** — for each listed failure, a link to its failure screenshot, again
  only when a base URL makes the link resolvable.

The summary inherits the run's redaction
([BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)) — it is
built from the already-scrubbed manifest, so no raw secret reaches the webhook. The Slack renderer
turns this model into a Block Kit message; adding generic JSON / Teams / Discord later is a new
renderer over the same model, touching no run logic.

### Determinism and the prime directives

The whole feature lives strictly after the verdict, which is what keeps it inside the contract:

- **No LLM, ever** — the summary is a mechanical projection of `manifest.json`; there is no
  AI-written message. Nothing here touches the Tier-2 gate.
- **Delivery cannot change the verdict or the exit code.** The POST happens after `run` has decided
  pass/fail and written the manifest. A non-2xx response, a timeout, or an unreachable host is
  logged through operational logging
  ([BE-0055](../../implemented/BE-0055-operational-logging/BE-0055-operational-logging.md)) and
  surfaced as a warning, but the run's result and exit code are already fixed and never move.
- **Bounded, non-blocking delivery** — a short timeout and a small, bounded retry with backoff, so
  a slow webhook cannot stall or hang the run. (No fixed `sleep` in the run path; retry backoff is a
  delivery-side concern outside the deterministic run, not a wait condition inside it.)
- **App-agnostic** — endpoints, formats, and event filters are config; the runner, drivers, and
  report code are unchanged.

### Where it hooks in

One emission point after the manifest is written in the CLI run path
(`bajutsu/cli/commands/run.py`, alongside the existing `github.emit(...)` call), reused by
serve-launched runs so a hosted run notifies the same way. `start` fires from the run entry point
before the first scenario.

## Alternatives considered

- **Leave it entirely to the CI layer (the status-quo "Not adopting" stance).** Correct for
  CI-pipeline runs, but it strands every CI-less surface (hosted `serve`, scheduled, crawl) with no
  notification path at all. This item adopts only the bounded, generic-transport slice and leaves
  routing/escalation to the receiver, so the original principle survives.
- **Generic signed JSON POST first, Slack as one adapter.** The cleaner long-term shape, and the
  summary model is built format-neutral precisely so this stays open. We start with Slack because it
  is the concrete, immediately useful target and exercises the full path (events, bounded summary,
  report link) end to end; generic / Teams / Discord are then renderers over the proven model, not a
  rebuild.
- **An LLM-written, "smart" summary of the failure.** Rejected outright — it would put an LLM on the
  run-completion path and risks reading as a verdict. The message is a deterministic projection of
  the manifest. (AI-assisted *investigation* of a failure stays in the `triage` path, never in the
  notification.)
- **Per-scenario streaming notifications (a ping as each scenario finishes).** Deferred as too noisy
  for a chat channel; the completion summary is the unit teams actually want. Could return later as
  an opt-in for dashboard sinks.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [BE-0068 — Regenerable reports](../../implemented/BE-0068-regenerable-reports/BE-0068-regenerable-reports.md) — `manifest.json` as the canonical run model the summary projects from.
- [BE-0060 — Download / export a run report as a zip](../../implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) — how a local run still moves a complete report when no `reportUrl` is available.
- [BE-0055 — Operational logging for the hosted serve](../../implemented/BE-0055-operational-logging/BE-0055-operational-logging.md) — where delivery failures are recorded.
- [BE-0015](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) / [BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) — hosted `serve`, the surface that gives a run a `reportUrl` and that most needs an out-of-band notification.
- [BE-0044 — Scenario provenance](../../implemented/BE-0044-scenario-provenance/BE-0044-scenario-provenance.md) / [BE-0063 — Git config source](../../implemented/BE-0063-git-config-source/BE-0063-git-config-source.md) — the branch/commit identity carried in the payload.
- [BE-0032 — Secret variables](../../implemented/BE-0032-secret-variables/BE-0032-secret-variables.md) — sourcing the webhook URL as a secret.
- [BE-0034 — Tags / selective runs](../../implemented/BE-0034-tags-selective-runs/BE-0034-tags-selective-runs.md) — the selector reused to scope a sink to certain scenarios.
- `roadmaps/README.md` "Not adopting" → *Scheduling / Slack / TestRail integration* — the prior decision this item bounds and partially reverses.
