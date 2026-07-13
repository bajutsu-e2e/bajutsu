**English** · [日本語](BE-XXXX-config-relative-path-resolution-ja.md)

# BE-XXXX — Resolve config-declared paths relative to the config file

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-config-relative-path-resolution.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Configuration sourcing |
<!-- /BE-METADATA -->

## Introduction

A local config's path-shaped fields — `scenarios`, `baselines`, `schemas`, `goldens`, the iOS/Android
`appPath`, and `xcuitest.testRunner` — resolve today against the invoking process's working directory,
not against the config file that declares them. This item anchors them to the config file's own
directory instead, so the same file resolves the same way no matter where `bajutsu` is invoked from.
A local config is operator-trusted, so its paths are **not** confined to a subtree: they may point at a
sibling directory outside the config's folder — which the repo's own configs need (the showcase's
`xcuitest.testRunner` lives under the repo-root `BajutsuKit/`, a sibling of `demos/`). This is the
distinction from BE-0063's Git-source rebase, which stays confined because a fetched config is not
trusted.

## Motivation

`_load_effective_with_source` (`bajutsu/cli/_shared.py:174-235`) already rebases a config's path fields
against a stable root instead of the caller's cwd — but only when the config comes from a Git source: it
calls `eff.rebased(root)` with `root` set to the materialized checkout (BE-0063). When the config is
local (`spec is None`), it short-circuits and returns `eff` untouched (`_shared.py:229-230`), so the
YAML's relative paths stay interpreted against whatever directory `bajutsu` happened to be invoked from.

That makes the same config file behave differently depending on where it's run from. A scenario suite
that resolves cleanly from the repo root can point at nothing — or, worse, silently resolve to an
unrelated file that happens to exist — when run from a subdirectory, a sibling worktree, or a CI job
whose working-directory convention differs from a contributor's shell. `make worktree` (BE-0069) makes
running the same config from a different checkout path routine, which is exactly the situation this
footgun lives in: a config that "just worked" in one checkout isn't guaranteed to behave the same when
the cwd convention it was invoked under doesn't carry over.

The scenario side already closed this same gap — `use:` / `dataFile` (BE-0174) and `setup` resolve
relative to the referring scenario file. Config-side path fields are the one part of the system still
anchored to the cwd instead of the declaring file.

One premise needs stating precisely, because it shapes the design. BE-0063 does **not** anchor at the
config file's own directory; it anchors at the **checkout root** — the top of the fetched tree, which is
typically an ancestor of the config (a config at `e2e/bajutsu.config.yaml` resolves `e2e/scenarios`, a
path written from the checkout root, not `scenarios`). A local config has no checkout, so the natural
stable anchor is instead the config file's own directory. These are related but distinct anchors, and
the difference is why the repo's own configs — authored to run from the repo root with repo-root-relative
paths — all need rewriting to be config-directory-relative (see *Detailed design*).

