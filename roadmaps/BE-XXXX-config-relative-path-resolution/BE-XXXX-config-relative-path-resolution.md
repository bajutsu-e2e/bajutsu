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
not against the config file that declares them. This item makes them resolve relative to the config
file itself instead, confined to that file's own directory tree, by extending the rebase Git-sourced
configs already get (BE-0063) to local configs too — closing the one remaining place where "relative to
the referring file" isn't the rule.

## Motivation

`_load_effective_with_source` (`bajutsu/cli/_shared.py:174-235`) already knows how to rebase a config's
path fields against a stable root instead of the caller's cwd: when the config comes from a Git source,
it calls `eff.rebased(root)` with `root` set to the materialized checkout (BE-0063). But when the config
is local (`spec is None`), it short-circuits and returns `eff` untouched (`_shared.py:229-230`) — the
YAML's relative paths stay interpreted against whatever directory `bajutsu` happened to be invoked from.

That makes the same config file behave differently depending on where it's run from. A scenario suite
that resolves cleanly from the repo root can point at nothing — or, worse, silently resolve to an
unrelated file that happens to exist — when run from a subdirectory, a sibling worktree, or a CI job
whose working-directory convention differs from a contributor's shell. `make worktree` (BE-0069) makes
running the same config from a different checkout path routine, which is exactly the situation this
footgun lives in: a config that "just worked" in one checkout isn't guaranteed to behave the same when
the cwd convention it was invoked under doesn't carry over.

It's also an existing asymmetry, not a new risk to weigh: the exact same YAML, fetched via a Git source,
already resolves consistently and confined to its own tree (BE-0063); loaded locally, it's cwd-relative
and unconfined. Scenario-side references closed this same gap already — `use:` / `dataFile` (BE-0174)
and `setup` resolve relative to the referring scenario file, confined to the suite root. Config-side
path fields are the one part of the system still anchored to the cwd instead of the declaring file.

## Detailed design

1. **Generalize the call site**, not the rebase logic. In `_load_effective_with_source`
   (`bajutsu/cli/_shared.py:196-235`), when `spec is None` (a local config), set
   `root = cfg_path.resolve().parent` instead of `None`, and call `eff.rebased(root)` unconditionally —
   the same call already made for a Git source, just with the config file's own directory as `root`
   instead of a checkout root. `source` (Git provenance) stays `None` for a local config; only the
   rebase decision changes.
2. **`Effective.rebased()` itself needs no change.** It already covers every field in scope —
   `scenarios`, `baselines`, `schemas`, `goldens`, the iOS/Android sub-config's `app_path`, and
   `xcuitest.test_runner` (`bajutsu/config.py:580-634`) — and already enforces confinement (an absolute
   or `../`-escaping value raises `ValueError`, mirroring the serve-hardening path confinement,
   BE-0051). Update its docstring, since "only a Git source calls this" (`config.py:587`) stops being
   true.
3. **Fields intentionally out of scope**, carried over from `rebased()`'s existing exclusions plus one
   new one:
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
4. **Confinement is unconditional**, matching BE-0063 exactly: an absolute path or a `../` escape out of
   the config file's own directory raises `ValueError`, surfaced through the same friendly exit-2 path
   `_load_effective_with_source` already has for the Git-source case (`_shared.py:231-235` needs no new
   branch — the existing `except ValueError` handler covers local configs too once they call
   `rebased()`).
5. **This is a deliberate breaking change, not a compatibility shim.** Every local config's path fields
   switch from cwd-relative to config-file-relative in one cutover; no fallback or opt-out flag. In the
   common layout — a config file with its scenarios/baselines next to it, run from its own directory —
   the two anchors already coincide, so most configs are unaffected in practice. Configs that relied on
   being invoked from elsewhere, or that used absolute or `../`-escaping paths, must be updated; call
   this out plainly in the PR body and the migration note in `docs/configuration.md`.
6. **Update docs.** `docs/configuration.md:81` ("Relative to the run's cwd") and the BE-0063 section
   (`docs/configuration.md:330-369`, currently framed as a Git-only rebase) need to describe the unified
   rule — config-relative and confined, regardless of source — instead of a Git-specific special case.
7. **Tests.** Existing tests that assert cwd-relative resolution for local configs need updating to the
   new file-relative + confinement semantics. Add local-config coverage for the confinement-rejection
   case (an escaping `../`, an absolute path) mirroring the Git-source tests BE-0063 already has.

## Alternatives considered

- **A per-field or per-config opt-in flag** to enable file-relative resolution: rejected — the safer
  interpretation should be the default, not something a config author has to discover and turn on. An
  opt-in leaves the footgun in place for everyone who doesn't set it.
- **Keep cwd-relative as the default; add a flag or config key to switch to file-relative**: rejected for
  the same reason, and it permanently doubles the resolution semantics a reader has to hold in mind
  instead of retiring the old one.
- **Convert relative paths to config-relative, but keep allowing absolute paths / `../` escapes for
  local configs (no confinement)**: considered, since it's a smaller behavior change. Rejected in favor
  of exact parity with BE-0063 — a config that resolves one way locally and differently once fetched via
  Git is a confusing surface to reason about and a plausible source of "works on my machine, breaks
  under Git-config CI" bug reports. Confinement is also cheap: an escaping path is already an unusual
  and likely-accidental thing for a config to declare.
- **Also rebase `launchServer.cwd` to default to the config's directory**: deferred rather than folded
  in here — it's a different kind of field (a subprocess's working directory, not a reference to a file
  the config points at), and conflating the two would blur what this item is actually fixing. Revisit as
  its own item if it proves to be a similar footgun in practice.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Rebase local configs through `eff.rebased(cfg_path.resolve().parent)` in
      `_load_effective_with_source`, dropping the Git-only short-circuit.
- [ ] Update `Effective.rebased()`'s docstring to drop the "only a Git source calls this" framing.
- [ ] Update `docs/configuration.md` (the cwd-relative note and the BE-0063 section) to describe the
      unified, source-independent rule.
- [ ] Update existing tests asserting cwd-relative local-config resolution; add confinement-rejection
      coverage for local configs (absolute path, `../` escape).

## References

- [BE-0063 — Load config (and its scenario tree) from a Git repository + ref](../BE-0063-git-config-source/BE-0063-git-config-source.md)
  — the `Effective.rebased()` mechanism and confinement check this item generalizes to local configs.
- [BE-0174 — Contain scenario component and data refs within the suite root](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment.md)
  — the precedent for "relative to the referring file, confined to a root" on the scenario side.
- [BE-0051 — Serve hardening for hosting](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
  — the path-confinement discipline `rebased()`'s escape check mirrors.
- [BE-0090 — Govern and sandbox command execution from uploaded bundle configs](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md)
  — the separate `bundle_root` anchor concept this item leaves untouched.
- `docs/configuration.md:81`, `docs/configuration.md:330-369` — the current cwd-relative note and the
  BE-0063 Git-rebase section to be updated.
