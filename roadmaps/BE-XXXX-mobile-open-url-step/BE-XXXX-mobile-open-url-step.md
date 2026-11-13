**English** · [日本語](BE-XXXX-mobile-open-url-step-ja.md)

# BE-XXXX — An openUrl step that opens a URL mid-scenario on iOS and Android

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-mobile-open-url-step.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

This item adds an `openUrl` step that hands a URL to the operating system at any point in a mobile
scenario, such as `- openUrl: "myapp://orders/42"`. The step works on iOS and Android. The app that
receives the URL is the app the scenario drives at that point: the app under test, or the app an
enclosing `app:` block names. After it hands over the URL, the step waits until that app holds the
foreground. The step fails with a reason when the operating system rejects the URL or the app never
comes to the front.

The item also fixes two defects in the launch-time deeplink on Android, because the new step shares
its command. Today `subject.launch.deeplink` passes silently when no activity can handle the URL, and
the device shell splits a URL that contains `&`.

## Motivation

Today a mobile scenario can open a deep link once, at launch, through `subject.launch.deeplink`.
That deeplink opens before any step runs. A screen that needs a signed-in user cannot be reached
that way, because the scenario has not signed in yet when the URL opens. The `openurl` calls in the
environment layer (`simctl openurl` and `adb shell am start`) exist, but no step reaches them.

The web backend has the step this item asks for. [BE-0464](../BE-0464-web-navigate-step/BE-0464-web-navigate-step.md)
added `navigate`, which loads a URL mid-scenario in the browser. BE-0464 left iOS deep links and
Android intents out of scope on purpose. It named three points such a step must settle: which app
resolves the URL scheme, the confirmation dialog on iOS, and the surface the call rides on. This item
settles those three points for the mobile backends.

The gap also blocks porting a scenario between platforms. On iOS, a scenario can sign in, bring the
system browser forward, and type the URL into its address bar. Android has no step-for-step
equivalent, so the same scenario has no portable form. On both platforms the workaround is a UI walk
through another app. Each tap in that walk is a place
for the scenario to break for a reason unrelated to the screen under test.

The launch deeplink on Android has a defect of its own. `adb shell am start` reports an unresolved
intent as `Error: Activity not started, unable to resolve Intent` and still exits with status 0. The
adb wrapper checks the exit status alone, so a run whose deeplink never opened continues on the wrong
screen. Its first failing step then points at the wrong cause.

The observable outcome has two parts. A scenario signs in, runs `openUrl` with a deep link, and then
taps and asserts on the screen that link opens; the scenario passes the same way on every run. A
scenario whose URL no app can handle fails at the `openUrl` step, or at launch, with the reason
`am start` or the runner gave, instead of failing later at an unrelated selector.

## Detailed design

### Authoring surface

```yaml
steps:
  - use: signIn
  - openUrl: "showcase://tabs/settings"          # the app under test receives the URL
  - assert: [{ exists: { id: settings.title } }]
  - openUrl: "showcase://items/${vars.itemId}"   # ${...} tokens expand like any step field
  - app:
      bundleId: ${vars.browserId}                # the browser receives the URL
      steps:
        - openUrl: "https://example.com/promo"
        - tap: { label: "Open in app" }
```

`openUrl` is an ordinary action. A step carries one action, and the common modifiers (`name`,
`capture`, `target`) apply as they do to every other action. The YAML value is a plain string. An
`OpenUrl` model with one `url` field accepts that string through a `before` validator, the same
technique `Navigate` uses (`bajutsu/common/scenario/models/actions/navigate.py`). The validator
rejects a mapping, so `{url: ...}` does not become an undocumented public shape.

The step accepts an absolute URL with any scheme, with three exceptions. The table lists the rule.

| Value | Result |
|---|---|
| `myapp://path` or `https://host/path` | handed to the operating system as written |
| a value with no scheme, such as `/settings` | rejected; a mobile URL has no `baseUrl` to resolve against |
| a `javascript:`, `data:`, or `file:` URL | rejected; none of them names a destination in an app |

The load-time check covers values with no `${...}` token. The handler checks a value that contains
a token again after expansion. One shared function holds the rule, and both checks call it, as
`navigate_url_problem` does for `navigate`.

### Which app receives the URL

The step hands the URL to the app the driver addresses at that moment. Both mobile drivers already
track that app for `app:` blocks:

- the XCUITest runner keeps an app stack, pushed by `/app/enter` and popped by `/app/leave`;
- the adb driver keeps `_app_stack`, and `_front_package` returns its top or the app under test
  (`bajutsu/common/drivers/adb/adb_driver.py:666`).

