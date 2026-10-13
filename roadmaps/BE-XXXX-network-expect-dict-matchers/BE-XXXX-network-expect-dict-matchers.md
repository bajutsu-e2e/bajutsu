**English** · [日本語](BE-XXXX-network-expect-dict-matchers-ja.md)

# BE-XXXX — Match request query and body by key/value dictionaries in network assertions

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-network-expect-dict-matchers.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Verification & coverage |
<!-- /BE-METADATA -->

## Introduction

The `request` assertion and the `event` assertion check a query string or a body through key/value dictionaries, such as `query: { page: "2" }` and `body: { user: { id: "42" } }`. Authors write a nested field as a nested YAML map, so the matcher mirrors the shape of the JSON it checks. Each leaf value is either a literal (exact text match) or a small operator map (`$matches` for a regex, `$exists` for presence). One shared matcher serves both assertions. The change covers the assertion side alone: `mocks` keep their current grammar.

## Motivation

Today the `request` assertion inspects a query string or a body through a regular expression alone. `urlMatches: "page=2&sort=asc"` breaks when the app reorders its parameters. `bodyMatches: '"id":\s*42'` breaks when the JSON is pretty-printed or its keys move. A scenario author has to think about serialization, though the intent is "the request carried `page=2`".

The `event` assertion already shows the better shape. Its `body: { name: purchase_completed, amount: "300" }` parses the JSON request body and compares each key as text, whatever the key order or spacing. That dictionary has three limits: top-level keys of a JSON body, equality comparison, and the `event` assertion alone. Query strings have no dictionary form at all.

The change gives `request` and `event` one common dictionary grammar for query parameters and body fields. Authors write a nested field as a nested YAML map, and a value may be a regex or an existence check when exact equality is too strict.

The observable result is a scenario that survives a harmless change in parameter order or JSON formatting. A check that needed `bodyMatches` with an escaped regex becomes `body: { user: { id: "42" } }`. When one candidate request reached the endpoint, the failure message names the mismatched key, such as `query.page: expected "2", got "3"`. A regex that did not match tells the author far less.

## Detailed design

The following scenario shows every form this item adds.

```yaml
expect:
  - request:
      method: POST
      path: /api/orders
      query: { page: "2", sort: { $matches: "^(asc|desc)$" } }
      body:
        user: { id: "42", email: { $exists: true } }
        items: [ { sku: A-1, qty: "1" } ]
        coupon: { $exists: false }
```

### Fields

| Field | On | Meaning |
|---|---|---|
| `query` | `request`, `event` | Each `key: value` must hold for the URL's query string. Both key and value are URL-decoded (`+` reads as a space, as in `urllib.parse.parse_qs`). A key with an empty or missing value (`?debug`, `?page=`) is present with the value `""`, as `URLSearchParams` reads it (`parse_qs(..., keep_blank_values=True)`). A repeated key matches if any occurrence satisfies the value. |
| `body` | `request` (new), `event` (extended) | Each `key: value` must hold for the parsed JSON request body, recursing into nested maps and lists. |

All criteria stay AND-ed with the existing fields. `bodyMatches` and `urlMatches` keep working unchanged. A matcher that sets only a non-empty `query` or `body` counts as having a criterion, so the "at least one criterion" validators accept it. An empty `query: {}` or `body: {}` is a validation error. An empty dictionary holds for every exchange, so `request: { body: {} }` would match any traffic. The `event` assertion already refuses an empty `body` as its only criterion for the same reason. Because `requestSequence`, `responseSchema.request`, and `until: { request }` reuse the same `RequestMatch` model and `match_request`, they gain `query` and `body` too. The tests and documentation cover them as well.

### Value grammar

A matcher value is one of four forms. The JSON value it is compared with is the field at the same position in the captured body.

| Form | Example | Holds when |
|---|---|---|
| Scalar | `id: "42"` | The text form of the JSON value equals the scalar. |
| Operator map | `id: { $matches: "^4" }` | Every operator holds (see below). |
| Plain map | `user: { id: "42" }` | The JSON value is an object, and each listed key holds recursively. Unlisted keys are ignored. |
| List | `tags: [a, b]` | The JSON value is an array of the same length, and each element holds against the matcher at the same index. |

The scalar comparison is the one `event.body` uses today. `amount: "300"` matches the JSON number `300`, and booleans and null read as `true`, `false`, and `null`. A matcher scalar must be a YAML string, as in `event.body` today. An unquoted number, boolean, or null (`qty: 1`) is a load-time validation error, so authors quote it (`qty: "1"`). The rule keeps a mistyped scalar from silently never matching. The `$exists` operator is the one place a boolean appears.

The two operators are:

- **`$matches: <regex>`.** The text form of the JSON value must contain a match for the regex, like `urlMatches`.
- **`$exists: <bool>`.** `true` requires the key to be present, whatever its value. `false` requires the key to be absent.

