**English** · [日本語](ja/evidence.md)

# The evidence (Evidence/Trace) subsystem

[Evidence](glossary.md#evidence-capturepolicy-trace-triage) capture for a recurring action is expressed as a **repeatedly-firing rule** rather than a one-shot instruction. The rule ensures the same evidence is collected without AI on every subsequent run.

Implementation: `bajutsu/evidence/core.py` (instant + Sinks) · `bajutsu/evidence/intervals.py` (interval: video / deviceLog / appTrace). Firing is decided on the orchestrator side ([run-loop](run-loop.md#evidence-rule-firing)).

Related: [the capture tokens in scenarios](scenarios.md#capture-token-grammar) · [reporting](reporting.md)

---

## Three ways to request evidence

| Way | Use | Example |
|---|---|---|
| **A. Rules (`capturePolicy`)** ★ central | automatic capture **every time** a particular action happens | network exchanges on every tap of `settings.*` |
| **B. Per-step (`capture:`)** | this one step only | video + deviceLog around a specific wait |
| **C. Default policy** | a baseline guarantee | config's `capture: [screenshot.after, elements, actionLog]` |

> C (config default) `capture` resolves to `Effective.capture` ([configuration](configuration.md))
> and is applied on top of every step, alongside the scenario's `capturePolicy` and the per-step
> `capture` — unlike those two, it fires unconditionally rather than on a trigger, so it acts as a
> baseline guarantee rather than a rule.

## Evidence kinds and acquisition timing

A `capture:` token is `<kind>[.<modifier>]` ([scenarios](scenarios.md#capture-token-grammar)).

| Kind | Source | Interval / instant | Status |
|---|---|---|---|
| `screenshot` | the driver (XCUITest's own `/screenshot` endpoint, `adb`'s `screencap`, Playwright natively) | instant | ✅ captured |
| `elements` (a11y / accessibility tree) | `driver.query()` as JSON | instant | ✅ captured |
| `actionLog` | orchestrator internals (action · duration) plus each driver's own actuation records | — | ✅ inherent in the manifest |
| `video` | `simctl io recordVideo` | interval | ✅ captured (needs udid) |
| `deviceLog` | `simctl spawn log stream` | interval | ✅ captured (needs udid) |
| `network` | the in-app collector (BajutsuKit → `network.json`) | interval | ✅ captured (the `--network` run flag) |
| `appTrace` | `simctl spawn log stream` over the app's os_log subsystem | interval | ✅ captured (needs udid + subsystem) |
| `rawTree` | the device's own reply behind `elements`, untouched (`base.RawSourceProvider`; adb and XCUITest today) | instant | ✅ captured (opt-in, no-op elsewhere) |

> `appTrace` pairs the app's `os_signpost` / `os_log` `<name> started` / `<name> finished` markers
> into timed intervals (`intervals.parse_app_trace`). `network` is produced by the request collector
> rather than the interval system — its exchanges are written to `<sid>/network.json`
> ([network observation](drivers.md), the `--network` flag).

> `rawTree` writes `hierarchy.raw<suffix>` — the device's/runner's own reply, untouched by any of
> bajutsu's processing: adb's `uiautomator dump`/resident XML (`.xml`), or XCUITest's undecoded
> `GET /elements` body (`.json`). On adb's resident channel, when narrowing changed something, it also
> writes `hierarchy.parsed-input.xml` (what `parse_hierarchy` actually consumed, after SystemUI decor
> windows were stripped) — XCUITest applies no such transform, so it never writes a second file.
> It exists to diagnose a mismatch between a resolved coordinate and the real screen: whether the
> device's/runner's own reply already looked wrong, or bajutsu's own parsing changed it. Never in the
> default capture list — a scenario opts in with `capture: [rawTree, ...]`.
>
> One redaction rule refuses it outright: when `redact.labels` is configured, `rawTree` writes
> nothing for the whole run and logs why. `redact.labels` masks a labeled element's value
> *structurally* — `elements.json` is written from the parsed tree, so the writer knows which value to
> blank — but the raw dump is free text with no such structure, so it would ship an unmasked superset
> of what `elements.json` just masked. Every other redaction rule (`headers`, `fields`, resolved
> secret values) applies to the dump as free text and leaves `rawTree` enabled — with one caveat for
> `redact.headers`/`redact.fields`: their key-pattern masking is written for multi-line logs, where a
> matched value ends at the next newline, but a UI Automator dump is emitted as a single line. A
> configured key that happens to match text inside the dump itself (an on-screen label or
> `content-desc` reading like `Token: ...`) therefore masks everything after that match to the end of
> the file, not just the matched value — the dump still ships, just truncated. A resolved secret value
> (bound via `${secrets.*}`) is unaffected, since it is masked by matching a known literal rather than
> a key pattern.

### `actionLog` — what each step actually did to the screen

`actionLog` needs no capture request and writes no file of its own: every step's outcome carries an
`actuations` list in `manifest.json`, one entry per primitive the driver performed, and the report and
the `bajutsu trace` timeline read it from there. It answers the question a screenshot and an element
tree cannot: *where did this tap land, and how far did this swipe travel*.

| Field | Meaning |
|---|---|
| `gesture` | the driver primitive — `tap`, `doubleTap`, `longPress`, `swipe`, `scroll`, `pinch`, `rotate`, the text primitives, `selectOption`, `systemAlert`, `back` |
| `via` | how the gesture reached its target: `coordinate` (the driver computed a point and sent it), `handle` (XCUITest actuated a snapshot handle), `identity` (the Android device resolved the element and chose the point), `bridge` (a WebView call addressed by element id), `focused` (a text primitive on whatever field holds focus), `key`, `history` |
| `unit` | the coordinate space: `point` (iOS), `pixel` (Android), `cssPixel` (a browser page, or a WebView's own space) |
| `points` | the coordinates the driver sent, in order — one for a tap, two for a drag's start and end. A two-finger gesture records the single anchor its two contacts were derived from, not the contacts |
| `frame` · `target` | the resolved element's bounds and its accessibility identifier |
| `accepted` | whether the platform accepted this attempt, on the two channels that answer (XCUITest's handle actuation, Android's device-side endpoint). A refused attempt is shown struck through, so a stale-retried tap does not read as several taps; `None` means the channel gave no separate answer |
| `duration_s` · `scale` · `radians` | the gesture's non-positional parameters, where it has any |

Three rules bound what a record may say, and every backend honors them:

- **Only a coordinate that was really sent.** `points` is empty whenever no coordinate crossed to the
  platform — a handle-based iOS tap, an Android device-side gesture — because the point was chosen on
  the far side. The record shows the resolved `frame` instead rather than presenting the frame's centre
  as a measurement it did not take.
- **No device work.** Every value is one the actuator already had, so recording costs no extra query,
  read, or round trip.
- **No authored string, ever.** `manifest.json` is written without a redactor, so the record carries
  neither a `type` step's text (not even its length — `Redactor` uses a fixed-width placeholder
  precisely so no artifact discloses a secret's length), nor a `selectOption`'s option, nor an element's
  accessibility label. `target` is always the resolved accessibility identifier and nothing else, so it
  is unset for an element that has none.

A record is written when the gesture is *attempted*, before its transport answers, so a step that
failed to actuate still shows what it aimed at. The step's own result says whether the step worked.

One actuation belongs to no step: the reactive system-alert guard also fires before the scenario-level
`expect` re-check, so its record lands on the scenario's `expect_actuations` beside `expect_alerts`.
A backend that does not implement the record simply contributes none, and the run is unchanged.
The driver's log is bounded, so a pathological step (a `maxScrolls` in the hundreds) can lose its
earliest records, and a damaged record can be lost the same way when a report loads a manifest back —
`dropped_actuations` counts either kind rather than letting the list read as complete.

**Default modifiers**: the always-on instant baseline (below) is `before` — captured before the step
acts, not after. A `capturePolicy` rule or inline `capture:` still defaults an unmodified instant
kind to `after` when it fires; interval kinds (`video`/`deviceLog`) default to `around` (start
before the action, stop after the step). Stating `screenshot.before` explicitly on a rule/inline
capture is redundant with the baseline and is dropped rather than re-taken.

## A. `capturePolicy` (rule-based)

Repeatedly-firing rules, written per scenario (implementation: `scenario/models/evidence.py` `CaptureRule` /
`Trigger`).

```yaml
capturePolicy:
  # On every tap of settings.*, also capture the network exchanges — screenshot and elements are
  # already guaranteed on every step by config's default policy (C, above)
  - on: { action: tap, idMatches: "settings.*" }
    capture: [network]

  # On every screen transition
  - on: { event: screenChanged }
    capture: [screenshot.around, elements]

  # On error in any step, capture the maximum (the safety net)
  - on: { result: error }
    capture: [screenshot, video, deviceLog, elements, actionLog]
```

The trigger `on` is **exactly one** of `action` / `event` / `result`:

- `action: <tap|longPress|type|swipe|...>` — optionally combined with `idMatches` (glob against the
  primary target's `id`). `idMatches` can only be used with `action`.
- `event: screenChanged` — fires if `query()` changed during that step.
- `result: error` — fires if the step failed (the safety net).

The detailed firing logic is in [run-loop](run-loop.md#evidence-rule-firing).

> **Preview firing before a run (BE-0028).** A loose glob or a `screenChanged` rule can fire on
> far more steps than intended, and attaching a heavy capture (`video` / `deviceLog` / `appTrace` /
> `network`) to it quietly produces gigabytes. `bajutsu trace --explain <scenario.yaml>` is a
> read-only dry run that counts
> how many times each rule would fire (and on which steps), and flags ⚠ a heavy capture on a
> broadly-matching rule — so you can tighten the match before paying for it. See [cli](cli.md#trace).

## B. Inline evidence

To capture just one step, attach `capture:` directly to the step.

```yaml
- tap: { id: settings.reindex }
- wait: { for: { id: settings.reindexComplete }, timeout: 5 }
  capture: [video, deviceLog]     # record the interval of this wait
```

(real example in [`demos/showcase/scenarios/evidence.yaml`](../demos/showcase/scenarios/evidence.yaml))

## Interval evidence (video / deviceLog / appTrace)

Implementation: `bajutsu/evidence/intervals.py`. These are **subprocess child processes** — `simctl` on iOS,
`adb` on Android — started before the action and stopped after the step settles. Process spawning is
injectable (`Spawn`) and testable. Web has no subprocess: its intervals are Playwright-native and
supplied by the driver (see below). (`appTrace` is an iOS interval too — a `log stream` over the
app's os_log subsystem, paired into timed intervals by `parse_app_trace`.)

> **Interval kinds are opt-in (BE-0028).** `video` / `deviceLog` / `appTrace` are heavy, so a
> scenario records an interval **only when it asks for that kind** — through an inline `capture:`
> or a `capturePolicy` rule (e.g. a `result: error` rule that captures `video`). A scenario that
> requests none records none, keeping the common case cheap; the lightweight instant baseline
> (`screenshot` + `elements`) is always captured, so a failure still leaves evidence (DESIGN §10).
> It is captured **before** the step acts — `before.png` and `elements.json`, showing the screen the
> step is about to act on rather than the one its action left behind. The scenario's last step gets
> one further baseline capture after it acts (`after.png`), since no following step exists to carry
> its result forward the way every other step's pre-step baseline already does.
> Preview what a scenario would record with `bajutsu trace --explain` (see [cli](cli.md#trace)).

| Kind | Start command (iOS / Android) | Stop signal | Filename |
|---|---|---|---|
| `video` | `simctl io <udid> recordVideo --codec h264` / `adb shell screenrecord` | **SIGINT** (a hard kill would corrupt the mp4) | `scenario.mp4` |
| `deviceLog` | `simctl spawn <udid> log stream --level debug --style compact [--predicate ...]` / `adb logcat -b main,system,crash,events -T 1` | SIGTERM | `device.log` |

- `start_video` / `start_device_log` (iOS) and `start_screenrecord` / `start_logcat` (Android)
  return an `Interval`, and `Interval.stop()` sends the signal and finalizes the file. `deviceLog`
  waits up to 10s, then kills; `video` gets a generous 120s finalize window before the kill, because
  `recordVideo` / `screenrecord` still has to flush and mux the whole clip to disk, and a premature
  kill truncates the mp4 (no `moov` atom) and, on iOS, wedges the simulator's recording session.
  `screenrecord` records device-side, so its `Interval` also pulls the finalized mp4 off the device
  on stop and removes the device copy. If the pull fails (the device vanished), the sink drops that
  one artifact with a warning rather than emit a path with no file behind it — it does not fail an
  otherwise-passing scenario while finalizing interval evidence. `adb screenrecord` caps a single
  recording at ~180s (the platform default/maximum, not a limit bajutsu tunes), so an Android video
  of a longer scenario ends at that mark.
- deviceLog can be narrowed by `--predicate` (NSPredicate) to a subsystem, etc. (the CLI's
  `--log-predicate`) on iOS; `adb logcat` is unfiltered by tag/priority (a logcat filterspec is a
  different syntax, a later knob) and starts the follow from the tail so it reflects the scenario
  window, not the whole ring buffer. It does widen past bare `logcat`'s default buffer set
  (`main,system,crash`) to add `events`: an app's own uncaught exception lands in `crash`, but a
  process killed by `ActivityManager` for memory pressure logs only a structured `am_kill` /
  `am_low_memory` entry in `events` — without it, that cause is indistinguishable from a silent,
  uncaptured failure. The kernel's own out-of-memory (OOM) / low memory killer (LMK) path lands in
  the kernel ring buffer instead, which `logcat -b kernel` reaches only where logd bridges
  `/proc/kmsg` (`ro.logd.kernel`, typically userdebug builds) — so it is left out of the set here,
  not out of reach.
- `INTERVAL_KINDS = {"video", "deviceLog", "appTrace"}`. The orchestrator uses this set to split
  "interval / instant."
- **The scenario-wide `video` begins before the app launches on Android**, so the recording spans
  the app's cold start rather than missing it. There, the environment's `start` starts recording
  (after the device is booted and the app installed, but before `am start`) and hands the running
  `Interval` back through `prestarted_intervals`; the sink *adopts* it at scenario start
  (`intervals.adopt`) instead of starting a fresh one, and on stop finalizes it and relocates the
  file to `scenario.mp4`. Web wires the same up-front capture into the browser context at creation.
  XCUITest, the current iOS backend, records on demand instead: nothing starts a recording before
  the `xcodebuild` runner spawns and launches the app, so its `prestarted_intervals` is always empty.
  The up-front
  behavior is gated by `records_video_up_front`, `True` for Android and web and `False` for
  XCUITest and the fake backend; a scenario that requests no `video` starts none regardless.
- **A confirmed start time corrects the report's step/network timestamps to the video's real
  origin, not the moment recording was merely requested.** `start_video` (iOS) and
  `start_screenrecord` (Android), passed `confirm_started=True` at their production call sites,
  poll a real signal after spawning — iOS the output file's first written byte, Android the
  device-side process appearing (a weaker guarantee: a process existing is not proof its encoder is
  yet emitting frames, but still real and earlier than a guess) — and store the confirmed
  `time.monotonic()` instant on `Interval.true_start`. `intervals.adopt` carries `true_start`
  forward unchanged when it relocates a prestarted interval, so Android's confirmation (made before
  `adopt` even runs) is not lost. The web actuator stamps `true_start` right after the recording
  page is created, with no poll: `record_video_dir` enables recording for the pages in a context,
  but the video itself does not exist until a page does, so the stamp waits for `new_page()` rather
  than `new_context()`. `run_scenario` resolves `video_start_offset = true_start - scenario_start`
  once per scenario and records the corrected origin as `RunResult.video_anchor_s`. A poll that
  never confirms leaves `true_start` at `None`, so the offset is `0.0` and the anchor is exactly
  what it would have been without this correction — never a guessed number.

  How long that poll may run is the one knob here. Startup jitter in `simctl` and `adb` is
  measurably worse on a loaded continuous-integration (CI) machine than on a developer's, and a poll
  that gives up costs the whole scenario its correction. So the ceiling takes an override:
  `BAJUTSU_VIDEO_START_TIMEOUT` (seconds) replaces the 5-second compiled default, and
  [`.github/workflows/ios-e2e.yml`](../.github/workflows/ios-e2e.yml) raises it for the iOS lane
  alongside the three `BAJUTSU_XCUITEST_*` timeouts that already work this way. Raising it costs
  nothing on the healthy path, because the poll returns the moment the recording confirms.
- **The recorded timestamps are absolute; a viewer derives the video-relative offset when it
  renders.** `run_scenario` reads the wall clock once, beside its `time.monotonic()` stamp, giving
  the scenario an anchor pair: any later monotonic instant `t` becomes the wall-clock instant
  `scenario_wall_start + (t - scenario_start)`. Every step's `started_at` and every network
  exchange's `startedAt` takes that form, so what survives the run is the raw timing data rather
  than a number a correction has already reduced. A viewer — `report.html`, `bajutsu trace` —
  subtracts `video_anchor_s` at render time to place an event on the recording's timeline. That is
  what makes the placement recomputable from a saved run: after a fix to how the anchor resolves, a
  re-render lands the events where they belong, with no need to run the scenario again
  ([BE-0348](../roadmaps/BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording.md)).
  Every timing *decision* still reads the monotonic clock alone, since a wall clock can jump
  backward mid-run and would corrupt a wait's timeout or a step's duration. See
  [reporting](reporting.md#manifestjson) for what each recorded field means to a report reader.

### Touch markers in the recording (`--touch-markers`)

A recording shows every consequence of a gesture and never the gesture itself, so `bajutsu run
--touch-markers` asks the app under test to draw a marker at each touch it receives: a translucent
circle at the contact, and a trail behind a contact that moves. The marks are drawn inside the app's
own process, so they reach the recorded video and each step's `after.png` alike. Because they are
drawn from the `UIEvent` the app dequeues rather than from the coordinate the driver sent, a marker
is evidence that the touch was *delivered*, which a driver-side coordinate record cannot show.

Three properties matter before turning the flag on.

- **It needs an app that links BajutsuKit.** The drawing lives in `BajutsuKit` (`BajutsuTouch`),
  which the demo apps already link. The flag sets `BAJUTSU_TOUCH_MARKERS=1` on the app's launch
  environment, and an app that does not link BajutsuKit ignores the variable.
- **The marker is a `CALayer`, so it never enters the accessibility tree.** A layer is not a
  `UIResponder` and conforms to no accessibility protocol, so no selector can resolve to it and it
  can swallow no gesture. `demos/showcase/scenarios/golden/golden_xcuitest.yaml` holds that claim to
  account by asserting the same tree golden twice, once with the markers on and once with them off.
- **A gesture's marks stay until the next gesture starts.** No timer removes them, which is what
  keeps them in the step's screenshot, and equally why a run with the flag on produces screenshots
  that differ from a run without it. Leave the flag off for any pixel comparison, the way the
  Android lanes leave the operating system's `show_touches` and `pointer_location` settings off for
  theirs (`demos/showcase/android/Makefile`).

The markers are evidence only: no assertion reads them, and the flag is off by default.

## Sinks (where evidence goes)

```python
class EvidenceSink(Protocol):
    def capture(self, driver, step_id, kinds, *, elements=None) -> list[Artifact]: ...   # instant captures after a step
    def wait_diagnostic(self, step_id, *, trace, elements) -> Artifact | None: ...       # the first-wait timeout diagnostic (below)
    def start_scenario_intervals(self, scenario_id, kinds) -> list[Interval]: ...        # begin video / deviceLog / appTrace for the whole scenario
    def finish_scenario_intervals(self, scenario_id, started) -> list[Artifact]: ...     # stop them and collect the files
```

| Sink | Behavior |
|---|---|
| `NullSink` (default) | writes nothing (keeps a run side-effect-free) |
| `FileSink(run_dir, udid, log_predicate)` | writes under `run_dir/<step_id>/` |

A capture the environment already began before launch (Android's `video`) is *adopted*
rather than started — the sink relocates its finalized file into the scenario dir on stop. Otherwise
interval captures come from the driver's `driver_interval` provider when it supplies one (web's
Playwright-native console / video, Android's `adb` logcat); failing that `FileSink`
takes the simctl path, which it skips when `udid` is absent. The CLI's `run` uses
`FileSink(runs/<runId>, udid=..., log_predicate=...)` ([cli](cli.md#run)).

## First-wait timeout diagnostic (BE-0231)

A `wait for <element>` that times out writes `run_dir/<step_id>/wait-timeout.json`
**unconditionally** — independent of `capturePolicy`, so a timeout that no policy rule would have
captured still leaves the evidence needed to decide *why* it fired. It is pure diagnosis, never a
verdict input (the run's pass/fail still comes only from machine-checkable assertions).

The file is self-contained so a rerun-to-green does not discard it:

| Field | What it answers |
|---|---|
| `readiness` | Whether the post-launch readiness gate had passed and on which signal (`readyWhen` / `namespace` / `count`, or `timeout`) — separates "the gate returned before the content" from "the content rendered but the awaited element did not". `null` on a lane that carried no readiness result. |
| `trace` | The poll timeline: how many polls, when the tree first became non-empty (`firstNonemptySeconds`, `null` if it never did), and how many elements were present at the timeout — separating "nothing rendered / transient-empty" from "rendered, awaited element absent" from "slow cold-boot render". |
| `provenance` | A [BE-0049](../roadmaps/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) stamp (scenario hash, tool version, git revision), so the evidence stays identifiable independently of the run. Its `scenarioHash` fingerprints **this scenario alone**, without the file-level `description` the run manifest's `scenarioHash` folds in when present — so it can diverge from the manifest's hash even for a single-scenario run, not only for a suite/matrix run. |
| `elements` | The (redacted) element tree at the moment of timeout. |

It is recorded as an `Artifact(kind="waitDiagnostic", provider="runner")` — written by the run loop,
not a backend actuator.

## Artifact provenance (provider)

Every piece of evidence is recorded as an `Artifact(name, kind, provider)`, leaving in the manifest
**which provider it came from**.

```python
@dataclass
class Artifact:
    name: str       # filename (e.g. "before.png")
    kind: str       # "screenshot" / "elements" / "video" / "deviceLog" / "network" / "waitDiagnostic"
    provider: str   # who supplied this artifact (see table below)
```

| `provider` value | Meaning |
|---|---|
| `"driver"` | The actuator captured it directly (screenshots, element trees). |
| `"runner"` | The run loop wrote it (the first-wait timeout diagnostic, [BE-0231](../roadmaps/BE-0231-smoke-idb-first-wait-settling/BE-0231-smoke-idb-first-wait-settling.md)). |
| `"simctl"` | Interval evidence from `simctl` (video, device log, app trace). |
| `"adb"` | Interval evidence from `adb` (screenrecord video, logcat device log). |
| `"collector"` | The app-side network collector (`BAJUTSU_COLLECTOR`). |
| `"playwright"` | Native Playwright network observation (web backend). |
| `"<backend> (fallback)"` | A read-only evidence fallback supplied the artifact ([BE-0020](../roadmaps/BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback.md)). |

When an evidence kind cannot be supplied by any backend in the list, a `SkippedCapture(kind,
reason)` is recorded per scenario and disclosed in the manifest — the gap is never silently empty.

## Visual evidence

A `visual` assertion produces a `VisualEvidence` record carried into the manifest and the
report. It contains the run-dir-relative paths to the baseline copy, the actual screenshot,
and the diff visualization (when the comparison found differences), plus `diff_pct` (the
percentage of pixels that differed) and `engine` — the comparison engine that produced the
verdict (`"exact"` or `"pixelmatch"`; [BE-0165](../roadmaps/BE-0165-visual-compare-engines/BE-0165-visual-compare-engines.md)).

The engine is selected per assertion (`compare:`) with a target-level config fallback
(`visualCompare`), and is recorded in the manifest so the algorithm that produced each
verdict is traceable. Implementation: `bajutsu/assertions/visual.py` `VisualEvidence`.

## Masking (redact)

Screenshots, logs, and network data can capture personally identifiable information (PII) and tokens. Declare what to mask before writing. Implementation: `scenario/models/evidence.py` `Redact`. Config's `redact` and the scenario's `redact` are merged (union) ([configuration](configuration.md#merging-redact)).

```yaml
redact:
  labels: ["Card Number"]               # accessibility labels
  headers: ["X-Session"]                # extra HTTP header names (on top of the defaults)
  fields: ["token", "password"]         # JSON/body field names
  unmaskHeaders: ["authorization"]      # opt out of a default (visible, deliberate)
```

> **Sensitive headers are masked by default** (a scenario needs no `redact:` for this): the
> built-in set is `authorization`, `proxy-authorization`, `cookie`, `set-cookie`, `x-api-key`,
> and `x-auth-token`, matched case-insensitively. `cookie` and `set-cookie` are treated as one
> concern — naming (or unmasking) either covers both. Header names in `redact.headers` add to
> this set; they never replace it. If you genuinely need a default header's raw value (e.g.
> debugging an auth failure), name it under `unmaskHeaders` — turning off protection is an
> explicit, visible choice, never the mere absence of `redact:`.

> Redaction **is applied** before evidence is written (`evidence/redaction.py` `Redactor`): the device log /
> app trace are scrubbed by key→value patterns, the element tree masks a value when its label is
> configured (or scrubs an embedded secret), and each network exchange is masked structurally —
> header values by name, and the url / request / response bodies as free text (so query params and
> `token` / `password` body fields are caught). Images (screenshots / video) cannot be masked and
> are left as-is.
>
> Redaction also extends to **secret input values**: the literal values behind `${secrets.X}`
> (resolved from the environment, declared via config's `secrets:`
> [configuration](configuration.md#secrets-secrets)) are masked wherever they would appear in
> evidence — not just the configured `labels` / `headers` / `fields`. Longest values are masked
> first so a value that is a substring of another never leaves a partial leak.
>
> Value matching is **encoding-aware**: the same secret reaches evidence verbatim but often
> encoded, so its literal bytes never appear. Alongside the raw value, redaction masks its
> common encodings — percent-encoded (a URL query or form field, e.g. `p@ss` as `p%40ss`),
> HTML-escaped and JSON-escaped forms, and an `Authorization: Basic <base64(user:pass)>` token
> whose decoded credential carries the value. This is a fixed set of transforms applied to
> *known* values (the value is encoded, then searched for), not a decode-everything scan, so
> the cost and false-positive surface stay bounded. One limitation remains: where evidence is
> genuinely fragmented before redaction runs (a value split across streamed chunks that
> redaction never sees as one contiguous string), matching is best-effort — assembled full-text
> evidence, the common case, is unaffected.
>
> The executed scenario is also snapshotted into the run directory (`scenario.yaml`, and the raw
> YAML view in the report). A `totp` step's `secret` is a durable base32 seed, not a one-time code,
> so a **literal** seed written straight into the scenario is masked to `<redacted>` in that
> snapshot — a `${secrets.X}` reference is kept as-is (it is not the seed, and its resolved value
> is masked by the secret-value rule above). Prefer `${secrets.X}` for a `totp` seed so it never
> sits in the scenario file to begin with.

## File permissions

Redaction reduces what a leaked artifact reveals, but it is a best-effort denylist, so who can read the artifact matters too. The runner creates each run directory owner-only (`0700`) and writes the sensitive files it may hold — `network.json`, the copied `scenario.yaml`, the element dump (`elements.json`), and screenshots — owner-only (`0600`), independent of the host's `umask` ([BE-0131](../roadmaps/BE-0131-run-artifact-permissions/BE-0131-run-artifact-permissions.md)). Everything else lands under the `0700` run directory, so a run's evidence is not readable by another local account on a shared host (a CI runner, say) by default. Implementation: `artifact_perms.py`.
