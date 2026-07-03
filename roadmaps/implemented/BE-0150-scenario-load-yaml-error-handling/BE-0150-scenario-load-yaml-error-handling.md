**English** · [日本語](BE-0150-scenario-load-yaml-error-handling-ja.md)

# BE-0150 — Fail cleanly on a malformed scenario in `trace --explain` and `audit`

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0150](BE-0150-scenario-load-yaml-error-handling.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0150") |
| Implementing PR | [#603](https://github.com/bajutsu-e2e/bajutsu/pull/603) |
| Topic | Codebase quality & technical debt |
| Origin | Found while scoping BE-0117 (CLI command-layer coverage) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu trace --explain <scenario>` and `bajutsu audit <scenario>` both load a scenario through the
shared `load_expanded_scenarios` helper (`bajutsu/cli/_shared.py`) and wrap the call in
`except (OSError, ValueError)` to turn a bad input into a clean `exit 2` with an actionable message.
That guard is incomplete: a **YAML syntax error** raises `yaml.YAMLError` (e.g. `ParserError` /
`ScannerError`), which is **not** a subclass of `ValueError`, so it escapes the guard and surfaces as
an uncaught Python traceback with `exit 1`. This item makes a malformed scenario file fail the same
clean way a structurally-invalid one already does.

## Motivation

The two commands document a contract — "a missing / unreadable input or an invalid scenario exits 2
with a message" — and their sibling code paths honor it: a structurally-invalid scenario (valid YAML
whose content fails schema validation) raises a pydantic `ValidationError`, which *is* a `ValueError`
subclass, so it is caught and rendered as `failed to load scenario: …` with `exit 2`. But the most
common authoring mistake — a typo that makes the file not parse as YAML at all (an unclosed brace, a
bad indent) — takes a different, worse path: the raw `yaml` exception propagates, the user sees a
stack trace instead of a one-line message, and the exit code is `1` rather than the documented `2`.

This is a small correctness gap with user-facing impact: the tool should give a deterministic,
readable error for the input a human is most likely to get wrong, not a traceback. It was found while
writing the CLI-layer tests for [BE-0117](../../implemented/BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet.md);
that item's tests deliberately use the reachable `ValueError` branch and left this gap for a focused
fix, since closing it changes behavior (a new exception is caught) rather than only adding tests.

## Detailed design

The work is mutually exclusive and collectively exhaustive across the loader and its two callers:

- **Decide where the boundary lives.** `load_expanded_scenarios` documents `Raises: OSError` /
  `ValueError` ("the file parses but its content is invalid"). A YAML *syntax* error means the file
  does **not** parse, so it is arguably outside today's documented contract. Two coherent options
  (pick one, apply consistently):
  1. **Normalize at the loader.** Wrap the `load_scenario_file(path.read_text(...))` parse in
     `load_expanded_scenarios` so a `yaml.YAMLError` is re-raised as a `ValueError` (chained with
     `from e`). This honors the helper's existing documented `ValueError` contract, and **every**
     caller (both `trace --explain` and `audit`, plus any future one) gets the clean behavior for
     free. Update the docstring to say invalid-or-unparseable content raises `ValueError`.
  2. **Broaden at each caller.** Change the two `except (OSError, ValueError)` clauses to also catch
     `yaml.YAMLError`. Localized, but leaks a `yaml` import into the command modules and must be
     repeated at every future call site.

  Option 1 is preferred (one place, matches the documented contract, no per-caller repetition).
- **Implement the chosen fix** in `bajutsu/cli/_shared.py` (option 1) — the parse is the only place a
  `yaml.YAMLError` originates. The error text must remain actionable (name the file and the parse
  problem).
- **Add regression tests** for both entry points with a genuinely malformed YAML scenario (e.g. an
  unclosed flow mapping): assert `exit 2` and a `failed to load scenario` message, not a traceback.
  Keep the existing `ValueError` (structurally-invalid) tests — both branches must stay covered.
- **Confirm no caller regressed.** `run` uses its own loader (not `load_expanded_scenarios`), so it
  is out of scope; verify that the `trace` / `audit` happy paths and their other error branches are
  unchanged, and that the gate stays green.

## Alternatives considered

- **Leave it as is.** Rejected: the documented contract says these inputs exit 2 with a message; a
  traceback on the single most common authoring typo is a real, if small, correctness bug.
- **Catch a broad `Exception` at the callers.** Rejected: that would also swallow unrelated bugs
  (programming errors) behind a "failed to load scenario" message, hiding real failures — the
  opposite of the project's fail-loud stance. The fix must catch exactly the parse-error family.
- **Validate YAML separately before loading.** Rejected as redundant: the loader already parses once;
  a second pre-parse is wasted work and a second source of truth. Normalizing the exception from the
  single existing parse is simpler.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Choose the boundary (loader normalization vs per-caller catch) and record the decision
- [x] Implement the fix so a `yaml.YAMLError` becomes a clean `exit 2` (with the docstring updated)
- [x] Add regression tests for `trace --explain` and `audit` on malformed YAML (keeping the existing
      `ValueError` branch tests)
- [x] Confirm the `trace` / `audit` happy paths and other branches are unchanged and the gate is green

- Chose **option 1 (normalize at the loader)**: `load_expanded_scenarios` in `bajutsu/cli/_shared.py`
  now wraps its parse/expand body and re-raises a `yaml.YAMLError` as `ValueError(...) from e`, so a
  malformed scenario (or referenced component) fails with the documented `exit 2` and a `failed to
  load scenario` message instead of an uncaught traceback. Regression tests cover both
  `trace --explain` and `audit`; the existing `ValueError` (structurally-invalid) tests stay. As a
  bonus, `coverage` — which shares the loader and the same `except (OSError, ValueError)` guard —
  gets the clean behavior for free, which is the argument for fixing it once in the loader.

## References

- `bajutsu/cli/_shared.py:75` (`load_expanded_scenarios`) — documents `Raises: OSError` / `ValueError`;
  the `yaml` parse that can raise `yaml.YAMLError` lives here.
- `bajutsu/cli/commands/trace.py:49-53` (`_explain`) — `except (OSError, ValueError)` that a
  `yaml.YAMLError` escapes.
- `bajutsu/cli/commands/audit.py:87-91` (`audit`) — the same guard, same gap.
- [BE-0117 — Cover the rest of the CLI command layer, then ratchet the coverage floor](../../implemented/BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet.md)
  — the item during whose testing this gap was found.
