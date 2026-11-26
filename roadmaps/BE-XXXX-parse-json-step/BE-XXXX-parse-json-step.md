**English** · [日本語](BE-XXXX-parse-json-step-ja.md)

# BE-XXXX — Read values out of `JSON` text with a parseJson step

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-parse-json-step.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0440](../BE-0440-http-step-field-extraction/BE-0440-http-step-field-extraction.md), [BE-0472](../BE-0472-eval-step-value-operations/BE-0472-eval-step-value-operations.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a `parseJson` step. The step reads text in JavaScript Object Notation (`JSON`).
It copies values into variables by path. It also removes the `extractBody` option from the `http`
step, because the new step takes over that job.

The removal breaks existing files. A scenario that uses `extractBody` stops loading.

## Motivation

[BE-0440](../BE-0440-http-step-field-extraction/BE-0440-http-step-field-extraction.md) reads fields
from a response body inside the `http` step alone. That step speaks the Hypertext Transfer Protocol
(`HTTP`). Text held in a variable has no such reader.

The `json` operation of the `eval` step turns a `JSON` array into a list. It cannot read inside an
object.

We can tell that the change arrived when a scenario reads a nested value from text it saved earlier.

## Detailed design

### The `parseJson` step

```yaml
- parseJson:
    from: "${vars.response}"
    extract:
      - { var: code, path: "data.items[0].code" }
      - { var: userId, path: "data.user.id" }
```

`from` is a string with the usual `${...}` interpolation. `extract` is a non-empty list of
`{ var, path }` entries. The step takes its rules from `extractBody`:

- The path grammar stays the same: object keys and zero-based `[n]` indexes.
- The step stores a string value as it stands. Any other value becomes compact `JSON` text.
- Text that is not `JSON`, a malformed path, or an unresolved path fails the step.
- All entries resolve together. One bad entry leaves every variable unchanged.
- Two entries with one `var` are a load error, checked again after interpolation.
- A substituted path value fills one segment and cannot carry `.`, `[`, or `]`.

Two rules come from the `eval` step
([BE-0472](../BE-0472-eval-step-value-operations/BE-0472-eval-step-value-operations.md)). Error
messages never quote the text or a substituted value. A value read from a secret input counts as a
secret. Later artifacts mask it. The text in `from` shares the `eval` input limit of 1,048,576
characters.

### Removing `extractBody`

`extractBody` leaves the `http` step. A scenario that still uses it stops loading, with a message
that points to `saveBody` plus `parseJson`:

```yaml
- http: { url: "${secrets.API}/login", status: 200, saveBody: login }
- parseJson:
    from: "${vars.login}"
    extract:
      - { var: token, path: "data.token" }
```

### Prime directives

| Directive | How the design keeps it |
|---|---|
| No model on the verdict path | Path resolution is a fixed walk over parsed `JSON`. |
| Determinism | A bad input or path fails the step and writes nothing. |
| App-agnostic | The step and its path grammar are the same for every target. |

### Work breakdown

| # | Work | Main files | Done when | Depends on |
|---|---|---|---|---|
| 1 | Add the `parseJson` model, handler, load check, secret marking, and code generation comment | `bajutsu/common/scenario/`, `bajutsu/common/orchestrator/`, `bajutsu/codegen/` | Unit tests cover the rules above, and each code generator emits a named `parseJson` `TODO` comment | none |
| 2 | Remove `extractBody` and move its tests to `parseJson` | `http_request.py`, `_http_extract.py`, `handlers/http.py`, `substitution.py` | An old scenario stops loading with the migration message | 1 |
| 3 | Update the documents in both languages | `docs/scenarios.md`, `docs/dsl-grammar.md`, `docs/architecture.md` | No page describes `extractBody` as available | 2 |

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| Add a path operation to `eval` | A named step reads better for several values at once and keeps the `{ var, path }` form. |
| Keep `extractBody` beside `parseJson` | Two ways to do one job would need the same rules maintained twice. |
| Widen `extractBody` with wildcards and filters | The option would still read a response body alone. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `parseJson` step
- [ ] Removal of `extractBody`
- [ ] Documents in both languages

## References

| Item | Why it matters here |
|---|---|
| [BE-0440](../BE-0440-http-step-field-extraction/BE-0440-http-step-field-extraction.md) | The path grammar that `parseJson` keeps |
| [BE-0472](../BE-0472-eval-step-value-operations/BE-0472-eval-step-value-operations.md) | The rules for secret values and error messages |
