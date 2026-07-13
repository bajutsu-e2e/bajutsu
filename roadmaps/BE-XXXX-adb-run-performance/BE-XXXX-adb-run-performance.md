**English** · [日本語](BE-XXXX-adb-run-performance-ja.md)

# BE-XXXX — Speed up adb scenario runs (uiautomator dump bottleneck)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-adb-run-performance.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform expansion (Android / Web / Flutter) |
| Related | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md), [BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md) |
<!-- /BE-METADATA -->

## Introduction

Running a scenario on the Android adb backend ([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md))
is markedly slower than the same scenario on the iOS idb backend — enough that authoring and
iterating against Android feels sluggish. This item pins the cause down to a single dominant cost
and proposes a phased fix: two low-risk reductions in how often the runner reads the screen, and the
real remedy — a resident UI Automator server that removes the per-read startup cost that makes the
Android read an order of magnitude slower than the iOS one. Nothing here touches the determinism
contract: reads stay condition-driven, ambiguous selectors still fail fast, and no LLM enters the
`run` path.

## Motivation

The slowness is not in *acting* on the device — it is in *reading* it. Every screen read on the adb
backend (`AdbDriver.query()`, [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)) shells out to
`adb exec-out uiautomator dump /dev/tty` ([`dump_cmd`](../../bajutsu/adb.py)), and that command
carries a large **fixed per-invocation cost**.

Measured on a local arm64 emulator (API 34), on the launcher screen (a small ~12 KB tree):

