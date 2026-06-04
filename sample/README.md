# SimyokeSample

A small, self-contained SwiftUI app used as Simyoke's fixture. It is instrumented
to exercise **every** Simyoke primitive: all step types, all assertion kinds,
launch-env hooks, a deeplink, and an `os_signpost` interval. Example scenarios live
in [`scenarios/`](scenarios) and the app is wired in the repo-root
[`simyoke.config.yaml`](../simyoke.config.yaml) as the `sample` app.

## Build & run

Requires Xcode and [XcodeGen](https://github.com/yonyz/XcodeGen) (`brew install xcodegen`).

```bash
make sample-gen      # xcodegen generate  -> SimyokeSample.xcodeproj
make sample-build    # compile for the iOS Simulator
# or: cd sample && xcodegen generate && open SimyokeSample.xcodeproj
```

The generated `.xcodeproj` and `build/` are gitignored; `project.yml` is the source
of truth.

Bundle id: `com.simyoke.sample` · deeplink scheme: `simyokesample`.

## Launch-env hooks

Set as `SIMCTL_CHILD_<NAME>` (Simyoke does this from `launchEnv`).

| Variable | Effect |
|---|---|
| `SAMPLE_UITEST=1` | Disable animations (keeps condition waits tight) |
| `SAMPLE_SKIP_ONBOARDING=1` | Start at the login screen |
| `SAMPLE_LOGGED_IN=1` | Start at the home screen (skip onboarding + login) |
| `SAMPLE_SCREEN=settings` | Open the settings sheet on launch (use with `SAMPLE_LOGGED_IN`) |
| `SAMPLE_SEED=<n>` | Seed `n` list rows (default 3) |

Deeplinks: `simyokesample://settings`, `simyokesample://home`.

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

## Primitive coverage

| Primitive | Where |
|---|---|
| tap / type(into) / wait(for) | `scenarios/smoke.yaml` |
| enabled / disabled | `scenarios/auth.yaml` |
| selected / exists(+negate) / value / capturePolicy | `scenarios/settings.yaml` |
| count / search filter | `scenarios/list.yaml` |
| os_signpost interval | emitted by the settings reindex (`com.simyoke.sample` subsystem) |
