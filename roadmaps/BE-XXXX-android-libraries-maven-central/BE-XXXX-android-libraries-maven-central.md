**English** · [日本語](BE-XXXX-android-libraries-maven-central-ja.md)

# BE-XXXX — Publish BajutsuAndroid and IdentifierTool to Maven Central

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-android-libraries-maven-central.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md), [BE-0283](../BE-0283-android-network-capture/BE-0283-android-network-capture.md), [BE-0355](../BE-0355-native-z-position/BE-0355-native-z-position.md), [BE-0405](../BE-0405-android-identifiertool/BE-0405-android-identifiertool.md), [BE-0481](../BE-0481-android-device-set-location/BE-0481-android-device-set-location.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu ships two Android libraries that an app under test embeds.
[`BajutsuAndroid`](../../BajutsuAndroid) gives bajutsu an in-app foothold for the clipboard, network
capture, z-order reporting, and location mocking. [`IdentifierTool`](../../IdentifierTool) gives an app a stable
`resource-id` / `content-desc`. Today an app can consume either library in one way alone: clone
this repository and include the library's directory as a Gradle module by path.

This item publishes both libraries to **Maven Central**, the repository every new Android Studio
project already resolves from. An app then adds one line to its build file:

```kotlin
debugImplementation("io.github.bajutsu-e2e:bajutsu-android:0.1.0")
```

Each library gets its own version and its own release tag. A tag push runs a GitHub Actions
workflow that signs the artifacts, uploads them through the Sonatype Central Portal, and creates a
matching GitHub Release.

## Motivation

The iOS side already has a one-step install. `BajutsuKit` is a Swift Package Manager (SwiftPM)
package at this repository's root, so an Xcode project adds it by its repository address and version. The Android
side has no counterpart. An adopting team today faces three costs:

- **A clone or a submodule.** The app's build must see this repository's files on disk. A Git
  submodule drags in the whole Python core, the docs, and the demos to reach one small directory.
- **A build that cannot stand alone.** `BajutsuAndroid/build.gradle.kts` declares no plugin
  versions. It takes the Android Gradle Plugin (AGP) and Kotlin versions from whichever build
  includes it. An app on a different AGP version can fail to configure, for a reason unrelated to
  bajutsu.
- **No version to pin.** A path include tracks whatever commit the clone sits on. Two developers on
  one team can embed different receivers without knowing it, and nothing records which one a run
  used.

These costs fall on the first step of adoption. A team evaluating bajutsu for Android meets them
before it runs a single scenario.

The outcome a reader can check once this item ships: an Android Studio project created from the
default template adds `debugImplementation("io.github.bajutsu-e2e:bajutsu-android:<version>")` and builds.
The build needs no extra repository, no clone, and no credential. The same holds for
`bajutsu-identifier-tool`.

## Detailed design

### Coordinates

| Library | Source directory | Maven coordinates | Kotlin package (unchanged) |
|---|---|---|---|
| BajutsuAndroid | `BajutsuAndroid/` | `io.github.bajutsu-e2e:bajutsu-android` | `dev.bajutsu.android` |
| IdentifierTool | `IdentifierTool/` | `io.github.bajutsu-e2e:bajutsu-identifier-tool` | `dev.bajutsu.identifier` |

The groupId `io.github.bajutsu-e2e` follows the `bajutsu-e2e` GitHub organization. The Central
Portal grants a namespace in two ways. A publisher who owns a domain proves it with a Domain Name
System (DNS) `TXT` record. A publisher without one proves control of a code-hosting account
instead, which yields a namespace such as `io.github.<name>`. Bajutsu owns no domain and has no plan
to register one, so we take the second way. Verifying the namespace is a one-time manual step (see
"One-time manual setup" below).

The Kotlin packages keep their names. A Kotlin package name cannot contain a hyphen, and a package
need not match its Maven groupId. Renaming the packages would break every existing consumer for no
gain.

Each library publishes its `release` variant alone, as an Android Archive (AAR), with a sources jar
and a Javadoc jar. The Central Portal requires all three, plus a signature on each file. It also
requires a Project Object Model (POM) file that names the license (Apache License 2.0), the source
repository, and a developer.

### A standalone Gradle build for publishing

Neither library directory can build on its own today. Both take their plugin versions from the
including build. This item adds `android-libraries/`, a new top-level Gradle build that exists to
build and publish the two libraries:

- `android-libraries/settings.gradle.kts` includes `:bajutsu-android` from `../BajutsuAndroid` and
  `:bajutsu-identifier-tool` from `../IdentifierTool`, by path. The pattern matches what the
  showcase does today.
- `android-libraries/build.gradle.kts` declares the AGP, Kotlin, and
  [`com.vanniktech.maven.publish`](https://github.com/vanniktech/gradle-maven-publish-plugin) plugin
  versions. It applies the publishing plugin to both projects and sets the shared POM fields once.
- `android-libraries/` carries its own Gradle wrapper, so the release job and a contributor's
  machine resolve the same Gradle version.

The two library directories gain one file each: a `gradle.properties` holding the per-library POM
fields (`POM_ARTIFACT_ID`, `POM_NAME`, `POM_DESCRIPTION`). Their `build.gradle.kts` files stay
publish-agnostic. The showcase (`demos/showcase/android`) and `IdentifierTool/sample` keep including
the libraries by path and never apply the publishing plugin. Neither build changes.

The vanniktech plugin handles the Central Portal upload, the in-memory signing, the sources and
Javadoc jars, and the POM in one place. Signing applies purely when the signing properties are
present, so the secret-free `verify` job and a contributor's local `publishToMavenLocal` both run
unsigned. Android libraries on Maven Central commonly publish with it today.

### Versions and tags

Each library carries an independent version. BE-0405 split `IdentifierTool` out of `BajutsuAndroid`
so that the two never share a release, and a single version would undo that split at the packaging
step.

| Library | Tag pattern | Example |
|---|---|---|
| BajutsuAndroid | `bajutsu-android-v<semver>` | `bajutsu-android-v0.1.0` |
| IdentifierTool | `bajutsu-identifier-tool-v<semver>` | `bajutsu-identifier-tool-v0.1.0` |

The tag is the single source of the published version. No file in the tree records a release
number, so a release needs no version-bump commit. A local build without a tag publishes
`0.0.0-SNAPSHOT`, which never reaches Maven Central. Both libraries start at `0.1.0`. A minor version
below `1.0.0` may break the application programming interface (API), under semantic versioning's own rules for a pre-1.0 release.

### The release workflow

A new workflow, `.github/workflows/android-libraries.yml`, has two jobs.

| Job | Trigger | What it runs |
|---|---|---|
| `verify` | A pull request touching `BajutsuAndroid/`, `IdentifierTool/`, or `android-libraries/` | `./gradlew publishToMavenLocal` for both libraries, unsigned, then a check that each expected AAR, sources jar, Javadoc jar, and POM exists under `~/.m2` |
| `release` | A push of a tag matching either pattern above | Parse the library and version from the tag, then run `:<library>:publishAndReleaseToMavenCentral`, then create a GitHub Release with the AAR attached |

The `verify` job catches a broken POM or a missing jar on the pull request that causes it. Without
the job, that breakage would surface at the next release instead. The job needs no secret, so it
runs on forks too.

The `release` job runs in a GitHub environment named `maven-central`. Four secrets live in that
environment alone, out of reach of any pull-request workflow:

- `MAVEN_CENTRAL_USERNAME` and `MAVEN_CENTRAL_PASSWORD` — a Central Portal user token.
- `SIGNING_IN_MEMORY_KEY` and `SIGNING_IN_MEMORY_KEY_PASSWORD` — the text-armored GNU Privacy Guard
  (GPG) private key and its passphrase.

A repository ruleset restricts who may push the two tag patterns. The `release` job's first step
then validates the tag before any upload: it fails on a version that is not semantic versioning
(`<major>.<minor>.<patch>`), and fails on a tag whose commit is not an ancestor of `main`
(`git merge-base --is-ancestor "$GITHUB_SHA" origin/main`).

`publishAndReleaseToMavenCentral` uploads the bundle and releases it automatically once the Central
Portal's own validation passes. Maven Central never deletes or overwrites a release. A maintainer
fixes a bad release by publishing the next patch version.

### One-time manual setup

These steps need a human with the right account access. No workflow can do them:

1. Create a Central Portal account for the project, and request the `io.github.bajutsu-e2e`
   namespace. The Central Portal issues a verification key. A `bajutsu-e2e` organization owner
   creates a temporary public repository named after that key, then deletes the repository once
   the Central Portal marks the namespace verified.
2. Generate a GPG key pair for signing, and publish its public key to a keyserver the Central
   Portal checks (`keys.openpgp.org`).
3. Generate a Central Portal user token.
4. Create the `maven-central` environment, and restrict its deployment branches and tags to the two
   tag patterns alone, so no pull-request workflow can read it. Store the four secrets in that
   environment, and add the tag ruleset.

The developer guide records these steps (see "Documentation"). A maintainer rotating a key or a
token later then follows the same page.

### Documentation

- `BajutsuAndroid/README.md` and `IdentifierTool/README.md`, with their Japanese mirrors, replace
  the path-include instructions with the Maven coordinates. Each README keeps the path include as a
  second option for a contributor working on the library itself. Both READMEs recommend
  `debugImplementation`, since both libraries are test and debug tools.
- `BajutsuAndroid/README.md` notes that `BajutsuNet`'s OkHttp interceptor declares OkHttp as
  `compileOnly`. The POM does not list OkHttp, so an app calling the interceptor brings OkHttp
  itself, as it already does.
- `IdentifierTool/README.md` carries the same note for `androidx.compose.ui`, which the library
  declares `compileOnly`. The POM does not list Compose, so an app calling the Compose helpers
  brings Compose itself, as a path include requires today.
- `docs/developer-guide.md` and `docs/ja/developer-guide.md` gain a "Releasing the Android libraries"
  section: the tag patterns, the version policy, and the one-time setup above.
- `docs/drivers.md` (the adb section) and its Japanese mirror point at the coordinates where they
  mention the two libraries.

### Out of scope

- **A version-compatibility check between the bajutsu core and the embedded library.** Once apps
  pin a published version, the Python adb driver and the in-app receiver can drift apart. Detecting
  that drift needs a protocol version on the broadcast and a check in `bajutsu/`. That change
  touches the driver, not the packaging, so it belongs in its own item.
- **`BajutsuAndroidUIAutomatorServer`.** bajutsu installs that server's Android package (APK) on
  the device itself. No
  app build consumes it, so Maven Central gives it nothing.
- **Snapshot publishing** (a `-SNAPSHOT` build from every `main` commit). Nobody has asked for it. A
  later item can add it without changing anything this item builds.

### Work breakdown (MECE: mutually exclusive, collectively exhaustive)

1. Add `android-libraries/` (settings, root build file with the plugin versions and shared POM
   fields, Gradle wrapper), and a `gradle.properties` with the per-library POM fields in
   `BajutsuAndroid/` and `IdentifierTool/`. Done when `./gradlew publishToMavenLocal` in
   `android-libraries/` produces both AARs with their sources jars, Javadoc jars, and POMs, and the
   showcase still builds unchanged.
2. Add `.github/workflows/android-libraries.yml` with the `verify` and `release` jobs, and map
   `android-libraries/` to the `platform` topic in `scripts/sync_roadmap_topic_labels.py`, beside
   the existing `IdentifierTool/` rule. Leave `scripts/e2e_changes.py` unchanged: the showcase never
   builds through `android-libraries/`, so the on-device Android lane has nothing to exercise there.
   Done when `make lint-actions` passes, the `verify` job is green on the pull request, and the
   `release` job's tag validation rejects a non-semantic version and a commit outside `main`.
3. Complete the one-time manual setup listed above. Done
   when the Central Portal shows `io.github.bajutsu-e2e` as verified.
4. Update the READMEs and docs listed under "Documentation", in both languages.
5. Push `bajutsu-android-v0.1.0` and `bajutsu-identifier-tool-v0.1.0`. Done when a fresh Android
   Studio project from the default template resolves and builds against both coordinates, with no
   repository beyond `google()` and `mavenCentral()`.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| JitPack | JitPack builds a library from a Git tag on demand, with almost no setup. But every consumer must add `maven("https://jitpack.io")` to its build. The first request for a version waits for a build, and an outage blocks every consumer's sync. This repository's root is a Python project, so JitPack would also need a `jitpack.yml` to find the Gradle build. |
| GitHub Packages | GitHub Packages requires a personal access token to resolve even a public package. Every consumer, including every CI job that builds an app, would have to provision one. |
| AAR files attached to GitHub Releases alone | A downloaded AAR carries no POM, so Gradle resolves none of its dependencies, such as `androidx.core`. Nothing tells a consumer that a newer version exists. This item still attaches the AAR to each GitHub Release, for a reader who wants the file by itself. |
| A groupId under an owned domain, such as `dev.bajutsu` (domain `bajutsu.dev`) or `dev.bajutsu-e2e` (domain `bajutsu-e2e.dev`) | Either keeps the hosting provider's name out of the coordinates, and `dev.bajutsu` matches the Kotlin packages exactly. But the domain must be bought, renewed every year, and never allowed to lapse, since a lapsed domain could pass to a third party. Bajutsu has no plan to own a domain. If it ever does, moving to that namespace changes every consumer's coordinates. |
| One shared version and one tag for both libraries | One workflow path and one tag per release. But a fix to one library would bump the other's version with no change behind it, and BE-0405 split the two libraries so that each releases on its own schedule. |
| Apply the publishing plugin inside each library's own `build.gradle.kts` | Every build that includes the library by path would then have to declare the plugin's version too. The showcase and `IdentifierTool/sample` would need edits unrelated to their purpose. Configuring the plugin from `android-libraries/` keeps those builds untouched. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — `android-libraries/` publishing build, plus a per-library `gradle.properties`
- [ ] Unit 2 — `android-libraries.yml` workflow (`verify` and `release`), plus the topic-label
      entry
- [ ] Unit 3 — one-time manual setup, from the namespace through the tag ruleset
- [ ] Unit 4 — READMEs and docs updated in both languages
- [ ] Unit 5 — first release of both libraries, confirmed from a fresh Android Studio project

## References

- [`BajutsuAndroid/README.md`](../../BajutsuAndroid/README.md)
- [`IdentifierTool/README.md`](../../IdentifierTool/README.md)
- [`demos/showcase/android/settings.gradle.kts`](../../demos/showcase/android/settings.gradle.kts)
- [Sonatype Central Portal: publishing requirements](https://central.sonatype.org/publish/requirements/)
- [Sonatype Central Portal: registering a namespace](https://central.sonatype.org/register/namespace/)
- [gradle-maven-publish-plugin](https://github.com/vanniktech/gradle-maven-publish-plugin)
