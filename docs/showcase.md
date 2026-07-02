**English** · [日本語](ja/showcase.md)

# The showcase suite (the single iOS fixture)

> Bajutsu's iOS test fixture lives under [`demos/showcase/`](../demos/showcase). It is **the same
> app written twice** — UIKit and SwiftUI — and **each in an accessibility-on / -off variant**, so
> four products (`showcase-swiftui`, `showcase-swiftui-noax`, `showcase-uikit`, `showcase-uikit-noax`)
> come from two codebases. It packs the interaction surface a real app has (five tabs with push
> navigation, all four modal styles, text entry, gestures, async loading, live + mockable
> networking, an OS-alert screen) into the smallest coherent app that still tells that whole story.
>
> BE-0079 made it the **single** iOS fixture, retiring the older `demo` / `sample` / `sample2` apps.
> The authoritative, screen-by-screen contract — every identifier, every scenario mapping — is
> [`demos/showcase/SPEC.md`](../demos/showcase/SPEC.md); this page summarizes how to reach it.

Related: [scenarios](scenarios.md) · [configuration](configuration.md) · [codegen](codegen.md) · [cli](cli.md)

---

## Why two toolkits × two accessibility variants

The showcase makes two axes Bajutsu's design rests on visible:

- **Toolkit axis** (UIKit vs SwiftUI) — the two `-a11y` products expose an *identical* identifier
  contract, so the shared [`demos/showcase/scenarios/`](../demos/showcase/scenarios) runs unchanged
  against either. What differs is the element tree the backend sees, which is exactly what a
  cross-toolkit driver must absorb.
- **Accessibility axis** (`-a11y` ↔ `-noax`) — the `-noax` builds carry **no** identifiers
  (`idNamespaces: []`). They are the controlled experiment for selector stability (DESIGN §5): the
  same goal recorded against both shows the value of accessibility work as a concrete diff, and they
  are the `record` / `doctor` "missing accessibility" subjects.

## Build and run

Registered as four `targets.<name>` in [`demos/showcase/showcase.config.yaml`](../demos/showcase/showcase.config.yaml)
(bundle ids `com.bajutsu.showcase.{swiftui,uikit}[.noax]`, deeplink schemes `showcase{swiftui,uikit}[noax]`).
Built with XcodeGen + xcodebuild (`project.yml` is the source of truth; `.xcodeproj` / `build/` are
gitignored).

```bash
make -C demos/showcase swiftui-build       # compile the SwiftUI a11y product for the Simulator
make -C demos/showcase run-swiftui         # build → install → bajutsu run (idb) against a booted Simulator
make -C demos/showcase doctor              # the accessibility A/B: a11y grades Ready, -noax Blocked
make -C demos/showcase ui-test             # the codegen path: scenario → XCUITest → xcodebuild test
```

`bajutsu run` / `serve` also build the app on demand via each target's `build` command, so a manual
build is rarely needed first.

## Launch-environment hooks

Driven via `launchEnv` and passed as `SIMCTL_CHILD_<NAME>` ([drivers](drivers.md#environment-management-simctl)).
There is deliberately **no** launch-env shortcut to a *screen* or a *data state* (BE-0079): the app
always launches on the Stable tab with a fixed catalog, so a scenario reaches every other screen by
driving the UI, and observes the app's own data rather than injecting one.

| Variable | Effect |
|---|---|
| `SHOWCASE_UITEST=1` | disable animations (keeps condition waits tight) |
| `SHOWCASE_TAB=<name>` | initial tab: `stable` (default) / `search` / `log` / `notices` / `permissions` |
| `SHOWCASE_API_URL` / `SHOWCASE_HTTP_BASE` | base URLs for the catalog GET and the echo POST/DELETE endpoints |

The full identifier catalog, the deeplink grammar, and the primitive-to-scenario mapping are in
[`demos/showcase/SPEC.md`](../demos/showcase/SPEC.md).
