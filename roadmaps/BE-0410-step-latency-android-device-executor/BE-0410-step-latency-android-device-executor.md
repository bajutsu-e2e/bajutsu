**English** · [日本語](BE-0410-step-latency-android-device-executor-ja.md)

# BE-0410 — Android on-device step executor inside the instrumentation server

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0410](BE-0410-step-latency-android-device-executor.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0410") |
| Topic | Platform support |
| Related | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md), [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md), [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md), [BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md), [BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md) defines a device-side step-execution protocol. The protocol fixes what moves from the host to the device, what stays on the host, and the selector-semantics contract that both platforms share. The protocol is the route to Bajutsu's 250–500 millisecond per-step target. Host-side driver tuning alone cannot reach that target.

This item is the Android half of that protocol's implementation. It builds a step executor into the resident UI Automator server. [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md) introduced that server to replace the per-invocation `uiautomator dump`. The server already runs as an instrumentation with a live `UiAutomation` session and an accessibility-event listener. The executor needs these two capabilities and no others, so it does not have to add either one.

## Motivation

Every condition wait on Android today polls the resident server over HTTP. `settledDump` confirms a settle by taking two tree dumps and comparing them. That approach pays for a full read twice, even when nothing changed between the dumps.

[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md) already traces most of the measured 2204-millisecond `POST /act` average to a fixed 2000-millisecond `POSTDATE_BUDGET_MS` wait. A settled screen cannot end that wait early. The trace still awaits confirmation from the server's own logs.

The same observation starts this item's design. The server already has the accessibility-event stream that shows a settle happened, so it does not need to poll. The item proposes an executor that answers from the event stream directly, in place of a fixed budget or a two-dump comparison.

Once the executor is built, check it against this estimate for a tap step:

- 20–50 milliseconds for a direct accessibility-tree walk
- 50 milliseconds for input injection
- a settle judgment in the tens of milliseconds, once it comes from the event stream rather than a dump comparison
- around 150–300 milliseconds for the whole step, compared to today's 3.25–3.32 second Android baseline

A later reader can confirm the estimate by tracing a real tap step against the built executor.

## Detailed design

**Implementation order.** This item is the fourth and last of four related items. The order is strict: BE-0407, BE-0408, the iOS executor ([BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md)), then this item. No item follows this one in the sequence.

**Work on this item must not begin until BE-0409 is complete.** Sequencing the two platform executors lets the selector-semantics port happen once, in BE-0409, before it is repeated here. The port moves `resolve_unique` to Swift there and to Kotlin here. A gap that BE-0409's port surfaces then needs no independent rediscovery on both platforms at once.

The resident server already has what an executor needs: a live `UiAutomation` session and an accessibility-event listener. The existing `nativeZ` node walk already exercises both. Four changes turn the server into a step executor:

1. **Replace the `dumpWindowHierarchy`-XML tree read with a direct `AccessibilityNodeInfo` traversal.** The server already performs the same kind of traversal for `nativeZ`. This change generalizes it into the primary read path, not a secondary one, and removes the XML serialize/parse round trip on both ends.
2. **Evaluate `wait` and `settled` from `TYPE_WINDOW_CONTENT_CHANGED` and `WINDOWS_CHANGED` events**, replacing the current two-dump-match approach. BE-0407 proposes the same event-driven judgment as a point fix for the `POSTDATE_BUDGET_MS` wait. This item generalizes the judgment into the executor's default way of deciding a screen has settled. The judgment no longer sits as a narrow fix on the existing dump-based path.
3. **Inject taps and pans with `UiAutomation.injectInputEvent`**, confirming publication by the event type the injection produces. A fixed wait or a follow-up read no longer confirms it.
4. **Capture screenshots with `UiAutomation.takeScreenshot()`**, returned alongside the step's result. A separate `adb exec-out screencap` subprocess call no longer captures them.

Selector resolution ports the host's `resolve_unique` logic ([`bajutsu/common/drivers/base.py`](../../bajutsu/common/drivers/base.py)) to Kotlin. The port includes the Android-specific derived-label fallback ([`drivers/adb.py:251-282`](../../bajutsu/common/drivers/adb.py)), per BE-0408's shared selector-semantics contract. The driver conformance suite ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)) verifies the port, the same way it verifies the iOS executor's port.

## Alternatives considered

- **Fold this item into the `POSTDATE_BUDGET_MS` point fix that BE-0407 proposes, rather than building a full event-driven executor.** Rejected. The point fix targets one wait inside today's request/response `POST /act` path. It needs confirmation from the server's own logs before it can even land. This item is the larger redesign. It generalizes the same underlying idea, reading the event stream instead of polling or budgeting, into the server's whole condition-evaluation path. The two items relate but are not substitutes. The point fix can land first and independently, and this item does not depend on it landing first.
- **Keep the two-dump settle comparison and remove the `POSTDATE_BUDGET_MS` wait alone.** Rejected as the sole fix. Even with that wait removed, each settle check still pays for a full tree read twice. The event stream that this item uses avoids that cost by answering directly.
- **Route Android's executor design through XML instead of `AccessibilityNodeInfo`, to keep the parsing code shared with today's host-side parser.** Rejected. The on-device traversal already exists for `nativeZ`. Reusing the traversal removes a serialize/parse round trip, which keeping XML would preserve for no benefit. The host-side parser stays as is for the `uiautomator dump` fallback path, which this item does not touch.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

**Sequence status: blocked on BE-0409's completion** (see *Implementation order* in
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
- [x] Once the `roadmap-id` workflow allocates the four ids on `main`, backfill a reciprocal
  `Related` link with BE-0407, BE-0408, and BE-0409 (see the same box on BE-0407).

## References

[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[BE-0210 — Android on-device actuation fidelity](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md),
[BE-0234 — Speed up adb scenario runs](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md),
[`bajutsu/common/drivers/base.py`](../../bajutsu/common/drivers/base.py),
[`bajutsu/common/drivers/adb.py`](../../bajutsu/common/drivers/adb.py),
[`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt)
