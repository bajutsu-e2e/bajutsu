**English** · [日本語](BE-XXXX-android-non-ascii-text-entry-ja.md)

# BE-XXXX — Enter non-ASCII text on Android through the resident server

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-android-non-ascii-text-entry.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#2283](https://github.com/bajutsu-e2e/bajutsu/pull/2283) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

A `type` step on the Android backend can now enter any text, including Japanese and emoji. Until
this item, the step crashed on the device as soon as the text held one non-ASCII character. We keep
`adb shell input text` for the plain ASCII it handles correctly. Everything else goes through a new
`/type` endpoint on the resident UI Automator server, the instrumentation process that Bajutsu keeps
running on the device for hierarchy reads and gestures.

## Motivation

`input text` turns each character into key events through the virtual keyboard's character map. That
map holds no key for a non-ASCII character, so the lookup returns null, and the command dies:

```text
java.lang.NullPointerException: Attempt to get length of null array
    at com.android.server.input.InputShellCommand.sendText(InputShellCommand.java:319)
```

The step fails with a raw non-zero exit from `adb shell`, which names neither the cause nor a remedy.
A scenario for a Japanese-language app has no way to fill a search box or a form on Android, while the
same scenario runs on the other backends. A test tool that cannot enter the language of the app under
test leaves that app's text paths uncovered.

`input text` has a second, quieter defect. It reads a literal `%s` as its escape for a space and
offers no way to escape the escape, so `100%sure` arrives as `100 ure`. The step passes and the field
holds the wrong value.

A later reader can check the outcome directly: `demos/showcase/scenarios/text_entry_android.yaml`
types `馬`, `術🐎`, ` x`, and `語` in turn, and the field then holds `馬術🐎 x語` on both the Compose and
the Views showcase apps.

## Detailed design

### Route

`AdbDriver.type_text` asks one question, `adb.input_text_can_type(text)`: is the text ASCII, with no
literal `%s`? When the answer is yes, the driver runs `input text` exactly as before, so existing
scenarios keep sending real key events. When the answer is no, the driver sends the text to `/type`.
The route depends on the text alone, which keeps the choice deterministic (prime directive 2).

### The `/type` endpoint

The host sends `POST /type` with the text as a base64 body. The text stays out of the request target
because a typed value may be a secret, and a target is what a proxy or a log would keep. Base64 also
keeps the server's character reader counting bytes one-for-one against `Content-Length`. The server
logs neither the body nor its length.

The server then performs these steps:

1. Wait for the app to go idle, so that a read cannot miss the text an earlier step entered. Then wait
   up to two seconds for an editable node to hold input focus. Until the focus from the tap on `into`
   arrives, the node holding focus can be the one from before the tap.
2. Refuse the request if the node is a password field, is not editable, or does not offer
   `ACTION_SET_TEXT`.
3. Build the new value from the current text and selection. `ACTION_SET_TEXT` replaces the whole
   content while typing inserts at the caret, so the server has to compute the result of inserting.
   A hint that the field reports as its text counts as empty.
4. Perform `ACTION_SET_TEXT`, then move the caret back after the inserted text when the insertion
   did not land at the end.

### Failure behavior

No second route exists for this text, so every refusal fails the step and names the reason:

| Situation | Result |
|---|---|
| No resident server on this lease | The step fails and says the text needs the server |
| A server that predates `/type` (404) | The step fails and asks for a rebuilt server |
| No focused field, a password field, or a field without the action | The step fails with the device's reason |
| The request went out and the reply was lost | A warning, and the run continues without resending |

The last row follows the rule that tap gestures already use. Sending the text again could enter it twice,
while text that never arrived fails the scenario's own assertion. No reply and no log line quotes the
text.

### Limits

`ACTION_SET_TEXT` reaches the field directly, so this route exercises no input method editor (IME):
no composing state, no conversion candidates. A scenario cannot test IME behavior through it.
A field that does not offer the action, such as a custom-drawn Flutter text field, fails the step.

The text goes to whichever editable field holds focus. The endpoint does not check that the field is the `into`
target, which matches `input text`: its key events also go to the focused field.

We refuse password fields outright, because the platform masks or omits their accessibility text.
The server cannot tell what `ACTION_SET_TEXT` would overwrite, even to confirm that the field is empty.

## Alternatives considered

| Alternative | Why we did not choose it |
|---|---|
| A custom IME committing the text | The closest to real input, but it changes a device-wide setting that has to be restored, even after a crash |
| ADBKeyBoard | The same IME switch, plus a third-party APK to install on the device under test |
| Clipboard plus paste | Overwrites the clipboard, which breaks the `clipboard` assertion, and leaves a typed secret there |
| `Instrumentation.sendStringSync` | Goes through the same character map as `input text`, so it fails on the same characters |
| Failing every non-ASCII `type` with a clear error | Small and honest, but the scenario still cannot run |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Route text by `input_text_can_type` in `AdbDriver.type_text`.
- [x] Add the `/type` endpoint to the resident server, with request-body reading.
- [x] Add the host client, the channel wiring, and the failure mapping.
- [x] Add the unit tests and the `text_entry_android` end-to-end scenario for Compose and Views.
- [x] Update the driver documentation and the server README in both languages.

Log:

- 2026-11-04 ([#2283](https://github.com/bajutsu-e2e/bajutsu/pull/2283)): Reproduced the NullPointerException on an API 34 emulator, then implemented the route.
  The new scenario passed there on Compose and Views. With the resident server disabled, it failed
  with the named reason. No end-to-end scenario covers the password refusal or an insertion before
  the end of the text.

## References

- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md):
  the resident UI Automator server this endpoint extends.
- [BE-0155](../BE-0155-idb-input-text-via-stdin/BE-0155-idb-input-text-via-stdin.md): the rule that a
  typed value stays out of the host's command line, which this endpoint extends to the request target.
- [`AccessibilityNodeInfo.ACTION_SET_TEXT`](https://developer.android.com/reference/android/view/accessibility/AccessibilityNodeInfo#ACTION_SET_TEXT):
  the platform action behind the endpoint.
