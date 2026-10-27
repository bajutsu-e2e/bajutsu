**English** · [日本語](BE-XXXX-eval-step-value-operations-ja.md)

# BE-XXXX — An `eval` step that splits, joins, and filters values held in variables

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-eval-step-value-operations.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | — |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

This item adds an `eval` step that transforms a value a scenario already holds in a variable. The step
takes a source string and an ordered list of operations, such as `split`, `filter`, `sort`, `pick`,
and `join`. It stores the result under `${vars.*}`. Operations come from a fixed vocabulary. Inside a
pipeline a value is either a string or a list, and the loader checks that the types line up before
the run starts. A result that is a list is stored as compact JSON array text. When any input traces
back to `${secrets.*}`, the result joins the set of values that the artifact redactor masks, so a
derived value never reaches a report in plain text.

## Motivation

A scenario captures runtime values in four ways today. The `extract` modifier reads an element's
`value`, `label`, or `identifier`. The `http` step stores a response body or one JSON field. The
`email` step extracts a code with a regular expression. The `generate` step computes a random or
datetime value. Every one of them stores the value as it arrived. No step changes a stored value.

The values a target shows are often not the values a later step needs. A cart screen shows one label,
`Apple, banana ,Cherry`, and the scenario must assert the sorted, trimmed items or type the second
item into a search box. A banner reads `Order #A-1042 confirmed`, and the next screen wants only
`A-1042`. A test needs a password built from two parts. Today the author has three options. The author
can assert against the whole string and accept a brittle comparison. The author can move the logic
into an external script, which hides the flow from the scenario file. Or the author can give up the
check.

When this item ships, a scenario holds that logic in one `eval` step. The scenario file then shows
every step from capture to use, and the run report records the produced value. The observable change
is that a scenario which splits a captured label and asserts one element needs no helper script and no
`http` round trip to a scratch service.

## Detailed design

The step name `eval` could suggest that the step runs arbitrary code. It does not. `eval` runs a fixed
vocabulary of operations over strings and lists, and a scenario cannot add an operation. The shape
follows `generate` ([BE-0377](../BE-0377-dynamic-value-generation/BE-0377-dynamic-value-generation.md)):
one step, one `into: { var }` target, and a value stored under `vars.<name>`.

### Authoring surface

```yaml
- tap: { id: cart.open }
  extract:
    items: { sel: { id: cart.summary }, prop: label }   # "Apple, banana ,Cherry"

- eval:
    from: "${vars.items}"
    ops:
      - split: { by: "\\s*,\\s*", regex: true }
      - filter: { notEmpty: true }
      - sort
    into: { var: sortedItems }                           # ["Apple","Cherry","banana"]

- eval:
    from: "${vars.sortedItems}"
    ops: [json, { pick: first }]
    into: { var: firstItem }                             # Apple

- eval:
    from: "${secrets.API_TOKEN}"
    ops: [{ substring: { start: 0, end: 8 } }]
    into: { var: tokenPrefix }                           # masked in every artifact
```

Fields:

- **`from`** is a string and is required. It uses the same `${...}` interpolation as every other
  step field. Joining two variables needs no operation, because `from: "${vars.a}-${vars.b}"`
  already builds the joined string.
- **`ops`** is a non-empty list. Each entry runs on the result of the previous entry. An operation
  without arguments is written as a bare string (`trim`, `json`, `count`, `unique`, `reverse`, `sort`).
  Every other operation is a mapping with one key.
- **`into`** is `{ var: <name> }`, the same `VarTarget` that `generate` and `totp` use
  (`bajutsu/common/scenario/models/actions/var_target.py`). Writing a name that already holds a
  value replaces it, as `extract` and `generate` do.

### Values and types

A pipeline value is a string or a list of strings. `from` starts the pipeline as a string. The final
value is stored as follows:

- A string is stored as is.
- A list is stored as compact JSON array text, such as `["a","b"]`. The renderer is `json_text`
  (`bajutsu/common/assertions/dict_match.py`), the same one `extractBody` uses, so a stored list
  reads like a captured JSON body and keeps non-ASCII text unescaped.

A later `eval` turns that text back into a list with the `json` operation. The explicit step avoids a
guess: a string that merely looks like JSON stays a string until the author says otherwise.

