**English** · [日本語](SPEC.ja.md)

# Bajutsu Showcase — fixture specification

> The single source of truth for the showcase dogfood targets. The UIKit and SwiftUI
> codebases both implement *this* spec, identifier-for-identifier, so one scenario set
> drives every variant. Read it before changing any app source. Design rationale:
> [`DESIGN.md`](../../DESIGN.md); roadmap item: the `dogfood-showcase-apps` BE item.

## 1. Purpose

The showcase is Bajutsu's **next-generation dogfood target** — the practice ground for
`record` (Tier 1 authoring), `crawl` (Tier 1 exploration, [BE-0038](../../roadmaps/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)),
and `run` (Tier 2 deterministic gate). It deliberately packs the interaction surface a real
app has — tabbed + navigation + modal screen transitions, text entry, gestures, async
loading, networking (live + mockable), and a screen that intentionally raises OS-level
alerts — into the smallest coherent app that still tells that whole story.

It is the **single iOS fixture** (BE-0079 retired the older `demo`/`sample`/`sample2` apps).
Where a single-variant app is one codebase, the showcase ships **the same app written twice**
(UIKit and SwiftUI) and **each in two accessibility variants**, to make a teaching point
Bajutsu's whole design rests on visible:

| Variant | Accessibility identifiers | Demonstrates |
|---|---|---|
| **`*-a11y`** (identifiers ON) | every actionable element carries a stable `accessibilityIdentifier` | `run` — id-based selectors resolve uniquely, scenarios replay deterministically; `doctor --target` grades **Ready** |
| **`*-noax`** (identifiers OFF) | no identifiers at all | `record` — the AI author must fall back down the stability ladder ([DESIGN §5](../../DESIGN.md)) to `label`/`traits`/coordinates; `doctor --target` grades **Blocked**. The cost of skipping accessibility, made concrete |

> **Why the pairing matters.** Selector stability is the determinism lever
> ([DESIGN §2](../../DESIGN.md)). The `-a11y` ↔ `-noax` twins are the controlled experiment:
> same app, same flows, identifiers the only difference. Run the same goal through `record`
> against both and the diff *is* the value of accessibility work.

## 2. App matrix

The iOS matrix below is 2 codebases × 2 build variants = 4 products; §2.1 adds the 4 Android twins.
Each toolkit is **one codebase, two build targets**. The variant difference is a single Swift
active-compilation condition, `ACCESSIBLE`; there is no forked source. (See §8.)

| App name (`targets.<name>`) | Toolkit | `ACCESSIBLE` | Bundle id | Deeplink scheme | Display name |
|---|---|---|---|---|---|
| `showcase-swiftui` | SwiftUI | defined | `com.bajutsu.showcase.ios.swiftui` | `showcaseswiftui` | Showcase SwiftUI |
| `showcase-swiftui-noax` | SwiftUI | — | `com.bajutsu.showcase.ios.swiftui.noax` | `showcaseswiftuinoax` | Showcase SwiftUI (no a11y) |
| `showcase-uikit` | UIKit | defined | `com.bajutsu.showcase.ios.uikit` | `showcaseuikit` | Showcase UIKit |
| `showcase-uikit-noax` | UIKit | — | `com.bajutsu.showcase.ios.uikit.noax` | `showcaseuikitnoax` | Showcase UIKit (no a11y) |

The two `-a11y` apps MUST expose **byte-for-byte identical** identifier sets, launch-env hooks,
and deeplinks, so `demos/showcase/scenarios/*.yaml` runs unchanged against either. The UIKit and
SwiftUI views may differ in construction but never in the contract below.

### 2.1 Android twins ([`android/`](android/), BE-0007 preparation)

The same fixture exists for Android — built ahead of the
[BE-0007 Android backend](../../roadmaps/BE-0007-android-backend/BE-0007-android-backend.md)
as the app pair that backend will drive. **Jetpack Compose** mirrors the SwiftUI codebase,
**Android Views** mirrors UIKit, and the a11y/noax pair is one Gradle flavor switch
(`BuildConfig.ACCESSIBLE`) — no forked source, exactly like `ACCESSIBLE` on iOS.

