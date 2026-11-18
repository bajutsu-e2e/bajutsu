# IdentifierTool

English · [日本語](README.ja.md)

IdentifierTool is the in-app identifier support library for [bajutsu](../) on Android. It is a
standalone Android library that gives bajutsu a stable `resource-id` or `content-desc`. It covers the
plumbing Android hides for that purpose: a Compose helper (`testTag` + `testTagsAsResourceId`) and a
Views helper (`resources.getIdentifier` against a declared `android:id`).

The library has **no dependency on [`BajutsuAndroid`](../BajutsuAndroid)** (BE-0233's clipboard /
network-capture library), and `BajutsuAndroid` has none on it. An app that wants identifiers alone adds
one dependency. That dependency never pulls in `BajutsuAndroid`'s clipboard receiver or network
interceptor, and the two never share a release.

## Why in-app support

An app needs two pieces of knowledge to give bajutsu a working identifier. Android hides both, and the
platform documents neither:

- **Compose.** A `Modifier.testTag(id)` surfaces as UI Automator's `resource-id` attribute only when
  `testTagsAsResourceId = true` is set through Compose's `semantics {}` block. The flag must be set
  again inside every modal window (`ModalBottomSheet`, `Dialog`), because each modal starts its own
  semantics subtree and the root's flag never reaches it. A skipped modal dumps an empty `resource-id`
  with no warning at build or run time.
- **Views.** A `View`'s id must already exist as a declared `android:id` resource. That declaration
  lets `resources.getIdentifier(name, "id", packageName)` resolve the name. An undeclared name resolves
  to `0`, which reads as "this view carries no id", not as a typo.

## Integrate

To use the library as a Gradle module, include it by path. The showcase does this in
[`demos/showcase/android/settings.gradle.kts`](../demos/showcase/android/settings.gradle.kts):

```kotlin
include(":identifier-tool")
project(":identifier-tool").projectDir = file("../../../IdentifierTool")
```

Then depend on the module: `implementation(project(":identifier-tool"))`. [`sample/`](sample/README.md)
is a minimal, standalone app built this way, with no other bajutsu library alongside it.

Call the helpers that match your UI toolkit. The Views half lives in `dev.bajutsu.identifier`, and the
Compose half lives in `dev.bajutsu.identifier.compose`. With this split, a Views-only consumer never
imports a symbol it has no Compose classpath to resolve.

```kotlin
// Views
import dev.bajutsu.identifier.accessibilityId
import dev.bajutsu.identifier.accessibilityStateValue

view.accessibilityId("stable_refresh")
view.accessibilityStateValue("loading")
```

```kotlin
// Compose
import dev.bajutsu.identifier.compose.accessibilityId
import dev.bajutsu.identifier.compose.accessibilityStateValue
import dev.bajutsu.identifier.compose.enableAccessibilityIds

Modifier
    .enableAccessibilityIds() // at the content root, and again inside every modal window
    .accessibilityId("stable.refresh")
    .accessibilityStateValue("loading")
```

Every function tags unconditionally, and none of them checks a flag. A library artifact cannot read a
field like `BuildConfig.ACCESSIBLE`, which Gradle generates per application module on an arbitrary
consumer's own classpath. The on/off decision is therefore yours. Wrap the calls in your own check:
a `BuildConfig` field, a Gradle build type, or no check at all when identifiers should always be
present in your instrumented build.

## The `android:id` caveat (Views only)

`accessibilityId` still resolves ids through Android's own lookup,
`resources.getIdentifier(name, "id", context.packageName)`. A Views-based consumer must therefore
declare every id name it passes as an `android:id` resource ahead of time. The showcase's own
[`ids.xml`](../demos/showcase/android/views/src/main/res/values/ids.xml) shows the pattern.

UI Automator's `resource-id` field exists purely for a view whose id carries a resource entry name. An
id made at run time (`View.generateViewId()`, for instance) has none. This is Android's own constraint,
and no packaging can close the gap.

A Compose-based consumer pays no such cost: `testTag` accepts any string at run time and needs no
resource declaration.

## What this doesn't ship

IdentifierTool ships no clipboard or network-capture code, and `View.accessibilityId` reports no
z-order. A consumer that wants those adds [`BajutsuAndroid`](../BajutsuAndroid) as a separate
dependency and composes the calls itself. The showcase's own
[`Accessibility.kt`](../demos/showcase/android/views/src/main/java/com/bajutsu/showcase/views/Accessibility.kt)
shows the pattern.
