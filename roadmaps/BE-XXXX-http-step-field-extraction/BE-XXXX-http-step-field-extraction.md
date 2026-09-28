**English** · [日本語](BE-XXXX-http-step-field-extraction-ja.md)

# BE-XXXX — Extract specific fields from the http step's response body

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-http-step-field-extraction.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

The `http` step ([BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md)) already issues a
request from the runner and can save the whole response body as a runtime variable. This item adds
`extractBody`: an optional list on the same step that pulls one or more named fields out of a JSON
response, by path, straight into `vars.*`. A scenario that fetches a JSON fixture no longer needs
the whole body; it names the field it needs.

## Motivation

A test-data service commonly answers with a JSON object, not a bare scalar: a login endpoint
returns `{"data": {"token": "...", "user": {"id": 42}}}`, not the token alone. `saveBody` stores
that whole object as text, and no step can reach a field inside it today. A scenario that needs
merely `data.token` must either ask the service for a narrower response shape, which it may not
offer, or give up on the field. Once `extractBody` ships, a scenario points an `http` step at a
JSON API and reads a nested field directly into `${vars.token}`.

## Detailed design

`extractBody` sits beside `saveBody` on the same `http` step, and a step may set either, both, or
neither:

```yaml
- http:
    method: POST
    url: "${secrets.API}/login"
    body: '{"user": "e2e"}'
    status: 200
    extractBody:
      - { var: token, path: "data.token" }
      - { var: userId, path: "data.user.id" }
- type: { text: "${vars.token}", into: { id: auth.token } }
```

Fields and contract:

- **`extractBody`** — a list of `{ var, path }` entries. Each parses the response body as JSON,
  walks `path`, and stores the value it finds as `vars.<var>`. `extractBody` stores a string value
  as-is; it stores any other JSON value (number/boolean/null/object/array) as the same text
  `_json_text` (`bajutsu/common/assertions/evaluate/_functions.py`) already produces for a captured
  body field — `42`, `true`, `null`, `{"id":42}` — rather than defining a second renderer for the
  same concept. A later `${vars.*}` comparison then reads the same shape the API returned, never a
  Python-specific rendering like `None` or `True`.
- **`path`** is a sequence of object keys and `[n]` (zero-based) array indexes, in any order and any
  number: an opening key carries no leading dot (`data.token`); an index may follow a key
  (`items[0].id`) or another index (`rows[0][1]`); and a path may open on an index
  when the response body is itself a JSON array (`[0].id`). An index is a non-negative decimal
  integer: `[-1]` is a malformed path, never an index counted from the end the way Python's list
  indexing reads it. A malformed path — a negative or non-numeric index, an empty segment
  (`a..b`), or a trailing dot — fails the step with an error naming the `var` and the `path`. The
  grammar stops there: no wildcards, no filters, no computed segments. BE-0036 already rejected a
  general `shell`/`exec` step on the same grounds (see its *Alternatives considered*): a small,
  fixed grammar stays auditable from the scenario file alone, where a general expression language
  would not.
- A response body that fails to parse as JSON, or a `path` that does not resolve, fails the step
  with an error naming the `var` and the `path` that did not resolve. A `path` fails to resolve in
  any of these cases:
  - a missing key
  - an out-of-range index
  - a key applied to anything but an object
  - an index applied to anything but an array (a string, number, boolean, or `null` included)

  A scenario never reads a placeholder or an empty value in place of the field it asked for.
- `extractBody` and `saveBody` read the same response body independently; setting both stores the
  whole text under `saveBody`'s name and the named fields under `extractBody`'s, from one request.
  Two `extractBody` entries with the same `var`, or an entry whose `var` equals `saveBody`, is a
  scenario load error — never a silent overwrite decided by write order. That check runs in the
  scenario loader itself, never as `Step` model validation: a model validator would re-fire on the
  substituted step below and raise an uncaught error, in place of the handler's own clean step
  failure.
- Like every other step field, a scenario may write `path` and `var` themselves with `${vars.*}` /
  `${secrets.*}` scenario-variable substitution (`path: "items[${vars.i}].id"`); the runner
  substitutes those tokens before parsing `path`, the same way it already does for `url` or
  `saveBody`. A substituted value is one path segment, never new path structure: a value carrying
  `.`, `[`, or `]` fails the step with the same malformed-path error, so a field name that reaches
  the scenario from a response body can never redirect the walk to a different field. "No computed
  segments" describes the grammar itself: it never branches on the response body's own content, and
  says nothing about a scenario parameterizing a field with its own declared variables. The
  path-grammar check runs purely on the substituted step, never on the scenario as loaded, where
  `path` may still hold a raw `${vars.*}` token. The loader-level duplicate-`var` check above runs
  again on the substituted step; a collision that substitution alone reveals fails the step at run
  time with the same error naming the `var`.

