**English** · [日本語](BE-XXXX-config-project-hub-ja.md)

# BE-XXXX — Config project hub in serve (register, list, switch, run)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-config-project-hub.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Authoring experience (record / GUI editor) |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md), [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md), [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu serve` binds exactly one active config at a time: the launcher takes one config
(a file, a Git source, or an uploaded zip bundle), and every tab — run, record, crawl, the
run-stats dashboard — operates against that single binding. Switching to another config means
restarting `serve` with a different `--config`. That is fine for driving one app, but it makes
`serve` a poor **hub** for a team that maintains several configs (several apps, or several
targets of one app) and wants to see them side by side, pick one, run it, and come back to
its history.

This proposal turns `serve` into that hub: a lightweight **project registry** where each
config is registered as a named project you can add, list, switch between, and run — from both
the Web UI and the CLI. The metrics side (comparing projects against each other) is a separate
proposal, **cross-project metrics comparison dashboard**; this item delivers the registry and
the per-project run plumbing it builds on.

## Motivation

Two facts about today's `serve` create the gap:

- **One config per process.** The active config is chosen at launch and fixed for the
  lifetime of the process ([BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md)
  lets you *view* it, read-only, but not switch it). A team with three configs runs three
  `serve` processes on three ports, with no shared surface listing them.
- **Run history is scoped to that one config.** The aggregate run-stats dashboard
  ([BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md)) aggregates the
  run history of whatever config is currently bound. There is no way to say "show me the
  history of the *checkout* config" without first restarting `serve` bound to it.

Meanwhile the hosted design already anticipates exactly this shape. The single-tenant server
backend shipped under [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
carries a `projects` table (`id`, `org_id`, `name` = the config's app name, `created_at`,
`unique(org_id, name)`) and a `runs.project_id` foreign key — but **nothing writes
`project_id` today**: it is unwired scaffolding waiting for a "project picker" that the hosted
UI was always meant to grow. The local `serve` has no notion of a project at all.

So there are two half-built halves of one feature: a hosted schema with no local producer, and
a local launcher with no multi-config surface. This item fills the local half in a way that
**reuses the hosted schema rather than inventing a second one**, so the local hub and the
future hosted project picker are the same concept — the local registry resolves to the single
`default` org that BE-0015 already falls back to, exactly as the DB-backed run listing does.

**A hub, not a scheduler.** "Continuously run tests against a config" is deliberately scoped to
*accepting an external trigger* — a manual **Run** button, a CLI invocation, or a CI/cron call
to an HTTP endpoint — not to Bajutsu growing its own scheduler. The roadmap already records
scheduling as *Not adopting* ("the domain of the CI/notification layer"), and this item does not
reverse that: it gives the trigger a stable, project-addressed target (so a cron job or CI step
can say "run project *checkout*" without knowing a filesystem path), and pairs with the existing
webhook run notifications ([BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md))
on the result side. The cadence stays outside Bajutsu.

This stays within the prime directives. The registry and the run trigger are fully
deterministic — a project is a named config binding, and a run is the same `bajutsu run` the
launcher already spawns; no LLM enters the `run`/CI path. Per-app differences stay in each
project's own config (`targets.<name>`), so the hub itself is app-agnostic — it lists and
selects configs, it does not encode anything about a particular app.

## Detailed design

The work is MECE across five units: the registry model, its persistence, the API, the UI, and
the CLI/trigger surface.

### 1. The project model (reuse BE-0015's schema)

A **project** is a named binding to a config source. It reuses the `projects` row BE-0015
already defined — `name` (unique within the org), `org_id` (the single `default` org locally),
`created_at` — and extends the row with the **config source** it binds: the same three sources
`serve` accepts today ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) Git,
[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) zip upload, or a local file
path), stored as a small discriminated record (`kind` + its locator) rather than a raw path, so
the hosted backend can later resolve the same record through its own `ScenarioStore` without a
schema change. A run started for a project stamps `runs.project_id`, finally exercising the
foreign key BE-0015 left dangling.

### 2. Persistence (local first, storage-seam-aligned)

The registry persists through the same seam boundary BE-0015 established. With a database wired
(`BAJUTSU_DATABASE_URL`), projects and their runs live in the `projects` / `runs` tables. With
no database — the default local `serve` — the registry persists to a small on-disk store under
the serve state dir (JSON alongside the existing `runs/` tree), so a single-user local hub needs
no Postgres. Both paths sit behind one `ProjectRegistry` accessor (list / get / add / remove /
resolve-active), assembled in `_build_server_state` exactly where the other seams are wired,
keyed off whether a repository is present.

