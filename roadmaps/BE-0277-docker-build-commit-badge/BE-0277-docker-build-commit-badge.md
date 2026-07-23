**English** · [日本語](BE-0277-docker-build-commit-badge-ja.md)

# BE-0277 — Embed the commit hash into self-hosted Docker images for the version badge

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0277](BE-0277-docker-build-commit-badge.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0277") |
| Implementing PR | [#1133](https://github.com/bajutsu-e2e/bajutsu/pull/1133) |
| Topic | Hosting the web UI |
<!-- /BE-METADATA -->

## Introduction

Extend the version badge from [BE-0272](../BE-0272-serve-version-badge/BE-0272-serve-version-badge.md)
to self-hosted Docker deployments, where its Git-plumbing detection has no
`.git` checkout to read from. Embed the commit hash at `docker build` time via
a build argument, and have `server_checkout()` fall back to it when Git
detection comes up empty — so a self-hosted `serve` behind a container still
shows which commit it's running, the same way a local dev checkout already
does.

## Motivation

BE-0272 reads the commit/branch/dirty flag with `git` plumbing anchored at
bajutsu's own package directory (`bajutsu/serve/operations/version.py`), and
its own "Alternatives considered" section already flagged this: embedding the
commit at build time was deferred because "bajutsu isn't published to PyPI
yet... there's no build pipeline to hook into today." A self-hosted Docker
deployment *is* such a build pipeline, and it's available today —
[`deploy/self-host/Dockerfile`](../../deploy/self-host/Dockerfile) already
runs `docker build` against a checkout of this repository.

Today that Dockerfile has no `.dockerignore` and does `COPY . /app`, so if
`.git` happens to be present in the build context, BE-0272's detection
incidentally works — but that's accidental, not a supported contract:

- Copying `.git` wholesale into the image bloats it and busts the `COPY`
  layer's build cache on every commit, which is itself worth fixing
  regardless of the version badge (see *Detailed design*).
- The actual self-hosted deployment isn't `docker build .` run by hand against
  this checkout — it's driven by an external deployment pipeline's own
  configuration, outside this repository. What that pipeline's build context
  looks like, and whether it hands Docker a full working tree with `.git`
  intact, isn't something this repository controls or can assume.

So relying on incidental `.git` presence is fragile from two independent
directions. An explicit build argument is a contract this repository can
define and document once, and *any* deployment pipeline — this repo's own
compose stack, or an external one — can satisfy it the same way: pass the
commit it's building at the moment it builds, rather than hoping the image
happens to carry a usable `.git`.

## Detailed design

- **Build argument.** Add `ARG GIT_COMMIT=""` to
  [`deploy/self-host/Dockerfile`](../../deploy/self-host/Dockerfile) and embed
  it into the image as `ENV BAJUTSU_BUILD_COMMIT=$GIT_COMMIT`, following the
  existing `BAJUTSU_*` environment-variable convention
  (`bajutsu/config_source.py`, `bajutsu/serve/state.py`, etc.). Left unset, it
  stays empty and changes nothing — the fallback below is a no-op.
- **Fallback read.** In `server_checkout()`
  (`bajutsu/serve/operations/version.py`), when the `git rev-parse --short
  HEAD` read returns `None` (no `.git` checkout), read
  `BAJUTSU_BUILD_COMMIT` from the environment; if it's non-empty, return it as
  `commit` with `branch: None` and `dirty: None` — a build-arg embedded value
  has no working branch or dirty-tree concept, so those fields stay their
  "unknown" defaults (`None`) rather than fabricating a known "clean" value. Git detection stays the
  primary source and runs first, unchanged, so this only ever activates the
  cases BE-0272 already left as "nothing to report."
  Consider surfacing where the value came from (e.g. a `source: "git" |
  "build-arg"` field) so the frontend badge can render it distinctly (no
  branch name / dirty marker implies "embedded", not "clean checkout").
- **`.dockerignore`.** Add a repository-root `.dockerignore` excluding `.git`
  (and other build-irrelevant paths already gitignored, e.g. `.venv/`,
  `runs/`, `tmp/`) so the build context no longer incidentally carries `.git`
  at all. This turns the build argument from "a fallback for when `.git` is
  missing" into "the only source," which is the more honest contract: a
  Docker image's commit identity comes from an explicit, documented input,
  not from whatever the build context happened to contain.
- **Wiring the build.** Document in
  [docs/self-hosting.md](../../docs/self-hosting.md) (and its Japanese mirror)
  how to pass the commit at build time, e.g.
  `docker build --build-arg GIT_COMMIT=$(git rev-parse HEAD) -f
  deploy/self-host/Dockerfile .` — mirroring the existing worker-image build
  command already documented there.
  [`docker-compose.yml`](../../deploy/self-host/docker-compose.yml) defines
  two services that build this Dockerfile (`migrate`, `bajutsu`); extend the
  `bajutsu` service's `build:` stanza — the one that actually serves the badge
  endpoint — to pass the same argument (Compose resolves `args` from the
  invoking shell's environment or a `.env` value), so `docker compose build`
  picks it up without extra flags. The `migrate` service runs Alembic and
  never serves `server_checkout()`, so it needs no build arg.
  An external deployment pipeline that builds this Dockerfile on its own
  (rather than through this repo's compose stack) is outside this
  repository's scope to change, but the build-argument contract this item
  defines is exactly what such a pipeline needs to supply to get a working
  badge — this item makes that possible; wiring any particular external
  pipeline to actually pass it is a follow-up for whoever owns that pipeline.
- No LLM is involved anywhere in this path — the fallback is a deterministic
  environment-variable read (prime directive 1). It's a mechanism for the
  tool's own build identity, not a per-app concern, so the app-agnostic
  principle (prime directive 3) doesn't apply.

## Alternatives considered

- **Always prefer the build-arg value when present, even over a live `.git`
  read.** Rejected: a checkout-based `serve` (the common dev-loop case
  BE-0272 was built for) should keep reflecting live, per-request state (e.g.
  a session left running while the checkout is edited); an unset build arg
  must never shadow that. Git detection stays first; the build arg only fills
  the gap Git detection already leaves as "nothing to report."
- **A setuptools-scm-style version-bump embedded into the package itself**, so a
  `pip install`-ed copy could also report a commit. This is the general case
  BE-0272 deferred, and it's still deferred here too: it needs a build/publish
  pipeline bajutsu doesn't have yet. This item stays scoped to the Docker path
  this repository already ships and controls
  (`deploy/self-host/Dockerfile`), which doesn't need that pipeline.
- **Skip the `.dockerignore` change and rely on the build arg as a pure
  fallback.** Considered, but leaving `.git` copyable means the image-bloat /
  cache-bust problem BE-0272's incidental behavior causes stays unfixed, and
  the version badge would keep silently depending on whatever the build
  context happens to contain. Fixing both in the same item is a small,
  cohesive change since they touch the same Dockerfile and the same
  motivation (don't rely on incidental `.git` presence).

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `deploy/self-host/Dockerfile`: add `ARG GIT_COMMIT=""` and embed it into
      `ENV BAJUTSU_BUILD_COMMIT`.
- [x] `bajutsu/serve/operations/version.py`: fall back to
      `BAJUTSU_BUILD_COMMIT` in `server_checkout()` when Git detection finds
      no checkout; adds a `source: "git" | "build-arg" | null` field the
      frontend badge uses to render an embedded commit distinctly.
- [x] Root `.dockerignore`: exclude `.git` and other build-irrelevant,
      already-gitignored paths.
- [x] `docker-compose.yml`: pass the build argument through `build.args`.
- [x] Docs: `docs/self-hosting.md` and its Japanese mirror document the
      `--build-arg GIT_COMMIT=$(git rev-parse HEAD)` invocation.

Log:

- Implemented in [#1133](https://github.com/bajutsu-e2e/bajutsu/pull/1133): build
  arg + `BAJUTSU_BUILD_COMMIT` embed, `server_checkout()` fallback with a
  `source` field, badge tooltip, root `.dockerignore`, compose `build.args`,
  and the bilingual self-hosting docs.

## References

- [BE-0272](../BE-0272-serve-version-badge/BE-0272-serve-version-badge.md) —
  the version badge and its Git-plumbing detection this item extends; its own
  "Alternatives considered" section deferred build-time embedding pending a
  build pipeline, which the self-hosted Docker path already is.
- [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) —
  the self-hosted Tier B control plane (`deploy/self-host/`) this item's
  Dockerfile belongs to.
- [`deploy/self-host/Dockerfile`](../../deploy/self-host/Dockerfile),
  [`deploy/self-host/docker-compose.yml`](../../deploy/self-host/docker-compose.yml) —
  the files this item changes.
