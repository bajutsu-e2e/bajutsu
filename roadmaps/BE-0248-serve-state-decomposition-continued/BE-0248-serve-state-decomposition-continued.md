**English** · [日本語](BE-0248-serve-state-decomposition-continued-ja.md)

# BE-0248 — Continue decomposing ServeState into auth and provider-settings managers

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0248](BE-0248-serve-state-decomposition-continued.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0248") |
| Implementing PR | [#1092](https://github.com/bajutsu-e2e/bajutsu/pull/1092) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`ServeState` (`bajutsu/serve/state.py:287`–`627`) is a roughly forty-field dataclass at the center of
`serve`. Two earlier items already started its decomposition:

- BE-0198 carved out the most self-contained slice, the job registry (`jobs`, the id sequence, and
  their lock). The slice became a standalone `JobRegistry` type, which `ServeState` now holds and
  delegates to.
- BE-0206 later moved the state container itself into its own module, `bajutsu/serve/state.py`,
  separate from the job-execution engine.

This item continues the same decomposition on the two next-most-cohesive slices that still live
directly on `ServeState`: the authentication/session/OAuth cluster and the AI-provider-settings
cluster. Each becomes its own type. `ServeState` holds the type and forwards to it, exactly as it now
holds `job_registry`.

`serve` sits outside the deterministic `run`/CI gate (prime directive 1). The refactor therefore
serves readability and maintainability alone, with no effect on any assertion or pass/fail verdict.

## Motivation

A single dataclass mixes config binding, authentication, per-org AI settings, four storage seams, the
job registry, upload-sandbox state, and evidence/object-store config. The type is hard to reason
about. A reader looking for "how does a request get authenticated" or "how is a provider choice
persisted" first has to subtract everything else on the type mentally.

BE-0198's `JobRegistry` extraction showed that the fix is not a rewrite. The fix is to find a
self-contained field-and-method cluster and give it a boundary. Two such clusters remain.

1. **Auth / session / OAuth.** The fields `token`, `sessions`, `oauth`, `oauth_allowed_users`,
   `oauth_admins`, and `oauth_viewers` (`state.py:387`–`401`) belong to this cluster. So do the
   methods `check_token`, `issue_session`, and `valid_session` (`state.py:528`–`538`). Together they
   answer one question: is this request authenticated, and as whom? They share no state with the rest
   of `ServeState`.

   The authorization layer (`bajutsu/serve/authz.py`) reads `oauth_allowed_users`, `oauth_admins`, and
   `oauth_viewers` for role-based access control (RBAC). These fields travel with the rest of the
   auth cluster. Server construction configures them alongside `token` and `oauth`, and they never
   change after that.
2. **Provider settings.** This cluster has these members:
   - the fields `provider_settings` and `provider_settings_store`
   - the two locks that guard them, `_provider_lock` (`state.py:451`) and `_persist_lock`
     (`state.py:456`)
   - the methods `org_provider_settings`, `put_org_provider_settings`, and `set_org_provider_choice`
     (`state.py:540`–`584`)

   Together they form the in-memory half of the per-org AI provider selection (BE-0229).

   The persistence half, `_persist_provider_settings` (`bajutsu/serve/operations/config.py:631`),
   already lives outside `state.py`. It reaches back in, takes both locks, and reads and writes
   `provider_settings` directly. That is possible today only because it imports `ServeState`'s
   internals. A named manager gives this cross-module caller a narrow surface to call, so the caller
   no longer reaches around the dataclass.

The two clusters own two of `ServeState`'s remaining locks, `_provider_lock` and `_persist_lock`.
The job registry's own lock already moved with BE-0198. The third remaining lock, `ant_login_lock`
(`state.py:428`), guards the unrelated `ant login` subprocess and stays out of scope. The `sessions`
cluster keeps its own locking inside `InMemorySessionStore` (`sessions.py:37`), not on `ServeState`.

The clusters also own roughly a dozen of the fields. They are the next-largest cohesive groups after
the registry. BE-0198 named this same opportunity a "plausible follow-up" in its own **Detailed
design** and left it out of scope to keep that PR reviewable. This item is the follow-up for two of
the remaining groups. The storage-seam group is a distinct, larger cluster of its own, so a later
item takes it. That group is `artifacts`, `scenarios`, `baselines`, `secrets`, `executor`,
`repository`, and `org_stores` / `StoreBundle`.

## Detailed design

The refactor preserves behavior throughout. Every `serve` endpoint, operations-layer function, and
test observes the same authentication decisions, session lifetimes, and provider-settings reads and
writes as today. The work splits into two mutually exclusive units, mirroring the pattern that
BE-0198 set, plus the supporting steps that follow.

- **Extract an auth/session type for the first cluster.** Introduce a type that owns `token`,
  `sessions`, `oauth`, `oauth_allowed_users`, `oauth_admins`, and `oauth_viewers`. Two shapes are
  possible. `AuthConfig` would cover the fixed `token` / `oauth` / allow-list configuration. A broader
  `SessionManager` would also wrap `sessions` and the `issue_session` / `valid_session` /
  `check_token` methods. The design decides which shape reads more clearly, because `sessions` is
  itself already a swappable `SessionStore` seam rather than a plain field.

  `ServeState` holds one instance. It has two ways to expose the type. It can forward `check_token` /
  `issue_session` / `valid_session` as thin delegating methods, the pattern that `register` /
  `try_register` already use for `job_registry`. Or it can expose the type directly and update the
  handful of call sites (`serve/handler.py`, `serve/authz.py`) to read through it. Pick one and apply
  it consistently, as BE-0198 did.
- **Extract a `ProviderSettingsManager` for the second cluster.** The manager owns
  `provider_settings`, `provider_settings_store`, `_provider_lock`, and `_persist_lock`. It exposes
  `org_provider_settings` / `put_org_provider_settings` / `set_org_provider_choice`, with their
  existing copy-on-read / copy-on-write discipline preserved exactly:
  - Every read returns an independent `OrgProviderSettings` copy, including its `slots` dict, so a
    caller can never alias the live entry.
  - Every write stores a fresh copy rather than the caller's own instance.

  This is the invariant that matters most here, as atomic id assignment under one lock mattered most
  for `JobRegistry`. The new type's boundary must preserve it, not just a copy of the method bodies.

  `operations/config.py`'s `_persist_provider_settings` is the one caller today that reaches into
  `ServeState` to take both locks directly. It becomes a caller of the manager's public surface
  instead. Either the manager grows a `persist`-shaped method that takes `_persist_lock` itself, or
  it exposes both locks for that one out-of-package caller. The design picks whichever keeps the
  manager's own invariants enforceable without leaking lock objects beyond this one necessary case.
- **Leave `ServeState` as the coordinator.** After both extractions, `ServeState` holds references to
  `job_registry` (already the case), the new auth/session type, and the new `ProviderSettingsManager`.
  It forwards the handful of call sites that need to keep reading through `ServeState` today. It
  stops *defining* the auth and provider-settings fields, methods, and locks itself.
- **Preserve lock discipline exactly.** Each extracted manager owns its own lock. `_provider_lock` and
  `_persist_lock` both move with the provider-settings cluster. They stay two distinct locks, so I/O
  never runs inside the in-memory lock, as `_persist_lock` documents today. Two patterns stay intact:
  - `org_provider_settings` takes its copy and returns it after releasing the lock
    (publish-outside-lock).
  - `set_org_provider_choice` performs the whole mutation inside the lock and publishes nothing
    outside it while the lock is held.

  No new lock is introduced, and no existing lock's scope changes.
- **Update tests and the module-list docs.** Add focused unit tests for each extracted type,
  constructed on its own with no full `ServeState` and no mocks, mirroring BE-0198's registry tests.
  Update `docs/architecture.md` / `docs/ja/architecture.md` if the serve module list names
  `state.py`'s responsibilities in a way that this split changes.

Out of scope, named so the boundary is explicit: these groups all remain on `ServeState`.

- the storage-seam group (`artifacts`, `scenarios`, `baselines`, `secrets`, `executor`, `repository`,
  `org_stores` / `StoreBundle`)
- the run-I/O-location group (`runs_dir`, `scenarios_dir`, `baselines_dir`, `uploads_dir`, `cwd`,
  `base_cwd`, `root`)
- the upload-sandbox state (`upload`, `upload_exec`, `bind_upload` / `release_upload`)
- the evidence/object-store config (`evidence`, `object_store`, `object_store_prefix`)

This item does not touch `JobRegistry`, `Job`, `run_job`, or any code in `bajutsu/serve/jobs.py`.

## Alternatives considered

- **Extract every remaining cluster in one pass** (auth, provider settings, storage seams, run-I/O
  locations, upload-sandbox state, and evidence/object-store config, all at once). Rejected for this
  item. The operations layer references `ServeState` throughout. A single PR touching every remaining
  group would be large and hard to review in one pass. BE-0198 gave the same reasoning for carving
  out only the job registry first. The auth and provider-settings clusters are the next two most
  self-contained groups, and each already has its own lock(s) and a small, well-named method set.
  Extracting the two together is one reviewable step. Further extraction of the storage-seam and
  other groups can follow as separate items.
- **Leave `ServeState` as is and rely on its field-group comments.** Rejected. The comments already
  describe the auth and provider-settings groups accurately, as BE-0198's motivation observed for the
  job registry before its extraction. But the grouping stays implicit. Only convention across three
  methods enforces the copy-on-read / copy-on-write discipline that prevents `provider_settings` dict
  aliasing. A class boundary that a new caller cannot easily bypass would enforce it better.
- **Move only the fields into a plain nested dataclass without moving the methods.** Rejected. A bag
  of fields with no methods leaves the lock-discipline invariant scattered across `ServeState`'s own
  method bodies, exactly as today. It moves data without moving the responsibility. BE-0198 raised
  the same objection against free functions over a shared dict.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Extract a `SessionManager` owning `token`, `sessions`, `oauth`, `oauth_allowed_users`,
      `oauth_admins`, `oauth_viewers`, and the `check_token` / `issue_session` / `valid_session`
      methods
- [x] Extract a `ProviderSettingsManager` owning `settings` (the per-org dict), `store`,
      `_provider_lock`, `_persist_lock`, and the `org_provider_settings` /
      `put_org_provider_settings` / `set_org_provider_choice` methods, preserving the
      copy-on-read/copy-on-write discipline exactly
- [x] Migrate `operations/config.py`'s `_persist_provider_settings` to call the manager's public
      `persist` method instead of reaching into `ServeState`'s locks directly
- [x] Leave `ServeState` as a coordinator holding both new managers (`auth` / `providers`,
      alongside the existing `job_registry`) and route call sites through them consistently
- [x] Add focused unit tests for each extracted type constructed standalone (no full `ServeState`)
- [x] Confirm `docs/architecture.md` / `docs/ja/architecture.md` need no change — the serve module
      list describes `serve/` at a high level and never names `state.py`'s internal responsibilities

## References

- `bajutsu/serve/state.py:287`–`627` (the `ServeState` dataclass)
- `bajutsu/serve/state.py:387`–`401` (`token`, `sessions`, `oauth`, `oauth_allowed_users`,
  `oauth_admins`, `oauth_viewers`)
- `bajutsu/serve/state.py:528`–`538` (`check_token` / `issue_session` / `valid_session`)
- `bajutsu/serve/state.py:414`–`420` (`provider_settings`, `provider_settings_store`)
- `bajutsu/serve/state.py:451`–`456` (`_provider_lock`, `_persist_lock`)
- `bajutsu/serve/state.py:540`–`584` (`org_provider_settings` / `put_org_provider_settings` /
  `set_org_provider_choice`)
- `bajutsu/serve/operations/config.py:631` (`_persist_provider_settings`, the one caller that reaches
  into both locks from outside `state.py` today)
- `bajutsu/serve/authz.py` (the RBAC checks that read `oauth_allowed_users` / `oauth_admins` /
  `oauth_viewers`)
- BE-0198 (`roadmaps/BE-0198-serve-state-job-registry-split/`) — the `JobRegistry` extraction this
  item continues, including its own **Detailed design**'s note that a full decomposition was left to
  "later items"
- BE-0206 (`roadmaps/BE-0206-serve-state-module-split/`) — moved the state container into
  `bajutsu/serve/state.py`, the module this item's extractions land in
