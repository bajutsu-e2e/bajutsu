**English** · [日本語](BE-0048-behavioral-protocol-assertions-ja.md)

# BE-0048 — Behavioral / protocol assertions

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0048](BE-0048-behavioral-protocol-assertions.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Accepted, in progress** |
| Track | [Accepted](../../README.md#accepted) |
| Topic | Candidates from competitive research (Maestro) |
| Origin | Maestro |
<!-- /BE-METADATA -->

## Introduction

Assert on what the app actually *did*, not only on what the screen shows: analytics /
telemetry events that were sent, the schema of a response the app received, and the order and
count of requests it made. Every check is a pure, deterministic function over the network
exchanges Bajutsu already captures (`network.json`) — no LLM, so it fits the Tier-2 run/CI gate.

## Motivation

Maestro and most UI-layer E2E tools verify only the visible surface: an element exists, a label
reads a certain way, a screenshot matches. But a large class of requirements is about behavior
the screen never shows — that a `purchase_completed` analytics event fired exactly once with the
right amount, that the response feeding a list conformed to its agreed schema, that a token
refresh happened before the protected call and not twice. These pass every on-screen check while
the actual contract is broken.

Bajutsu is unusually well-placed here. It already observes the app's own traffic
(`bajutsu/network.py` + the `BajutsuKit` in-app collector) and offers a `request` assertion,
`wait: { until: request }`, and deterministic `mocks`. What is thin today is the *assertion
surface* over that captured data: it matches little beyond method / URL / status. Deepening it
is the project's clearest moat. Maestro positions itself explicitly as "UI-layer automation, not
code instrumentation" — which means it structurally cannot assert on an analytics payload or a
response body's shape without abandoning its founding premise. Extending the network-truth
assertions reframes E2E from "drive the screen and look" to "verify the app's observable
contract", a differentiation a no-instrumentation competitor cannot copy.

## Detailed design

This is a proposal at design altitude. Everything below evaluates against the **already-captured**
exchanges, so the verdict stays a machine check with no model in the loop.

New scenario-level `expect` forms (names settle on adoption), all reading the recorded network
timeline:

```yaml
expect:
  # An analytics/telemetry event was sent — match endpoint + body fields, with a count.
  - event:
      url: "https://t.example.com/track"
      body: { name: "purchase_completed", amount: "300" }   # exact or ${vars.*} match
      count: { equals: 1 }
  # A captured response body conforms to a JSON Schema.
  - responseSchema:
      request: { method: GET, urlMatches: ".*/api/items" }
      schema: schemas/items.json        # relative to the app's schema dir
  # Requests happened in the expected order and multiplicity.
  - requestSequence:
      - { method: POST, urlMatches: ".*/auth/refresh" }
      - { method: GET,  urlMatches: ".*/api/account" }
```

- **Evaluation.** Pure functions in `bajutsu/assertions.py` over the parsed `network.json`
  exchanges (the same data `request` already reads). `event` / `requestSequence` are
  field- and order-matching; `responseSchema` validates a captured body against a stored JSON
  Schema. Deterministic given the same recording — the run/CI verdict stays machine-only.
- **Redaction.** Payload matching reuses `bajutsu/redaction.py`, so values asserted on are still
  masked in written evidence (the assertion sees the captured exchange; the report sees the
  redacted form).
- **App-agnostic.** Which endpoints carry analytics, and where schema files live, is per-app
  config (`apps.<name>`); the assertion machinery is identical across apps.
- **Relationship to siblings.** This is the *observe-and-verify* counterpart to the
  fetch-oriented utility steps ([BE-0036](../../implemented/BE-0036-utility-steps/BE-0036-utility-steps.md)):
  those steps *fetch* a side-channel value into `${vars.*}`; these assertions *verify* the app's
  own traffic. It also complements the deterministic, non-structural
  [BE-0029](../../implemented/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md)
  visual-regression assertion — both check things the accessibility tree cannot express, without
  an LLM.

### Implementation status

The **`event`** assertion shipped first (`bajutsu/scenario/models/assertions.py` `EventMatch` +
`CountOp`; `bajutsu/assertions.py` `_eval_event`): it filters the captured timeline by the event's
endpoint — reusing the existing `RequestMatch` matcher — then by structured JSON request-body fields,
and checks the surviving count against an `equals` / `atLeast` / `atMost` operator (default: at least
one). `${vars.*}` / `${secrets.*}` tokens interpolate into body values through the existing assertion
substitution path, so no per-field wiring was needed.

The **`requestSequence`** assertion then shipped (`Assertion.request_sequence`;
`bajutsu/assertions.py` `_eval_request_sequence`): a non-empty list of `RequestMatch`es matched as an
ordered subsequence over the timeline (each matcher matches a distinct exchange strictly after the
previous; unrelated traffic may interleave). It reuses `match_request`, adds no new dependency, and is
pure — order is its job, so a matcher's own `count` is ignored.

Both are AI-free and on the Tier-2 run/CI gate like every other assertion.

Deferred to a later slice: **`responseSchema`** (validate a captured response body against a stored
JSON Schema — carries a new schema-validation dependency, an `apps.<name>.schemas` config dir, and
runner wiring, so it is a separate slice).

## Alternatives considered

* **Let an LLM read the payloads and judge "did the right thing happen?"** Rejected: an LLM in
  the pass/fail gate violates prime directive #1 and makes the verdict non-reproducible. The
  whole point is that protocol checks are *more* deterministic than UI checks, not less.
* **Shell out to an external contract-testing tool around the run.** Rejected: it moves the
  assertion out of the scenario YAML — the shared hub — so the executed contract is no longer
  visible in the file or the report, and it couples the test to the CI host.
* **Keep only the status-code-level `request` assertion (status quo).** Acceptable for smoke
  coverage but leaves the deepest differentiation on the table; the network data is already
  captured, so the marginal cost of asserting over it is low.

## References

`bajutsu/network.py`, `bajutsu/assertions.py`, `bajutsu/redaction.py`,
[`BajutsuKit`](../../../BajutsuKit/README.md), [evidence.md](../../../docs/evidence.md),
[DESIGN §2 / §6.4](../../../DESIGN.md)
