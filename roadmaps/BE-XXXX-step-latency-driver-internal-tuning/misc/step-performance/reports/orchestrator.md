# Orchestrator-side step-execution performance report

Scope: one scenario step from `_StepRunner._run_one` through `_handle_action` (`bajutsu/common/orchestrator/loop.py:1249`), plus per-scenario fixed costs. All driver calls named are calls the *orchestrator* issues; driver-internal round trips are noted where they matter for counting.

## 0. The common per-step skeleton (`_handle_action`, loop.py:1249-1730)

Every leaf step (tap/type/swipe/wait/assert/relaunch/…) runs this sequence in order:

1. **Pre-step baseline capture** (loop.py:1274-1329): `sink.capture(driver, step_id, ["screenshot.before","elements.before"], elements=prev_after)`. Under `FileSink` this is **one `driver.screenshot()`** (`before.png`, core.py:174-186) plus `write_elements` (core.py:156-171). `elements=None` — the first step of every scenario, and any step after one whose read was skipped — makes `write_elements` call **`driver.query()` inside the sink** (core.py:170); that read is invisible to `total_reads`/`prev_after` (the loop only avoids it for `web` blocks, loop.py:1283-1289). Under `NullSink` the call is a no-op.
2. Interpolation + `_resolve_system_alert` (loop.py:1335-1337; CPU only).
3. **`screenChanged` pre-read** (loop.py:1366-1376): only when any `capturePolicy` rule has `on.event == screenChanged` (`wants_screen_changed`, loop.py:645). Reuses `prev_after`; otherwise `active_driver.query()` (loop.py:1374).
4. **Interrupt guard** (`_InterruptGuard`, loop.py:860-952): built only if `interrupts` declared. For a non-wait step, `clear_before_act` costs **one extra `query()`** unless `before` was read fresh this iteration (loop.py:1404-1412); each fired recovery re-queries (loop.py:938).
5. **`_tip_poll_hook`** (loop.py:956-990): no query unless `iosTipKitHandling` and a tip is up.
6. **`_run_step_body`** (loop.py:368-473) — see per-kind below.
7. **End-of-step guards** (loop.py:1488-1566), only when `not ok`: `_dismiss_blocking_tip` (a driver call only when opted in), then `alert_guard(driver)` = `AlertGuardConfig.__call__` (types.py:511-531): `probe_native` → `system_alert_labels()` (types.py:437) + possibly `handle_system_alert` (types.py:450); on "absent", `dismiss_from_tree_once` → **`driver.query()`** (types.py:486) + possibly `tap` (types.py:503). Each guard buys one retry of the whole body.
8. `drain_actuations` (loop.py:1576; in-memory on every backend, types.py:569-577) and **`drain_interruptions`** (loop.py:1581) — on XCUITest this is a **real HTTP round trip** `POST /interruptionPolicy/drain` every step (xcuitest.py:1170-1176); no-op elsewhere.
9. **Mandatory `after.png`** (loop.py:1601): `sink.capture(driver, step_id, ["screenshot.after"])` → `driver.screenshot()` on every step under `FileSink`; no-op under `NullSink`.
10. `_ScreenRead` (loop.py:245-307, built at 1640). Consumers that force `.get()`: `screen_changed` compare (1641, only with a `screenChanged` policy), wait-timeout diagnostic (1650, only on a failed `for` wait), `extract` (1664), and **`writes_elements`** (1712-1719) — true for every step under any non-`NullSink`, because `_collect_captures` always leads with `elements` (evidence_rules.py:190-194). So under `FileSink` every mutating step pays one post-step `query()`; `assert`/`wait` seed it from their own last read (BE-0259) and pay nothing extra.
11. `_settle_extract_read` (loop.py:174-242): only with `extract`; zero-budget without `BAJUTSU_MIN_WAIT_TIMEOUT` (one read), else polls at `_adaptive_sleep`/`_POLL`=50 ms until two reads agree and `read_lag()` (adb: 4.0 s, adb.py:536) has elapsed since `actuated_at`.
12. **Post-step capture** (loop.py:1721): `write_elements` → `redact_elements` + `json.dumps(indent=2)` + credential-shape regex + temp-file write + `chmod` + rename (sink.py:72-74, 161-210); any extra `screenshot.<modifier>`/`rawTree` tokens from policy.
13. `prev_after = screen.cached` (1729) seeds the next step.

