**English** · [日本語](BE-0117-coverage-floor-ratchet-ja.md)

# BE-0117 — Cover the rest of the CLI command layer, then ratchet the coverage floor

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0117](BE-0117-coverage-floor-ratchet.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0117") |
| Implementing PR | [#562](https://github.com/bajutsu-e2e/bajutsu/pull/562) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

A sibling proposal (`roadmaps/proposals/` under the slug `cli-command-coverage`) covers
`doctor.py` / `record.py` / `run.py`, the three CLI command modules the 2026-07-02 codebase
report singled out. It does not cover the rest of `bajutsu/cli/commands/` — seven more modules sit
between 17.6% and 71.9% branch coverage, several of them worse than any module that item touches.
This item adds unit tests for those seven modules, then raises the `make check` coverage floor
(`--cov-fail-under` in the `Makefile`) to lock in both gains.

## Motivation

The CLI command layer is every user's entry point, and coverage across it is uneven. Beyond the
three modules the sibling proposal already targets, measured branch coverage is:

| Module | Coverage | Missing lines |
|---|---|---|
| `bajutsu/cli/commands/lint.py` | 17.6% | 14–37 (the entire `lint()` body) |
| `bajutsu/cli/commands/worker.py` | 23.3% | 31–42, 62–138 (`_post_json`, `_object_store`, `_write_console_log`, and the `worker()` loop's branches) |
| `bajutsu/cli/commands/mcp.py` | 40.0% | 22–31 (transport validation, the `fastmcp` import guard, server startup) |
| `bajutsu/cli/commands/trace.py` | 66.7% | 32–36, 51–53 (the "no run found" and `--explain` error branches) |
| `bajutsu/cli/commands/crawl.py` | 67.4% | 138, 180–195, 212–229, 262–317, 338–356 (option-validation and dispatch branches) |
| `bajutsu/cli/commands/schema.py` | 71.4% | 10, 12 (the whole `schema()` body — never invoked in tests) |
| `bajutsu/cli/commands/audit.py` | 71.9% | 68–69, 79–102, 134, 167–205 (usage-error branches and `_history_audit`) |

None of this needs a Simulator: it is option validation, error-message branches, and small pure
helpers (`_post_json`, `_object_store`, `_write_console_log` in `worker.py`) — exactly the kind of
logic the existing fast (non-E2E) suite already targets elsewhere, and exactly the pattern the
sibling `cli-command-coverage` proposal already uses for `doctor.py` / `record.py` / `run.py`. Once
both proposals land, the branch-coverage floor trails reality by more than the 1.8 points measured
today (currently 87% floor vs. 88.8% measured); ratcheting it up front-loads that slack into an
enforced regression guard instead of leaving it as unused margin a real regression could hide in.

## Detailed design

Work breaks down by module, then a final floor-raise once all test additions land:

- **`lint.py`**: `CliRunner` tests for `lint`'s branches — file not found (exit 1), an unreadable
  file (mock `Path.read_text` to raise `OSError`, exit 1), a scenario with lint errors (exit 1,
  errors echoed), a clean scenario with no `from:` provenance advisory, and a clean scenario that
  does produce one (BE-0044's `provenance_coverage`).
- **`worker.py`**: unit-test the three isolated helpers directly — `_post_json` (a 200 response, a
  non-2xx `HTTPError` response, both with and without a body), `_object_store` (the `ImportError`
  branch returns `None`; the success branch returns the constructed store), and
  `_write_console_log` (no run dir → no-op, empty buffered lines → no-op, writes the joined
  non-`None` lines to `console.log`). For the `worker()` polling loop itself, drive one iteration
  through `_post_json`/`execute_job_spec` mocks with a `side_effect` that raises after the first
  lease+result round trip, asserting the lease/execute/upload/post-result sequence runs once
  without needing a real control plane.
- **`mcp.py`**: `CliRunner` tests for an unsupported `--transport` (exit 2, message names the valid
  choices), the `fastmcp`-not-installed path (mock the `bajutsu.mcp` import to raise `ImportError`,
  exit 2), and the success path for both `stdio` and `sse` (mock `create_server`/`server.run` so
  the command returns instead of blocking).
- **`trace.py`**: `CliRunner` tests for `trace`'s "no run found" branch (missing `run_dir`, and a
  `runs/` root with no matching run) and `_explain`'s error branches (a missing scenario path, and
  a scenario that fails to load via `load_expanded_scenarios`, e.g. invalid YAML).
- **`crawl.py`**: `CliRunner` tests for the option-validation and dispatch branches that don't need
  a live actuator — an unknown `--agent` (exit 2), an unavailable backend/actuator (mock
  `select_actuator` to raise, exit 2), and a missing AI credential when `--dismiss-alerts` or the
  guide needs one (exit 2) — against the `fake` backend, mirroring the pattern the sibling proposal
  uses for `run.py`'s option surface.
- **`schema.py`**: one `CliRunner` test invoking `bajutsu schema` and asserting the output parses as
  JSON and matches `scenario_json_schema()`'s output — the whole command body is currently
  unexercised.
- **`audit.py`**: `CliRunner` tests for the usage-error branches (`--history` combined with
  `--repeat`, `--history` combined with a positional `scenario`, no `scenario` and no `--history`)
  and unit tests for `_history_audit`'s grouping-by-`provenance.scenarioHash` and flaky-verdict
  classification logic against a small synthetic runs directory.
- **Raise `--cov-fail-under` in the `Makefile`.** Once the above lands (alongside the sibling
  `cli-command-coverage` proposal, whichever merges last), run `make test` and read the actual
  branch-coverage percentage from the pytest-cov summary; set the new floor to that value, rounded
  down to avoid a gate that fails on ordinary run-to-run noise. Update the
  `--cov-fail-under=87` line at `Makefile:69` accordingly, then re-run `make check` to confirm the
  new floor is both accurate and stable.

## Alternatives considered

- **Ratchet the floor first, then backfill tests to satisfy it.** Rejected for the same reason the
  sibling `cli-command-coverage` proposal rejects it: ratcheting before the tests exist either
  blocks unrelated work or forces rushed, low-value tests written just to hit a number.
- **Fold this into the `cli-command-coverage` proposal instead of a separate item.** Rejected: that
  proposal's scope (`doctor.py` / `record.py` / `run.py`) already matches the original codebase
  report's finding exactly; broadening it to seven more unrelated modules would make its own
  Detailed design MECE breakdown harder to review as one unit. Keeping them separate, non-
  overlapping proposals lets either land independently, with the floor-raise sequenced after both.
- **Leave the floor at 87% and skip the extra CLI modules.** Rejected: this is the status quo the
  original finding identifies as the problem — unused slack, and (as discovered while scoping this
  item) `lint.py` at 17.6% and `worker.py` at 23.3% are worse than any of the three modules the
  original report named.
- **Set the new floor exactly to the measured percentage.** Rejected: pytest-cov's branch-coverage
  percentage can shift by a fraction of a point between otherwise-identical runs (e.g. platform
  differences in which branches a conditional import takes); rounding down leaves headroom so the
  gate doesn't flap on noise.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit-test `lint.py`'s command branches (not found / unreadable / lint errors / clean with and
      without a provenance advisory)
- [x] Unit-test `worker.py`'s `_post_json`, `_object_store`, `_write_console_log`, and one iteration
      of the `worker()` polling loop
- [x] Unit-test `mcp.py`'s transport validation, the `fastmcp` import guard, and the `stdio`/`sse`
      success paths
- [x] Unit-test `trace.py`'s "no run found" and `_explain` error branches
- [x] Unit-test `crawl.py`'s option-validation and dispatch branches (unknown agent, unavailable
      backend, missing AI credential)
- [x] Add a `CliRunner` test for `schema.py`'s command body
- [x] Unit-test `audit.py`'s usage-error branches and `_history_audit`'s grouping/classification
      logic
- [x] Raise `--cov-fail-under` in the `Makefile` to the new measured floor and confirm `make check`
      passes cleanly

- [#562](https://github.com/bajutsu-e2e/bajutsu/pull/562) — the CLI test additions took `lint.py`
  17.6% → 100%, `worker.py` 23.3% → 97%, `mcp.py` 40% → 100%, `trace.py` 66.7% → 100%, `schema.py`
  71.4% → 100%, and `audit.py` 71.9% → 81% (its remaining gap is the device-only `--repeat`
  execution path); `crawl.py`'s device-free option-validation branches are covered (the rest of the
  command needs a live actuator). Total branch coverage rose to 89.34%, so the `Makefile` floor was
  raised `--cov-fail-under=87` → `89` (the `docs/ci.md` mirror, which had drifted to `85`, and the
  PR-template example were realigned).

## References

- `bajutsu/cli/commands/lint.py:14-37` (`lint`), 17.6% coverage
- `bajutsu/cli/commands/worker.py:30-141` (`_post_json`, `worker`, `_object_store`,
  `_write_console_log`), 23.3% coverage
- `bajutsu/cli/commands/mcp.py:14-31` (`mcp`), 40.0% coverage
- `bajutsu/cli/commands/trace.py:13-54` (`trace`, `_explain`), 66.7% coverage
- `bajutsu/cli/commands/crawl.py:58-...` (`crawl`), 67.4% coverage
- `bajutsu/cli/commands/schema.py:8-12` (`schema`), 71.4% coverage
- `bajutsu/cli/commands/audit.py:38-...` (`audit`, `_history_audit`), 71.9% coverage
- [`Makefile:69`](../../Makefile) — the `--cov-fail-under` line this item raised from 87 to 89.
- [`pyproject.toml`](../../pyproject.toml) — `[tool.coverage.run] branch = true`, the branch-
  coverage mode the floor is measured against.
- [BE-0067 — Code-quality gate hardening](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)
  — introduced branch coverage and the 87% floor this item ratchets further.
- The sibling proposal at the `cli-command-coverage` slug under `roadmaps/proposals/` — covers
  `doctor.py` / `record.py` / `run.py`; this item covers the remaining seven CLI command modules.
- Originates from the 2026-07-02 codebase-analysis report (technical debt), plus additional
  low-coverage modules found while scoping this item.