| App name (`targets.<name>`) | Toolkit | `ACCESSIBLE` | Application id | Deeplink scheme | Display name |
|---|---|---|---|---|---|
| `showcase-compose` | Compose | true | `com.bajutsu.showcase.android.compose` | `showcasecompose` | Showcase Compose |
| `showcase-compose-noax` | Compose | false | `com.bajutsu.showcase.android.compose.noax` | `showcasecomposenoax` | Showcase Compose (no a11y) |
| `showcase-views` | Views | true | `com.bajutsu.showcase.android.views` | `showcaseviews` | Showcase Views |
| `showcase-views-noax` | Views | false | `com.bajutsu.showcase.android.views.noax` | `showcaseviewsnoax` | Showcase Views (no a11y) |

The §5 contract is the shared **logical** inventory; how each platform surfaces it differs only
in the channel (the BE-0007 selector mapping):

| iOS (§5, §8) | Android Compose | Android Views |
|---|---|---|
| `accessibilityIdentifier` | `testTag` → `resource-id` (`testTagsAsResourceId`), dotted ids **verbatim** | `android:id` → `resource-id`, ids with `.`/`-` → `_` (`stable.refresh` → `stable_refresh`) |
| `accessibilityValue` mirror | `content-desc` | `content-desc` |
| `launchEnv` via `ProcessInfo` | intent extras | intent extras |
| deeplink scheme + host | VIEW intent-filter, same host grammar (§4) | same |
| SpringBoard alerts (§7) | runtime-permission dialogs (`POST_NOTIFICATIONS`, `ACCESS_FINE_LOCATION`) | same |
| `UIPasteboard` round-trip (§5.4) | `ClipboardManager` | same |

Both Android toolkits mirror the state value to `content-desc` (not Compose's `stateDescription`,
which a `uiautomator dump` does not expose). Two Android-only carve-outs from the shared contract:

- **`SHOWCASE_UITEST` (disable animations, §3).** On iOS the app itself disables animations; on Android
  animations are disabled device-wide by the driver (`adb shell settings put global
  animator_duration_scale 0`), so the app reads the hook but takes no in-app action.
- **The notification prompt (§7) needs API 33+.** `POST_NOTIFICATIONS` is a runtime permission only on
  Android 13 (API 33) and later; on older emulators the prompt never appears. Run the fixture on an
  API 33+ emulator so the alert-guard flow has a prompt to guard.

Because Compose testTags reproduce the dotted ids verbatim, the shared [`scenarios/`](scenarios)
set drives `showcase-compose` unchanged. The Views ids are underscore-mapped (an `android:id`
name allows neither `.` nor `-`); the shared set drives `showcase-views` unchanged too because each
selector lists **both** id forms — `id: [stable.refresh, stable_refresh]` matches whichever the tree
renders, an OR resolved by the deterministic core, keeping the id convention explicit in the scenario
rather than a driver-side `.` ↔ `_` rewrite (BE-0221). Networking goes through **OkHttp** with
`BajutsuAndroid`'s interceptor, so `network` evidence works on Android too (BE-0283, §6); `mocks`
stay a follow-up. Everything else below holds for all four Android products.

## 3. Launch environment hooks

Driven via `launchEnv` ([DESIGN §6.1](../../DESIGN.md)). All are read once at launch from
`ProcessInfo`. Prefix `SHOWCASE_`.

| Variable | Effect | Default |
|---|---|---|
| `SHOWCASE_UITEST` | disable animations (tight condition waits) | unset |
| `SHOWCASE_API_URL` | base URL for the catalog GET (`/horses`) | `https://example.com` |
| `SHOWCASE_HTTP_BASE` | base for the echo POST/DELETE endpoints | `https://httpbin.org` |

