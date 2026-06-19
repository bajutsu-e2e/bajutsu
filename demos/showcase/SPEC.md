**English** · [日本語](SPEC.ja.md)

# Bajutsu Showcase — fixture specification

> The single source of truth for the showcase dogfood apps. The UIKit and SwiftUI
> codebases both implement *this* spec, identifier-for-identifier, so one scenario set
> drives every variant. Read it before changing any app source. Design rationale:
> [`DESIGN.md`](../../DESIGN.md); roadmap item: the `dogfood-showcase-apps` BE item.

## 1. Purpose

The showcase is Bajutsu's **next-generation dogfood target** — the practice ground for
`record` (Tier 1 authoring), `crawl` (Tier 1 exploration, [BE-0038](../../roadmaps/proposals/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)),
and `run` (Tier 2 deterministic gate). It deliberately packs the interaction surface a real
app has — tabbed + navigation + modal screen transitions, text entry, gestures, async
loading, networking (live + mockable), and a screen that intentionally raises OS-level
alerts — into the smallest coherent app that still tells that whole story.

It supersedes the older `sample` fixture ([`demos/features/app`](../features/app)). Where
`sample` is one SwiftUI app, the showcase ships **the same app written twice** (UIKit and
SwiftUI) and **each in two accessibility variants**, to make a teaching point Bajutsu's whole
design rests on visible:

| Variant | Accessibility identifiers | Demonstrates |
|---|---|---|
| **`*-a11y`** (identifiers ON) | every actionable element carries a stable `accessibilityIdentifier` | `run` — id-based selectors resolve uniquely, scenarios replay deterministically; `doctor --app` grades **Ready** |
| **`*-noax`** (identifiers OFF) | no identifiers at all | `record` — the AI author must fall back down the stability ladder ([DESIGN §5](../../DESIGN.md)) to `label`/`traits`/coordinates; `doctor --app` grades **Blocked**. The cost of skipping accessibility, made concrete |

> **Why the pairing matters.** Selector stability is the determinism lever
> ([DESIGN §2](../../DESIGN.md)). The `-a11y` ↔ `-noax` twins are the controlled experiment:
> same app, same flows, identifiers the only difference. Run the same goal through `record`
> against both and the diff *is* the value of accessibility work.

## 2. App matrix (2 codebases × 2 build variants = 4 products)

Each toolkit is **one codebase, two build targets**. The variant difference is a single Swift
active-compilation condition, `ACCESSIBLE`; there is no forked source. (See §8.)

| App name (`apps.<name>`) | Toolkit | `ACCESSIBLE` | Bundle id | Deeplink scheme | Display name |
|---|---|---|---|---|---|
| `showcase-swiftui` | SwiftUI | defined | `com.bajutsu.showcase.swiftui` | `showcaseswiftui` | Showcase SwiftUI |
| `showcase-swiftui-noax` | SwiftUI | — | `com.bajutsu.showcase.swiftui.noax` | `showcaseswiftuinoax` | Showcase SwiftUI (no a11y) |
| `showcase-uikit` | UIKit | defined | `com.bajutsu.showcase.uikit` | `showcaseuikit` | Showcase UIKit |
| `showcase-uikit-noax` | UIKit | — | `com.bajutsu.showcase.uikit.noax` | `showcaseuikitnoax` | Showcase UIKit (no a11y) |

The two `-a11y` apps MUST expose **byte-for-byte identical** identifier sets, launch-env hooks,
and deeplinks, so `demos/showcase/scenarios/*.yaml` runs unchanged against either. The UIKit and
SwiftUI views may differ in construction but never in the contract below.

## 3. Launch environment hooks

Driven via `launchEnv` ([DESIGN §6.1](../../DESIGN.md)). All are read once at launch from
`ProcessInfo`. Prefix `SHOWCASE_`.

