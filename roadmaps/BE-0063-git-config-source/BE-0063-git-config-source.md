**English** · [日本語](BE-0063-git-config-source-ja.md)

# BE-0063 — Load config (and its scenario tree) from a Git repository + ref

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0063](BE-0063-git-config-source.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0063") |
| Implementing PR | [#277](https://github.com/bajutsu-e2e/bajutsu/pull/277) (the last of several BE-0063 PRs) |
| Topic | Configuration sourcing |
| Related | [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md), [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view.md) |
<!-- /BE-METADATA -->

## Introduction

Every command takes `--config <path>` as a **local filesystem path**, with the default `bajutsu.config.yaml`. The config entries `scenarios`, `baselines`, `setup`, `appPath`, and `build` are paths **relative to the working directory**.

This proposal lets the `--config` flag of every command (`run`, `record`, `doctor`, and `crawl`) name a **Git repository at a ref** instead. The serve UI's config picker gains the same ability. The form is `github:<owner>/<repo>@<ref>:<path>`.

Bajutsu materializes the repository subtree at that ref and loads the config from it. It resolves the relative paths of the config against the checked-out tree. Only the *acquisition* of the config-and-scenarios bundle changes. The schema, the runner, the drivers, and the deterministic gate stay exactly as they are.

Related items: the hosting counterparts [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the `ScenarioStore` seam) and [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (self-hosting), plus [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) (serve hardening).

## Motivation

A team's config and scenarios already live in a Git repository. [DESIGN §6.5](../../DESIGN.md) fixes this on purpose: "scenarios are just files in the repo, git holds the history, and Bajutsu has no store of its own." Yet running them requires checking that repository out locally and running from inside it. For continuous integration (CI) and for a hosted or self-hosted `serve`, the local checkout is friction or is impossible:

- **Self-hosted serve.** The web UI is a thin launcher. Today the operator of a self-hosted serve ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) Tier A) has to place the team's config and scenarios on the Mac and keep them in sync by hand. Pointing serve at `github:acme/mobile-tests@main` makes the UI pull the test repository directly. Switching branches then becomes a field in the UI rather than a redeploy.
- **Hosted control plane.** The multi-tenant service ([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)) needs an answer to the question behind the `ScenarioStore` seam: "where does a project's config and scenarios come from?" A Git-backed source answers it today, alongside the object-store path.
- **CI without a checkout.** `bajutsu run --config github:acme/mobile-tests@<sha>:e2e/bajutsu.config.yaml --app sample` lets a generic runner execute the suite of a separate test repository. The runner keeps no working copy of that repository.

The crux that shapes the whole design is that **the config alone is not enough.** [`demos/features/demo.config.yaml`](../../demos/features/demo.config.yaml) sets `scenarios: demos/features/app/scenarios`, an `appPath:`, and `build: make -C demos/features sample-build`. All three are relative to the working directory of the run. Fetching only the YAML would leave those paths dangling. "Load the config from Git" must therefore mean "materialize the repo subtree that holds the config, at a chosen ref, and load from there."

## Detailed design

### The spec syntax

`--config` keeps accepting a local path. It also accepts a Git source.

The GitHub shorthand is the first-class, headline form:

```
github:<owner>/<repo>[@<ref>][:<path>]
```

- `<ref>` is a branch, a tag, or a commit SHA. The default is the default branch of the repository.
- `<path>` is the path of the config file within the repository. The default is `bajutsu.config.yaml` at the repository root, matching the `DEFAULT_CONFIG` filename.
- Examples:
  - `github:acme/mobile-tests` — default branch, root config
  - `github:acme/mobile-tests@main:e2e/bajutsu.config.yaml`
  - `github:acme/mobile-tests@v1.4.0:e2e/bajutsu.config.yaml`
  - `github:acme/mobile-tests@9f3c1ab:e2e/bajutsu.config.yaml` — pinned, reproducible

A general Git URL keeps the door open for GitHub Enterprise, GitLab, or a self-hosted host later. GitHub is the only host this item implements.

```
git+https://<host>/<owner>/<repo>.git@<ref>#<path>
```

A value with no recognized scheme is still a local path, so every existing invocation behaves exactly as before.

### Resolution and the "git source" seam

A new resolver sits behind the existing `_load_effective` (`bajutsu/cli/_shared.py`). Every command and serve gain the capability from that one place, and no command parses Git specs itself. `_load_effective(config, app)` becomes:

1. **Parse** the `config` string. A local path keeps today's behavior unchanged. A Git spec yields `(host, owner, repo, ref, path)`.
2. **Resolve the ref to an immutable commit SHA.** For GitHub, `GET /repos/{owner}/{repo}/commits/{ref}` returns the `sha`. This one cheap request works for a branch, a tag, or a SHA alike. The commit SHA is the determinism anchor for every later step.
3. **Materialize the tree at that SHA** into a content-addressed cache directory, `~/.cache/bajutsu/gitsrc/<host>/<owner>/<repo>/<sha>/`. The immutable SHA keys the directory, so a cache hit is always valid. A pinned-SHA run is fully offline after the first fetch. The fetch uses the GitHub tarball endpoint (`GET /repos/{owner}/{repo}/tarball/{sha}`). That takes one request and no `git` binary, accepts any ref, and extracts atomically into the cache directory.
4. **Load** the config from `<cache>/<path>`. Resolve its relative entries (`scenarios`, `baselines`, `setup`, `appPath`, `build`, and a scenario's `mocks`) **against the checkout root**, not the current directory of the caller. This is the one cross-cutting change to path handling: the path base becomes an explicit value (the materialized root) instead of the implicit process working directory.
5. **Record provenance.** The resolved `<host>/<owner>/<repo>@<sha>` and the original ref go into the `manifest.json` of the run and appear in serve. A branch-based run still states exactly which commit it executed.

The resolver is a small, testable seam. It consists of a `ConfigSource` Protocol with a `LocalSource` (today's behavior) and a `GitHubSource` implementation, an env-driven token, and a lazy import. [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) established the same seam pattern for serve. The tests run on the Linux gate against a fake transport, with no network and no Simulator.

### Determinism and the mutable-ref boundary

This design brushes against [DESIGN §2](../../DESIGN.md) (determinism first), so we state the boundary plainly. A bare branch ref is **mutable**: `…@main` resolved today and a week from now can name different commits. Bajutsu allows a bare branch for authoring convenience, but bounds the mutability so that it is never hidden:

- Bajutsu resolves the branch to a concrete commit SHA **at load** and **records that SHA**. The run is then reproducible after the fact, because re-running against the recorded SHA replays the same tree.
- The resolver runs **no model call** and is fully deterministic given a SHA. Nothing here enters the Tier-2 judge path, because config acquisition is not a pass/fail decision.
- A gate that needs bit-for-bit reproducibility pins a **tag or commit SHA** (`@v1.4.0`, `@9f3c1ab`). When `run` receives a bare branch in a gate context, it can warn. Under the opt-in `--require-pinned-config`, it fails instead.

The design thus keeps the spirit of "no fixed sleeps / fail rather than guess." Bajutsu never silently runs an unknown revision, and the manifest always shows what it ran.

### Authentication (private repositories)

A public repository needs no credential. A private repository uses a token from `GITHUB_TOKEN` or `GH_TOKEN`. When neither is set and the `gh` CLI is logged in, the resolver falls back to `gh auth token`. The token travels as an `Authorization: Bearer` header on the API and tarball requests.

Bajutsu never logs the token. It adds the token to the defaults of redaction so that it cannot leak into evidence. Where they fit, the existing `bajutsu/github.py` helpers are reused rather than duplicated.

### Caching, offline use, and freshness

- The cache is **content-addressed by commit SHA** and hence immutable. It needs no invalidation, and concurrent runs share it safely: each run extracts to a temporary directory, then renames it into place.
- A branch or tag ref re-resolves its SHA on each load, using the one cheap commits API call. `--config …@<sha>` skips even that call and runs offline on a cache hit. A `--config-offline` switch (use the cache, never touch the network) supports air-gapped re-runs.
- Cache garbage collection is a least-recently-used or time-to-live (TTL) prune of the cache directory. The exact policy is left to implementation.

### What stays unchanged (app-agnostic, schema-stable)

The config schema (`bajutsu/config.py`), `resolve()`, the runner, the drivers, the assertion evaluator, and the deterministic gate are untouched. A Git source is a new way to **acquire** the same config and tree, and nothing more. It matches the principle of [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) that "only its invocation and plumbing move," and the principle of [DESIGN §6.5](../../DESIGN.md) that "git holds the history." Per-app differences stay in the config, and the tool does not branch per repository.

### CLI surface (run / doctor / record / crawl)

The resolver lives behind `_load_effective`, so every command that takes `--config` accepts a Git source with no per-command change. This is the CI and scripting path, distinct from the serve GUI below. Two kinds of command consume a Git source differently:

- **Read-only: `run` and `doctor`.** These are the natural Git-source consumers and the point of the CI case. They read the config (and, for `run`, the scenarios) from the materialized checkout, then execute or score against them. They write nothing back.
  - `bajutsu run --config github:acme/mobile-tests@v1.4.0:e2e/bajutsu.config.yaml --app checkout`
  - `bajutsu doctor --config github:acme/mobile-tests@main:e2e/bajutsu.config.yaml --app checkout`
  - `--app <name>` selects an entry from the `apps:` of the *fetched* config, exactly as with a local config. `--backend`, `--udid`, `--workers`, and `--scenario` are unchanged.
- **Authoring: `record` and `crawl`.** These *produce* new files, such as a scenario or a `screenmap.json`. The read-only, SHA-keyed cache cannot receive such files, so a Git source is **read-only input** for them. They may resolve the app config and existing scenarios from it as context for the agent. They write the generated artifact to a **local path** (`--out`, defaulting under the current directory) and never into the cache. The author reviews that file and commits it to the repository through normal git. This is the principle of [DESIGN §6.5](../../DESIGN.md), "AI output is a reviewable diff the human commits," unchanged. For a tight local authoring loop, point `--config` at a local checkout as today.

Two switches ride along on every command. `--config-offline` uses the cache and never touches the network. `--require-pinned-config` fails rather than warns on a bare branch, for a gate (see *Determinism* above).

### serve surface (the GUI consumer)

The serve config picker is today a local file browser confined to `--root`. It gains a "from Git" mode, with fields for repository, ref, and path, or a single `github:…` string. On open, serve resolves and materializes into its cache as described above. It then drives the existing run and record paths against the checkout. `serve --config github:…` binds a Git source at startup, the same way `--config <path>` binds a local one.

This is the [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) Tier-A payoff: the operator points the Mac's serve at the team repository instead of syncing files by hand. The path-confinement hardening from [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) applies to the checkout root just as it does to `--root` today.

## Alternatives considered

- **Fetch only the config YAML, not the tree.** Rejected as the primary design. The `scenarios`, `appPath`, `build`, and `baselines` entries of the config are relative paths, so a config without its tree cannot `run`. Materializing the whole subtree makes the feature useful.
- **`git clone` instead of the tarball endpoint.** A `git clone --depth 1 --branch <ref>` (or a partial clone with `--filter=blob:none`) is the obvious mechanism. It needs the `git` binary on the host, and `--branch` does not accept an arbitrary commit SHA. Shallow-fetching a SHA needs the server's `uploadpack.allowReachableSHA1InWant`, which is not universally enabled. The tarball endpoint takes one HTTP request, needs no `git`, and accepts a branch, tag, or SHA uniformly. A shallow clone stays as the fallback for a non-GitHub Git host.
- **Per-file Contents API fetch.** Rejected. Enumerating and fetching a scenarios *directory* file by file is chatty and racy. A single tarball at a SHA is atomic and complete.
- **Branch-only (always latest), or pinned-only (reject branches).** Rejected in favor of "any ref, record the resolved SHA." Branch tracking is convenient for authoring, and pinning is required for a reproducible gate. Recording the SHA reconciles the two.
- **A bespoke Bajutsu config store (upload configs to a service).** Rejected. The store contradicts [DESIGN §6.5](../../DESIGN.md) (no store of its own), and Git already is the versioned source of truth.
- **Resolve relative paths against the current directory of the caller even for a Git source.** Rejected. The directory of the caller has nothing to do with the fetched tree. Paths must resolve against the materialized checkout root, which is why the path base is made explicit.
- **Let `record` and `crawl` write back into the Git source.** Rejected. The cache is content-addressed by an immutable commit SHA, so a write into it would break that invariant. The change would also be ephemeral, because a later prune could drop it. Authoring instead emits to a local path that the human commits through git. That is the same review-then-commit flow as today, and it stays consistent with treating a Git source as read-only input.

## Progress

- [x] CLI acquisition core (`bajutsu/config_source.py`) — `parse_config_spec` (`github:` / `git+https://…`) and `materialize` (ref→SHA, GitHub tarball into a content-addressed cache, traversal-safe, atomic), wired behind `_load_effective` so `run` / `doctor` accept a Git `--config`.
- [x] Resolved-commit run provenance — `manifest.json`'s `provenance.configSource` records `{ host, owner, repo, ref, sha }`.
- [x] `--config-offline` and `--require-pinned-config` gate switches on `bajutsu run`.
- [x] Checkout-root path confinement (`Effective.rebased`) — an escaping `..` or absolute path exits 2.
- [x] Build-on-demand from the checkout root when `appPath` is set but missing.
- [x] serve "from Git" picker — `serve --config github:…` and the "From a Git repository" dialog field.
- [x] `record` / `crawl` treat a Git source as read-only input, writing output to a local path.

## References

- [DESIGN §6.5](../../DESIGN.md) (scenarios are git-tracked files; no store of its own),
  [DESIGN §8](../../DESIGN.md) (CLI and per-app config).
- `bajutsu/cli/_shared.py` (`_load_effective`), `bajutsu/config.py` (the relative-path fields),
  `bajutsu/cli/commands/serve.py` (the config picker), `bajutsu/github.py`.
- [`demos/features/demo.config.yaml`](../../demos/features/demo.config.yaml) — a config whose
  `scenarios` / `appPath` / `build` are relative to the working directory (the crux).
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the `ScenarioStore`
  seam a Git source implements), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)
  (self-hosting; serve-from-repo is the Tier-A win),
  [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
  (serve hardening — token auth and path confinement the Git source honors).
- [docs/configuration.md](../../docs/configuration.md).
