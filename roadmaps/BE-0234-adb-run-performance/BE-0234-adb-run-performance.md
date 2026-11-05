**English** · [日本語](BE-0234-adb-run-performance-ja.md)

# BE-0234 — Speed up adb scenario runs (uiautomator dump bottleneck)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0234](BE-0234-adb-run-performance.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0234") |
| Implementing PR | [#987](https://github.com/bajutsu-e2e/bajutsu/pull/987), [#1944](https://github.com/bajutsu-e2e/bajutsu/pull/1944) (closes the last unit by decision) |
| Topic | Platform support |
| Related | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md), [BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md) |
<!-- /BE-METADATA -->

## Introduction

A scenario on the Android adb backend ([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md))
runs markedly slower than the same scenario on the iOS idb backend. Authoring and iterating against
Android feels sluggish as a result.

This item pins the cause down to a single dominant cost and proposes a phased fix. Two low-risk
changes reduce how often the runner reads the screen. The real remedy is a resident UI Automator
server. It removes the per-read startup cost that makes an Android read an order of magnitude slower
than an iOS read.

Nothing here touches the determinism contract. Reads stay condition-driven, and ambiguous selectors
still fail fast. No LLM enters the `run` path.

## Motivation

The slowness is not in *acting* on the device. It is in *reading* the device. Every screen read on
the adb backend (`AdbDriver.query()`, [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py))
shells out to `adb exec-out uiautomator dump /dev/tty` ([`dump_cmd`](../../bajutsu/adb.py)). That
command carries a large **fixed per-invocation cost**.

The measurements come from a local arm64 emulator (API 34) on the launcher screen, a small tree of
about 12 KB.

