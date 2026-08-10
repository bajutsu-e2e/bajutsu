**English** · [日本語](BE-XXXX-xcuitest-picker-wheel-adjust-ja.md)

# BE-XXXX — Add a deterministic pickerWheel value-setting step for XCUITest

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-xcuitest-picker-wheel-adjust.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

The resident XCUITest runner already recognizes a `UIPickerView`/`UIDatePicker` wheel as an
element — `typeName(_:)` classifies it as `pickerWheel`
(`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`) — but no step can set its value. This
item adds `setPickerValue`, a step that moves a picker wheel to an exact value the way
XCUITest's own `XCUIElement.adjust(toPickerWheelValue:)` does, closing the one gap left in
wheel-style picker automation on the XCUITest backend.

```yaml
- setPickerValue: { sel: { id: some.picker.identifier }, value: "大学" }
```

## Motivation

A form that presents a `UIPickerView` or a wheel-style `UIDatePicker` behind a text field's
`inputView` is common on iOS: a third-party form library's `.picker(PickerContext)` style renders
a plain `UIPickerView`, and a year-month field is often a `UIDatePicker` switched to a wheel-only
mode through an undocumented raw `mode` value. Neither is exotic — both are ordinary iOS
form patterns a scenario needs to fill and then verify, the same way it already fills a text
field and asserts on `value`.

None of the steps a scenario can already use sets a wheel to an exact value. `tap` addresses a
resolved handle, not a coordinate, so it cannot land on a specific row — a wheel's individual
values are not separately addressable elements. `swipe` / `drag` / `scroll` are the
coordinate-driven drag steps in the scenario domain-specific language (DSL), and each is a bounded or
directional drag: they can spin a wheel roughly toward a value, but nothing about a coordinate
drag can guarantee the wheel stops exactly there, so asserting the resulting value would depend
on the drag distance matching the row height by chance. `tapPoint`, the DSL's other
coordinate-driven step, fares no better: a single tap on a fixed point can only hit whatever
value the wheel already shows, never move it toward one it does not. That is the coordinate-drag
hazard prime directive 2 already rules out for every other step — an ambiguous or approximate
action must fail rather than land on a best-effort result — and a wheel-value scenario has no
other step to fall back on today.

XCUITest itself solves this with `XCUIElement.adjust(toPickerWheelValue:)`, called directly on the
resolved wheel element rather than on a coordinate. The call is handle-based exactly the way `tap`
already is in this codebase's implementation model (`XcuitestDriver.tap` resolves a selector to a
handle, then actuates on that handle — `bajutsu/drivers/xcuitest.py`), not coordinate-based the way
`swipe` / `drag` are. `setPickerValue` follows `tap`'s shape for exactly this reason, closing a
gap none of the existing steps can reach.

## Detailed design

**Schema.** `Step` (`bajutsu/scenario/models/steps.py`) is not a discriminated union — every
action is an optional field, and `_one_action` enforces that exactly one is set. Adding
`SetPickerValue(sel: Selector, value: str)` to `bajutsu/scenario/models/actions.py`, following
the existing `Drag` / `Swipe` models, plus one new `set_picker_value` field on `Step`, is the
only schema change needed. Five places derive from that field automatically, with no further
change: `_STEP_ACTIONS` (from `Step.model_fields`); `STEP_ACTIONS`
(`bajutsu/scenario/models/__init__.py`); the "exactly one action" validation; `_RUNTIME_ACTIONS`
(`bajutsu/orchestrator/actions/_registry.py`); and `_ACTION_KEYS`
(`bajutsu/analysis/impact.py`).

**Addressing a multi-component wheel.** A `UIDatePicker` in wheel-only mode lays out its year and
month as two independent components, each its own `pickerWheel`-typed element. The runner's
snapshot walk (`XcuitestElementProvider.swift`, the `SnapshotNode` tree) already recurses into an
element's children generically, so both components already surface as separate nodes today, not
just the parent `UIDatePicker`. `Selector` (`bajutsu/scenario/models/selector.py`) already carries
`within`, `traits`, and `index` for exactly this kind of disambiguation — the same `index`
discipline `handleSystemAlert` uses to pick one button among several. `sel` in `setPickerValue` always
addresses one such component, so `value` stays a plain string: `within: { id: birthdate.picker },
traits: [pickerWheel], index: 0` selects the year wheel, `index: 1` the month wheel, and
`value: "2016年"` and `value: "4月"` are two separate steps. This reuses the addressing mechanism
every other selector-based step already has instead of growing a second,
`setPickerValue`-specific way to name a component.

**Runtime dispatch.** `bajutsu/orchestrator/actions/handlers/gestures.py` gains an
`@_handler("set_picker_value")` function, following `_do_tap`'s shape, that calls
`driver.set_picker_value(step.set_picker_value.sel, step.set_picker_value.value)`. Without
it, `_registry.py`'s `_do_action` raises `AssertionError("unhandled action")` the moment the field
is set — this handler is the one runtime piece the schema addition does not wire up on its own.

