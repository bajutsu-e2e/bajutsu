**English** · [日本語](BE-XXXX-android-identifiertool-ja.md)

# BE-XXXX — IdentifierTool: a standalone Android identifier library

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-android-identifiertool.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md), [BE-0283](../BE-0283-android-network-capture/BE-0283-android-network-capture.md), [BE-0355](../BE-0355-native-z-position/BE-0355-native-z-position.md) |
<!-- /BE-METADATA -->

## Introduction

An Android app needs specific plumbing to give bajutsu a stable `resource-id` or `content-desc`.
Today, that plumbing lives purely inside the [showcase](../../demos/showcase/android) app's own
source. No library ships it. Every Android app wanting the same identifiers writes its own copy
from scratch.

This item adds **IdentifierTool**, a new library at the repo root. It sits beside
[`BajutsuKit`](../../BajutsuKit) and [`BajutsuAndroid`](../../BajutsuAndroid), as its own directory
and its own Gradle module. It has no dependency on `BajutsuAndroid`, and `BajutsuAndroid` has none
on it. An app that wants identifiers alone adds one dependency: IdentifierTool. That dependency
never pulls in `BajutsuAndroid`'s clipboard or network-capture code. It never shares a release with
them either.

## Motivation

Android hides two pieces of knowledge that any app needs to give bajutsu a working identifier. The
platform itself documents neither piece, and both invite mistakes.

The first piece concerns Compose. A `Modifier.testTag(id)` surfaces as UI Automator's `resource-id`
attribute under one condition alone. That condition is `testTagsAsResourceId = true`, set through
Compose's `semantics {}` block. An app must set that flag again inside every modal window: a
`ModalBottomSheet`, a `Dialog`. Each modal starts its own semantics subtree. The root's flag never
reaches it. A skipped modal dumps an empty `resource-id`, with no warning at build or run time.

The second piece concerns Views. A `View`'s id must already exist as a declared `android:id`
resource. That declaration is what lets `resources.getIdentifier(name, "id", packageName)` resolve
it. An undeclared name resolves to `0`. That reads as "this view carries no id," not as a plain
typo.

Neither issue is unique to the showcase. Any Android app addressing its UI by a stable identifier
hits both. Today, the showcase's own source is where both workarounds live. An app onboarding
bajutsu has to read that source. It then copies the relevant lines by hand. This item removes that
step.

A reader can check the outcome once this item ships. Write a new Android app. Add the
IdentifierTool Gradle dependency. Call the two or three functions it adds. Read back a
`uiautomator dump`. It reports the expected `resource-id`, with no line copied from the showcase.

## Detailed design

### Where IdentifierTool lives

IdentifierTool lives at `IdentifierTool/`, a new top-level directory. It holds two files:

- `BajutsuAccessibility.kt` — the View-based helpers: `View.accessibilityId(name)` and
  `View.accessibilityStateValue(value)`. This item ports both from the Views showcase's `aid` /
  `stateValue`.
- `BajutsuAccessibilityCompose.kt` — the Compose-based helpers: `Modifier.accessibilityId(id)`,
  `Modifier.accessibilityStateValue(value)`, and `Modifier.enableAccessibilityIds()`. This item
  ports all three from the Compose showcase's `aid` / `stateValue` / `enableTestTagsAsResourceId`.

Every ported function drops its `BuildConfig.ACCESSIBLE` check. See "Gating stays outside the
library" below. The Compose showcase's `selectedState` does not move. See "Alternatives
considered."

A consumer using a single toolkit still adds one dependency, not two. The two files sit in one
module. The toolkits stay separable in code review this way, without forcing two artifacts on any
consumer. `IdentifierTool`'s functions are plain extension functions, not `@Composable` functions.
`build.gradle.kts` needs `androidx.compose.ui` as `compileOnly` for that reason. It needs no Compose
compiler plugin. A consumer using Views alone sees no build-size change after adding the dependency.

### No dependency on BajutsuAndroid

`View.accessibilityId` does not call `BajutsuZOrder.report`, unlike the showcase's current `aid()`.
`BajutsuZOrder` lives in `BajutsuAndroid`
([BE-0355](../BE-0355-native-z-position/BE-0355-native-z-position.md)). Calling it from
IdentifierTool would reintroduce the exact dependency edge this item removes.

A consumer wanting both an identifier and z-order reporting adds both dependencies. It then
composes both calls itself:

```kotlin
// demos/showcase/android/views/src/main/java/com/bajutsu/showcase/views/Accessibility.kt
fun <T : View> T.aid(name: String): T {
    if (BuildConfig.ACCESSIBLE) {
        accessibilityId(name)
        BajutsuZOrder.report(this)
    }
    return this
}
```