### Operations

| Operation | Form | Input → output | Behavior |
|---|---|---|---|
| `split` | `split: ","` or `split: { by, regex? }` | string → list | Splits on a literal separator, or on a regular expression when `regex` is true. An empty separator is a load error. |
| `join` | `join: "-"` | list → string | Joins the elements with the separator. An empty separator is allowed. |
| `filter` | `filter: { <predicate>, not? }` | list → list | Keeps elements that satisfy exactly one predicate: `matches` (regular expression, searched anywhere), `equals`, `contains`, or `notEmpty`. `not: true` keeps the others. An empty result is a valid list. |
| `pick` | `pick: first`, `last`, or an index | list → string | Takes one element. An index is a non-negative integer counted from zero. |
| `count` | `count` | list → string | The number of elements, as decimal text. |
| `sort` | `sort`, `sort: asc`, or `sort: { order, numeric? }` | list → list | Orders by Unicode code point. With `numeric: true` it orders by decimal value and fails on a non-number. |
| `unique` | `unique` | list → list | Drops repeats and keeps the first occurrence of each element. |
| `reverse` | `reverse` | list → list | Reverses the order. |
| `json` | `json` | string → list | Parses a JSON array. A string element stays as is, and any other element renders through `json_text`. |
| `regex` | `regex: { pattern, group? }` | string → string | Returns capture `group` (default 0) of the first match. |
| `replace` | `replace: { find, with, regex? }` | string → string | Replaces every occurrence. |
| `substring` | `substring: { start, end? }` | string → string | Takes the characters from `start` up to `end`, both counted from zero. |
| `trim` | `trim` | string → string | Removes leading and trailing whitespace. |
| `case` | `case: lower` or `upper` | string → string | Changes letter case. |

An operation never maps over the elements of a list. A scenario that must trim every element splits
with a regular expression that absorbs the whitespace, as the first example does.

### Load-time and run-time checks

The loader walks each `ops` list and tracks the type flow from `from` (a string) to the end. An
operation applied to the wrong type, such as `join` after `regex`, is a scenario load error that
names the step and the operation. The loader also rejects an empty `ops`, an empty `split`
separator, a `filter` with zero or several predicates, and a negative or non-integer index. A
regular expression is compiled at load time when its text holds no `${...}` token.

Operation arguments accept `${vars.*}` and `${secrets.*}` tokens, as other step fields do. An argument
that holds a token cannot be checked at load time, so the run re-runs the same checks on the
substituted step and fails the step with the same message.

The step fails at run time, with no fallback value, in these cases:

- a `regex` pattern with no match, or a `group` the pattern does not have
- an index outside the list
- `substring` with `start` or `end` beyond the string, or `start` after `end`
- `json` on text that is not a JSON array
- `sort` with `numeric: true` on an element that is not a decimal number
- an input string over 1,048,576 characters, or a list over 10,000 elements

A failure message names the step, the operation's position, and the operation. It never quotes a value,
because the value may derive from a secret. An empty list is a valid result, so a scenario that
expects at least one element asserts that with `count` and an `assert` step.

### Secret handling

The run records evidence through a redactor that masks known secret values
(`Redactor`, `bajutsu/common/evidence/redaction.py`). Today that set is fixed when the run starts.
An `eval` result derived from a secret is a new secret value, and masking only the original would
leave the derived text in plain view. The design adds this rule: **a result is secret when any of its
inputs is secret.**

- An input is secret when `from` or an argument holds a `${secrets.*}` token. It is also secret when
  it holds a `${vars.<n>}` token and the run has marked variable `<n>` secret.
- When the result is secret, the step runner adds the result to the run's secret set, including every
  encoding variant the redactor already derives
  ([BE-0153](../BE-0153-encode-aware-secret-redaction/BE-0153-encode-aware-secret-redaction.md)), and
  marks `into.var` secret. A non-secret result clears the mark on `into.var`, because the variable now
  holds a different value.
- The registration happens before the runner records the step, so the step's own report row and trace
  already show the masked text.
- A result registers however short it is. A one-character secret result then masks that character
  everywhere in later evidence, which can make an artifact hard to read. We accept that cost over a
  length floor that would let a fragment of a secret through.

