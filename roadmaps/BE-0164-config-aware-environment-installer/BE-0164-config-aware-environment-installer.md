**English** · [日本語](BE-0164-config-aware-environment-installer-ja.md)

# BE-0164 — Config-aware environment installer

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0164](BE-0164-config-aware-environment-installer.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0164") |
| Implementing PR | [#679](https://github.com/bajutsu-e2e/bajutsu/pull/679) |
| Topic | doctor / onboarding |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's driver-agnostic core needs different external pieces depending on the backend a project
uses:

- Homebrew's `idb_companion` for the iOS Simulator.
- Playwright's Chromium build for the web backend.
- The `anthropic` SDK for the Tier‑1 AI paths.

Nothing today looks at a project's config and installs exactly what it needs.

What exists instead is three independent, partial mechanisms that each cover one slice. `make setup`
installs the Python toolchain alone. `make deps` installs idb alone, hardcoded.
[`scripts/serve.sh`](../../scripts/serve.sh) installs idb alone, and for `serve` alone.

This item proposes a single, config-aware installer. It reads a project's effective config and
installs the pip extras and external tools that the configured backends require.

## Motivation

"When do bajutsu's dependencies get installed?" has no single answer today. The honest answer is
"it depends which of several disconnected mechanisms you happen to run":

- **`make setup`** runs `uv sync --group dev`. It installs the Python toolchain and the AI‑free base
  package, and nothing for any backend.
- **`make deps`** installs the iOS-only pieces: `brew bundle` against [`Brewfile`](../../Brewfile)
  for `idb_companion` / `xcodegen`, plus `uv sync --extra idb`. It installs them unconditionally,
  even for a project that targets the web backend. No equivalent target exists for
  `uv sync --extra web` + `playwright install chromium`, or for `--extra ai` / `--extra visual` /
  `--extra mcp`.
- **`scripts/serve.sh`** re-implements a *subset* of the idb check in `make deps` (the
  `.venv/bin/idb` and `idb_companion` presence checks). It does so on the `bajutsu serve` launch
  path alone. Running `bajutsu run`, `bajutsu doctor`, or `bajutsu record` directly gets none of it.
- For the **web backend**, the one "installer" that exists is the remedy string that `doctor` and
  `preflight.py` print after a check has already failed (`uv sync --extra web` /
  `playwright install chromium`; see [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)).
  A user has to read and run it by hand.
- `pyproject.toml` declares a dozen optional extras (`ai`, `idb`, `web`, `visual`, `mcp`, `bedrock`,
  `server`, `db`, `oauth`, `schema`, `s3`, `gcs`, `cloud`, …). Each is imported lazily behind its own
  guard. No single step maps "the extras a *specific* project's config needs" to "the extras
  presently installed" and closes the gap.

The practical effect: a fresh contributor, or a team standing up its own bajutsu-driven test repo,
discovers a missing piece when a command fails deep inside a run. The failure looks like `no available
actuator among ['idb']`, an `ImportError` from an unguarded lazy import, or a `preflight` failure.
The user then has to reverse-engineer which of several install paths applies.

Every future backend is on track to repeat this pattern from scratch, with its own ad hoc Makefile
target or shell script. Android ([BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md))
and Flutter ([BE-0008](../BE-0008-flutter-support/BE-0008-flutter-support.md)) are the next two.
One canonical, extensible place for this logic prevents that.

This is an onboarding concern, the same territory as the `doctor` / `preflight` of
[BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md). It is a separate item
rather than part of BE-0024 on purpose. BE-0024 diagnoses ("is the environment ready?"), while this
item *acts* ("make the environment ready"). Acting is a materially different capability, because it
runs package managers and downloads binaries. The Detailed design of BE-0024 says explicitly that
such a capability now gets its own proposal, rather than being absorbed as a catch-all candidate.

## Detailed design

The work breaks down into three independent, MECE (mutually exclusive, collectively exhaustive)
pieces:

1. **One declarative source of truth for "what a backend/extra needs."** Today the same facts are
   hardcoded in three places that can drift independently: the Brewfile, the presence checks in
   `scripts/serve.sh`, and the remedy strings in `preflight.py`. All three describe the idb
   requirement. The web backend's requirement (`uv sync --extra web` + `playwright install
   chromium`) exists as prose in a remedy string and nowhere machine-readable.

   This item introduces a single mapping, per backend family (`idb` / `web` / `fake`) and per
   optional capability (`ai`, `visual`, `mcp`, …). The mapping holds three things:
   - the pip extra name;
   - an external-tool presence check (a `command -v` probe);
   - how to install the tool when missing (a Homebrew formula reference for macOS-only tools, a
     `playwright install <browser>` invocation, or "none needed").

   The remedy strings of `preflight.py`, `make deps`, and the new installer all read from this one
   mapping instead of maintaining independent copies.
2. **A config-aware install step.** Given a project's effective config (the same `--config`
   resolution that `run` and `doctor` already use), the step resolves which backends
   `targets.*.backend` references, reusing the existing resolution logic of `backends.py`. It also
   resolves whether an AI provider is configured. It then installs only what those targets need.
   That is neither "idb unconditionally" (today's `make deps`) nor "everything" (an option that
   does not exist today).

   Each step is idempotent, following the existing pattern in `scripts/serve.sh`
   (`[ ! -x .venv/bin/idb ]`, `command -v idb_companion`). Nothing is reinstalled once present, and
   the step is safe to re-run on every `make setup`.
3. **Two entry points that share one implementation.** The first entry point is a Makefile target
   for contributors. It either folds `make setup` + `make deps` into one step or introduces
   `make install` alongside them. It is meant to run right after `git clone`, the same moment
   `make setup` runs today.

   The second entry point is the underlying logic, factored into a script that the Makefile calls
   rather than duplicating inline in `Makefile`. That keeps one implementation, which contributors
   and any downstream project's own Makefile can invoke the way they already invoke
   `scripts/serve.sh` and `scripts/preflight.sh`.

This item stays a **local, developer-invoked** setup step. It is never wired into a hosted or
multi-tenant path (`bajutsu serve --backend=server`, an uploaded-bundle run). Running package
managers and downloading binaries on behalf of an uploaded config would cross the boundary that
[BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md)
deliberately closed (govern and sandbox command execution from uploaded bundle configs). A person
runs this installer against their own clone. A server never executes it on a config it received over
the network.

None of this touches the deterministic `run` / CI gate. It is pure environment bootstrap, runs
before any scenario executes, and never becomes part of a pass/fail decision (prime directive #1).

The installer also stays backend-agnostic by construction (prime directive #3). The mapping in
step 1 is where a new backend's requirements plug in, in place of a fork of the installer logic.

## Alternatives considered

**Extend `bajutsu doctor` with a `--fix` flag that installs what it diagnosed missing**, turning the
CLI subcommand itself into the installer. This is an appealing complement. Once the mapping in
step 1 exists, the remedy strings of `doctor` could shell out to the same install functions instead
of merely printing them.

The approach puts the mechanism behind a Typer CLI command, which exists only *after* the Python
package is installed and importable. That does not help with the more fundamental gap this item
targets: what to run right after `git clone`, before any Python environment exists. It is worth
revisiting as a thin follow-up once the installer's logic is centralized, not as a replacement.

**Keep the per-backend ad hoc scripts as the pattern** (a new `scripts/<backend>-serve-equivalent.sh`
each time a backend gains a corner case). Rejected: this is today's status quo, it does not scale to
Android or Flutter, and it leaves the same facts (which extra, which external tool) duplicated
across the Brewfile, `scripts/serve.sh`, and the remedy text of `preflight.py`.

**Install implicitly and silently the first time a command needs something** (no explicit step at
all). Rejected: `scripts/serve.sh` already does a narrow version of this for idb. Generalizing it
(auto-installing on every command, for every backend) would run package managers and download
binaries without the user having asked at that moment. That side effect is bigger and more
surprising than the existing, narrowly scoped precedent.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Declarative mapping of backend/extra → pip extra + external-tool check + install command
- [x] Config-aware install step (resolve configured backends, install only what's needed, idempotent)
- [x] Shared Makefile target + script entry point (supersedes/folds `make deps`, callable right after `git clone`)

### Log

- **2026-07-05** — Shipped all three pieces in one change. `bajutsu/requirements.py` holds the one
  declarative mapping (backend/capability → pip extra + external-tool probe + install method), now
  the single source `preflight.py`'s remedy strings read from (its hardcoded `_BACKEND_TOOLS` and web
  remedy prose removed). `bajutsu/provision.py` resolves a config's referenced backends (via
  `backends.resolve_actuators`) and configured AI provider into a pure `InstallPlan`, then executes
  it idempotently over injectable subprocess seams. `scripts/install.sh` + `make install` (depends on
  `setup`, which keeps its config-agnostic base role) are the two entry points; `make deps` and
  `scripts/serve.sh` now delegate idb provisioning to the same script, and `idb_companion` moved out
  of the `Brewfile` into the mapping (the Brewfile keeps only the sample-app build tool `xcodegen`).

## References

- [`docs/architecture.md`](../../docs/architecture.md) — module list and implementation status
- [`README.md`](../../README.md#setup) — current Setup / Requirements sections
- [`Brewfile`](../../Brewfile) · [`scripts/serve.sh`](../../scripts/serve.sh) · [`bajutsu/preflight.py`](../../bajutsu/preflight.py)
- [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md) — doctor / onboarding
- [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md) — govern and sandbox command execution from uploaded bundle configs
- [BE-0111](../BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency.md) — AI SDK as an optional extra