**Driver protocol.** `Driver` (`bajutsu/drivers/base.py`) gains
`set_picker_value(self, sel: Selector, value: str) -> None`, implemented by every concrete
`Driver`. `XcuitestDriver` implements it the same way `tap` is implemented: `_resolve_handle`
resolves `sel` to a handle, then `_actuate` sends it to the runner with the same stale-handle
retry loop `tap` already uses. `adb.py` and `playwright.py` implement it by raising
`base.UnsupportedAction` — the same shape `select_option` uses on the two backends that lack a
native `<select>` (`xcuitest.py` and `adb.py`), except mirrored. A picker wheel is an iOS-only UI
control, the reverse of `select_option`'s web-only one. This time Android and web are unsupported
by construction rather than by omission. `webview.py`'s `WebContextDriver` also raises
`UnsupportedAction`, matching its own `select_option` (a `WKWebView` DOM has no picker wheel
either). `xcuitest_live.py`'s `XcuitestLiveDriver` — the W3C WebDriver route BE-0238 added for a
device-cloud session, a second, independent implementer of the XCUITest family alongside the
resident-runner `XcuitestDriver` — raises `UnsupportedAction` for this item: it is the same
platform, so a live-route implementation may be possible through Appium's XCUITest driver, but
that is a separate build-time evaluation this item does not commit to.

`fake.py` needs more than `select_option`'s shape here: `FakeDriver.select_option` only checks
that `sel` resolves uniquely and never validates the option itself. The absent-value behavior
this item needs to unit-test has no equivalent in `select_option`'s own test suite.
`tests/test_select_option.py` covers parse, dispatch, and fake recording; it has no absent-option
case. Absent-value detection is a central part of this item. It is the one behavior the Swift
runner needs a value-readback workaround for, precisely because `adjust(toPickerWheelValue:)`
cannot report it on its own (below), so it needs its own fast unit-test path rather than only an
on-device one. A sibling wheel component (the year vs. the month wheel of a `UIDatePicker`) has no
identifier of its own — *Addressing a multi-component wheel* above disambiguates them purely by
`within` / `traits` / `index` for exactly this reason — so a seed keyed by identifier could not
tell the two apart. `fake.py` instead gains a seeded `picker_wheel_options: dict[int, list[str]]`
keyed by `id()` of the specific `Element` object in the fixture's `screen` list, the same
separately-seeded shape `system_alert_buttons` uses for `handle_system_alert`, but keyed by object
identity rather than by identifier so two identifier-less sibling elements each keep their own
options. `FakeDriver.set_picker_value` resolves `sel` uniquely (zero matches raises
`ElementNotFound`, more than one raises `AmbiguousSelector`, the same discipline every other
action uses), then checks `value` against the resolved element's seeded options: a match records
the actuation, a miss raises `ElementNotFound` naming the value that was not found. This keeps the
handler-dispatch, preflight, and absent-value paths testable without a Simulator, including the
multi-component case.

**Capability and preflight.** `Capability` (`bajutsu/drivers/base.py`) gains `PICKER_WHEEL`, and
`capability_preflight.py`'s `_REQUIREMENTS` gains one entry for it, following the
`HANDLE_SYSTEM_ALERT` entry's shape. A scenario that uses `setPickerValue` on a backend that
does not advertise `PICKER_WHEEL` fails at preflight, before any device work starts, naming the
step's location in the scenario.

**Swift runner.** `Router.swift` (`BajutsuKit/Sources/BajutsuRunner/`) gains a
`("POST", "/setPickerValue")` route and a `handleSetPickerValue` function that resolves the request's
handle through `SnapshotStore`, the same way `handleTap` does, then calls a new
`ElementProviding.setPickerValue(backingElement:value:)` method. `XcuitestElementProvider`
(`BajutsuKit/Runner/Sources/`) implements it by resolving the live element through
`liveElement(for:)` and calling `el.adjust(toPickerWheelValue: value)`, the same live-element
resolution `tap(backingElement:taps:duration:)` already uses.

**Detecting a value the wheel does not have.** `adjust(toPickerWheelValue:)` never throws and
returns nothing; when the requested string never becomes the wheel's on-screen value, XCTest
records that as a soft `XCTIssue` rather than a raised failure. `RunnerUITest`'s
`continueAfterFailure = true` (`BajutsuKit/Runner/Sources/RunnerUITest.swift`) exists precisely to
tolerate a soft issue like this without tearing down the resident runner — which means the call
site cannot rely on an exception to notice the value never landed. The provider must instead read
the wheel's resulting value back after the call and compare it against the requested string,
returning a new `TapResult` case (a sibling of the existing `.notFound` / `.notHittable`,
`ElementProviding.swift`) when they do not match — a case of its own, not a reuse of `.notFound`,
because `.notFound`'s existing message ("no actuatable element for …", named after the selector)
would misreport a resolved, live element whose value simply did not match. `Router` maps that case
to a distinct response status. `_actuate`'s status dispatch (`bajutsu/drivers/xcuitest.py`) is a
fixed check over `_OK` / `_STALE` / `_NOT_FOUND` / `_NOT_HITTABLE` today, and *any other status
already falls to a catch-all that raises `XcuitestChannelError`* (an infra error, not a
`SelectorError`) — so `set_picker_value` needs its own branch there, alongside the existing four,
that raises `base.ElementNotFound` naming the value that did not land. This mirrors
`select_option`'s own precedent for an absent value (`playwright.py`'s `select_option` re-raises an
absent `<select>` option as `ElementNotFound`, a `SelectorError`), so the run loop's existing
selector-failure handling covers it once that branch exists.

