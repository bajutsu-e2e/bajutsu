**English** · [日本語](BE-0408-step-latency-device-executor-protocol-ja.md)

# BE-0408 — Add a device-side step-execution protocol as the foundation for on-device step loops

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0408](BE-0408-step-latency-device-executor-protocol.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0408") |
| Implementing PR | [#1949](https://github.com/bajutsu-e2e/bajutsu/pull/1949) |
| Topic | Platform support |
| Related | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md), [BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md), [BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md) |
<!-- /BE-METADATA -->

## Introduction

This item defines a protocol and selector semantics that move condition evaluation onto the device. They fix what moves to the device and what stays on the host. They also fix how a selector resolves identically on both sides. Two platform-specific items build on it: [BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md), an iOS executor inside the XCTest runner, and [BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md), an Android executor inside the resident instrumentation server. Neither platform implementation is in this item's scope.

The need comes from a performance investigation recorded under [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md), which proposes driver-internal tuning. The investigation measured a real `tap` step. Bajutsu targets 250–500 milliseconds for end-to-end step execution, evidence capture included. BE-0407's reductions still leave iOS at roughly 0.3–0.6 seconds per step and Android at roughly 0.6–1.2 seconds. Both miss the target. Every step still pays at least one host-device round trip for each condition it polls. Closing the gap needs the condition evaluated on the device, not polled from the host.

## Motivation

A `wait for` step polls the host every 50 milliseconds until its selector matches. A `wait until: settled` step polls similarly. Each poll is a full host-device round trip. On iOS, a poll is a fresh HTTP request into the XCTest runner. On Android, a poll is a request into the resident instrumentation server.

Driver-internal tuning cannot remove this polling loop. Evaluating the awaited condition needs a device read, and today the host alone performs that read. A screen-closed `assert` (`exists`, `label`, `value`, `count`, `enabled`) has the same limit, because evaluating it needs the same host-owned device read.

Moving that evaluation onto the device removes the polling loop entirely. A condition-evaluating step becomes one round trip instead of many: the host sends the condition and receives the result once it is satisfied.

This move does not relax prime directive 1 ("AI is the author and the failure investigator, never the judge"). That directive forbids an AI from deciding pass/fail. It does not require the host process to evaluate a condition.

A device-side executor resolves a selector and evaluates a wait or an `assert` deterministically. It is exactly as deterministic as the host doing the same read today. The code that walks the accessibility tree stays deterministic when it runs on-device instead of over HTTP.

The verifiable outcome this protocol design should produce: once a platform implementation lands and exercises the protocol, a `wait for` step's round-trip count drops from one per 50-millisecond poll to one per step. [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md) did the same when it collapsed a multi-round-trip element read into a single snapshot.

## Detailed design

**Implementation order.** This item is the second of four related items, which proceed in a strict order:

1. The driver-internal-tuning item ([BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md)).
2. This item.
3. The iOS executor ([BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md)).
4. The Android executor ([BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md)).

**Work on this item must not begin until BE-0407 is complete.** This item's design picks up from BE-0407's shipped, measured baseline. A protocol designed against a moving baseline risks resting on numbers that change under it. For the same reason, BE-0409 must not begin until this item is complete. An executor built against a moving protocol design needs rework whenever the protocol changes.

### What moves, and what does not

Python keeps three responsibilities that no protocol change should move off the host:

- Expanding a scenario into the steps a device-side executor can run. The scenario format and its expansion rules stay host-owned.
- Receiving the evidence the device returns and writing it into `manifest.json` and the HTML report. The evidence is the resolved element tree, coordinates, a screenshot, and the timestamp a condition was satisfied at. Writing is unchanged from today.
- Deciding pass/fail. A device-side evaluation result is an input. The host re-checks it against the same deterministic rule the host already applies, and the host, not the device, still renders the final verdict.

These move to the device:

- Selector resolution.
- `wait for`, `until: gone`, and `until: settled`.
- A screen-closed `assert` (`exists`, `label`, `value`, `count`, `enabled`).
- The four actuation kinds (`tap`, `type`, `swipe`, `scroll`).

An `assert` of `http`, `email`, `generate`, `visual`, `golden`, or `request` stays on the host. None of those reads the device under test at all.

### Selector semantics as the shared contract

Today, `find_all` and `resolve_unique` in [`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py) are the single definition of what a selector means. That definition covers `within`, `idMatches`, `labelMatches`, and the trait derivations. On Android, it also covers the derived-label fallback in [`drivers/adb/_functions.py`](../../bajutsu/common/drivers/adb/_functions.py).

A device-side executor needs its own copy of this logic in Swift and Kotlin. Both copies must resolve every selector to the same element the host's copy would. Both must fail the same way on an ambiguous match. A selector match is the kind of deterministic judgment prime directive 1 requires. A disagreement between device-side and host-side resolution would be a determinism regression, not a performance one.

The existing tool for checking this is the driver conformance suite of [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md). Once a device-side resolver exists, the suite can run the same selector fixtures against it. A platform implementation then proves equivalence rather than asserting it.

### Staged protocol rollout

Each stage below is independently useful and removes one class of round trip. A platform implementation can land the stages incrementally, not as one large protocol change:

1. **`wait for` and `until: gone` move first.** A `POST /wait` request carries the selector and the timeout. The device polls internally and returns once the condition is satisfied or the wait times out. This stage alone removes every 50-millisecond host poll a wait step pays today.
2. **`until: settled` moves next.** iOS receives the screen-transition signal ([BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md)) directly instead of the host relaying it. Android evaluates settle from its own accessibility event stream, which the resident server already listens to.
3. **Screen-closed `assert` kinds move next.** They are evaluated against the device's own tree read, not one shipped back to the host first.
4. **Steps bundle into one `POST /scenario` call.** Once individual step kinds resolve on-device, a scenario segment can be sent as a sequence. It then executes without a host round trip per step. At that point a step costs at most one round trip, which matches this item's motivation.

### Not in this item's scope

This item defines the protocol and the selector-semantics contract alone. Two runtimes implement the protocol: the iOS executor inside the XCTest runner process and the Android executor inside the resident instrumentation server. Each is its own separate, larger proposal. Each needs an on-device design specific to its platform: event injection, accessibility tree access, and screenshot capture.

## Alternatives considered

- **Build an in-app executor (inside BajutsuKit or the Android on-device SDK) instead of a process-boundary one.** Rejected, for two independent reasons.
  - An in-app executor cannot inject synthetic touch or keyboard events, cannot read a frame-bearing accessibility tree, and cannot take a screenshot from inside the app process. Only the XCTest runner process and Android instrumentation can. An in-app executor also cannot see system surfaces (a permission dialog, the system keyboard, `SFSafariViewController` content) or observe the app crashing. Both need a process boundary that the app itself sits on the far side of. An in-app SDK's role stays what it is today, an observation aid like the screen-transition signal, not an actuator.
  - App-agnosticism (prime directive 3) is the second reason. An in-app executor would need embedding into every application under test, and an application without that embedding would lose on-device execution entirely. A process-boundary executor drives whatever app is under test, the same way the existing XCTest runner and resident instrumentation server already do.
- **Skip the protocol design and let each platform invent its own wire format.** Rejected: the `Driver` protocol layer already unifies the two platforms' selector semantics, wait kinds, and assert kinds. Diverging that unification at the device-executor layer would reintroduce the per-platform special-casing this proposal exists to avoid.
- **Fold this proposal into the two platform-specific executor items instead of keeping it separate.** Rejected: the selector-semantics contract and the staged rollout order apply to both platforms identically. Stating them once here, referenced by both platform items, avoids two copies of the same design drifting apart.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

**Sequence status: BE-0407 completed** ([#1944](https://github.com/bajutsu-e2e/bajutsu/pull/1944)), which unblocked this item (see *Implementation order* in *Detailed design*). Every box below is now checked.

- [x] Write the wire format for stage 1 (`POST /wait`) and agree it across both platform items before
  either begins implementing. Shipped as one OpenAPI 3.1 document,
  [`protocol/device-executor.openapi.yaml`](protocol/device-executor.openapi.yaml) (companion
  narrative in [`protocol/README.md`](protocol/README.md)), rather than prose alone — pinned by a
  `jsonschema` conformance test (`tests/test_be0408_protocol_fixtures.py`) so a future BE-0409 or
  BE-0410 PR that drifts from the shared schema fails the fast gate.
- [x] Port `find_all` / `resolve_unique` selector semantics to a shared design document precise
  enough for two independent (Swift and Kotlin) implementations to agree on edge cases (ambiguous
  matches, trait derivation, the Android derived-label fallback). Shipped as a new *Porting
  contract for a device-side resolver* section in [`docs/selectors.md`](../../docs/selectors.md)
  (and its [Japanese mirror](../../docs/ja/selectors.md)), covering every edge case named here plus
  two Swift behaviors a port must deliberately not close (`PositionPath`'s frame tolerance and
  ordered-trait comparison).
- [x] Extend the driver conformance suite ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md))
  with a fixture set a device-side resolver can be run against once one exists. Shipped as a
  sibling fixture set, [`tests/fixtures/be0408/`](../../tests/fixtures/be0408/), rather than inside
  BE-0114's `DriverConformanceContract`. That contract requires a live `Driver`, which no
  device-side resolver has yet. Selector resolution is already backend-agnostic — every backend
  shares one `resolve_unique` — so running the same fixtures once per backend would add no
  information a per-backend harness could report differently. `tests/test_selector_fixtures.py`
  replays every case against the real implementation today, so the fixture set cannot itself drift
  from what it is meant to pin.
- [x] Define the wire format for stages 2–4 (`settled`, screen-closed `assert`, `POST /scenario`).
  Base it on this document's own stage 1 design and BE-0409/BE-0410's own written proposals, not on
  a real implementation's feedback. BE-0409 cannot start until this item is complete, so no such
  feedback yet exists. Shipped in the same OpenAPI document as stage 1 (`POST
  /assert`, `POST /scenario`; `settled` folds into `POST /wait` as a fourth mode) — see
  `protocol/README.md`'s *Revision history* for how a later implementation's findings feed back
  into a dated revision of this document, rather than being a precondition for writing it once.
- [x] Once the `roadmap-id` workflow allocates the four ids on `main`, backfill a reciprocal
  `Related` link with BE-0407, BE-0409, and BE-0410 (see the same box on BE-0407).

Log:

- [#1949](https://github.com/bajutsu-e2e/bajutsu/pull/1949) completes all five units. It ships:
  - the language-neutral selector-resolution fixtures (`tests/fixtures/be0408/`) and their replay test against `find_all` / `resolve_unique` / `parse_hierarchy`;
  - the *Porting contract for a device-side resolver* section in `docs/selectors.md`;
  - a draft shared OpenAPI 3.1 protocol document (`protocol/device-executor.openapi.yaml` + `protocol/README.md`) covering stages 1–4;
  - the document's own conformance fixtures and test (`tests/fixtures/be0408/protocol/`, `tests/test_be0408_protocol_fixtures.py`).

  The protocol document brings Android into the same JSON contract as iOS (org.json now, with an `openapi-generator`-Kotlin-models recommendation for BE-0410 to evaluate). It uses a `pollBudgetMs` polling design in place of single-call cancellation.

  The PR also fixed the stale `bajutsu/common/drivers/base.py` / `drivers/adb.py` citations throughout (packaged per-class since BE-0411) and a self-contradictory checklist item. A three-round self-review pass against the CI review contract found and fixed 25 issues before the gate ran. Most concerned the draft OpenAPI schema's own structural rigor: untyped fields accepting `null`, unenforced "exactly one of" rules, and an unvalidated bundle payload.

## References

[BE-0105 — Single-snapshot XCUITest query](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md),
[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[BE-0310 — iOS accessibility screen-change readiness](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md),
[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py),
[`bajutsu/common/drivers/adb/_functions.py`](../../bajutsu/common/drivers/adb/_functions.py),
[`protocol/device-executor.openapi.yaml`](protocol/device-executor.openapi.yaml),
[`docs/selectors.md`](../../docs/selectors.md)
