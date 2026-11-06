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

This item extends the version badge from
[BE-0272](../BE-0272-serve-version-badge/BE-0272-serve-version-badge.md) to self-hosted Docker
deployments. In a container, the badge's Git-plumbing detection has no `.git` checkout to read from.
The item embeds the commit hash at `docker build` time through a build argument, and
`server_checkout()` falls back to that value when Git detection comes up empty. A self-hosted `serve`
behind a container then shows which commit it is running, the same way a local dev checkout already
does.

## Motivation

BE-0272 reads the commit, branch, and dirty flag with `git` plumbing anchored at bajutsu's own
package directory (`bajutsu/serve/operations/version.py`). Its "Alternatives considered" section
already flagged the gap. It deferred embedding the commit at build time because "bajutsu isn't
published to PyPI yet... there's no build pipeline to hook into today." A self-hosted Docker
deployment *is* such a build pipeline, and it exists today:
[`deploy/self-host/Dockerfile`](../../deploy/self-host/Dockerfile) already runs `docker build`
against a checkout of this repository.

That Dockerfile has no `.dockerignore` and does `COPY . /app`. If `.git` happens to be present in
the build context, BE-0272's detection works incidentally. The behavior is accidental and not a
supported contract, for two independent reasons:

- Copying `.git` wholesale into the image bloats the image and busts the `COPY` layer's build cache
  on every commit. That problem deserves a fix regardless of the version badge (see *Detailed
  design*).
- The actual self-hosted deployment is not `docker build .` run by hand against this checkout. An
  external deployment pipeline's own configuration, outside this repository, drives the build. This
  repository neither controls nor can assume what that pipeline's build context looks like, or
  whether it hands Docker a full working tree with `.git` intact.

An explicit build argument is a contract that this repository can define and document once. *Any*
deployment pipeline, whether this repo's own compose stack or an external one, can satisfy the
contract the same way: pass the commit under build at the moment of the build, instead of hoping the
image carries a usable `.git`.

## Detailed design

- **Build argument.** Add `ARG GIT_COMMIT=""` to
  [`deploy/self-host/Dockerfile`](../../deploy/self-host/Dockerfile), and embed it into the image as
  `ENV BAJUTSU_BUILD_COMMIT=$GIT_COMMIT`, following the existing `BAJUTSU_*` environment-variable
  convention (`bajutsu/config_source.py`, `bajutsu/serve/state.py`, and others). Left unset, the
  value stays empty and changes nothing, so the fallback below is a no-op.
- **Fallback read.** In `server_checkout()` (`bajutsu/serve/operations/version.py`), the
  `git rev-parse --short HEAD` read returns `None` when no `.git` checkout exists. In that case, read
  `BAJUTSU_BUILD_COMMIT` from the environment. If the value is non-empty, return it as `commit` with
  `branch: None` and `dirty: None`. A build-arg embedded value has no working branch or dirty-tree
  concept, so those fields keep their "unknown" defaults (`None`) and never fabricate a known "clean"
  value. Git detection stays the primary source and runs first, unchanged, so the fallback activates
  only in the cases BE-0272 already left as "nothing to report."
  Consider surfacing where the value came from (for example a `source: "git" | "build-arg"` field),
  so the frontend badge can render an embedded value distinctly. The absence of a branch name and a
  dirty marker then implies "embedded", not "clean checkout".
- **`.dockerignore`.** Add a repository-root `.dockerignore` that excludes `.git`, plus other
  build-irrelevant paths that are already gitignored (for example `.venv/`, `runs/`, and `tmp/`). The
  build context then no longer carries `.git` at all. This turns the build argument from "a fallback
  for when `.git` is missing" into "the only source". That is the more honest contract: a Docker
  image's commit identity comes from an explicit, documented input and not from whatever the build
  context happened to contain.
- **Wiring the build.** Document in [docs/self-hosting.md](../../docs/self-hosting.md) (and its
  Japanese mirror) how to pass the commit at build time, for example
  `docker build --build-arg GIT_COMMIT=$(git rev-parse HEAD) -f deploy/self-host/Dockerfile .`. The
  command mirrors the worker-image build command already documented there.
  [`docker-compose.yml`](../../deploy/self-host/docker-compose.yml) defines two services that build
  this Dockerfile (`migrate` and `bajutsu`). Extend the `build:` stanza of the `bajutsu` service,
  which serves the badge endpoint, to pass the same argument. Compose resolves `args` from the
  invoking shell's environment or a `.env` value, so `docker compose build` picks the value up
  without extra flags. The `migrate` service runs Alembic and never serves `server_checkout()`, so it
  needs no build arg.
  An external deployment pipeline that builds this Dockerfile on its own, outside this repo's compose
  stack, is beyond this repository's scope to change. The build-argument contract this item defines
  is exactly what such a pipeline must supply to get a working badge. This item makes that possible,
  and wiring a particular external pipeline to pass the argument is a follow-up for whoever owns that
  pipeline.
- **Prime directives.** No LLM is involved anywhere in this path, because the fallback is a
  deterministic environment-variable read (prime directive 1). The mechanism covers the tool's own
  build identity and is not a per-app concern, so the app-agnostic principle (prime directive 3) does
  not apply.

## Alternatives considered

- **Always prefer the build-arg value when present, even over a live `.git` read.** Rejected. A
  checkout-based `serve`, the common dev-loop case BE-0272 was built for, should keep reflecting
  live, per-request state (for example a session left running while the checkout is edited). An
  unset build arg must never shadow that state. Git detection stays first, and the build arg only
  fills the gap that Git detection already leaves as "nothing to report."
- **A setuptools-scm-style version bump embedded into the package itself**, so that a
  `pip install`-ed copy could also report a commit. This is the general case BE-0272 deferred, and
  the case stays deferred here, because it needs a build and publish pipeline that bajutsu does not
  have yet. This item stays scoped to the Docker path that this repository already ships and
  controls (`deploy/self-host/Dockerfile`), which does not need that pipeline.
- **Skip the `.dockerignore` change and rely on the build arg as a pure fallback.** Considered, but
  leaving `.git` copyable keeps the image-bloat and cache-bust problem that BE-0272's incidental
  behavior causes, and the version badge would keep depending silently on whatever the build context
  happens to contain. Fixing both problems in the same item is a small, cohesive change, because both
  touch the same Dockerfile and share the same motivation (do not rely on incidental `.git`
  presence).

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
