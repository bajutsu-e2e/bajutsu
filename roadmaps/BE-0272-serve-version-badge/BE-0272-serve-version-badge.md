**English** · [日本語](BE-0272-serve-version-badge-ja.md)

# BE-0272 — Show bajutsu's running commit/version in the serve Web UI header

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0272](BE-0272-serve-version-badge.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0272") |
| Implementing PR | [#1117](https://github.com/bajutsu-e2e/bajutsu/pull/1117), [#1124](https://github.com/bajutsu-e2e/bajutsu/pull/1124) |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->

## Introduction

Add a small, always-visible indicator to the serve Web UI header. It shows which commit of bajutsu
itself is running, or which released version when no Git checkout exists. The indicator sits beside
the `bajutsu` wordmark and stays distinct from the config-provenance badge
([BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md)). The two answer different
questions. The config badge answers "where did the loaded config come from". The new indicator answers
"which build of the tool is serving this page".

## Motivation

The parallel-work guidance in `CLAUDE.md` (`make worktree`) means that several checkouts of this repository, each on a different branch or commit, can run their own `serve` instance at the same time. Nothing on the running page says which checkout's `serve` a user is looking at.

The only version identifier in the codebase is `bajutsu.__version__` (`bajutsu/__init__.py`). `serve` never surfaces it, and the CLI has no `--version` flag either.

The existing header badge shows Git provenance for the *config*, not for the tool. The two badges sit in the same header, so a reader can easily conflate them.

## Detailed design

**Backend.** Add an endpoint, or a field on an existing lightweight status endpoint, that reports the running server's own identity:

- The version string, always.
- A short commit SHA, the branch name, and a dirty flag (uncommitted changes present), when the process's working directory is inside a Git checkout.

The server reads the Git fields with Git plumbing commands: `git rev-parse --short HEAD`, `git rev-parse --abbrev-ref HEAD`, and `git status --porcelain`. This is a deterministic, no-LLM subprocess read, but it is a new mechanism for serve. The existing config-provenance stamp (`source_provenance` in `bajutsu/config_source.py`) resolves its commit through the GitHub API at bind time instead. It does so only for a remote `github:`-sourced config, because a local file config has no Git provenance at all. The server reads the Git fields fresh on every request, so a session left running while its checkout is edited stays accurate.

When no `.git` is present (for example after `pip install bajutsu`), the response omits commit, branch, and dirty, and the frontend shows only the version string.

**Access control.** The implementation decides whether this read is open to every visitor or gated by role. The branch name is the sensitive field. This repo's own convention (`CLAUDE.md`, "One topic per branch") names branches `claude/<topic>` or `<user>/<topic>`, and such names routinely encode an in-progress BE slug. Exposing the branch name on a hosted or shared deployment could therefore leak what is being worked on.

[BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md) set the precedent. It gates its wider-disclosure `/api/config/content` read to the `admin` role. The version string alone is not sensitive. A reasonable default keeps the version open and gates commit, branch, and dirty (or at least the branch name) to `admin`.

**Frontend.** A small badge in the header template sits beside the `bajutsu` wordmark, for example `v0.0.0 · a1b2c3d (branch-name)`, with a distinct marker when the checkout is dirty. The appropriate `serve.*.js` module from the [BE-0202](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization.md) split renders the badge.

**Prime directives.** No LLM is involved anywhere in this path. The read is a deterministic subprocess call, so it never touches the Tier-2 `run`/CI gate (prime directive 1). The path concerns the tool's own identity, not any per-app config, so the app-agnostic principle (prime directive 3) does not apply here.

## Alternatives considered

- **Bake the commit SHA into the package at build time** (a setuptools-scm-style approach), so that a pip-installed copy could report a commit too. Deferred. bajutsu is not yet published to PyPI (`docs/ci.md` describes it as pre-release and vendored through a submodule), so there is no build pipeline to hook into today. The idea can be revisited once packaging and publishing land.
- **Add the indicator as a check in the doctor panel ([BE-0148](../BE-0148-serve-doctor/BE-0148-serve-doctor.md)), instead of a header badge.** Rejected. The doctor panel is for on-demand readiness diagnosis, and a user opens it to check. The commit and version indicator is closer to a persistent orientation cue than to a diagnosis. It belongs somewhere always visible, not behind an opened panel.
- **A dedicated "About" modal.** Rejected for the same reason. Hiding the indicator behind a click defeats the primary use case, which is to glance at the header and confirm which commit's `serve` instance is in front of the user.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Backend: expose version (plus commit/branch/dirty when running from a Git checkout)
      via a status endpoint. Split into two GETs (`bajutsu/serve/operations/version.py`):
      `GET /api/version` (open) and `GET /api/version/checkout` (Git plumbing, read per request).
      Git is anchored at bajutsu's own package dir, not the process CWD, so a bound `github:`
      config's checkout can't be mistaken for the tool's own build.
- [x] Access control: version stays open; commit/branch/dirty are `admin`-gated via an early
      GET case in `bajutsu/serve/authz.py` (a branch name can encode an in-progress topic).
- [x] Frontend: `#appversion` badge in the serve header (`serve.html.j2`), populated by
      `loadVersion()` in `serve.core.mjs` — version always, `· <sha> (branch)` + a dirty marker
      when the admin-gated checkout read succeeds. Placed beside the `bajutsu` wordmark (a
      `.brandrow` next to the title), not among the nav tabs.
- [x] Docs: recorded in `docs/architecture.md`'s implementation status and its Japanese mirror.

### Log

- (this PR) Implemented the two endpoints + authz gate + header badge, with operations-level
  and HTTP tests (`tests/serve/test_version_ops.py`, `tests/serve/test_http_version.py`) and RBAC
  coverage (`tests/serve/test_rbac.py`). Flipped Status → Implemented.
- [#1124](https://github.com/bajutsu-e2e/bajutsu/pull/1124) — moved the badge from the tail of the
  nav-tab row to a `.brandrow` beside the `bajutsu` wordmark; markup/endpoints/`loadVersion()`
  unchanged. Updated this item's prose to match.

## References

- [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md) — the config
  provenance display this badge is kept distinct from (it sits beside the title instead).
- [BE-0202](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization.md) — the
  serve.js module structure this extends.
- `CLAUDE.md`, "Isolate concurrent sessions with worktrees" — the multi-checkout scenario
  that motivates this item.
