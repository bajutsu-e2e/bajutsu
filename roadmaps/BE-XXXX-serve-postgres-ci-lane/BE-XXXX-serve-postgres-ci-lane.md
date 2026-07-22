**English** · [日本語](BE-XXXX-serve-postgres-ci-lane-ja.md)

# BE-XXXX — Real-Postgres CI lane for the serve database layer

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-postgres-ci-lane.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Hosting the web UI (cloud / self-hosted) |
| Related | [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md) |
<!-- /BE-METADATA -->

## Introduction

`serve`'s DB layer already avoids the worst version of this gap: `tests/serve/test_db_migrations.py`
runs real Alembic `upgrade`/`downgrade` against a real throwaway SQLite file rather than bypassing
migrations with `Base.metadata.create_all()`. What it does not do is run any of that — migrations or
the ORM/repository layer above them — against Postgres, the dialect the hosted, multi-tenant
deployment actually targets. Every other `serve` DB test uses `create_engine("sqlite://")`. This item
adds a real-Postgres CI lane alongside the existing SQLite one.

## Motivation

SQLite and Postgres diverge in exactly the places a migration is most likely to hide a bug: JSON and
array column types, server-side defaults, and the constraint-naming conventions each dialect
generates differently for the same declarative model. A migration that upgrades and downgrades
cleanly against SQLite can still fail against Postgres. The codebase already knows this divergence
exists — `test_db_repository.py`'s FK-enforcement test explicitly targets what its own comment calls
"the Postgres-vs-SQLite gap," and both a migration (`0010_run_project_fk_set_null.py`, branching on
`dialect.name == "postgresql"`) and the ORM models (a `JSONB` variant on Postgres) already carry
dialect-specific code. None of that dialect-specific code has ever run against a real Postgres
instance in CI; it has been written and reviewed against SQLite alone. Because this gap sits entirely
inside `serve`'s DB layer, it never reaches a real user until they run the migration against their
own hosted Postgres instance — which is the worst possible time to discover a dialect-specific bug.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **A Postgres service container in CI.** Add a `postgres` service to the `serve` test job
  (`ci.yml`), the standard GitHub Actions pattern, so a real (if ephemeral) Postgres instance is
  available for the duration of the job.
- **Run the existing migration test suite against it too.** Parametrize (or duplicate)
  `test_db_migrations.py`'s upgrade/downgrade tests to run against both SQLite and the new Postgres
  service, reusing the same assertions rather than writing a second spec.
  Also run the wider DB-touching suite (`test_db_models.py`, `test_db_repository.py`,
  and `test_oauth.py`'s persistence tests) against Postgres, since dialect-specific column/constraint
  behavior can surface there even when the migration itself succeeds.
- **Non-gating first.** Land the new job as CI signal, following the precedent in
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md): a
  first-time Postgres service container can hit its own teething problems (image-pull hiccups,
  connection timing, and a dialect edge case the SQLite-only suite has never exercised) independent of
  ordinary flakiness, so it earns required status only once it proves stable.

## Alternatives considered

- **Trust SQLite coverage, since Alembic targets both dialects from one migration file.** One
  migration file targeting two dialects is exactly why dialect-specific behavior can diverge silently
  — Alembic emits different SQL per dialect from the same Python, and only running against the real
  target dialect observes what it actually emits.
- **Test Postgres compatibility by manual review of migration diffs instead of a CI lane.** Manual
  review catches obvious cases (an explicit `postgresql.JSON` misuse) but not implicit ones (a default
  server-side constraint name colliding, a type coercion that behaves differently) — the class of bug
  a real database catches for free that review does not.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add a Postgres service container to the `serve` CI job.
- [ ] Run the migration upgrade/downgrade tests and the wider DB-touching test suite against it.
- [ ] Wire it into CI as a non-gating signal, promote to required once stable.

## References

- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/serve/server/migrations/`, `tests/serve/test_db_migrations.py`,
  `tests/serve/test_db_models.py`, `tests/serve/test_db_repository.py`, `tests/serve/test_oauth.py`,
  `tests/serve/test_import_guard.py`, `.github/workflows/ci.yml`