| Operation | Wall-clock |
|---|---|
| `adb exec-out uiautomator dump /dev/tty` (the `query()` path) | **≈ 2.3–2.5 s** |
| `uiautomator dump --compressed` | ≈ 2.0–2.4 s (no improvement) |
| dump to a device file, then `cat` it | ≈ 2.4–2.5 s (no improvement) |
| `adb shell input tap` (the tap itself) | ≈ 0.07 s |
| bare adb round-trip (`getprop`) | ≈ 0.04 s |
| iOS `idb describe-all` (reference, repo's own figure, [`waits.py`](../../bajutsu/orchestrator/waits.py)) | ≈ 0.1–0.3 s |

Two facts follow from these numbers.

- **The cost does not depend on tree size, compression, or output sink.** It is therefore not XML
  transfer or tree traversal. It is the per-invocation overhead of `uiautomator dump`. Each call
  spins up a fresh instrumentation process, connects a `UiAutomation` session, waits for idle, dumps
  the tree, and tears the session down.
- **Actuation is already fast.** A tap takes 0.07 s, so the whole iOS-vs-Android gap lives in the
  read. An adb read takes ≈ 2.4 s against ≈ 0.1–0.3 s for an idb read, a 10–20× gap per read.

The number of reads in a step amplifies that per-read cost. The runner
([`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py)) reads as follows.

- `after = active_driver.query()` runs **unconditionally at the end of every step**, whether or not
  the result is used. The result feeds `screenChanged`, `extract`, and element-bearing captures, and
  a plain `tap` step has none of them.
- `before = active_driver.query()` runs on every step when the scenario's `capturePolicy` contains
  any `screenChanged` rule.
- The action itself reads at least once inside the driver (`_settle()` → `_resolve()`). It reads
  more while a transition is mid-flight.

A realistic per-step read count, as a lower bound, looks like this.

| Step | Reads (floor) | Wall-clock at ≈ 2.4 s/read |
|---|---|---|
| `tap` (no `screenChanged` policy) | settle 1 + after 1 = **2** | ≈ 4.8 s |
| `tap` (with `screenChanged` policy) | before 1 + settle 1 + after 1 = **3** | ≈ 7.2 s |
| `assert` | body 1 + after 1 = **2** | ≈ 4.8 s |

The `settle 1` in that table is a *best case*. `_settle()` reads once and returns immediately when
the identifier/frame key matches the previous read. A `tap` commonly *does* change the screen,
though. On a changed key, `_settle()` polls up to `_SETTLE_MAX_POLLS` (= 3) further reads while it
waits for the tree to stop moving. A screen-changing `tap` can therefore cost up to **4 reads
(~9.6 s)** in `settle` alone. The counts in the table are the floor, and a transition-heavy step
costs more.

A 10-step scenario spends **on the order of ~48 s in dumps at the floor**, and more with
transitions. This cost makes Android authoring feel heavy, and it is entirely read-bound.

The gap is a backend-internal performance gap, so it fits squarely inside prime directive 3
(app-agnostic). The fix lives in the adb Driver and the shared runner. It changes no scenario and
adds no per-app special-casing. It must also preserve the determinism contract. Reads that gate on a
condition stay condition waits, never a fixed `sleep`, and ambiguous matches keep failing fast.

## Detailed design

**Scope as shipped:** units 1–3 and 5 shipped in the implementing PR below. They cover the
read-count reductions, the adb `_settle` tuning, the measurement, and the regression guard.

**Unit 4 (the resident UI Automator server) was carved out to a follow-up item.** It needs a device
to verify and a packaged instrumentation, so it does not fit this item's fast-gate change. The split
is the path the *Alternatives considered* section below pre-authorized ("narrowed to units 1–3").
The description of unit 4 stays here for continuity. The follow-up item is where it will be built.

The work is phased. The cheap, self-contained wins land first and de-risk the measurement. The
architectural change lands last, behind a fallback.

The work touches the adb Driver ([`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)) and the
shared runner ([`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py)). For unit 3 it
also adds a resident-server dependency. It changes no shared scenario and adds no LLM to any path.

Each unit is proven against the driver conformance suite
([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)) and the Android
e2e lane ([BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)). Before
and after per-step timings are recorded on device.

### Work breakdown (MECE)

1. **Establish the baseline and a per-step read counter.** Before changing anything, record the
   on-device wall-clock of a representative shared scenario. Instrument the read count per step with
   a debug log or a run-summary line. Every later unit's win is then measured, not asserted. The
   baseline is the yardstick that units 2–4 are held against. It answers the "how slow, exactly"
   question that the Motivation opened with.

2. **Stop reading the screen when the result is unused (runner, low risk).** Make the
   `after = active_driver.query()` at the end of each step
   ([`loop.py`](../../bajutsu/orchestrator/loop.py)) **lazy/conditional**. Compute it only when
   something consumes it: the scenario `wants_screen_changed`, the step has an `extract`, or a fired
   capture needs `elements`. A plain `tap` or `assert` step with none of these skips the read
   entirely, which removes ~2.4 s per step. This is a pure removal of a redundant read. It changes no
   condition wait, so determinism is untouched.

   The `before` read gets a *stronger* treatment. A `screenChanged` capture rule is not step-scoped.
   `_rule_fires` in [`evidence_rules.py`](../../bajutsu/orchestrator/evidence_rules.py) keys only off
   the `screen_changed` boolean, not the step's kind or id. Once a scenario `wants_screen_changed`,
   every step's `before` is genuinely needed, and no per-step condition can gate it.

   Nothing actuates the device between one step's end-of-step `after`
   ([`loop.py`](../../bajutsu/orchestrator/loop.py):449) and the next step's `before`
   ([`loop.py`](../../bajutsu/orchestrator/loop.py):417). Both observe *identical* device state. The
   change therefore does not gate `before`. It **reuses the previous step's `after` as this step's
   `before`**. The `before` read drops to (near) zero across the whole scenario, instead of being
   conditioned per step. Only the first step, which has no prior `after`, still reads once.

   Both changes are backend-independent and speed up idb too. The gain is proportionally smaller
   there, since an idb read is already cheap.

3. **Tune `_settle` for a slow read (adb, low risk).** The settle constants in the adb driver
   (`_SETTLE_POLL_S = 0.05`, `_SETTLE_MAX_POLLS = 3`, [`adb.py`](../../bajutsu/drivers/adb.py)) were
   inherited from idb. On idb a read is cheap, and the poll interval dominates. On adb the read
   itself takes ~2.4 s, so the poll interval is irrelevant. The real lever is the *number* of settle
   reads.

   Re-tune the settle bound for a slow read, and document it. A stable screen then settles in one
   read, and only a genuinely animating screen pays for extra reads. The "wait until the tree stops
   moving" semantics stay intact: it is still a condition wait, with no fixed sleep.

   Fold in the other stray reads too. These are the `screen_size_from_elements(driver.query())`
   calls in [`gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py),
   [`alerts.py`](../../bajutsu/alerts.py), and [`crawl.py`](../../bajutsu/crawl.py). Reuse the screen
   extent from an already-read tree wherever one is available.

4. **Replace per-dump startup with a resident UI Automator server (adb, the real fix).** Units 2–3
   cannot cross a floor: the ~2.4 s *per-invocation* cost of `uiautomator dump`. This unit removes
   the floor. It keeps a **resident UI Automator instrumentation** alive for the run and queries the
   hierarchy over a local socket or HTTP. A read then no longer pays a fresh instrumentation startup.
   Appium's UiAutomator2 driver uses the same approach. The expected result is a read that drops from
   ~2.4 s to ~0.1–0.3 s, which closes the 10–20× gap with iOS.

   The resident server is a **uniform, app-independent** component, because it drives whatever app is
   under test. It is app-agnostic in the same way as the BajutsuKit and BajutsuAndroid on-device SDKs
   ([BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md)), and it is not a
   per-app carve-out.

   This unit swaps the internals of `AdbDriver._describe()` alone. The parsing
   (`parse_hierarchy`), the normalization, the transient-empty retry, and the whole selector/resolve
   contract stay unchanged. The existing `uiautomator dump` path remains as a fallback when the
   server is unavailable, so no device is left worse off.

5. **Guard the win against regression.** Record the before/after per-step timing in the item's
   Progress log. Extend the driver conformance suite and the e2e lane, so that a future change that
   reintroduces a redundant read or a slow path is caught.

   A hard CI *timing* gate is out of scope. Wall-clock is environment-dependent, and such a gate
   would be flaky. The guard is a documented measurement, plus a read-count assertion where one is
   stable.

## Alternatives considered

- **`uiautomator dump --compressed`.** Rejected as the primary fix. Measured at ≈ 2.0–2.4 s, it does
  not move the needle, because the cost is startup, not tree size. It also drops nodes that the
  selector layer may need. It could ride along as a minor tweak, but it does not address the
  bottleneck.

- **Dump to a device file and pull/`cat` it instead of `/dev/tty`.** Rejected. Measured at
  ≈ 2.4–2.5 s, it is identical, because the sink is not the cost.

- **Poll less, or add fixed sleeps to mask the latency.** Rejected outright. A fixed `sleep` violates
  prime directive 2. Reading *less often* than a condition requires would make the run
  non-deterministic. The fix is to make each necessary read cheap and to drop only the *unnecessary*
  reads. It never weakens a condition wait.

- **Split unit 4 (resident server) into its own BE item.** Reasonable. If the resident-server design
  grows (packaging the instrumentation APK, its lifecycle, its dependency surface), it should be
  promoted to a standalone item, and this item narrowed to units 1–3. The units stay together for
  now. All units share one motivation (the read-bound Android run) and one yardstick (the per-step
  wall-clock from unit 1). Phasing them in one item keeps the measurement honest end to end.

- **Accept the gap as inherent to Android.** Rejected. The 10–20× figure is a per-*invocation*
  startup cost. It is not an inherent property of reading an Android hierarchy. A resident session
  pays that startup once, which is exactly what unit 4 does.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add a per-step read counter — the run loop logs the runner-issued screen reads per step and a
  scenario total (the yardstick for the reductions below); the on-device wall-clock baseline is
  recorded when the change is exercised on a device.
- [x] Make the end-of-step `after` read lazy (taken only when a consumer needs the tree — a
  `screenChanged` capture, an `extract`, or a wait-timeout diagnostic) and reuse the previous step's
  `after` as the next step's `before` (identical device state) instead of re-reading.
- [x] Re-tune `_settle` for a slow read: on adb the ~2.4s read paces the settle loop, so the inter-poll
  interval is 0 (the poll count is kept at 3 to preserve the settle condition). The other stray
  one-off reads (`screen_size_from_elements(driver.query())` in the shared gesture / alert / crawl
  handlers) each read a tree that is *not* already in hand at the call site, so folding them needs a
  driver-level last-read cache — deferred with the resident-server work below.
- [x] ~~Replace per-dump startup with a resident UI Automator server, `uiautomator dump` kept as
  fallback.~~
  - Carved out to a follow-up item: needs a device to verify and a packaged instrumentation, so it
    does not fit this item's fast-gate change. Landed as
    [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md).
- [x] Guard the win with a read-count assertion on the fast gate (`tests/orchestrator/test_read_count.py`
  for the runner reductions, `tests/test_adb.py` for the adb `_settle` reads); a wall-clock timing
  gate stays out of scope (environment-dependent, would be flaky).

## References

[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
[BE-0210 — Android on-device actuation fidelity](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md),
[BE-0223 — Reach every Android tab by driving the tab bar over adb](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation.md),
[BE-0233 — adb clipboard on-device fidelity](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md),
[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[BE-0208 — Android on-device e2e in CI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md),
[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py),
[`bajutsu/adb.py`](../../bajutsu/adb.py),
[`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py),
[`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py)
