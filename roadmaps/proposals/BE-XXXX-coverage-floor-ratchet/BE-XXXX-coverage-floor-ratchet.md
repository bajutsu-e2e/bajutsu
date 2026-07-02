**English** · [日本語](BE-XXXX-coverage-floor-ratchet-ja.md)

# BE-XXXX — Ratchet the coverage floor

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-coverage-floor-ratchet.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Development infrastructure (contributor workflow) |
<!-- /BE-METADATA -->

## Introduction

The deterministic gate (`make check`) enforces a branch-coverage floor via `--cov-fail-under` in
the `Makefile`. That floor is a ratchet: BE-0067 raised it from 85% to 87% when branch coverage
landed, expecting it to move again as coverage improves. It has not moved since. This item closes
that gap by raising the floor to match the coverage the suite already achieves.

## Motivation

The floor is currently 87%, but the suite's actual measured branch coverage is 88.8% — 1.8
points of slack between what the gate demands and what the codebase already delivers. That gap is
pure risk with no offsetting benefit: a change that deletes tests or adds untested branches can
give back up to 1.8 points before the gate notices, so a real regression can land, merge, and sit
on `main` silently until coverage happens to dip below 87% for an unrelated reason and someone has
to untangle which change actually caused it. A floor that trails reality stops acting as a floor.
Ratcheting it up to the measured level costs nothing today — every test that provides that 88.8%
already exists — and it converts 1.8 points of dead slack into an enforced regression guard. This
is a size-S change: one number in the `Makefile`, verified against a fresh coverage run.

## Detailed design

The work is a single unit, sequenced after the "cover the CLI command layer" item lands:

- **Wait for "cover the CLI command layer" to merge.** That item adds unit tests for `doctor`,
  `record`, and `run`, which will raise measured coverage further. Ratcheting now and again right
  after would just mean touching the same line twice; doing it once after that item lands captures
  the full gain in one step.
- **Raise `--cov-fail-under` in the `Makefile`.** Run `make test` (or `make check`) after that item
  merges, read the actual branch-coverage percentage from the pytest-cov summary, and set the new
  floor to that measured value (rounded down to avoid a gate that fails on ordinary run-to-run
  noise). Update the `--cov-fail-under=87` line at `Makefile:69` accordingly.
- **Confirm the gate still passes at the new floor.** Re-run `make check` once the floor is raised
  to confirm the new number is both accurate and stable, so the ratchet doesn't immediately start
  failing CI on unrelated PRs.

## Alternatives considered

- **Ratchet immediately, before the CLI-command-layer item lands.** Rejected: that item is already
  in flight and will move coverage again almost immediately, so ratcheting first would mean doing
  the same one-line change twice for no benefit. Sequencing after it captures both gains in a
  single step.
- **Set the floor exactly to the measured percentage.** Rejected: pytest-cov's branch-coverage
  percentage can shift by a fraction of a point between otherwise-identical runs (e.g. platform
  differences in which branches a conditional import takes). Rounding down leaves enough headroom
  that the gate doesn't flap on noise while still closing the bulk of the slack.
- **Add a CI check that fails when the floor drifts far from actual coverage, instead of ratcheting
  by hand.** Rejected as disproportionate for a size-S fix: it would add a new script and a new
  failure mode to the gate to solve a problem that a periodic manual ratchet already solves at
  near-zero cost. Worth reconsidering only if the floor is left to drift repeatedly.
- **Leave the floor at 87%.** Rejected: this is the status quo the finding identifies as the
  problem — unused slack that lets a real regression through undetected.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Wait for "cover the CLI command layer" to merge and re-measure coverage.
- [ ] Raise `--cov-fail-under` in the `Makefile` to the new measured floor.
- [ ] Confirm `make check` passes cleanly at the new floor.

No PR has landed yet.

## References

- [`Makefile:69`](../../../Makefile) — the `--cov-fail-under=87` line this item raises.
- [`pyproject.toml`](../../../pyproject.toml) — `[tool.coverage.run] branch = true`, the branch-
  coverage mode the floor is measured against.
- [BE-0067 — Code-quality gate hardening](../../implemented/BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)
  — introduced branch coverage and the 87% floor this item ratchets further.
- Originates from the 2026-07-02 codebase-analysis report (technical debt).
