**English** · [日本語](BE-0387-ruff-lint-rule-expansion-ja.md)

# BE-0387 — Broaden ruff's lint rule selection

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0387](BE-0387-ruff-lint-rule-expansion.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0387") |
| Implementing PR | [#PRNUM](https://github.com/bajutsu-e2e/bajutsu/pull/PRNUM) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

`pyproject.toml` selects `ANN B C4 DTZ E F I N PERF PIE PT RUF S SIM T20 UP` for ruff's linter,
building up in stages that each closed one gap — most recently `S`, flake8-bandit's rules, added by
[BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md). Several
categories flake8 covers through separate plugins still sit outside that list. This item adds a
curated set of them — `PTH` (pathlib over `os.path`), `RET` (return-statement consistency), `ARG`
(unused arguments), `SLF` (private-member access from outside its class), `W` (the pycodestyle
warnings `E`'s sibling never turned on), `PLE` (Pylint's error tier, which ruff reimplements
directly), and a hand-picked subset of `TRY` (exception-handling style) and `PLW` (Pylint's warning
tier) — consistent with BE-0067's use of ruff's own `S` rules rather than a separate Bandit
install, applied to the rest of the gap this repository's static analysis leaves against a typical
flake8 + Pylint setup.

## Motivation

`make lint` runs `ruff check .` repo-wide ([Makefile:93-94](../../Makefile)), so `tests/`, `demos/`,
and `scripts/` are gated by the same rule selection as `bajutsu/`, not exempted from it. Running
each candidate category against `bajutsu/` alone measures a manageable, mostly signal-bearing load:
`W` reports zero findings (free to turn on), `RET` reports 2, `PTH` reports 17, `SLF` reports 38,
`ARG` reports 130, and `TRY` reports 468. That `bajutsu/`-only view understates the repo-wide cost,
though: `ARG` alone reports 1,406 findings on `tests/` — more than ten times the 130 in `bajutsu/` —
because an unused fixture or mock parameter is the normal pytest pattern, the same rationale this
item already gives below for its `SLF` tests-ignore. `TRY`'s count is dominated by one rule, `TRY003`
(438 of 468): it flags any `raise` whose message is longer than a few words, a Pylint-derived heuristic
widely considered too aggressive in practice, since a specific error message is often the point of a
`raise`, not a smell. The other three `TRY` rules are a small, precise set instead — `TRY300` (17,
suggesting an `else` block over code that runs only on a `try`'s success path), `TRY004` (10,
preferring `TypeError` over a bare `ValueError` for a wrong-type check), and `TRY400` (3, preferring
`logging.exception` over `logging.error` inside an `except` block, which keeps the traceback in the
log). `ARG`'s 130 findings split as `ARG002` (94, an unused method argument — often a `Driver`
Protocol implementation that does not need every parameter for a given backend), `ARG001` (20, an
unused function argument), and `ARG005` (16, an unused lambda argument).

Adopting the rest of Pylint's rule set through ruff's `PL` prefix is a separate question this item
does not take up: a full `PL` run measures 526 findings, and `PLC0415` (import outside top level)
alone accounts for 258 of them — a false-positive-heavy result here, since this codebase's own
convention is to import an optional or heavy dependency lazily inside the function that needs it
(the mypy overrides for `boto3`, `playwright`, and the rest already document that pattern). `PLE`
(Pylint's error tier — the rules closest to a real bug) reports zero findings, so this item selects
it alongside `W` on the same "free to turn on" grounds: no migration cost now, and every future
`PLE` error — a misplaced bare `raise` (`PLE0704`), an `await` outside an `async` function
(`PLE1142`), a bad `strip` argument (`PLE1310`) — gated from here on. This item's `PLW` slice —
`PLW0603` (7, a `global` statement), `PLW2901` (3, a loop variable overwritten inside its own loop),
and `PLW1510` (3, `subprocess.run` called without an explicit `check=`) — is the useful remainder
once `PLC0415`'s noise is set aside. A further subset of `PL`, the function-size rules
`PLR0911` / `PLR0912` / `PLR0915`, belongs with the sibling cyclomatic-complexity-ceiling proposal
rather than here, since they measure the same thing a complexity threshold measures — how large and
branchy one function has grown — not a style question.

## Detailed design

Add the eight categories to `[tool.ruff.lint]`'s `select` list, each landing as its own commit so a
review can look at one rule's fixes at a time:

- **`W`**: add with no follow-up work — zero findings today.
- **`PLE`**: add with no follow-up work — zero findings today.
- **`RET`**: add and apply the fixes directly — 2 in `bajutsu/` plus 4 in `tests/`, 6 total (4 of
  the 6 `--fix`-eligible).
- **`PTH`**: add and fix the findings — 17 in `bajutsu/` plus 6 in `tests/` plus 3 in
  `demos/`/`scripts/`, 26 total — each replaces an `os.path` call with the equivalent
  `pathlib.Path` method, matching the style the rest of the codebase already favors for new code.
- **`SLF`**: add, ignoring it in `tests/**` alongside the `ANN` / `T20` / `S` ignores already there
  (a test reaching into a fixture's or a fake's private state to assert on it is a normal pattern,
  not a smell) — then fix the 38 findings in `bajutsu/` itself, either by exposing a narrow public
  accessor where the access is legitimate or by moving the access inside the owning class where it
  is not.
- **`ARG`**: add, ignoring it in `tests/**` alongside the `SLF` ignore above (an unused fixture or
  mock parameter is the normal pytest pattern, not a smell) — then triage the 130 `bajutsu/`
  findings by cause: a `Driver` Protocol method that legitimately ignores a parameter for one
  backend keeps the parameter and gets a targeted `# noqa: ARG002` with a comment naming which
  protocol it satisfies; a parameter nothing calls with a real value gets removed.
- **`TRY`**: add with `TRY003` in the top-level `[tool.ruff.lint]` `ignore` list (global, since the
  rule is noisy everywhere, not only in this codebase) and fix the remaining `TRY300` / `TRY004` /
  `TRY400` findings.
- **`PLW`**: add with `PLW0603` triaged case by case (a `global` used for an intentional
  module-level cache stays, with a comment; one that is really a missed refactor gets fixed) and
  the smaller `PLW2901` / `PLW1510` findings fixed directly. Outside `bajutsu/`, `tests/` carries a
  13-finding residue (8 `PLW0108` unnecessary-lambda, 5 `PLW1510`) fixed the same way — `PLW0108` by
  replacing the lambda with the function or expression it wraps.

`tests/**` and `demos/**` keep their existing blanket ignores for `ANN`, `T20`, and `S`
([pyproject.toml](../../pyproject.toml)); this item does not narrow those. `scripts/**` keeps its
`T20` / `S607` ignores. Measuring all eight categories across all four trees — `bajutsu/`, `tests/`,
`demos/`, and `scripts/` — before landing shows `tests/**` needs two new blanket ignores, `SLF` and
`ARG`, for the reasons given above; the small `PTH`, `RET`, and `PLW` residues found outside
`bajutsu/` are fixed rather than ignored, as detailed in each bullet above. `demos/**` and
`scripts/**` need no new blanket ignore.

## Alternatives considered

- **Adopt the full `PL` prefix** — rejected: `PLC0415` alone contributes 258 of 526 findings,
  overwhelmingly against this codebase's deliberate lazy-import convention. Selecting `PLE` by its
  own prefix, as this item does, takes the tier worth having without inheriting that noise.
- **Adopt a dedicated Pylint, Dlint, or `hacking` install alongside ruff** — rejected: consistent
  with BE-0067's use of ruff's `S` rules rather than a separate Bandit install, ruff already
  reimplements the useful slice of each (`PL*` for Pylint's rules, and `S` already covers the security-linting role
  Dlint would add), so a second tool would duplicate the gate's own linter rather than fill a real
  gap. `hacking` enforces OpenStack's project conventions specifically and has no application here.
- **Enable `TRY003` and suppress it per call site** — rejected: at 438 findings, a per-site `# noqa`
  would outnumber the exception-raising code it decorates: silencing the rule once, with a comment
  explaining why, communicates the same decision without the noise.
- **Fold `ARG002` findings into a repo-wide underscore-prefix convention (`_backend_specific_arg`)**
  — rejected: renaming every Protocol parameter that one backend ignores would touch every
  implementation's signature for a cosmetic change; a targeted `# noqa` at the one implementation
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
  ([#PRNUM](https://github.com/bajutsu-e2e/bajutsu/pull/PRNUM)). The counts above are the measured
  ones at landing time; they drift slightly from the proposal's (`ARG002` 94 → 98, `TRY004` 10 → 11,
  `PLW1510` in `tests/` 5 → 7) because the tree grew after the proposal was written. Three
  treatments differ from what the *Detailed design* anticipated, each for a reason the code only
  showed once the rule was on:
  - **`SLF`** turned out to flag no class state at all. All 38 findings are module-level private
    helpers referenced across module boundaries — `adb._real_run` / `_checked_serial`,
    `simctl._real_run`, `base._contains`, `readiness._await_ready` / `_await_boot`,
    `intervals._spawn`, `_yaml._Loader` — several of them the default argument of a *public*
    function, which makes them part of their module's contract already. Dropping the underscore is
    the "narrow public accessor" the design asked for; `oplog`'s namespaced `_bajutsu_oplog` marker
    and `theme_editor`'s deliberate cache clear are the only two noqa.
  - **`TRY004`** kept all ten exception types behind a noqa rather than switching them to
    `TypeError`. Each validates an external payload — a JSON/YAML document, an HTTP response shape —
    where a wrong type is a data error, not a caller passing the wrong type; several are documented
    `Raises: ValueError` and their callers catch `ValueError`, so the switch would have silently
    broken those handlers.
  - **`TRY300`** moved 15 of its 17 returns into an `else`. `notify._mask_url` keeps its return
    inside the `try` because the returned f-string is exactly what the guard protects, and the step
    dispatcher keeps its own because that block returns from four branches — hoisting only the last
    would suggest the other three are not on the success path.

## References

- [BE-0067 — Code-quality gate hardening](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)
  — enabled ruff's own `S` rules rather than installing Bandit separately.
- [pyproject.toml](../../pyproject.toml) — the `[tool.ruff.lint]` `select` / `ignore` /
  `per-file-ignores` this item edits.
- [Ruff rules reference](https://docs.astral.sh/ruff/rules/) — the source for every rule code cited
  above.
