**English** · [日本語](BE-XXXX-config-aware-environment-installer-ja.md)

# BE-XXXX — Config-aware environment installer

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-config-aware-environment-installer.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | doctor / onboarding |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's driver-agnostic core needs different external pieces depending on which backend a
project actually uses — Homebrew's `idb_companion` for the iOS Simulator, Playwright's Chromium
build for the web backend, the `anthropic` SDK for the Tier‑1 AI paths — but nothing today looks at
a project's config and installs exactly what it needs. What exists instead is three independent,
partial mechanisms that each cover one slice: `make setup` (Python toolchain only), `make deps`
(idb only, hardcoded), and [`scripts/serve.sh`](../../scripts/serve.sh) (idb only, `serve` only).
This item proposes a single, config-aware installer that reads a project's effective config and
installs the pip extras and external tools its configured backends actually require.

## Motivation

"When do bajutsu's dependencies get installed?" has no single answer today, and the honest answer
is "it depends which of several disconnected mechanisms you happen to run":

- **`make setup`** runs `uv sync --group dev` — the Python toolchain and the AI‑free base package
  only. It installs nothing for any backend.
- **`make deps`** installs the iOS-only pieces (`brew bundle` against
  [`Brewfile`](../../Brewfile) for `idb_companion` / `xcodegen`, plus `uv sync --extra idb`) —
  unconditionally, even for a project that only targets the web backend. There is no equivalent
  target for `uv sync --extra web` + `playwright install chromium`, or for `--extra ai` /
  `--extra visual` / `--extra mcp`.
- **`scripts/serve.sh`** re-implements a *subset* of `make deps`'s idb check (the `.venv/bin/idb`
  and `idb_companion` presence checks), but only on the `bajutsu serve` launch path — running
  `bajutsu run` / `bajutsu doctor` / `bajutsu record` directly gets none of it.
- For the **web backend**, the only "installer" that exists is the remedy string `doctor` /
  `preflight.py` print after a check has already failed (`uv sync --extra web` /
  `playwright install chromium` — see [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)),
  which a user has to read and run by hand.
- `pyproject.toml` declares a dozen optional extras (`ai`, `idb`, `web`, `visual`, `mcp`, `bedrock`,
  `server`, `db`, `oauth`, `schema`, `s3`, `gcs`, `cloud`, …), each imported lazily behind its own
  guard, but no single step maps "the extras a *specific* project's config needs" to "the extras
  presently installed" and closes the gap.

The practical effect: a fresh contributor (or a team standing up their own bajutsu-driven test
repo) only discovers a missing piece when a command fails deep inside a run — `no available
actuator among ['idb']`, an `ImportError` from an unguarded lazy import, or a `preflight` failure —
and then has to reverse-engineer which of several install paths applies. And every future backend
(Android — [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), Flutter —
[BE-0008](../BE-0008-flutter-support/BE-0008-flutter-support.md)) is on track to repeat this same
pattern from scratch — its own ad hoc Makefile target or shell script — unless there is one
canonical, extensible place this logic lives.

