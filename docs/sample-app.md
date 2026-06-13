**English** · [日本語](ja/sample-app.md)

# The sample app (the BajutsuSample fixture)

> The small, self-contained SwiftUI app under `demos/features/app/`. A test fixture built to exercise **every**
> Bajutsu primitive (all step kinds, all assertion kinds, launch-env hooks, a deeplink, and an
> `os_signpost` interval), plus a 10-tab UI gallery (Home / Components / Controls / Text / Lists /
> Gestures / Presentation / Async / System / Network) over an onboarding → login auth flow.
>
> Full details in [`demos/features/app/README.md`](../demos/features/app/README.md). Here we summarize the mapping to
> Bajutsu features.

Related: [scenarios](scenarios.md) · [configuration](configuration.md) · [codegen](codegen.md) · [cli](cli.md)

---

## Positioning

- Registered as the `sample` app in the root [`bajutsu.config.yaml`](../bajutsu.config.yaml).
- bundle id `com.bajutsu.sample` · deeplink scheme `bajutsusample`.
- Built with XcodeGen + xcodebuild (`project.yml` is the source of truth; `.xcodeproj`/`build/` are
  gitignored).
- Example scenarios are in [`demos/features/app/scenarios/`](../demos/features/app/scenarios).

```bash
make -C demos/features sample-gen     # xcodegen generate -> BajutsuSample.xcodeproj
make -C demos/features sample-build   # compile for the iOS Simulator
```

## Launch-env hooks

