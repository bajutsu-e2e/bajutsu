**English** · [日本語](README.ja.md)

# Showcase — Android (Compose + Views)

The Android half of the Bajutsu showcase dogfood suite, built ahead of the
[BE-0007 Android backend](../../../roadmaps/BE-0007-android-backend/BE-0007-android-backend.md)
as the fixture that backend will drive. The behavior, element inventory, launch-env hooks,
deeplinks, and OS-alert placement are defined once in [`../SPEC.md`](../SPEC.md) and implemented
here element-for-element, mirroring the iOS pair: **Jetpack Compose** is the SwiftUI twin,
**Android Views** the UIKit twin, and each ships in two accessibility flavors.

## Four products from two codebases

The flavor difference is a single Gradle product flavor pair (`a11y` / `noax`) driving one
`BuildConfig.ACCESSIBLE` boolean; there is no forked source. The gated helpers in each module's
`Accessibility.kt` (SPEC §8) attach an identifier — and mirror state for assertions — only in
the `a11y` flavor.

| Gradle task | Toolkit | `ACCESSIBLE` | Application id | Display name | Deeplink scheme |
|---|---|---|---|---|---|
| `:compose:assembleA11yDebug` | Compose | true | `com.bajutsu.showcase.android.compose` | Showcase Compose | `showcasecompose` |
| `:compose:assembleNoaxDebug` | Compose | false | `com.bajutsu.showcase.android.compose.noax` | Showcase Compose (no a11y) | `showcasecomposenoax` |
| `:views:assembleA11yDebug` | Views | true | `com.bajutsu.showcase.android.views` | Showcase Views | `showcaseviews` |
| `:views:assembleNoaxDebug` | Views | false | `com.bajutsu.showcase.android.views.noax` | Showcase Views (no a11y) | `showcaseviewsnoax` |

The `a11y` builds expose the SPEC §5 element inventory as UI Automator `resource-id`s; the
`noax` builds compile to a tree with none — the cost of skipping accessibility, made concrete
(`doctor --target` will grade the pair Ready / Blocked once BE-0007 lands).

## How identifiers surface (the two id conventions)

The two modules deliberately exercise BE-0007's two Android id paths:

- **Compose** — `Modifier.aid("stable.refresh")` sets a `testTag`, and the content root sets
  `testTagsAsResourceId = true`, so UI Automator surfaces the tag as `resource-id`. A `testTag`
  accepts any string, so the dotted SPEC §5 ids reproduce **verbatim** and the shared
  [`../scenarios/`](../scenarios) set can drive this app unchanged.
- **Views** — `View.aid("stable_refresh")` assigns a real `android:id` (declared in
  [`views/src/main/res/values/ids.xml`](views/src/main/res/values/ids.xml)), which UI Automator
  surfaces natively. An `android:id` name allows neither `.` nor `-`, so the SPEC ids map
  mechanically: both become `_` (`stable.refresh` → `stable_refresh`,
  `search.results-empty` → `search_results_empty`). The shared [`../scenarios/`](../scenarios) set
  still drives Views unchanged because each selector **lists both id forms** —
  `id: [stable.refresh, stable_refresh]` matches whichever the tree renders, an OR resolved by the
  deterministic core (BE-0221). The id convention stays **explicit in the scenario**, never a hidden
  driver-side `.` ↔ `_` rewrite, and the fixture exposes the honest native convention rather than
  papering over it.

State mirroring is the same in both toolkits: each mirrors the value to `content-desc`, the channel
a `uiautomator dump` exposes (Compose's `stateDescription` is not in the dump). See SPEC §2.1.

## Launch-env hooks and deeplinks

`launchEnv` (SPEC §3) arrives as **intent extras** on the launcher Activity — BE-0007's launch
sequence passes them via `am start` — read once at launch: `SHOWCASE_UITEST`, `SHOWCASE_TAB`,
`SHOWCASE_API_URL`, `SHOWCASE_HTTP_BASE`. Deeplinks (SPEC §4) use the per-product scheme above
with the shared host grammar (`…://stable` … `…://permissions`); the launcher Activity is
`singleTask`, so a deeplink selects the tab in the running app and pops any pushed detail.

The OS-alert fixture (SPEC §7) maps to Android's **runtime-permission dialogs**: the
Permissions tab's two buttons raise the `POST_NOTIFICATIONS` and `ACCESS_FINE_LOCATION`
prompts — out-of-process system UI, exactly the alert-guard fixture the iOS SpringBoard
prompts provide. Run on an **API 33+ emulator**: `POST_NOTIFICATIONS` is a runtime permission
only on Android 13+, so the notification prompt does not appear on older images (SPEC §2.1).
`SHOWCASE_UITEST` disables animations device-wide via the driver on Android, not in-app (SPEC §2.1).

## Build

Requires a JDK (17+) and the Android SDK. The Gradle wrapper is committed, so no separate Gradle
install is needed — `./gradlew` (via the Makefile) downloads the pinned Gradle on first use.

```bash
make -C demos/showcase/android build-all   # all four APKs
make -C demos/showcase/android compose-build      # just the Compose a11y product
make -C demos/showcase/android views-noax-build   # just the Views no-a11y product
```

Each APK lands at `<module>/build/outputs/apk/<flavor>/debug/`, exactly where
[`../showcase.config.yaml`](../showcase.config.yaml)'s android targets point their `appPath`.
`run` / `doctor` against these targets need the BE-0007 adb backend; until it lands,
`--backend android` fails with the registry's "not implemented yet".
