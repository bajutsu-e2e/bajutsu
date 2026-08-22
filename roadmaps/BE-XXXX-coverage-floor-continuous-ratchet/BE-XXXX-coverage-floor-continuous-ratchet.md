**English** · [日本語](BE-XXXX-coverage-floor-continuous-ratchet-ja.md)

# BE-XXXX — Turn the coverage floor into a continuous ratchet

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-coverage-floor-continuous-ratchet.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

The `make check` coverage floor (`--cov-fail-under` in the `Makefile`) only ever moves when someone
measures the suite, writes tests for whatever is weak, and edits the number by hand — the sequence
[BE-0117](../BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet.md) ran once. This item
turns that sequence into two standing checks instead of a one-time exercise: an advisory nudge when
measured coverage has drifted well above the floor, and a hard per-file floor that a change can
never lower, addressing the per-module blind spot BE-0067 flagged and left for later ("a heavier
stance… per-file coverage floors… out of scope; noted as future steps").

## Motivation

A current run of the fast suite (`pytest -m "not web and not ondevice"` with branch coverage)
measures 93% total coverage against a `--cov-fail-under=89` floor — a 4-point gap between what the
gate demands and what the suite already delivers. That gap is exactly the room a regression could
use: a change that drops coverage by 3 points still passes the gate, and nothing today prompts
anyone to notice the drop or raise the floor to close it back up.

The global percentage also still hides the weak spot [BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)'s
branch-coverage change did not fully address: a single number over the whole package. Per-file
branch coverage today ranges from 65% (`bajutsu/cli/commands/doctor.py`, the lowest file above a
handful of statements — `__main__.py`'s four never-imported lines sit at 0%) up to 100% across 136
fully covered files; other files under 80% include `bajutsu/serve/operations/_common.py` (69%),
`bajutsu/report/richtext.py` (72%), `bajutsu/runner/mailbox.py` (73%), and
`bajutsu/serve/upload_artifacts.py` (76%). None of that is visible from the single `TOTAL` line
`make check` gates on — a file could drop from 65% to 40% and the global floor would likely still
pass, since the total sits at 93%, well above the floor, and the rest of the codebase absorbs the
loss.

## Detailed design

Two independent checks, both driven by the `coverage.json` the `test` target already writes:

1. **A floor-drift advisory, run from `make lint-pr`.** The floor moves into
   `[tool.coverage.report]`'s `fail_under` key in `pyproject.toml` first — coverage.py honours that
   key natively, so the `Makefile`'s `test` target drops its `--cov-fail-under=89` flag, leaving the
   gate and the advisory one declarative source instead of a value scraped from a shell recipe line
   that a later edit (a new flag, a wrap, a reorder) could silently change. A new script then compares
   `coverage.json`'s `totals.percent_covered` against that `fail_under` value after every `test` run
   and prints a reminder — never fails the build — once the gap passes 2 points, naming the current
   measured percentage and the floor. It runs from `make lint-pr`, the target this repository already
   reserves for advisory checks — the `Makefile`'s own comment on `lint-pr` reads "ADVISORY and
   deliberately NOT in `check`" — rather than from `make check`, whose every other prerequisite fails
   the build on a nonzero exit; a non-blocking step placed there would have to swallow its own exit
   status, hiding the advisory's real failures (a missing `coverage.json`, a crash after a `Makefile`
   edit, a bad parse) while `make check` still reports green.
2. **A per-file floor that only ever rises.** A checked-in snapshot (e.g. `coverage-floors.json`)
   records, per source file with more than a handful of statements, the branch-coverage percentage
   measured the last time this item's check ran, seeded with no margin subtracted — a slack margin
   would hand each file the same room the global floor's 4-point gap hands the whole repository
   today, the exact regression window this item exists to close; `make coverage-floors` (below) is
   already the deliberate escape hatch for a drop a human decides to accept. A new `make check` step
   is check-only — mirroring the `format` / `format-check` split, it fails when a file's current
   measured coverage drops below its recorded floor and never writes to the snapshot. A separate
   `make coverage-floors` target recomputes and rewrites the snapshot to each file's current, higher
   coverage; a human runs it deliberately and commits the result once coverage has risen, the same way
   `make format` is a deliberate, separate step from the check-only gate. `doctor.py` at 65% keeps its
   65%-based floor rather than being forced up to today's global 89%; the point is that it can only go
   up from wherever it starts, never silently back down — and never automatically, mid-`make check`.

Both checks read `coverage.json` after the existing `test` target runs; neither reruns the suite or
adds a second coverage collection pass.

## Alternatives considered

- **Raise the global floor to 91% or 92% now, as a one-time bump** — rejected: that repeats
  [BE-0117](../BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet.md)'s pattern exactly,
  leaving the same gap to reopen the next time coverage improves without anyone raising the number
  again. This item's advisory check exists specifically so the next gap gets noticed sooner than
  the next audit.
- **Make the per-file floor a hard ceiling as well as a hard floor** (fail if a file's coverage
  rises without the snapshot being updated in the same PR) — rejected: that would fail a
  contributor's PR for the unrelated reason of having improved coverage, which punishes exactly the
  behavior this item wants to encourage. The floor only ever blocks a drop, never blocks a rise.
- **Set every file's initial per-file floor to today's global 89%** — rejected: that would fail
  `make check` immediately for every file already under 89% (`doctor.py` and the rest listed in
  *Motivation*), turning this item's landing PR into an unrelated obligation to fix every weak file
  first. Seeding each file's floor from its own current measurement keeps the landing PR additive.
- **Make the floor-drift advisory a hard failure once the gap passes 2 points** — rejected: a hard
  failure would block an unrelated PR that happens to add well-tested code, purely for the "offense"
  of widening the gap further. An advisory keeps the signal without blocking work that is not itself
  the problem.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Move the coverage floor into `[tool.coverage.report]`'s `fail_under` in `pyproject.toml`;
  drop `--cov-fail-under` from the `Makefile`'s `test` target.
- [ ] Add the floor-drift advisory script and wire it into `make lint-pr` (advisory).
- [ ] Generate the initial `coverage-floors.json` snapshot from today's measured per-file branch
  coverage.
- [ ] Add the per-file floor check to `make check` (blocking, check-only — never writes).
- [ ] Add the separate `make coverage-floors` rewrite target that a human runs and commits once
  coverage has risen.
- [ ] Document both checks in [CLAUDE.md](../../CLAUDE.md)'s gate list and in
  [docs/ai-development.md](../../docs/ai-development.md).

## References

- [BE-0067 — Code-quality gate hardening](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)
  — added branch coverage and named per-file floors as a future step this item now takes up.
- [BE-0117 — Cover the rest of the CLI command layer, then ratchet the coverage floor](../BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet.md)
  — the one-time version of the sequence this item turns into a standing mechanism.
- [Makefile](../../Makefile), [pyproject.toml](../../pyproject.toml) — the `test` target and
  `[tool.coverage.run]` config both new checks read `coverage.json` from.