Run history is partitioned by project on both paths, so `GET /api/projects/<name>/runs`, the
UI's per-project "latest run verdict," and the sibling cross-project dashboard all work locally
too. With a database, that partition is the `runs.project_id` column (unit 1). With no database,
the on-disk store records the same association — each run under the existing `runs/` tree is
tagged with the project it belongs to (a project→run-ids index in the JSON store, the local
equivalent of the `project_id` column), so a per-project run listing is a lookup rather than a
scan. The launch config's auto-registered active project owns any runs started before an explicit
project is created.

Local behavior with no registry configured is unchanged: a bare `serve --config X` still binds
`X` and, on first use, auto-registers it as the active project so nothing regresses for the
single-config user.

### 3. API

New endpoints on the control plane, all deterministic and org-scoped (resolving to `default`
locally):

- `GET /api/projects` — list registered projects (name, config source, last run summary).
- `POST /api/projects` — register a project from a config source (validated against the
  hosted config-source allowlist [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)
  defines — upload and Git only, no client-supplied filesystem path when hosted).
- `DELETE /api/projects/<name>` — deregister (history is retained, only the binding is removed).
- `POST /api/projects/<name>/run` — enqueue a run for that project through the existing
  `RunExecutor` seam, stamping `project_id`. This is the external-trigger target.
- `GET /api/projects/<name>/runs` — the project's run history (the per-project slice the
  cross-project dashboard will aggregate).

The existing single-config endpoints keep working; project-scoped ones are additive.

### 4. UI

A **project switcher** in the serve shell (a picker in the header, next to the config viewer)
and a **projects list** view: each row shows the project name, its config source, its latest
run verdict, and a **Run** button. Selecting a project rebinds the UI's active project without
restarting the process — every existing tab (run / record / crawl / the BE-0102 dashboard) then
operates against the selected project's config and history. This is the surface that makes
`serve` a hub rather than a single-config launcher.

### 5. CLI / trigger surface

A thin CLI mirror so CI and cron can drive the hub headlessly, without the Web UI:

- `bajutsu project add <name> --config <source>` / `bajutsu project ls` /
  `bajutsu project rm <name>`.
- `bajutsu run --project <name>` — resolve the project's config and run it, equivalent to the
  `POST /api/projects/<name>/run` trigger. This is what a cron entry or CI step calls; the
  cadence lives in that external system, not in Bajutsu.

## Alternatives considered

- **Invent a fresh local-only project schema.** Rejected: BE-0015 already defines `projects` /
  `runs.project_id`, and a second, incompatible local model would guarantee a painful
  reconciliation when the hosted picker lands. Reusing the schema (falling back to the `default`
  org locally, as the DB-backed run listing already does) keeps the local hub and the hosted
  picker one concept.
- **Multiple `serve` processes + a static index page.** The status-quo workaround — run one
  `serve` per config and bookmark the ports. Rejected: no shared history, no switching, no single
  place to trigger a run; it is exactly the gap this item closes.
- **Add a built-in scheduler so Bajutsu runs projects on a cadence.** Rejected as out of scope
  and against a standing roadmap decision (*Not adopting: scheduling — the domain of the
  CI/notification layer*). The hub exposes a project-addressed trigger and leaves the cadence to
  CI/cron, pairing with [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md)
  for the notification half.
- **Fold this into BE-0015.** Rejected: BE-0015 is the hosted, multi-tenant topology (OAuth,
  worker pool, Orka). This item is the *local* hub that first exercises BE-0015's project schema
  and is useful with no cloud at all; keeping it separate lets it ship on the local gate without
  waiting on the hosted stack. They are linked `Related`.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] 1 — The project model: extend BE-0015's `projects` row with a config-source record; stamp `runs.project_id`.
- [ ] 2 — Persistence: the `ProjectRegistry` seam (DB-backed when a repository is present, on-disk JSON otherwise), run history partitioned by project on both paths (the `project_id` column with a DB, a project→run-ids index without); auto-register the launch config as the active project.
- [ ] 3 — API: the five `/api/projects…` endpoints, org-scoped, additive to the existing single-config ones.
- [ ] 4 — UI: the project switcher + projects list, rebinding the active project without a restart.
- [ ] 5 — CLI: `bajutsu project add/ls/rm` and `bajutsu run --project <name>` as the headless trigger.

## References

`bajutsu/serve/`, `bajutsu/serve/server/db.py` (the `projects` / `runs` tables),
[architecture](../../docs/architecture.md), [cli](../../docs/cli.md#serve),
[reporting](../../docs/reporting.md);
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the hosted schema
this reuses), [BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md) (the
per-config dashboard the switcher rebinds),
[BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md) (the read-only config
viewer this makes switchable),
[BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md) (the
notification half of the external-trigger loop), and the sibling **cross-project metrics
comparison dashboard** proposal, which aggregates the per-project run history this item records.
