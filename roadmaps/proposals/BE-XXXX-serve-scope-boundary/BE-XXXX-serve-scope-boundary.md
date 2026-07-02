**English** · [日本語](BE-XXXX-serve-scope-boundary-ja.md)

# BE-XXXX — Bound serve scope and keep host concerns out of shared config

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-scope-boundary.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Hosting the web UI (cloud / self-hosted) |
<!-- /BE-METADATA -->

## Introduction

`serve` has grown from a local preview server into the largest, fastest-growing subsystem in the
repository, and its host-facing concerns (organizations, roles) have started leaking into the
shared `config.py` that the CLI's deterministic core also depends on. This proposal draws an
explicit boundary around `serve`'s scope and keeps multi-tenant hosting concerns out of the config
schema the rest of the tool shares.

## Motivation

`bajutsu/serve/` (including its `server/` subpackage) is 6,920 lines of Python across roughly 30
modules, plus `bajutsu/templates/serve.js` at 1,575 lines of vanilla JavaScript with no build step
and no test harness — together on the order of a fifth of the whole repository (`bajutsu/` totals
about 30,000 lines of Python). `bajutsu/serve/operations.py` alone is 1,376 lines (tracked
separately by the sibling `split-serve-operations-module` proposal). The subsystem carries SQLAlchemy
models, Alembic migrations, OAuth, RBAC, an object store, and a Redis-backed worker — infrastructure
a local CLI tool's core has no reason to know about.

The growth is not confined to `bajutsu/serve/`. `bajutsu/config.py:340` defines `OrgConfig`, a model
for "one tenant under `orgs.<name>` (BE-0015 multi-tenancy)", and `Config` itself carries an
`orgs: dict[str, OrgConfig]` field (`config.py:357`) plus resolution helpers (`org_for_user`,
`org_for_target`, `org_for_identity`, `targets_for_org`, `config.py:380-415`). None of this is
meaningful to a solo developer running `bajutsu run` against a local Simulator — it exists purely to
support `serve`'s hosted, multi-tenant deployment (BE-0015). Because `Config` is the schema every
entry point parses, a hosting concern has become a permanent tax on the shared config surface,
directly contradicting the "app-agnostic" and "keep the deterministic core unchanged" premise that
`Effective`/`Config` are supposed to serve.

Severity: High. This is architectural drift, not a bug — but each new hosting feature (BE-0015
public hosting, BE-0016 self-hosting, BE-0051 hardening) makes the boundary harder to draw
retroactively, and the config schema is the part of the codebase every backend and every target
depends on.

## Detailed design

The boundary is drawn with one concrete move plus a machine-checked rule, not a size cap or a
package split. Evidence gathered from the current tree grounds each step:

1. **Document the rule.** Add a short architecture note (in `docs/`, mirrored in `docs/ja/`)
   stating it plainly: `bajutsu/config.py`, `bajutsu/drivers/`, `bajutsu/runner/`, and
   `bajutsu/scenario/` stay host-agnostic — no organization, role, tenancy, or billing concept may
   enter them, and none of the `db` (SQLAlchemy/Alembic/psycopg) or `oauth` (Authlib) extras may be
   imported there. `bajutsu/serve/` (and only `bajutsu/serve/`) owns hosting concerns. The note
   points at the enforcement test below instead of asking reviewers to hold the rule in their head.
2. **Enforce it with a gate test, not a size ceiling.** The boundary already holds today — a check
   of every core module finds zero imports of `bajutsu.serve` or of the `db`/`oauth` extras, and
   `bajutsu/serve/server/logbus.py` and `sessions.py` already take their Redis client through an
   injected `RedisLike` protocol rather than importing `redis` directly, so nothing in `serve` even
   requires a hard Redis dependency. Add `tests/test_serve_boundary.py`: walk the AST of
   `bajutsu/config.py`, `bajutsu/drivers/**`, `bajutsu/runner/**`, and `bajutsu/scenario/**`, and
   fail if any of them imports `bajutsu.serve` or a `db`/`oauth`-extra package. Running this in
   `make check` turns "serve stays bounded" into a regression the gate catches, so no separate
   installable distribution (e.g. `bajutsu-serve`) is needed to get isolation — the isolation is
   already dependency-level, this just makes it permanent.