| Variable | Effect | Default |
|---|---|---|
| `SHOWCASE_UITEST` | disable animations (tight condition waits) | unset |
| `SHOWCASE_SKIP_ONBOARDING` | start at login instead of onboarding | unset |
| `SHOWCASE_LOGGED_IN` | start logged-in, on Home | unset |
| `SHOWCASE_TAB` | initial tab: `stable`/`search`/`log`/`profile` | `stable` |
| `SHOWCASE_SEED` | number of seeded catalog rows (offline) | `5` |
| `SHOWCASE_API_URL` | base URL for the catalog GET (`/horses`) | `https://example.com` |
| `SHOWCASE_HTTP_BASE` | base for the echo POST/DELETE endpoints | `https://httpbin.org` |

> Onboarding and login are **suppressible** so most scenarios start at the screen under test —
> the same clean-state injection the `sample` fixture uses.

## 4. Deeplinks

Scheme is per-variant (§2); the host grammar is shared. Opening any deeplink also dismisses
modals and pops navigation to the tab root.

| Deeplink (host) | Effect |
|---|---|
| `…://stable` / `search` / `log` / `profile` | select that tab |
| `…://horse/<id>` | select Stable tab, push Horse Detail for `<id>` |
| `…://permissions` | select Profile tab, push the Permissions screen (the OS-alert screen) |

## 5. Screen-by-screen specification

Five flows: **Onboarding → Login** (modal gate), then a four-tab main UI. Every actionable
element's identifier is listed; the `-noax` variants omit all of them. Identifiers follow
[DESIGN §7.3](../../DESIGN.md): `<namespace>.<element>`, lowercase, data-derived, unique per
screen. State is mirrored to `accessibilityValue` (in `-a11y`) so assertions read it.

### 5.0 Auth gate — `onboarding`, `auth` namespaces

A `fullScreenCover`/modal over the main UI while `screen != home`, exactly like the `sample`
app's `AuthFlowView`.

**Onboarding** (skipped when `SHOWCASE_SKIP_ONBOARDING`/`SHOWCASE_LOGGED_IN` set):
- `onboarding.title` — heading "Welcome"
- `onboarding.continue` — button → advances to Login

**Login:**
- `auth.email` — email text field
- `auth.password` — secure text field. **Must NOT set `textContentType = .password`/`.newPassword`** — that is what suppresses the iOS "Save Password?" system sheet (§7).
- `auth.submit` — button. Empty email or password → show `auth.error`; otherwise dismiss keyboard and go Home.
- `auth.error` — validation message (only when present)

### 5.1 Tab: Stable — `stable`, `horse` namespaces

A `NavigationStack` (SwiftUI) / `UINavigationController` (UIKit). Catalog list with async load.

- `stable.title` — nav title "Stable"
- `stable.refresh` — toolbar/button that re-fetches the catalog (GET `SHOWCASE_API_URL` + `/horses`); sets `stable.status` value to `loading` → `done`/`error`
- `stable.status` — text; `accessibilityValue` = `idle`/`loading`/`done`/`error`
- `stable.row.<horseId>` — one per catalog row, `<horseId>` data-derived (e.g. `stable.row.3`). Tapping pushes Horse Detail. Use `idMatches: "stable.row.*"` + `count` for set assertions.
- `stable.empty` — shown only when the catalog is empty (`SHOWCASE_SEED=0` and no network rows)

**Horse Detail** (pushed; also reachable via `…://horse/<id>`):
- `horse.title` — the horse's name
- `horse.id.value` — id, mirrored to value
- `horse.fetch` — button: GET detail (`/horses/<id>`); `horse.status` value `loading`→`done`/`error`
- `horse.status` — value as above
- `horse.favorite` — toggle; `selected` trait reflects state; mirrored to `horse.favorite.value` (`on`/`off`)
- `nav.back` — back button (reserved `nav` namespace; the system back button gets this id explicitly)

### 5.2 Tab: Search — `search` namespace

