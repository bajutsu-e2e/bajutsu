**English** · [日本語](BE-0048-behavioral-protocol-assertions-ja.md)

# BE-0048 — Behavioral / protocol assertions

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0048](BE-0048-behavioral-protocol-assertions.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0048") |
| Implementing PR | [#205](https://github.com/bajutsu-e2e/bajutsu/pull/205) · [#208](https://github.com/bajutsu-e2e/bajutsu/pull/208) · [#212](https://github.com/bajutsu-e2e/bajutsu/pull/212) |
| Topic | Verification & coverage |
| Origin | Maestro |
<!-- /BE-METADATA -->

## Introduction

This item lets scenarios assert on what the app actually *did*, not only on what the screen shows.
The assertions cover three targets: analytics / telemetry events that were sent, the schema of a
response the app received, and the order and count of requests the app made. Every check is a pure,
deterministic function over the network exchanges that Bajutsu already captures (`network.json`). No
LLM runs in these checks, so they fit the Tier-2 run/CI gate.

## Motivation

Maestro and most UI-layer E2E tools verify the visible surface alone: an element exists, a label
reads a certain way, a screenshot matches. Yet a large class of requirements concerns behavior
that the screen never shows. Three examples:

- A `purchase_completed` analytics event fired exactly once with the right amount.
- The response feeding a list conformed to its agreed schema.
- A token refresh happened before the protected call, and not twice.

These requirements pass every on-screen check while the actual contract is broken.

Bajutsu is unusually well placed here. It already observes the app's own traffic
(`bajutsu/network.py` plus the `BajutsuKit` in-app collector). It also offers a `request` assertion,
`wait: { until: request }`, and deterministic `mocks`. What is thin today is the *assertion surface*
over that captured data, which matches little beyond method / URL / status.

Deepening that surface is the project's clearest moat. Maestro positions itself explicitly as
"UI-layer automation, not code instrumentation". It structurally cannot assert on an analytics
payload or a response body's shape without abandoning its founding premise. Extending the
network-truth assertions reframes E2E from "drive the screen and look" to "verify the app's
observable contract". A no-instrumentation competitor cannot copy that differentiation.

## Detailed design

This section is a proposal at design altitude. Everything below evaluates against the
**already-captured** exchanges, so the verdict stays a machine check with no model in the loop.

The design adds new scenario-level `expect` forms (names settle on adoption). Each form reads the
recorded network timeline:

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

- **Evaluation.** The checks are pure functions in `bajutsu/assertions.py` over the parsed
  `network.json` exchanges, the same data that `request` already reads. `event` / `requestSequence`
  match fields and order. `responseSchema` validates a captured body against a stored JSON Schema.
  The result is deterministic given the same recording, so the run/CI verdict stays machine-only.
- **Redaction.** Payload matching reuses `bajutsu/redaction.py`, so values asserted on are still
  masked in written evidence. The assertion sees the captured exchange, and the report sees the
  redacted form.
- **App-agnostic.** Which endpoints carry analytics, and where schema files live, is per-app config
  (`apps.<name>`). The assertion machinery is identical across apps.
- **Relationship to siblings.** These assertions are the *observe-and-verify* counterpart to the
  fetch-oriented utility steps ([BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps.md)). Those
  steps *fetch* a side-channel value into `${vars.*}`, while these assertions *verify* the app's own
  traffic. They also complement the deterministic, non-structural
  [BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md)
  visual-regression assertion. Both check things that the accessibility tree cannot express, without
  an LLM.

## Alternatives considered

* **Let an LLM read the payloads and judge "did the right thing happen?"** Rejected. An LLM in the
  pass/fail gate violates prime directive #1 and makes the verdict non-reproducible. The whole point
  is that protocol checks are *more* deterministic than UI checks, not less.
* **Shell out to an external contract-testing tool around the run.** Rejected. It moves the
  assertion out of the scenario YAML, the shared hub, so the executed contract is no longer visible
  in the file or the report. It also couples the test to the CI host.
* **Keep the status-code-level `request` assertion alone (status quo).** This is acceptable for
  smoke coverage but leaves the deepest differentiation on the table. The network data is already
  captured, so the marginal cost of asserting over it is low.

## Progress

- [x] `event` assertion — filters the captured timeline by endpoint and JSON body fields, checking the surviving count against `equals` / `atLeast` / `atMost`.
- [x] `requestSequence` assertion — an ordered subsequence over the timeline (unrelated traffic may interleave).
- [x] `responseSchema` assertion — validates the first matching response against a stored JSON Schema, via a lazily imported `jsonschema` extra.
- All three are AI-free and on the Tier-2 run/CI gate, like every other assertion.

## References

- `bajutsu/network.py`, `bajutsu/assertions.py`, `bajutsu/redaction.py`
- [`BajutsuKit`](../../BajutsuKit/README.md), [evidence.md](../../docs/evidence.md)
- [DESIGN §2 / §6.4](../../DESIGN.md)
