**English** · [日本語](BE-0063-git-config-source-ja.md)

# BE-0063 — Load config (and its scenario tree) from a Git repository + ref

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0063](BE-0063-git-config-source.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Configuration sourcing |
<!-- /BE-METADATA -->

## Introduction

Every command today takes `--config <path>` as a **local filesystem path** (default
`bajutsu.config.yaml`), and the config's `scenarios` / `baselines` / `setup` / `appPath` / `build`
entries are paths **relative to the working directory**. This proposal lets the `--config` flag on
every command (`run` / `record` / `doctor` / `crawl`) and the serve UI's config picker name a
**Git repository at a ref** instead —
`github:<owner>/<repo>@<ref>:<path>` — so Bajutsu materializes that repo subtree at the ref, loads
the config from it, and resolves the config's relative paths against the checked-out tree. Only the
*acquisition* of the config-and-scenarios bundle changes; the schema, the runner, the drivers, and
the deterministic gate stay exactly as they are.

Related: the hosting counterparts
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the `ScenarioStore`
seam) and [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (self-hosting),
plus [BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
(serve hardening).

## Motivation

A team's config and scenarios already live in a Git repository — [DESIGN §6.5](../../../DESIGN.md)
fixes this on purpose: "scenarios are just files in the repo, git holds the history, and Bajutsu has
no store of its own." Yet to run them you must first check that repository out locally and run from
inside it. For continuous integration (CI) and for a hosted or self-hosted `serve`, that local
checkout is friction or is impossible:

- **Self-hosted serve.** The web UI is a thin launcher. Today the operator of a self-hosted serve
  ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) Tier A) has to hand-place
  the team's config and scenarios onto the Mac and keep them in sync by hand. Pointing serve at
  `github:acme/mobile-tests@main` makes the UI pull the team's test repository directly, and
  switching branches becomes a field in the UI rather than a redeploy.
- **Hosted control plane.** For the multi-tenant service
  ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)), a Git-backed source
  is a today-runnable implementation of the `ScenarioStore` seam's question — "where does a project's
  config and scenarios come from?" — alongside the object-store path.
- **CI without a checkout.** `bajutsu run --config github:acme/mobile-tests@<sha>:e2e/bajutsu.config.yaml --app sample`
  lets a generic runner execute a separate test repository's suite without keeping a working copy of
  it.

The crux that shapes the whole design: **the config alone is not enough.**
[`demos/features/demo.config.yaml`](../../../demos/features/demo.config.yaml) sets
`scenarios: demos/features/app/scenarios`, an `appPath:`, and `build: make -C demos/features
sample-build` — all relative to the run's working directory. Fetching only the YAML would leave
those paths dangling. "Load the config from Git" therefore has to mean "materialize the repo subtree
the config lives in, at a chosen ref, and load from there."

## Detailed design

### The spec syntax

`--config` keeps accepting a local path; in addition it accepts a Git source.

GitHub shorthand (the first-class, headline form):

```
github:<owner>/<repo>[@<ref>][:<path>]
```

- `<ref>` — a branch, tag, or commit SHA. Default: the repository's default branch.
- `<path>` — the path within the repository to the config file. Default: `bajutsu.config.yaml` at
  the repository root (matching the `DEFAULT_CONFIG` filename).
- Examples:
  - `github:acme/mobile-tests` — default branch, root config
  - `github:acme/mobile-tests@main:e2e/bajutsu.config.yaml`
  - `github:acme/mobile-tests@v1.4.0:e2e/bajutsu.config.yaml`
  - `github:acme/mobile-tests@9f3c1ab:e2e/bajutsu.config.yaml` — pinned, reproducible

A general Git URL keeps the door open for GitHub Enterprise, GitLab, or a self-hosted host later
(GitHub is the only host this item implements):

```
git+https://<host>/<owner>/<repo>.git@<ref>#<path>
```

A value with no recognized scheme is still treated as a local path, so every existing invocation
behaves exactly as before.

### Resolution and the "git source" seam

A new resolver sits behind the existing `_load_effective` (`bajutsu/cli/_shared.py`), so **every
command and serve gain the capability from one place** rather than each command parsing Git specs.
`_load_effective(config, app)` becomes:

1. **Parse** the `config` string. A local path keeps today's behavior unchanged; a Git spec yields
   `(host, owner, repo, ref, path)`.
2. **Resolve the ref to an immutable commit SHA.** For GitHub, `GET /repos/{owner}/{repo}/commits/{ref}`
   returns the `sha` — one cheap request that works for a branch, a tag, or a SHA alike. This commit
   SHA is the determinism anchor for everything below.