> There is **no auth gate**: the app launches straight into the tab UI, always on the Stable tab.
> Every other tab is reached by tapping the native tab bar, which the XCUITest backend does by
> label (BE-0107 retired the `SHOWCASE_TAB` launch shortcut, which the retired idb backend needed because it could not tap the tab bar, BE-0290). The
> catalog is **fixed** at five horses — there is no launch-env seed knob (BE-0079): a scenario
> observes the app's own data, it cannot inject a data state. Likewise there is no launch-env
> shortcut onto a *pushed* screen (see §4).

## 4. Deeplinks

Scheme is per-variant (§2); the host grammar is shared. A deeplink **selects a tab** (and
dismisses modals / pops that tab to root); it does **not** push a detail screen (BE-0079). A
detail is reached only by tapping its catalog row, so there is no shortcut straight onto a
pushed screen.

| Deeplink (host) | Effect |
|---|---|
| `…://stable` / `search` / `log` / `notices` / `permissions` | select that tab |

## 5. Screen-by-screen specification

A **five-tab main UI, no auth gate** — the app launches straight into the tabs. Every actionable
element's identifier is listed; the `-noax` variants omit all of them. Identifiers follow
[DESIGN §7.3](../../DESIGN.md): `<namespace>.<element>`, lowercase, data-derived, unique per
screen. State is mirrored to `accessibilityValue` (in `-a11y`) so assertions read it.

### Screen inventory

| # | Screen | Reached via | Kind | Namespace(s) | Spec |
|---|---|---|---|---|---|
| 1 | Stable (catalog list) | `stable` tab | tab · list | `stable` | §5.1 |
| 2 | Horse Detail | Stable row | push | `horse` | §5.1 |
| 3 | Search | `search` tab | tab · filter list | `search` | §5.2 |
| 4 | Log | `log` tab | tab · form + modals | `log` | §5.3 |
| 5 | — Filter sheet | `log.openFilter` | sheet (detents) | `log` | §5.3 |
| 6 | — Gallery cover | `log.openGallery` | full-screen cover | `log` | §5.3 |
| 7 | — Delete dialog | `log.openDelete` | action sheet | `log` | §5.3 |
| 8 | Notices (list) | `notices` tab | tab · long list (scroll) | `notice` | §5.5 |
| 9 | Notice Detail | Notices row | push | `notice` | §5.5 |
| 10 | Permissions | `permissions` tab / `…://permissions` | tab · **OS alerts** + pasteboard round-trip | `perm`, `sys` | §5.4 |

Tabs, left to right: **Stable · Search · Log · Notices · Permissions**.

Each tab item is itself identified by its own namespace root — `stable`, `search`, `log`,
`notice`, `perm` — with no `.`/`-`, so the one literal `id` selects it on iOS, Compose, and Views
alike (`scenarios/tabs.yaml`); the `-noax` variants carry none, as elsewhere.

### 5.1 Tab: Stable — `stable`, `horse` namespaces

A `NavigationStack` (SwiftUI) / `UINavigationController` (UIKit). Catalog list with async load.

