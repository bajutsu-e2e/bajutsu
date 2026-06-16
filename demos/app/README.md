# BajutsuDemo — the dedicated demo app

**English** · [日本語](README.ja.md)

A small, focused SwiftUI app for the on-device demos (`make tour` / `make features`). It is
deliberately minimal — just the **onboarding → login → home (counter)** flow — so the demo
tells the whole story (author → run → modify → diagnose) without distraction. The richer,
exercise-everything fixture is the separate [`sample` app](../features/app/README.md).

## Screens and accessibility ids

Every interactive element has a stable `accessibilityIdentifier` — the selectors the scenarios
resolve against (Bajutsu prefers ids over labels/coordinates).

| Screen | Element | id | Notes |
|---|---|---|---|
| Onboarding | "Get Started" button | `onboarding.start` | advances to login |
| Login | Email field | `auth.email` | |
| Login | Password field (secure) | `auth.password` | a real masked `SecureField` (the secret stays dots in screenshots) |
| Login | "Log in" button | `auth.submit` | disabled until both fields are non-empty |
| Home | "Home" title | `home.title` | the login destination scenarios `wait` for |
| Home | "Count: N" label | `counter.value` | mirrors the count into `accessibilityValue` so `value.equals` reads it on idb |
| Home | "Increment" button | `counter.increment` | +1 per tap |
| Home | "Log out" button | `home.logout` | resets and returns to onboarding |

## Launch-env hooks

The app reads these from its launch environment (Bajutsu injects them via the config's
`launchEnv`):

- `DEMO_UITEST` — disable animations so condition waits stay tight (set in
  [`demos/demo.config.yaml`](../demo.config.yaml)).
- `DEMO_SKIP_ONBOARDING` — start on the login screen.
- `DEMO_LOGGED_IN` — start on Home (skip onboarding + login).

The auth flow is a modal (`fullScreenCover`) over an always-present Home, so acting on Home
right after login doesn't race a rebuilt view — the transition idb's accessibility query can
otherwise briefly see as an empty tree.

On a successful login the app also clears the password field before it resigns. iOS's
SpringBoard-level "Save Password?" prompt is invisible to idb and collapses the app's element
tree to a single node while it's up; the deterministic tour/features runs carry no API key, so
the vision-based alert guard can't clear it. Clearing the field leaves iOS nothing to offer to
save, so login settles straight to Home with no blocking prompt.

## Build

```bash
make -C demos app-build        # xcodegen generate -> xcodebuild for the iOS Simulator
```

This produces `BajutsuDemo.app` under `demos/app/build/…` (the `.xcodeproj` and `build/` are
gitignored — regenerate locally). `bajutsu run` / `bajutsu serve` also build it on demand via
the config's `build` command, so the demos build it for you when the binary is missing.

This app and its scenarios are built and run on-device as part of the demos — `make -C demos
tour` (run → modify → diagnose) and `make -C demos features` (the tagged/shared/secret
showcase) both drive it on a booted Simulator via idb.
