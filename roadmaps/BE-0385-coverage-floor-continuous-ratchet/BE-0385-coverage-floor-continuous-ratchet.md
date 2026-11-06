**English** · [日本語](BE-0385-coverage-floor-continuous-ratchet-ja.md)

# BE-0385 — Turn the coverage floor into a continuous ratchet

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0385](BE-0385-coverage-floor-continuous-ratchet.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0385") |
| Implementing PR | [#1789](https://github.com/bajutsu-e2e/bajutsu/pull/1789) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

The coverage floor in `make check` (`--cov-fail-under` in the `Makefile`) moves only when someone
measures the suite, writes tests for whatever is weak, and edits the number by hand.
[BE-0117](../BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet.md) ran that sequence
once. This item turns it into two standing checks instead of a one-time exercise:

- An advisory nudge when measured coverage has drifted well above the floor.
- A hard per-file floor that a change can never lower.

The per-file floor addresses the per-module blind spot that BE-0067 flagged and left for later ("a
heavier stance… per-file coverage floors… out of scope; noted as future steps").

## Motivation

A current run of the fast suite (`pytest -m "not web and not ondevice"` with branch coverage)
measures 93% total coverage against a `--cov-fail-under=89` floor. The gate demands 89% and the suite
already delivers 93%, a 4-point gap. That gap is exactly the room a regression could use. A change
that drops coverage by 3 points still passes the gate, and nothing today prompts anyone to notice
the drop or to raise the floor and close the gap.

The global percentage also hides a weak spot that the branch-coverage change of
[BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md) did not
fully address: it is a single number over the whole package. Per-file branch coverage today ranges
from 65% up to 100%. The lowest file is `bajutsu/cli/commands/doctor.py` at 65%, among the files
above a handful of statements (`__main__.py`'s four never-imported lines sit at 0%). The top of the
range is 100% across 136 fully covered files. Other files under 80% include:

- `bajutsu/serve/operations/_common.py` (69%)
- `bajutsu/report/richtext.py` (72%)
- `bajutsu/runner/mailbox.py` (73%)
- `bajutsu/serve/upload_artifacts.py` (76%)

None of this is visible from the single `TOTAL` line that `make check` gates on. A file could drop
from 65% to 40% and the global floor would likely still pass, since the total sits at 93%, well
above the floor, and the rest of the codebase absorbs the loss.

## Detailed design

Two independent checks, both driven by the `coverage.json` that the `test` target already writes:

**1. A floor-drift advisory, run from `make lint-pr`.**

The floor moves into the `fail_under` key of `[tool.coverage.report]` in `pyproject.toml` first.
coverage.py honours that key natively, so the `Makefile`'s `test` target drops its
`--cov-fail-under=89` flag. The gate and the advisory then share one declarative source. Otherwise
the advisory would scrape a value from a shell recipe line, which a later edit (a new flag, a wrap, a
reorder) could change without anyone noticing.

A new script compares `totals.percent_covered` in `coverage.json` against that `fail_under` value
after every `test` run. Once the gap passes 2 points, it prints a reminder that names the current
measured percentage and the floor. It never fails the build.

The script runs from `make lint-pr`, the target this repository already reserves for advisory checks.
The `Makefile`'s own comment on `lint-pr` reads "ADVISORY and deliberately NOT in `check`". It does
not run from `make check`, whose every other prerequisite fails the build on a nonzero exit. A
non-blocking step placed there would have to hide its own exit status. That would conceal the
advisory's real faults (a missing `coverage.json`, a broken run after a `Makefile` edit, a bad parse)
while `make check` still reports green.

**2. A per-file floor that only ever rises.**

A checked-in snapshot (for example `coverage-floors.json`) records the branch-coverage percentage
measured the last time this item's check ran. It holds one entry per source file with more than a
handful of statements. The seed subtracts no margin. A slack margin would hand each file the same
room that the global floor's 4-point gap hands the whole repository today, the regression window this
item exists to close. `make coverage-floors` (below) is already the deliberate escape hatch for a
drop a human decides to accept.

A new `make check` step is check-only, mirroring the `format` / `format-check` split. It fails when a
file's current measured coverage drops below its recorded floor. It never writes to the snapshot.

A separate `make coverage-floors` target recomputes the snapshot and rewrites it to each file's
current, higher coverage. A human runs it deliberately and commits the result once coverage has
risen, the same way `make format` is a deliberate step separate from the check-only gate.

`doctor.py` at 65% keeps its 65%-based floor rather than rising to today's global 89%. The point is
that every file can only go up from wherever it starts. It can never go back down unnoticed, and
never automatically in the middle of `make check`.

Both checks read `coverage.json` after the existing `test` target runs. Neither reruns the suite or
adds a second coverage collection pass.

## Alternatives considered

- **Raise the global floor to 91% or 92% now, as a one-time bump** — rejected. That repeats
  [BE-0117](../BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet.md)'s pattern
  and leaves the same gap to reopen the next time coverage improves, unless someone raises the
  number again. This item's advisory check exists so the next gap gets noticed sooner than the next
  audit.
- **Make the per-file floor a hard ceiling as well as a hard floor** (fail if a file's coverage
  rises and the same PR leaves the snapshot unchanged) — rejected. That would fail a
  contributor's PR for the unrelated reason of having improved coverage, which punishes the very
  behavior this item wants to encourage. The floor blocks a drop and never blocks a rise.
- **Set every file's initial per-file floor to today's global 89%** — rejected. That would fail
  `make check` at once for every file already under 89% (`doctor.py` and the rest listed in
  *Motivation*). The landing PR of this item would then carry an unrelated obligation to fix every
  weak file first. Seeding each file's floor from its own current measurement keeps the landing PR
  additive.
- **Make the floor-drift advisory a hard failure once the gap passes 2 points** — rejected. A hard
  failure would block an unrelated PR that happens to add well-tested code, purely for the "offense"
  of widening the gap further. An advisory keeps the signal without blocking work that is not itself
  the problem.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Move the coverage floor into `[tool.coverage.report]`'s `fail_under` in `pyproject.toml`;
  drop `--cov-fail-under` from the `Makefile`'s `test` target.
- [x] Add the floor-drift advisory script and wire it into `make lint-pr` (advisory).
- [x] Generate the initial `coverage-floors.json` snapshot from today's measured per-file branch
  coverage.
- [x] Add the per-file floor check to `make check` (blocking, check-only — never writes).
- [x] Add the separate `make coverage-floors` rewrite target that a human runs and commits once
  coverage has risen.
- [x] Document both checks in [CLAUDE.md](../../CLAUDE.md)'s gate list and in
  [docs/ai-development.md](../../docs/ai-development.md).

## References

- [BE-0067 — Code-quality gate hardening](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)
  — added branch coverage and named per-file floors as a future step this item now takes up.
- [BE-0117 — Cover the rest of the CLI command layer, then ratchet the coverage floor](../BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet.md)
  — the one-time version of the sequence this item turns into a standing mechanism.
- [Makefile](../../Makefile), [pyproject.toml](../../pyproject.toml) — the `test` target and
  `[tool.coverage.run]` config both new checks read `coverage.json` from.