`_dispatch_after` (loop.py:526-590) runs only when the scenario/config declares `after:` rules; it re-enters `_run_steps` per rule, so each hook step pays the full skeleton above.

## 1. Per-kind driver-call sequences (inside `_run_step_body`)

**tap** (`_do_tap`, handlers/gestures.py:129-133): `driver.tap(sel)` once. On `ElementNotTappable`: `scroll_until_tappable` down (≤3 steps) then up (≤6 steps), each iteration `query()` + `is_tappable()` + `scroll()` (gestures.py:84-101, scroll.py). Driver-internal: XCUITest `tap` = `GET /elements` + `POST /tap` (xcuitest.py:896-900, 789-850), with `_STALE` backoff retries; each transport call opens a **new `HTTPConnection`** (xcuitest.py:591). adb `tap` = resident device-side act (`_device_act` → `_settle()` → ≥1 `query()`) or `_center` → `_settle` (`_await_catchup` up to `_READ_LAG_S`=4 s polling every `_SETTLE_POLL_S`=0.1 s, adb.py:941-965; stability poll up to `_SETTLE_DEADLINE_S`=8 s, adb.py:983-1047) + `input tap`.

**type (with `into`)** (gestures.py:161-167): `driver.tap(into)` (same as above, with recovery) then `driver.type_text(text)` — two actuation round trips minimum.

**swipe/scroll**: directional `swipe` (gestures.py:213-247) = `settled_query()` (adb) or `query()` + `driver.scroll(from,to)`. `scroll` action (scroll.py:150-265): initial `query()`, then per iteration `driver.scroll()` + `_region_after_step` = `query()` re-polled every `_CHANGE_POLL_S`=0.1 s for up to `read_lag()` (4 s on adb) while the region looks unmoved (scroll.py ~412-448); when the tree cannot judge, `_settled_render` = `time.sleep(0.1)` + `driver.screenshot()` to a temp file + SHA hash, up to `_RENDER_SETTLE_S`=1.0 s (scroll.py:307-378).

**wait for / until gone** (`_wait`, waits.py:737-922): loop of `query()` (832/860) → `_exists` → `gate.observe(elements)` → `on_interrupt_poll` → `cancelled()` → heartbeat → `_adaptive_sleep` (`_POLL`=0.05 s minus query time, waits.py:209-218). Returns the satisfied tree as the step's `after` seed. Timeout = `max(w.timeout, BAJUTSU_MIN_WAIT_TIMEOUT)` (189-190). `_AlertGuardGate._observe_native` (290-371) issues **`system_alert_labels()` once per `guard.poll_interval`** (default 1.0 s, types.py:239), then `_dismiss_from_tree` (≤`_TREE_DISMISS_MAX_TAPS`=3 taps, `_TREE_RETAP_DELAY`=1.0 s, `_decline_giveup`=max(2.0, 2·poll_interval)); the collapsed-tree proxy costs no query (`_GUARD_DEBOUNCE_POLLS`=3).

**wait until settled — tree-diff path** (`_wait_settled`, waits.py:925-1009): `query()` at entry (965), then per poll `transitions()` (976, in-memory list copy under a lock, network.py:319-322), `query()` (997), `gate.observe`, `on_interrupt_poll`, `_adaptive_sleep`. Settled = `_SETTLE_POLLS`=2 consecutive identical trees with an identifier. **Minimum 3 queries** (entry + 2 agreeing). Never fails; timeout returns pass.

**wait until settled — BE-0310 signal path** (`_wait_settled_by_signal`, waits.py:1012-1062): entered from any tree-diff poll once `transitions()[-1][1] >= start`. `query()` at entry (1042), then loop until `now - last >= _TRANSITION_QUIESCENCE`=0.3 s: `transitions()`, `query()` (1056), `_adaptive_sleep`. So it still issues ≈ 0.3 s / max(0.05, query_cost) queries after the last transition (≈6 on iOS at ~35 ms/query), whose results are only used for guards; the decision itself never reads the tree. Note the canonical tap→navigate→settled step first runs tree-diff polls until the `viewDidAppear` report arrives ("a few hundred ms", waits.py:940-946).

**assert** (loop.py:433-448): `_clipboard_for` (a `simctl pbpaste` subprocess only with a `clipboard` assertion) then `_poll_asserts` (115-154): `query()` + `assertions.evaluate` (evaluate.py:502-535, pure CPU); zero-budget = exactly one read; with the floor set, `_adaptive_sleep` polling until pass or only `_READ_ONCE_KINDS` still fail. Returns the tree as seed → no post-step read.