Prime directives preserved:

- **No LLM on the run path.** Path resolution is a fixed, deterministic walk over parsed JSON; the
  pass/fail judgment still comes purely from that walk, never a model call.
- **Determinism.** A body that will not parse, or a path that will not resolve, is a clean step
  failure — never a silent empty value or a guessed nearby field.
- **App-agnostic.** The step and its grammar are identical across targets; the URL, the JSON shape,
  and the paths a scenario names are what differs per app.

## Alternatives considered

- **A full JSONPath or JMESPath expression grammar.** Either buys wildcards, filters, and computed
  segments beyond the dot/index grammar above. Neither is a direct dependency of this project today:
  JMESPath arrives transitively, through `boto3`/`botocore` in the optional AWS extras, and the
  project installs no JSONPath library at all. Adopting one means a new, directly-depended-on parser
  for a step whose scenarios in practice name one fixed field. Rejected in favor of the small
  grammar `extractBody` already covers; the full grammar remains available to revisit if a scenario
  needs a segment the dot/index form cannot express.
- **Reuse the `email` step's `bodyMatches` regex ([BE-0046](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md)) instead of a JSON path.**
  A regex against serialized JSON breaks on a harmless change to key order or whitespace, and a
  nested field needs a regex a reviewer cannot check against the response shape by eye. Rejected as
  the primary mechanism for a JSON API, which is the common shape of the services `http` targets.
- **Extend `saveBody` to accept either a plain string or a list of `{ var, path }` entries.** Fewer
  field names, but passing either a string or a list to one field makes that field harder to check
  and read than two fields with one job each. A separate `extractBody` also leaves every existing
  `saveBody: <name>` scenario unchanged.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add the `extractBody` field to the `http` step's scenario model, alongside `saveBody`. Reject
      a duplicate `var` across `extractBody` entries or a `var` colliding with `saveBody` in the
      scenario loader itself, never as `Step` model validation.
- [ ] Add JSON parsing and path resolution to the runner's `http` handler, including a leading
      index and chained indexes. Fail the step on a parse error, a malformed path, or an unresolved
      path. Fail it too when a substituted value carries `.`, `[`, or `]`. Render a non-string
      resolved value with the existing `_json_text` helper
      (`bajutsu/common/assertions/evaluate/_functions.py`). Re-run the loader's duplicate-`var`
      check on the substituted step, and fail the step on a collision substitution alone reveals.
- [ ] Document `extractBody` in `docs/scenarios.md` (beside `saveBody`) and in the `http` production
      of `docs/dsl-grammar.md`, with both `docs/ja/` mirrors.
- [ ] Add scenario-level tests covering:
      - a resolved nested field.
      - a path opening on an array index, and a chained index.
      - a non-string resolved value (`_json_text` rendering).
      - a missing key and an out-of-range index.
      - a key applied to a non-object, or an index applied to a non-array (a string included).
      - a malformed path (a negative index).
      - a non-JSON body.
      - a duplicate `var`: two entries sharing one name, and an entry colliding with `saveBody`.
      - a `path` and a `var` written with `${vars.*}`: a collision substitution alone reveals, and
        a substituted value carrying `.`/`[`/`]`.

## References

- [BE-0036 — HTTP utility step](../BE-0036-utility-steps/BE-0036-utility-steps.md) — the parent
  item; its own *Detailed design* already flags per-field extraction as unimplemented future work.
- [BE-0046 — OTP & email side-channel steps](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) —
  the `email` step's `bodyMatches` regex extraction, the precedent this item's *Alternatives
  considered* compares `extractBody` against.
- [`docs/scenarios.md`](../../docs/scenarios.md), [`docs/dsl-grammar.md`](../../docs/dsl-grammar.md),
  `bajutsu/common/scenario/models/actions/http_request.py`,
  `bajutsu/common/orchestrator/actions/handlers/http.py`,
  `bajutsu/common/assertions/evaluate/_functions.py` (`_json_text`, the existing renderer this item
  reuses), `bajutsu/common/orchestrator/loop/_step_runner.py` (the `try`/`except` around
  `${vars.*}` substitution that a duplicate-`var` check must not raise past)