A non-empty map whose keys all start with a single `$` is an operator map, and any other map is a plain map. A key starting with `$$` counts as a plain key. The `$` prefix keeps the two apart. Without the prefix, `{ matches: "x" }` could mean the operator or a JSON field named `matches`. A JSON field whose own name starts with `$` is written with a doubled prefix (`$$ref` checks the field `$ref`). An unknown operator, or a map that mixes `$` keys with plain keys, is a scenario validation error. Validation runs at load time, as for every other matcher. The top-level `query` and `body` maps are always key dictionaries, never operator maps. A top-level field named `$x` is written `$$x` there as well. A single-`$` key directly under `query` or `body` is a validation error.

A `query` value accepts a scalar or an operator map. A plain map or a list under `query` is a validation error, because a query string has no nesting.

### Nested maps and lists

A plain map describes a subset of the JSON object: every listed key must hold, and the matcher ignores keys it does not mention. A list is the exception and matches element by element, so `[a, b]` does not match `[a, b, c]`. An author who wants to check one element of a longer array writes `$matches` on the array's text form, or asks for an index form in a later item.

The current `event.body` grammar is the flat case of this grammar, so every existing `event` scenario keeps its meaning. The exception is a top-level body key starting with `$`, which must now be written `$$`. A non-JSON, non-object, or absent body fails every `body` criterion, as it does for `event` today. A `body` criterion on form-encoded bodies is out of scope.

### Shared matcher and reporting

One pure function evaluates a dictionary criterion against an exchange, and `match_request` and the `event` counting path both call it. The assertion label and the HTML report's matcher rendering (`richtext.py`) show the new fields. The failure detail names a mismatched key under one condition: exactly one exchange satisfies the endpoint and method criteria. The detail then gives the first key that did not match and the observed value. When every dictionary key holds on that candidate, the runner keeps today's count message. The failure then lies in `status`, `bodyMatches`, or the one-to-one assignment. With zero or several candidates, the runner keeps today's count message. A guess at which candidate the author meant would mislead more than it helps. When the matcher sets `count`, the runner keeps today's message, since no single key explains a count shortfall. Values accept `${vars.*}` and `${secrets.*}` tokens, as `event.body` values do today. The validated matcher keeps the author's grammar as written, and evaluation alone removes the `$$` escapes. The substitution of `${...}` tokens dumps and re-validates each assertion (`orchestrator/substitution.py`), and that round trip returns the same matcher. A test covers the round trip.

### Mocks and backends

Mocks match requests inside the app, in `BajutsuMocks.swift` on iOS and in `web_network.py` on web. The iOS matcher would ignore a `query` or `body` field in a mock's `match`, which makes the mock broader than written. The `Mock` model refuses both fields with a clear validation error. A later item can add them to mocks together with the Swift matcher.

The Playwright code generator mirrors `match_request` in JavaScript. It must translate the new fields or refuse them explicitly, so a generated test never silently drops a criterion. This design translates them, since the grammar is small. The JavaScript must reproduce the runner's text form of a JSON value (`_json_text`), including number formatting, and a parity test pins the two together.

### Work breakdown

- Models: the `query` and `body` fields, the nested value-grammar validator, and the mock rejection.
- Evaluation: the shared dictionary matcher and its use in `match_request` and the `event` path.
- Reporting: assertion labels, failure details, and report rendering.
- Code generation: the Playwright translation.
- Documentation: `docs/scenarios.md` and `docs/ja/scenarios.md`, plus a showcase scenario that exercises each form.

## Alternatives considered

- **Keep regexes (`urlMatches`, `bodyMatches`) and document recipes.** This leaves the order and formatting sensitivity in place, and the intent stays hidden inside escaping. Rejected.
- **A separate `queryContains` / `bodyContains` string pair.** It repeats the substring weakness of regexes without naming keys. Rejected.
- **Dotted paths as keys (`user.id: "42"`).** A dotted path is compact for one deep field. It breaks when a JSON key itself contains a dot, and it needs its own syntax for array indexes. A nested YAML map follows the grammar authors already write and mirrors the JSON shape. Rejected in favor of nested maps.
- **JSONPath or JMESPath expressions as keys.** Their expressive power exceeds the need. A new query language adds more to learn and to check. Rejected.
- **Operator keys without a prefix (`{ matches: ... }`).** The matcher could not tell the operator from a JSON field named `matches`. Rejected in favor of the `$` prefix.
- **Apply the grammar to mocks in the same change.** It needs a matching Swift implementation and parity tests across backends. The assertion side delivers the main value alone, so mocks stay a follow-up.
- **Add operators such as `$in`, `$not`, `$gt`.** Each operator adds grammar to learn and to test. `$matches` already expresses a set (`^(a|b)$`), and `$exists: false` expresses absence, so the small set stays.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Models: `query` / `body` fields, nested value-grammar validator, mock rejection
- [ ] Evaluation: shared dictionary matcher in `match_request` and the `event` path
- [ ] Reporting: labels, failure details, report rendering
- [ ] Code generation: Playwright translation
- [ ] Documentation and showcase scenario (both languages)

## References

- [BE-0048](../BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions.md) — introduced `event` and its `body` dictionary.
- [BE-0283](../BE-0283-android-network-capture/BE-0283-android-network-capture.md) — network capture on the Android backend, which these assertions read.
- `docs/scenarios.md`, section `request` (network assertion).