**handleSystemAlert** (`wait_for_system_alert`, waits.py:598-708): per poll `system_alert_labels()` at most every `_SYSTEM_ALERT_POLL`=0.2 s, `handle_system_alert(sel, _STEP_TAP_TIMEOUT=0)` when named, **plus a full `driver.query()` every tick just for the gate** (701) when a guard exists, `_adaptive_sleep`.

**relaunch** (handlers/device.py:13-21 → relaunchers.py:59-83 iOS; android.py:334-348): `simctl terminate` + `simctl launch` subprocesses (or `am force-stop`/`am start`), then **`await_ready`** (readiness.py:72-184): `deadline_ticks(10 s, 0.1→0.5 s backoff)` (base.py:738-765), each tick `transitions()` + `query()`, then `_await_settled` (187-231): up to `_SETTLE_POLLS`=3 more queries needing two identical `_tree_signature`s. Minimum 2 queries; first inter-tick sleep 0.1 s. The relaunch does **not** reset `prev_after`, so the next step's baseline reuses a pre-relaunch tree (loop.py:1274) — a correctness/evidence quirk rather than a cost.

### Round-trip counts, plain `tap` step, ok, no policies, no interrupts, no guard trip

| | FileSink (default in `run`, pool.py:441) | NullSink |
|---|---|---|
| `before.png` screenshot | 1 | 0 |
| hidden `query()` in `write_elements` when `prev_after` is None | 1 (first step / after a skipped read) | 0 |
| `driver.tap` | 1 orchestrator call (XCUITest: GET /elements + POST /tap = 2 HTTP; adb: settle query(s) + input) | same |
| `drain_interruptions` (XCUITest only) | 1 HTTP | 1 HTTP |
| `after.png` screenshot | 1 | 0 |
| post-step `query()` for `elements.json` | 1 | 0 |
| **Total orchestrator-issued driver calls** | **5 (6 on the first step)** — on XCUITest ≈ 7 HTTP round trips | **1 (+1 drain on XCUITest)** |

Nothing in the plain-tap path sleeps; every wait is condition-bounded.

## 2. Synchronous I/O and CPU on the critical path (before the next step starts)

- **Two PNG writes per step** (`before.png`, `after.png`): driver fetches bytes (XCUITest `GET /screenshot`, xcuitest.py:1259-1264; adb `screencap` subprocess with full stdout capture, backend_cli/adb.py:975-987; Playwright page screenshot) and `Path.write_bytes`, plus `restrict_file` chmod (sink.py:138).
- **`elements.json` twice per step** (baseline + post-step, same filename): `redact_elements` walks every element running `redact_text` (compiled regex list + `str.replace` per secret variant) on `value` and `label` (redaction.py:312-343), `json.dumps(indent=2, ensure_ascii=False)` (sink.py:174), `mask_credential_shapes` regex pass over the serialized body (sink.py:181), write to `.tmp`, chmod, `rename` (sink.py:196-210). `_resolve` calls `Path.resolve()` twice per write (sink.py:212-218).
- XCUITest `query()`: JSON decode of the whole `/elements` body + `_parse_elements` + `_apply_native_z` (xcuitest.py:731-742); `_raw_bytes` retained per query.
- `assertions.evaluate` / `find_all` / tree equality (`screen.get() != before`, loop.py:1641) are list-of-dict comparisons — cheap relative to a read.
- `_InterruptGuard.__post_init__` re-interpolates every interrupt condition per step (loop.py:888-891).
- Progress lines (`self.cfg.progress`, loop.py:1149, wait heartbeat every `_TICK_INTERVAL`=5 s) are stderr writes.
- Interval captures (video/deviceLog/appTrace) are **subprocesses started once per scenario**, not per step (`_SubprocessProc` `Popen`, intervals.py:160-183); per-step cost is zero, but the iOS `start_video(confirm_started=True)` polls file growth every 0.05 s up to `_VIDEO_START_TIMEOUT`=5 s before the first step (intervals.py:363-429), and `stop()` waits up to `_VIDEO_FINALIZE_TIMEOUT`=120 s (SIGINT + mux), then `_measured_start` parses the mp4 (media.py). Android video is prestarted at lease (android.py, `_prestart_video`) and its `stop()` polls `_await_screenrecord_stopped` every 0.2 s then `adb pull` (intervals.py:710-720). Text intervals are re-read and rescrubbed at finalize (`scrub_reserved`, sink.py:140-159).

