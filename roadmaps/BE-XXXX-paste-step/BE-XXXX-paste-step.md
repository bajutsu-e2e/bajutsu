**English** · [日本語](BE-XXXX-paste-step-ja.md)

# BE-XXXX — Add a paste step that inserts the clipboard into a field

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-paste-step.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#2438](https://github.com/bajutsu-e2e/bajutsu/pull/2438) |
| Topic | Scenario authoring features |
| Related | [BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps.md), [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md), [BE-0369](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice.md), [BE-0280](../BE-0280-conformance-unactuated-verbs/BE-0280-conformance-unactuated-verbs.md) |
<!-- /BE-METADATA -->

## Introduction

A [scenario](../../docs/glossary.md#scenario-authoring) can put text on the clipboard but cannot
paste it. `setClipboard` seeds the clipboard, and `copy` copies a field's selection to it. The only
way to observe either is the `clipboard` assertion, which reads the device clipboard from outside the
app. No step inserts the clipboard into a field, so the flow that motivated `setClipboard` stays out
of reach: a coupon code or a one-time code copied elsewhere and pasted into the app.

This item adds a `paste` step. It focuses a field and inserts the clipboard at the caret, replacing
any selection. It completes the text-editing set of `select`, `clear`, `delete`, and `copy`, and it
runs on iOS (XCUITest), Android (adb), and the web (Playwright).

## Motivation

Paste flows are common in real apps, and a test cannot cover them with `type`. Typing the text
skips the code path under test: the app's own paste handling, input formatting on paste, and the
clipboard read itself. The obvious substitute, a `tap` on a "Paste" control, fails as well. The edit
menu's Paste item has no identifier, and its label changes with the app's language.

The design also has to survive an iOS behaviour we measured on the iOS 26.5 Simulator. A hardware
Cmd+V pastes a clipboard that the same app wrote. A clipboard that another process wrote stays out
of the field, and neither an error nor iOS's paste-consent prompt appears. `setClipboard` writes
through `simctl`, which iOS attributes to another process. A Cmd+V-based step would therefore pass
`select` → `copy` → `paste` and silently do nothing for `setClipboard` → `paste`, the case the step
exists for.

Once this item lands, a scenario can seed the clipboard with `setClipboard`, run `paste` into a field,
and assert the field's `value`. On iOS, the `actuation` lane's `paste.yaml` shows this for an in-app
copy and for a `setClipboard` seed. Android and the web are covered by unit and conformance tests.

## Detailed design

### Step shape and verification

The step follows the shape of `select`, `clear`, and `delete`:

```yaml
- setClipboard: { text: "COUPON123" }
- paste: { into: { id: form.coupon } }
- assert:
    - value: { sel: { id: form.coupon }, equals: "COUPON123" }
```

The handler calls the driver's `paste(sel)`, which taps `into` to focus the field and then pastes.
The driver owns that focusing tap because iOS must read the screen before it (see below). The
handler wraps the call in the same scroll recovery as a `tap`. A focusing tap that fails does so
before anything is pasted, so retrying the whole call is safe. The step keeps no state, and its
outcome is checked by the existing `value` assertion, never by anything the step recorded. Unlike
`copy`, `paste` needs no prior `select`. It inserts at the caret the focusing tap leaves, which on a
field that already holds text is wherever the tap lands. Like every action
other than `copy`, it invalidates a standing selection, since the paste replaces whatever was selected.

### Driver contract and capability gate

`Driver.paste(sel)` takes the field's selector and focuses the field itself. `paste` shares the
`textSelection` capability with `select` and `copy`. A backend that backs select-all and copy also
backs paste, and preflight rejects a scenario using any of the three on a backend without the token.
The live Appium route and the WebView context raise `UnsupportedAction`, as they do for `copy`.

### Per-backend actuation

| Backend | Mechanism |
|---|---|
| Android (adb) | Ctrl+V through `input keycombination`, the same channel as Ctrl+A and Ctrl+C. The driver arms the same tree catch-up as `delete_text`, so the next read sees the pasted `value`. |
| Web (Playwright) | `ControlOrMeta+v`, which is Cmd+V on a macOS host where Ctrl+V pastes nothing. |
| iOS (XCUITest) | The edit menu's Paste item, as described below. |

On iOS, the driver first counts the on-screen elements that carry a Paste label, before touching
anything. Only a Paste item that a gesture *adds* counts as the edit menu's. An app's own "Paste"
button is therefore never taken for the menu. The driver then taps the field. A tap on a field that
already had focus raises the edit menu by itself, and a further gesture would dismiss it, so the
driver looks for a new Paste item for up to a second. If none appears, it long-presses the field for
0.6 seconds. A long press is used instead of a second tap because a UIKit text field reads two quick
taps as a double-tap and shows no menu. The driver waits up to five seconds for a new item, then taps
it. The step fails loudly in three cases:

- No new item appears: `ElementNotFound` (an empty clipboard, or a field that offers no paste).
- New Paste items appear in two languages at once: `AmbiguousSelector`.
- The new item shares its label with app content: the tap resolves two elements and fails with
  `AmbiguousSelector`.

The edit menu's Paste is a system paste control. It reads a clipboard written by another process,
and no paste-consent prompt appears. The runner needs no new endpoint: the driver composes the
existing tap, long-press, and element-query calls.

### The localized Paste label

The menu item has no identifier, only its label. UIKit renders that label in the app's localization,
not the device's language: on a Japanese Simulator, an app that ships no Japanese strings still shows
"Paste". The BE-0320 / BE-0369 lookup keys SpringBoard labels by the scenario's locale and therefore
does not apply here. The driver instead accepts the label of every language UIKit ships and requires
exactly one on screen.

The label table holds UIKit's own `Localizable.strings` values for the key `Paste`. They come from
the iOS 26.5 runtime's `UIKitCore.framework`, not from manual transcription.
[`misc/extract_paste_labels.py`](misc/extract_paste_labels.py) regenerates the table from a runtime.

### Codegen

| Target | Emitted code |
|---|---|
| XCUITest | A tap. Then a 0.6-second `press(forDuration:)`, unless `app.staticTexts["Paste"]` appears within a second. Then a tap on `app.staticTexts["Paste"]`. The label is English, so an app localized elsewhere needs its own label. |
| UI Automator | A click, then `pressKeyCode(KEYCODE_V, META_CTRL_ON)`. |
| Playwright | `focus()`, then `keyboard.press('ControlOrMeta+v')`. |

### On-device coverage

`demos/showcase/scenarios/paste.yaml` holds three scenarios on the Search field. The first pastes an
in-app `copy` back into the cleared field. The second pastes a `setClipboard` seed into an empty
field. The third pastes at the caret of a field that already holds text and focus. All three passed
on the iOS 26.5 Simulator against the SwiftUI and UIKit apps.

The file runs only on the `actuation` CI lane, after `text_editing.yaml`, and the `pasteboard` tag
keeps it out of the local bulk runs. The reason is a separate Simulator behaviour found while building
this item. After a `setClipboard`, in-app select-all and copy (⌘A / ⌘C) do nothing on that device for
every later scenario, until the device is erased. The bulk runs discover files in name order and
would run `paste.yaml` before `text_editing.yaml`. The same behaviour is why the in-app scenario
comes first in the file.

## Alternatives considered

- **Hardware Cmd+V on iOS.** It is the simplest mechanism, and it pastes an in-app copy. It was
  rejected because it silently pastes nothing from a `setClipboard` seed, as *Motivation* describes.
  An earlier experiment that routed `type` through Cmd+V also hit iOS's paste-consent prompt, which
  blocked the runner.
- **A locale-keyed label lookup, as BE-0369 does for SpringBoard prompts.** That lookup keys on the
  scenario's locale. The edit menu follows the app's localization instead, so the lookup would
  resolve "ペースト" for a Japanese scenario while the menu shows "Paste". Accepting every UIKit label
  matches whichever localization the app resolves to.
- **A second tap to raise the edit menu.** It worked on the SwiftUI app and failed on the UIKit app,
  which reads the two taps as a double-tap.
- **Treat any on-screen Paste label as the menu.** An earlier draft did this, and the self-review
  caught the flaw. An app's own "Paste" button would be tapped instead, nothing would be pasted, and
  the step would report success. Counting labels before the first touch removes the guess.
- **A runner-side `/paste` endpoint.** The driver composes the existing tap, long-press, and query
  calls. A new endpoint would add contract surface without making the mechanism more reliable.

## Progress

- [x] `paste` step model, `Step` field, and handler, with the `textSelection` preflight gate.
- [x] `Driver.paste(sel)` on every backend. Android sends Ctrl+V, the web sends `ControlOrMeta+v`,
  and iOS taps the edit menu's Paste item that its own gesture added. The live Appium route and the
  WebView context raise.
- [x] The UIKit Paste label table and its extraction script.
- [x] Codegen for XCUITest, UI Automator, and Playwright.
- [x] Tests: handler, preflight, per-driver, codegen, and the driver conformance case's paste half.
- [x] `paste.yaml` on the `actuation` iOS lane, verified on the iOS 26.5 Simulator (SwiftUI and
  UIKit apps).
- [x] English and Japanese documentation.

## References

- [BE-0265 — Text-editing steps](../BE-0265-text-editing-steps/BE-0265-text-editing-steps.md) —
  `select`, `clear`, `delete`, and `copy`, whose shape and capability this step shares.
- [BE-0052 — Device state](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)
  — `setClipboard` and the `clipboard` assertion.
- [BE-0369 — The paste-consent prompt](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice.md)
  — the iOS prompt that the edit-menu route avoids.
- [`docs/scenarios.md`](../../docs/scenarios.md) — the step reference.