3. **Materialize the tree at that SHA** into a content-addressed cache directory,
   `~/.cache/bajutsu/gitsrc/<host>/<owner>/<repo>/<sha>/`. Because the directory is keyed by the
   immutable SHA, a cache hit is always valid, and a pinned-SHA run is fully offline after the first
   fetch. The fetch uses the GitHub tarball endpoint (`GET /repos/{owner}/{repo}/tarball/{sha}`): one
   request, no `git` binary, any ref, extracted atomically into the cache directory.
4. **Load** the config from `<cache>/<path>` and resolve its relative entries (`scenarios`,
   `baselines`, `setup`, `appPath`, `build`, and a scenario's `mocks`) **against the checkout root**,
   not the caller's current directory. This is the one cross-cutting change to path handling: the
   path base becomes an explicit value (the materialized root) instead of the implicit process
   working directory.
5. **Record provenance.** The resolved `<host>/<owner>/<repo>@<sha>`, together with the original ref,
   is written into the run's `manifest.json` and surfaced in serve, so a branch-based run still
   states exactly which commit it executed.

The resolver is a small, testable seam: a `ConfigSource` Protocol with a `LocalSource` (today's
behavior) and a `GitHubSource` implementation, an env-driven token, and a lazy import — the same
seam pattern [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) established
for serve. Its tests run on the Linux gate against a fake transport, with no network and no
Simulator.

### Determinism and the mutable-ref boundary

This brushes against [DESIGN §2](../../../DESIGN.md) (determinism first), so it is worth stating
plainly. A bare branch ref is **mutable**: `…@main` resolved today and a week from now can be
different commits. Bajutsu allows it for authoring convenience, but bounds the mutability so it is
never hidden:

- The branch is resolved to a concrete commit SHA **at load**, and **that SHA is recorded**, so the
  run is reproducible after the fact — re-running against the recorded SHA replays the same tree.
- The resolver runs **no model call** and is fully deterministic given a SHA. Nothing here enters
  the Tier-2 judge path; this is config acquisition, not a pass/fail decision.
- A gate that needs bit-for-bit reproducibility pins a **tag or commit SHA** (`@v1.4.0`,
  `@9f3c1ab`). `run` can warn — or, under an opt-in `--require-pinned-config`, fail — when it is
  handed a bare branch in a gate context.

So the design keeps the spirit of "no fixed sleeps / fail rather than guess": Bajutsu never silently
runs an unknown revision, and what it ran is always recoverable from the manifest.

### Authentication (private repositories)

A public repository needs no credential. A private one uses a token from `GITHUB_TOKEN` or
`GH_TOKEN`, falling back to `gh auth token` when the `gh` CLI is logged in, sent as an
`Authorization: Bearer` header on the API and tarball requests. The token is never logged and is
added to redaction's defaults so it cannot leak into evidence. Where they fit, the existing
`bajutsu/github.py` helpers are reused rather than duplicated.

### Caching, offline use, and freshness

- The cache is **content-addressed by commit SHA**, hence immutable, so it needs no invalidation,
  and concurrent runs share it safely (extract to a temporary directory, then rename into place).
- A branch or tag ref re-resolves its SHA on each load (the one cheap commits API call);
  `--config …@<sha>` skips even that and runs offline on a cache hit. A `--config-offline` switch
  (use the cache, never touch the network) supports air-gapped re-runs.
- Cache garbage collection is a least-recently-used or time-to-live (TTL) prune of the cache
  directory; the exact policy is left to implementation.

### What stays unchanged (app-agnostic, schema-stable)

The config schema (`bajutsu/config.py`), `resolve()`, the runner, the drivers, the assertion
evaluator, and the deterministic gate are untouched. A Git source is purely a new way to **acquire**
the same config and tree — exactly
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)'s "only its invocation
and plumbing move" and [DESIGN §6.5](../../../DESIGN.md)'s "git holds the history." Per-app
differences stay in the config; the tool does not branch per repository.

### CLI surface (run / doctor / record / crawl)

Because the resolver lives behind `_load_effective`, every command that takes `--config` accepts a
Git source with no per-command change — this is the CI and scripting path, distinct from the serve
GUI below. Two kinds of command consume a Git source differently:

- **Read-only — `run` and `doctor`.** The natural Git-source consumers, and the point of the CI
  case: they read the config (and, for `run`, the scenarios) from the materialized checkout and
  execute or score against them, writing nothing back.
  - `bajutsu run --config github:acme/mobile-tests@v1.4.0:e2e/bajutsu.config.yaml --app checkout`
  - `bajutsu doctor --config github:acme/mobile-tests@main:e2e/bajutsu.config.yaml --app checkout`
  - `--app <name>` selects an entry from the *fetched* config's `apps:`, exactly as with a local
    config; `--backend` / `--udid` / `--workers` / `--scenario` are unchanged.