## 3. Per-scenario fixed costs

- Lease (`pool.py:252-480`): actuator resolution, collector bridge, then `launch_driver` (launch.py:26-110) → `env.start` (simctl erase/boot/install/launch or `am start`; warm XCUITest runner resume with `_WARM_HEALTH_TIMEOUT`=10 s, environments/xcuitest.py:260,1519) → `await_ready` (readiness constants above; readiness timeout default 10 s, poll 0.1→0.5 s, settle ≤3 polls). `FileSink` construction builds a `Redactor` and computes `run_provenance` (dumps the scenario YAML) per lease (pool.py:441-465).
- `_run_on_lease` (pipeline.py:612-722): `push_interruption_policy` (an HTTP write on XCUITest, types.py:533-554), `_maybe_emit_score` (`driver.query()`, first scenario only, pipeline.py:216-245), a golden screen-bounds `query()` when goldens are configured (pipeline.py:648-660), then `run_scenario`; after it, `_write_network` JSON (pipeline.py:90-121).
- `run_scenario` (loop.py:593-820): `start_scenario_intervals` (655) before the first step; trailing `expect` = `_poll_asserts` (`query()` each poll, loop.py:743-747) + `drain_interruptions` + optional `alert_guard` retry (752-770) with `visual.capture_actual` screenshots (741, 763); `finish_scenario_intervals` in `finally` (792) is the post-run finalization described above.
- `relaunch` step: see §1 — subprocess launch + full readiness gate mid-scenario.

## 4. Measured / stated timing numbers in the tree

| Number | Source |
|---|---|
| adb `uiautomator dump` ≈ 2.4 s per read; resident channel ~0.1–0.3 s | loop.py:127-128, 248-249, 1589, 1710; adb.py:54, 1016, 1043; BE-0234 §Alternatives (2.0–2.4 s `--compressed`, 2.4–2.5 s file+cat) |
| XCUITest `GET /elements` ~0.034 s median (was ~18.6 s); `/elements` walk "~10s+ per screen" | BE-0105 Progress 2026-07-05; xcuitest.py:111 |
| idb `describe-all` 100–300 ms per call | waits.py:129, 212; BE-0259 Motivation |
| `_POLL` 50 ms; guard native probe 1 s; `_SYSTEM_ALERT_POLL` 0.2 s; `_TRANSITION_QUIESCENCE` 0.3 s | waits.py:31, 38, 591; types.py:239 |
| UIKit sheet ~0.35–0.5 s, Android dialog ~0.25 s+; `_TREE_RETAP_DELAY` ~1 s horizon | waits.py:100, 125-130 |
| iOS native alert dismiss "well under a tenth of a second" | docs/scenarios.md:125 |
| adb `_READ_LAG_S` 4.0 s, `_CATCHUP_DWELL_S` 0.5 s, `_SETTLE_DEADLINE_S` 8.0 s, `_SETTLE_POLL_S` 0.1 s | adb.py:536, 543, 505-506 |
| CoordinateTreeDriver transient-empty retry ≤ ~0.75 s (0.05 s doubling to 0.2 s, 5 retries) | coordinate_tree.py:44-46; DESIGN.md:554 |
| `/zorder` round trip 2.0–2.4 ms; per-node refresh 24 ms vs `dumpWindowHierarchy` 19 ms | BE-0355:170, 260-261 |
| Simulator first boot ~80 s; smoke "Run scenarios ~218 s" | BE-0088:20-51 |
| BE-0087 idb settle: `describe-all` ~2.5 s on loaded CI | BE-0087:37 |
| `_VIDEO_START_TIMEOUT` 5 s, `_VIDEO_FINALIZE_TIMEOUT` 120 s, `_STOP_TIMEOUT` 10 s | intervals.py:102-110 |
| XCUITest socket timeouts 15 s read / 30 s write; recovery 60 s | xcuitest.py:115, 124, 155 |
| Android video cap ~180 s | android.py:280; intervals.py:645 |

## 5. Ranked bottleneck hypotheses (per-step, estimates marked ~)

