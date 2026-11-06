**English** · [日本語](BE-0309-serve-postgres-ci-lane-ja.md)

# BE-0309 — Real-Postgres CI lane for the serve database layer

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0309](BE-0309-serve-postgres-ci-lane.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0309") |
| Implementing PR | [#1347](https://github.com/bajutsu-e2e/bajutsu/pull/1347), [#1353](https://github.com/bajutsu-e2e/bajutsu/pull/1353), [#1359](https://github.com/bajutsu-e2e/bajutsu/pull/1359), [#1362](https://github.com/bajutsu-e2e/bajutsu/pull/1362), [#1365](https://github.com/bajutsu-e2e/bajutsu/pull/1365) |
| Topic | Verification & coverage |
| Related | [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a real-Postgres CI lane alongside the existing SQLite one for `serve`'s database
layer.

The layer already avoids the worst version of the gap.

`tests/serve/test_db_migrations.py` runs real Alembic `upgrade`/`downgrade` against a throwaway
SQLite file. The test does not bypass migrations with `Base.metadata.create_all()`.

No test runs the migrations against Postgres, though. Postgres is the dialect that the hosted,
multi-tenant deployment targets. The ORM/repository layer above the migrations has the same gap:
every other `serve` DB test uses `create_engine("sqlite://")`.

## Motivation

SQLite and Postgres diverge in the places where a migration is most likely to hide a bug: JSON and
array column types, server-side defaults, and the constraint names that each dialect generates
differently for the same declarative model. A migration can upgrade and downgrade cleanly against
SQLite and still fail against Postgres.

The codebase already knows about this divergence. The FK-enforcement test in
`test_db_repository.py` targets what its own comment calls "the Postgres-vs-SQLite gap". A
migration (`0010_run_project_fk_set_null.py`, which branches on `dialect.name == "postgresql"`)
and the ORM models (a `JSONB` variant on Postgres) already carry dialect-specific code.

None of that dialect-specific code has ever run against a real Postgres instance in CI. The
authors wrote and reviewed it against SQLite alone.

The gap sits inside `serve`'s DB layer, so no user hits it until they run a migration against
their own hosted Postgres instance. That moment is the worst time to discover a dialect-specific
bug.

## Detailed design

This section stays at proposal altitude. The work is MECE along the units below.

- **A dedicated Postgres CI job, not an addition to `check`.** `ci.yml` defines one test job,
  `check`, and `main` already requires it. A `postgres` service in `check` would gate every PR on
  container startup and on any dialect-specific failure from day one. No non-gating stage would
  be achievable. Instead, add a new job that mirrors BE-0282's `network (playwright)` job in
  `web-e2e.yml`, and leave the existing required job unchanged. The new job uses a `postgres`
  service container, the standard GitHub Actions pattern. That job then has a real, ephemeral
  Postgres instance while it runs.
- **Run the existing migration test suite against the new job too.** Parametrize (or duplicate)
  the upgrade/downgrade tests in `test_db_migrations.py` to run against both SQLite and the new
  Postgres service. Reuse the same assertions instead of writing a second spec.
- **Run the DB-touching suite across `tests/serve/` against Postgres.** About two dozen files call
  `create_engine("sqlite://")`, among them `test_db_models.py`, `test_db_repository.py`, and the
  persistence tests in `test_oauth.py`. Dialect-specific column and constraint behavior can
  surface in these files even when the migration succeeds.
- **Non-gating first.** Land the new job as a CI signal, following the precedent in
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md). A
  first-time Postgres service container can hit its own teething problems: image-pull hiccups,
  connection timing, and a dialect edge case that the SQLite suite has never exercised. These
  problems are independent of ordinary flakiness. The job earns required status once it proves
  stable.

## Alternatives considered

- **Trust SQLite coverage, since Alembic targets both dialects from one migration file.** One
  migration file that targets two dialects is why dialect-specific behavior can diverge unnoticed.
  Alembic emits different SQL per dialect from the same Python. Running against the real target
  dialect is the sole way to observe what Alembic emits.
- **Test Postgres compatibility by manual review of migration diffs instead of a CI lane.** Manual
  review catches obvious cases, such as a misused explicit `postgresql.JSON`. Review misses
  implicit cases: a default server-side constraint name that collides, or a type coercion that
  behaves differently. A real database catches that class of bug at no extra cost.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add a dedicated Postgres CI job with a service container (not a change to `check`).
- [x] Run the migration upgrade/downgrade tests and the wider DB-touching test suite against it.
  *(Slice 1 ran the migration tests against Postgres. Slice 2 added the three highest-risk
  DB-touching suites — `test_db_models.py`, `test_db_repository.py`, and `test_oauth.py`'s
  persistence tests — behind a shared `serve_engine` fixture. The fixture retrofits the per-test
  isolation that a shared Postgres needs. Slice 3 moved the remaining eighteen files onto the same
  fixture, so every serve DB test that builds its schema on an in-memory engine now runs against
  both dialects.)*
- [x] Wire it into CI as a non-gating signal, promote to required once stable.
  *(The non-gating signal landed first. Slice 4 gave the lane an always-reporting `serve db (postgres)`
  gate, which mirrors the required `E2E (web)` aggregator. A path-skip then no longer leaves the
  check pending forever, so the check can be required. A maintainer then added the check to the
  "Require code review" branch ruleset, so `serve db (postgres)` is now a required check. That
  final step is a repository setting outside any code PR.)*

### Log

- Slice 1 — `serve-db.yml` stands up a `postgres:16` service container and reruns the
  upgrade/downgrade assertions of `test_db_migrations.py` against real Postgres. The tests are
  parametrized over both dialects: SQLite in the fast gate, Postgres behind the `postgres` marker
  in the lane. The same assertions serve both dialects, and the lane runs `-m postgres -n0`.
  Migration 0010's `postgresql` FK branch and the JSONB column variants get their first
  real-Postgres coverage. The lane is non-gating for now, per BE-0282's precedent.
- Slice 2 — a shared `tests/conftest.py` adds the `serve_engine` fixture. The fixture parametrizes
  a requesting test over both dialects: SQLite in the gate, the `postgres`-marked parameter in the
  lane. It also gives the shared Postgres service a per-test schema reset, which SQLite got for
  free from a throwaway in-memory database. The three highest-risk suites now request the fixture:
  `test_db_models.py`, `test_db_repository.py`, and the persistence tests in `test_oauth.py`. The
  lane therefore runs the whole `tests/serve` directory by marker
  (`pytest tests/serve -m postgres -n0`), and a later suite joins automatically once it adopts the
  fixture. Running against real Postgres surfaced several repository tests that inserted
  `runs`/`projects` without their parent org rows. Those tests relied on SQLite's FKs-off default,
  and they now seed the org to respect the referential integrity that Postgres enforces. One test
  asserts the FKs-off dangling-`project_id` behavior and stays SQLite-only. A sibling test already
  covers its Postgres `ON DELETE SET NULL` counterpart.
- Slice 3 — the remaining eighteen DB-touching `tests/serve` files adopt the `serve_engine`
  fixture. Every serve DB test that builds its schema on an in-memory engine, not just the three
  highest-risk files, now runs against both in-memory SQLite in the gate and real Postgres in the
  lane. Real Postgres surfaced the same foreign-key divergence as slice 2. The secret and
  provider-settings stores insert rows keyed by `org_id`, and the secret store also writes
  `updated_by`, a `users.id` foreign key. SQLite's foreign-keys-off default let those keys dangle.
  Each store's test helper now seeds the parent org (and writer) rows, so both dialects hold. A
  single schema-less error-path test in `test_db_run_listing.py` stays SQLite-only by design. The
  workflow needed no change: the lane already selects the directory by marker
  (`pytest tests/serve -m postgres -n0`), so each newly retrofitted file joined the lane
  automatically.
- Slice 4 — `serve-db.yml` is restructured into the always-reporting shape that a required check
  needs. The workflow now triggers on every PR (and the merge queue) instead of using a
  workflow-trigger `paths:` filter. A `changes` job (`dorny/paths-filter`) decides whether the
  heavy `postgres` job runs. A new `if: always()` gate job named `serve db (postgres)` translates
  the work job's skip into a pass and the work job's failure into a red. The required `E2E (web)`
  aggregator uses the same pattern, so a path-skip never leaves a required check pending. The
  slice does not itself make the check required. That is a repository ruleset change that a
  maintainer makes once the gate has proven stable. The slice merely makes the check safe to
  require.
- Required — a maintainer added `serve db (postgres)` to the required status checks of the
  "Require code review" branch ruleset. The lane now gates the merge like `check` and the
  `E2E (…)` aggregators. This step completes the item. It is a repository setting rather than
  code, so it ships outside any PR, and this note records that it happened.

## References

- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
- `bajutsu/serve/server/migrations/`, `tests/serve/test_db_migrations.py`,
  `tests/serve/test_db_models.py`, `tests/serve/test_db_repository.py`, `tests/serve/test_oauth.py`,
  `tests/serve/test_import_guard.py`, `.github/workflows/ci.yml`, `.github/workflows/web-e2e.yml`
