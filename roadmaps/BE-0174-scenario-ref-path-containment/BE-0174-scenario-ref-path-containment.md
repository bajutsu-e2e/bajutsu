**English** · [日本語](BE-0174-scenario-ref-path-containment-ja.md)

# BE-0174 — Contain scenario component and data refs within the suite root

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0174](BE-0174-scenario-ref-path-containment.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0174") |
| Implementing PR | [#720](https://github.com/bajutsu-e2e/bajutsu/pull/720) |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

Confine the file references a scenario expands — its `use:` component refs and data-driven CSV refs
— to the suite it belongs to, so a scenario can never make the loader read a file outside its own
tree. The device-free loader (`load_expanded_scenarios` / `expand_components` / `expand_data` in
`bajutsu/scenario`) resolves each ref as `base / ref` against the scenario file's directory, with no
containment check: an absolute path or a `../` chain reaches anywhere the process can read.
Deterministic and app-agnostic — no model, no verdict change; a rejected ref fails loudly, exactly
like an ambiguous selector.

## Motivation

The loader is reached from many surfaces, not one. On the CLI, `coverage`, `audit`, and `trace
--explain` expand a whole suite; in the serve Web UI, the Coverage tab
([BE-0146](../BE-0146-serve-coverage/BE-0146-serve-coverage.md)) and the existing `run` / `audit`
operations do the same. Every one of them resolves a scenario's `use: { component: <ref> }` and its
CSV `data:` refs with a bare `base / ref` and no bound on where that path may land.

That is a path-traversal read primitive whenever a scenario file is not fully trusted. A scenario
carrying `use: { component: ../../../../etc/passwd }` (or an absolute path) makes the loader open
that file; and because a malformed target is surfaced in the error message — `_parse_yaml_named`
names the offending file, and a parse error can echo file fragments — the failure path can *leak*
what it read, not just read it. BE-0146's Coverage tab widened the surface enough to notice this, but
it predates that change and is not specific to it: the same loader underlies the CLI and serve's
other paths.

Bajutsu already treats "the caller controls the *contents* under a root, but the *shape* of a
reference must be confined" as a first-class concern elsewhere — the run-artifact store confines
every `rel` to `runs_dir` (`bajutsu/serve/artifacts.py`), and
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) confined the
client-supplied *scenario path* on `/api/run`. This item closes the analogous hole one layer in: the
refs *inside* a scenario file. It matters most once configs/suites are not all authored by the
operator — an uploaded bundle ([BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)),
a Git-sourced config ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)), or a
multi-tenant hosted deployment ([BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)).

## Detailed design

A containment check in the shared loader, applied to every ref before it is read; deterministic, with
no bearing on pass/fail.

- **A containment root per load.** Resolve each ref as today (`base / ref`), then require the
  resolved **real path** (symlinks followed) to stay within an allowed root. The root is the suite's
  own directory tree — the scenarios dir the load started from — so a scenario and the components it
  shares under that tree resolve normally.
- **Allow legitimate relative refs; reject escapes.** A shared-component layout like
  `use: { component: ../components/login.yaml }` is a real pattern and must keep working *when it
  stays inside the root*. What is rejected is a ref that resolves outside the root, an absolute path,
  or a symlink that points outside — the three ways a ref leaves the tree. Rejection is a clear,
  deterministic error naming the offending ref, and the error must not echo the target file's
  contents (close the leak, not just the read).
- **One choke point, every caller.** The check lives in the shared loader
  (`bajutsu/scenario/load_expanded.py` and the `expand_components` / `expand_data` ref resolvers), so
  the CLI (`coverage` / `audit` / `trace`) and serve (Coverage, `run`, `audit`) all inherit it
  without per-caller code. Callers pass the containment root (their scenarios dir); the loader owns
  the enforcement.
- **App-agnostic and AI-free.** The root comes from where the suite was loaded, not per-app
  knowledge; the check is a pure path comparison — no model, never on the verdict path.

## Alternatives considered

* **Leave it to deployment (trust the suite author).** Rejected: the loader is reached from
  untrusted-config paths (upload / Git / hosted), and "safe only because everything is trusted" is
  exactly the property BE-0051 removed for the sibling `/api/run` path. A read-plus-leak primitive is
  not acceptable to leave open on those surfaces.
* **Blanket-ban `..` in refs.** Rejected: it breaks the legitimate `../components/shared.yaml`
  layout. The correct bound is *containment within the root after resolution*, which allows `..` that
  stays inside and rejects `..` that escapes — a ban on the token is both too strict and (via
  symlinks) too weak.
* **Guard only the serve entry points.** Rejected: the hole is in the shared loader, so guarding one
  caller leaves the CLI and serve's other paths open and invites drift. The fix belongs at the choke
  point, like the artifact-store `rel` confinement.
* **Sandbox the whole load in a container.** Out of scope here — that is the upload-execution posture
  ([BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)); a path-containment
  check is the proportionate, always-on fix for reads and needs no runtime isolation.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add a containment check to the shared ref resolvers (`load_expanded_scenarios` /
      `expand_components` / `expand_data`): resolve to a real path and require it to stay within the
      caller-supplied suite root; reject absolute paths and symlink escapes with a leak-free error
- [x] Thread the containment root from each caller (CLI `coverage` / `audit` / `trace`; serve
      Coverage / `run` / `audit`) into the loader
- [x] Cover it in the fast suite: a legitimate in-root `../components/…` ref still loads; an
      absolute path, an out-of-root `../` chain, and a symlink escape are each rejected without
      echoing file contents

### Log

- The shared choke point is `contained_ref(root, base, ref)` in `bajutsu/scenario/load_expanded.py`:
  it resolves `base / ref` to a real path (symlinks followed) and requires it to stay within the
  caller's suite root, rejecting the escape with a leak-free `ValueError` that names only the ref.
  `load_expanded_scenarios` (default root = the file's dir) and `load_scenarios_dir` (root = the
  scenarios dir) route both the component and CSV resolvers through it, so the CLI
  `coverage` / `audit` / `trace --explain` and the serve Coverage tab inherit it. The CLI `run`
  path (`_expand_file`), which serve `run` drives via subprocess, shares the same helper. serve
  `audit` parses a single file without expanding refs, so it has no ref to confine.
- Note: `run`'s `setup:` prelude ref still resolves with a bare `base / ref`; confining it is a
  natural sibling follow-up, kept out of this item's declared component/data scope.

## References

* `bajutsu/scenario/load_expanded.py`, `bajutsu/scenario/expand.py` — the shared loader and the
  `use:` / CSV ref resolvers this change confines.
* `bajutsu/serve/artifacts.py` — the existing precedent: every run-relative `rel` is confined to
  `runs_dir` in one place.
* [BE-0146 — E2E coverage map in the serve Web UI](../BE-0146-serve-coverage/BE-0146-serve-coverage.md)
  — the surface whose review (PR [#702](https://github.com/bajutsu-e2e/bajutsu/pull/702)) surfaced this;
  [BE-0051 — Serve hardening for hosting](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
  — confined the client-supplied scenario *path*, the sibling of this ref-containment fix;
  [BE-0073 — Config bundle upload](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md),
  [BE-0063 — Git config source](../BE-0063-git-config-source/BE-0063-git-config-source.md),
  [BE-0108 — Hosted config source restriction](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)
  — the untrusted-config surfaces that make this matter.
* [CLAUDE.md](../../CLAUDE.md), [DESIGN.md](../../DESIGN.md) — determinism-first (a bad ref
  fails loudly, never guesses) and the app-agnostic boundary the check preserves.
