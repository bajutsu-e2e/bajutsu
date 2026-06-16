# BajutsuDemo — the dedicated demo app

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
| Login | Password field (secure) | `auth.password` | a real `SecureField` → iOS "Save Password?" prompt |
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

## Build

```bash
make -C demos app-build        # xcodegen generate -> xcodebuild for the iOS Simulator
```

This produces `BajutsuDemo.app` under `demos/app/build/…` (the `.xcodeproj` and `build/` are
gitignored — regenerate locally). `bajutsu run` / `bajutsu serve` also build it on demand via
the config's `build` command, so the demos build it for you when the binary is missing.

> **Note:** this app and its scenarios were authored against the iOS Simulator's accessibility
> model but, at the time of writing, have not yet been built and run on-device — verify with
> `make -C demos tour` on a Mac with a booted Simulator.