- **Authoring — `record` and `crawl`.** These *produce* new files (a scenario, a `screenmap.json`),
  which the read-only, SHA-keyed cache cannot receive. A Git source is therefore **read-only input**:
  they may resolve the app config and existing scenarios from it (context for the agent), but the
  generated artifact is written to a **local path** (`--out`, defaulting under the current
  directory), never into the cache. The author reviews that file and commits it to the repository
  through normal git — exactly [DESIGN §6.5](../../../DESIGN.md)'s "AI output is a reviewable diff the
  human commits," unchanged. For a tight local authoring loop, point `--config` at a local checkout
  as today.

Two switches ride along on every command: `--config-offline` (use the cache, never touch the
network) and `--require-pinned-config` (fail rather than warn on a bare branch, for a gate — see
*Determinism* above).

### serve surface (the GUI consumer)

The serve config picker — today a local file browser confined to `--root` — gains a "from Git" mode:
fields for repository, ref, and path, or a single `github:…` string. On open, serve resolves and
materializes as above into its cache, then drives the existing run and record paths against the
checkout. `serve --config github:…` binds a Git source at startup the same way `--config <path>`
binds a local one. This is the
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) Tier-A payoff: the operator
points the Mac's serve at the team repository instead of hand-syncing files, and the path-confinement
hardening from
[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
applies to the checkout root just as it does to `--root` today.

## Alternatives considered

- **Fetch only the config YAML, not the tree.** Rejected as the primary design: the config's
  `scenarios` / `appPath` / `build` / `baselines` are relative paths, so a config without its tree
  cannot `run`. Materializing the whole subtree is what makes the feature useful.
- **`git clone` instead of the tarball endpoint.** A `git clone --depth 1 --branch <ref>` (or a
  partial clone with `--filter=blob:none`) is the obvious mechanism, but it requires the `git` binary
  on the host, and `--branch` does not accept an arbitrary commit SHA (shallow-fetching a SHA needs
  the server's `uploadpack.allowReachableSHA1InWant`, which is not universally enabled). The tarball
  endpoint is one HTTP request, needs no `git`, and accepts a branch, tag, or SHA uniformly. A
  shallow clone is kept as the fallback for a non-GitHub Git host.
- **Per-file Contents API fetch.** Rejected: enumerating and fetching a scenarios *directory*
  file-by-file is chatty and racy, whereas a single tarball at a SHA is atomic and complete.
- **Branch-only (always latest), or pinned-only (reject branches).** Both extremes rejected in favor
  of "any ref, record the resolved SHA": branch-tracking is convenient for authoring, pinning is
  required for a reproducible gate, and recording the SHA reconciles the two.
- **A bespoke Bajutsu config store (upload configs to a service).** Rejected: it contradicts
  [DESIGN §6.5](../../../DESIGN.md) (no store of its own); Git already is the versioned source of
  truth.
- **Resolve relative paths against the caller's current directory even for a Git source.** Rejected:
  the caller's directory has nothing to do with the fetched tree, so paths must resolve against the
  materialized checkout root, which is why the path base is made explicit.
- **Let `record` / `crawl` write back into the Git source.** Rejected: the cache is content-addressed
  by an immutable commit SHA, so writing into it would break that invariant and the change would be
  ephemeral (a later prune could drop it). Authoring instead emits to a local path the human commits
  through git — the same review-then-commit flow as today, consistent with treating a Git source as
  read-only input.

## References

- [DESIGN §6.5](../../../DESIGN.md) (scenarios are git-tracked files; no store of its own),
  [DESIGN §8](../../../DESIGN.md) (CLI and per-app config).
- `bajutsu/cli/_shared.py` (`_load_effective`), `bajutsu/config.py` (the relative-path fields),
  `bajutsu/cli/commands/serve.py` (the config picker), `bajutsu/github.py`.
- [`demos/features/demo.config.yaml`](../../../demos/features/demo.config.yaml) — a config whose
  `scenarios` / `appPath` / `build` are relative to the working directory (the crux).
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the `ScenarioStore`
  seam a Git source implements), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)
  (self-hosting; serve-from-repo is the Tier-A win),
  [BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
  (serve hardening — token auth and path confinement the Git source honors).
- [docs/configuration.md](../../../docs/configuration.md).
