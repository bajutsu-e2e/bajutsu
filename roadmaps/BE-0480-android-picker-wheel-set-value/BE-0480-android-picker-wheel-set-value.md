**English** · [日本語](BE-0480-android-picker-wheel-set-value-ja.md)

# BE-0480 — Set an Android NumberPicker wheel to an exact value with setPickerValue

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0480](BE-0480-android-picker-wheel-set-value.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0480") |
| Implementing PR | [#2412](https://github.com/bajutsu-e2e/bajutsu/pull/2412) (units 1–6) |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

The `setPickerValue` step moves a wheel-style picker to the row with a named value. Today it runs on
iOS alone: [BE-0356](../BE-0356-xcuitest-picker-wheel-adjust/BE-0356-xcuitest-picker-wheel-adjust.md)
built it on XCUITest's `adjust(toPickerWheelValue:)`, and the adb backend raises
`UnsupportedAction` for it. This item brings the same step to Android. It drives the platform's
wheel control, `android.widget.NumberPicker`, and the spinner-mode `DatePicker` and `TimePicker`
built from it. The scenario syntax stays as it is:

```yaml
- setPickerValue:
    sel: { within: { id: form.birthdate }, traits: [pickerWheel], index: 2 }
    value: "2016"
```

The adb backend's resident UI Automator server gains a `/setPickerValue` endpoint. The endpoint
steps the resolved wheel one row at a time through accessibility scroll actions, reads the wheel's
value back after each step, and stops on the named row. No coordinate crosses the wire, so the step
stays as deterministic on Android as it is on iOS.

## Motivation

A wheel picker is an ordinary Android form control. A `NumberPicker` picks a count or a category,
and a `DatePicker` or `TimePicker` in spinner mode lays out its fields as two or three
`NumberPicker` wheels. A scenario that fills such a form has to set the wheel before it can assert
on what the app did with the value.

No step an Android scenario can use today sets a wheel to an exact value. The platform parity table
in `docs/platform-parity.md` lists `pickerWheel` as a gap on Android, and a scenario that uses
`setPickerValue` fails preflight on the adb backend before any device work starts. The remaining
steps cannot stand in for it, for the reason BE-0356 gives on iOS. `swipe`, `drag`, and `scroll`
are coordinate drags: they spin a wheel toward a value, but nothing makes the wheel stop on that
value. A `tap` on the neighbouring row moves the wheel by one row. Repeating it needs one gesture and
one wait per row. Each of those taps also travels the coordinate path that the resident server's
`/act` endpoint was built to retire. Prime directive 2 rules out an approximate landing, so an
Android author has no deterministic way to fill a wheel field at all.

The gap also splits a shared scenario. A cross-platform form scenario has to drop its wheel steps,
or keep two copies, because the iOS selector `{ within, traits: [pickerWheel], index }` addresses
nothing on Android. The adb backend maps a `NumberPicker` node to the trait `numberPicker`, derived
from its class name, and never to `pickerWheel`.

The change has shipped once three things hold:

- The two showcase scenarios in `demos/showcase/scenarios/picker_wheel.yaml` pass on the adb backend in the `android-e2e.yml` smoke job, with selectors that also resolve on iOS.
- A scenario that uses `setPickerValue` no longer fails preflight on adb.
- The `pickerWheel` row in `docs/platform-parity.md` reads ✅ for Android.

## Detailed design

**Scope.** The step covers `android.widget.NumberPicker` and every control built from it: the
spinner-mode `DatePicker` and `TimePicker`, and a Jetpack Compose screen that hosts a
`NumberPicker` through `AndroidView`. A wheel drawn by Compose itself is out of scope. Compose ships
no standard wheel, so each app's custom wheel exposes a different accessibility shape. Supporting
those wheels would need a per-app convention, which prime directive 3 keeps out of the driver. The
scenario schema, the `Step` model, the runtime handler, and the `FakeDriver` that BE-0356 added stay
unchanged. This item changes only the adb backend, the resident server, the Android showcase apps,
and the documents that describe them.

**Trait mapping.** `_traits` in `bajutsu/common/drivers/adb/_functions.py` derives a node's first
trait from its widget class, so a `NumberPicker` node carries `numberPicker` today. The function
gains one rule: a node whose class is `android.widget.NumberPicker` also carries `pickerWheel`.
The token becomes a new `Trait.PICKER_WHEEL` in `bajutsu/common/drivers/base/trait.py`, so the shared
name has one definition for every backend to map onto and for the conformance suite to pin. The
`Trait.SECURE_TEXT_FIELD` member is the precedent (BE-0331). The class-derived `numberPicker` stays, so an existing selector keeps resolving. The shared token means a
scenario addresses a wheel with the same selector on both platforms. A spinner-mode `DatePicker`
dumps as a container that holds its `NumberPicker` children. `within` scopes by frame containment,
and `traits` and `index` pick one wheel among them, which is the addressing BE-0356 already uses on
iOS. The component order follows the locale, as it does on iOS: under `en_US` it is month, day, year.

**The resident server endpoint.** The resident server in
`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt`
gains a `/setPickerValue` route next to `/act` and `/type`. The request carries the same identity
fields, `index`, `count`, `since`, and budget that `/act` takes, plus `value`. The handler starts the
way `respondAct` does. It validates every field without defaulting any of them, waits for idle and
for the postdate mark, and reads the matching bounds. It answers the existing stale status when its
own count differs from the host's. From that point on, the handler works on the accessibility node,
not on a coordinate:

1. The handler finds the `AccessibilityNodeInfo` whose screen bounds equal `matches[index]`, and whose
   class is `android.widget.NumberPicker`. If the node is anything else, the handler answers a new
   *not-a-picker-wheel* status.
2. The handler reads the wheel's current value from the text of its child input node,
   `android:id/numberpicker_input`. That child is the row the wheel shows as selected.
3. Before it walks, the handler picks a direction. If the current value and the requested value both
   parse as integers after trimming whitespace, it walks first toward the requested value: forward
   when that value is larger, backward when it is smaller. Otherwise it walks forward first. A year
   wheel thus reaches its row by the shortest walk. A wrapping hour or minute wheel may go the long
   way round, which the row cap still bounds. While the value
   differs from the requested one, the handler performs `ACTION_SCROLL_FORWARD` (or
   `ACTION_SCROLL_BACKWARD` for a backward walk) on the wheel node, waits for idle, and reads the value
   again. `NumberPicker`'s accessibility provider maps each action to a one-row step, the same change
   a user's single flick of one row makes.
4. The first walk can end at an edge without a match. A non-wrapping wheel reaches its last or first
   row: the action returns `false`, or the value stays unchanged across a confirming re-read.
   A single unchanged read can be a row whose text has not republished yet, so it never ends a pass
   alone. The handler then walks the other direction. This keeps correctness for numeric rows whose order
   is not monotonic. A wrapping wheel returns to the value it started from, and that ends the walk.
   Either case without a match means the wheel holds no such row, so the handler answers a new
   *picker-value-absent* status.
5. Two budgets bound the walk, so a wheel that keeps changing its own values cannot hold the request
   forever. A fixed row cap, `MAX_PICKER_ROWS = 400`, bounds the total rows stepped across both
   passes. Reaching it answers a new *picker-walk-exhausted* status, and the reply names the cap. Each row also has
   a per-row budget, `PICKER_ROW_BUDGET_MS = 300`: one accessibility scroll plus the idle wait for
   `NumberPicker`'s one-row animation. That figure is a starting value. The first on-device run measures it and adjusts it. The handler
   also stops at a walk deadline of `MAX_PICKER_ROWS × PICKER_ROW_BUDGET_MS`, counted from the first
   step, and answers *picker-walk-exhausted* naming the deadline. A slow row thus never pushes the
   reply past the host timeout.

A match answers `200` through `respondLanded`, the same reply `/act` sends. The host therefore gets
the post-actuation tree and the actuation mark without an extra read. The handler never types into
the input node. Avoiding `ACTION_SET_TEXT` is the design choice the *Alternatives considered*
section explains.

**Host driver.** `bajutsu/common/backend_cli/adb_resident/_functions.py` gains a
`set_picker_value(...)` client next to `act(...)`. The client sends the same identity fields and maps
each reply status to an outcome. `AdbDriver.set_picker_value` in
`bajutsu/common/drivers/adb/adb_driver.py` replaces today's `UnsupportedAction` stub. The method
resolves `sel` the way `_device_act` does for `tap`: `_resolve` on the settled tree, which applies
`resolve_unique`, then `_scroll_into_view` when nothing matches. A selector that still matches
nothing raises `ElementNotFound`, and two or more matches raise `AmbiguousSelector`. The method then sends the
element's identity and ordinal. It handles each reply status as follows:

| Reply status | What `AdbDriver.set_picker_value` does |
|---|---|
| `200` | Records the actuation and seeds the next read from the returned tree, as `_device_act` does for a tap. |
| stale | Re-resolves and retries with the same bounded loop `_device_act` already runs for a stale `/act` reply. When the loop runs out of attempts, or the resolved element has no recorded identity to send or outlived the read its ordinal was counted from, raises `ElementNotFound` naming the selector, because there is no coordinate path to fall back to. |
| not-a-picker-wheel | Raises `ElementNotFound`, naming the selector and stating that the element is not a `NumberPicker`. |
| picker-value-absent | Raises `ElementNotFound`, naming the value that never appeared. BE-0356 maps the same case on iOS this way. |
| picker-walk-exhausted | Raises `ElementNotFound`, stating that the walk hit its row cap or deadline before the value appeared. The message differs from the absent-value one, so a budget that needs retuning never reads as a missing row. |
| `404` from an older server | Raises `UnsupportedAction`, stating that the deployed resident server predates `/setPickerValue` and needs a rebuild. The driver latches the absence of `/setPickerValue` alone for the lease, in its own flag, the way `AdbActUnsupported` already handles a missing `/act`. The `/act` path keeps serving taps. |
| reply lost after sending | Raises `ElementNotFound`, stating that the server may have moved the wheel but did not confirm the row. A tap's `_device_act` continues as if landed in this case; this step cannot, because a half-walked wheel shows the wrong value. |

The `set_picker_value` client does not reuse `ACTION_TIMEOUT_S`. Its timeout is
`MAX_PICKER_ROWS × PICKER_ROW_BUDGET_MS` plus `ACTION_TIMEOUT_S`, about 130 seconds with the starting
values. The constant is defined next to `ACTION_TIMEOUT_S` in
`bajutsu/common/backend_cli/adb_resident/budgets.py`. The server answers *picker-walk-exhausted* at the
row cap or deadline before the host's timeout can fire. So a host timeout means the channel failed, not that the
value is missing.

The step has no coordinate fallback. When the resident channel is unavailable, the method raises
`UnsupportedAction` and does not drag the wheel. A coordinate drag cannot promise to land on the
row, which is the hazard this step exists to avoid.

**Capability and preflight.** `AdbDriver.CAPABILITIES` gains `Capability.PICKER_WHEEL`. The
requirement entry in `bajutsu/common/capability/capability_preflight.py` keeps its predicate, and
its description changes from "iOS XCUITest only" to name both backends. The `Driver.set_picker_value`
comment in `bajutsu/common/drivers/base/driver.py` and the `setPickerValue` bullet in the module
docstring of `capability_preflight.py` drop the claim that a picker wheel is iOS-only and that only XCUITest can land on a named row. The web backend, the
`WebContextDriver`, and the `XcuitestLiveDriver` keep raising `UnsupportedAction`, and preflight
keeps rejecting the step on them.

**Code generation.** The UI Automator generator in `bajutsu/codegen/uiautomator.py` emits a labeled
`// TODO` for `setPickerValue`, naming the selector and the value. UI Automator's `UiObject2` exposes
no accessibility action, so a faithful translation needs a helper that walks the wheel the way the
endpoint does. That helper is a separate piece of work, outside this item.

**Showcase.** Both Android showcase apps gain a picker screen behind the `SHOWCASE_PICKERS` launch
environment variable, as the iOS app has. The Views app under `demos/showcase/android/views/` uses a
native `NumberPicker` and a spinner-mode `DatePicker`. The Compose app under
`demos/showcase/android/compose/` hosts the same two controls through `AndroidView`, which also
proves the Compose path that *Scope* names. The Compose screen uses the identifiers the iOS screen uses: `form.school`, `form.school.value`,
`form.birthdate`, and `form.birthdate.value`. The Views screen uses their underscore forms, such as
`form_school`, because an Android resource name cannot hold a dot. Each app mirrors a
picked value into its `.value` element's `content-desc`, which the adb backend reads as `value`.
Each `id` in `picker_wheel.yaml` lists both forms, as every scenario on the Views lane does since
BE-0221 (`id: [form.school, form_school]`). Its `swiftui` tag keeps the UIKit and Flutter iOS lanes
out. `picker_wheel` joins `E2E_SCENARIOS` and `E2E_VIEWS_SCENARIOS` in
`demos/showcase/android/Makefile`, the lists the `android-e2e.yml` smoke job runs. The file's header
comment drops its iOS-only claim and narrows its SwiftUI-only one to the iOS lanes, which keep
excluding the `swiftui` tag. The iOS scenario's month value is
`"May"`, the label the iOS wheel renders under `en_US`. The first on-device run checks the row labels
that the Android spinner renders under the same locale. If a label differs, the Android lane gets its
own scenario file in place of a shared one.

**Documentation.** Each English page below changes together with its `docs/ja/` mirror.

- `docs/scenarios.md`: the `setPickerValue` row and section drop "iOS (XCUITest) only" and describe
  the Android mechanism. The section also states that a Compose-drawn wheel is out of scope. The closing paragraph that says
  a picker wheel has no counterpart on Android is rewritten.
- `docs/drivers.md`: the capability table's `pickerWheel` row adds `adb` and drops "(iOS only)", and
  the preflight paragraph that calls a picker wheel a native iOS control names both backends. The
  `APP_CONTEXT` paragraph that groups `PICKER_WHEEL` with the XCUITest-only capabilities drops it.
- `docs/platform-parity.md`: the `pickerWheel` row reads ✅ for Android, with a note on the
  Compose limit.
- `docs/selectors.md`: the adb trait mapping notes that a `NumberPicker` node carries both
  `numberPicker` and `pickerWheel`.
- `docs/architecture.md`: the `setPickerValue` entry names the resident server endpoint, next to
  the XCUITest mechanism, and the on-device coverage line names the `android-e2e.yml` smoke job next to `ios-e2e.yml`. The line stating that the XCUITest backend and `FakeDriver` alone declare
  `PICKER_WHEEL` adds the adb backend.

**Tests.** The deterministic gate covers the host side without an emulator:

- A `_traits` test checks that a `NumberPicker` node carries both `numberPicker` and `pickerWheel`,
  and that no other class gains `pickerWheel`. A conformance case pins `Trait.PICKER_WHEEL` as
  the token every wheel-capable backend maps its wheel to.
- `AdbDriver.set_picker_value` tests stub the resident channel and cover each row of the reply
  table above: a match, a stale reply followed by a successful retry, an exhausted stale loop, a
  non-wheel element, an absent value, an exhausted walk, a `404` from an older server, a lost reply, and an unavailable
  channel.
- A preflight test checks that a `setPickerValue` scenario passes preflight on adb and still fails
  on the web backend.
- A case in `tests/test_adb_resident_budgets.py` reads `MAX_PICKER_ROWS` and `PICKER_ROW_BUDGET_MS` from
  the server source. It checks that `WAIT_BUDGET_MS`, the walk deadline, `ACT_PUBLISH_BUDGET_MS`, and
  `ACT_TREE_BUDGET_MS` together fit inside the `set_picker_value` timeout with a second to spare.
  Retuning the row budget on one end alone then fails the gate.
- The `PICKER_WHEEL` case in `tests/driver_conformance.py` covers the adb driver once the driver
  advertises the token.

The on-device path, from the endpoint through the showcase screen, runs in `android-e2e.yml`. That
job is not part of `make check`.

## Alternatives considered

| Option | What it would do | Why this item does not take it |
|---|---|---|
| Write the value into the wheel's input field | Set `android:id/numberpicker_input` with `ACTION_SET_TEXT`, then move focus away so the wheel commits the value. One request regardless of distance. | The commit depends on the wheel's input filter and on focus moving at the right time. A wheel with `descendantFocusability` set to block its children cannot be edited at all. The result would depend on how each app configured the control. |
| Tap the neighbouring row from the host | Use the existing `tap` on the row above or below the selected one, once per row, until the value matches. No server change. | Each row costs a full gesture round trip and an animation wait, which makes it the slowest option. Each tap also lands through a coordinate, the path the resident `/act` endpoint was built to retire. |
| Always walk both directions with no direction hint | Walk forward to an edge, then backward, whatever the values are. Simplest server logic. | A spinner `DatePicker`'s year wheel spans about 200 rows by default. Setting an earlier year walks forward to the last year and back, which can take over a minute. The direction hint makes a non-wrapping numeric wheel walk the shortest way, and the two-pass walk remains the fallback for text rows. |
| Include Compose-drawn wheels | Define an accessibility convention a Compose wheel can follow, and drive any wheel that follows it. | Compose has no standard wheel, so the convention would be Bajutsu's own invention, which the app must adopt. That is a separate design with its own trade-offs, larger than closing the `NumberPicker` gap. |
| A separate step name for Android | Add `setNumberPickerValue` and leave `setPickerValue` iOS-only. | The intent is the same on both platforms: move a wheel to a named row. A second name would split shared scenarios for no gain in expressiveness. BE-0356 picked one step name per distinct control for the same reason. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Trait mapping: a `NumberPicker` node also carries `pickerWheel`, defined as `Trait.PICKER_WHEEL`, with its unit and conformance tests
- [x] Resident server: the `/setPickerValue` endpoint with its stale, not-a-picker-wheel, picker-value-absent, and picker-walk-exhausted statuses, the confirming re-read at an edge, the numeric direction hint, and the row cap
- [x] Host driver: the channel client with its own timeout, `AdbDriver.set_picker_value`, the `PICKER_WHEEL` capability, the preflight description, the `Driver.set_picker_value` comment and the preflight docstring, their unit tests, and the budgets check against the server's walk constants
- [x] Code generation: the labeled `// TODO` in the UI Automator generator
- [x] Showcase: the picker screen in the Views and Compose apps, and `picker_wheel` in the `android-e2e.yml` smoke job (added to the Android Makefile scenario lists)
- [x] Documentation: `scenarios`, `drivers`, `platform-parity`, `selectors`, and `architecture`, in both languages

Log:

- 2026-11-11 — Units 1–6 landed together ([#2412](https://github.com/bajutsu-e2e/bajutsu/pull/2412)).
  `picker_wheel` passed on an API 34 arm64 emulator for both the Compose and Views showcase apps.
  Four deviations from the Detailed design:
  - A `NumberPicker` node carries no `resource-id`, because its accessibility provider sets none.
    The showcase screens therefore put each id on a container around the wheel. The scenario
    reaches the wheel through `within` plus `pickerWheel`, as it does on iOS.
  - The row wait polls the wheel's value every 20 ms until it changes. An idle wait never released
    in time: it needs a quiet spell longer than the wheel's whole one-row animation. With that wait,
    a 25-row walk read two unchanged rows in a row and ended as a false edge.
  - The first on-device run measured 142–1469 ms per row (median 625 ms, 38 rows). The row budget
    therefore starts at 1,500 ms, not 300 ms. The walk deadline becomes 600 s and the host timeout
    613 s. That timeout fires on a channel fault alone, since the server answers first.
  - The resident-channel wiring gained a `set_picker_value` callable on `ResidentChannel`,
    `ResidentServer`, `make_driver`, and the Android environment, which the Detailed design did not
    list. The web backend's and the WebView context's refusal messages stopped calling the step
    iOS-only.
  - The self-review pass hardened the walk beyond the design. A row that stays unchanged across two
    row budgets now fails as `picker-walk-exhausted` instead of reading as an edge, since turning
    back there could report a row the wheel then leaves. A landed row must hold for 400 ms. A wheel
    that refuses both scroll directions answers a fifth status, `picker-immovable`. The device
    matches the wheel by its whole identity and refuses two matches, and the host first refuses a
    wheel another element covers, as `tap` does.

## References

- [BE-0356](../BE-0356-xcuitest-picker-wheel-adjust/BE-0356-xcuitest-picker-wheel-adjust.md) — the iOS
  `setPickerValue` step, its schema, and its `PICKER_WHEEL` capability, which this item reuses.
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md) — the
  resident UI Automator server that gains the `/setPickerValue` endpoint.
- [BE-0476](../BE-0476-android-non-ascii-text-entry/BE-0476-android-non-ascii-text-entry.md) — the
  `/type` endpoint, an earlier case of the resident server driving a node through an accessibility action.
- [`docs/platform-parity.md`](../../docs/platform-parity.md) — the parity table whose `pickerWheel`
  row this item closes.
- [`docs/scenarios.md`](../../docs/scenarios.md#setpickervalue) — the `setPickerValue` step reference.
