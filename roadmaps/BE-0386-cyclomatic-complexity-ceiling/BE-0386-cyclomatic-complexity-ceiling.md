**English** · [日本語](BE-0386-cyclomatic-complexity-ceiling-ja.md)

# BE-0386 — Enforce a cyclomatic complexity ceiling

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0386](BE-0386-cyclomatic-complexity-ceiling.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0386") |
| Implementing PR | [#1790](https://github.com/bajutsu-e2e/bajutsu/pull/1790) (units 1–2), [#2077](https://github.com/bajutsu-e2e/bajutsu/pull/2077) (units 3–6) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

Nothing in `make check` bounds how large or branchy a single function may grow. The gate reviews
style, types, and security, but not structure. This item closes that gap with two sets of ruff's own
rules, and adds no dedicated complexity tool such as [Radon](https://pypi.org/project/radon/) or its
threshold wrapper [Xenon](https://pypi.org/project/xenon/):

- ruff's built-in `C901` (mccabe cyclomatic complexity);
- a small slice of Pylint's "too many X" rules that ruff already reimplements: `PLR0911`,
  `PLR0912`, and `PLR0915`, which count return statements, branches, and statements.

The ceiling starts loose and ratchets down over subsequent PRs. This is the pattern
[BE-0117](../BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet.md) used for the coverage
floor: cover the worst offenders first, then tighten the number that locks the gain in.

## Motivation

Measuring `bajutsu/` at mccabe's own default ceiling (10) finds 59 functions over it. The
distribution is steep, not flat. Raising the ceiling to 20 drops the count to 13. Two functions sit
far above the rest of that list:

- `_make_handler` ([bajutsu/serve/handler.py:90](../../bajutsu/serve/handler.py)) at complexity 100.
- `make_app` ([bajutsu/serve/server/app.py:122](../../bajutsu/serve/server/app.py)) at 55.

Both are HTTP-handler factories that define many endpoint methods in one function body. Two readings
of mccabe's count are possible: genuine branching inside those bodies, or each nested handler's own
branches folded into the factory's total. The implementation phase needs to answer that question
before deciding whether to refactor either function or exempt it. This item does not resolve the
ambiguity in advance.

The other eleven functions over complexity 20 are spread across the codebase with no shared pattern:

- `crawl` (46, `bajutsu/crawl/core.py:849`)
- `device_pool` (44, `bajutsu/runner/pool.py:71`)
- `_wait` (35, `bajutsu/orchestrator/waits.py:376`)
- `_emit_step` (26, 23, and 36 in the Playwright, uiautomator, and XCUITest codegen modules
  respectively)
- `run_one` (32, `bajutsu/runner/pipeline.py:257`)
- `record` (30, `bajutsu/record.py:469`)
- `_handle_action` (31, `bajutsu/orchestrator/loop.py:1000`)
- `lease` (24, `bajutsu/runner/pool.py:228`)
- `_step_selectors` (21, `bajutsu/analysis/audit.py:94`)

The Pylint-derived function-size rules measure a related but distinct property. They count how many
statements, branches, or `return`s a function accumulates, not how many decision points it has. At
their own defaults they find more findings:

- `PLR0911` (too many returns, default ceiling 6) finds 44.
- `PLR0912` (too many branches, default ceiling 12) finds 24.
- `PLR0915` (too many statements, default ceiling 50) finds 15.

Outside `bajutsu/`, `C901` at a ceiling of 25 is clean. `PLR0911` and `PLR0912` add 5 more findings
(3 `PLR0911`, 2 `PLR0912`) across `tests/`, `demos/`, and `scripts/`. The combined triage under
`ruff check .` is 88 findings, not 83.

Two other Pylint-derived rules in the same refactor tier measure API surface and literal use, not
structure: `PLR0913` (too many arguments, 93 findings) and `PLR2004` (a magic value used in a
comparison, 73 findings). They stay out of this item's scope; see *Alternatives considered*.

## Detailed design

Add `C901`, `PLR0911`, `PLR0912`, and `PLR0915` to `[tool.ruff.lint]`'s `select` list. Set every
threshold explicitly rather than leaving it at its default:

1. **Set `[tool.ruff.lint.mccabe] max-complexity = 25`.** At that ceiling, the 10 highest-complexity
   functions measured today violate it, a small, reviewable list. Each one is triaged during
   implementation. A function whose complexity reflects genuine branching is refactored. A function
   whose complexity does not gets a targeted `# noqa: C901` that names the reason. The
   `_make_handler` / `make_app` factory question above is exactly the kind of case this triage
   resolves.
2. **Set the Pylint-derived thresholds the same way, at `max-returns = 12`, `max-branches = 20`,
   and `max-statements = 80`**, rather than at their defaults of 6 returns, 12 branches, and 50
   statements.
   - A ceiling that starts at the codebase's own outliers is the choice that step 1 makes for
     `max-complexity`. Both halves of this item then apply one policy instead of two.
   - The defaults also put `PLR0911` in standing conflict with `RET`, a rule family already in the
     gate. `RET505` pushes a function toward early returns, which a ceiling of 6 returns then counts
     against it.
   - At the three thresholds above, the rules raise 23 findings rather than the 88 that the defaults
     raise. The 23 fall across functions that are largely the outliers `C901` already names.
   - Triage them the same way as step 1: a fix where the count reflects a function that should
     split, and a targeted `# noqa` where it does not.
3. **Ratchet `max-complexity` down once step 1's list is clear.** Lowering it to 20 brings 3 more
   functions into scope, to 15 brings 6 more, and to 12 brings 13 more beyond that. Each drop lands
   as its own PR once the previous ceiling's list is fully resolved. This mirrors the "cover, then
   raise the floor" sequencing of
   [BE-0117](../BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet.md). Ruff's own default
   of 10 is a reasonable stopping point rather than a target to continue past automatically. Revisit
   it when a later baseline measurement still finds the ceiling loose relative to the codebase's
   actual functions.

### What the counts measure for a function that nests other functions

*Motivation* left one question to the implementation phase. Does `_make_handler`'s complexity of 100
and `make_app`'s of 55 come from branching inside those factory bodies, or from folding each nested
endpoint handler's own branches into the factory's total? Measurement answers it.

`C901` and `PLR0915` fold a nested function's count into the function enclosing it. `PLR0911` and
`PLR0912` count the enclosing function's own returns and branches alone. A function whose body
contains no branch at all reports a complexity of 7 once it defines one inner function of
complexity 6.

The folding accounts for both factories. `_make_handler` defines 22 handler methods, and none of the
22 exceeds a complexity of 10. `make_app` defines 15 endpoint functions, and none exceeds 9. Neither
factory branches heavily in its own body, so we refactor neither.

Exempting a factory here costs no coverage, because ruff reports every nested function separately
and checks each one against the same ceiling. Both factories take a `# noqa: C901` that names the
reason. The same reading covers `crawl`, `device_pool`, and the `lease` closure inside
`device_pool`. Their scores are likewise the sum of the closures each one defines.

### Where a high count reflects the number of cases rather than tangled logic

Some counts measure a function's own branching rather than the closures it defines. Such a count has
two sources: logic that tangles, and the number of cases the function must cover. Two kinds of
function recur across the triage, and in both the count comes from the number of cases. We split
neither kind.

The first kind is exhaustive dispatch over a closed schema. The three backends' `_emit_step`
functions and their `_emit_assertion` peers carry one branch per scenario step kind or assertion
kind. The count tracks the scenario schema's size, and splitting one of those functions would remove
the single place a new step kind clearly belongs. The routing policy in `required_role` reads the
same way, with one return per gated route class.

The second kind is a request handler whose returns are validation guards, each carrying a distinct
HTTP status. That is the early-return shape `RET505` asks for.

Every function of either kind takes a targeted `# noqa` that names which of the two kinds it is.

The one function the triage does split is the `coverage` command. Its 21 branches include a
self-contained block that resolves the screens dimension from `--crawl` evidence. That block becomes
`_screens_coverage`, and the command drops under the ceiling without an exemption.

## Alternatives considered

- **Enable the three Pylint-derived rules at their default thresholds** — rejected during
  implementation. The defaults raise 88 findings at once, the same rushed triage that the bullet
  below rejects for `max-complexity`. They also make `PLR0911` contradict `RET505`, which is already
  in the gate and pushes code toward the early returns that `PLR0911` would then penalize.
  *Detailed design* step 2 records the thresholds chosen instead.
- **Adopt Radon and Xenon instead of ruff's `C901`** — rejected. Ruff is already the linter in the
  gate, and `C901` measures the same mccabe cyclomatic complexity that Radon computes and Xenon
  checks against a threshold. It costs no second tool and no second `make check` step. This is
  consistent with BE-0067's use of ruff's `S` rules rather than a separate Bandit install.
- **Set `max-complexity` to ruff's default (10) immediately** — rejected. That flags 59 functions at
  once, most of them not the outliers that most need attention, and forces a rushed triage rather
  than the staged one this item designs.
- **Adopt `PLR0913` (too many arguments) and `PLR2004` (magic value comparison) in this item** —
  rejected. Neither measures structural complexity: `PLR0913` measures a function's parameter-list
  size, and `PLR2004` measures whether a comparison's constant should be a named value. Folding
  either in here would mix two different quality questions under one "complexity ceiling" heading.
  Either is a candidate for its own future item.
- **Refactor `_make_handler` and `make_app` immediately, ahead of any tooling change** — rejected.
  Refactoring before measuring the rest of the codebase against the same ceiling would fix two
  functions in isolation. It would not establish the ceiling that catches every function like them,
  present and future.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add `C901` with `max-complexity = 25`; triage the 10 functions this flags (refactor or a
  targeted `# noqa: C901`), resolving the `_make_handler` / `make_app` folding question first.
- [x] Add `PLR0911`, `PLR0912`, and `PLR0915` with `max-returns = 12`, `max-branches = 20`, and
  `max-statements = 80`; triage the 23 combined findings.
- [x] Ratchet `max-complexity` to 20; triage the 3 additional functions this flags.
- [x] Ratchet `max-complexity` to 15; triage the 14 additional functions this flags (6 planned).
- [x] Ratchet `max-complexity` to 12; triage the 17 additional functions this flags (13 planned).
- [x] Re-measure against ruff's default of 10; decide whether to ratchet further or stop there.

Log:

- 2026-08-26 — Landed the ceiling itself. `C901` at `max-complexity = 25` and the three
  Pylint-derived rules at 12 returns, 20 branches, and 80 statements now run in `make check`,
  flagging 33 findings across 14 functions. Measurement settled the folding question *Motivation*
  deferred, so `_make_handler`, `make_app`, `crawl`, `device_pool`, and the `lease` closure took a
  targeted `# noqa` rather than a refactor. The `coverage` command is the one function the triage
  split, into `coverage` and a new `_screens_coverage` helper. Four functions whose counts
  reflect genuine length — `_wait`, `_handle_action`, and `run_one` on the deterministic run path,
  plus the interactive `record` loop — carry a `# noqa` naming the ratchet steps below as where a
  split belongs, so that the PR setting the ceiling does not also change run-path behavior.
- 2026-09-28 — [#2077](https://github.com/bajutsu-e2e/bajutsu/pull/2077) —
  Finished the ratchet, which leaves 12 as the final `max-complexity`. We triaged every function
  each step brought into scope. The counts had drifted since the proposal. The step to 20 flagged 3
  more functions, as planned. The step to 15 flagged 14 more, though the plan expected 6 such
  functions. The step to 12 flagged 17 more, though the plan expected 13 such functions. We split 12
  of those 34 functions, each along a self-contained block moved into a private helper. The `doctor`
  command and `wait_for_system_alert` are two of them. The other 22 functions took a targeted
  exemption, each naming its reason. In some, mccabe folds a nested function's count into the
  enclosing one. Others dispatch over every scenario step kind, or run validation guards in a
  request handler. The rest sit on the run path, where a split carries behavioral risk. The
  function `start_run_set` took both treatments.
  Moving its per-scenario resolution into a helper brought it under the ceiling of 20 in that step.
  Its remaining guards took the exemption at the ceiling of 12 instead. At ruff's default of 10,
  another 51 functions fail. Each of them scores 11 or 12, so the ratchet stops at a ceiling of 12
  rather than going lower. Exempting most of those 51 functions would dilute the exemptions that
  mark the real outliers. The four long functions the first unit exempted keep their exemption. The
  comment beside each now leaves their split to a refactor of its own.

## References

- [BE-0117 — Cover the rest of the CLI command layer, then ratchet the coverage floor](../BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet.md)
  — the "cover the worst cases, then tighten the floor" sequencing this item mirrors for a
  complexity ceiling instead of a coverage floor.
- [BE-0067 — Code-quality gate hardening](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)
  — enabled ruff's own `S` rules rather than installing Bandit separately.
- [Ruff mccabe rule (`C901`)](https://docs.astral.sh/ruff/rules/complex-structure/) and
  [Ruff's Pylint rules](https://docs.astral.sh/ruff/rules/#pylint-pl) — the rule definitions this
  item enables.
- [Radon](https://pypi.org/project/radon/) and [Xenon](https://pypi.org/project/xenon/) — the
  dedicated tools this item's *Alternatives considered* weighs against ruff's built-in equivalent.