The showcase keeps this composition, so its own behavior does not change. A consumer wanting
identifiers alone skips the second call. Its build never links `BajutsuAndroid` at all.

### Gating stays outside the library

Neither `accessibilityId` nor `accessibilityStateValue` checks any flag; both tag unconditionally.
`Bajutsu.startClipboard`, in `BajutsuAndroid`, already works this way. BajutsuKit's Swift
counterpart does too. [`AccessibilityID.swift`](../../demos/showcase/ios/swiftui/Sources/AccessibilityID.swift)
wraps SwiftUI's own `.accessibilityIdentifier(_:)`. The wrap sits inside the showcase app's own
`#if ACCESSIBLE` condition. `BajutsuKit` itself carries no matching gate.

A compiled library cannot read a field named `BuildConfig.ACCESSIBLE`. That field would live on an
arbitrary consumer's own classpath. Android generates that class per application module, under the
consumer's own package. IdentifierTool carries no symbol it could have referenced ahead of time, as
a result.

A library could still express that gate two ways. Both cost more than they are worth:

- **Reflection.** Look up a field name assumed present on the consumer. R8 can strip that field.
  IdentifierTool has no way to ship a keep rule on the consumer's behalf. What breaks becomes a
  missing identifier, with no warning raised, not a build error.
- **A matching Gradle flavor.** Give IdentifierTool its own product flavor. Ask every consumer to
  add a flavor of the same name. That turns the goal, one dependency and two calls, into something
  else. It becomes a mandatory build-file change for every adopting app.

Leaving the decision to the caller avoids both costs. A consuming app wraps the unconditional
function in a short check of its own choosing:

- a `BuildConfig` field
- a Gradle build type
- no check at all, when identifiers should always be present in its instrumented build

### The showcase keeps its own gate, unchanged for callers

The showcase's two `Accessibility.kt` files stop implementing the tagging logic themselves.
Instead, they delegate to IdentifierTool's functions. That delegation sits behind the existing
`BuildConfig.ACCESSIBLE` check. The Views example above shows this for `aid`. Every call site under
`demos/showcase/android` keeps calling `.aid(...)` / `.stateValue(...)` unchanged. By grep, that is
122 call sites across 18 files. The showcase's own function names and signatures stay the same.
Their bodies alone move.

The showcase becomes IdentifierTool's first consumer. It keeps exercising the new functions through
the existing `android-e2e.yml` emulator lane. The current test coverage carries over, as a result.
IdentifierTool needs no dedicated unit-test suite. A live UI Automator dump is what gives the
behavior meaning. The emulator lane already produces that dump.

### A platform limit the library cannot remove

Views' `accessibilityId` still resolves ids through Android's own lookup. That lookup is
`resources.getIdentifier(name, "id", context.packageName)`. A consuming app must still declare
every id name it passes. Each one needs an `android:id` resource, declared ahead of time. The
showcase's own `res/values/ids.xml` is one example.

UI Automator's `resource-id` field exists purely for a view whose id carries a resource entry name.
An id made at run time has no such entry — one from `View.generateViewId()`, for instance.
`resources.getIdentifier` returning `0` for an undeclared name is Android's own constraint. This
item cannot close that gap by better packaging.

A Views-based consumer still writes its own `ids.xml`. It needs one entry per string it passes to
`accessibilityId`. A Compose-based consumer pays no such cost. `testTag` accepts any string at run
time and needs no resource declaration.

### Documentation

`IdentifierTool/README.md` and `README.ja.md` follow the shape of `BajutsuAndroid`'s own README.
They cover:

- why in-app tagging needs a library at all
- how to integrate it
- the caveat above, on `ids.xml`, specific to Views
- IdentifierTool ships no clipboard or network-capture code
- a consumer wanting those still adds `BajutsuAndroid` separately

Two other docs gain the same cross-reference: [`docs/architecture.md`](../../docs/architecture.md)
and [`docs/drivers.md`](../../docs/drivers.md). Each already mentions the showcase's
`Accessibility.kt` files. Each gains a mention of IdentifierTool next to it. A reader following the
adb id convention then also reaches the library. Today that reader reaches the showcase's own copy
alone.

### Work breakdown (MECE: mutually exclusive, collectively exhaustive)

1. Create `IdentifierTool/` as a new Gradle library module, mirroring `BajutsuAndroid`'s own
   `build.gradle.kts` shape (namespace, `compileSdk`, `minSdk`) but with no dependency on it.