- `search.title` — nav title "Search"
- `search.field` — search field; filters the same catalog by name, case-insensitive
- `search.row.<horseId>` — filtered rows (same id scheme as Stable rows but `search.` namespace)
- `search.count` — text, `accessibilityValue` = number of matches
- `search.empty` — `search.results-empty`, shown when the query matches nothing
- `search.clear` — clears the field

### 5.3 Tab: Log — `log` namespace (forms + modals)

A training-log composer exercising every input control and every modal style.

- `log.title` — nav title "Log"
- `log.note` — multiline text field
- `log.count` — stepper for a numeric count; `log.count.value` mirrors the number
- `log.intense` — toggle "Intense"; `log.intense.value` = `on`/`off`
- `log.submit` — button: POST to `SHOWCASE_HTTP_BASE` + `/post` with the note/count as JSON; on success shows `log.toast` (auto-dismiss ~1.2 s → exercises `wait until gone`) and appends a row
- `log.status` — value `idle`/`loading`/`done`/`error`
- `log.row.<n>` — submitted entries

Modals reachable from Log (the four presentation styles):
- `log.openFilter` → **sheet** with detents: `log.sheet.title`, `log.sheet.apply`, `log.sheet.close`
- `log.openGallery` → **fullScreenCover**: `log.cover.title`, `log.cover.close`
- `log.openDelete` → **confirmationDialog / action sheet**: choices `log.dialog.archive`, `log.dialog.delete` (destructive), `log.dialog.cancel`; result mirrored to `log.dialog.value` (`none`/`archive`/`delete`)
- `log.toast` — the transient toast described above

### 5.4 Tab: Profile — `profile`, `account`, `perm`, `about` namespaces (navigation + OS alerts)

A `Form`/grouped list that pushes sub-screens — the navigation-depth showcase.

- `profile.title` — nav title "Profile"
- `profile.normalize` — toggle "Normalize"; `profile.normalize.value` = `on`/`off`; flipping sets `profile.changed`
- `profile.changed` — text shown after any settings change
- `profile.openAccount` → push **Account**
- `profile.openPermissions` → push **Permissions** (the OS-alert screen)
- `profile.openAbout` → push **About**

**Account** (`account`):
- `account.title`, `account.email.value` (the logged-in email, mirrored), `account.logout` (→ returns to Login gate)

