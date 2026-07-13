**English** · [日本語](BE-0245-adb-resident-uiautomator-server-ja.md)

# BE-0245 — Resident UI Automator server for adb reads

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0245](BE-0245-adb-resident-uiautomator-server.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0245") |
| Implementing PR | [#1011](https://github.com/bajutsu-e2e/bajutsu/pull/1011) |
| Topic | Platform support |
| Related | [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) |
<!-- /BE-METADATA -->

## Introduction

Reading the screen on the Android adb backend is an order of magnitude slower than on iOS, and the
whole gap is a per-invocation startup cost: every `AdbDriver.query()` shells out to
`adb exec-out uiautomator dump`, which spins up a fresh instrumentation, connects a `UiAutomation`
session, waits for idle, dumps, and tears the session down — ≈ 2.4 s each time, against ≈ 0.1–0.3 s
for the same read on idb. This item removes that startup cost by keeping a **resident UI Automator
instrumentation** alive for the duration of a run and querying the hierarchy over a local
socket / HTTP, the approach Appium's UiAutomator2 driver takes. It is the fourth (and final,
architectural) unit carved out of [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md),
kept separate because it needs a device to verify and a packaged instrumentation, so it does not fit
that item's fast-gate change. Nothing here touches the determinism contract: reads stay
condition-driven, ambiguous selectors still fail fast, and no LLM enters the `run` path.

## Motivation

[BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md) pinned the Android run's
slowness to the screen read and shipped the two low-risk reductions (making the runner's end-of-step
read lazy and reusing the previous step's tree as the next step's `before`) plus the adb `_settle`
retune. Those cut how *often* the runner reads, but they cannot lower the floor: the ≈ 2.4 s
*per-invocation* cost of `uiautomator dump` itself. BE-0234's own measurements show this cost is
independent of tree size, compression, and output sink — it is the instrumentation startup, not the
XML transfer or traversal — so no amount of reading-less crosses it. A read-bound backend where each
necessary read still costs ≈ 2.4 s keeps Android authoring heavy even after BE-0234's reductions.

A resident session pays that startup **once** for the whole run, then answers each subsequent read
over an already-open channel. That is exactly what closes the 10–20× gap with iOS: the expected
result is a read that drops from ≈ 2.4 s to ≈ 0.1–0.3 s. The cost is real (a packaged instrumentation
and its lifecycle), which is why it is its own item rather than a knob — but the payoff is the one
change that makes the Android read as cheap as the iOS one.

The resident server is a **uniform, app-independent** component: it drives whatever app is under
test, exactly as the on-device SDKs
([BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md), BajutsuKit /
BajutsuAndroid) do. A uniform library that every target uses identically is not a per-app carve-out,
so this fits prime directive 3 (app-agnostic) the same way those SDKs do: the tool, drivers, and
runner stay unchanged across targets, and no scenario changes.

## Detailed design

The change is confined to how the adb driver *obtains* a hierarchy dump. It swaps the internals of
`AdbDriver._describe()` ([`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)) only — the
parsing (`parse_hierarchy`), the normalization to Elements, the transient-empty retry, `_settle`,
and the whole selector / resolve contract stay exactly as they are, so every existing adb test and
the driver conformance suite ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md))
keep passing unchanged. The existing `uiautomator dump` path is retained as a fallback, so a device
where the resident server cannot start is never left worse off than today.

### Work breakdown (MECE)

1. **Package and launch the resident instrumentation.** Bundle a UI Automator instrumentation
   (an `androidx.test` UiAutomator server, in the shape Appium's UiAutomator2 server uses) and
   install / start it on the target device at the start of a run. This is the app-independent
   component; decide its distribution (a checked-in prebuilt APK vs. built on demand) and where it
   lives in the repo, mirroring how the on-device SDKs are packaged
   ([BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md)).

2. **A hierarchy-query channel.** Expose the accessibility hierarchy over a local socket / HTTP
   (`adb forward` to the resident server) and define the request/response for "dump the current
   hierarchy". The response must carry the same information the current `uiautomator dump` XML does,
   so `parse_hierarchy` (or a thin adapter to it) still produces identical Elements. No new selector
   semantics — this unit changes transport, not meaning.

3. **Swap `AdbDriver._describe()` to the resident channel, with the dump path as fallback.** Route
   `_describe()` through the resident server when it is available; on any startup / channel failure,
   fall back to `adb exec-out uiautomator dump` (today's path) so the backend degrades to the
   current behavior rather than failing. Everything above `_describe()` — `query()`'s
   transient-empty retry, `_settle`, `_resolve`, actuation — is untouched.

4. **Server lifecycle tied to the run.** Start the resident server once per device lease and stop it
   when the run ends (including on failure / interrupt), so no instrumentation is left running on the
   device. Fold this into the existing device-pool / lease lifecycle rather than a bespoke path.

5. **Verify on device and guard against regression.** Record the before/after per-step read
   wall-clock on a real emulator (the BE-0234 read-count yardstick is already in place), confirm the
   read drops to ≈ 0.1–0.3 s, and extend the Android e2e lane
   ([BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)) so the resident
   path and the dump fallback are both exercised. A hard CI *timing* gate stays out of scope
   (wall-clock is environment-dependent and would be flaky), consistent with BE-0234.

## Alternatives considered

- **Keep per-invocation `uiautomator dump` and only read less (BE-0234's units 1-3).** Already
  shipped, and worthwhile, but it cannot cross the ≈ 2.4 s per-read floor — that floor is precisely
  what this item removes. The two are complementary: fewer reads *and* a cheap read.

- **`uiautomator dump --compressed` / dump to a device file then `cat`.** Both measured by BE-0234 at
  ≈ 2.0–2.5 s — no improvement, because the cost is the instrumentation startup, not the XML size or
  the output sink. Rejected there; they do not address the bottleneck here either.

- **Adopt Appium's UiAutomator2 server wholesale as a dependency.** Tempting, but it brings a large
  surface (a full Appium server, its command set, its versioning) for what bajutsu needs, which is
  just a resident hierarchy dump. The design leans on Appium's *approach* (a resident instrumentation
  queried over a socket) without taking on the whole server; the exact reuse-vs-minimal boundary is
  an open question for the implementation.

- **A persistent `uiautomator` shell process reused across reads.** Does not help: each
  `uiautomator dump` invocation starts its own instrumentation regardless of how the shell is kept
  open, so the startup cost is paid per dump, not per shell.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Package and launch the resident UI Automator instrumentation (app-independent component; decide distribution).
- [ ] Define the hierarchy-query channel (local socket / HTTP) whose response `parse_hierarchy` consumes unchanged.
- [ ] Swap `AdbDriver._describe()` to the resident channel, keeping `uiautomator dump` as the fallback.
- [ ] Tie the resident server's lifecycle to the device lease (started once, stopped on run end / failure).
- [ ] Verify on device (read drops to ≈ 0.1–0.3 s) and guard the resident + fallback paths in the Android e2e lane.

Log:

- PR-A ([#1011](https://github.com/bajutsu-e2e/bajutsu/pull/1011)) — Python-only, device-free scaffold for the resident-channel swap (unit 3). Adds an
  injectable `fetch_hierarchy` seam to `AdbDriver`: `_describe()` reads via `_read_source()`, which uses
  the resident fetch when configured and degrades to `uiautomator dump` with a loud warning on
  `AdbResidentError`. `parse_hierarchy` and everything above `_describe()` (transient-empty retry,
  `_settle`, `_resolve`, actuation) are unchanged, and the default (no fetch) keeps today's
  dump-every-read behavior exactly — so no box is ticked yet. The `adb forward` builder and the real
  HTTP transport factory are deferred to PR-C, where they stop being dead code.

## References

[BE-0234 — Speed up adb scenario runs (uiautomator dump bottleneck)](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md),
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
[BE-0233 — adb clipboard on-device fidelity](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md),
[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[BE-0208 — Android on-device e2e in CI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md),
[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py),
[`bajutsu/adb.py`](../../bajutsu/adb.py)