**The `datePicker` classification gap is unaffected.** `docs/selectors.md` already documents that
a `UIDatePicker`'s own container element falls to `other` in `typeName(_:)`, since the switch has
no `.datePicker` case. `setPickerValue` never depends on that classification: it addresses the
individual `pickerWheel` component elements a wheel-style `UIDatePicker` exposes as children, not
the parent container, so the step operates a `UIDatePicker` wheel today exactly as it operates a
plain `UIPickerView` wheel — the classification gap on the parent element does not need closing for
this step to work. `docs/selectors.md` gains a note pointing to `setPickerValue` as how a
scenario still sets a `datePicker`'s value despite the parent falling to `other`.

**codegen.** `bajutsu/codegen/xcuitest.py`'s `_emit_step` gains a case emitting
`element.adjust(toPickerWheelValue: "...")` for `step.set_picker_value`, the same shape as its
existing `handle_system_alert` case. The `adb` and `playwright` emitters need no new code: an
unrecognized step already falls through to the existing `// TODO: unsupported step` stub, the
same fallback `selectOption` already hits on those two generators today.

**Documentation.** `docs/scenarios.md` gains a row in the step grammar table and a
`### setPickerValue` section (with the YAML example above and the "why not a coordinate drag"
reasoning from *Motivation*), following `### drag`'s existing shape; `docs/ja/scenarios.md` gets
the Japanese mirror. `docs/drivers.md`'s capability table gains a `pickerWheel` row (xcuitest only);
`docs/ja/drivers.md` gets the mirror. `docs/selectors.md` and `docs/ja/selectors.md` get the
`datePicker` note described above.

**Tests.** Three unit tests, following the parse/dispatch/fake-recording shape
`tests/test_select_option.py` already uses, extended with the seeded `picker_wheel_options` above:

- dispatching `setPickerValue` with a `value` the fake wheel's seeded options hold reaches
  `driver.set_picker_value` and records the actuation;
- dispatching it with a `value` the fake wheel's seeded options do not hold fails with
  `ElementNotFound`;
- running a scenario that uses `setPickerValue` against a backend without `Capability.PICKER_WHEEL`
  (`adb` or `playwright`) fails at preflight, before any actuation is attempted.

## Alternatives considered

- **A `component` / `values` payload addressing a multi-component picker as a whole
  (`value: { component: 0, value: "2016年" }` or `values: ["2016年", "4月"]`).** Rejected:
  `Selector` already addresses one component through `within` / `traits` / `index`, the same
  mechanism every other selector-based step uses. A second, `setPickerValue`-specific way to
  name a component would duplicate that mechanism rather than reuse it, for no addressing power
  the existing fields do not already have.
- **A coordinate-drag step that stops once the wheel's value matches, mirroring `scroll`'s bounded
  loop.** Rejected: a picker wheel's row height and scroll physics are not queryable, so nothing
  bounds how far a drag must travel to land on a given value — the same non-determinism `swipe` /
  `drag` already carry for this exact purpose, which is why they cannot serve here (*Motivation*).
  `adjust(toPickerWheelValue:)` is deterministic because it acts on the element XCTest already
  resolved, not on a coordinate.
- **Reuse `tap` with a `value` modifier instead of a new step name.** Rejected: `tap` means the same
  thing — activate the resolved element — on every backend and every element kind. Branching that
  meaning on whether the resolved element happens to be a picker wheel would make one step's
  contract backend- and element-type-dependent, where `select_option`'s precedent is instead a
  distinct step name for a distinct control.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Schema: `SetPickerValue` action model, `Step.set_picker_value` field
- [ ] Runtime: `gestures.py` handler, `Driver.set_picker_value` (xcuitest / adb / playwright /
      webview / xcuitest_live / fake), `Capability.PICKER_WHEEL` + preflight requirement
- [ ] Swift runner: `/setPickerValue` route, `ElementProviding.setPickerValue`,
      `XcuitestElementProvider` implementation with value-readback detection of an absent option,
      the matching `_actuate` status branch (`bajutsu/drivers/xcuitest.py`)
- [ ] codegen: xcuitest emitter case
- [ ] Docs: `docs/scenarios.md`, `docs/drivers.md`, `docs/selectors.md` (both languages)
- [ ] Tests: value found, value absent (`ElementNotFound`), missing-capability preflight failure,
      the multi-component (year/month) case

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
