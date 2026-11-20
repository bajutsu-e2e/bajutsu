**English** · [日本語](BE-0493-ios-edit-menu-text-selection-ja.md)

# BE-0493 — Drive iOS select and copy through the system edit menu

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0493](BE-0493-ios-edit-menu-text-selection.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0493") |
| Implementing PR | [#2493](https://github.com/bajutsu-e2e/bajutsu/pull/2493) |
| Topic | Platform support |
| Related | [BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps.md), [BE-0489](../BE-0489-paste-step/BE-0489-paste-step.md), [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md), [BE-0381](../BE-0381-runner-openapi-contract/BE-0381-runner-openapi-contract.md) |
<!-- /BE-METADATA -->

## Introduction

On the iOS Simulator, the `select` and `copy` steps stop working on a device once anything outside
the app under test has written the pasteboard. The breakage lasts until SpringBoard restarts, or
until the device is rebooted or erased. This item moves both steps off the hardware shortcuts (⌘A and ⌘C) and onto the system edit
menu's "Select All" and "Copy" items, which keep working on a device in that state. The iOS `paste`
step already drives the same menu, so the three text-selection steps end up on one mechanism. To let the driver aim the menu gesture at the field, the item also widens
`Driver.select_all()` to take the field's selector, as `paste(sel)` already does, which touches every
driver.

## Motivation

Today the XCUITest runner implements `select` as `app.typeKey("a", modifierFlags: .command)` and
`copy` as the same call with `"c"` (`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`). Issue
[#2439](https://github.com/bajutsu-e2e/bajutsu/issues/2439) reported that a `setClipboard` step
(`simctl pbcopy`) breaks both shortcuts on that Simulator. After the seed, the field shows no
selection highlight, and the `clipboard` assertion still reads the seeded text.

An on-device investigation on the iOS 26.5 Simulator narrowed the cause down. The trigger is a
pasteboard write by any process other than the foreground app:

- A bare `simctl pbcopy` breaks the device, with no scenario around it.
- A write through `UIPasteboard` from inside the XCUITest runner's own process breaks it too.
- The app's own ⌘C write does not break it. Running `text_editing.yaml` repeatedly on one device
  passes every time.

The state lives in SpringBoard's memory, not in the pasteboard's contents or its daemons:

| Attempted recovery | `text_editing.yaml` afterwards |
|---|---|
| Read the pasteboard back (`simctl pbpaste`) | Fails |
| Clear the pasteboard with an empty `pbcopy` | Fails |
| Restart `pasted`, `CoreSimulatorBridge`, `kbd`, or `testmanagerd` | Fails |
| Restart SpringBoard, or reboot the device | Passes |

The trigger is not fully characterized. `device.yaml` seeds the clipboard with `setClipboard` and
reads it back at once through the `clipboard` assertion, and `text_editing.yaml` still passes after
it on the same device. A read-back after the device has already broken does not repair it, so the
immediate read-back appears to keep the device from breaking in the first place. No scenario can
rely on that undocumented timing, and a scenario that seeds the clipboard without reading it back
does break the device.

Bajutsu cannot fix iOS, but it can stop depending on the shortcut. In the same investigation, a
runner patched to tap the edit menu's "Select All" and "Copy" items passed `text_editing.yaml` twice
on a device that was already in the broken state. The unpatched runner failed on the same device.

Leaving the shortcuts in place keeps a hidden ordering rule on every iOS lane. A lane that runs
scenario files in name order on one device breaks whenever a `setClipboard` scenario sorts ahead of a
`select` scenario. Today, `paste.yaml` (BE-0489) avoids that rule twice over: it carries a
`pasteboard` tag that keeps it out of the bulk runs, and the `actuation` CI lane runs it after
`text_editing.yaml` on purpose. A scenario author who adds the next `setClipboard` scenario has to
know both workarounds.

Once this item ships, `text_editing.yaml` passes on a device where a `setClipboard` scenario ran
first. The `actuation` lane checks that directly by running `paste.yaml`, which seeds the clipboard
without an immediate read-back, before `text_editing.yaml`. The `pasteboard` tag and its ordering note leave the showcase Makefile.

## Detailed design

### Driver API change

`Driver.select_all()` takes no argument today (`bajutsu/common/drivers/base/driver.py`), and an
`Element` carries no keyboard-focus field. The XCUITest driver therefore cannot name the field to tap
or long-press from `query()` alone. This item changes the method to `select_all(sel)`, the same shape
as `paste(sel)`, and `copy_selection()` to `copy_selection(sel)` for the reason the next section
gives. The change reaches every place that declares or calls the two methods:

- the `Driver` protocol and the tracing wrapper (`bajutsu/common/drivers/tracing.py`);
- the six drivers: XCUITest, live XCUITest, adb, Playwright, the WebView context, and the fake driver;
- the `select` and `clear` handlers in `bajutsu/common/orchestrator/actions/handlers/gestures.py`,
  which pass the step's selector. `select` drops its own focusing tap outright, and `clear` drops
  it only on a backend with `TEXT_SELECTION`;
- the `copy` handler and `SelectionState` in `bajutsu/common/orchestrator/actions/_registry.py`. The
  selection state records the selector of the `select` step that established it, and `copy` passes
  that selector to `copy_selection(sel)`.

The handlers no longer tap the field first; they call
`_tap_with_recovery(lambda: driver.select_all(sel), driver, sel)`, the way `_do_paste` calls `paste`.
Like `paste(sel)`, `select_all(sel)` focuses the field itself. On iOS, a tap on a field that already
has focus raises the edit menu, so a separate focusing tap in the handler would put a "Select All"
item on screen before the driver counts the labels, and the driver would never see a new one. Every
driver other than XCUITest taps the field and then actuates as it does today. The `clear` handler still reads the field's current value first. It hands the focusing tap to
`select_all(sel)` only on a backend with `TEXT_SELECTION`. On an empty field, and on a backend
without that capability, it keeps its own tap, because its counted backspace run acts on whichever
field has focus. Every driver other than XCUITest ignores the selector that `copy_selection(sel)`
receives and copies the active selection as it does today.

### Actuation in the XCUITest driver

The Python XCUITest driver (`bajutsu/common/drivers/xcuitest/xcuitest_driver.py`) composes both
actions from its existing primitives (`tap`, `long_press`, and `query`), the way its `paste` method
already does. No runner endpoint is involved.

- **`select_all`** counts the "Select All" labels on screen, then taps the field that `sel` names, before any other touch in the step. A tap on a field that already has focus can raise the edit menu
  (BE-0489), though the probes below saw it fail to right after a `type` step. If no new "Select All" item appears within a
  second, the driver long-presses the field instead, since a second tap would read as a double-tap.
  The driver then taps the one "Select All" label that gained an element.
- **`copy_selection`** does not rely on the menu that `select` left behind. It dismisses any edit
  menu still on screen, runs the same select-all gesture on the field that `sel` names, and taps the
  "Copy" item that its own gesture added. After "Select All", iOS replaces the menu with one that
  holds "Cut", "Copy", and "Paste". Running the select-all again reproduces the selection that
  `select` made, because `select` supports `mode: all` alone, and every step that could change the
  field's text already ends the selection. A `select` → `copy` → `copy` sequence therefore keeps
  working, and no label count carries over from one step to the next.
- **`paste`** gains the same first move. An edit menu that `select` left up holds a "Paste" item, so
  today's `paste` would stop with "a Paste item was already on screen before the gesture". The driver
  first dismisses that menu and waits for its items to leave. A "Paste" label that stays is app
  content, and the existing error still names it.

The edit menu's own behavior on the iOS 26.5 Simulator dictates both rules. Four probes on the
SwiftUI showcase app observed it:

| Probe | Observed |
|---|---|
| Tap the field right after a `type` step | No menu appeared; a 0.6-second long-press then raised "Select All" |
| Tap "Copy" | The menu closed and the selection ended; a later long-press raised no menu |
| Tap the field while the menu is up | The menu closed and the selection ended |
| `paste` while `select`'s menu is up | Failed: "a Paste item was already on screen before the gesture" |

A tap on the field therefore dismisses a lingering menu, and nothing in a later step can reopen a
menu for a selection that still exists. Each menu-driven action starts from a dismissed menu and
raises its own.

Counting labels before the gesture keeps app content out of the match. A button in the app labeled
"Copy" is already on screen before the gesture, so it never counts as the menu's item. When the app's
own element shares the menu item's label, the final tap resolves ambiguously and fails (prime
directive 2). When the menu never appears, the step raises `ElementNotFound` and names the item it
waited for.

The `clear` step calls `select_all(sel)` and then one backspace (`bajutsu/common/orchestrator/actions/handlers/gestures.py`).
The handler now leaves the focusing tap to `select_all(sel)`, and the rest of the sequence stays as it is,
because the backspace deletes whatever selection the edit menu made. `clear` also keeps its fallback that deletes any remaining text by count.

### The localized menu labels

The menu items carry no identifier, only a label. UIKit renders that label in the app's localization,
not the device's language, as BE-0489 found for "Paste". The driver therefore cannot look up the
label from the scenario's locale. It accepts the label of every language UIKit ships and requires
one of them, and no more, to gain an element.

`bajutsu/common/drivers/xcuitest/paste_menu.py` becomes a module that holds three tables: "Paste",
"Select All", and "Copy". Each table holds UIKit's own `Localizable.strings` values for that key, read
from the iOS 26.5 runtime's `UIKitCore.framework`. BE-0489's extraction script
(`roadmaps/BE-0489-paste-step/misc/extract_paste_labels.py`) gains the two new keys, so one run
regenerates all three tables.

### Runner and contract

The `/selectAll` and `/copy` endpoints have no caller once the driver drives the menu itself. This
item removes them from the runner's OpenAPI contract
(`BajutsuKit/Sources/BajutsuRunner/openapi.yaml`), the `ElementProviding` protocol, the router, the
API handler, the XCUITest element provider, and the Swift test fakes. Leaving them in place would keep
the shortcut reachable for any future caller, which is what this item retires.

### Codegen

The generated XCUITest code follows the driver, as it already does for `paste`
(`bajutsu/codegen/xcuitest.py`):

| Step | Emitted code |
|---|---|
| `select` | A tap on the field. Then a 0.6-second `press(forDuration:)`, unless `app.staticTexts["Select All"]` appears within a second. Then a wait for that item and a tap on it. |
| `copy` | A wait for `app.staticTexts["Copy"]`, then a tap on it. |
| `clear` | The `select` sequence above, then one `XCUIKeyboardKey.delete`. |

The generated `copy` taps the item that the preceding `select` left on screen, so a second `copy` in
a row needs the `select` sequence again in the exported test. Like the generated `paste`, these
labels are English. An app localized into another language needs
its own label in the exported test.

### Lanes and documentation

- `.github/workflows/ios-e2e.yml` moves `paste.yaml` ahead of `text_editing.yaml` on the `actuation`
  lane, so that lane checks the regression on every run.
- `demos/showcase/Makefile` drops `pasteboard` from every `--exclude` list together with its
  comment, and `paste.yaml` drops the tag and its header's ordering note. The `actuation` job's
  comments in `ios-e2e.yml` that describe `select` and `copy` as ⌘A / ⌘C and explain the old order
  change with it. `paste.yaml` and `text_editing.yaml` gain the `textselection` tag, and
  `run-flutter` excludes it, as the Scope section below explains.
- `docs/codegen.md` and its `docs/ja/` mirror describe the new emitted code for `select`, `copy`, and
  `clear`.
- `docs/architecture.md` and its mirror describe the iOS actuation of the text-selection steps, if
  they name the shortcuts.

### Scope

This item changes the local XCUITest route alone. The live Appium / WebDriver route (BE-0238) still
raises `UnsupportedAction` for `select`, `copy`, and `paste`. Moving these steps to driver-side
composition makes that route a candidate for the same actuation later, but this item does not wire it.

The Flutter showcase is out of scope too. Its `run-flutter` target drives a Flutter app through the
XCUITest backend, and Flutter draws its own text-selection toolbar rather than UIKit's edit menu.
Nobody has checked whether a tap on a focused Flutter field raises that toolbar, or which labels it
exposes. `run-flutter` therefore keeps `paste.yaml` and `text_editing.yaml` out of its run, through a
`textselection` tag that both files carry and that `run-flutter` alone excludes. The SwiftUI and
UIKit bulk runs include both files. That exclusion records a regression rather than avoiding one:
once the shortcuts and the two runner endpoints are gone, `select`, `copy`, and `clear` stop working
on a Flutter app through the XCUITest backend, unless Flutter's toolbar exposes the same labels.
`clear`'s counted-backspace fallback does not cover the gap, since `select_all` raises
`ElementNotFound` on the absent menu first. The local Flutter SDK was too old to build the showcase
app, so this proposal could not check Flutter's toolbar on a device. The implementation checks it
first. If the toolbar exposes "Select All" and "Copy", the exclusion goes away. Otherwise
`docs/drivers.md`'s Flutter limitation list gains that entry.

## Alternatives considered

| Option | Summary | Why not adopted |
|---|---|---|
| Write the pasteboard from inside the runner | Replace `simctl pbcopy` in `setClipboard` with a `UIPasteboard` write in the runner's process | The investigation showed that a write from the runner's process breaks the shortcuts the same way. The trigger is any writer other than the foreground app, not `simctl` itself. |
| Restart SpringBoard after every `setClipboard` | Restart SpringBoard so it comes back with no broken state | SpringBoard owns the home screen and the app switcher, so a restart disturbs the app under test mid-scenario and adds several seconds of waiting for the device to settle. |
| Try ⌘A first and fall back to the menu | Keep the fast shortcut and use the menu when it fails | No backend can read a selection range, so the driver cannot tell whether ⌘A worked. A fallback that guesses would make the step's actuation depend on device history (prime directive 2). |
| Look up the labels at run time inside the runner | Ask UIKit for "Select All" and "Copy" in the right language from the runner process | The runner's own localization decides that lookup, while the menu follows the app's localization, so the two can disagree. It would also split the label handling from BE-0489's table. |
| Keep the shortcuts and document the ordering rule | Leave the actuation alone and describe the `pasteboard` tag and ordering as a known limitation | Every new `setClipboard` scenario would inherit the hidden ordering rule, and the regression would stay untested. |

The edit menu costs about 1.6 seconds per `select` or `copy` on the iOS 26.5 Simulator, where a
shortcut costs almost nothing. That cost is per step, and only the scenarios that select or copy text pay it, so a
deterministic step outweighs it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Driver API: `select_all(sel)` that focuses the field itself, and `copy_selection(sel)` with `SelectionState` recording the selector, across the protocol, the tracing wrapper, every driver, and the `select`, `clear`, and `copy` handlers.
- [x] Label tables for "Select All" and "Copy", with the extraction script extended to regenerate them.
- [x] XCUITest driver `select_all` and `copy_selection` through the edit menu, with unit tests.
- [x] Runner: remove `/selectAll` and `/copy` from the contract, protocol, router, handler, provider, and fakes.
- [x] XCUITest codegen for `select`, `copy`, and `clear`, with tests.
- [x] `actuation` lane order, the `pasteboard` tag and exclude lists, the `textselection` tag on `run-flutter`, verified on the iOS 26.5 Simulator (SwiftUI and UIKit apps).
- [x] `paste` dismisses an edit menu an earlier step left on screen.
- [x] Flutter: check the toolbar's labels on a device, then drop the `textselection` exclusion or record the limitation in `docs/drivers.md`.
- [x] English and Japanese documentation.

Log:

- [#2493](https://github.com/bajutsu-e2e/bajutsu/pull/2493) — Units 1-9. Shipped the whole item.
  `select_all(sel)` and `copy_selection(sel)` reach every driver, `copy` takes the selector the
  `select` recorded, and the XCUITest driver drives the edit menu's Select All and Copy from the
  three label tables. The runner's `/selectAll` and `/copy` are gone. One deviation from this design:
  no separate tap dismisses a menu an earlier step left up. The gesture's own focusing tap closes it,
  and the label baseline follows each count down as it is observed, so a lingering item is never
  taken for a new one and no extra tap can read as a double-tap. On the iOS 26.5 Simulator,
  `paste.yaml` and then `text_editing.yaml` passed on one device, on the SwiftUI and UIKit apps
  alike. The Flutter check found that Flutter's toolbar exposes "Select All" and "Paste", so
  `select`, `clear`, and `paste` work, but it closes after Select All with no "Copy" item, so `copy`
  fails. `run-flutter` keeps the `textselection` exclusion, and `docs/drivers.md` records the
  limitation.

## References

- Issue [#2439](https://github.com/bajutsu-e2e/bajutsu/issues/2439) — the report of ⌘A and ⌘C
  doing nothing after a `setClipboard`.
- [BE-0265 — Text-editing steps](../BE-0265-text-editing-steps/BE-0265-text-editing-steps.md) —
  `select`, `clear`, `delete`, and `copy`, whose iOS actuation this item changes.
- [BE-0489 — The paste step](../BE-0489-paste-step/BE-0489-paste-step.md) — the edit-menu paste
  and the label table this item extends.
- [BE-0052 — Device state](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)
  — `setClipboard` and the `clipboard` assertion.
- [BE-0381 — The runner's OpenAPI contract](../BE-0381-runner-openapi-contract/BE-0381-runner-openapi-contract.md)
  — the contract this item removes two endpoints from.
