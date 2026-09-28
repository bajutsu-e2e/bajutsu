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
that whole object as text, so a scenario that needs merely `data.token` still has to work around the
wrapping object — by asking the service for a narrower response shape it may not offer, or by
giving up on the field it needs — a field no step can reach today. Once `extractBody` ships, a
scenario points an `http` step at a JSON API and reads a nested field directly into
`${vars.token}`.

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
  as-is; it stores any other JSON value (number/boolean/null/object/array) as the compact JSON text
  `json.dumps` produces for it: `42`, `true`, `null`, `{"id":42}`. A later `${vars.*}` comparison
  then reads the same shape the API returned, never a Python-specific rendering like `None` or
  `True`.
- **`path`** is a sequence of object keys and `[n]` (zero-based) array indexes, in any order and any
  number: a bare key starts the path (`data.token`), an index may follow a key (`items[0].id`) or
  another index (`rows[0][1]`), and a path may open on an index when the response body is itself a
  JSON array (`[0].id`). The grammar stops there: no wildcards, no filters, no computed segments.
  BE-0036 already rejected a general `shell`/`exec` step on the same grounds (see its *Alternatives
  considered*): a small, fixed grammar stays auditable from the scenario file alone, where a
  general expression language would not.
- A response body that fails to parse as JSON, or a `path` that does not resolve — a missing key, an
  out-of-range index, or a key applied to an array (or an index applied to an object) — fails the
  step with an error naming the `var` and the `path` that did not resolve. A scenario never reads a
  placeholder or an empty value in place of the field it asked for.
- `extractBody` and `saveBody` read the same response body independently; setting both stores the
  whole text under `saveBody`'s name and the named fields under `extractBody`'s, from one request.
  Two `extractBody` entries with the same `var`, or an entry whose `var` equals `saveBody`, is a
  scenario load error — never a silent overwrite decided by write order.

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

- [ ] Add the `extractBody` field to the `http` step's scenario model, alongside `saveBody`; reject
      a duplicate `var` across `extractBody` entries or a `var` colliding with `saveBody` at load
      time.
- [ ] Add JSON parsing and path resolution to the runner's `http` handler, including a leading
      index and chained indexes; fail the step on a parse error or an unresolved path, and render a
      non-string resolved value with `json.dumps`.
- [ ] Document `extractBody` in `docs/scenarios.md` and its `docs/ja/` mirror, beside `saveBody`.
- [ ] Add scenario-level tests covering:
      - a resolved nested field
      - a path opening on an array index, and a chained index
      - a non-string resolved value (`json.dumps` rendering)
      - a missing key and an out-of-range index
      - a key applied to an array, or an index applied to an object
      - a non-JSON body

## References

- [BE-0036 — HTTP utility step](../BE-0036-utility-steps/BE-0036-utility-steps.md) — the parent
  item; its own *Detailed design* already flags per-field extraction as unimplemented future work.
- [BE-0046 — OTP & email side-channel steps](../BE-0046-otp-email-steps/BE-0046-otp-email-steps.md) —
  the `email` step's `bodyMatches` regex extraction, the precedent this item's *Alternatives
  considered* compares `extractBody` against.
- [`docs/scenarios.md`](../../docs/scenarios.md), `bajutsu/common/scenario/models/actions/http_request.py`,
  `bajutsu/common/orchestrator/actions/handlers/http.py`
