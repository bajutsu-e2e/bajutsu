# IdentifierTool

English · [日本語](README.ja.md)

In-app identifier support for [bajutsu](../) on Android. A standalone Android library that gives
bajutsu a stable `resource-id` or `content-desc`, covering the plumbing Android hides for that: a
Compose helper (`testTag` + `testTagsAsResourceId`) and a Views helper
(`resources.getIdentifier` against a declared `android:id`).

It has **no dependency on [`BajutsuAndroid`](../BajutsuAndroid)** (BE-0233's clipboard / network-
capture library), and `BajutsuAndroid` has none on it. An app wanting identifiers alone adds one
dependency; that dependency never pulls in `BajutsuAndroid`'s clipboard receiver or network
interceptor, and the two never share a release.

## Why in-app support

Android hides two pieces of knowledge that any app needs to give bajutsu a working identifier, and
neither is documented by the platform itself:

- **Compose.** A `Modifier.testTag(id)` surfaces as UI Automator's `resource-id` attribute only
  when `testTagsAsResourceId = true` is set through Compose's `semantics {}` block. That flag must
  be set again inside every modal window (`ModalBottomSheet`, `Dialog`): each modal starts its own
  semantics subtree, so the root's flag never reaches it, and a skipped modal dumps an empty
  `resource-id` with no warning at build or run time.
- **Views.** A `View`'s id must already exist as a declared `android:id` resource — that
  declaration is what lets `resources.getIdentifier(name, "id", packageName)` resolve it. An
  undeclared name resolves to `0`, which reads as "this view carries no id," not as a typo.

## Integrate

As a Gradle module, include it by path (the showcase does this in
[`demos/showcase/android/settings.gradle.kts`](../demos/showcase/android/settings.gradle.kts)):

```kotlin
include(":identifier-tool")
project(":identifier-tool").projectDir = file("../../../IdentifierTool")
```

then depend on it: `implementation(project(":identifier-tool"))`.

Call the helpers that match your UI toolkit:

```kotlin
// Views
import dev.bajutsu.identifier.accessibilityId
import dev.bajutsu.identifier.accessibilityStateValue

view.accessibilityId("stable_refresh")
view.accessibilityStateValue("loading")
```

```kotlin
// Compose
import dev.bajutsu.identifier.accessibilityId
import dev.bajutsu.identifier.accessibilityStateValue
import dev.bajutsu.identifier.enableAccessibilityIds

Modifier
    .enableAccessibilityIds() // at the content root, and again inside every modal window
    .accessibilityId("stable.refresh")
    .accessibilityStateValue("loading")
```

Every function tags unconditionally — none of them check a flag. A library artifact cannot read a
field like `BuildConfig.ACCESSIBLE`, generated per application module on an arbitrary consumer's own
classpath, so the on/off decision is yours: wrap the calls in your own check (a `BuildConfig` field,
a Gradle build type, or no check at all when identifiers should always be present in your
instrumented build).

## The `android:id` caveat (Views only)

`accessibilityId` still resolves ids through Android's own lookup,
`resources.getIdentifier(name, "id", context.packageName)`, so a Views-based consumer must declare
every id name it passes as an `android:id` resource ahead of time — see the showcase's own
[`ids.xml`](../demos/showcase/android/views/src/main/res/values/ids.xml) for the pattern. UI
Automator's `resource-id` field exists purely for a view whose id carries a resource entry name; an
id made at run time (`View.generateViewId()`, for instance) has none, and this is Android's own
constraint — no packaging can close that gap. A Compose-based consumer pays no such cost: `testTag`
accepts any string at run time and needs no resource declaration.

## What this doesn't ship

IdentifierTool ships no clipboard or network-capture code, and `View.accessibilityId` reports no
z-order. A consumer wanting those adds [`BajutsuAndroid`](../BajutsuAndroid) as a separate
dependency and composes the calls itself — the showcase's own
[`Accessibility.kt`](../demos/showcase/android/views/src/main/java/com/bajutsu/showcase/views/Accessibility.kt)
shows the pattern.
