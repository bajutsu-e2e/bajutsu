**English** · [日本語](BE-0356-xcuitest-picker-wheel-adjust-ja.md)

# BE-0356 — Add a deterministic pickerWheel value-setting step for XCUITest

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0356](BE-0356-xcuitest-picker-wheel-adjust.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0356") |
| Implementing PR | [#1648](https://github.com/bajutsu-e2e/bajutsu/pull/1648) |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

The resident XCUITest runner already recognizes a `UIPickerView`/`UIDatePicker` wheel as an
element: `typeName(_:)` classifies it as `pickerWheel`
(`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`). No step can set its value, however.
This item adds `setPickerValue`, a step that moves a picker wheel to an exact value the way
XCUITest's own `XCUIElement.adjust(toPickerWheelValue:)` does. The step closes the one gap left in
wheel-style picker automation on the XCUITest backend.

```yaml
- setPickerValue: { sel: { id: some.picker.identifier }, value: "大学" }
```

## Motivation

A form that presents a `UIPickerView` or a wheel-style `UIDatePicker` behind a text field's
`inputView` is common on iOS. A third-party form library's `.picker(PickerContext)` style renders
a plain `UIPickerView`. A year-month field is often a `UIDatePicker` switched to a wheel-only
mode through an undocumented raw `mode` value. Neither is exotic. Both are ordinary iOS form
patterns that a scenario needs to fill and then verify, the same way it already fills a text
field and asserts on `value`.

None of the steps a scenario can already use sets a wheel to an exact value.

- `tap` addresses a resolved handle, not a coordinate, so it cannot land on a specific row. A
  wheel's individual values are not separately addressable elements.
- `swipe`, `drag`, and `scroll` are the coordinate-driven drag steps in the scenario
  domain-specific language (DSL). Each is a bounded or directional drag. A drag can spin a wheel
  roughly toward a value, but nothing about it guarantees that the wheel stops there.
  Asserting the resulting value would depend on the drag distance matching the row height by
  chance.
- `tapPoint`, the DSL's other coordinate-driven step, fares no better. A single tap on a fixed
  point can hit only the value the wheel already shows, and never moves the wheel toward another.

This is the coordinate-drag hazard that prime directive 2 already rules out for every other step:
an ambiguous or approximate action must fail rather than land on a best-effort result. A
wheel-value scenario has no other step to fall back on today.

XCUITest itself solves this with `XCUIElement.adjust(toPickerWheelValue:)`, called directly on the
resolved wheel element rather than on a coordinate. The call is handle-based, as `tap` is in this
codebase's implementation model: `XcuitestDriver.tap` resolves a selector to a handle, then
actuates on that handle (`bajutsu/drivers/xcuitest.py`). It is not coordinate-based, as `swipe`
and `drag` are. `setPickerValue` follows the shape of `tap` for this reason, and closes a gap that
none of the existing steps can reach.

## Detailed design

**Schema.** `Step` (`bajutsu/scenario/models/steps.py`) is not a discriminated union. Every
action is an optional field, and `_one_action` enforces that exactly one is set. Two additions are
the only schema change needed:

- `SetPickerValue(sel: Selector, value: str)` in `bajutsu/scenario/models/actions.py`, following
  the existing `Drag` and `Swipe` models.
- One new `set_picker_value` field on `Step`.

Five places derive from that field automatically, with no further change:

- `_STEP_ACTIONS` (from `Step.model_fields`)
- `STEP_ACTIONS` (`bajutsu/scenario/models/__init__.py`)
- the "exactly one action" validation
- `_RUNTIME_ACTIONS` (`bajutsu/orchestrator/actions/_registry.py`)
- `_ACTION_KEYS` (`bajutsu/analysis/impact.py`)

**Addressing a multi-component wheel.** A `UIDatePicker` in wheel-only mode lays out its year and
month as two independent components, each its own `pickerWheel`-typed element. The runner's
snapshot walk (`XcuitestElementProvider.swift`, the `SnapshotNode` tree) already recurses into an
element's children generically. Both components already surface as separate nodes today,
alongside the parent `UIDatePicker`.

`Selector` (`bajutsu/scenario/models/selector.py`) already carries `within`, `traits`, and `index`
for this kind of disambiguation. `handleSystemAlert` uses the same `index` discipline to pick one
button among several. In `setPickerValue`, `sel` always addresses one such component, so `value`
stays a plain string. For example, `within: { id: birthdate.picker }, traits: [pickerWheel],
index: 0` selects the year wheel, and `index: 1` selects the month wheel. `value: "2016年"` and
`value: "4月"` are two separate steps.

On device, the `within` container must enclose the components, because iOS reports each wheel
taller than the picker holding it. So the identifier belongs on a surrounding container,
not on the `UIDatePicker` itself. This reuses the addressing mechanism that every other
selector-based step already has, instead of growing a second, `setPickerValue`-specific way to
name a component.

**Runtime dispatch.** `bajutsu/orchestrator/actions/handlers/gestures.py` gains an
`@_handler("set_picker_value")` function, following the shape of `_do_tap`. The function calls
`driver.set_picker_value(step.set_picker_value.sel, step.set_picker_value.value)`. Without it,
`_do_action` in `_registry.py` raises `AssertionError("unhandled action")` the moment the field is
set. This handler is the one runtime piece that the schema addition does not wire up on its own.

**Driver protocol.** `Driver` (`bajutsu/drivers/base.py`) gains
`set_picker_value(self, sel: Selector, value: str) -> None`, implemented by every concrete
`Driver`.

`XcuitestDriver` implements it the way it implements `tap`. `_resolve_handle` resolves `sel` to a
handle, then `_actuate` sends the handle to the runner with the same stale-handle retry loop that
`tap` uses.

`adb.py` and `playwright.py` implement it by raising `base.UnsupportedAction`. This is the shape
`select_option` uses on the two backends that lack a native `<select>` (`xcuitest.py` and
`adb.py`), mirrored. A picker wheel is an iOS-only UI control, the reverse of `select_option`'s
web-only one. Android and web are unsupported by construction rather than by omission.

`webview.py`'s `WebContextDriver` also raises `UnsupportedAction`, matching its own
`select_option`, because a `WKWebView` DOM has no picker wheel either.

`xcuitest_live.py`'s `XcuitestLiveDriver` is the W3C WebDriver route that BE-0238 added for a
device-cloud session. It is a second, independent implementer of the XCUITest family alongside the
resident-runner `XcuitestDriver`. It raises `UnsupportedAction` for this item. The platform is the
same, so a live-route implementation may be possible through Appium's XCUITest driver. That is a
separate build-time evaluation, which this item does not commit to.

`fake.py` needs more than the shape of `select_option` here. `FakeDriver.select_option` checks
only that `sel` resolves uniquely, and never validates the option itself. The absent-value
behavior that this item must unit-test has no equivalent in `select_option`'s own test suite:
`tests/test_select_option.py` covers parse, dispatch, and fake recording, and has no absent-option
case.

Absent-value detection is a central part of this item. It is the one behavior for which the Swift
runner needs a value-readback workaround, because `adjust(toPickerWheelValue:)` cannot report it
on its own (see below). It needs its own fast unit-test path, not merely an on-device
one.

A sibling wheel component (the year versus the month wheel of a `UIDatePicker`) has no identifier
of its own. *Addressing a multi-component wheel* above disambiguates the two purely by `within`,
`traits`, and `index` for this reason, so a seed keyed by identifier could not tell them apart.
`fake.py` instead gains a seeded `picker_wheel_options: dict[int, list[str]]`. It is keyed by
`id()` of the specific `Element` object in the fixture's `screen` list. This is the same
separately-seeded shape that `system_alert_buttons` uses for `handle_system_alert`, but keyed by
object identity rather than by identifier, so two identifier-less sibling elements each keep their
own options.

`FakeDriver.set_picker_value` resolves `sel` uniquely. Zero matches raise `ElementNotFound`, and
more than one raises `AmbiguousSelector`, the discipline that every other action uses. It then
checks `value` against the resolved element's seeded options. A match records the actuation. A
miss raises `ElementNotFound`, naming the value that was not found.

A resolved wheel carrying *no* seed entry is a fixture mistake rather than an absent value. It
raises a distinct, loud error instead of falling through to `ElementNotFound`. Otherwise a stale
key (the copies `query()` returns in scrollable mode, or a `react` callback that rebuilds
`screen`) would let the absent-value test pass for the wrong reason. The handler-dispatch,
preflight, and absent-value paths thus stay testable without a Simulator, including the
multi-component case.

**Capability and preflight.** `Capability` (`bajutsu/drivers/base.py`) gains `PICKER_WHEEL`. The
resident-runner `XcuitestDriver.CAPABILITIES` and `FakeDriver.CAPABILITIES` frozensets advertise
it, and no other backend does. `_REQUIREMENTS` in `capability_preflight.py` gains one entry for
it, following the shape of the `HANDLE_SYSTEM_ALERT` entry. A scenario that uses `setPickerValue`
on a backend that does not advertise `PICKER_WHEEL` fails at preflight, before any device work
starts, and the failure names the step's location in the scenario.

**Swift runner.** `Router.swift` (`BajutsuKit/Sources/BajutsuRunner/`) gains a
`("POST", "/setPickerValue")` route and a `handleSetPickerValue` function. The function resolves
the request's handle through `SnapshotStore`, as `handleTap` does. It then calls a new
`ElementProviding.setPickerValue(backingElement:value:)` method. `XcuitestElementProvider`
(`BajutsuKit/Runner/Sources/`) implements the method by resolving the live element through
`liveElement(for:)` and calling `el.adjust(toPickerWheelValue: value)`. This is the same
live-element resolution that `tap(backingElement:taps:duration:)` already uses.

**Detecting a value the wheel does not have.** `adjust(toPickerWheelValue:)` never throws and
returns nothing. When the requested string never becomes the wheel's on-screen value, XCTest
records a soft `XCTIssue` rather than raising a failure. `RunnerUITest` sets
`continueAfterFailure = true` (`BajutsuKit/Runner/Sources/RunnerUITest.swift`) precisely to
tolerate a soft issue like this without tearing down the resident runner. The call site
cannot rely on an exception to notice that the value never landed.

The provider instead reads the wheel's resulting value back after the call and compares it with
the requested string. It samples up to a small fixed cap rather than reading once, so a wheel
still settling its deceleration is not reported as a value the wheel does not have. This is the
bounded-sample discipline that `actuateUntilStateChanges`
(`BajutsuKit/Sources/BajutsuRunner/GestureRetry.swift`) already applies to a landing observable in
`el.value`.

When the values do not match, the provider returns a new `TapResult` case, a sibling of the
existing `.notFound` and `.notHittable` (`ElementProviding.swift`). It is a case of its own
because the existing message of `.notFound` ("no actuatable element for …", named after the
selector) would misreport a resolved, live element whose value did not match. `Router`
maps the new case to a distinct response status.

Today `_actuate` (`bajutsu/drivers/xcuitest.py`) dispatches on status with a fixed check over
`_OK`, `_STALE`, `_NOT_FOUND`, and `_NOT_HITTABLE`. *Any other status already falls to a catch-all
that raises `XcuitestChannelError`*, which is an infra error and not a `SelectorError`.
`set_picker_value` needs its own branch there, alongside the existing four. The branch
raises `base.ElementNotFound`, naming the value that did not land. This mirrors the precedent of
`select_option` for an absent value: `select_option` in `playwright.py` re-raises an absent
`<select>` option as `ElementNotFound`, a `SelectorError`. The run loop's existing
selector-failure handling covers the new case once that branch exists.

**The `datePicker` classification gap is unaffected.** `docs/selectors.md` already documents that
the container element of a `UIDatePicker` falls to `other` in `typeName(_:)`, since the switch has
no `.datePicker` case. `setPickerValue` never depends on that classification. It addresses the
individual `pickerWheel` component elements that a wheel-style `UIDatePicker` exposes as children,
not the parent container. The step operates a `UIDatePicker` wheel today as it operates a
plain `UIPickerView` wheel, so the classification gap on the parent element does not need closing
for this step to work. `docs/selectors.md` gains a note pointing to `setPickerValue` as how a
scenario still sets a `datePicker`'s value despite the parent falling to `other`.

**codegen.** `_emit_step` in `bajutsu/codegen/xcuitest.py` gains a case that emits
`element.adjust(toPickerWheelValue: "...")` for `step.set_picker_value`, the same shape as its
existing `handle_system_alert` case. The `adb` and `playwright` emitters need no new code. An
unrecognized step already falls through to the existing `// TODO: unsupported step` stub, the same
fallback that `selectOption` already hits on those two generators today.

**Documentation.**

- `docs/scenarios.md` gains a row in the step grammar table and a `### setPickerValue` section,
  with the YAML example above and the "why not a coordinate drag" reasoning from *Motivation*. The
  section follows the existing shape of `### drag`. `docs/ja/scenarios.md` gets the Japanese
  mirror.
- The capability table in `docs/drivers.md` gains a `pickerWheel` row (xcuitest only).
  `docs/ja/drivers.md` gets the mirror.
- `docs/selectors.md` and `docs/ja/selectors.md` get the `datePicker` note described above.

**Tests.** Four tests follow the parse, dispatch, and fake-recording shape that
`tests/test_select_option.py` already uses, extended with the seeded `picker_wheel_options` above:

- dispatching `setPickerValue` with a `value` that the fake wheel's seeded options hold reaches
  `driver.set_picker_value` and records the actuation;
- dispatching it with a `value` that the fake wheel's seeded options do not hold fails with
  `ElementNotFound`;
- running a scenario that uses `setPickerValue` against a backend without `Capability.PICKER_WHEEL`
  (`adb` or `playwright`) fails at preflight, before any actuation is attempted;
- a `PICKER_WHEEL` case in `tests/driver_conformance.py`, following
  `test_select_option_capability_matches_behavior`: a backend that advertises the token must not
  raise `UnsupportedAction`, and one that does not must raise it rather than silently no-op'ing.

## Alternatives considered

- **A `component` / `values` payload addressing a multi-component picker as a whole
  (`value: { component: 0, value: "2016年" }` or `values: ["2016年", "4月"]`).** Rejected.
  `Selector` already addresses one component through `within`, `traits`, and `index`, the
  mechanism every other selector-based step uses. A second, `setPickerValue`-specific way to name
  a component would duplicate that mechanism for no addressing power that the existing fields lack.
- **A coordinate-drag step that stops once the wheel's value matches, mirroring the bounded loop of
  `scroll`.** Rejected. A picker wheel's row height and scroll physics are not queryable, so
  nothing bounds how far a drag must travel to land on a given value. `swipe` and `drag` already
  carry this non-determinism, which is why they cannot serve here (*Motivation*).
  `adjust(toPickerWheelValue:)` is deterministic because it acts on the element that XCTest
  already resolved, not on a coordinate.
- **Reuse `tap` with a `value` modifier instead of a new step name.** Rejected. `tap` means the
  same thing on every backend and every element kind: activate the resolved element. Branching
  that meaning on whether the resolved element happens to be a picker wheel would make the
  contract of one step depend on the backend and the element type. The precedent of
  `select_option` is instead a distinct step name for a distinct control.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Schema: `SetPickerValue` action model, `Step.set_picker_value` field
- [x] Runtime: `gestures.py` handler, `Driver.set_picker_value` (xcuitest / adb / playwright /
      webview / xcuitest_live / fake), `Capability.PICKER_WHEEL` + preflight requirement
- [x] Swift runner: `/setPickerValue` route, `ElementProviding.setPickerValue`,
      `XcuitestElementProvider` implementation with value-readback detection of an absent option,
      the matching `_actuate` status branch (`bajutsu/drivers/xcuitest.py`)
- [x] codegen: xcuitest emitter case
- [x] Docs: `docs/scenarios.md`, `docs/drivers.md`, `docs/selectors.md` (both languages)
- [x] Tests: value found, value absent (`ElementNotFound`), missing-capability preflight failure,
      the multi-component (year/month) case, the `PICKER_WHEEL` case in `tests/driver_conformance.py`

## References

- [DESIGN.md §5](../../DESIGN.md) — the `Driver` abstraction, `Element` / `Selector` shape, and the
  selector-resolution determinism contract this step stays inside.
- [BE-0191 (`selectOption`, Unit 5)](../BE-0191-pluggable-theme-system-serve-ui/BE-0191-pluggable-theme-system-serve-ui.md)
  — the closest existing precedent for a platform-specific action: one DSL action, one `Driver`
  protocol method, an `UnsupportedAction` raise on the backends that lack it.
- [BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps.md) — the shape this item's
  *Detailed design* follows for wiring a new step through the schema, the handler registry, the
  `Driver` protocol, and `fake.py`, and the precedent for leaving a per-backend actuation wrinkle
  to build-time triage.
- [`docs/selectors.md`](../../docs/selectors.md) — documents the `datePicker` → `other` `typeName`
  classification trade-off this item's addressing model works around without needing to close it.
- `bajutsu/drivers/xcuitest.py` (`XcuitestDriver.tap`, `_resolve_handle`, `_actuate`) — the
  handle-based actuation flow `set_picker_value` follows.
- `BajutsuKit/Sources/BajutsuRunner/Router.swift` (`handleTap`) and
  `BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`
  (`tap(backingElement:taps:duration:)`) — the Swift-side flow `handleSetPickerValue` /
  `setPickerValue(backingElement:value:)` follow.
