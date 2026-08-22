# Captured real-alert fixtures (BE-0308)

Real dialogs captured from the showcase app on a real iOS Simulator, with the ground truth needed to
judge whether the system-alert guard's vision locator answers them correctly
([BE-0308](../../../roadmaps/BE-0308-alerts-guard-real-model-verification/BE-0308-alerts-guard-real-model-verification.md)).

Each fixture is a pair: `<name>.png` is what the guard's own capture path would hand the model
(`bajutsu.screenshots.screenshot_bytes`, downscale included), and `<name>.json` records the screen it
was captured against plus every button on the dialog, read from the device's accessibility tree
rather than measured by eye.

| Fixture | Dialog | Correct answer |
|---|---|---|
| `notif_permission` | the OS notification-authorization prompt | the coordinates of `Don’t Allow`, beside the granting button |
| `location_permission` | the OS location prompt, three stacked choices | the coordinates of `Don’t Allow`, the bottom one of the three |
| `paste_consent` | iOS's cross-process paste-consent prompt (BE-0369) | the coordinates of `Don’t Allow Paste` |
| `app_destructive_dialog` | the app's own delete confirmation — `Archive` / **`Delete`** / `Cancel` | *no prompt present*: the guard must leave a dialog the app owns alone, and the button it would reach for deletes |

`tests/test_real_model_alerts.py` replays them two ways: a deterministic ground-truth check on every
gate (no credential — it verifies each fixture is self-consistent), and a key-gated live check that
asks a real model where to tap and asserts the answer lands inside the correct control's frame.

## The JavaScript Object Notation (JSON) file

| Field | Meaning |
|---|---|
| `schema` | the format's version; a load refuses an unknown one rather than reading old ground truth under new rules |
| `screen` | the device's real screen bounds in points — the space `dismiss` and `others` frames are in, and the scale a normalized answer is multiplied back up by. It is the backend's own viewport (BE-0326), *not* `screen_size_from_elements`, which overshoots a scrollable screen |
| `dismiss` | the one control a correct answer must land on, or `null` when the dialog is not an OS prompt at all |
| `others` | every button on the same dialog a correct answer must avoid, so a failure can name the one it reached for |
| `note` | why this dialog is in the set, quoted into the assertion's failure message |

## Re-capturing

Capture needs a booted Simulator with the showcase SwiftUI app and the resident runner built, so it
is a manual, local step — never part of the gate, and no CI job runs it:

```bash
make -C demos/showcase swiftui-build runner-build
```

```bash
BAJUTSU_ALERT_FIXTURE_UDID=<udid> uv run pytest tests/test_alert_fixtures_ondevice.py -m ondevice -n0
```

Each case writes its pair here. Review the screenshots and the recorded labels, then commit them so
the replay covers everyone. Re-capture when the showcase screens move, or when a new iOS release
changes what SpringBoard renders — the harness refuses to write a fixture whose expected button the
device did not actually report, so a drift fails the capture instead of quietly relabelling it.