Confinement, likewise, is not a property of "being a Git source" but of **trust**. The repo already draws
this line: a fetched Git-API config and an uploaded bundle are untrusted and rebased *with* confinement
(`bajutsu/serve/operations/config.py:471`, `bajutsu/serve/operations/upload.py`), while a local file
config is marked operator-trusted and is not (`bajutsu/serve/operations/config.py:435`, "a local file config is operator-trusted
(BE-0121)"). Applying confinement to a local config would reject the showcase's
`xcuitest.testRunner: BajutsuKit/Runner/...` — a repo-root sibling of `demos/` that cannot be reached
from `demos/showcase/` without a `../` escape — so this item leaves local paths unconfined, consistent
with that existing trust boundary.

## Detailed design

1. **Give `Effective.rebased()` a `confine` switch.** `rebased(root, *, confine=True)`
   (`bajutsu/config.py:580-634`) already joins every field in scope against `root` — `scenarios`,
   `baselines`, `schemas`, `goldens`, the iOS/Android sub-config's `app_path`, and
   `xcuitest.test_runner`. Its escape check (an absolute or `../`-escaping value raises `ValueError`,
   mirroring the serve-hardening confinement, BE-0051) becomes conditional on `confine`. Existing
   callers (the Git-source and uploaded-bundle paths) keep `confine=True` unchanged; a local config
   passes `confine=False`. Update the docstring, since "only a Git source calls this" (`config.py:587`)
   stops being true.
2. **Rebase local configs at the call site — without repurposing `checkout_root`.** In
   `_load_effective_with_source` (`bajutsu/cli/_shared.py:196-235`), when `spec is None` (a local
   config), rebase against a *local* anchor (`cfg_path.resolve().parent`) via
   `eff.rebased(local_root, confine=False)`, but keep returning `None` for the function's third tuple
   element (`checkout_root`) — don't reuse the `root` variable for both. `checkout_root` is not just a
   rebase anchor; other call sites read it as a Git-vs-local signal: `run.py`'s on-demand
   `build_if_missing` fires only `if checkout_root is not None`, and `record`/`crawl`'s
   `_refuse_out_in_checkout` treats a non-`None` value as "this is a read-only Git checkout, refuse
   writing output inside it." Returning the config's own directory there too would silently switch on
   both Git-only behaviors for every local config. `source` (Git provenance) stays `None` either way;
   the returned `Effective` carries absolute path fields rooted at the config's directory, so every
   downstream consumer that reads *those fields* is cwd-independent for free.
3. **Fields intentionally out of scope**, carried over from `rebased()`'s existing exclusions plus two
   new ones:
   - `build` (a shell command, e.g. `make -C demos/showcase`) — not a path field.
   - `setup` — already resolved relative to the scenario file that references it (BE-0174), not to the
     config.
   - `launchServer.cwd` — the working directory handed to a launched subprocess
     (`bajutsu/runner/launch_server.py:123`), not a reference to a file the config points at. Changing
     its default anchor is a distinct semantic decision (what a dev server's cwd defaults to), left for
     a separate item if it turns out to be a similar footgun.
   - `sandbox.dockerfile` — already anchored to a `bundle_root` concept (BE-0090,
     `bajutsu/runner/sandbox.py:130-206`), a third anchor distinct from both cwd and "declaring file";
     out of scope here.
4. **Fix serve's local-config binds to match.** serve never calls `_load_effective_with_source`; it
   resolves a config's path fields against `state.cwd` (`bajutsu/serve/state.py`, `serve/operations/jobs.py`),
   and its Git/upload binds already set `state.cwd` to the config's tree. Only the **local** binds are
   left anchored at serve's launch directory — startup with a non-Git `--config`
   (`bajutsu/cli/commands/serve.py`) and the fs file-browser bind (`serve/operations/config.py:433`). Set
   `state.cwd = config_path.parent` in those two local binds, mirroring the Git/upload pattern, so
   serve's spawned runs and its in-process scenario/app-path reads resolve from the config's directory
   too. The individual serve operations that call `load_config`/`resolve` only read metadata (backend,
   `id_namespaces`, `ai`, `bundleId`), so they need no change.
5. **This is a deliberate breaking change, not a compatibility shim.** Every local config's path fields
   switch from cwd-relative to config-directory-relative in one cutover; no fallback or opt-out flag.
   The repo's own configs are all authored repo-root-relative and run from the repo root, so they must be
   rewritten to config-directory-relative in the same change: `demos/demo.config.yaml`,
   `demos/showcase/showcase.config.yaml` (including `testRunner: ../../BajutsuKit/Runner/...`),
   `demos/docs-site/docs-site.config.yaml`, `demos/serve-ui/dogfood.config.yaml`, and
   `demos/web/demo.config.yaml`. The on-device conformance test builds its `Effective` via a raw
   `resolve()` that bypasses the loader (`tests/test_driver_conformance_ondevice.py:119`), so it must
   apply the same `rebased(config_dir, confine=False)`. Call the migration out plainly in the PR body and
   in `docs/configuration.md`.
6. **Update docs.** `docs/configuration.md:81` ("Relative to the run's cwd") and the BE-0063 section
   (`docs/configuration.md:330-369`, framed today as a Git-only rebase) need to describe the rule as it
   now stands: config-directory-relative for every source, confined only for untrusted (Git-API /
   uploaded) configs, unconfined for operator-trusted local files. Mirror in `docs/ja/configuration.md`.
7. **Tests.** Add coverage that a local config's path fields resolve against the config's directory
   independent of cwd (chdir elsewhere, assert unchanged), that a local config may point outside its own
   directory (no `ValueError`), and a `rebased(..., confine=False)` unit test alongside the existing
   confinement one. The on-device conformance lane and the demo-config rewrite are exercised only by the
   heavier CI lanes (`smoke (idb)` / `E2E` / `xcuitest` / `conformance`), not the Linux gate — flag that
   in the PR.

## Alternatives considered

- **Config-directory-relative *with* confinement (exact parity with BE-0063)**: this was the first shape
  considered. Rejected once it met the repo's own layout — the showcase's
  `xcuitest.testRunner: BajutsuKit/Runner/...` points at a repo-root sibling of `demos/`, which cannot be
  expressed from `demos/showcase/` without a `../` escape that confinement rejects. More fundamentally,
  confinement exists to contain an *untrusted* fetched config (BE-0051); a local file is already treated
  as operator-trusted (BE-0121), so confining it would add friction without a matching threat.
- **A per-field or per-config opt-in flag** to enable file-relative resolution: rejected — the safer
  interpretation should be the default, not something a config author has to discover and turn on. An
  opt-in leaves the footgun in place for everyone who doesn't set it.
- **Keep cwd-relative as the default; add a flag or config key to switch to file-relative**: rejected for
  the same reason, and it permanently doubles the resolution semantics a reader has to hold in mind
  instead of retiring the old one.
- **Also rebase `launchServer.cwd` to default to the config's directory**: deferred rather than folded
  in here — it's a different kind of field (a subprocess's working directory, not a reference to a file
  the config points at), and conflating the two would blur what this item is actually fixing. Revisit as
  its own item if it proves to be a similar footgun in practice.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add a `confine` switch to `Effective.rebased()`; update its docstring.
- [ ] Rebase local configs through `eff.rebased(cfg_path.resolve().parent, confine=False)` in
      `_load_effective_with_source`.
- [ ] Anchor serve's local-config binds at the config's directory (startup `--config`, fs bind).
- [ ] Rewrite the in-repo demo configs to config-directory-relative paths.
- [ ] Rebase the on-device conformance test's `Effective` against the config directory.
- [ ] Update `docs/configuration.md` and `docs/ja/configuration.md`.
- [ ] Add cwd-independence + unconfined-local-path tests, plus a `rebased(confine=False)` unit test.

## References

- [BE-0063 — Load config (and its scenario tree) from a Git repository + ref](../BE-0063-git-config-source/BE-0063-git-config-source.md)
  — the `Effective.rebased()` mechanism this item extends; its confinement stays on for untrusted Git
  sources.
- [BE-0174 — Contain scenario component and data refs within the suite root](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment.md)
  — the precedent for "relative to the referring file" on the scenario side.
- [BE-0051 — Serve hardening for hosting](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
  — the path-confinement discipline `rebased()`'s escape check mirrors, kept for untrusted sources.
- [BE-0090 — Govern and sandbox command execution from uploaded bundle configs](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md)
  — the separate `bundle_root` anchor concept this item leaves untouched.
- `docs/configuration.md:81`, `docs/configuration.md:330-369` — the current cwd-relative note and the
  BE-0063 Git-rebase section to be updated.
