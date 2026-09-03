**English** · [日本語](BE-XXXX-step-latency-device-executor-protocol-ja.md)

# BE-XXXX — Add a device-side step-execution protocol as the foundation for on-device step loops

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-step-latency-device-executor-protocol.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) |
<!-- /BE-METADATA -->

## Introduction

A performance investigation, recorded under a companion item proposing driver-internal tuning,
measured a real `tap` step against the 250–500 millisecond target
Bajutsu sets for end-to-end step execution, evidence capture included. That companion item's
reductions still leave iOS at roughly 0.3–0.6 seconds per step and Android at roughly 0.6–1.2
seconds — short of the target, because every step still pays at least one host-device round trip for
each condition it polls. Closing the remainder needs the condition itself evaluated on the device,
not polled from the host. This item defines the protocol and selector semantics that make that
possible: what moves to the device, what stays on the host, and how a selector resolves identically
on both sides. It is the shared foundation two platform-specific companion items (an iOS executor
inside the XCTest runner, an Android executor inside the resident instrumentation server) build on;
neither platform implementation is in this item's scope.

## Motivation

A `wait for` step polls the host every 50 milliseconds until its selector matches; a `wait until:
settled` step polls similarly. Each poll is a full host-device round trip — on iOS, a fresh HTTP
request into the XCTest runner; on Android, a request into the resident instrumentation server. No
amount of driver-internal tuning removes this polling loop, because the condition being waited on can
only be evaluated by reading the device, and today only the host does that reading. The same is true
of a screen-closed `assert` (`exists`, `label`, `value`, `count`, `enabled`): evaluating it needs a
device read the host currently owns.