### Core-layer changes

The secret set is a plain `list[str]` passed to several `Redactor` instances at run start
(`bajutsu/common/runner/pipeline.py`, `bajutsu/common/runner/pool.py`). A list cannot grow after a
`Redactor` has built its search terms in `__init__`. The change has two parts:

1. A small mutable `SecretValues` class holds the raw values, with `add(value)` and a version number.
   `Redactor` accepts it where it accepts a list, and rebuilds its terms when the version changes. A
   caller that passes a plain list keeps working.
2. The run state gains a `secret_vars` set beside `bindings`. The step runner
   (`bajutsu/common/orchestrator/loop/_step_runner.py`) computes the secret status of an `eval` step
   from the step as written, before substitution, because the handler only sees substituted text.

Masking starts at the `eval` step. An artifact written earlier in the run is not rewritten.

### Reporting

The step runner copies the produced value into the step outcome, as it does for `generate`
(`outcome.generated`, `_step_runner.py`). A failed pipeline leaves the variable unset. The `generated`
field already exists in the manifest (schema v8), so this item adds no manifest version. A secret
result appears masked there, through the redactor.

### Codegen, REPL, and documentation surfaces

`eval` runs in the Bajutsu runner and has no XCUITest, Playwright, or UI Automator equivalent. Each
code generator emits a labeled `// TODO` for the step, as it does for `generate`, `http`, and `totp`
([BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)). The REPL
session prints the stored value after an `eval`, as it does after `generate`
(`bajutsu/repl/session.py`), and passes it through the redactor first. `eval` never reaches the app, so
it joins the step kinds listed in `bajutsu/common/orchestrator/loop/app_crash_latches.py`.

### Out of scope

- **Mapping a sub-pipeline over list elements** (an `each` operation). The fixed vocabulary stays
  flat, which keeps the type flow easy to check. Add it when a scenario needs a per-element operation
  that a regular expression cannot express.
- **Arithmetic** (`add`, `sub`, `mul`). It forces a decision on decimal rounding and number
  formatting. A separate item can add it to the same `ops` list.
- **Iterating over a stored list.** `forEach` walks UI elements only. A `forEach` over a list variable
  is a separate item that builds on this one.
- **Masking secrets retroactively.** Evidence written before the `eval` step stays as written.
- **Derived values from `extract`, `http`, or `email`.** The run treats a value read from the target
  as non-secret, as it does today.

### Work breakdown (MECE)

| # | Unit | Done when |
|---|---|---|
| 1 | Scenario model: `Eval`, the operation types, `Step.eval`, load-time type-flow and argument checks | model tests load every operation form and reject each load error listed above |
| 2 | Evaluator: a pure function that runs an `ops` list on a string, with the caps and value-free failure messages | unit tests cover every operation, every run-time failure, and the caps |
| 3 | Handler `_do_eval`: substitution, a second run of the checks on substituted arguments, the write to `vars.*`, and the produced value in the step outcome | a fake-driver run stores a string result and a list result, and a failing pipeline leaves the variable unset |
| 4 | Secret handling: `SecretValues`, the `Redactor` change, `secret_vars` in the run state, and registration before the step is recorded | a test shows a substring of a secret, and a variable derived from that variable, masked in the report, the trace, and the network log |
| 5 | Step-kind surfaces: runner step sets, REPL display, crash-latch list, and a labeled `TODO` in the three code generators | codegen goldens show the `TODO`, and the REPL test shows the masked value |
| 6 | Scenario-level tests with the fake driver for the examples in this item | the capture, split, filter, sort, pick, and assert flow passes end to end |
| 7 | Documentation: the step table and a section in `docs/scenarios.md`, the production in `docs/dsl-grammar.md`, and both `docs/ja/` mirrors | the English and Japanese pages list `eval`, and `make check` is green |

## Alternatives considered