2. Add `BajutsuAccessibility.kt` with `View.accessibilityId(name)` and
   `View.accessibilityStateValue(value)`, ported from the Views showcase, ungated.
3. Add `BajutsuAccessibilityCompose.kt`. Port `Modifier.accessibilityId(id)` and
   `Modifier.accessibilityStateValue(value)` from the Compose showcase, ungated.
   `Modifier.enableAccessibilityIds()` moves the same way. Add `androidx.compose.ui` as
   `compileOnly`.
4. Update `demos/showcase/android/settings.gradle.kts` to include IdentifierTool by path. It already
   includes `BajutsuAndroid` the same way.
5. Rewrite the showcase's two `Accessibility.kt` files. They delegate to IdentifierTool's functions,
   behind the existing `BuildConfig.ACCESSIBLE` check. The Views file also composes
   `BajutsuZOrder.report` explicitly, as shown above. Keep `aid`, `stateValue`, `selectedState`, and
   `enableTestTagsAsResourceId` as local names; none of the 122 existing call sites change.
6. Write `IdentifierTool/README.md` and `README.ja.md`, in the shape described above.
7. Update the `docs/architecture.md` and `docs/drivers.md` cross-references to name IdentifierTool.
8. Verify the change two ways. First, build both flavors of both showcase modules, using
   `demos/showcase/android`'s existing Gradle tasks. Second, run the `android-e2e.yml` CI lane
   against the migrated showcase. That lane alone provides the coverage this moved logic needs.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Add the helpers to `BajutsuAndroid` instead of a new module | This was the original design in an earlier draft of this item. `BajutsuAndroid`'s Views half already depends on `BajutsuZOrder.report`, so the two capabilities looked related enough to share a module. But bundling identifiers with clipboard and network capture forces one release train on three unrelated capabilities, and a consumer wanting only identifiers still links the clipboard receiver's code. A separate module removes both costs, at the price of one more directory and one more Gradle include for the showcase to wire up. |
| Keep `BajutsuZOrder.report` wired into `View.accessibilityId`, and let IdentifierTool depend on `BajutsuAndroid` for it | This preserves the showcase's one-call convenience, but it reintroduces exactly the dependency this item removes: any consumer of IdentifierTool would transitively pull in `BajutsuAndroid`'s clipboard receiver. Composing the two calls at the showcase's own call site costs one extra line, once, and keeps the two libraries independent. |
| Move `BajutsuZOrder` itself into IdentifierTool | `BajutsuZOrder` is a general position-reporting capability, not an identifier-tagging one; the showcase's own code already calls it from places `accessibilityId` does not reach. Moving it would not remove a dependency — it would only relocate where the coupling lives, and it would pull an unrelated capability out of `BajutsuAndroid` for no gain. |
| Let the library read a same-named `BuildConfig.ACCESSIBLE` field on the consumer, through reflection | A library artifact does not know a consumer's package name ahead of time. R8 can also strip the field a reflective lookup depends on. IdentifierTool has no keep rule it can ship on the consumer's behalf. What breaks becomes a missing identifier, with no warning raised, not a build error. |
| Give IdentifierTool its own product flavor dimension, matched by name on every consumer | Gradle's variant-aware dependency resolution can match a library's flavor to a consumer's flavor of the same name, so this is technically realizable. But it asks every consumer to add a flavor dimension with the library's exact name and values, merely to get the off switch. That is a heavier ask than the wrapper function this item ships instead. |
| Port `selectedState` into IdentifierTool alongside `accessibilityId` and `accessibilityStateValue` | `selectedState` is `Modifier.semantics { selected = true }`, with no Android-specific plumbing behind it, unlike `testTagsAsResourceId` or `resources.getIdentifier`. A consuming app loses nothing by writing those three words itself. |

## Progress

- [ ] Not started.

## References

- [`BajutsuAndroid/README.md`](../../BajutsuAndroid/README.md)
- [`BajutsuKit/README.md`](../../BajutsuKit/README.md)
- [`demos/showcase/android/compose/src/main/java/com/bajutsu/showcase/compose/Accessibility.kt`](../../demos/showcase/android/compose/src/main/java/com/bajutsu/showcase/compose/Accessibility.kt)
- [`demos/showcase/android/views/src/main/java/com/bajutsu/showcase/views/Accessibility.kt`](../../demos/showcase/android/views/src/main/java/com/bajutsu/showcase/views/Accessibility.kt)
- [`demos/showcase/ios/swiftui/Sources/AccessibilityID.swift`](../../demos/showcase/ios/swiftui/Sources/AccessibilityID.swift)
- [`docs/drivers.md`](../../docs/drivers.md#adb-android)
