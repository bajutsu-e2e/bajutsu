# BajutsuSample

A small, self-contained SwiftUI app used as Bajutsu's fixture. It is instrumented
to exercise **every** Bajutsu primitive: all step types, all assertion kinds,
launch-env hooks, a deeplink, and an `os_signpost` interval. Example scenarios live
in [`scenarios/`](scenarios) and the app is wired in the repo-root
[`bajutsu.config.yaml`](../../bajutsu.config.yaml) as the `sample` app.

## Build & run

Requires Xcode and [XcodeGen](https://github.com/yonyz/XcodeGen) (`brew install xcodegen`).

```bash
make -C demos/features sample-gen      # xcodegen generate  -> BajutsuSample.xcodeproj
make -C demos/features sample-build    # compile for the iOS Simulator
# or: cd demos/features/app && xcodegen generate && open BajutsuSample.xcodeproj
```

The generated `.xcodeproj` and `build/` are gitignored; `project.yml` is the source
of truth.

Bundle id: `com.bajutsu.sample` · deeplink scheme: `bajutsusample`.

## Launch-env hooks

Set as `SIMCTL_CHILD_<NAME>` (Bajutsu does this from `launchEnv`).

| Variable | Effect |
|---|---|
| `SAMPLE_UITEST=1` | Disable animations (keeps condition waits tight) |
| `SAMPLE_SKIP_ONBOARDING=1` | Start at the login screen |
| `SAMPLE_LOGGED_IN=1` | Start at the home screen (skip onboarding + login) |
| `SAMPLE_SCREEN=settings` | Open the settings sheet on launch (use with `SAMPLE_LOGGED_IN`) |
| `SAMPLE_TAB=<name>` | Select a tab on launch: `home` (default), `components`, `controls`, `text`, `lists`, `gestures`, `presentation`, `async`, `system` |
| `SAMPLE_SEED=<n>` | Seed `n` home list rows (default 3) |

Deeplinks: `bajutsusample://settings`, `bajutsusample://home`, and one per tab —
`bajutsusample://components`, `bajutsusample://controls`, `bajutsusample://text`,
`bajutsusample://lists`, `bajutsusample://gestures`, `bajutsusample://presentation`,
`bajutsusample://async`, `bajutsusample://system` (each also logs in).

## accessibilityIdentifier catalog

Identifiers follow the namespaced, data-derived convention (`<namespace>.<element>`).

| Screen | Identifiers |
|---|---|
| Onboarding | `onboarding.title`, `onboarding.start` |
| Login (`auth.*` reserved) | `auth.title`, `auth.email`, `auth.password`, `auth.error`, `auth.submit` (disabled until both fields filled) |
| Home | `home.title`, `home.search`, `home.list`, `home.load`, `home.spinner`, `home.loaded`, `nav.settings` |
| Counter | `counter.value` (exposes accessibilityValue), `counter.increment` |
| List rows | `list.row.<id>` (data-derived; `<id>` is the row's stable id) |
| Settings | `settings.title`, `settings.normalizeToggle` (isSelected trait when on), `settings.banner` (appears after a change), `settings.reindex`, `settings.status` (value), `settings.reindexComplete` (appears when done), `settings.close` |
| Controls (`SAMPLE_TAB=controls`) | `ctrl.toggle` (label hidden so the element is just the switch) + `ctrl.toggle.value`, `ctrl.stepper` + `ctrl.stepper.value`, `ctrl.slider` + `ctrl.slider.value`, `ctrl.segment.{one,two,three}` (id'd buttons) + `ctrl.segment.value`, `ctrl.menu` + `ctrl.menu.value`, `ctrl.button` + `ctrl.button.value`, `ctrl.buttonDisabled` (always disabled). State mirrors to each `*.value` label. |
| Text (`SAMPLE_TAB=text`) | `text.basic` + `text.basic.value` + `text.count`, `text.clear`, `text.email`, `text.editor` + `text.editor.value`, `text.required`, `text.error` (too short), `text.submit` (disabled until valid), `text.submitted` (value) |
| Lists & Nav (`SAMPLE_TAB=lists`) | `lists.search`, `lists.list`, `lists.row.<id>` (data-derived, swipe-to-delete), `lists.empty` (no match), `lists.count` (value), `lists.edit` (EditButton, reorder/delete), `lists.refreshed` (after pull-to-refresh), `lists.detail.title` + `lists.detail.value` (pushed detail) |
| Gestures (`SAMPLE_TAB=gestures`) | `gest.doubletap` + `gest.doubletap.value` (tap count), `gest.pinch` + `gest.pinch.value` (`in`/`out`), `gest.rotate` + `gest.rotate.value` (`cw`/`ccw`). double-tap drives on idb; pinch/rotate need multi-touch (XCUITest). |
| Presentation (`SAMPLE_TAB=presentation`) | `pres.openSheet` -> `pres.sheet.title` + `pres.sheet.close` (detents), `pres.openCover` -> `pres.cover.title` + `pres.cover.close`, `pres.openDialog` -> action sheet -> `pres.dialog.value` (`archive`/`delete`), `pres.showToast` -> `pres.toast` (auto-dismisses) |
| Async (`SAMPLE_TAB=async`) | `async.startProgress` -> `async.progress.value` + `async.progress.done`, `async.loadFail` -> `async.error` + `async.retry` -> `async.loaded`, `async.search` -> `async.debounced.value` (after debounce), `async.loadMore` -> `async.count` |
| System (`SAMPLE_TAB=system`) | `sys.requestNotif` -> SpringBoard prompt -> `sys.notif.value` + `sys.notif.authorized` (vision guard), `sys.copy` / `sys.paste` -> `sys.paste.value` (pasteboard), `sys.share` (ShareLink, out-of-process) |

## Primitive coverage

| Primitive | Where |
|---|---|
| tap / type(into) / wait(for) | `scenarios/smoke.yaml` |
| enabled / disabled | `scenarios/auth.yaml` |
| selected / exists(+negate) / value / capturePolicy | `scenarios/settings.yaml` |
| count / search filter | `scenarios/list.yaml`, `scenarios/lists.yaml` |
| os_signpost interval | emitted by the settings reindex (`com.bajutsu.sample` subsystem) |

## P1 UI gallery (Controls / Text / Lists)

Three extra tabs broaden the fixture beyond the smoke flow into a small UI gallery.
Every interactive control mirrors its state into a `*.value` result label so headless
backends can assert outcomes by value. Scenarios: `scenarios/controls.yaml`,
`scenarios/text.yaml`, `scenarios/lists.yaml`.

| Tab | Exercises |
|---|---|
| Controls | Toggle, Stepper, Slider, single-select segment (id'd buttons), Menu, enabled/disabled Button |
| Text | TextField (default/email), TextEditor, clear, character count, inline validation gating submit |
| Lists & Nav | List + search filter, swipe-to-delete, EditButton reorder/delete, pull-to-refresh, push navigation, empty state |
| Gestures | double-tap (idb: two taps), pinch / rotate (multi-touch). `scenarios/gestures.yaml` |
| Presentation | sheet (detents), fullScreenCover, confirmationDialog, auto-dismissing toast. `scenarios/presentation.yaml` |
| Async | determinate ProgressView, fail -> retry -> success, debounced search, pagination. `scenarios/async.yaml` |
| System | notification permission (SpringBoard prompt), pasteboard, share sheet. `scenarios/system.yaml` (pasteboard, plain idb) + `scenarios/permission.yaml` (notification, run with `--dismiss-alerts --alert-instruction "tap Allow"`) |

**System / out-of-process note:** idb's accessibility query is scoped to the foreground
app, so SpringBoard prompts (permissions, "Save Password?") and the share sheet are
invisible to it. The notification prompt is cleared by the run's vision alert guard
(`--dismiss-alerts`); pasteboard is in-app and drives on plain idb. Reinstall the app
before `permission.yaml` — uninstall+install resets notification authorization (there is
no `simctl privacy` service for notifications).

**Gesture DSL note:** `doubleTap` / `pinch` / `rotate` are scenario primitives. idb is
single-touch, so `pinch` / `rotate` fail a `bajutsu run` with a clear "needs multiTouch"
reason; `bajutsu codegen` emits them as `doubleTap()` / `pinch(withScale:)` / `rotate(_:)`,
so their on-device verification path is the generated XCUITest.