> **Screen titles carry no id.** On iOS 26 a navigation-bar title is not exposed as an
> addressable accessibility element (only its buttons are), so the tab screens have **no**
> `<ns>.title` identifier — they use a plain `navigationTitle` / `titleView` for display only.
> A scenario confirms it is on a screen via a screen-unique **content leaf** (e.g. `stable.row.1`
> or `stable.status` for Stable, `search.field` for Search, `perm.requestNotif` for Permissions).
> The detail screens keep `horse.title` / `notice.detail.title` — those are real content (the
> entity's name shown in the body), not a nav-bar title.

- `stable.refresh` — toolbar/button that re-fetches the catalog (GET `SHOWCASE_API_URL` + `/horses`); sets `stable.status` value to `loading` → `done`/`error`
- `stable.status` — text; `accessibilityValue` = `idle`/`loading`/`done`/`error`
- `stable.row.<horseId>` — one per catalog row, `<horseId>` data-derived (e.g. `stable.row.3`). Tapping pushes Horse Detail. Use `idMatches: "stable.row.*"` + `count` for set assertions.
- `stable.empty` — shown only when the catalog is empty; the catalog is fixed non-empty (BE-0079), so this is defensive markup, not a reachable state in the showcase

**Horse Detail** (pushed by tapping a Stable row):
- `horse.title` — the horse's name
- `horse.id.value` — id, mirrored to value
- `horse.fetch` — button: GET detail (`/horses/<id>`); `horse.status` value `loading`→`done`/`error`
- `horse.status` — value as above
- `horse.favorite` — toggle; `selected` trait reflects state; mirrored to `horse.favorite.value` (`on`/`off`)
- **Back** — the standard system back button (pushed by the navigation stack). The backend drives it by its OS-provided id `BackButton`; there is no app-defined back id.

### 5.2 Tab: Search — `search` namespace

- `search.field` — search field; filters the same catalog by name, case-insensitive
- `search.row.<horseId>` — filtered rows (same id scheme as Stable rows but `search.` namespace)
- `search.count` — text, `accessibilityValue` = number of matches
- `search.empty` — `search.results-empty`, shown when the query matches nothing
- `search.clear` — clears the field

### 5.3 Tab: Log — `log` namespace (forms + modals)

A training-log composer exercising every input control and every modal style.

- `log.note` — multiline text field
- `log.count` — stepper for a numeric count; `log.count.value` mirrors the number
- `log.intense` — a button-backed toggle "Intense" (the retired idb backend could not flip a native Toggle/UISwitch on iOS 26, BE-0290); `log.intense.value` = `on`/`off`
- `log.segment.<one|two|three>` — a button-backed segmented control (the retired idb backend could not switch a native `Picker(.segmented)` / `UISegmentedControl` on iOS 26, BE-0290); the selected button carries the `selected` trait and the choice mirrors to `log.segment.value` (`one`/`two`/`three`, default `one`)
- `log.submit` — button: POST to `SHOWCASE_HTTP_BASE` + `/post` with the note/count as JSON; on success shows `log.toast` (auto-dismiss ~1.2 s → exercises `wait until gone`) and appends a row
- `log.status` — value `idle`/`loading`/`done`/`error`
- `log.row.<n>` — submitted entries

Dedicated gesture targets (a long-press and a double-tap, each mirroring its result so a
scenario can assert the gesture landed; both start below the form's fold, so a run scrolls
them into view first):
- `log.longpress` — long-press target; `log.longpress.value` = `idle`/`pressed`
- `log.doubletap` — double-tap target; `log.doubletap.value` mirrors the tap count (`0`, `1`, …)

Modals reachable from Log (the four presentation styles):
- `log.openFilter` → **sheet** with detents: `log.sheet.title`, `log.sheet.apply`, `log.sheet.close`
- `log.openGallery` → **fullScreenCover**: `log.cover.title`, `log.cover.close`
- `log.openDelete` → **action sheet** (a custom overlay of plain buttons, not a confirmationDialog / UIAlertController, whose actions the retired idb backend could not drive on iOS 26, BE-0290): choices `log.dialog.archive`, `log.dialog.delete` (destructive), `log.dialog.cancel`; result mirrored to `log.dialog.value` (`none`/`archive`/`delete`)
- `log.toast` — the transient toast described above

### 5.4 Tab: Permissions — `perm`, `sys` namespaces (**the OS-integration screen**)

A `NavigationStack` (SwiftUI) / `UINavigationController` (UIKit). **The one screen that
intentionally raises OS-level alerts** (§7) — promoted to a top-level tab so the alert-guard
flow is reached directly — plus a System section with an in-app pasteboard round-trip.

- `perm.requestNotif` — button → `UNUserNotificationCenter.requestAuthorization`. Raises the **SpringBoard notification prompt** (out-of-process; an in-app accessibility query cannot see it — cleared by the run's vision alert guard, or tapped "Allow" via `alertHandling`).
- `perm.notif.value` — `notDetermined`/`authorized`/`denied`
- `perm.notif.authorized` — element shown only once granted (gives the run a positive condition to wait for)
- `perm.requestLocation` — button → `CLLocationManager.requestWhenInUseAuthorization`. Raises the **system location prompt** (also SpringBoard).
- `perm.location.value` — `notDetermined`/`authorizedWhenInUse`/`denied`

**System** — an in-app pasteboard round-trip, mirroring state the backend's app-scoped query cannot
otherwise observe. It stays in-app because reading a pasteboard seeded by another process trips
iOS's paste-permission prompt; a value this app itself wrote reads back silently:
- `sys.copy` — button that writes a known string (`bajutsu-clip`) to the pasteboard
- `sys.paste` — button that reads the pasteboard back into `sys.paste.value`
- `sys.paste.value` — the pasted text; a scenario taps `sys.copy` then `sys.paste` and asserts it

### 5.5 Tab: Notices — `notice` namespace (long list → detail, scroll-to-element)

A `NavigationStack` (SwiftUI) / `UINavigationController` (UIKit) holding a plain vertical list of
**20** static notices, seeded identically in both apps (ids `1…20`, title "Notice `<id>`"). The
list is **intentionally longer than one screen**: the bottom rows start *off-screen*, and because
rows render lazily an off-screen row is **not in the accessibility tree at all** until scrolled
into view. So `notice.row.20` is the canonical **scroll-to-element** target — a scenario must
`swipe` the list (each swipe scrolls a fraction of the screen, §6.2) until the row appears, then `tap` it.
A plain list → detail flow distinct from the data-loading Stable catalog; a clean target for
navigation, scroll, and crawl scenarios.

- `notice.row.<id>` — one per *visible* notice (`notice.row.1` … the off-screen tail appears only after scrolling), `<id>` data-derived. Tapping pushes Notice Detail. (Don't assert a fixed `count` over `notice.row.*` — only the on-screen rows are in the tree, which is device-dependent.)

**Notice Detail** (pushed by tapping a Notices row):
- `notice.detail.title` — the notice's title (the screen's identifying element; the nav title carries no id)
- `notice.detail.body` — the notice's body text
- **Back** — the standard system back button; the backend drives it by its OS-provided id `BackButton` (see §5.1).

## 6. Networking

Uses the standard in-app collector integration (iOS: BajutsuKit; Android: BajutsuAndroid):

- **iOS** — the app links **BajutsuKit** and calls `BajutsuNet.startIfEnabled()` at launch (a no-op
  unless `BAJUTSU_COLLECTOR` is injected). All requests then flow through the interceptor, so
  `network` evidence and `mocks` work without app changes ([DESIGN §3.2](../../DESIGN.md)).
- **Android** — the app links **BajutsuAndroid**, calls `BajutsuNet.configure(launchEnv)` at launch,
  and adds `BajutsuNet.interceptor()` to its OkHttp client (BE-0283). `network` evidence then works
  the same way; `mocks` stay a follow-up, and capture is OkHttp-only (the `URLSession`-only bound's
  Android twin).
- Endpoints: catalog GET `SHOWCASE_API_URL` (default `https://example.com`), detail GET
  `<base>/horses/<id>`, log POST `SHOWCASE_HTTP_BASE/post`. Each request mirrors its status to
  the relevant `*.status` `accessibilityValue` so a scenario can `wait` on the response before
  asserting.
- A request deliberately carries a secret header (`Authorization: Bearer …`) and body field
  (`password`) so redaction has something to mask ([DESIGN §9](../../DESIGN.md)).

## 7. OS-alert policy (deliberate, scoped)

> Requirement: OS alerts (push-notification permission, location) are **off by default**,
> and present **only on a specific screen**.

- **No launch-time prompts.** The app never requests notification/location authorization at
  startup — only on explicit taps inside the **Permissions** tab (§5.4).
- **The deliberate alerts** live only on the **Permissions** tab: the notification prompt and the
  location prompt. Both are SpringBoard (out-of-process) — invisible to any in-app accessibility query —
  so they are the canonical fixture for the run's **vision alert guard** / `alertHandling`
  ([`permission.yaml`](scenarios/permission.yaml) is this fixture's scenario).

## 8. The `ACCESSIBLE` build flag (how the variants share one codebase)

A single Swift active-compilation condition, `ACCESSIBLE`, set on the `-a11y` target only.

The helpers are named to echo Apple's own API (`accessibilityIdentifier` / `accessibilityValue`)
without shadowing it: `accessibilityID(_:)` attaches the identifier, `accessibilityStateValue(_:)`
mirrors state into `accessibilityValue`.

**SwiftUI** — `View` helpers apply the identifier / value only when the flag is set:

```swift
extension View {
    /// Attach a stable accessibility identifier in the a11y build; no-op otherwise.
    func accessibilityID(_ id: String) -> some View {
        #if ACCESSIBLE
        return AnyView(self.accessibilityIdentifier(id))
        #else
        return AnyView(self)
        #endif
    }

    /// Mirror state into accessibilityValue (a11y build only) so assertions can read it.
    func accessibilityStateValue(_ value: String) -> some View {
        #if ACCESSIBLE
        return AnyView(self.accessibilityValue(value))
        #else
        return AnyView(self)
        #endif
    }
}
```

**UIKit** — an extension on `UIAccessibilityIdentification` (which `UIView`/`UIBarItem` conform to),
plus an `accessibilityStateValue(_:)` on `UIView`/`UIBarItem`:

```swift
extension UIAccessibilityIdentification {
    /// Set a stable accessibility identifier in the a11y build; no-op otherwise.
    @discardableResult func accessibilityID(_ id: String) -> Self {
        #if ACCESSIBLE
        accessibilityIdentifier = id
        #endif
        return self
    }
}
```

Every identifier in §5 is applied through `accessibilityID(...)`. The state-mirroring
`accessibilityStateValue(...)` (and any `accessibilityLabel` that exists **for assertions**) is
likewise gated behind `#if ACCESSIBLE`; labels that exist purely for VoiceOver semantics may stay
unconditional. The `-noax` build therefore
presents a tree with no identifiers and no mirrored values — exactly the app a team that
skipped accessibility ships, and exactly what `record` must cope with and `doctor` must flag.

## 9. Identifier namespace summary (`idNamespaces`)

For the `-a11y` apps' `targets.<name>.idNamespaces` ([DESIGN §7.3](../../DESIGN.md)). There are no
reserved (shared cross-screen) namespaces; the back control is the OS-provided system back button
(id `BackButton`), outside the app's namespaces.

```
stable, horse, search, log, notice, perm, sys, net
```

The `-noax` apps declare an **empty** `idNamespaces: []` — an honest declaration that the build
exposes no identifiers, which is what makes `doctor --target showcase-…-noax` grade **Blocked** on
`idCoverage` rather than appearing to "pass".

## 10. What each Bajutsu command demonstrates against this fixture

| Command | Variant | Story |
|---|---|---|
| `run` | `-a11y` | Deterministic replay of every scenario in `scenarios/` — tabs, push nav, all four modal styles, networking (live + mocked), and the alert-guarded Permissions flow. |
| `doctor --target` | both | `-a11y` → **Ready**; `-noax` → **Blocked** (`idCoverage` ≈ 0). The pair quantifies accessibility debt. |
| `record` | `-noax` | AI authors a scenario for a natural-language goal against an app with no identifiers, falling to label/traits/coordinates — the stability-ladder cost made visible. The `-a11y` twin shows the clean id-based output for the same goal. |
| `crawl` ([BE-0038](../../roadmaps/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)) | `-a11y` | Breadth-first exploration over a genuinely branchy app (5 tabs × pushes × 4 modal styles) → a screen map; the id-based state fingerprint is stable because §5 identifiers are. (Forward-looking: lands when BE-0038 ships.) |