| Operation | Wall-clock |
|---|---|
| `adb exec-out uiautomator dump /dev/tty` (the `query()` path) | **≈ 2.3–2.5 s** |
| `uiautomator dump --compressed` | ≈ 2.0–2.4 s (no improvement) |
| dump to a device file, then `cat` it | ≈ 2.4–2.5 s (no improvement) |
| `adb shell input tap` (the tap itself) | ≈ 0.07 s |
| bare adb round-trip (`getprop`) | ≈ 0.04 s |
| iOS `idb describe-all` (reference, repo's own figure, [`waits.py`](../../bajutsu/orchestrator/waits.py)) | ≈ 0.1–0.3 s |

Two facts fall out of this. First, the cost is **independent of tree size, compression, and output
sink** — so it is not XML transfer or tree traversal, it is the per-invocation overhead of
`uiautomator dump`: each call spins up a fresh instrumentation process, connects a `UiAutomation`
session, waits for idle, dumps, and tears the session down. Second, actuation is already fast
(a tap is 0.07 s), so the entire iOS-vs-Android gap lives in the read: **≈ 2.4 s per adb read
against ≈ 0.1–0.3 s per idb read, a 10–20× per-read gap.**

That per-read cost is then **amplified by how many reads a step performs**. In the runner
([`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py)):

- `after = active_driver.query()` runs **unconditionally at the end of every step**, whether or not
  its result is used (it feeds `screenChanged`, `extract`, and element-bearing captures — none of
  which a plain `tap` step has).
- `before = active_driver.query()` runs on every step when the scenario's `capturePolicy` contains
  any `screenChanged` rule.
- The action itself reads at least once inside the driver (`_settle()` → `_resolve()`), more while a
  transition is mid-flight.

So a realistic per-step read count — **as a lower bound** — is:

| Step | Reads (floor) | Wall-clock at ≈ 2.4 s/read |
|---|---|---|
| `tap` (no `screenChanged` policy) | settle 1 + after 1 = **2** | ≈ 4.8 s |
| `tap` (with `screenChanged` policy) | before 1 + settle 1 + after 1 = **3** | ≈ 7.2 s |
| `assert` | body 1 + after 1 = **2** | ≈ 4.8 s |

The `settle 1` in that table is a *best case*. `_settle()` reads once and returns immediately when
the identifier/frame key is unchanged from the previous read, but a `tap` commonly *does* change the
screen — and on a changed key `_settle()` polls up to `_SETTLE_MAX_POLLS` (= 3) further reads waiting
for the tree to stop moving. So a screen-changing `tap` can cost up to **4 reads (~9.6 s)** in
`settle` alone; the table's counts are the floor, and a transition-heavy step costs more.

A 10-step scenario therefore spends **on the order of ~48 s in dumps at the floor, more with
transitions**. This is what makes Android authoring feel heavy, and it is entirely read-bound.

This is a backend-internal performance gap, so it fits squarely inside prime directive 3
(app-agnostic): the fix lives in the adb Driver and the shared runner, changes no scenario, and adds
no per-app special-casing. It must preserve the determinism contract — reads that gate on a
condition stay condition waits (never a fixed `sleep`), and ambiguous matches keep failing fast.

## Detailed design

The work is phased so the cheap, self-contained wins land first and de-risk the measurement, and the
architectural change lands last behind a fallback. It touches the adb Driver
([`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)), the shared runner
([`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py)), and — for unit 3 — adds a
resident-server dependency; it changes no shared scenario and adds no LLM to any path. Each unit is
proven against the driver conformance suite ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md))
and the Android e2e lane ([BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)),
with before/after per-step timings recorded on device.

### Work breakdown (MECE)

1. **Establish the baseline and a per-step read counter.** Before changing anything, record the
   on-device wall-clock of a representative shared scenario and instrument the read count per step
   (a debug log or a run-summary line), so every later unit's win is measured, not asserted. This is
   the yardstick units 2–4 are held against, and it fixes the "how slow, exactly" question the
   Motivation opened with.

2. **Stop reading the screen when the result is unused (runner, low risk).** Make the
   `after = active_driver.query()` at the end of each step
   ([`loop.py`](../../bajutsu/orchestrator/loop.py)) **lazy/conditional**: compute it only when it is
   actually consumed — the scenario `wants_screen_changed`, the step has an `extract`, or a fired
   capture needs `elements`. For a plain `tap`/`assert` step with none of these, skip the read
   entirely, removing ~2.4 s per step. This is a pure removal of a redundant read, not a change to
   any condition wait, so determinism is untouched.

   The `before` read is different and gets a *stronger* treatment. A `screenChanged` capture rule is
   not step-scoped (`_rule_fires` in
   [`evidence_rules.py`](../../bajutsu/orchestrator/evidence_rules.py) keys only off the
   `screen_changed` boolean, not the step's kind/id), so once a scenario `wants_screen_changed` every
   step's `before` is genuinely needed — there is no per-step condition to gate it on. But nothing
   actuates the device between one step's end-of-step `after`
   ([`loop.py`](../../bajutsu/orchestrator/loop.py):449) and the next step's `before`
   ([`loop.py`](../../bajutsu/orchestrator/loop.py):417): they observe *identical* device state. So
   rather than gate `before`, **reuse the previous step's `after` as this step's `before`**, dropping
   the `before` read to (near) zero across the whole scenario instead of only conditioning it per
   step — the first step (no prior `after`) still reads once. Both changes are backend-independent
   and speed up idb too (proportionally smaller, since its read is already cheap).

3. **Tune `_settle` for a slow read (adb, low risk).** The settle constants in the adb driver
   (`_SETTLE_POLL_S = 0.05`, `_SETTLE_MAX_POLLS = 3`, [`adb.py`](../../bajutsu/drivers/adb.py)) were
   inherited from idb, where a read is cheap and the poll interval dominates. On adb the read itself
   is ~2.4 s, so the poll interval is irrelevant and the real lever is the *number* of settle reads.
   Re-tune (and document) the settle bound for a slow read so a stable screen settles in one read and
   only a genuinely-animating one pays for extra reads — keeping the "wait until the tree stops
   moving" semantics intact (still a condition wait, no fixed sleep). Fold in the other stray reads
   too (`screen_size_from_elements(driver.query())` in
   [`gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py) /
   [`alerts.py`](../../bajutsu/alerts.py) /
   [`crawl.py`](../../bajutsu/crawl.py)) where a screen extent can reuse an already-read tree.

4. **Replace per-dump startup with a resident UI Automator server (adb, the real fix).** The
   floor units 2–3 cannot cross is the ~2.4 s *per-invocation* cost of `uiautomator dump`. Remove it
   by keeping a **resident UI Automator instrumentation** alive for the run and querying the
   hierarchy over a local socket / HTTP, instead of paying a fresh instrumentation startup on every
   read — the approach Appium's UiAutomator2 driver uses. The expected result is a read that drops
   from ~2.4 s to ~0.1–0.3 s, closing the 10–20× gap with iOS. The resident server is a **uniform,
   app-independent** component (it drives whatever app is under test), so it is app-agnostic in the
   same way the BajutsuKit / BajutsuAndroid on-device SDKs are
   ([BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md)) — not a per-app
   carve-out. This unit swaps the internals of `AdbDriver._describe()` only; the parsing
   (`parse_hierarchy`), the normalization, the transient-empty retry, and the whole selector/resolve
   contract stay unchanged, and the existing `uiautomator dump` path remains as a fallback when the
   server is unavailable, so no device is left worse off.

5. **Guard the win against regression.** Record the before/after per-step timing in the item's
   Progress log, and extend the driver conformance suite / e2e lane so a future change that
   reintroduces a redundant read or a slow path is caught. (A hard CI *timing* gate is out of scope —
   wall-clock is environment-dependent and would be flaky; this is a documented measurement plus a
   read-count assertion where one is stable.)

## Alternatives considered

- **`uiautomator dump --compressed`.** Rejected as the primary fix: measured at ≈ 2.0–2.4 s, it does
  not move the needle (the cost is startup, not tree size), and it drops nodes the selector layer may
  need. It could ride along as a minor tweak but does not address the bottleneck.

- **Dump to a device file and pull/`cat` it instead of `/dev/tty`.** Rejected: measured at
  ≈ 2.4–2.5 s, identical — the sink is not the cost.

- **Poll less / add fixed sleeps to mask the latency.** Rejected outright: a fixed `sleep` violates
  prime directive 2, and reading *less often* than a condition requires would make the run
  non-deterministic. The fix is to make each necessary read cheap and to drop only the *unnecessary*
  reads, never to weaken a condition wait.

- **Split unit 4 (resident server) into its own BE item.** Reasonable, and if the resident-server
  design grows (packaging the instrumentation APK, its lifecycle, its dependency surface) it should
  be promoted to a standalone item and this one narrowed to units 1–3. Kept together for now because
  all units share one motivation (the read-bound Android run) and one yardstick (per-step wall-clock
  from unit 1), and phasing them in one item keeps the measurement honest end to end.

- **Accept the gap as inherent to Android.** Rejected: the 10–20× figure is a per-*invocation*
  startup cost, not an inherent property of reading an Android hierarchy — a resident session pays
  that startup once, which is exactly what unit 4 does.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Establish the on-device baseline and a per-step read counter (the yardstick for units 2–4).
- [ ] Make the end-of-step `after` read lazy/conditional, and reuse it as the next step's `before` (identical device state) instead of re-reading.
- [ ] Re-tune `_settle` for a slow read and fold in stray one-off reads.
- [ ] Replace per-dump startup with a resident UI Automator server, `uiautomator dump` kept as fallback.
- [ ] Record before/after timings and guard the win (conformance / e2e, no flaky timing gate).

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
