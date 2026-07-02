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

The direction is to make the `serve`/core boundary explicit rather than to prescribe one exact
mechanical split up front; work breaks into three independent tracks:

1. **Name and document the boundary.** Add a short architecture note (in `docs/`) stating the rule
   plainly: `bajutsu/config.py`, `bajutsu/drivers/`, `bajutsu/runner/`, `bajutsu/scenario/`, and the
   other Tier-1/Tier-2 modules stay host-agnostic — no organization, role, tenancy, or billing
   concept may enter them. `bajutsu/serve/` (and only `bajutsu/serve/`) owns hosting concerns. This
   gives reviewers a citable rule instead of relying on ambient judgment PR by PR.
2. **Move host-facing config out of `config.py`.** Relocate `OrgConfig`, the `orgs` field, and the
   `org_for_*`/`targets_for_org` helpers (`config.py:340-415`) into a `serve`-owned module (e.g.
   `bajutsu/serve/orgs.py`), which loads and layers org data on top of the core `Config` at the
   `serve` boundary instead of inside the shared model. A local `bajutsu run` should never construct
   or see an `OrgConfig`.
3. **Cap or carve out `serve`'s growth.** Evaluate, and record a decision on, whether `serve`
   continues to live in the `bajutsu` package with an enforced size/complexity ceiling, or is split
   into a separate installable distribution (e.g. `bajutsu-serve`) that depends on the core package.
   Either choice must keep `make check`'s deterministic-core gate ignorant of `serve`'s dependencies
   (SQLAlchemy, Alembic, Redis client, OAuth libraries) — those stay optional extras, never
   requirements of the CLI's core install.
4. **Give `serve.js` a test harness commensurate with its size.** 1,575 lines of untested vanilla
   JavaScript is itself a scope-creep symptom; establishing at least a minimal test/build setup (or
   explicitly deciding to keep it framework-free with lighter guardrails, e.g. lint + smoke tests) is
   part of bounding `serve`'s footprint, tracked here since it is part of the same boundary story
   rather than the `operations.py` facade split.

This is scoping work, not a behavior change: it does not alter what `serve` does, only where its
code and config concerns live, so it stays compliant with all three prime directives by construction
(no runtime behavior of `run`/CI is touched).

## Alternatives considered

- **Do nothing and keep growing `serve` in place.** Cheapest short-term, but the config leak
  compounds: every future hosting feature (SSO, billing, audit log retention) would have a precedent
  for landing in `config.py`, permanently coupling the CLI's schema to SaaS concerns.
- **Split `serve` into a separate repository entirely.** Maximizes isolation but adds real
  cross-repo coordination cost (versioning, CI, release) for a codebase where `serve` still needs to
  track core `Config`/`Driver` changes closely; a separate *distribution* built from the same
  monorepo (option 3 above) captures most of the isolation benefit without that overhead.
- **Freeze `serve` feature work until the split lands.** Rejected — `serve` hardening and hosting
  (BE-0015, BE-0016, BE-0051) are active, valuable tracks; the boundary should be drawn incrementally
  alongside them, not by blocking them.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Document the `serve`/core boundary rule (host concerns confined to `bajutsu/serve/`)
- [ ] Move `OrgConfig` / `orgs` / `org_for_*` helpers out of `bajutsu/config.py` into a `serve`-owned module
- [ ] Decide and record whether `serve` gets a growth ceiling or becomes a separate distribution
- [ ] Add a minimal test/build harness for `bajutsu/templates/serve.js` (or record a lighter-guardrail decision)

No PR has landed yet.

## References

- `bajutsu/config.py:340` — `OrgConfig`, host-facing multi-tenancy config
- `bajutsu/config.py:357` — `Config.orgs` field
- `bajutsu/config.py:380-415` — `org_for_user` / `org_for_target` / `org_for_identity` / `targets_for_org`
- `bajutsu/serve/` (6,920 lines of Python) and `bajutsu/templates/serve.js` (1,575 lines,
  untested) — the subsystem's current footprint
- `bajutsu/serve/operations.py` (1,376 lines) — tracked separately by the sibling
  `split-serve-operations-module` proposal
- Related: BE-0011 (local web UI serve), BE-0051 (serve hardening for hosting), BE-0015 (web UI
  public hosting), BE-0016 (web UI self-hosting)
- Originates from the 2026-07-02 codebase-analysis report (design).