Passed as `SIMCTL_CHILD_<NAME>` (Bajutsu does this conversion automatically from `launchEnv` ·
[drivers](drivers.md#environment-management-simctl)). Inject state to set up a test's preconditions.

| Variable | Effect |
|---|---|
| `SAMPLE_UITEST=1` | disable animations (keeps condition waits tight) |
| `SAMPLE_SKIP_ONBOARDING=1` | start at the login screen |
| `SAMPLE_LOGGED_IN=1` | start at the home screen (skip onboarding + login) |
| `SAMPLE_SCREEN=settings` | open the settings sheet on launch (use with `SAMPLE_LOGGED_IN`) |
| `SAMPLE_TAB=<name>` | select a tab on launch: `home` (default), `components`, `controls`, `text`, `lists`, `gestures`, `presentation`, `async`, `system`, `network` |
| `SAMPLE_SEED=<n>` | seed n home list rows (default 3) |

Deeplinks: `bajutsusample://settings`, `bajutsusample://home`, and one per tab —
`bajutsusample://components`, `bajutsusample://controls`, `bajutsusample://text`,
`bajutsusample://lists`, `bajutsusample://gestures`, `bajutsusample://presentation`,
`bajutsusample://async`, `bajutsusample://system` (each also logs in). The `network` tab has no
deeplink; reach it with `SAMPLE_TAB=network`.

## accessibilityIdentifier catalog

Follows the naming convention (`<namespace>.<element>` ·
[configuration](configuration.md#identifier-naming-convention)). `auth.*` / `nav.*` are reserved
namespaces. Dynamic rows (`list.row.<id>` / `lists.row.<id>`) are disambiguated by a data-derived key.

| Screen | Key identifiers |
|---|---|
| Onboarding | `onboarding.title` / `onboarding.start` |
| Login | `auth.email` / `auth.password` / `auth.submit` (disabled until both fields are filled) / `auth.error` |
| Home | `home.title` / `home.search` / `home.list` / `home.spinner` / `nav.settings` |
| Counter | `counter.value` (exposes accessibilityValue) / `counter.increment` |
| List rows | `list.row.<id>` (data-derived) |
| Settings | `settings.normalizeToggle` (selected trait when on) / `settings.banner` (appears after a change) / `settings.reindex` / `settings.status` (value) / `settings.reindexComplete` |
| Controls (`SAMPLE_TAB=controls`) | `ctrl.toggle` / `ctrl.stepper` / `ctrl.slider` / `ctrl.segment` / `ctrl.menu` / `ctrl.button` (+ a `*.value` mirror each) / `ctrl.buttonDisabled` |
| Text (`SAMPLE_TAB=text`) | `text.basic` (+ `text.basic.value` / `text.count`) / `text.clear` / `text.email` / `text.editor` / `text.required` / `text.error` / `text.submit` (gated until valid) / `text.submitted` |
| Lists & Nav (`SAMPLE_TAB=lists`) | `lists.search` / `lists.row.<id>` (swipe-to-delete) / `lists.empty` / `lists.count` (value) / `lists.edit` / `lists.refreshed` / `lists.detail.title` (+ `lists.detail.value`) |
| Gestures (`SAMPLE_TAB=gestures`) | `gest.doubletap` (+ `gest.doubletap.value`) / `gest.pinch` (+ `.value`) / `gest.rotate` (+ `.value`) — double-tap is idb-drivable; pinch / rotate need real multi-touch (their on-device path is the generated XCUITest) |
| Presentation (`SAMPLE_TAB=presentation`) | `pres.openSheet` → `pres.sheet.title` / `pres.sheet.close` · `pres.openCover` → `pres.cover.*` · `pres.openDialog` → `pres.dialog.value` · `pres.showToast` → `pres.toast` (auto-dismisses → `wait until gone`) |
| Async (`SAMPLE_TAB=async`) | `async.startProgress` → `async.progress.value` / `async.progress.done` · `async.loadFail` → `async.error` → `async.retry` → `async.loaded` · `async.search` → `async.debounced.value` (debounced) · `async.loadMore` → `async.count` |
| System (`SAMPLE_TAB=system`) | `sys.requestNotif` → `sys.notif.value` / `sys.notif.authorized` (the OS prompt lives in SpringBoard → cleared by the vision alert guard) · `sys.copy` / `sys.paste` → `sys.paste.value` (in-app pasteboard) · `sys.share` (system share sheet) |
| Network (`SAMPLE_TAB=network`) | `net.fetch` / `net.get-query` / `net.post` (carries a secret header + body for redaction) / `net.delete` · `net.status` (value) · `net.captured.*` (method / status / duration / url, read back from BajutsuKit) — needs BajutsuKit + `BAJUTSU_COLLECTOR` |

> Every interactive control on the gallery tabs mirrors its state into a `*.value` result label, so
> headless backends can assert outcomes **by value** rather than by reading the control itself.

## Primitives mapped to scenarios

Which scenario uses each primitive (mapped to the grammar in [scenarios](scenarios.md)).

| Primitive | Scenario |
|---|---|
| tap / type(into) / wait(for) | [`smoke.yaml`](../demos/features/app/scenarios/smoke.yaml) |
| enabled / disabled | [`auth.yaml`](../demos/features/app/scenarios/auth.yaml) |
| selected / exists(+negate) / value / capturePolicy | [`settings.yaml`](../demos/features/app/scenarios/settings.yaml) |
| count / idMatches / search filter | [`list.yaml`](../demos/features/app/scenarios/list.yaml) · [`lists.yaml`](../demos/features/app/scenarios/lists.yaml) |
| longPress / in-app alert (label tap) / swipe(on+direction) | [`components.yaml`](../demos/features/app/scenarios/components.yaml) |
| video / deviceLog interval + os_signpost | [`evidence.yaml`](../demos/features/app/scenarios/evidence.yaml) |
| Controls gallery (toggle / stepper / slider / picker / menu / button) | [`controls.yaml`](../demos/features/app/scenarios/controls.yaml) |
| Text entry (value + char count / clear / inline validation) | [`text.yaml`](../demos/features/app/scenarios/text.yaml) |
| List search / swipe-delete / edit / pull-to-refresh / push nav / empty state | [`lists.yaml`](../demos/features/app/scenarios/lists.yaml) |
| doubleTap / pinch / rotate (multi-touch via codegen) | [`gestures.yaml`](../demos/features/app/scenarios/gestures.yaml) |
| Sheet / full-screen cover / confirmationDialog / toast (`wait until gone`) | [`presentation.yaml`](../demos/features/app/scenarios/presentation.yaml) |
| Progress / fail→retry→success / debounce / pagination (condition waits) | [`async.yaml`](../demos/features/app/scenarios/async.yaml) |
| Notification prompt (alert guard) / pasteboard | [`system.yaml`](../demos/features/app/scenarios/system.yaml) |
| `request` assertion / HTTP methods / deterministic `mocks` | [`network.yaml`](../demos/features/app/scenarios/network.yaml) · [`network_methods.yaml`](../demos/features/app/scenarios/network_methods.yaml) · [`network_mock.yaml`](../demos/features/app/scenarios/network_mock.yaml) |
| relaunch (re-inject env mid-scenario) | [`relaunch.yaml`](../demos/features/app/scenarios/relaunch.yaml) |

## The UI-test target and make targets

Two paths against a real Simulator ([`Makefile`](../Makefile)). `SIM` auto-detects the booted device.

### `make -C demos/features e2e` (run on the idb backend)

```
sample-build → simctl install → bajutsu run smoke.yaml (idb / --no-erase) → bajutsu doctor
```

Prereqs: a booted Simulator · `brew install facebook/fb/idb-companion` · `uv sync --extra idb`.

### The UI-test target and make target

`make -C demos/features ui-test` runs the **codegen path**: generate an XCUITest from a scenario and run it via
xcodebuild (no bajutsu runtime / idb / AI at test time · [codegen](codegen.md)).

```
bajutsu codegen components.yaml -o BajutsuSampleUITests/ComponentsUITests.swift
  → xcodegen generate → xcodebuild test (scheme: UITests)
```

`project.yml` already defines the `BajutsuSampleUITests` (`bundle.ui-testing`) target and the
`UITests` scheme. The generated
[`ComponentsUITests.swift`](../demos/features/app/BajutsuSampleUITests/ComponentsUITests.swift) is committed (as
an example of codegen output).
