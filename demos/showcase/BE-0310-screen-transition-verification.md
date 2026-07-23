**English** · [日本語](BE-0310-screen-transition-verification.ja.md)

# BE-0310 — on-device verification: the screen-transition signal

This is the on-device confirmation
[BE-0310](../../roadmaps/BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md)
names as its gate (Unit 5): whether `BajutsuScreen`'s `viewDidAppear` swizzle actually reports a
transition, on both showcase toolkits, for the transitions the readiness gate and the `settled` wait
now consult — and whether it stays silent for the two cases it deliberately does not cover. The fast gate
(`make check`) runs no Simulator, so this procedure is run by hand on a Mac; record the outcome in
this file (or a linked follow-up comment on the item) once you have.

## Prerequisites

- macOS with Xcode and a booted Simulator (`make -C demos/showcase run-swiftui` already works —
  see the repo root [`CLAUDE.md`](../../CLAUDE.md) for `make deps` / `make runner-build` setup).
- Both showcase apps already call `BajutsuNet.startIfEnabled()`
  ([`ShowcaseApp.swift`](ios/swiftui/Sources/ShowcaseApp.swift),
  [`AppDelegate.swift`](ios/uikit/Sources/AppDelegate.swift)), which now also activates
  `BajutsuScreen` — no app-code change is needed to run this procedure.

## What to confirm

For **both** `showcase-swiftui` and `showcase-uikit`:

1. **Cold launch → first screen.** The first screen's `viewDidAppear` should fire, giving
   readiness a signal at cold launch too — the first view controller appears just as a later push
   or tab switch does. Record the outcome; if it does not report, that is not a regression, only
   that readiness keeps using the BE-0218 ladder for that one moment, per the proposal.
2. **Navigation push** (`stable.row.3` → Horse Detail, [`navigation.yaml`](scenarios/navigation.yaml)).
3. **Modal presentation** (Log tab → detented sheet, [`modals.yaml`](scenarios/modals.yaml)).
4. **Tab switch** ([`tabs.yaml`](scenarios/tabs.yaml)).
5. **Not covered, by design — confirm the fallback's role from evidence:**
   - An **in-place data update** with no screen change: the Log tab's "Intense" toggle
     (`log.intense`), which mirrors state to an accessibility value with no navigation. It presents
     no new view controller, so `viewDidAppear` never fires and nothing is reported.
   - A **custom transition bypassing the standard containers**: neither showcase app has a screen
     built this way today. If you want to close this sub-case empirically rather than by code
     inspection, add a throwaway `UIView.transition(with:duration:options:animations:)` (UIKit) or
     a custom `AnyTransition` (SwiftUI) to a scratch screen, observe, then discard the change —
     don't commit a permanent showcase screen for a one-off check.

## Procedure A — via `bajutsu run` + the two new debug log lines

BE-0310 added two `_logger.debug(...)` lines for exactly this confirmation:
`bajutsu.platform_lifecycle.readiness` logs `"readiness satisfied by the screenChanged signal"`
when the new rung decides readiness, and `bajutsu.orchestrator.waits` logs `"settled via the
screen-transition signal (quiescence=...)"` when `settled` uses it. The CLI wires no `--verbose`
flag today, so raise the level with a one-line wrapper instead of `bajutsu run` directly:

```bash
cd /path/to/bajutsu
uv run python -c "
import logging
logging.basicConfig(level=logging.DEBUG, format='%(name)s: %(message)s')
from bajutsu.cli import main
main()
" run --target showcase-swiftui --udid "$(xcrun simctl list devices booted | grep -oE '[0-9A-F-]{36}' | head -1)" \
  --backend ios --config demos/showcase/showcase.config.yaml \
  --scenario demos/showcase/scenarios/navigation.yaml demos/showcase/scenarios/modals.yaml demos/showcase/scenarios/tabs.yaml
```

Repeat with `--target showcase-uikit`. Grep the run's output for the two debug lines above; their
presence confirms the signal decided readiness/settled instead of the tree-diff fallback for that
run. Their *absence* on a run that should have transitioned (push/modal/tab) is the failure signal
this procedure exists to catch.

## Procedure B — the SwiftUI XCUITest scaffold

[`ios/swiftui/UITests/BE0310ScreenTransitionSignalUITests.swift`](ios/swiftui/UITests/BE0310ScreenTransitionSignalUITests.swift)
is a hand-written (not `bajutsu codegen`-generated) scaffold that stands in for the real Python
collector with a minimal loopback HTTP listener, so it exercises the actual
`BAJUTSU_COLLECTOR` → `POST /transitions` wire path. It drives cold launch, the same push/modal/tab
flows as Procedure A, and the in-place-update case, asserting the transition count before/after
each. Run it with:

```bash
cd demos/showcase/ios/swiftui && xcodegen generate
xcodebuild test -project BajutsuShowcaseSwiftUI.xcodeproj -scheme UITests \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

(`make -C demos/showcase ui-test` runs the same scheme, but first regenerates
`ComponentsUITests.swift` via `bajutsu codegen` — harmless alongside this file, since both live
under the same `UITests` target's `sources:`.)

**UIKit has no `UITests` Xcode target today** — `demos/showcase/ios/uikit/project.yml` never grew
one (only SwiftUI's `BajutsuShowcaseSwiftUIUITests` exists, see its `project.yml`). Procedure A
(via `bajutsu run` + the debug log lines) is UIKit's primary confirmation path; adding a mirrored
`UITests` target to the UIKit project is a reasonable follow-up if a XCUITest-based check is wanted
there too, but is out of this item's scope to add blind.

## Recording the outcome

Once run, update
[BE-0310's Progress checklist](../../roadmaps/BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md#progress) —
check off Unit 5 and add a dated log line summarizing what fired and what didn't, on which
toolkit(s), mirroring the log format the other checklist entries use.
