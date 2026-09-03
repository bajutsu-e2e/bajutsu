**English** · [日本語](BE-0410-step-latency-android-device-executor-ja.md)

# BE-0410 — Android on-device step executor inside the instrumentation server

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0410](BE-0410-step-latency-android-device-executor.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0410") |
| Topic | Platform support |
| Related | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md), [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md), [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md), [BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md), [BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md) |
<!-- /BE-METADATA -->

## Introduction

A companion proposal defines a device-side step-execution protocol — what moves from the host to the
device, what stays on the host, and the selector-semantics contract both platforms share — as the
route to Bajutsu's 250–500 millisecond per-step target, past what host-side driver tuning alone can
reach. This item is the Android half of that protocol's implementation: a step executor built into
the resident UI Automator server
([BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md)'s replacement for the
per-invocation `uiautomator dump`), which already runs as an instrumentation with a live
`UiAutomation` session and an accessibility-event listener — the two capabilities this executor needs
and does not have to newly acquire.

## Motivation

Every condition wait on Android today polls the resident server over HTTP, and `settledDump` confirms
a settle by taking two tree dumps and comparing them — an approach that costs a full read twice, even
when nothing changed between them. A companion driver-internal-tuning item already traces most of the
measured 2204-millisecond `POST /act` average to a fixed 2000-millisecond
`POSTDATE_BUDGET_MS` wait that a settled screen can never satisfy before the budget runs out,
pending confirmation from the server's own logs. That same observation — that the server already has
the accessibility-event stream needed to know a settle happened, without polling for it — is the
starting point for this item's design: an executor that answers from the event stream directly instead
of a fixed budget or a two-dump comparison.

The estimate this item should be checked against once built: 20–50 milliseconds for a direct
accessibility-tree walk, 50 milliseconds for input injection, and a settle judgment in the tens of
milliseconds once it comes from the event stream rather than a dump comparison — around 150–300
milliseconds total for a tap step, compared to today's 3.25–3.32 second Android baseline. A later
reader can confirm this by tracing a real tap step against the built executor.

## Detailed design

**Implementation order.** This item is the fourth and last of four related items in a strict order:
the driver-internal-tuning item, the device-side protocol item, the iOS executor item, then this item.
**Work on this item must not begin until the iOS executor item is complete** — sequencing the two
platform executors lets the selector-semantics port (`resolve_unique` to Swift there, to Kotlin here)
happen once, in the iOS executor item, before it is repeated here, so a gap that item's port surfaces
does not have to be independently rediscovered in both platforms at once. This item has no successor
in the sequence.

The resident server already has what an executor needs — it is an instrumentation with a live
`UiAutomation` session and an accessibility-event listener, both already exercised for the existing
`nativeZ` node walk. Four changes turn it into a step executor:

1. **Replace the `dumpWindowHierarchy`-XML tree read with a direct `AccessibilityNodeInfo`
   traversal.** The server already performs the same kind of traversal for `nativeZ`; this generalizes
   it into the primary read path instead of a secondary one, removing the XML serialize/parse round
   trip on both ends.
2. **Evaluate `wait` and `settled` from `TYPE_WINDOW_CONTENT_CHANGED` and `WINDOWS_CHANGED` events**,
   replacing the current two-dump-match approach. This is the same event-driven judgment the
   companion driver-internal-tuning item proposes as a point fix for the `POSTDATE_BUDGET_MS` wait,
   generalized here into the executor's default way of deciding a screen has settled, rather than a
   narrow fix layered on the existing dump-based path.
3. **Inject taps and pans with `UiAutomation.injectInputEvent`**, confirming publication by the event
   type the injection produces rather than a fixed wait or a follow-up read.
4. **Capture screenshots with `UiAutomation.takeScreenshot()`**, returned alongside the step's result
   instead of through a separate `adb exec-out screencap` subprocess call.

Selector resolution ports the host's `resolve_unique` logic
([`bajutsu/common/drivers/base.py`](../../bajutsu/common/drivers/base.py)) to Kotlin, including the
Android-specific derived-label fallback
([`drivers/adb.py:251-282`](../../bajutsu/common/drivers/adb.py)), per the companion protocol item's
shared selector-semantics contract — verified against the driver conformance suite
([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)) the same way the
iOS executor's port is.

## Alternatives considered

- **Fold this item into the `POSTDATE_BUDGET_MS` point fix the driver-internal-tuning item proposes,
  rather than building a full event-driven executor.** Rejected: the point fix targets one wait inside
  today's request/response `POST /act` path and needs confirmation from the server's own logs before
  it can even land; this item is the larger redesign that generalizes the same underlying idea — read
  the event stream instead of polling or budgeting — into the server's whole condition-evaluation
  path. The two are related but not substitutes: the point fix can land first and independently, and
  this item does not depend on it landing first.
- **Keep the two-dump settle comparison and only remove the `POSTDATE_BUDGET_MS` wait.** Rejected as
  the sole fix: even with that wait removed, a two-dump comparison still costs a full tree read twice
  every time a settle is checked, which the event stream this item uses can avoid answering from
  directly.
- **Route Android's executor design through XML instead of `AccessibilityNodeInfo`, to keep the
  parsing code shared with today's host-side parser.** Rejected: the on-device traversal already
  exists for `nativeZ`, so reusing it removes a serialize/parse round trip that keeping XML would
  preserve for no benefit — the host-side parser stays as-is for the `uiautomator dump` fallback path,
  which this item does not touch.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

**Sequence status: blocked on the iOS executor item's completion** (see *Implementation order* in
*Detailed design*). Do not start the checklist below before then.

- [ ] Port `resolve_unique` selector semantics, including the derived-label fallback, to Kotlin,
  verified against the driver conformance suite
  ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)) once the
  protocol item's fixture extension lands.
- [ ] Replace the XML tree read with a direct `AccessibilityNodeInfo` traversal as the primary read
  path.
- [ ] Evaluate `wait` and `settled` from the accessibility-event stream instead of a two-dump
  comparison.
- [ ] Inject taps and pans with `UiAutomation.injectInputEvent`, confirmed by event type.
- [ ] Capture screenshots with `UiAutomation.takeScreenshot()`, returned with the step result.
- [ ] Trace a real tap step against this executor and record the resulting per-step wall-clock here,
  compared against the 150–300 millisecond estimate above.
- [ ] Once the `roadmap-id` workflow allocates the four ids on `main`, backfill a reciprocal
  `Related` link with the driver-internal-tuning, device-side protocol, and iOS executor items (see
  the same box on the driver-internal-tuning item).

## References

[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[BE-0210 — Android on-device actuation fidelity](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md),
[BE-0234 — Speed up adb scenario runs](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md),
[`bajutsu/common/drivers/base.py`](../../bajutsu/common/drivers/base.py),
[`bajutsu/common/drivers/adb.py`](../../bajutsu/common/drivers/adb.py),
[`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt)
