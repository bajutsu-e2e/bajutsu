**English** · [日本語](BE-XXXX-native-app-build-parity-ja.md)

# BE-XXXX — Build the app on demand on every platform, and force a rebuild on request

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-native-app-build-parity.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

A target's config can name the shell command that builds its app. The command lives in
`subject.build`, beside `subject.path`, the built app a run installs on the device. Today Bajutsu
runs that command in two places: a run whose config comes from a Git repository, and a job started
from the web UI (`bajutsu serve`). Both places run it only for an iOS target, and only when the
built app is missing.

This item makes `subject.build` behave the same on every native platform and on every entry point.
It adds two changes:

- **Build on demand everywhere.** An Android target's build runs wherever an iOS target's does.
  A run started from a local config file builds a missing app too, instead of failing.
- **Force a rebuild on request.** `bajutsu run --rebuild`, and a matching checkbox on the web UI's
  Replay panel, run `subject.build` even when the built app already exists.

## Motivation

The schema accepts `subject.build` on an Android target, yet no code path reads it. The resolver
copies the command into the resolved Android config, but every caller that runs a build checks
for the iOS config type first. An Android user who writes the field gets no build and no warning;
the run fails later on a missing Android application package (APK). The configuration reference describes `subject.build` for
both platforms, so the silent gap contradicts the documentation as well as the user's intent.

A local config has the same gap on iOS. The Git path builds on demand because a fresh checkout
holds no binary. A local checkout is in the same position after a `git clean` or on a new machine,
but `bajutsu run` there stops with a missing-binary error. The user must then run the build command
by hand, although the config already states the command.

Build-on-demand alone cannot pick up a source change. Once the app exists, a run reuses it, so a
developer who edits the app's source and reruns a scenario tests the old build. The only remedy
today is to delete the built app before the run. A flag that always builds closes that loop in one
step.

After this change, an Android target with `subject.build` and a missing APK builds it and runs,
from the command line and from the web UI alike. A run with `--rebuild` prints the build's output
before the first scenario even when the app already exists.

## Detailed design

The work splits into five units: the shared build function, the command-line run, the web UI job,
the web UI control, and the documentation.

### Unit 1 — One build function with a force switch

`build_if_missing` in `bajutsu/common/runner/build.py` becomes `build_app(build, app_path, *,
cwd, force=False)`. Without `force`, the function behaves as before: it runs the command when the
app path is set and the file is missing. With `force`, the function runs the command whenever one
is set, and the app's existence no longer matters.

The function also honors the environment flag that `serve` sets on a run it does not trust to run
host commands. That flag, `BAJUTSU_WITHHOLD_RUNNER_BUILD`, already withholds `driver.runner.build`
for an uploaded bundle, and for a Git config bound through the application programming interface (API) without `--allow-remote-build`.
Under the flag, an on-demand build is skipped, and the run reports the missing app as it did before.
A forced build under the flag fails with a clear message. This rule matters once a local config
builds on demand. Without it, a run that `serve` spawned for an untrusted bundle would run the
bundle's build command, which `serve` itself refuses to run.

### Unit 2 — The command-line run

`bajutsu run` builds the primary target's app for an iOS or an Android target, from any config
source. The working directory follows the rule the configuration reference already states:

| Config source | Working directory of `subject.build` |
|---|---|
| Git repository | the checkout root (unchanged) |
| Local file | the config file's own directory |

The same rule reaches the other targets whose builds a run makes before any device work. Those
targets are the members of a device group: a target that a later `installApp` step installs, and a
target that starts on a shared device. Each of them now builds on Android and from a local config
too.

`--rebuild` passes `force` to every one of those builds, and each target builds once per run.
The flag needs the primary target to declare `subject.build`. Without one, `--rebuild` would build
nothing, so the run exits with status 2 and names the target instead of starting. A group member
without `subject.build` keeps its existing app, since such a member's app is expected on the device
already.

### Unit 3 — The web UI job

`target_build_info`, the helper that hands a job its app path and build command, reads the Android
config as well as the iOS one. A `Job` gains a `rebuild` field. The field travels in the job
specification, so a remote worker forces the build as the local server does. `_build_app` builds
when `rebuild` is set or the app is missing.

`POST /api/run` accepts `rebuild: true` and refuses it with status 400 in two cases:

- **No build to force.** The target declares no `subject.build`, or `serve` withholds the command
  for an untrusted binding.
- **A stored binary replaces the build.** The request also carries `binaryArtifact`. The run then
  installs that stored binary, so a rebuild would build an app the run never uses.

`serve` performs the build itself, before it spawns the run. The spawned `bajutsu run` therefore
never receives `--rebuild`, so one job builds the app once.

### Unit 4 — The web UI control

The Replay panel gains a "rebuild the app first" checkbox, sent as `rebuild` on `POST /api/run`.
The checkbox sits with the other per-run options. A fan-out Replay hides it, as it hides the other
per-run options, so the fan-out request carries no `rebuild`. Record and Crawl keep their current
behavior; each still builds a missing app on demand, now on Android too.

### Unit 5 — Documentation

The configuration reference, the command-line reference, and the self-hosting guide describe the
new behavior in English and Japanese. The text drops the statement that a local `run` never builds,
names Android beside iOS, and documents `--rebuild` and the Replay checkbox.

## Alternatives considered

- **Rebuild on every run, without a flag.** A build often takes minutes, and most runs follow no
  source change. Forcing the build every time would slow every run, so the forced build stays an
  explicit request.
- **Compare timestamps between the sources and the built app.** Bajutsu would need to know which
  files the app is built from. That knowledge belongs to the build tool, such as `make` or Gradle,
  and a config field naming the sources would duplicate it. A user who wants incremental builds can
  point `subject.build` at a build tool that already decides what to rebuild, and combine it with
  `--rebuild`.
- **A general pre-run hook instead of `subject.build`.** A hook could run any command before a
  scenario, but the run would then lose the one fact the build field carries: which path the command
  produces. Bajutsu needs that path to decide whether to build at all, and to install the result.
- **Keep the local path build-free.** A local run that stops on a missing binary leaves the user to
  find and run the command, while the config already names the command. The Git path proved the
  on-demand build safe, and the withhold flag in unit 1 keeps it off untrusted bindings.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — One build function with a force switch, which honors the withhold flag
- [ ] Unit 2 — The command-line run builds on Android and from a local config, and takes `--rebuild`
- [ ] Unit 3 — The web UI job builds on Android and accepts `rebuild` on `POST /api/run`
- [ ] Unit 4 — The Replay panel's "rebuild the app first" checkbox
- [ ] Unit 5 — The configuration, command-line, and self-hosting documentation, in both languages

## References

- [BE-0063 — Load config from a Git repository](../BE-0063-git-config-source/BE-0063-git-config-source.md):
  the Git-sourced build-on-demand this item extends.
- [BE-0121 — Unconditional CSRF and Host-allowlist defenses for serve](../BE-0121-serve-csrf-host-allowlist/BE-0121-serve-csrf-host-allowlist.md):
  the trust rule that withholds a build for a Git config bound through the API.
- [`bajutsu/common/runner/build.py`](../../bajutsu/common/runner/build.py) and
  [`bajutsu/serve/jobs.py`](../../bajutsu/serve/jobs.py): the two build paths this item changes.