This is squarely an onboarding concern (the same territory as
[BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)'s `doctor` / `preflight`), but
it is deliberately a separate item rather than folded into BE-0024: BE-0024 diagnoses ("is the
environment ready?"), while this item *acts* ("make the environment ready") — a materially
different capability (it runs package managers and downloads binaries) that BE-0024's own
Detailed design explicitly says now gets its own proposal rather than absorbing it as a catch-all
candidate.

## Detailed design

The work breaks down into three independent, MECE (mutually exclusive, collectively exhaustive)
pieces:

1. **One declarative source of truth for "what a backend/extra needs."** Today the same facts are
   hardcoded in three places that can drift independently: the Brewfile + `scripts/serve.sh`'s
   presence checks + `preflight.py`'s remedy strings, all describing the idb requirement; the web
   backend's requirement (`uv sync --extra web` + `playwright install chromium`) exists only as
   prose in a remedy string, nowhere machine-readable. This item introduces a single mapping — per
   backend family (`idb` / `web` / `fake`) and per optional capability (`ai`, `visual`, `mcp`, …) —
   of: the pip extra name, an external-tool presence check (a `command -v` probe), and how to
   install it when missing (a Homebrew formula reference for macOS-only tools, a `playwright
   install <browser>` invocation, or "none needed"). `preflight.py`'s remedy strings, `make deps`,
   and the new installer all read from this one mapping instead of maintaining independent copies.
2. **A config-aware install step.** Given a project's effective config (the same `--config`
   resolution `run` / `doctor` already use), resolve which backends are actually referenced by
   `targets.*.backend` (reusing `backends.py`'s existing resolution logic) and whether an AI
   provider is configured, then install only what those targets need — not "idb unconditionally"
   (today's `make deps`) and not "everything" (no such option exists today). Each step is
   idempotent, following the existing pattern in `scripts/serve.sh` (`[ ! -x .venv/bin/idb ]`,
   `command -v idb_companion`) — nothing is reinstalled once already present, and the step is safe
   to re-run on every `make setup`.
3. **Two entry points that share one implementation.** A Makefile target for contributors (folding
   `make setup` + `make deps` into one step, or introducing `make install` alongside them, meant to
   run right after `git clone` — the same moment `make setup` runs today), and the underlying logic
   factored into a script the Makefile calls (not duplicated inline in `Makefile` itself), so it
   stays a single implementation contributors and any downstream project's own Makefile can invoke
   the same way `scripts/serve.sh` and `scripts/preflight.sh` already are.

This item stays a **local, developer-invoked** setup step — it is never wired into a hosted or
multi-tenant path (`bajutsu serve --backend=server`, an uploaded-bundle run). Running package
managers and downloading binaries on behalf of an uploaded config would cross the boundary
[BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md)
deliberately closed (govern and sandbox command execution from uploaded bundle configs) — so this
installer is something a person runs against their own clone, not something a server executes on a
config it received over the network.

None of this touches the deterministic `run` / CI gate: it is pure environment bootstrap, runs
before any scenario executes, and never becomes part of a pass/fail decision (prime directive #1).
It also stays backend-agnostic by construction (prime directive #3) — the mapping in step 1 is
exactly where a new backend's requirements plug in, rather than a fork of the installer logic.

## Alternatives considered

**Extend `bajutsu doctor` with a `--fix` flag that installs what it diagnosed missing**, turning the
CLI subcommand itself into the installer. This is an appealing complement — once the mapping in
step 1 exists, `doctor`'s remedy strings could shell out to the same install functions instead of
just printing them — but it puts the mechanism behind a Typer CLI command that only exists *after*
the Python package is already installed and importable, which doesn't help the more fundamental gap
this item targets: what to run right after `git clone`, before any Python environment exists yet.
Worth revisiting as a thin follow-up once the installer's logic is centralized, not as a
replacement for it.

**Keep the per-backend ad hoc scripts as the pattern** (a new `scripts/<backend>-serve-equivalent.sh`
each time a backend gains a corner case). Rejected: it is exactly today's status quo, doesn't scale
to Android/Flutter, and leaves the same facts (which extra, which external tool) duplicated across
the Brewfile, `scripts/serve.sh`, and `preflight.py`'s remedy text.

**Install implicitly and silently the first time a command needs something** (no explicit step at
all). Rejected: `scripts/serve.sh` already does a narrow version of this for idb, and generalizing
it further (auto-installing on every command, for every backend) would run package managers and
download binaries without the user having asked for it at that moment — a bigger and more
surprising side effect than the existing, narrowly-scoped precedent.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Declarative mapping of backend/extra → pip extra + external-tool check + install command
- [ ] Config-aware install step (resolve configured backends, install only what's needed, idempotent)
- [ ] Shared Makefile target + script entry point (supersedes/folds `make deps`, callable right after `git clone`)

## References

- [`docs/architecture.md`](../../docs/architecture.md) — module list and implementation status
- [`README.md`](../../README.md#setup) — current Setup / Requirements sections
- [`Brewfile`](../../Brewfile) · [`scripts/serve.sh`](../../scripts/serve.sh) · [`bajutsu/preflight.py`](../../bajutsu/preflight.py)
- [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md) — doctor / onboarding
- [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md) — govern and sandbox command execution from uploaded bundle configs
- [BE-0111](../BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency.md) — AI SDK as an optional extra
