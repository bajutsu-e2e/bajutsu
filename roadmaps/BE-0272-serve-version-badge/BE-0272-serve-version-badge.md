**English** · [日本語](BE-0272-serve-version-badge-ja.md)

# BE-0272 — Show bajutsu's running commit/version in the serve Web UI header

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0272](BE-0272-serve-version-badge.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0272") |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->

## Introduction

Add a small, always-visible indicator to the serve Web UI header showing which commit —
or, absent a Git checkout, which released version — of bajutsu itself is running. It sits
next to the existing config-provenance badge ([BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md)),
but answers a different question: not "where did the loaded config come from" but "which
build of the tool is serving this page."

## Motivation

The parallel-work guidance in `CLAUDE.md` (`make worktree`) means several checkouts of
this repository, each on a different branch or commit, can run their own `serve` instance
at the same time. Nothing on the running page today says which checkout's `serve` you're
looking at.

The only version identifier in the codebase, `bajutsu.__version__` (`bajutsu/__init__.py`),
is never surfaced in serve, and there is no CLI `--version` flag either.

The existing header badge is Git provenance for the *config*, not for the tool — the two
are easy to conflate since they'll sit in the same header.

## Detailed design

- **Backend.** Add an endpoint (or a field on an existing lightweight status endpoint)
  that reports the running server's own identity: the version string always, and — when
  the process's working directory is inside a Git checkout — a short commit SHA, the
  branch name, and a dirty flag (uncommitted changes present). These are read with Git
  plumbing commands (`git rev-parse --short HEAD`, `git rev-parse --abbrev-ref HEAD`,
  `git status --porcelain`) — a deterministic, no-LLM subprocess read, but a new mechanism
  for serve: the existing config-provenance stamp (`source_provenance` in
  `bajutsu/config_source.py`) instead resolves its commit via the GitHub API at bind time,
  and only for a remote `github:`-sourced config (a local file config has no Git provenance
  at all). These are read fresh on every request so a session left running while its
  checkout is edited stays accurate.
- When no `.git` is present (e.g. after `pip install bajutsu`), the response simply omits
  commit/branch/dirty, and the frontend shows only the version string.
- **Access control.** Decide during implementation whether this read is open to every
  visitor or role-gated. The branch name is the sensitive field: this repo's own
  convention (`CLAUDE.md`, "One topic per branch") names branches `claude/<topic>` or
  `<user>/<topic>`, which routinely encodes an in-progress BE slug, so exposing it on a
  hosted or shared deployment could leak what's being worked on. [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md)
  set the precedent by gating its wider-disclosure `/api/config/content` read to the
  `admin` role. The version string alone is not sensitive; a reasonable default is to keep
  version open and gate commit/branch/dirty (or at least the branch name) to `admin`.
- **Frontend.** A small badge in the header template, next to the config-provenance badge
  — e.g. `v0.0.0 · a1b2c3d (branch-name)` — with a distinct marker when dirty. Rendered by
  the appropriate `serve.*.js` module from the [BE-0202](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization.md)
  split.
- No LLM is involved anywhere in this path — it's a deterministic subprocess read, so it
  never touches the Tier-2 `run`/CI gate (prime directive 1). It concerns the tool's own
  identity rather than any per-app config, so the app-agnostic principle (prime directive
  3) doesn't apply here.

## Alternatives considered

- **Bake the commit SHA into the package at build time** (a setuptools-scm-style
  approach), so a pip-installed copy could report a commit too. Deferred: bajutsu isn't
  published to PyPI yet (`docs/ci.md` describes it as pre-release, vendored via
  submodule), so there's no build pipeline to hook into today. Revisit once packaging and
  publishing land.
- **Add it as a doctor-panel ([BE-0148](../BE-0148-serve-doctor/BE-0148-serve-doctor.md))
  check** instead of a header badge. Doctor is for on-demand readiness diagnosis — a
  panel the user opens to check. The commit/version indicator is closer to a persistent
  orientation cue than a diagnosis, so it belongs somewhere always visible rather than
  behind a panel open.
- **A dedicated "About" modal.** Rejected for the same reason: hiding it behind a click
  defeats the primary use case of glancing at the header to confirm which commit's
  `serve` instance is in front of you.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Backend: expose version (plus commit/branch/dirty when running from a Git checkout)
      via a status endpoint.
- [ ] Access control: decide and implement whether commit/branch/dirty are open or
      `admin`-gated (version stays open).
- [ ] Frontend: render the badge in the serve header next to the provenance badge.
- [ ] Docs: record the badge in `docs/architecture.md`'s implementation status (and its
      Japanese mirror) once shipped.

## References

- [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md) — the config
  provenance display this badge sits beside.
- [BE-0202](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization.md) — the
  serve.js module structure this extends.
- `CLAUDE.md`, "Isolate concurrent sessions with worktrees" — the multi-checkout scenario
  that motivates this item.
