**English** · [日本語](BE-0387-ruff-lint-rule-expansion-ja.md)

# BE-0387 — Broaden ruff's lint rule selection

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0387](BE-0387-ruff-lint-rule-expansion.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0387") |
| Implementing PR | [#1761](https://github.com/bajutsu-e2e/bajutsu/pull/1761) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

`pyproject.toml` selects `ANN B C4 DTZ E F I N PERF PIE PT RUF S SIM T20 UP` for ruff's linter. The
list built up in stages that each closed one gap. The most recent stage added `S`, flake8-bandit's
rules, in
[BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md). Several
categories that flake8 covers through separate plugins still sit outside the list.

This item adds a curated set of them:

- `PTH` (pathlib over `os.path`)
- `RET` (return-statement consistency)
- `ARG` (unused arguments)
- `SLF` (private-member access from outside its class)
- `W` (the pycodestyle warnings that `E`'s sibling never turned on)
- `PLE` (Pylint's error tier, which ruff reimplements directly)
- a hand-picked subset of `TRY` (exception-handling style) and `PLW` (Pylint's warning tier)

The choice is consistent with BE-0067's use of ruff's own `S` rules rather than a separate Bandit
install. It applies the same approach to the rest of the gap that this repository's static analysis
leaves against a typical flake8 + Pylint setup.

## Motivation

`make lint` runs `ruff check .` repo-wide ([Makefile:93-94](../../Makefile)). The rule selection that
gates `bajutsu/` therefore also gates `tests/`, `demos/`, and `scripts/`, with no exemption.

Running each candidate category against `bajutsu/` alone measures a manageable, mostly signal-bearing
load:

- `W` reports zero findings (free to turn on).
- `RET` reports 2.
- `PTH` reports 17.
- `SLF` reports 38.
- `ARG` reports 130.
- `TRY` reports 468.

That `bajutsu/`-only view understates the repo-wide cost, though. `ARG` reports 1,406 findings on
`tests/` alone, more than ten times the 130 in `bajutsu/`. An unused fixture or mock parameter is the
normal pytest pattern, the same rationale this item gives below for its `SLF` tests-ignore.

`TRY`'s count is dominated by one rule, `TRY003` (438 of 468). It flags any `raise` whose message is
longer than a few words. This Pylint-derived heuristic is widely considered too aggressive in
practice, since a specific error message is often the point of a `raise`, not a mistake. The other
three `TRY` rules are a small, precise set:

- `TRY300` (17) suggests an `else` block over code that runs on a `try`'s success path alone.
- `TRY004` (10) prefers `TypeError` over a bare `ValueError` for a wrong-type check.
- `TRY400` (3) prefers `logging.exception` over `logging.error` inside an `except` block, which
  keeps the traceback in the log.

`ARG`'s 130 findings split three ways:

- `ARG002` (94) is an unused method argument. It is often a `Driver` Protocol implementation that
  does not need every parameter for a given backend.
- `ARG001` (20) is an unused function argument.
- `ARG005` (16) is an unused lambda argument.

Adopting the rest of Pylint's rule set through ruff's `PL` prefix is a separate question that this
item does not take up. A full `PL` run measures 526 findings, and `PLC0415` (import outside top
level) alone accounts for 258 of them. That result is false-positive-heavy here, because this codebase's
own convention is to import an optional or heavy dependency lazily inside the function that needs it.
The mypy overrides for `boto3`, `playwright`, and the rest already document that pattern.

`PLE` (Pylint's error tier, the rules closest to a real bug) reports zero findings. This item
selects it alongside `W` on the same "free to turn on" grounds: no migration cost now, and every
future `PLE` error is gated from here on. Examples are a misplaced bare `raise` (`PLE0704`), an `await`
outside an `async` function (`PLE1142`), and a bad `strip` argument (`PLE1310`).

This item's `PLW` slice is the useful remainder once `PLC0415`'s noise is set aside:

- `PLW0603` (7) flags a `global` statement.
- `PLW2901` (3) flags a loop variable overwritten inside its own loop.
- `PLW1510` (3) flags `subprocess.run` called without an explicit `check=`.

A further subset of `PL`, the function-size rules `PLR0911` / `PLR0912` / `PLR0915`, belongs with the
sibling cyclomatic-complexity-ceiling proposal rather than here. Those rules measure the same thing a
complexity threshold measures, how large and branchy one function has grown, and raise no style
question.

## Detailed design

Add the eight categories to `[tool.ruff.lint]`'s `select` list. Each lands as its own commit, so a
review can look at one rule's fixes at a time:

- **`W`**: add with no follow-up work. It has zero findings today.
- **`PLE`**: add with no follow-up work. It has zero findings today.
- **`RET`**: add and apply the fixes directly. There are 2 in `bajutsu/` plus 4 in `tests/`, 6 total
  (4 of the 6 `--fix`-eligible).
- **`PTH`**: add and fix the findings: 17 in `bajutsu/` plus 6 in `tests/` plus 3 in
  `demos/`/`scripts/`, 26 total. Each fix replaces an `os.path` call with the equivalent
  `pathlib.Path` method, matching the style the rest of the codebase already favors for new code.
- **`SLF`**: add, and ignore it in `tests/**` alongside the `ANN` / `T20` / `S` ignores already
  there. A test that reaches into a fixture's or a fake's private state to assert on it is a normal
  pattern, not a mistake. Then fix the 38 findings in `bajutsu/` itself. Where the access is
  legitimate, expose a narrow public accessor. Where it is not, move the access inside the owning
  class.
- **`ARG`**: add, and ignore it in `tests/**` alongside the `SLF` ignore above. An unused fixture or
  mock parameter is the normal pytest pattern, not a mistake. Then triage the 130 `bajutsu/` findings
  by cause. A `Driver` Protocol method that legitimately ignores a parameter for one backend keeps
  the parameter and gets a targeted `# noqa: ARG002`, with a comment naming which protocol it
  satisfies. A parameter that nothing calls with a real value gets removed.
- **`TRY`**: add with `TRY003` in the top-level `[tool.ruff.lint]` `ignore` list. The ignore is
  global, since the rule is noisy everywhere, not merely in this codebase. Fix the remaining
  `TRY300` / `TRY004` / `TRY400` findings.
- **`PLW`**: add with `PLW0603` triaged case by case. A `global` used for an intentional module-level
  cache stays, with a comment. A `global` that is really a missed refactor gets fixed. Fix the
  smaller `PLW2901` / `PLW1510` findings directly. Outside `bajutsu/`, `tests/` carries a 13-finding
  residue (8 `PLW0108` unnecessary-lambda, 5 `PLW1510`), fixed the same way. A `PLW0108` fix replaces
  the lambda with the function or expression it wraps.

`tests/**` and `demos/**` keep their existing blanket ignores for `ANN`, `T20`, and `S`
([pyproject.toml](../../pyproject.toml)); this item does not narrow those. `scripts/**` keeps its
`T20` / `S607` ignores.

Before landing, we measured all eight categories across all four trees: `bajutsu/`, `tests/`,
`demos/`, and `scripts/`. The measurement shows that `tests/**` needs two new blanket ignores, `SLF`
and `ARG`, for the reasons given above. The small `PTH`, `RET`, and `PLW` residues found outside
`bajutsu/` are fixed rather than ignored, as detailed in each bullet above. `demos/**` and
`scripts/**` need no new blanket ignore.

## Alternatives considered

- **Adopt the full `PL` prefix** — rejected. `PLC0415` alone contributes 258 of 526 findings,
  overwhelmingly against this codebase's deliberate lazy-import convention. Selecting `PLE` by its
  own prefix, as this item does, takes the tier worth having without inheriting that noise.
- **Adopt a dedicated Pylint, Dlint, or `hacking` install alongside ruff** — rejected. This is
  consistent with BE-0067's use of ruff's `S` rules rather than a separate Bandit install. Ruff
  already reimplements the useful slice of each: `PL*` covers Pylint's rules, and `S` already covers
  the security-linting role Dlint would add. A second tool would duplicate the gate's own linter
  rather than fill a real gap. `hacking` enforces OpenStack's project conventions specifically and
  has no application here.
- **Enable `TRY003` and suppress it per call site** — rejected. At 438 findings, a per-site `# noqa`
  would outnumber the exception-raising code it decorates. Silencing the rule once, with a comment
  that explains why, communicates the same decision without the noise.
- **Fold `ARG002` findings into a repo-wide underscore-prefix convention (`_backend_specific_arg`)**
  — rejected. Renaming every Protocol parameter that one backend ignores would touch every
  implementation's signature for a cosmetic change. A targeted `# noqa` at the one implementation
  that does not use the parameter is smaller and states the reason in place.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add `W` — no fixes needed.
- [x] Add `PLE` — no fixes needed.
- [x] Add `RET` and apply its 6 fixes (2 in `bajutsu/`, 4 in `tests/`; 4 of the 6 `--fix`-eligible).
- [x] Add `PTH` and fix its 26 findings (17 in `bajutsu/`, 6 in `tests/`, 3 in `demos/`/`scripts/`).
- [x] Add `SLF`, with a `tests/**` ignore, and fix its 38 findings in `bajutsu/`.
- [x] Add `ARG`, with a `tests/**` ignore, and triage its 134 `bajutsu/` findings (98 `ARG002`, 20
  `ARG001`, 16 `ARG005`) plus fix the 6 in `demos/`/`scripts/` (3 `ARG001`, 2 `ARG002`, 1 `ARG005`).
- [x] Add `TRY` with `TRY003` ignored, and fix its 31 remaining findings (`TRY300` / `TRY004` / `TRY400`).
- [x] Add `PLW` and fix its findings — 13 in `bajutsu/` (`PLW0603` / `PLW2901` / `PLW1510`) plus 17
  outside it (15 in `tests/`: 8 `PLW0108` + 7 `PLW1510`; 2 in `demos/`/`scripts/`: 1 `PLW1510`, 1
  `PLW2901`).

**Log**

- Landed all eight categories, one commit each, in the order the checklist lists them
  ([#1761](https://github.com/bajutsu-e2e/bajutsu/pull/1761)). The counts above are the measured
  ones at landing time; they drift slightly from the proposal's (`ARG002` 94 → 98, `TRY004` 10 → 11,
  `PLW1510` in `tests/` 5 → 7) because the tree grew after the proposal was written. Three
  treatments differ from what the *Detailed design* anticipated, each for a reason the code only
  showed once the rule was on:
  - **`SLF`** turned out to flag no class state at all. All 38 findings are module-level private
    helpers referenced across module boundaries — `adb._real_run` / `_checked_serial`,
    `simctl._real_run`, `base._contains`, `readiness._await_ready` / `_await_boot`,
    `intervals._spawn`, `_yaml._Loader` — several of them the default argument of a *public*
    function, which makes them part of their module's contract already. Dropping the underscore is
    the "narrow public accessor" the design asked for, and six of the seven take it. `_yaml._Loader`
    is the exception: `pyproject.toml`'s file-level `S506` ignore exists so that module's
    `yaml.load` line stays byte-identical to `main`, where CodeQL dismissed the matching
    `py/unsafe-deserialization` alert — and a code-scanning dismissal is keyed to a fingerprint that
    hashes the alert's own source line, so renaming the class would re-open it as a fresh alert to
    triage. Its three cross-module references carry a noqa instead, alongside `oplog`'s namespaced
    `_bajutsu_oplog` marker and `theme_editor`'s deliberate cache clear — five noqa in all.
  - **`TRY004`** kept all eleven exception types behind a noqa rather than switching them to
    `TypeError`. Ten validate an external payload — a JSON/YAML document, an HTTP response shape —
    where a wrong type is a data error, not a caller passing the wrong type; several are documented
    `Raises: ValueError` and their callers catch `ValueError`, so the switch would have silently
    broken those handlers. The eleventh, in `serve/_cli_flags.py`, guards a typer-registry
    invariant rather than a payload, and its noqa says so rather than borrowing the other ten's
    reason.
  - **`TRY300`** moved 15 of its 17 returns into an `else`. `notify._mask_url` keeps its return
    inside the `try` because the returned f-string is exactly what the guard protects, and the step
    dispatcher keeps its own because that block returns from four branches — hoisting only the last
    would suggest the other three are not on the success path.

  Of `ARG002`'s 98 `bajutsu/` findings, 97 are interface-mandated and keep the parameter behind a
  noqa naming the shape. The one exception is `XcuitestEnvironment._create_replacement`, a private
  method with two in-class callers and no protocol obligation, whose `eff` is simply dead — so it
  joins `_write_pixelmatch_diff`'s `h` and `_run_if`'s `clock` among the removals.

## References

- [BE-0067 — Code-quality gate hardening](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)
  — enabled ruff's own `S` rules rather than installing Bandit separately.
- [pyproject.toml](../../pyproject.toml) — the `[tool.ruff.lint]` `select` / `ignore` /
  `per-file-ignores` this item edits.
- [Ruff rules reference](https://docs.astral.sh/ruff/rules/) — the source for every rule code cited
  above.