**Permissions** (`perm`) — **the one screen that intentionally raises OS-level alerts** (§7):
- `perm.title`
- `perm.requestNotif` — button → `UNUserNotificationCenter.requestAuthorization`. Raises the **SpringBoard notification prompt** (out-of-process; idb cannot see it — cleared by the run's vision alert guard, or tapped "Allow" via `dismissAlerts`).
- `perm.notif.value` — `notDetermined`/`authorized`/`denied`
- `perm.notif.authorized` — element shown only once granted (gives the run a positive condition to wait for)
- `perm.requestLocation` — button → `CLLocationManager.requestWhenInUseAuthorization`. Raises the **system location prompt** (also SpringBoard).
- `perm.location.value` — `notDetermined`/`authorizedWhenInUse`/`denied`

**About** (`about`):
- `about.title`, `about.version.value`, `nav.back`

## 6. Networking

Mirrors the `sample` fixture's BajutsuKit integration:

- The app links **BajutsuKit** and calls `BajutsuNet.startIfEnabled()` at launch (a no-op unless
  `BAJUTSU_COLLECTOR` is injected). All requests then flow through the interceptor, so `network`
  evidence and `mocks` work without app changes ([DESIGN §3.2](../../DESIGN.md)).
- Endpoints: catalog GET `SHOWCASE_API_URL` (default `https://example.com`), detail GET
  `<base>/horses/<id>`, log POST `SHOWCASE_HTTP_BASE/post`. Each request mirrors its status to
  the relevant `*.status` `accessibilityValue` so a scenario can `wait` on the response before
  asserting.
- A request deliberately carries a secret header (`Authorization: Bearer …`) and body field
  (`password`) so redaction has something to mask ([DESIGN §9](../../DESIGN.md)).

## 7. OS-alert policy (deliberate, scoped)

> Requirement: OS alerts (push-notification permission, password-save) are **off by default**,
> and present **only on a specific screen**.

- **No launch-time prompts.** The app never requests notification/location authorization at
  startup — only on explicit taps inside the **Permissions** screen (§5.4).
- **No password-save sheet.** The login secure field does **not** set
  `textContentType = .password`/`.newPassword` and the form is not an AutoFill-recognized
  login, so iOS does not offer "Save Password?". This is the deliberate *suppression*.
- **The deliberate alerts** live only on **Permissions**: the notification prompt and the
  location prompt. Both are SpringBoard (out-of-process) — invisible to idb's app-scoped query —
  so they are the canonical fixture for the run's **vision alert guard** / `dismissAlerts`
  ([`permission.yaml`](../features/app/scenarios/permission.yaml) is the existing precedent).

## 8. The `ACCESSIBLE` build flag (how the variants share one codebase)

A single Swift active-compilation condition, `ACCESSIBLE`, set on the `-a11y` target only.

**SwiftUI** — a `View` helper applies the identifier only when the flag is set:

```swift
extension View {
    /// Attach a stable accessibility identifier in the a11y build; no-op otherwise.
    func aid(_ id: String) -> some View {
        #if ACCESSIBLE
        return AnyView(self.accessibilityIdentifier(id))
        #else
        return AnyView(self)
        #endif
    }
}
```

**UIKit** — an extension on `UIAccessibilityIdentification` (which `UIView`/`UIBarItem` conform to):

```swift
extension UIAccessibilityIdentification {
    /// Set a stable accessibility identifier in the a11y build; no-op otherwise.
    @discardableResult func aid(_ id: String) -> Self {
        #if ACCESSIBLE
        accessibilityIdentifier = id
        #endif
        return self
    }
}
```

Every identifier in §5 is applied through `aid(...)`. `accessibilityValue`/`accessibilityLabel`
that mirror **state for assertions** are likewise gated behind `#if ACCESSIBLE`; labels that
exist purely for VoiceOver semantics may stay unconditional. The `-noax` build therefore
presents a tree with no identifiers and no mirrored values — exactly the app a team that
skipped accessibility ships, and exactly what `record` must cope with and `doctor` must flag.

## 9. Identifier namespace summary (`idNamespaces`)

For the `-a11y` apps' `apps.<name>.idNamespaces` ([DESIGN §7.3](../../DESIGN.md)). Reserved
shared namespaces `auth` and `nav` come from `defaults.reservedNamespaces`.

```
onboarding, auth, nav, stable, horse, search, log, profile, account, perm, about, net
```

The `-noax` apps declare an **empty** `idNamespaces: []` — an honest declaration that the build
exposes no identifiers, which is what makes `doctor --app showcase-…-noax` grade **Blocked** on
`idCoverage` rather than appearing to "pass".

## 10. What each Bajutsu command demonstrates against this fixture

| Command | Variant | Story |
|---|---|---|
| `run` | `-a11y` | Deterministic replay of every scenario in `scenarios/` — tabs, push nav, all four modal styles, networking (live + mocked), and the alert-guarded Permissions flow. |
| `doctor --app` | both | `-a11y` → **Ready**; `-noax` → **Blocked** (`idCoverage` ≈ 0). The pair quantifies accessibility debt. |
| `record` | `-noax` | AI authors a scenario for a natural-language goal against an app with no identifiers, falling to label/traits/coordinates — the stability-ladder cost made visible. The `-a11y` twin shows the clean id-based output for the same goal. |
| `crawl` ([BE-0038](../../roadmaps/proposals/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)) | `-a11y` | Breadth-first exploration over a genuinely branchy app (4 tabs × pushes × 4 modal styles) → a screen map; the id-based state fingerprint is stable because §5 identifiers are. (Forward-looking: lands when BE-0038 ships.) |
