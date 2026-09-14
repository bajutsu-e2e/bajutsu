**English** · [日本語](BE-XXXX-device-executor-shared-selector-core-ja.md)

# BE-XXXX — Share the on-device selector core between iOS and Android via Rust and UniFFI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-device-executor-shared-selector-core.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md), [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md), [BE-0405](../BE-0405-android-identifiertool/BE-0405-android-identifiertool.md), [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md), [BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md), [BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md), [BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md)
defines a device-side step-execution protocol for iOS and Android. It states that Swift and Kotlin
each need an independent copy of the host's selector-matching logic. It also states that the two
copies must resolve every selector exactly the way the host's own code does. This item proposes
writing that logic once, in Rust. Both platforms would call it through
[UniFFI](https://mozilla.github.io/uniffi-rs/)-generated bindings, replacing a hand-written Swift
port and a hand-written Kotlin port with two thin bindings around one compiled core.
[BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md)'s
iOS executor and
[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md)'s
Android executor would each call the same crate, instead of each carrying its own copy of `matches`,
`find_all`, and `resolve_unique`.

## Motivation

BE-0408's own design names the risk directly. The two device-side copies "must resolve every
selector to the same element the host's copy would." They must also fail "the same way on an
ambiguous match." BE-0409 and BE-0410 respond to that risk by ordering themselves: the iOS port
lands first, so "a gap this item's port surfaces does not have to be independently rediscovered by
both at once." That ordering lowers the cost of *rediscovering* a gap. It does not stop the two
ports from disagreeing with each other in the first place. Neither item has started its `find_all` /
`resolve_unique` port yet: BE-0409 waits on BE-0408 (now [Implemented](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md)), and BE-0410 waits on
BE-0409. No Swift or Kotlin implementation of either function exists today. That makes now the point
where one implementation costs less than two.

The functions this item would move are already pure data transformations, not platform glue.
`matches`, `find_all`, `resolve_unique`, and `_collapse_identical_duplicates` in
[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py) take
an `Element` list and a `Selector`. Both are plain dictionaries of strings, lists, and tuples. Each
function returns a bool, a filtered list, a single element, or a raised error. None of the four reads an
accessibility tree, injects a tap, or opens a socket. Every platform-specific step happens outside
them: on the Python driver today, and on both device executors once built. A function with no
platform dependency has no reason to exist in three independently maintained copies.

The risk of an independent port runs deeper than ordinary drift. `idMatches` runs Python's
`fnmatch.fnmatchcase`. `labelMatches` runs Python's `re.compile(...).search`. An independent Swift
port would reach for `NSRegularExpression` and Foundation's own glob handling. An independent Kotlin
port would reach for `java.util.regex` and its own. Three engines would then decide what a scenario
author's own pattern matches, each with its own rules for character classes, anchoring, and Unicode
properties. A selector that passes on the host and fails on a device executor surfaces as a flaky
scenario, not a loud error — the same is true of a selector that matches a different element there.
A wrong match looks the same as a right one from the caller's side. A shared Rust engine collapses
that count from three independent engines to two: Python's `re` and `fnmatch` stay the tested
reference on the host, and the Rust core becomes the one other pattern engine either device executor
runs.

Once built, a later reader can check the result directly.
BE-0408 already shipped the fixtures that would check it:
[`tests/fixtures/be0408/`](../../tests/fixtures/be0408/)'s 42 selector-resolution cases and 9
derived-label cases, replayed against the Python reference by
[`tests/test_selector_fixtures.py`](../../tests/test_selector_fixtures.py). Today's plan would run
those same fixtures separately against a Swift port and a Kotlin port, once each exists. Either port
could pass every fixture while still disagreeing with the other on a case the fixtures do not cover.
Adding a new selector rule — a new trait, a new fallback — becomes one Rust change both executors
pick up on their next binary update. Today's plan needs two hand-written changes, kept in step by
memory alone.

## Detailed design

**Implementation order.** This item extends the four-item sequence BE-0407 → BE-0408 → BE-0409 →
BE-0410. BE-0408 is [Implemented](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md)
([#1949](https://github.com/bajutsu-e2e/bajutsu/pull/1949)): the step this item replaces already
shipped, as prose rather than as code — a new *Porting contract for a device-side resolver* section
in [`docs/selectors.md`](../../docs/selectors.md), stating the field-level selector contract
(`within`, `idMatches`, the trait derivations, the duplicate-collapse key) precisely enough for two
independent Swift and Kotlin implementations to agree, backed by the fixture corpus Motivation
cites. This item does not replace an unstarted step; it replaces an already-shipped prose contract —
"a port must reproduce this by hand, correctly" — with a compiled crate neither port needs to
reproduce by hand at all. It lands as a follow-up to BE-0408, before BE-0409 or BE-0410 write any
platform-side matching code, since both would otherwise start hand-porting against a contract this
item intends to make moot. Landing this item also removes the reason BE-0409 and BE-0410 order
themselves against each other today: both would call the same already-verified crate, so neither
needs the other's hand-written port to go first. Once this item's crate and its two bindings exist,
BE-0409 and BE-0410 can proceed in either order, or in parallel.

**What the crate reimplements, and what stays host-only.** Nine functions from
[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py) are
reimplemented in Rust — the Python copies stay in place as the conformance suite's reference:
`matches`, `find_all`, `resolve_unique`, `_collapse_identical_duplicates`, `contains`,
`topmost_at_point`, `redirect_candidates`, `raise_if_covered`, and `frame_center`. Together they
cover selector resolution and the tap-occlusion check for `tap`, one of the actuation kinds BE-0408
moves to the device. Each is pure, the property Motivation already establishes: an
`Element`/`Selector` pair (or a list of them) goes in; a bool, an index or a list of indices, a
point, or a raised error comes out.

Two signatures change because of the FFI (foreign function interface) boundary itself, where
Python's object identity has no equivalent. First, `find_all` returns `Vec<u32>` — indices into the
caller's `elements` list — instead of a list of elements, and `resolve_unique` returns a single
`u32` index instead of an element. A UniFFI record crosses that boundary by value, so a returned
element would be a copy with no traceable link back to the platform handle (an `XCUIElement`, an
`AccessibilityNodeInfo`) the caller resolved it from; an index lets the caller look up both the
element and its own parallel handle from the list it built. Second, `topmost_at_point`,
`redirect_candidates`, and `raise_if_covered` take a `target_index: u32` in place of an element, for
the same reason: Python locates `target` inside `elements` by identity (`is`), specifically so two
content-identical elements — a known XCUITest duplicate registration — resolve to the one the caller
actually holds, not merely one that looks like it. An index preserves that distinction; a
value-copied record cannot. `redirect_candidates` also returns `Vec<u32>` rather than elements, for
that same reason on the way out: its caller actuates on the descendant it picks, the same way
`XcuitestDriver._tap_sole_reachable_descendant` looks each candidate up today as `handles[id(el)]` —
an identity lookup a value-copied record would break just as a returned `find_all` element would.
`topmost_at_point`'s `Element` return stays a copy: `raise_if_covered` only formats its identifier
and frame into a message, so nothing downstream needs to trace it back to a platform handle.

Selector pattern matching needs a named engine, not just a signature. `idMatches` runs Python's
`fnmatch.fnmatchcase`; `labelMatches` runs `re.compile(...).search`
([`_functions.py:112-122`](../../bajutsu/common/drivers/base/_functions.py)). The crate uses the
[`regex`](https://docs.rs/regex) crate for `labelMatches`, and reimplements `fnmatch`'s own
translation of a shell-glob pattern into a regular expression string for `idMatches` — the same
approach Python's `fnmatch` module itself takes internally — compiling the result with that same
`regex` crate, so the crate carries exactly one pattern engine rather than two. That translation
alone is not enough: `fnmatch.translate` emits an end anchor of `\Z`, which `regex` does not
recognize (it offers only `\z`), and `fnmatchcase` gets its start anchor from calling `re.match`,
not from the pattern itself, while `regex`'s own match call is unanchored. The crate's `idMatches`
therefore rewrites the translated pattern's `\Z` to `\z` and wraps the whole thing in `^(?:…)\z`
before compiling, reproducing [`docs/selectors.md`](../../docs/selectors.md)'s own instruction that
a port must reproduce Python's anchoring for each field rather than inherit the port language's own
default — fully anchored for `idMatches`, unanchored for `labelMatches`.

`labelMatches` carries its own gap `idMatches` does not: `regex` rejects several constructs Python's
`re` accepts — lookaround (`(?=…)`, `(?!…)`), backreferences, the `\Z` end-of-string anchor, atomic
groups and possessive quantifiers, and conditional patterns (`(?(id)yes|no)`) — and a pattern using
any of them compiles on the host and fails to compile in the crate, the same host/device
disagreement this item exists to remove, relocated rather than closed. A bare `$` is worse than a
rejection, because it compiles in both engines and means something different in each: Python's `$`
(without `MULTILINE`) also matches just before a trailing newline, while `regex`'s `$` matches only
at the true end of the haystack. `regex` has no lookahead to express Python's version, so the crate
rejects `$` outright rather than let it compile into a quietly different match. The crate treats
every construct in this paragraph as a distinct, loud failure at first use — folded into the shared
error enum below — rather than a silent non-match a caller cannot tell apart from "the element
isn't there." A case added to the fixture corpus (see Verification) pins one such pattern, so a
future change to either engine that reopens the gap fails the suite instead of surfacing as a flaky
scenario.

`gesture_anchor` is equally pure, but it stays out of this item's scope. BE-0408 moves `tap`, `type`,
`swipe`, and `scroll` to the device — not the two-finger `pinch` / `rotate` gestures `gesture_anchor`
computes an anchor for. Porting it now would build for a stage this item's own protocol has not
reached.

`deadline_ticks` and `wait_until` do not move either. Both implement the host's own polling loop
around a single-shot check, and BE-0408's stage 1 has the device poll *internally* instead of
exposing a poll primitive to the host. An on-device executor needs its own native, event-driven or
timed loop around the shared match function — not a ported copy of the host's poll loop.
`default_wait_for`, the single-shot check every backend's `wait_for` delegates to, needs no separate
port for the same reason: it is `find_all(...).len() >= 1`, which each executor's own native loop
expresses directly against the shared `find_all`. `id_candidates`, `validate_id_candidates`,
`permission_capability`, and `native_z_from_json` also stay in Python (see Data model for
`id_candidates`'s reason); the latter three because each serves scenario authoring or evidence
parsing on the host, work no device executor performs.

**Data model.** [`Element`](../../bajutsu/common/drivers/base/element.py) becomes a UniFFI
dictionary record, field for field: `identifier`, `label`, and `value` as optional strings; `traits`
as a string list; `frame` as a four-field `(x, y, w, h)` record; `nativeZ` as an optional double.
[`Selector`](../../bajutsu/common/drivers/base/selector.py) needs more than a rename. `id` and
`idMatches` each accept a single string or a list in Python (`str | list[str]`, BE-0221's
OR-candidate form); UniFFI has no such union type, so both become `Option<Vec<String>>` at the
record boundary — `None` when the field is absent, matching Python's own field-presence check
(`"id" in sel`), not an empty list standing in for absence. Wrapping a single value into a
one-element list is a one-line step trivial enough that
each caller does it itself when building the record — which is why `id_candidates`, the Python
helper that does the same wrapping today, stays host-only rather than moving into the crate. Within
the crate, `matches` and `find_all` see only the already-normalized list. `within` becomes
`Option<Box<Selector>>`, since a `Selector` can nest inside its own `within` field. `index` becomes
`Option<i32>`, not `u32`: Python accepts a negative value there, counting from the end
(`_functions.py:322-325`). A field's absence in Python's `total=False` dict becomes the same `None`
on both sides.

[`Trait`](../../bajutsu/common/drivers/base/trait.py)'s six string constants — `button`, `link`,
`notEnabled`, `selected`, `other`, `secureTextField` — pass through as plain strings on both sides.
That matches what the Python code and the JSON wire format already use, so a seventh constant added
later needs no new enum kept in step. [`ElementNotFound`](../../bajutsu/common/drivers/base/element_not_found.py),
[`AmbiguousSelector`](../../bajutsu/common/drivers/base/ambiguous_selector.py), and
[`ElementNotTappable`](../../bajutsu/common/drivers/base/element_not_tappable.py) become one UniFFI
error enum with a fourth variant, `UnsupportedPattern`, added alongside them for the pattern-engine
gap the previous section names — a `labelMatches` value the `regex` crate cannot compile, carrying
the field name and the pattern itself. Python raises nothing equivalent today, since `re.compile`
accepts every pattern this fourth variant exists for; a device executor is the only caller that can
hit it. The other three variants carry structured failure detail as typed fields, not a formatted
message string: `ElementNotFound` carries the selector and a `reason` of `noMatch` or `outOfRange` —
`resolve_unique` raises two distinct messages today, "the index is out of range" (`_functions.py:324`)
and "nothing matched" (`:327`), and BE-0408's own fixture corpus already names this same split
`resolveUnique.reason`, so the variant reuses that name rather than inventing a second one;
`AmbiguousSelector` carries the selector and the candidate count; `ElementNotTappable` carries the
selector plus the covering element's identifier, label, and frame — `raise_if_covered` formats
`covering["identifier"] or covering["label"] or "<unnamed>"` into its message
(`_functions.py:452`), so the variant needs both fields to reproduce that fallback, not identifier
alone. The host, not the device, renders these fields into the message text a run report shows
today; a Swift or Kotlin caller passes the variant back unformatted over
the existing evidence path, so a step failing on a device executor reads exactly like the same step
failing on the host does today.

`_collapse_identical_duplicates` ports with no added parameter: the crate's version compares frames
for exact equality, exactly like the host's, with no tolerance. `docs/selectors.md`'s porting
contract carries a second rule for this function beyond the tolerance, in its *The duplicate-collapse
key* section: the collapse keeps the *first* candidate carrying a given key, in `find_all`'s own
document order, and a plain hash map has no defined iteration order to preserve that with — the
contract names Swift's `Dictionary` as the concrete trap. Rust's `std::collections::HashMap` is the
same trap, and a sharper one: its default hasher reseeds per process, so iteration order can vary
between runs of the same binary, not only between platforms. The crate's port therefore uses an
insertion-ordered map (such as the `indexmap` crate's `IndexMap`) for the collapse, keyed the same
way the host's `seen` dict already is, rather than `HashMap`. `docs/selectors.md`'s porting
contract states why the tolerance itself must stay divergent, in a section titled *Two divergences a
port must keep, not close*.
[`PositionPath.swift`](../../BajutsuKit/Sources/BajutsuRunner/PositionPath.swift)'s
`resolvableMatchingIndex` and `attributesMatch` already diverge from this module on purpose: a point
of slack on each frame value, and an ordered-array trait comparison rather than a set. Both exist
because `resolvableMatchingIndex` answers a different question — whether an already-recorded element
handle, re-resolved through a fresh live read, is still the same element — while
`_collapse_identical_duplicates` answers "do these candidates from one atomic snapshot report
identical content." The contract's own text is explicit that a port "must not 'correct' either one
toward Python's own behavior." This item's crate therefore stops at `_collapse_identical_duplicates`
alone; `resolvableMatchingIndex` and `framesEqual` stay exactly as they are, in Swift, untouched by
this item, and no Progress step here should ever propose folding one into the other.

Android's derived-label fallback (`_derived_label` in
[`bajutsu/common/drivers/adb/_functions.py`](../../bajutsu/common/drivers/adb/_functions.py), applied
inside `_to_element`) stays at its current place in the pipeline. It computes a label onto the
`Element` *before*
the shared matching code sees it — on the Python driver today, and inside
`BajutsuAndroidUIAutomatorServer`'s Kotlin caller once BE-0410 lands. The shared crate needs no
Android-specific branch for this: every caller normalizes its own platform's raw reading into a plain
`Element` first, then calls the one shared core. This leaves one hand-written Kotlin port —
`_derived_label` itself has no Rust counterpart — that must still agree with Python's. Its output is
a single string with no further processing, and BE-0408's nine
[`android_derived_label.json`](../../tests/fixtures/be0408/android_derived_label.json) cases already
cover it, so the residual risk is far smaller than porting the whole matching path independently was.

**iOS: `BajutsuRunner`.** The package manifest is the repo-root
[`Package.swift`](../../Package.swift), not a file under `BajutsuKit/`: it has to sit at the clone
root for SwiftPM's git-based resolution, with every target's own `path:` pointing back into
`BajutsuKit/Sources/`. It already builds `BajutsuRunner` with a Swift Package Manager build plugin,
`OpenAPIGenerator`, that generates Swift source at build time, on every host that builds the
package. A `uniffi-bindgen`-generated Swift file is a second generated-source path alongside that
one, differing in one respect: its native half is a prebuilt `.xcframework` artifact, not something
the plugin compiles fresh each time. `cargo build`
targets `aarch64-apple-ios-sim` and `x86_64-apple-ios` for the Simulator, `aarch64-apple-ios` for
[BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md)'s real-device
targeting, and `aarch64-apple-darwin` so [`swift.yml`](../../.github/workflows/swift.yml)'s plain
Apple Silicon macOS runner — no Simulator, `swift build --package-path .` and `swift test
--package-path .` — keeps building and testing the package the way it does today. No
`x86_64-apple-darwin` slice is built, which narrows one host set: an Intel Mac keeps the Simulator
slice it needs to run a scenario, but no longer builds or unit-tests the package natively, the way
it does today. This item accepts that narrowing, since `swift.yml` already runs Apple Silicon only.
`uniffi-bindgen` emits the Swift bindings, a header, and a modulemap — nothing that merges
`.xcframework` slices. `lipo` (for the universal Simulator slice) and `xcodebuild
-create-xcframework` do that, merging the four `cargo build` outputs into an `.xcframework` with
three platform slices, added to
`Package.swift` as a binary target `BajutsuRunner` depends on.

That one manifest is also the reason this binary target is not free: `Package.swift` publishes
`BajutsuKit` and `BajutsuRunner` as two products from one target graph, the same graph an app
depending on the `BajutsuKit` product alone still resolves. Unlike a build-time plugin, a binary
target is something SwiftPM's resolver has to locate and validate for the whole manifest, not only
for the product a consumer actually links — so an app that never touches `BajutsuRunner` may still
pay a resolve-time cost for an artifact it never uses. That cuts against this item's own goal of
keeping `BajutsuKit`, the in-app library, free of this build step, the same way BE-0405 keeps
`IdentifierTool` free of `BajutsuAndroid`'s dependencies. Whether that cost is acceptable as is, or
whether `BajutsuRunner` needs its own manifest to isolate it, is an open question this item leaves
for implementation to settle — Progress names it rather than deciding it here.

`BajutsuRunner` is bajutsu's own
bundled test runner. It reaches a developer's machine pre-built, through
[BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md)'s content-hash-keyed
cache — never compiled inside a consuming project's own build. This build step runs inside
bajutsu's own release pipeline, never inside an app under test's build.

**Android: `BajutsuAndroidUIAutomatorServer`.**
[`BajutsuAndroidUIAutomatorServer/server/build.gradle.kts`](../../BajutsuAndroidUIAutomatorServer/server/build.gradle.kts)
already depends on `androidx.test.uiautomator`, for the same instrumentation this executor extends.
A native dependency in that module is a new *kind* of dependency, not a new tolerance for one — but
it is a real change to the module's reach, not a neutral one. The instrumentation APK carries no
native code today, so it installs on every application binary interface (ABI) a device offers,
including the 32-bit `armeabi-v7a` and `x86` devices `minSdk = 26` still admits. `cargo-ndk`
cross-compiles the crate for `arm64-v8a`, plus `x86_64` for the
[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) emulator lane —
narrowing that set for the first time. A 32-bit device would no longer run the resident server. This
item accepts that narrowing rather than building for all four ABIs: a 32-bit-only Android device is
already outside what the Play Store has accepted from an app since 2019, so the resident server's
own device coverage tracks what a real target app can ship to today. `cargo-ndk` produces `.so`
files placed under `jniLibs`; `uniffi-bindgen` emits the Kotlin bindings the executor calls directly.
That build file's own comment already says the server "stays dependency-light" since it carries no
HTTP or JSON library, being a self-contained instrumentation. That comment states a preference
against an unneeded dependency, not a ban on the native dependency this executor actually needs.

**Verification.** [`tests/fixtures/be0408/`](../../tests/fixtures/be0408/) is already the
language-neutral corpus this crate needs: `selector_resolution.json`'s 42 schema-versioned cases
(each an `elements` list, one `selector`, and the `findAll` / `resolveUnique` outcome Python
produces) and `android_derived_label.json`'s 9 cases, both replayed against the Python reference by
[`tests/test_selector_fixtures.py`](../../tests/test_selector_fixtures.py) on every change today. A
small Rust binary — built for the CI host's own architecture, never shipped with the wheel or the
resident server — reads a case's `Element` list and `Selector` from that same JSON on standard
input, and writes the matched indices, or the error variant and its structured detail, on standard
output; `test_selector_fixtures.py` gains a second replay path that runs the existing 42 cases
through this binary, alongside the one that already runs them through Python, so the two stay
checked against one shared corpus rather than two independently maintained ones.

That corpus, unchanged, covers `find_all` and `resolve_unique` — which exercise `matches` and
`_collapse_identical_duplicates` internally — but not the five functions this item ports that
`selector_resolution.json`'s `elements`-plus-`selector` shape cannot express: `contains` (two
`Frame`s), `frame_center` (one `Frame`), `topmost_at_point` (a `Point` plus a target index),
`redirect_candidates` and `raise_if_covered` (a target index alongside the selector). The schema
gains a version 2 for these: a per-case `function` field selects which of the nine the case
exercises, and each case carries that function's own extra inputs (a point, a target index, a second
frame) alongside the `elements` list already there. `test_selector_fixtures.py` dispatches on
`function` the same way it already dispatches on `findAll` / `resolveUnique` presence, so the one
corpus and the one replay test grow to cover all nine functions instead of splitting into a second
mechanism. Once BE-0409 and BE-0410 exist, the same corpus runs a third time, through each
platform's own binding; a failure there narrows at once to "the binding," rather than reopening
whether the shared logic itself carries the defect.

## Alternatives considered

- **Leave BE-0408's shipped plan as the final state: a written porting contract plus a fixture
  corpus, and two independently written Swift and Kotlin ports checked against it.** Rejected as the
  sole safeguard. The risk BE-0408 already names — two independent copies must agree on every case,
  including which candidate an ambiguous match reports — stays live instead of a design removing it.
  Every future selector rule would need two hand-written patches, kept in step by test failures
  discovered after the fact, not prevented by construction.
- **Extend the same Rust core to Python too, through PyO3, for one three-language
  implementation.** Rejected. `bajutsu`'s pip package is pure Python today. It keeps its
  base install free of the AI SDK and Playwright on purpose, as opt-in extras rather than base
  dependencies
  ([BE-0111](../BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency.md)). A native
  extension in the base install would need a cross-platform wheel build for every `pip install
  bajutsu`. A tool such as [maturin](https://github.com/PyO3/maturin) builds one. The iOS and Android
  builds above stay inside bajutsu's own release pipeline alone. This build would run for every
  install instead. Python's implementation is already the tested reference the conformance suite
  checks the Rust core against; nothing in this item's motivation requires it to change.
- **Extend the same Rust core to BajutsuKit's and BajutsuAndroid's in-app collectors (`BajutsuNet`,
  `BajutsuZOrder`, the clipboard receiver) too.** Rejected. Those components' platform-hook
  mechanisms share no portable logic beyond the shape of the JSON payload each POSTs to a
  collector — `URLProtocol` swizzling on one side, an OkHttp `Interceptor` on the other; a loopback
  HTTP server on one side, `AccessibilityNodeInfo` extra-data on the other. A shared Rust core there
  would replace that thin, already-documented contract alone, while still needing a from-scratch
  platform-specific implementation on each side. `BajutsuRunner` and `BajutsuAndroidUIAutomatorServer`
  ship as bajutsu's own test infrastructure; these libraries instead ship inside the app under test.
  [BE-0405](../BE-0405-android-identifiertool/BE-0405-android-identifiertool.md) already commits
  `IdentifierTool` there to a dependency-free, minimal-footprint design — a design a bundled Rust
  static library would work against, not with.
- **Generate equivalent Swift and Kotlin source from one specification language, instead of compiling
  one native library both platforms link.** Rejected. Two independently compiled, generated copies
  can still diverge if a per-language code-generator backend carries its own bug — the same failure
  mode this item exists to remove. This repository has no existing code-generator backend at this
  level to build on. UniFFI, by contrast, is an existing, maintained tool built for this shape: one
  Rust core, host-language bindings for each platform.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Stand up the crate skeleton at `rust/selector-core/`, with `Element`, `Selector`, and `Trait`
  mirrored as UniFFI records and the three selector errors plus `UnsupportedPattern` as one UniFFI
  error enum.
- [ ] Implement `idMatches` and `labelMatches` matching on top of the [`regex`](https://docs.rs/regex)
  crate alone. For `idMatches`, reimplement `fnmatch`'s glob-to-regex translation, rewrite its `\Z`
  to `\z`, and wrap the result in `^(?:…)\z` before compiling, reproducing Python's start anchoring
  from `re.match` as well as its end anchoring. For `labelMatches`, compile the pattern unanchored,
  and reject `$` outright alongside lookaround, backreferences, `\Z`, atomic groups, possessive
  quantifiers, and conditional patterns — returning `UnsupportedPattern` for any of them, since
  `regex` cannot reproduce Python's optional-trailing-newline `$` without lookahead.
- [ ] Port the nine selector and geometry functions from
  [`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py),
  changing `find_all` and `redirect_candidates` to return `Vec<u32>`, `resolve_unique` a `u32`
  (indices into the caller's `elements`) in place of elements, and `topmost_at_point` /
  `redirect_candidates` / `raise_if_covered` to take a `target_index: u32` in place of an `Element`.
  Port `_collapse_identical_duplicates` with no added parameter, comparing frames for exact equality
  exactly like the host, and keyed with an insertion-ordered map (such as `indexmap::IndexMap`)
  rather than `std::collections::HashMap`, whose iteration order is not just platform-dependent but
  reseeded per process. `docs/selectors.md`'s porting contract keeps the frame-tolerance divergence
  from `resolvableMatchingIndex` on purpose, so no step here should unify the two:
  - `matches`
  - `find_all`
  - `resolve_unique`
  - `_collapse_identical_duplicates`
  - `contains`
  - `topmost_at_point`
  - `redirect_candidates`
  - `raise_if_covered`
  - `frame_center`
- [ ] Add a version 2 to [`tests/fixtures/be0408/`](../../tests/fixtures/be0408/)'s schema with a
  per-case `function` field and each function's own extra inputs (a point, a target index, a second
  frame), covering the five functions version 1's `elements`-plus-`selector` shape cannot express
  (`contains`, `frame_center`, `topmost_at_point`, `redirect_candidates`, `raise_if_covered`).
  Extend [`tests/test_selector_fixtures.py`](../../tests/test_selector_fixtures.py) to dispatch on
  `function` and replay the new cases against the Python reference, the same way it already replays
  version 1.
- [ ] Build the CLI conformance-runner binary that reads a
  [`tests/fixtures/be0408/`](../../tests/fixtures/be0408/) case from JSON on standard input and
  writes the matched indices, or the error variant and its structured detail, on standard output.
  Extend `test_selector_fixtures.py` with a second replay path that runs the existing corpus through
  this binary, alongside the path that already runs it through Python.
- [ ] Wire `cargo`, `uniffi-bindgen`, `lipo`, and `xcodebuild -create-xcframework` into the
  `BajutsuKit` Swift Package build, producing an `.xcframework` (three platform slices merged from
  four `cargo build` targets: Simulator, device, and macOS) binary target `BajutsuRunner` links
  against. Decide, before adding the target, whether the repo-root `Package.swift`'s single target
  graph makes that binary target part of what an app depending on the `BajutsuKit` product alone
  resolves — and, if so, whether to accept that cost or give `BajutsuRunner` its own manifest to
  isolate it, the same way BE-0405 isolates `IdentifierTool` from `BajutsuAndroid`.
- [ ] Wire `cargo-ndk` and `uniffi-bindgen` into `BajutsuAndroidUIAutomatorServer`'s Gradle build,
  producing the Kotlin bindings and `arm64-v8a` / `x86_64` `jniLibs` its executor links against.
- [ ] Add a Rust CI lane (`cargo test`, `cargo fmt --check`, `clippy`) for `rust/selector-core/`, and
  decide whether `make check` invokes it directly or a separate workflow does.
- [ ] Update `docs/selectors.md`'s *Porting contract for a device-side resolver* section (and its
  [Japanese mirror](../../docs/ja/selectors.md)) to state that `find_all`, `resolve_unique`, and
  `_collapse_identical_duplicates` come from this crate rather than a hand-written Swift or Kotlin
  port, leaving the *Two divergences a port must keep, not close* section unchanged — those two
  divergences describe `resolvableMatchingIndex`, which this item does not touch. Update BE-0409's
  and BE-0410's "port … to Swift / Kotlin" steps to call the compiled bindings instead, keeping
  BE-0410's derived-label port as its own step, since `_derived_label` normalizes before the shared
  core runs. Drop the iOS-port-first sequencing from BE-0409's Detailed design, and from BE-0410's
  Implementation order and Sequence status lines. Repoint BE-0409's Sequence status line from
  BE-0408 to this item, and BE-0410's from BE-0409 to this item, since both now depend on this
  item's crate rather than on the order between themselves.
- [ ] Once the `roadmap-id` workflow allocates this item's id on `main`, backfill a reciprocal
  `Related` link into BE-0408, BE-0409, and BE-0410.

## References

[BE-0111 — AI SDK as an optional dependency](../BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency.md),
[BE-0407 — Step-latency driver-internal tuning](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md),
[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[BE-0208 — Android emulator e2e CI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md),
[BE-0238 — iOS device cloud execution](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md),
[BE-0292 — XCUITest bundled runner](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md),
[BE-0405 — Android IdentifierTool](../BE-0405-android-identifiertool/BE-0405-android-identifiertool.md),
[BE-0408 — Step-latency device-executor protocol](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md),
[BE-0409 — iOS on-device step executor](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor.md),
[BE-0410 — Android on-device step executor](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md),
[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py),
[`bajutsu/common/drivers/adb/_functions.py`](../../bajutsu/common/drivers/adb/_functions.py),
[`BajutsuKit/Sources/BajutsuRunner/PositionPath.swift`](../../BajutsuKit/Sources/BajutsuRunner/PositionPath.swift),
[`docs/selectors.md`](../../docs/selectors.md),
[`tests/fixtures/be0408/`](../../tests/fixtures/be0408/),
[`tests/test_selector_fixtures.py`](../../tests/test_selector_fixtures.py),
[UniFFI](https://mozilla.github.io/uniffi-rs/)
