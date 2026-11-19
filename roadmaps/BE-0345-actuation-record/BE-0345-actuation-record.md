**English** · [日本語](BE-0345-actuation-record-ja.md)

# BE-0345 — Record the concrete coordinate and gesture geometry each step actually actuated

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0345](BE-0345-actuation-record.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0345") |
| Implementing PR | [#1511](https://github.com/bajutsu-e2e/bajutsu/pull/1511) |
| Topic | Verification & coverage |
<!-- /BE-METADATA -->

## Introduction

A finished run records what each step was *asked* to do, and never what the driver actually did to
the screen. This item adds the missing half. Every actuation a step performs is recorded on the
step's outcome: the coordinate a tap injected, the two endpoints a swipe travelled, and the channel
that carried the gesture. The record is written to `manifest.json` and shown in the report and the
`bajutsu trace` timeline. It is evidence only and no assertion reads it, so the deterministic
verdict is untouched.

## Motivation

A step's report entry names its action and its duration, and stops there. `StepOutcome`
(`bajutsu/orchestrator/types.py:118`) carries `index`, `action`, `ok`, `reason`, `duration_s`,
`started_at`, the assertion results, the artifacts, and the system alerts the guard dismissed.
Nothing on it says *where* a `tap` landed or how far a `swipe` travelled. The `action` field holds
only the kind name, `"tap"`, not the resolved target. The answer to "which pixel did this tap touch"
is absent from the one file a run treats as its source of truth.

The gap is not a missing view over data already captured. The concrete values exist only inside a
driver, for the microseconds between resolving them and injecting them. Only one of the four
backends even logs them in passing. The Android driver emits two `logger.debug` lines: the resolved
frame (`bajutsu/drivers/adb.py:865`) and the coordinate it is about to inject
(`bajutsu/drivers/adb.py:924`). The comment on the second calls the pair "the whole of *where did it
actually tap*". Both lines are invisible unless someone raised the log level before the run, and
neither reaches the run directory. The iOS (XCUITest), web (Playwright), and fake backends log
nothing comparable. On those backends the coordinate is unavailable at any log level, after the fact
or during the run.

That absence makes a whole class of failure expensive to diagnose. On Android, a gesture can miss
its target because the accessibility tree it was resolved from described the previous screen. This
is a recurring, measured problem. A `long_press` missed its target by 10 pixels, and a 73-pixel
swipe read as unchanged for more than a second. Both traced to a tree published before the gesture
([BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md),
[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)).

Diagnosing one of these failures means comparing where the touch went with where the element
actually was. The run directory cannot support that comparison today, because it keeps the element
tree (`elements.json`) and the screenshot but not the touch. The investigation instead re-runs the
scenario with debug logging on and hopes the flake reproduces.

The channel that carried a gesture matters as much, and it is unrecorded too. Android
actuates a `tap` in one of two ways:

- The resident UI Automator server's `/act` endpoint reads the element's bounds on the device
  microseconds before injecting.
- When that endpoint is unavailable, the host computes a coordinate a full round trip ahead of the
  touch ([BE-0339](../BE-0339-adb-device-side-actuation/BE-0339-adb-device-side-actuation.md)).

Only the second path can drift. A run that took the first path and still missed has a different
cause entirely, and `manifest.json` records neither path. [`DESIGN.md`](../../DESIGN.md) §10's
shipping-criteria checklist carries the requirement as an unticked line: a step that fell back to a
raw coordinate should be disclosed in the manifest as a degradation (`DESIGN.md:543`). No item owns
that line.

The [evidence](../../docs/glossary.md#evidence-capturepolicy-trace-triage) subsystem
already promises this record. `actionLog` is a declared evidence kind in
[`docs/evidence.md`](../../docs/evidence.md), in `DESIGN.md` §9, and in the shipped default capture
policy (`bajutsu/config/schema.py:258`, `capture: [screenshot.after, elements, actionLog]`).
`DESIGN.md` §9's table describes its source as "the orchestrator's internals (action, arguments,
result, and duration)" and marks it always-on.

The code does nothing with the token. `capture()` skips it with the comment "actionLog lives in the
manifest" (`bajutsu/evidence/core.py:179`), and what lives in the manifest is the action name and
the duration. The arguments half of that promise was never built. This item builds it, and revises
the two documents to describe what is actually recorded.

## Detailed design

One fact shapes the whole design: the concrete coordinate is knowable only inside the driver, and
sometimes not even there. Each [actuator](../../docs/glossary.md#driver-backend-actuator-platform)
resolves and delivers a gesture differently:

- The Android driver computes a pixel coordinate and injects it, or hands an element identity to the
  device and lets the device pick the coordinate.
- The iOS driver hands XCUITest a handle to a snapshotted element, and XCUITest picks the point.
- The web driver clicks a CSS-pixel coordinate.
- The WebView bridge actuates by selector inside the Document Object Model (DOM).

No layer above the driver sees any of that. The driver is where the record is produced.

Three rules govern the record.

**Rule one: the record holds the coordinate that was really handed to the platform, and never one
reconstructed afterwards.** A driver that computes a frame center and sends it records that center.
A driver handed two endpoints by the `swipe` handler records those endpoints, since they are what
crossed to the platform. A handle-based iOS tap records the resolved element and its frame and
leaves the touch point *unset*. No coordinate was handed to anything, because XCUITest picked the
point on the far side of the handle. Writing the frame's center there would present a plausible
guess as a measurement. The same holds for Android's device-side channel, which sends an element
identity and lets the device choose.

**Rule two: the record costs no device work.** Every value it carries is one the actuator already
had for its own use: the frame it resolved, the point it sent, the duration it passed. Recording
adds no device read, no query, and no round trip. The read-count invariant that
`tests/orchestrator/test_read_count.py` pins (a plain `tap` issues zero loop-side reads) stays
exactly as it is, and a unit below tests that it does. Memory is bounded for a different reason. The
crawl, `record`'s replay, and the driver conformance suite also call the actuators, and none of them
drains. The accumulator  caps itself, instead of growing with a long session.

**Rule three: the redaction boundary.** Three values a driver holds can carry a resolved
`${secrets.*}`, because `_interp_step` (`bajutsu/orchestrator/substitution.py:14-29`) substitutes the
whole step, so any authored string can:

- the text a `type` step entered;
- the `option` a `selectOption` step selects;
- an element's accessibility label, which is why `Redactor.redact_elements`
  (`bajutsu/evidence/redaction.py:150-169`) scrubs `label` and `value` before `elements.json` is
  written.

`manifest_dict` applies no redactor (`bajutsu/report/manifest.py`), so the record must not carry any
of the three in the first place. It records that a `type` or `selectOption` happened and nothing
about the string. It does not record even the string's length. `Redactor` replaces a secret with a
fixed-width placeholder (`redaction.py:23`) precisely so that no artifact discloses a password's
length, and a driver holds neither the redactor nor the bindings that would tell it which strings
were secrets.

The record's `target` is always the resolved `Element["identifier"]`, never a label, and never a
backend's own richer addressing value. That last clause is not hypothetical. Android addresses the
device-side channel with a `NodeIdentity`, a four-tuple of `resource-id`, `content-desc`, `text`,
and `class` taken verbatim from the dump (`bajutsu/drivers/adb.py:81-82`). Its `content-desc` and
`text` are the fields the redactor scrubs, and for a field a `type` step filled, `text` *is*
the entered string. Recording the identity as `target` would leak what rule three forbids.
The Android record keeps the normalized identifier, like every other backend. An element
with no identifier is still localized by its frame and its coordinate. Those carry no such risk and
are recorded verbatim.

### The record

`Actuation` is a frozen dataclass in a new module, `bajutsu/drivers/actuation.py`. It holds one
primitive a driver performed:

| Field | Meaning |
|---|---|
| `gesture` | the driver primitive, e.g. `tap`, `doubleTap`, `longPress`, `swipe`, `scroll`, `pinch`, `rotate`, `typeText`, `deleteText`, `selectAll`, `copy`, `selectOption`, `systemAlert`, `back` |
| `via` | how the gesture reached its target: `coordinate` (the driver computed a point and sent it), `handle` (XCUITest actuated a snapshot handle), `identity` (the Android device resolved the element and chose the point), `bridge` (a WebView bridge call addressed by element id), `focused` (a text primitive on whatever field holds focus, addressing nothing), `key` (a key event), `history` (browser history) |
| `unit` | the coordinate space the numbers are in: `point` (iOS), `pixel` (Android), `cssPixel` (a browser page, and a WebView's own space) |
| `points` | the contact points the gesture touched, in order — one for a tap, two for a drag's start and end, empty when the driver chose no coordinate |
| `frame` | the resolved element's frame `(x, y, w, h)`, where the driver resolved one |
| `target` | always the resolved `Element["identifier"]` and nothing else, so the field cannot carry free text; unset for an element with no identifier (rule three above) |
| `accepted` | whether the platform accepted this attempt, on the two channels that can refuse and be retried (XCUITest's handle actuation, Android's device-side endpoint); `None` where the driver got no separate answer, in which case the step's own `ok` / `reason` is what says whether the step worked |
| `duration_s` · `scale` · `radians` | the gesture's non-positional parameters, each set only where its gesture has one. A string parameter (`type`'s text, `selectOption`'s option) is absent, in any form — see rule three above |

`gesture` and `via` are `str`, not `Literal`, deliberately. The report renderer reconstructs these
records from a `manifest.json` that an older or newer version of the tool wrote
([BE-0068](../BE-0068-regenerable-reports/BE-0068-regenerable-reports.md) makes that
forward-and-backward compatibility the loader's contract). A `Literal` would assert at the type
level a guarantee the file on disk cannot make. The vocabularies are module-level tuples with a
docstring instead, and the renderer treats an unknown value as opaque text.

`ActuationLog`, in the same module, is the accumulator a driver holds. `record()` appends, and
`drain()` returns everything since the last drain and empties the log. It is backed by a `deque`
bounded at 512. A consumer that never drains (the crawl, `record`'s replay, the conformance suite)
keeps the most recent entries, instead of accumulating one record per gesture for a whole
session.

The bound is sized well above the worst case for a single drained step, which is not the handful a
`tap` costs. A `scroll` step spends up to `maxScrolls` gestures (default 15, author-settable with no
ceiling, `bajutsu/scenario/models/actions.py:332`). On Android, a plain `tap` can add three more
swipes as `_scroll_into_view` brings its target on screen (`adb.py:398`).

Dropping is also disclosed rather than silent, since the earliest gestures of a step are exactly
what "the scroll never reached the target" needs. `drain()` returns the count alongside the records,
and the loop puts it on `StepOutcome.dropped_actuations`. The report and the timeline then show a
truncated record *as* truncated. A log line would not do: this item's own Alternatives section
rejects that, since a log line is absent unless someone raised the level before the run and
never reaches the run directory.

`ActuationReporter` is the narrow, `runtime_checkable` protocol the orchestrator reads the log
through. It has one method, `drain_actuations()`. A backend that does not implement the protocol
records nothing, and the run is unchanged. `ViewportProvider`, `ReadLagProvider`, and
`SettledReadProvider` (`bajutsu/drivers/base.py`) already work the same way.

The module is new rather than an addition to `bajutsu/drivers/base.py`, whose own docstring calls
it the frozen linchpin every backend depends on. It joins both import-linter contracts in
[`pyproject.toml`](../../pyproject.toml) that already name `bajutsu.drivers.base`: the deterministic
core's independence from the periphery, and the portable inner contract. The record type can
never grow a dependency on `serve`, `triage`, the agents, the orchestrator, the runner, or
the report.

### Work breakdown (MECE)

1. **The record, the log, and the protocol.** Add `bajutsu/drivers/actuation.py` with `Actuation`,
   `ActuationLog` (a `deque` bounded at 512, counting and warning on a drop), `ActuationReporter`,
   and the two vocabulary tuples described above. Add the module to the `source_modules` of both
   `pyproject.toml` contracts that name `bajutsu.drivers.base`: the periphery-independence contract
   and the portable-inner-contract one. Either alone would leave the other's imports unguarded. No
   behavior changes in this unit, because nothing constructs an `Actuation` yet.

2. **Each backend records what it actually did.** Every driver gains an `ActuationLog` and a
   `drain_actuations()` method, and records one `Actuation` per primitive it performs.

   Each record is the *attempt*, written before the transport's outcome is known. A step that failed
   to actuate (an exhausted `stale` retry, a channel error, a resident endpoint that declined) still
   shows what it tried. The step's own `ok` / `reason` carries the verdict, and the drain in unit 3
   runs regardless of it. Android's fallback records two entries for one `tap`, in the
   order a reader needs: the declined `identity` attempt, then the `coordinate` injection that
   followed.

   Recording the attempt is only half of it. On the two channels that *answer*, XCUITest's handle
   actuation and Android's `/act` endpoint, the platform can refuse and the driver retries. The
   driver stamps the record with that answer as soon as it arrives (`ActuationLog.settle`). Without
   the stamp, a stale-retried tap would leave three byte-identical records and nothing saying which
   one the device honored, so a report would render one tap as three. A channel that gives no
   separate answer leaves `accepted` unset rather than claiming success. That is also how Android's
   "the request went out but the reply was lost" case reads.

   The recording site is wherever the concrete value already sits, so no driver resolves anything
   twice:
   - **XCUITest** (`bajutsu/drivers/xcuitest.py`) constructs the record in one place, `_actuate`,
     the function every handle-based gesture routes through. `_actuate` today receives only `path`,
     `body`, and `sel` (`xcuitest.py:550`) and so has no element in scope. Re-resolving one there
     would cost a second `/elements` round trip, which breaks rule two. So `_resolve_handle` returns
     the `(handle, element)` pair it already has, and each of the five callers (`tap`, `double_tap`,
     `long_press`, `pinch`, `rotate`, `xcuitest.py:585-606`) forwards that element plus its own
     `gesture` name into `_actuate`. Passing the name is deliberate. Inferring `doubleTap` from
     `body["taps"] == 2` would duplicate knowledge of the wire shape into the recorder, and would
     mislabel the day the shape changes. `via` is `handle` and `points` is empty.

     A `stale` reply re-resolves the handle
     ([BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md)).
     `_actuate` overwrites the element from its own re-resolve, and the record states the
     element the *final* attempt resolved, which is the one that was actuated.

     Eight primitives bypass `_actuate` and own their own transport, so each records where it sends:
     - `tap_point` (`xcuitest.py:594-598`), `swipe` (`:608-611`), and `scroll` (`:625-632`) record
       `via: coordinate` with their points.
     - The text primitives (`type_text`, `delete_text`, `select_all`, `copy_selection`,
       `xcuitest.py:681-699`) record `via: focused`. They address no element, acting on whatever
       field holds focus.
     - `handle_system_alert` (`xcuitest.py:639`) posts its own `/systemAlert/tap` and records
       `gesture: systemAlert`. Its `target` is usually unset. A SpringBoard alert button is
       addressed by visible label and generally carries no identifier (`xcuitest.py:665-675`), and
       rule three forbids recording the label. What makes unit 3's guard-tap attribution real is
       the record's presence on the interrupted step, read alongside the button name the
       step's existing `alerts[].label` already carries.

     Two iOS primitives produce no record of their own, for structural reasons worth stating.
     `back` taps the OS back button through `tap` (`xcuitest.py:676-679`), so an iOS `back` step
     records `gesture: tap` / `via: handle` on `BackButton`, and no iOS record ever carries
     `gesture: back`. `select_option` raises `UnsupportedAction`, so it records nothing. `unit` is
     `point` throughout.
   - **Android** (`bajutsu/drivers/adb.py`) records the two channels distinctly. `_device_act`
     records `via: identity` with the resolved element's normalized identifier as `target`, plus its
     resolved frame and no point, since the device chose the coordinate. The `target` is not the
     `NodeIdentity` tuple it sends the device, for the redaction reason rule three gives. The
     coordinate fallback records `via: coordinate` with the injected point. The internal resolve
     helpers (`_center`, `_center_with_screen`, `_resolve_frame_and_screen`) return the resolved
     element, so `target` and `frame` come with it.

     The rooted `sendevent` double-tap records the tree-space point, not the raw touch-device range
     it scales that point into. The raw range is an artifact of the injection method, and recording
     it would make two double-taps on the same element look like different coordinates. `unit` is
     `pixel`. `back` records `via: key` with no coordinate, matching what a `KEYCODE_BACK` event is.
     The text primitives record `via: focused`, as on iOS.
   - **Playwright** (`bajutsu/drivers/playwright.py`) records `via: coordinate` in `cssPixel` for
     every mouse and touch primitive, `via: focused` for the text primitives, and `via: history` for
     `back`. It is also the one backend that implements `select_option` (`playwright.py:697`). That
     is a genuine actuation that resolves the element and sets its value at the computed center,
     rather than raising. It records `gesture: selectOption` as a coordinate, with no trace of the
     option string, per rule three.
   - **The WebView context driver** (`bajutsu/webview.py`) records `via: coordinate`, not a channel
     of its own. `tap` (`webview.py:128-134`) and `double_tap` (`:141-143`) compute the element's
     frame center themselves and hand the bridge a point. `tap_point` (`:138-139`) forwards the
     caller's point unchanged. In every case a coordinate crossed to the WebView, which is what rule
     one asks for. The point is in the WebView's own coordinate space rather than the device
     screen's, so `unit` is `cssPixel` and the record is read against the WebView, not the device.

     `tap` also scrolls its target into view first (`scroll_to`, by element id). That scroll is
     recorded as its own `via: bridge` entry. It moves the content the tap's point was computed
     against, which is precisely the failure class this record exists to expose, so leaving it out
     would hide the thing a reader is looking for. `type_text` records `via: focused`. Every
     other primitive raises `UnsupportedAction` in this first slice and records nothing.
   - **`FakeDriver`** (`bajutsu/drivers/fake.py`) records `via: coordinate` with the frame center it
     computes for the record. This is legitimate under rule one, because the fake is the thing that
     would choose the point: it is a coordinate backend whose device is memory. The center is taken
     in `query()` space, translated by the scroll offset the fake's scrollable-viewport model applies
     (`fake.py:80-91`). It is not taken in the untranslated `self.screen` space its uniqueness check
     resolves against. A recorded point means the same thing as the points the orchestrator
     hands `swipe`.

     The fake's `unit` is `point`, an arbitrary but fixed choice. The fake's seeded frames belong to
     no real device's space, and leaving the field empty would make the fake the one backend whose
     records cannot be read uniformly.

     The fake implements the whole actuating surface and must cover that whole surface, since the
     `tests/orchestrator/` cases in unit 6 run on it. `back` records `via: key` (`fake.py:138`). The
     text primitives and `select_option` record as they do on the real backends (`fake.py:149-164`,
     with `select_option` succeeding rather than raising). `handle_system_alert` records
     `gesture: systemAlert` (`fake.py:166`). Recording a real coordinate rather than a stub is what
     lets unit 6 assert exact geometry on the fast gate with no device.

3. **The step loop drains the log onto the outcome.** `StepOutcome` gains
   `actuations: list[Actuation]`. `_handle_action` (`bajutsu/orchestrator/loop.py`) drains
   `active_driver` once, after the step body has finished, where `outcome.ok` and
   `outcome.reason` are already assigned.

   Draining once rather than per attempt is deliberate. When the reactive system-alert guard
   dismisses a prompt and retries the body, both attempts' actuations are real things that happened
   to the device, and the drained list holds them in the order they occurred. For the same reason,
   the tap the guard itself performed to dismiss the prompt lands on the step it interrupted. That is
   where a reader looking at that step would expect to find it.

   The drain reads `active_driver`, not `self.cfg.driver`, because a step inside a `web` block
   actuates the WebView driver. Nothing actuates the native driver during such a step, so nothing is
   stranded.

   One actuation happens with no step to attribute it to. The guard also fires for the
   scenario-level `expect` re-check, outside the step loop entirely (`loop.py:482`), which is why
   `RunResult` already carries `expect_alerts` separately from any step. `RunResult` gains
   `expect_actuations` beside it, drained at that same point, so the guard's tap is recorded rather
   than accumulated and dropped. Nothing leaks across scenarios either way: each scenario's
   environment builds a fresh driver, so the log dies with the lease.

4. **The manifest carries it, and the loader reads it back.** `manifest_dict` serializes
   `StepOutcome` with `asdict`, so the records reach `manifest.json` with no change to
   `bajutsu/report/manifest.py` beyond bumping `SCHEMA_VERSION` to 5 with a comment naming this item.
   An older run then renders with the new view absent rather than failing (the BE-0068 contract).

   Two places assert the version and must move with it, or the gate goes red:
   `tests/report/test_load.py:69` asserts `data["schemaVersion"] == 4`, and `docs/reporting.md` pins
   the number in prose twice (unit 7).

   `bajutsu/report/load.py` gains one line in `_step` and one in `_result` (for unit 3's
   `expect_actuations`) to reconstruct the nested records. It also gains a small coercion: JSON has no
   tuple, so `points` and `frame` arrive as lists and are converted back to the tuples the type
   declares. Without that coercion the existing `test_round_trip_through_manifest_is_lossless` would
   fail, which is the guard that test exists to be.

5. **Two views surface it.** The HTML report gains a row under each step that actuated something,
   mirroring the existing `alertrow` shape (`bajutsu/templates/report.html.j2`,
   `bajutsu/report/rows.py`, `bajutsu/templates/report.css`). The row shows the gesture, the channel,
   the resolved target, and the geometry:
   - `tap → (128.0, 460.5) pt on settings.reindex` for a coordinate tap;
   - `tap → settings.reindex [124, 448, 96, 44] via handle` where the driver chose no coordinate.

   Unit 3's scenario-level `expect_actuations` is surfaced the same way as the `alertrow` shape's own
   scenario-level counterpart. `rows.py` builds a step's `alerts` at `rows.py:164` and the expects
   block's `expect_alerts` at `rows.py:392`, so the new field is rendered beside the latter rather
   than written and then shown nowhere.

   The `bajutsu trace` timeline gains the same summary on its per-step line (`_step_event` in
   `bajutsu/trace.py`). That function reads the manifest dict directly, so it needs no loader change.
   The expect-phase records are report-only, since the timeline has no expect line to hang them on.
   The `serve` web UI is out of scope: it renders its own run view, and extending it is a
   separate change to a separate surface.

6. **Deterministic coverage.** `FakeDriver`-backed tests in `tests/orchestrator/` assert exact
   geometry, since unit 2 makes the fake record real coordinates:
   - A `tap` records the resolved element's frame center and its identifier.
   - A directional `swipe` records two points matching the endpoints `_scroll_gesture` computes.
   - A `long_press` records its duration.
   - A `pinch` records its scale and the resolved frame.

   Those cases use the fake's plain (non-scrolling) mode, where the recorded point and the seeded
   frame share one space. A further case runs the scrollable-viewport mode after a scroll and asserts
   the recorded point is the *translated* center, pinning the space unit 2 chose.

   Attribution is tested as its own property. Each step's outcome carries only the actuations that
   step performed. A non-actuating `assert` step carries none. A step whose body ran twice under the
   alert guard carries both attempts in order.

   A driver-level test per real backend asserts the channel. The Android driver records `identity`
   when the resident endpoint serves the gesture and `coordinate` when it declines. The XCUITest
   driver records `handle` with an empty `points` list.

   The redaction boundary gets its own cases, since a leak here would be silent:
   - A step actuating an element that has a label but no identifier records no `target` at all.
   - A `type` step's record carries no field derived from its text, neither the text nor a length.
   - The Android `identity` record carries the normalized identifier rather than any component of the
     `NodeIdentity` tuple it sent the device. The test asserts this against a seeded dump whose
     `content-desc` and `text` differ from its `resource-id`, so a regression to the tuple cannot
     pass.

   A `tests/orchestrator/test_read_count.py` case proves the drain issues no read, guarding the
   invariant that module exists to protect. A round-trip case proves a manifest written with
   actuations reconstructs to equal records. A `bajutsu trace` case proves the timeline line renders
   from a manifest dict.

7. **Documentation.** [`docs/evidence.md`](../../docs/evidence.md) and its Japanese mirror
   `docs/ja/evidence.md`: the `actionLog` row changes from "orchestrator internals (action ·
   duration)" to name what is now recorded. It also gains the redaction boundary (never the typed
   text, not even its length, and never a label) and the rule that a driver records only a coordinate
   it chose itself.

   [`DESIGN.md`](../../DESIGN.md) §9's evidence-kind table row for `actionLog` is revised the same
   way. §10's shipping-criteria checklist line about disclosing a coordinate fallback
   (`DESIGN.md:543`) is amended to record precisely which half this item closes, and no more.

   The ladder rung that line means is Android's. §5 (`DESIGN.md:191`) scopes the fallback to a `tap` /
   `longPress` / `doubleTap` that dropped to a coordinate because the resident endpoint was
   unavailable or the identity unknown, and the record's `identity`-versus-`coordinate` pair now
   names that per step. The token cannot be read as a degradation on its own, since `coordinate` is
   also the ordinary, non-degraded channel for Playwright, the WebView driver, the fake, and iOS
   `swipe` / `scroll` / `tap_point`. The amendment says so, rather than implying every `coordinate`
   record is a degradation. An `index` selector fallback is a selector-resolution concern, not an
   actuation one, and it stays open. The line  stays unticked, with its remaining half
   named.

   [`docs/architecture.md`](../../docs/architecture.md) and its Japanese mirror
   `docs/ja/architecture.md` list `actionLog` among the instant evidence kinds. Check and update both
   to match (BE-0113).

   Four more pages state, in prose, claims this item falsifies. Each is corrected with its mirror:
   - [`docs/reporting.md`](../../docs/reporting.md) and `docs/ja/reporting.md` call
     `steps[].duration_s` "the `actionLog`-equivalent information" (`reporting.md:69`). They also pin
     the schema version in prose twice: "it is `4` today" (`reporting.md:80`) and "`schemaVersion` is
     `4` once this block can appear" (`reporting.md:89-90`; `docs/ja/reporting.md:71`). The second is
     stated rather than as a floor, so it becomes false at 5.
   - [`docs/run-loop.md`](../../docs/run-loop.md) and `docs/ja/run-loop.md` repeat the same
     `actionLog`-equivalent claim inside their `StepOutcome` field listing (`run-loop.md:111`). That
     listing also gains `actuations`, and `RunResult`'s listing gains `expect_actuations`.

### Machine-checkable outcome

The deterministic suite, with no device and no model, checks five properties:

- A `FakeDriver` scenario's `StepOutcome` carries an `Actuation` whose `points` equal the resolved
  element's frame center, computed independently in the test from the seeded frame.
- A two-step scenario keeps each step's actuations on its own outcome.
- A step's recorded `target` is an identifier and never a label, on an element seeded with both.
- The manifest round-trip reconstructs equal records.
- The read-count test's pinned zero stays zero.

`make check` is the judge. The record never enters a verdict, because no assertion, wait, or extract
reads it (prime directive 1).

## Alternatives considered

**Return the record from each `Driver` actuator.** Changing `tap(sel) -> Actuation` would make the
record a type-level guarantee rather than an opt-in protocol, so a backend could not forget to
produce one. Rejected for blast radius against benefit. `Driver` has a dozen actuating
methods. The action handlers, the crawl, `record`'s replay, the alert guard, and the conformance
suite call every one of them, and each call site would have to accept and discard a value it does
not want. The opt-in protocol reaches the same coverage in this item, because every shipped backend
implements it in unit 2. It avoids that churn, and it matches how the driver surface has absorbed
its last four optional capabilities.

**Log the coordinates to the operational log instead.** The Android driver's two `logger.debug`
lines already do this, and their inadequacy is the motivation: a log line is not evidence. It is
absent unless the level was raised before the run, it does not travel with the run directory, and it
cannot be rendered alongside the screenshot and element tree it needs to be compared against.
Structured operational logging
([BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md)) is also explicitly a
`serve`-mode concern, kept off the deterministic `run` path. It draws the same line:
evidence is the test subject's trace, and the operational log is the tool's.

**Write an `actionLog.json` artifact per step.** The alternative to the manifest is a file per step,
like `elements.json`. Rejected because the record is small, structured, and already has a home. The
manifest is the run's single source of truth, `asdict` carries the records into it for free, and
`DESIGN.md` §9 already specifies `actionLog` as manifest-inherent rather than a file. A separate
file would add a write per step, a path to resolve in every consumer, and a second place for a step's
truth to live.

**Reconstruct the coordinate above the driver, from the element tree.** The orchestrator could
resolve the selector itself and record the frame center it computes. Rejected because the result
would be a guess presented as a measurement. It would state the coordinate the orchestrator *would*
have used. On iOS that is not what XCUITest touched. On Android it is not what the device picked when
the resident endpoint served the gesture. On either platform it is not what a stale tree caused a
real gesture to miss, which is the failure the record exists to make visible. The approach
would also cost a device read per step, breaking the invariant unit 6 pins.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — `Actuation` / `ActuationLog` / `ActuationReporter` in `bajutsu/drivers/actuation.py`,
      added to **both** `pyproject.toml` import contracts that name `bajutsu.drivers.base`.
- [x] Unit 2 — every backend records the primitive it performed, at the site that already holds the
      value.
- [x] Unit 3 — the step loop drains the log onto `StepOutcome.actuations`, and the scenario-level
      `expect` guard onto `RunResult.expect_actuations`.
- [x] Unit 4 — `manifest.json` carries the records and the report loader reads them back.
- [x] Unit 5 — the HTML report (steps and the expects block) and the `bajutsu trace` timeline surface
      them.
- [x] Unit 6 — deterministic coverage for geometry, attribution, channel, redaction, read count, and
      round trip.
- [x] Unit 7 — `docs/evidence.md`, `DESIGN.md`, `docs/architecture.md`, `docs/reporting.md`, and
      `docs/run-loop.md` (each with its Japanese mirror) describe what is recorded.

## References

- [`docs/evidence.md`](../../docs/evidence.md) — the evidence subsystem this item completes the
  `actionLog` kind of.
- [BE-0341](../BE-0341-pre-action-evidence-capture/BE-0341-pre-action-evidence-capture.md) — the
  sibling item that moved each step's baseline screenshot and element tree ahead of the step's
  action, so the screen a step acted on is what the report shows. This item adds what the step then
  did to that screen.
- [BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md) ·
  [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md) — the measured
  stale-tree gestures whose diagnosis this record supports.
- [BE-0339](../BE-0339-adb-device-side-actuation/BE-0339-adb-device-side-actuation.md) — the Android
  device-side actuation channel the record distinguishes from the host coordinate fallback.
- [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md) — the XCUITest
  stale-handle re-resolve whose final attempt is the one the record names.
- [BE-0068](../BE-0068-regenerable-reports/BE-0068-regenerable-reports.md) — the versioned render
  model whose compatibility contract shapes the schema bump and the `str`-over-`Literal` choice.
- [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md) — the
  proposal to route every write into a run directory through one redacting sink, which would close the
  unredacted-manifest gap rule three reasons from. Rule three holds either way: a record that never
  carries a secret-bearing string needs no boundary to protect it, and a boundary is no reason to start
  carrying one.
- [BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md) — the operational logging
  contract that draws the evidence-versus-tool-log line this item stays on the evidence side of.