Outside any `app:` block, the URL goes to the app under test. Inside an `app:` block that names a
browser, the URL goes to that browser. An author who wants a browser to open the URL wraps
the step in an `app:` block. The same block then lets the scenario drive the browser's UI. A separate
field on `openUrl` would give a second way to name the receiving app and would still need the block
to drive that app.

Scoping the URL to one app keeps the step deterministic. An unscoped Android `VIEW` intent can raise
the app chooser when two installed apps claim the URL, so the outcome would depend on the device's
installed apps.

### How the step decides the URL opened

The step succeeds when two conditions hold:

1. The operating system accepted the URL for the receiving app.
2. The receiving app holds the foreground within the bound the `app:` block uses on the same
   backend: the adb driver's 30-second `_APP_FRONT_TIMEOUT` on Android, and the runner's foreground
   poll (20 polls 0.5 seconds apart, about 10 seconds) on iOS.

The step does not wait for any screen content. A deep link that opens a screen asynchronously, after
a network call for example, is the author's to wait for with a following `assert` or `wait`. The
runner's condition waits already cover that case, and keeping the step free of content waits keeps
it free of per-app knowledge (prime directive 3).

### The iOS route

The runner gains one route, `POST /app/openUrl` with a body of `{url}`. The route calls
`XCUIApplication.open(_:)` on the app at the top of its stack, then waits for the app's state to reach `runningForeground`. The route returns `not-foreground` when the wait runs out, the same reply
`/app/enter` returns today.

Mid-scenario the receiving app is usually in front already, so the foreground wait alone cannot show
that the operating system accepted the URL. The route therefore runs `XCUIApplication.open(_:)`
inside the runner's `ObjCExceptionCatcher`, as the runner already does for element interactions
(`BajutsuKit/Sources/BajutsuRunner/APIHandler.swift`). An exception or an XCTest failure that the
call raises becomes a new `not-opened` reply, with the message as the reason. The driver maps
`not-opened` to a step failure. Unit 5 is done when the Simulator shows what `open(_:)` does for a
scheme that the receiving app does not declare. If `open(_:)` raises nothing for that URL, the docs
state the limit: on iOS such a URL passes the step, and the assertion that follows catches the wrong
screen.

`XCUIApplication.open(_:)` exists from iOS 16.4. The runner's deployment
target is iOS 15.0 (`BajutsuKit/Runner/project.yml`), so the route guards the call with
`#available(iOS 16.4, *)` and returns `unsupported` on an earlier system. `unsupported` is a new
`ActuationReply` status, added to the enum in `BajutsuKit/Sources/BajutsuRunner/openapi.yaml`. The
XCUITest driver maps it to `UnsupportedAction`. Without that mapping, the driver's fallback raises
`XcuitestChannelError`, which reads as a runner fault. Preflight does not know the device's iOS
version, so a scenario on iOS 16.3 or earlier fails at the step, after the run leases the device.

The runner route serves the Simulator and a physical device alike. `simctl openurl` reaches the
Simulator alone, and it cannot name the receiving app, so the step does not use it. The launch-time
deeplink keeps its current paths on iOS; this item does not change them.

A system confirmation dialog may appear after the step, for example when a page in a browser hands
a URL to another app. The step has finished by then, because its receiving app, the browser, is
already in front. Such a dialog is a system alert, so a `systemAlert` entry in `interrupts` or a
following `handleSystemAlert` step clears it. The step's own foreground wait runs inside the driver
and the runner, where no alert guard runs. A dialog that keeps the receiving app itself from the
front therefore fails the step with `not-foreground`. The step does not dismiss dialogs on its own: an automatic tap on
"Open" would hide a prompt the real user sees.

### The Android route

The adb driver runs `am start -W --activity-single-top -a android.intent.action.VIEW -d <url>
<package>`, with `<package>` set to `_front_package`. The `-W` flag makes `am start` wait for the
launch and print a status. When `_front_package` is `None`, because the driver addresses no app,
the step fails with a named error before running `am start`. Leaving `<package>` out would send the
unscoped intent that *Alternatives considered* rejects, and the driver already fails closed on a
missing package elsewhere (`bajutsu/common/backends.py`).

The `--activity-single-top` flag lets a scenario return to a screen with the same URL the launch
deeplink used. Without the flag, Android compares the new intent with the intent that started the
app's task. When the two match, Android brings the task to the front and delivers nothing. With the
flag, Android first checks whether the receiving activity sits at the top of the task. If it does,
the activity receives the URL through `onNewIntent`, before the comparison runs. A single-activity
app, which switches screens inside one activity, therefore receives every URL. In a multi-activity
app with a different activity on top, the comparison still runs, and the step fails as the table
below describes when the URL matches the intent that started the task. The app must handle a deep link in `onNewIntent`, as most apps that accept deep
links already do.