Moving that evaluation onto the device removes the polling loop entirely — a condition-evaluating
step becomes one round trip (send the condition, receive the result once satisfied) instead of many.
This does not relax prime directive 1 ("AI is the author and the failure investigator, never the
judge"): that directive says an AI must never decide pass/fail, not that the host process must be the
one to evaluate a condition. A device-side executor that deterministically resolves a selector and
evaluates a wait or an `assert` is exactly as deterministic as the host doing the same read today —
the code that walks the accessibility tree does not become non-deterministic by running on-device
instead of over HTTP. The verifiable outcome this item's protocol design should produce, once a
platform implementation lands and exercises it: a `wait for` step's round-trip count drops from one
per 50-millisecond poll to one per step, the same way [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md)
collapsed a multi-round-trip element read into a single snapshot.

## Detailed design

**Implementation order.** This item is the second of four related items in a strict order: the
driver-internal-tuning item, then this item, then the iOS executor item, then the Android executor
item. **Work on this item must not begin until the driver-internal-tuning item is complete** — this
item's design is meant to pick up from that item's shipped, measured baseline, and starting protocol
design against a baseline that is still moving risks designing around numbers that change under it.
Once this item ships, the iOS executor item must not begin until this item is complete either, for the
same reason: an executor built against a still-moving protocol design would need rework whenever the
protocol changed under it.

### What moves, and what does not

Python keeps three responsibilities no protocol change should move off the host:

- Expanding a scenario into the sequence of steps a device-side executor can run, since the scenario
  format and its expansion rules stay host-owned.
- Receiving whatever evidence the device returns — the resolved element tree, coordinates, a
  screenshot, the timestamp a condition was satisfied at — and writing it into `manifest.json` and the
  HTML report, unchanged from how evidence is written today.
- Deciding pass/fail. A device-side evaluation result is an input the host re-checks against the same
  deterministic rule the host already applies; the host, not the device, still renders the final
  verdict.

Selector resolution, `wait for`, `until: gone`, `until: settled`, a screen-closed `assert`
(`exists`, `label`, `value`, `count`, `enabled`), and the four actuation kinds (`tap`, `type`,
`swipe`, `scroll`) move to the device. An `assert` of `http`, `email`, `generate`, `visual`,
`golden`, or `request` stays on the host — none of those reads the device under test at all.

### Selector semantics as the shared contract

`find_all` and `resolve_unique` in [`bajutsu/common/drivers/base.py`](../../bajutsu/common/drivers/base.py)
are today's single definition of what a selector means: `within`, `idMatches`, `labelMatches`, the
trait derivations, and — on Android — the derived-label fallback in
[`drivers/adb.py:251-282`](../../bajutsu/common/drivers/adb.py). A device-side executor needs its own
copy of this logic in Swift and Kotlin, and the two copies must resolve every selector to the same
element the host's copy would, including failing the same way on an ambiguous match — a selector
match is exactly the kind of deterministic judgment prime directive 1 requires, so a device-side and a
host-side resolution disagreeing on it would be a determinism regression, not a performance one.
[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)'s driver
conformance suite is the existing tool for checking this: extending it to run the same selector
fixtures against a device-side resolver, once one exists, is how a platform implementation proves
equivalence rather than asserting it.

### Staged protocol rollout

Each stage below is independently useful and removes one class of round trip, so a platform
implementation can land them incrementally rather than as one large protocol change:

1. **`wait for` and `until: gone` move first.** A `POST /wait` request carries the selector and the
   timeout; the device polls internally and returns once satisfied or timed out. This alone removes
   every 50-millisecond host poll a wait step pays today.
2. **`until: settled` moves next.** iOS receives the screen-transition signal
   ([BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md))
   directly instead of the host relaying it; Android evaluates settle from its own accessibility
   event stream, the same stream the resident server already listens to.
3. **Screen-closed `assert` kinds move next**, evaluated against the device's own tree read rather
   than one shipped back to the host first.
4. **Steps bundle into one `POST /scenario` call.** Once individual step kinds resolve on-device, a
   scenario segment can be sent as a sequence and executed without an intervening host round trip per
   step — the point at which a step costs at most one round trip, matching this item's motivation.

### Not in this item's scope

This item defines the protocol and the selector-semantics contract only. Building the two runtimes
that implement it — the iOS executor inside the XCTest runner process and the Android executor inside
the resident instrumentation server — is each its own separate, larger proposal, since each needs
its own on-device design (event injection, accessibility tree access, screenshot capture) specific to
its platform.

## Alternatives considered

- **Build an in-app executor (inside BajutsuKit or the Android on-device SDK) instead of a
  process-boundary one.** Rejected: an in-app executor cannot inject synthetic touch or keyboard
  events, cannot read a frame-bearing accessibility tree, and cannot take a screenshot from inside the
  app process — only the XCTest runner process and Android instrumentation can. It also cannot see
  system surfaces (a permission dialog, the system keyboard, `SFSafariViewController` content) or
  observe the app crashing, both of which need a process boundary the app itself is on the other side
  of. An in-app SDK's role stays what it is today — an observation aid, like the screen-transition
  signal — not an actuator.
  App-agnosticism (prime directive 3) is a second, independent reason: an in-app executor would need
  embedding into every application under test, and an application without that embedding would lose
  on-device execution entirely. A process-boundary executor drives whatever app is under test, the
  same way the existing XCTest runner and resident instrumentation server already do.
- **Skip the protocol design and let each platform invent its own wire format.** Rejected: the two
  platforms' selector semantics, wait kinds, and assert kinds are already unified at the `Driver`
  protocol layer, and diverging that unification at the device-executor layer would reintroduce
  per-platform special-casing this proposal exists to avoid.
- **Fold this proposal into the two platform-specific executor items instead of keeping it
  separate.** Rejected: the selector-semantics contract and the staged rollout order apply to both
  platforms identically, and stating them once here, referenced by both platform items, avoids two
  copies of the same design drifting apart.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

**Sequence status: blocked on the driver-internal-tuning item's completion** (see *Implementation
order* in *Detailed design*). Do not start the checklist below before then.

- [ ] Write the wire format for stage 1 (`POST /wait`) and agree it across both platform items before
  either begins implementing.
- [ ] Port `find_all` / `resolve_unique` selector semantics to a shared design document precise
  enough for two independent (Swift and Kotlin) implementations to agree on edge cases (ambiguous
  matches, trait derivation, the Android derived-label fallback).
- [ ] Extend the driver conformance suite ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md))
  with a fixture set a device-side resolver can be run against once one exists.
- [ ] Define the wire format for stages 2–4 (`settled`, screen-closed `assert`, `POST /scenario`),
  informed by whatever stage 1 and the platform items learn from a real implementation.

## References

[BE-0105 — Single-snapshot XCUITest query](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md),
[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[BE-0310 — iOS accessibility screen-change readiness](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md),
[`bajutsu/common/drivers/base.py`](../../bajutsu/common/drivers/base.py),
[`bajutsu/common/drivers/adb.py`](../../bajutsu/common/drivers/adb.py)