3. **Move `OrgConfig` and the org helpers into `bajutsu/serve/orgs.py`.** Every current caller of
   `OrgConfig`, `DEFAULT_ORG`, `org_for_user`, `org_for_target`, `org_for_identity`, and
   `targets_for_org` already lives under `bajutsu/serve/` (`serve/__init__.py`, `authz.py`,
   `jobs.py`, `operations.py`, `server/worker_job.py`) — nothing in the core calls them today, so
   the move is mechanical, not exploratory:
   - Relocate `OrgConfig` (`config.py:339-348`), `DEFAULT_ORG` (`config.py:376-378`), and
     `org_for_user` / `org_for_target` / `org_for_identity` / `targets_for_org`
     (`config.py:380-415`) verbatim into a new `bajutsu/serve/orgs.py`, narrowing their signatures
     from `config: Config` to `orgs: dict[str, OrgConfig]` (all four only ever read
     `config.orgs`).
   - Drop the `orgs` field from `Config` (`config.py:357`). `_Model` sets `extra="forbid"`
     (`config.py:27`) as a deliberate typo guard, so once the field is gone `Config.model_validate`
     rejects any YAML that still has a top-level `orgs:` key — `serve` can no longer hand it the
     raw document unmodified.
   - Split `load_config` (`config.py:649-652`) into `parse_config_dict(data: dict) -> Config`
     (validation only) and `load_config(text: str) -> Config` (`_yaml.safe_load` then
     `parse_config_dict`) — a two-line refactor that gives `serve` a seam without duplicating YAML
     parsing.
   - Add `bajutsu/serve/orgs.py::load_serve_config(text: str) -> tuple[Config, dict[str, OrgConfig]]`:
     parse the raw YAML once, pop `orgs` before handing the remainder to `parse_config_dict`, and
     validate the popped block locally. `serve/__init__.py`, `authz.py`, `jobs.py`, and
     `operations.py` switch their `org_for_*`/`load_config` imports from `bajutsu.config` to
     `bajutsu.serve.orgs`. A local `bajutsu run` / `bajutsu record` keeps calling the plain
     `load_config` and never constructs or sees an `OrgConfig`.
4. **Give `serve.js` lint and a syntax gate, not a full test framework yet.** 1,575 lines of
   untested vanilla JavaScript is itself a scope-creep symptom, but the proportionate first step is
   a lighter guardrail: add a minimal ESLint flat config scoped to `bajutsu/templates/serve.js` and
   wire `node --check` plus `npx eslint` into `make lint`, skipped with a notice when Node isn't
   present — the same self-healing pattern `make` already uses for `actionlint`. A full
   component/unit-test harness (Jest or Vitest) is explicitly deferred until `serve.js` accumulates
   enough branching logic to need one; recording that trigger here keeps the deferral a decision
   rather than an oversight.

This is scoping and gate work, not a runtime behavior change: `serve`'s behavior toward users is
untouched — only where its code, config, and dependencies are allowed to live changes — so it stays
compliant with all three prime directives by construction (the two new gate checks run alongside
`make check`, not inside `run`).

## Alternatives considered

- **Do nothing and keep growing `serve` in place.** Cheapest short-term, but the config leak
  compounds: every future hosting feature (SSO, billing, audit log retention) would have a precedent
  for landing in `config.py`, permanently coupling the CLI's schema to SaaS concerns.
- **Split `serve` into a separate installable distribution (e.g. `bajutsu-serve`) or a separate
  repository.** Rejected for now. The isolation a split buys is already achieved at the dependency
  level (the `db`/`oauth` extras and the injected `RedisLike` protocol keep `serve`'s heavy
  dependencies out of the core install), so a package or repo split would add real
  versioning/CI/release coordination cost without solving a problem the gate test above doesn't
  already solve. Worth revisiting only if `bajutsu/serve/` needs its own release cadence
  independent of the CLI core.
- **Freeze `serve` feature work until the boundary work lands.** Rejected — `serve` hardening and
  hosting (BE-0015, BE-0016, BE-0051) are active, valuable tracks; the boundary should be drawn
  incrementally alongside them, not by blocking them.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Document the `serve`/core boundary rule (host concerns confined to `bajutsu/serve/`)
- [ ] Add `tests/test_serve_boundary.py`, an AST-based import check wired into `make check`
- [ ] Move `OrgConfig` / `DEFAULT_ORG` / `org_for_*` helpers into `bajutsu/serve/orgs.py`, splitting
      `load_config` into `parse_config_dict` + `load_config`
- [ ] Wire `node --check` + a minimal ESLint config for `bajutsu/templates/serve.js` into `make lint`

No PR has landed yet.

## References

- `bajutsu/config.py:27` — `_Model`'s `extra="forbid"`, the typo guard that makes dropping `orgs`
  from `Config` a breaking change for any YAML that still declares it unless `serve` pops the key
  first
- `bajutsu/config.py:340` — `OrgConfig`, host-facing multi-tenancy config
- `bajutsu/config.py:357` — `Config.orgs` field
- `bajutsu/config.py:376-415` — `DEFAULT_ORG`, `org_for_user` / `org_for_target` /
  `org_for_identity` / `targets_for_org`
- `bajutsu/config.py:649-652` — `load_config`, the seam to split into `parse_config_dict` +
  `load_config`
- `bajutsu/serve/__init__.py`, `authz.py`, `jobs.py`, `operations.py`, `server/worker_job.py` —
  every current caller of the org helpers; all already under `bajutsu/serve/`
- `bajutsu/serve/server/logbus.py`, `sessions.py` — existing `RedisLike`-protocol injection, the
  pattern that already keeps `redis` out of a hard dependency
- `pyproject.toml:39-42` — the `db` (SQLAlchemy/Alembic/psycopg) and `oauth` (Authlib) optional
  extras that already keep those dependencies out of the core install
- `bajutsu/serve/` (6,920 lines of Python) and `bajutsu/templates/serve.js` (1,575 lines,
  untested) — the subsystem's current footprint
- `bajutsu/serve/operations.py` (1,376 lines) — tracked separately by the sibling
  `split-serve-operations-module` proposal
- Related: BE-0011 (local web UI serve), BE-0051 (serve hardening for hosting), BE-0015 (web UI
  public hosting), BE-0016 (web UI self-hosting)
- Originates from the 2026-07-02 codebase-analysis report (design).
