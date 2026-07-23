**English** · [日本語](BE-0129-serve-scope-boundary-ja.md)

# BE-0129 — Bound serve scope and keep host concerns out of shared config

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0129](BE-0129-serve-scope-boundary.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0129") |
| Implementing PR | [#665](https://github.com/bajutsu-e2e/bajutsu/pull/665) |
| Topic | Hosting the web UI |
<!-- /BE-METADATA -->

## Introduction

`serve` has grown from a local preview server into the repository's largest, fastest-growing
subsystem. Its host-facing concerns (organizations, roles) have started leaking into
`bajutsu/config.py`, the schema the CLI's deterministic core also depends on.

This proposal draws an explicit boundary around `serve`'s scope and keeps multi-tenant hosting
concerns out of the config schema the rest of the tool shares.

## Motivation

| Component | Size | Note |
|---|---|---|
| `bajutsu/serve/` (incl. `server/`) | 6,920 lines / ~30 modules | SQLAlchemy, Alembic, OAuth, RBAC, an object store, a Redis-backed worker |
| `bajutsu/templates/serve.js` | 1,575 lines | vanilla JS, no build step, no tests |
| `bajutsu/serve/operations.py` | 1,376 lines | tracked separately by a planned follow-up proposal to split this module (TBD) |
| `bajutsu/` (whole core, Python) | ~30,000 lines | `serve` + `serve.js` together are roughly a fifth of this |

None of that infrastructure is something a local CLI tool's core has a reason to know about.

The growth reaches into the core, too. `bajutsu/config.py:352` defines `OrgConfig`, and `Config`
itself carries an `orgs: dict[str, OrgConfig]` field (`config.py:370`) plus four resolution helpers
(`config.py:393-429`). None of this means anything to a solo developer running `bajutsu run`
against a local Simulator — it exists purely to support `serve`'s hosted, multi-tenant deployment
(BE-0015).

`Config` is the schema every entry point parses, so this hosting concern has become a permanent tax
on the shared config surface — directly contradicting the "app-agnostic" and "keep the deterministic
core unchanged" premise `Effective`/`Config` are supposed to serve.

The deterministic `run` path parses that same schema, and — this is the subtle part — it does so
against an **org-bearing** config in the hosted topology. An operator declares `orgs:` in the very
`bajutsu.config.yaml` that a run consumes: `bajutsu/serve/operations/dispatch.py` ships the config's
full text to a remote worker as `materials` (or points a local server-mode run at the same file via
`--config`), and the core loader (`bajutsu/cli/_shared.py`'s `load_config`, and `bajutsu/mcp/tools.py`)
reads it. So the org model isn't only a *schema* tax — it is a value the deterministic `run` already
tolerates today because `Config` happens to carry an `orgs` field. Any move that drops that field must
keep `run` reading an org-bearing config working, or it breaks a documented hosted deployment
(`orgs:` is documented in `docs/configuration.md` / `docs/self-hosting.md`). This is exactly why the
boundary belongs in the loader, not in a bare field removal.

Severity: High. This is architectural drift, not a bug, but each new hosting feature (BE-0015,
BE-0016, BE-0051) makes the boundary harder to draw retroactively, and the config schema is the
part of the codebase every backend and every target depends on.

## Detailed design

The boundary is drawn with one concrete move plus a machine-checked rule, not a size cap or a
package split.

| # | Track | Concrete action |
|---|---|---|
| 1 | Document the rule | Short architecture note in `docs/` + `docs/ja/` naming the boundary |
| 2 | Enforce it in the gate | An import-linter forbidden contract (BE-0112) keeps the core off the `db`/`oauth` extras, via `make lint-imports` |
| 3 | Move `OrgConfig` out of `config.py` | New `bajutsu/serve/orgs.py`; split `load_config` |
| 4 | Guardrail for `serve.js` | `node --check` + minimal ESLint via a `make lint-js` step in `make check`; defer Jest/Vitest |

### 1. Document the rule

`bajutsu/config.py`, `bajutsu/drivers/`, `bajutsu/runner/`, and `bajutsu/scenario/` stay
host-agnostic: no organization, role, tenancy, or billing concept enters them, and neither does the
`db` (SQLAlchemy/Alembic/psycopg) or `oauth` (Authlib) extra. `bajutsu/serve/` — and only
`bajutsu/serve/` — owns hosting concerns.

The note points at the enforcement contract below instead of asking reviewers to hold the rule in
their head.

### 2. Enforce it in the gate, not with a size ceiling

The boundary already holds today: a check of every core module finds zero imports of
`bajutsu.serve` or of the `db`/`oauth` extras. `bajutsu/serve/server/logbus.py` and `sessions.py`
already take their Redis client through an injected `RedisLike` protocol rather than importing
`redis` directly, so `serve` doesn't even carry a hard Redis dependency.

BE-0112 landed an import-linter layer model (`[tool.importlinter]` in `pyproject.toml`, run by `make
lint-imports` inside `make check`) whose periphery contract *already* forbids the core from importing
`bajutsu.serve`. So rather than add a parallel AST test that would duplicate that half, a new
forbidden contract, "Deterministic core stays free of the db/oauth hosting extras", covers the part
the layer graph can't express: it forbids `bajutsu.config` / `bajutsu.drivers` / `bajutsu.runner` /
`bajutsu.scenario` from importing the external `db`/`oauth` packages (`sqlalchemy`, `alembic`,
`psycopg`, `cryptography`, `authlib`), with `include_external_packages` so import-linter sees the
external import. Together the two contracts turn "serve stays bounded" into a regression the gate
catches — no separate installable distribution (e.g. `bajutsu-serve`) is needed, since the isolation
is already dependency-level.

### 3. Move `OrgConfig` and the org helpers into `bajutsu/serve/orgs.py`

Every current caller already lives under `bajutsu/serve/` — the org helpers (`org_for_*` /
`targets_for_org`) are called only from `serve/__init__.py`, `authz.py`, and `operations/reads.py`,
and `DEFAULT_ORG` additionally from `jobs.py` and `server/worker_job.py`. Nothing in the core calls
any of them today, so the move is mechanical, not exploratory:

| Symbol | Today | After the move |
|---|---|---|
| `OrgConfig` | `config.py:352-362` | `bajutsu/serve/orgs.py` |
| `DEFAULT_ORG` | `config.py:390` | `bajutsu/serve/orgs.py` |
| `org_for_user` / `org_for_target` / `org_for_identity` / `targets_for_org` | `config.py:393-429` | `bajutsu/serve/orgs.py`, signature narrowed from `config: Config` to `orgs: dict[str, OrgConfig]` |
| `Config.orgs` field | `config.py:370` | removed |
| `load_config` | `config.py:663` | split into `parse_config_dict` (validation) + `load_config` (I/O) |

`_Model` sets `extra="forbid"` (`config.py:44`) as a deliberate typo guard, and it must stay on for
every other field. But `orgs` is different from a typo: the deterministic `run` legitimately reads an
org-bearing config in the hosted topology (see Motivation), so once the `Config.orgs` field is gone,
`Config.model_validate` would reject that config with "Extra inputs are not permitted: orgs" and break
the run. The core loader therefore treats `orgs` as a **serve-owned key it does not understand and
simply drops**: `parse_config_dict` pops any top-level `orgs` before validation, so the core is fully
org-agnostic (no `OrgConfig`, no org semantics) yet keeps `run` working against an org-bearing config,
while `extra="forbid"` still catches every genuine typo. The core never interprets `orgs`; it only
declines to choke on it.

`load_config` (`config.py:663`) splits into `parse_config_dict(data: dict) -> Config` (pop `orgs`,
then validate) and `load_config(text: str) -> Config` (YAML I/O over `parse_config_dict`). Every
existing core/serve caller of `load_config` keeps its signature.

`bajutsu/serve/orgs.py` gets a new `load_serve_config(text: str) -> tuple[Config, dict[str,
OrgConfig]]`: it parses the raw YAML once, pops `orgs` before handing the remainder to
`parse_config_dict`, and validates the popped block locally into `dict[str, OrgConfig]`. The serve
callers that need the org model switch their `org_for_*`/`targets_for_org` imports from
`bajutsu.config` to `bajutsu.serve.orgs`, and take orgs from `load_serve_config` (threaded through
`serve/helpers.py`'s cached loader) rather than off a `Config.orgs` field.

A local `bajutsu run` / `bajutsu record` keeps calling the plain `load_config` and never constructs
or sees an `OrgConfig` — and a hosted `run` reading an org-bearing config keeps working because the
core loader drops the key rather than rejecting it.

### 4. Give `serve.js` lint and a syntax gate, not a full test framework yet

1,575 lines of untested vanilla JavaScript is itself a scope-creep symptom, but the proportionate
first step is a lighter guardrail: a minimal ESLint flat config (`eslint.config.mjs`) scoped to
`bajutsu/templates/serve.js`, with `node --check` plus eslint wired into `make check` through a
`make lint-js` step. `node --check` runs wherever Node is present (including CI runners); eslint runs
only when it is already resolvable, so the gate never downloads it. Node absence skips with a notice,
the same pattern `make` already uses for `actionlint`, so `make check` still runs anywhere.

A full component/unit-test harness (Jest or Vitest) is explicitly deferred until `serve.js`
accumulates enough branching logic to need one. Recording that trigger here keeps the deferral a
decision, not an oversight.

---

This is scoping and gate work, not a runtime behavior change: `serve`'s behavior toward users is
untouched, only where its code, config, and dependencies are allowed to live. It stays compliant
with all three prime directives by construction — the two new gate checks run alongside `make
check`, not inside `run`.

## Alternatives considered

| Alternative | Verdict | Why |
|---|---|---|
| Do nothing, keep growing `serve` in place | Rejected | The config leak compounds — every future hosting feature (SSO, billing, audit log retention) gets a precedent for landing in `config.py` |
| Have the core loader **reject** a top-level `orgs:` (`extra="forbid"` on the whole document) | Rejected | The hosted `run` legitimately reads an org-bearing `bajutsu.config.yaml` (shipped to the worker as `materials`, or passed via `--config`), so rejecting `orgs:` would break a documented deployment. Dropping the key in the core loader keeps `run` working and stays org-agnostic, while `extra="forbid"` still catches every other typo |
| Strip `orgs:` at the serve→run boundary instead of in the core loader | Rejected | It would keep `extra="forbid"` rejecting `orgs:` core-side, but forces `dispatch.py` to rewrite the config text before shipping it and a *local* server-mode run to materialize a stripped temp file rather than pass `--config` at the real path — and a maintainer running `bajutsu run` by hand against an org-bearing config would still fail. Dropping the key once, in the loader every entry point already shares, is simpler and uniform |
| Split `serve` into a separate distribution (e.g. `bajutsu-serve`) or repository | Rejected for now | The `db`/`oauth` extras and the injected `RedisLike` protocol already isolate `serve`'s heavy dependencies; a split adds real versioning/CI/release cost without solving a problem the gate test doesn't already solve. Revisit if `serve` needs its own release cadence |
| Freeze `serve` feature work until the boundary lands | Rejected | `serve` hardening and hosting (BE-0015, BE-0016, BE-0051) are active, valuable tracks; the boundary should be drawn incrementally alongside them |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Document the `serve`/core boundary rule (host concerns confined to `bajutsu/serve/`)
- [x] Enforce it in the gate. BE-0112's import-linter already forbids the core from importing
      `bajutsu.serve`, so instead of a parallel AST test, a new forbidden contract keeps
      `config.py` / `drivers/` / `runner/` / `scenario/` off the `db`/`oauth` extras
      (`include_external_packages` sees the external import), via `make lint-imports` in `make check`
- [x] Move `OrgConfig` / `DEFAULT_ORG` / `org_for_*` / `targets_for_org` into `bajutsu/serve/orgs.py`
      (signatures narrowed to `dict[str, OrgConfig]`), splitting `load_config` into `parse_config_dict`
      + `load_config`, and add `load_serve_config`
- [x] Core loader **drops** a top-level `orgs:` (keeps `run` reading an org-bearing config working;
      `extra="forbid"` still catches other typos), and serve consumers take orgs from
      `load_serve_config` rather than `Config.orgs`
- [x] Wire `node --check` + a minimal ESLint config (`eslint.config.mjs`) for
      `bajutsu/templates/serve.js` into the gate via a `make lint-js` step in `make check`

Log:

- [#665](https://github.com/bajutsu-e2e/bajutsu/pull/665) — Draw the core/`serve` boundary: move the org model to `bajutsu/serve/orgs.py`, drop
  `orgs:` in the core loader, add the db/oauth import-linter contract, document the rule in
  `architecture.md` (both languages), and add the `serve.js` `lint-js` guardrail.

## References

| Location | What it is |
|---|---|
| `bajutsu/config.py:44` | `_Model`'s `extra="forbid"` — the typo guard that stays on for every other field while the loader drops `orgs` |
| `bajutsu/config.py:352` | `OrgConfig`, host-facing multi-tenancy config |
| `bajutsu/config.py:370` | `Config.orgs` field |
| `bajutsu/config.py:390-429` | `DEFAULT_ORG`, `org_for_user` / `org_for_target` / `org_for_identity` / `targets_for_org` |
| `bajutsu/config.py:663` | `load_config`, the seam to split into `parse_config_dict` + `load_config` (the latter dropping a top-level `orgs`) |
| `bajutsu/serve/__init__.py`, `authz.py`, `operations/reads.py` | the callers of the org helpers (`org_for_*` / `targets_for_org`); `DEFAULT_ORG` is additionally used by `jobs.py` and `server/worker_job.py` — all already under `bajutsu/serve/` |
| `bajutsu/serve/helpers.py:94-125` | `_load_config_cached` / `load_config_file`, the cached serve-side loader the org consumers reach through — where `load_serve_config` is threaded so orgs travel with the cached `Config` |
| `bajutsu/serve/operations/dispatch.py:117`, `bajutsu/cli/_shared.py:192`, `bajutsu/mcp/tools.py:21` | the `run` path that reads an org-bearing config in the hosted topology — why the core loader must drop `orgs` rather than reject it |
| `bajutsu/serve/server/logbus.py`, `sessions.py` | existing `RedisLike`-protocol injection, keeping `redis` out of a hard dependency |
| `pyproject.toml:39-42` | the `db` (SQLAlchemy/Alembic/psycopg) and `oauth` (Authlib) optional extras that already keep those dependencies out of the core install |

- Related: BE-0011 (local web UI serve), BE-0051 (serve hardening for hosting), BE-0015 (web UI
  public hosting), BE-0016 (web UI self-hosting)
- Originates from the 2026-07-02 codebase-analysis report (design).