The driver reads stdout and stderr together, whatever the exit status. The exit status does not
report a rejected intent, and `real_run` returns stdout alone today. The driver separates two kinds
of fault. Output from `am start` itself, such as an `Error` line, fails the step with that line as
the reason. A fault in adb's own transport, such as `error: device offline` or `error: device not
found`, stays a `DeviceError`, as today. A lost device is an infrastructure fault, not a rejected URL,
and the iOS route keeps the same split between a runner fault and a test outcome.

The table lists how the driver reads each kind of output. When several lines match, a line that
rejects the intent wins over `Status: ok`, because `-W` prints a status line after a warning.

| Output of `am start` | Result |
|---|---|
| `Status: ok`, with no rejecting line | accepted |
| `Warning: Activity not started, intent has been delivered to currently running top-most instance.` | accepted; the running activity received the intent through `onNewIntent` |
| a line that starts with `Error` | rejected, with that line as the reason |
| `Warning: Activity not started, its current task has been brought to the front` | rejected, with that line as the reason; Android brought the task forward without delivering the URL |
| a line that names an exception, such as `java.lang.SecurityException` | rejected, with that line as the reason |
| any other output, such as `Status: timeout` | rejected, with the whole output as the reason |

Acceptance needs a positive signal from the output: `Status: ok` or the delivery warning. The
foreground wait cannot stand in for one, because mid-scenario the receiving app usually holds the
resumed activity already, so the wait returns at its first poll. After an accepted intent, the
driver waits until `_front_package` holds the resumed activity. The
wait reuses the polling loop in `_bring_forward`, which already reads `dumpsys` for `app:` blocks.

`adb shell` joins its arguments into one command line that the device's shell parses. The driver
passes the URL through `shlex.quote`, as `text_script` already does for typed text. Without the
quoting, a query string's `&` ends the command, and a crafted URL could run a command on the device.

### The launch deeplink fix

`deeplink_cmd` (`bajutsu/common/backend_cli/adb/_functions.py:1004`) and the environment's
`open_url` gain the same changes: the `-W` flag, the quoted URL, and the output check from the table above.
A rejected launch deeplink then fails the run's launch with the `Error` line as the reason. The
check needs `-W` here: without the flag, `am start` can write its `Error` line to stderr, which
`real_run` discards. A
scenario that passed before with a deeplink that never opened will now fail at launch. That change is
the intended behavior, and the change log entry names it.

### Capability and preflight

The step rides on a new `Driver.open_url(url)` method and a new `openUrl` capability token. The
XCUITest driver advertises the token on the Simulator and on a physical device. The adb driver
advertises it too. Every other driver raises `UnsupportedAction`, and preflight rejects a scenario
that uses the step on a backend without the token, before the run leases a device.

The generic preflight walk skips `web:` and `app:` blocks and `interrupts` recovery steps. `openUrl`
therefore gets its own locator that follows all three, as `_navigate_locations` does for `navigate`
(`bajutsu/common/capability/capability_preflight.py`). The token check alone cannot reject an
`openUrl` nested inside a `web:` block on iOS or Android, because preflight reads the backend's
token set, not the `WebContextDriver`'s. That rule does not depend on the backend, so it belongs to
the scenario model, not to preflight. The model rejects an `openUrl` inside a `web:` block when the
scenario loads, as it already rejects `installApp` there
(`bajutsu/common/scenario/models/scenario/_targets.py`). A check inside `unsupported()` would ignore
the capabilities that function judges, and `requirements()` would not mirror it, so serve could
dispatch a job that the run preflight then rejects.

The web
backend keeps `navigate` and does not advertise `openUrl`. `navigate` loads a page inside the
browser, while `openUrl` hands a URL to the operating system, and one step name for both would blur
which of the two a scenario means.

`interrupts` recovery steps can use `openUrl` without any change, in most cases. A recovery runs on
the interrupted step's driver, which is the native driver outside a `web:` block. A recovery that
fires during a step nested inside a `web:` block runs on the `WebContextDriver`
(`bajutsu/common/orchestrator/loop/_interrupt_guard.py`). An `openUrl` there fails at run time with
`UnsupportedAction`. Preflight cannot know which step an interrupt will fire during.

### Documentation

The grammar, the authoring guide, and the parity table document the step, each in English and
Japanese:

- `docs/dsl-grammar.md` adds the `openUrl` production and its scheme rule;
- `docs/scenarios.md` adds a section with the sign-in-then-deeplink example and the browser example;
- `docs/platform-parity.md` adds an `openUrl` row, with ✅ for iOS and Android, whose note says the web backend uses `navigate` instead;
- the `urlNavigation` row's note in `docs/platform-parity.md` points at `openUrl` for mobile.

### Work breakdown

| Unit | Scope | Files |
|---|---|---|
| 1. Scenario model | `OpenUrl` model, the shared scheme rule, load-time checks (scheme, and no `openUrl` under `web:`), `Step.open_url` field | `bajutsu/common/scenario/models/actions/`, `bajutsu/common/scenario/models/steps/step.py`, `bajutsu/common/scenario/models/scenario/_targets.py` |
| 2. Driver surface and preflight | `Driver.open_url`, the `OPEN_URL` capability, the preflight locator, the handler, the fake driver | `bajutsu/common/drivers/base/`, `bajutsu/common/capability/capability_preflight.py`, `bajutsu/common/orchestrator/actions/handlers/navigation.py`, `bajutsu/common/drivers/fake/` |
| 3. Android route | the quoted command with `--activity-single-top`, the output check, the foreground wait in the adb driver | `bajutsu/common/backend_cli/adb/`, `bajutsu/common/drivers/adb/adb_driver.py` |
| 4. Launch deeplink fix | the `-W` flag, the quoted URL, and the output check on `subject.launch.deeplink` | `bajutsu/common/backend_cli/adb/_functions.py`, `bajutsu/common/backend_cli/adb/env.py`, `bajutsu/common/platform_lifecycle/environments/android/` |
| 5. iOS route | the `/app/openUrl` runner route, the `not-opened` and `unsupported` replies, and the XCUITest driver's call | `BajutsuKit/Sources/BajutsuRunner/`, `bajutsu/common/drivers/xcuitest/` |
| 6. Showcase scenario | a scenario that reaches a tab by `openUrl` mid-scenario, run on iOS and Android | `demos/showcase/scenarios/` |
| 7. Docs | the grammar, the guide, and the parity row, in English and Japanese | `docs/`, `docs/ja/` |

Units 1 and 2 come first. Units 3 and 5 depend on unit 2 and not on each other. Unit 4 depends on unit 3, whose `am start`
output check it reuses. Unit 3 is done when a scenario opens the launch deeplink's URL again with
`openUrl` on an emulator and reaches the screen it names. Unit 6 needs
units 3 and 5. The Swift code in unit 5 compiles in the `xcodebuild` end-to-end job alone, so that
job is the check for unit 5.

## Alternatives considered

| Alternative | Why we did not choose it |
|---|---|
| Extend `navigate` to the mobile backends | `navigate` resolves against `baseUrl`, accepts `http` and `https` alone, and waits for a page's `load` event. A mobile deep link has none of the three, so one name would carry two meanings. |
| A `DeviceControl` method over `simctl openurl` and `adb` | `DeviceControl` does not know which app an `app:` block addresses, and `simctl openurl` reaches neither a physical device nor a chosen app. |
| An unscoped URL that the operating system resolves | Two apps that claim the same URL raise the Android chooser, so the result depends on what the device has installed. |
| An `app` field on `openUrl` | The field gives a second way to name the receiving app, and driving that app still needs an `app:` block. |
| A `relaunch` with a deeplink | A relaunch terminates the app, which drops in-memory state such as a session the scenario has signed in to. |
| `simctl openurl` on the Simulator, with physical devices left as a gap | Two iOS paths would behave differently, and a physical device could not run the scenario at all. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Scenario model
- [ ] Driver surface and preflight
- [ ] Android route
- [ ] Launch deeplink fix
- [ ] iOS route
- [ ] Showcase scenario
- [ ] Docs

## References

- [BE-0464](../BE-0464-web-navigate-step/BE-0464-web-navigate-step.md) — the web `navigate` step,
  which left mobile deep links to a later item.
- [BE-0447](../BE-0447-install-app-step/BE-0447-install-app-step.md) — the
  `foreground` step and the app stack the `app:` block relies on.
- [`docs/platform-parity.md`](../../docs/platform-parity.md) — the parity table this item adds a row
  to.
- [`XCUIApplication.open(_:)`](https://developer.apple.com/documentation/xctest/xcuiapplication/open(_:))
  — the XCTest call the runner route uses.
- [`am` command reference](https://developer.android.com/tools/adb#am) — `am start` and its `-W`
  flag.