1. **Two screenshots per step under `FileSink`** (`before.png` at loop.py:1320, `after.png` at 1601). Estimate: XCUITest ~50–150 ms each (PNG encode + HTTP), adb `screencap` ~300–800 ms each (subprocess + full-res PNG over adb), so ~0.1–1.6 s/step. Removable without violating directives: `before.png` is evidence only (BE-0341); making it policy-driven (or reusing the previous step's `after.png` bytes/hardlink, since nothing actuates in between — the same reasoning BE-0234 applied to `prev_after`) keeps determinism intact. `after.png` could become policy-gated or async (write off-thread) since nothing on the verdict path reads it.
2. **Unconditional post-step `elements.json` read+write** (loop.py:1712-1727; `_collect_captures` evidence_rules.py:190). One `query()` per mutating step (~35 ms iOS, ~0.1–0.3 s adb resident, ~2.4 s adb dump) plus redaction + `json.dumps(indent=2)` + regex + fsync-free atomic write (~5–30 ms CPU for a 100-element tree). Also the **duplicate baseline `elements.json`** write (same file, overwritten at step end) doubles the CPU/IO half. Removable: make `elements` policy-driven like other capture kinds, or write only once (post-step) — evidence-only, no verdict impact.
3. **Hidden first-step `query()` inside the sink** (core.py:170) whenever `prev_after` is None — one extra read per scenario (and after each `web` block / skipped read), uncounted by `total_reads`. Removable by passing `elements=None` → skip `elements.before` or routing through `_ScreenRead`; ~35 ms–2.4 s per scenario depending on backend.
4. **`drain_interruptions` HTTP round trip every step on XCUITest** (loop.py:1581; xcuitest.py:1170). ~2–5 ms each plus a fresh TCP connect (xcuitest.py:591). Removable: piggyback the drained labels on the `/tap`/`/elements` reply, or drain once per scenario/on failure only. Related: every transport call opens a new `HTTPConnection` — keep-alive could shave ~1–3 ms × 5–7 calls/step.
5. **`wait until: settled` query burn** — tree-diff path needs ≥3 queries and the signal path keeps polling `query()` every ~50 ms for 0.3 s purely to feed guards (waits.py:1042-1061). On adb dump (2.4 s/read) a settle costs ≥7 s; on iOS ~0.3–0.5 s. Reducible: on the signal path query only when a gate/interrupt hook exists (otherwise `clock.sleep` to quiescence) — still a condition wait on the transition signal, not a fixed sleep, because the decision remains "no transition for 0.3 s".
6. **adb driver-internal settle/catch-up on every actuation** (`_await_catchup` ≤4 s, `_settle` ≤8 s, poll 0.1 s; adb.py:941-1047). Orchestrator-visible as tap latency; on a static screen the fast path returns after one read. Not removable from the orchestrator side; it is the deterministic guard against stale frames.
7. **Alert-guard costs under `wait`/`handleSystemAlert`**: `system_alert_labels()` every 1 s (a cross-process SpringBoard query) and, in `wait_for_system_alert`, a full `query()` per 50 ms tick solely for the gate (waits.py:701). ~35 ms × 20/s on iOS while the step waits. Reducible by pacing that gate query to `poll_interval`.
8. **`_InterruptGuard.clear_before_act` extra `query()` per act step** when `interrupts` are declared and no `screenChanged` policy (loop.py:1410) — documented (docs/ci.md, "one bounded read per act"); could reuse `prev_after` under an explicit staleness budget, but the current choice is deliberate for correctness.
9. **Per-scenario tails**: `start_video(confirm_started=True)` growth poll (≤5 s, usually sub-second), video `stop()` finalize (proportional to clip length), `await_ready` backoff (first sleep 0.1 s, settle ≤3 polls), `run_provenance`/`Redactor` construction per lease. All bounded condition waits; the finalize could overlap the next scenario's launch on a multi-device pool but is serial today (`run_scenario` `finally`, loop.py:792).
10. **Optional sleeps that are true fixed waits**: `_settled_render` `time.sleep(0.1)` before each digest (scroll.py ~369) and `_TREE_RETAP_DELAY` — both only on rare fallback paths; not on a passing plain step.

Items 1–4 are pure evidence-path cost and can be trimmed with no change to pass/fail semantics, no fixed sleeps, and no per-app logic; 5 and 7 are guard-pacing changes that keep the wait condition-driven; 6 and 8 are deliberate determinism guards and should stay unless a backend-side signal replaces them.