| Alternative | Why we did not choose it |
|---|---|
| One step per operation (`split`, `join`, `filter`) | Every operation needs a variable between it and the next, so a scenario fills with intermediate names. The step vocabulary also grows with each new operation. A pipeline adds an operation without adding a step kind. |
| Function calls inside tokens, such as `${join(split(vars.x, ","), "-")}` | It turns the flat lookup in `interp.py` into an expression parser. [BE-0377](../BE-0377-dynamic-value-generation/BE-0377-dynamic-value-generation.md) rejected the same idea for the same reason. |
| A general expression language such as JMESPath or jq | [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md) rejected a general language because it weakens the bound on what a scenario can do. [BE-0440](../BE-0440-http-step-field-extraction/BE-0440-http-step-field-extraction.md) found that no parser of that kind is a direct dependency today. |
| String operations only (`regex`, `substring`) | It cannot express splitting a captured label into items, joining items, or filtering them, which is the request behind this item. |
| Store a list as a delimiter-joined string | An element that holds the delimiter breaks the round trip. JSON array text has one unambiguous reading. |
| Keep lists inside one pipeline and store only strings | A scenario could not split once and pick different elements in later steps. |
| Forbid `${secrets.*}` as an `eval` input | It is the simplest rule. It also blocks building a credential from two parts or sending a token with a prefix. |
| Take no action on derived secrets | A derived value would reach reports in plain text, which contradicts [BE-0153](../BE-0153-encode-aware-secret-redaction/BE-0153-encode-aware-secret-redaction.md) and [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md). |
| Return an empty string when an operation does not match | The failure would surface in a later step as an unrelated mismatch. This conflicts with determinism first. |
| A length floor below which a derived secret is not registered | It lets a short fragment of a secret reach evidence, which defeats the purpose of masking. |

### Open points for review

- **Regular-expression cost.** Python's `re` module has no match timeout, and a pattern with nested
  repetition can run for a long time on a long input. The caps above bound the input but not the
  time. The options are to accept the risk for author-written patterns or to add a deadline in the
  evaluator.
- **Cap values.** The 1,048,576-character and 10,000-element caps are starting points. Confirm them
  against the largest label or body a real scenario captures.
- **Reuse of `generated`.** The manifest field is named for `generate`. Reusing it avoids a manifest
  version bump. Renaming it would give both steps an accurate name at the cost of a bump.
- **Short secret results.** A one-character result masks that character in all later evidence. If
  reports turn out to be unreadable in practice, the step could add a warning when a secret result
  falls below a length.
- **Step name.** We chose `eval` for its brevity. The Detailed design opens with a note that the step
  runs no code. Revisit the name if reviewers read it as arbitrary evaluation.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add the `Eval` model and the load-time checks.
- [ ] Add the evaluator with every operation, the caps, and value-free failure messages.
- [ ] Add the `eval` handler and the produced value in the step outcome.
- [ ] Add secret handling: `SecretValues`, the `Redactor` change, and `secret_vars`.
- [ ] Add the step-kind surfaces: runner sets, REPL, crash-latch list, and codegen `TODO`.
- [ ] Add scenario-level tests with the fake driver.
- [ ] Document `eval` in `docs/scenarios.md` and `docs/dsl-grammar.md`, with both `docs/ja/` mirrors.

Log:

- Proposed; no implementation yet.

## References

- [BE-0033 — Scenario variables + light control flow](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md)
  — `extract` and `vars.*`, and the rejection of a general expression language.
- [BE-0377 — Random and datetime value generation](../BE-0377-dynamic-value-generation/BE-0377-dynamic-value-generation.md)
  — the `generate` step this item follows in shape.
- [BE-0440 — Extract specific fields from the http step's response body](../BE-0440-http-step-field-extraction/BE-0440-http-step-field-extraction.md)
  — the `json_text` rendering and the fixed-grammar precedent.
- [BE-0032 — Secret variables](../BE-0032-secret-variables/BE-0032-secret-variables.md),
  [BE-0153](../BE-0153-encode-aware-secret-redaction/BE-0153-encode-aware-secret-redaction.md), and
  [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md) — the
  secret model and the redaction boundary that the secret rule extends.
- [`docs/scenarios.md`](../../docs/scenarios.md), [`docs/dsl-grammar.md`](../../docs/dsl-grammar.md),
  `bajutsu/common/orchestrator/actions/handlers/generate.py`,
  `bajutsu/common/evidence/redaction.py`, `bajutsu/common/orchestrator/loop/_step_runner.py`
