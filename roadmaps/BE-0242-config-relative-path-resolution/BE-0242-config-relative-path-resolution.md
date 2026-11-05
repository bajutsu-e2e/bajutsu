**English** · [日本語](BE-0242-config-relative-path-resolution-ja.md)

# BE-0242 — Resolve config-declared paths relative to the config file

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0242](BE-0242-config-relative-path-resolution.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0242") |
| Implementing PR | [#980](https://github.com/bajutsu-e2e/bajutsu/pull/980) |
| Topic | Configuration sourcing |
<!-- /BE-METADATA -->

## Introduction

A local config has six path-shaped fields: `scenarios`, `baselines`, `schemas`, `goldens`, the
iOS and Android `appPath`, and `xcuitest.testRunner`. Today they resolve against the invoking
process's working directory, not against the config file that declares them.

This item anchors them to the config file's own directory instead. The same file then resolves the
same way no matter where `bajutsu` is invoked from.

A local config is operator-trusted. Its paths are **not** confined to a subtree. They may
point at a sibling directory outside the config's folder, and the repo's own configs need that. The
showcase's `xcuitest.testRunner` lives under the repo-root `BajutsuKit/`, a sibling of `demos/`.

This is the distinction from BE-0063's Git-source rebase, which stays confined because a fetched
config is not trusted.

## Motivation

`_load_effective_with_source` (`bajutsu/cli/_shared.py:174-235`) already rebases a config's path
fields against a stable root instead of the caller's cwd. It does so when the config comes from
a Git source alone: it calls `eff.rebased(root)` with `root` set to the materialized checkout (BE-0063).
When the config is local (`spec is None`), the function short-circuits and returns `eff` untouched
(`_shared.py:229-230`). The YAML's relative paths then stay interpreted against whatever directory
`bajutsu` happened to be invoked from.

That makes the same config file behave differently depending on where it runs. A scenario suite that
resolves cleanly from the repo root can point at nothing when run from a subdirectory, a sibling
worktree, or a CI job whose working-directory convention differs from a contributor's shell. Worse,
it can resolve, with no warning, to an unrelated file that happens to exist.

`make worktree` (BE-0069) makes running the same config from a different checkout path routine. That
is the situation this footgun lives in. A config that worked in one checkout may behave differently
when the cwd convention of the earlier invocation does not carry over.

The scenario side already closed this same gap. `use:` / `dataFile` (BE-0174) and `setup` resolve
relative to the referring scenario file. Config-side path fields are the one part of the system still
anchored to the cwd instead of the declaring file.

One premise shapes the design, so it needs stating precisely. BE-0063 does **not** anchor at the
config file's own directory. It anchors at the **checkout root**, the top of the fetched tree. That
root is typically an ancestor of the config. A config at `e2e/bajutsu.config.yaml` resolves
`e2e/scenarios`, a path written from the checkout root, not `scenarios`.

A local config has no checkout. The natural stable anchor is instead the config file's own
directory. The two anchors differ, though they are related. That difference is why the repo's own configs
must all be rewritten to be config-directory-relative. They were authored to run from the repo root
with repo-root-relative paths (see *Detailed design*).

Confinement, likewise, is not a property of "being a Git source". It follows from **trust**. The
repo already draws this line.

- A fetched Git-API config and an uploaded bundle are untrusted. They are rebased *with* confinement
  (`bajutsu/serve/operations/config.py:471`, `bajutsu/serve/operations/upload.py`).
- A local file config is marked operator-trusted and is not confined
  (`bajutsu/serve/operations/config.py:435`, "a local file config is operator-trusted (BE-0121)").

Confinement would reject the showcase's `xcuitest.testRunner: BajutsuKit/Runner/...`.
That path is a repo-root sibling of `demos/`. Reaching it from `demos/showcase/` needs a `../` escape. This item leaves local paths unconfined, consistent with the existing trust boundary.

## Detailed design

1. **Give `Effective.rebased()` a `confine` switch.**
   - `rebased(root, *, confine=True)` (`bajutsu/config.py:580-634`) already joins every field in scope
     against `root`. The fields are `scenarios`, `baselines`, `schemas`, `goldens`, the iOS/Android
     sub-config's `app_path`, and `xcuitest.test_runner`.
   - Its escape check becomes conditional on `confine`. The check raises `ValueError` for an absolute
     or `../`-escaping value, mirroring the serve-hardening confinement (BE-0051).
   - Existing callers (the Git-source and uploaded-bundle paths) keep `confine=True` unchanged. A
     local config passes `confine=False`.
   - Update the docstring, since "only a Git source calls this" (`config.py:587`) stops being true.
2. **Rebase local configs at the call site, without repurposing `checkout_root`.**
   - In `_load_effective_with_source` (`bajutsu/cli/_shared.py:196-235`), when `spec is None` (a local
     config), rebase against a *local* anchor (`cfg_path.resolve().parent`) via
     `eff.rebased(local_root, confine=False)`.
   - Keep returning `None` for the function's third tuple element (`checkout_root`). Do not reuse the
     `root` variable for both.
   - `checkout_root` is not just a rebase anchor. Other call sites read it as a Git-vs-local signal.
     `run.py`'s on-demand `build_if_missing` fires `if checkout_root is not None` and nowhere else. The
     `_refuse_out_in_checkout` check in `record` / `crawl` treats a non-`None` value as "this is a
     read-only Git checkout, refuse writing output inside it". Returning the config's own directory
     there too would switch on both Git-only behaviors for every local config.
   - `source` (Git provenance) stays `None` either way. The returned `Effective` carries absolute path
     fields rooted at the config's directory. Every downstream consumer that reads *those fields* is
     cwd-independent for free.
3. **Fields intentionally out of scope.** They carry over from `rebased()`'s existing exclusions, plus
   two new ones.
   - `build` is a shell command, for example `make -C demos/showcase`. This is not a path field.
   - `setup` is already resolved relative to the scenario file that references it (BE-0174), not to
     the config.
   - `launchServer.cwd` is the working directory handed to a launched subprocess
     (`bajutsu/runner/launch_server.py:123`). It does not reference a file the config points at.
     Changing its default anchor is a distinct semantic decision: what a dev server's cwd defaults to.
     A separate item can take it up if it turns out to be a similar footgun.
   - `sandbox.dockerfile` is already anchored to a `bundle_root` concept (BE-0090,
     `bajutsu/runner/sandbox.py:130-206`). That is a third anchor, distinct from both cwd and
     "declaring file", and out of scope here.
4. **Fix serve's local-config binds to match.**
   - serve never calls `_load_effective_with_source`. It resolves a config's path fields against
     `state.cwd` (`bajutsu/serve/state.py`, `serve/operations/jobs.py`). Its Git and upload binds
     already set `state.cwd` to the config's tree.
   - The **local** binds alone are left anchored at serve's launch directory. They are startup with a
     non-Git `--config` (`bajutsu/cli/commands/serve.py`) and the fs file-browser bind
     (`serve/operations/config.py:433`).
   - Set `state.cwd = config_path.parent` in those two local binds, mirroring the Git and upload
     pattern. Serve's spawned runs and its in-process scenario and app-path reads then resolve from
     the config's directory too.
   - The individual serve operations that call `load_config` / `resolve` read metadata alone
     (backend, `id_namespaces`, `ai`, `bundleId`). They need no change.
5. **This is a deliberate breaking change, not a compatibility shim.**
   - Every local config's path fields switch from cwd-relative to config-directory-relative in one
     cutover. There is no fallback and no opt-out flag.
   - The repo's own configs are all authored repo-root-relative and run from the repo root. They must
     be rewritten to config-directory-relative in the same change: `demos/demo.config.yaml`,
     `demos/showcase/showcase.config.yaml` (including `testRunner: ../../BajutsuKit/Runner/...`),
     `demos/docs-site/docs-site.config.yaml`, `demos/serve-ui/dogfood.config.yaml`, and
     `demos/web/demo.config.yaml`.
   - The on-device conformance test builds its `Effective` via a raw `resolve()` that bypasses the
     loader (`tests/test_driver_conformance_ondevice.py:119`). It must apply the same
     `rebased(config_dir, confine=False)`.
   - Call the migration out plainly in the PR body and in `docs/configuration.md`.
6. **Update docs.**
   - `docs/configuration.md:81` ("Relative to the run's cwd") and the BE-0063 section
     (`docs/configuration.md:330-369`, framed today as a Git-only rebase) must describe the rule as
     it now stands.
   - The rule: config-directory-relative for every source, confined only for untrusted (Git-API and
     uploaded) configs, unconfined for operator-trusted local files.
   - Mirror the change in `docs/ja/configuration.md`.
7. **Tests.** Add three kinds of coverage.
   - A local config's path fields resolve against the config's directory independent of cwd: chdir
     elsewhere, then assert the result is unchanged.
   - A local config may point outside its own directory, with no `ValueError`.
   - A `rebased(..., confine=False)` unit test sits alongside the existing confinement one.

   The on-device conformance lane and the demo-config rewrite are exercised by the heavier CI
   lanes alone (`smoke (idb)` / `E2E` / `xcuitest` / `conformance`), not by the Linux gate. Flag that in the
   PR.

## Alternatives considered

- **Config-directory-relative *with* confinement (exact parity with BE-0063).** This was the first
  shape considered. Rejected once it met the repo's own layout.
  - The showcase's `xcuitest.testRunner: BajutsuKit/Runner/...` points at a repo-root sibling of
    `demos/`. It cannot be expressed from `demos/showcase/` without a `../` escape, which confinement
    rejects.
  - More fundamentally, confinement exists to contain an *untrusted* fetched config (BE-0051). A local
    file is already treated as operator-trusted (BE-0121). Confining it would add friction without a
    matching threat.
- **A per-field or per-config opt-in flag** to enable file-relative resolution. Rejected. The safer
  interpretation should be the default, not something a config author has to discover and turn on. An
  opt-in leaves the footgun in place for everyone who does not set it.
- **Keep cwd-relative as the default, and add a flag or config key to switch to file-relative.**
  Rejected for the same reason. It would also permanently double the resolution semantics a reader
  has to hold in mind, instead of retiring the old one.
- **Also rebase `launchServer.cwd` to default to the config's directory.** Deferred rather than
  folded in here. The field is of a different kind: a subprocess's working directory, not a reference
  to a file the config points at. Conflating the two would blur what this item is fixing. Revisit as
  its own item if it proves to be a similar footgun in practice.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add a `confine` switch to `Effective.rebased()`; update its docstring.
- [x] Rebase local configs through `eff.rebased(cfg_path.resolve().parent, confine=False)` in
      `_load_effective_with_source`.
- [x] Anchor serve's local-config binds at the config's directory (startup `--config`, fs bind).
- [x] Rewrite the in-repo demo configs to config-directory-relative paths.
- [x] Rebase the on-device conformance test's `Effective` against the config directory.
- [x] Update `docs/configuration.md` and `docs/ja/configuration.md`.
- [x] Add cwd-independence + unconfined-local-path tests, plus a `rebased(confine=False)` unit test.

Delivered by [#980](https://github.com/bajutsu-e2e/bajutsu/pull/980).

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
