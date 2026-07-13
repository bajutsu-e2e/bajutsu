**English** · [日本語](BE-XXXX-serve-state-decomposition-continued-ja.md)

# BE-XXXX — Continue decomposing ServeState into auth and provider-settings managers

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-state-decomposition-continued.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`ServeState` (`bajutsu/serve/state.py:287`–`627`) is a roughly forty-field dataclass at the center
of `serve`. BE-0198 already carved the most self-contained slice — the job registry (`jobs`, the id
sequence, and their lock) — out into a standalone `JobRegistry` type that `ServeState` now holds and
delegates to; BE-0206 later moved the state container itself into its own module,
`bajutsu/serve/state.py`, separate from the job-execution engine. This item continues that same
decomposition on the two next-most-cohesive slices still living directly on `ServeState`: the
authentication/session/OAuth cluster and the AI-provider-settings cluster. Each becomes its own
type that `ServeState` holds and forwards to, exactly as it now holds `job_registry`. `serve` sits
outside the deterministic `run`/CI gate (prime directive 1), so this is a pure readability and
maintainability refactor with no effect on any assertion or pass/fail verdict.

## Motivation

A single dataclass mixing config binding, authentication, per-org AI settings, four storage seams,
the job registry, upload-sandbox state, and evidence/object-store config is hard to reason about:
a reader looking for "how does a request get authenticated" or "how is a provider choice persisted"
has to first mentally subtract everything else on the type. BE-0198's `JobRegistry` extraction
demonstrated that the fix is not a rewrite — it is finding a self-contained field-and-method cluster
and giving it a boundary. Two such clusters remain:

1. **Auth / session / OAuth.** The fields `token`, `sessions`, `oauth`, `oauth_allowed_users`,
   `oauth_admins`, and `oauth_viewers` (`state.py:387`–`401`) and the methods `check_token`,
   `issue_session`, and `valid_session` (`state.py:528`–`538`) together answer one question — is
   this request authenticated, and as whom — and share no state with the rest of `ServeState`.
   `oauth_allowed_users` / `oauth_admins` / `oauth_viewers` are read by the authorization layer
   (`bajutsu/serve/authz.py`) for role-based access control (RBAC); they travel with the rest of the
   auth cluster because they are configured alongside `token` and `oauth` at server construction and
   never change after that.
2. **Provider settings.** The fields `provider_settings`, `provider_settings_store`, and the two
   locks that guard them — `_provider_lock` (`state.py:451`) and `_persist_lock` (`state.py:456`) —
   plus the methods `org_provider_settings`, `put_org_provider_settings`, and
   `set_org_provider_choice` (`state.py:540`–`578`) form the in-memory half of the per-org AI
   provider selection (BE-0229). The persistence half, `_persist_provider_settings`
   (`bajutsu/serve/operations/config.py:631`), already lives outside `state.py` and reaches back in
   to take both locks and read/write `provider_settings` directly — today only possible because it
   imports `ServeState`'s internals; a named manager gives that cross-module caller a narrow surface
   to call instead of reaching around the dataclass.

Together the two clusters own 3 of `ServeState`'s remaining locks (the fourth, the job registry's
own lock, already moved with BE-0198) and roughly a dozen of its fields — the next-largest cohesive
groups after the registry, and the same shape of opportunity BE-0198 named as a "plausible follow-up"
in its own **Detailed design** but deliberately left out of scope to keep that PR reviewable. This
item is that follow-up for two of the remaining groups; the storage-seam group (`artifacts`,
`scenarios`, `baselines`, `secrets`, `executor`, `repository`, `org_stores` / `StoreBundle`) is left
for a later item, since it is a distinct, larger cluster of its own.

## Detailed design

The refactor is behavior-preserving throughout: every `serve` endpoint, operations-layer function,
and test observes the same authentication decisions, session lifetimes, and provider-settings
reads/writes as today. The work splits into two mutually exclusive units, mirroring the pattern
BE-0198 set:

- **Extract an auth/session type for the first cluster.** Introduce a type (e.g. `AuthConfig` for
  the fixed `token` / `oauth` / allow-list configuration, or a broader `SessionManager` that also
  wraps `sessions` and the `issue_session` / `valid_session` / `check_token` methods — the design
  decides which shape reads more clearly, since `sessions` is itself already a swappable
  `SessionStore` seam rather than a plain field) that owns `token`, `sessions`, `oauth`,
  `oauth_allowed_users`, `oauth_admins`, and `oauth_viewers`. `ServeState` holds one instance and
  either forwards `check_token` / `issue_session` / `valid_session` as thin delegating methods (the
  pattern `register` / `try_register` already use for `job_registry`) or exposes the type directly
  and updates the handful of call sites (`serve/handler.py`, `serve/authz.py`) to read through it —
  pick one and apply it consistently, as BE-0198 did.
- **Extract a `ProviderSettingsManager` for the second cluster.** It owns `provider_settings`,
  `provider_settings_store`, `_provider_lock`, and `_persist_lock`, and exposes
  `org_provider_settings` / `put_org_provider_settings` / `set_org_provider_choice` with their
  existing copy-on-read/copy-on-write discipline preserved exactly — every read returns an
  independent `OrgProviderSettings` copy (including its `slots` dict) so a caller can never alias the
  live entry, and every write stores a fresh copy rather than the caller's own instance. This is the
  invariant that matters most here (the same way atomic id-assignment-under-one-lock mattered most
  for `JobRegistry`), so it must be preserved by the new type's boundary, not just by copying the
  method bodies. `operations/config.py`'s `_persist_provider_settings` — the one caller today that
  reaches into `ServeState` to take both locks directly — becomes a caller of the manager's public
  surface instead (either the manager grows a `persist`-shaped method that takes `_persist_lock`
  itself, or it exposes both locks for that one out-of-package caller; the design picks whichever
  keeps the manager's own invariants enforceable without leaking lock objects further than this one
  necessary case).
- **Leave `ServeState` as the coordinator.** After both extractions, `ServeState` holds references
  to `job_registry` (already the case), the new auth/session type, and the new
  `ProviderSettingsManager`, and forwards the handful of call sites that need to keep reading through
  `ServeState` today. It stops *defining* the auth and provider-settings fields, methods, and locks
  itself.
- **Preserve lock discipline exactly.** Each extracted manager owns its own lock (`_provider_lock`
  and `_persist_lock` both move with the provider-settings cluster, kept as two distinct locks so I/O
  never runs inside the in-memory lock, exactly as documented on `_persist_lock` today); the
  publish-outside-lock pattern already used by `org_provider_settings` (copy taken and returned
  after releasing the lock) and `set_org_provider_choice` (mutation happens entirely inside the lock,
  nothing published outside it while still locked) stays intact. No new lock is introduced and no
  existing lock's scope changes.
- **Update tests and the module-list docs.** Add focused unit tests for each extracted type
  constructed on its own (mirroring BE-0198's registry tests — no full `ServeState`, no mocks), and
  update `docs/architecture.md` / `docs/ja/architecture.md` if the serve module list names
  `state.py`'s responsibilities in a way this split changes.

Out of scope, named so the boundary is explicit: the storage-seam group (`artifacts`, `scenarios`,
`baselines`, `secrets`, `executor`, `repository`, `for_org` / `StoreBundle`), the run-I/O-location
group (`runs_dir`, `scenarios_dir`, `baselines_dir`, `uploads_dir`, `cwd`, `base_cwd`, `root`), the
upload-sandbox state (`upload`, `upload_exec`, `bind_upload` / `release_upload`), and the
evidence/object-store config (`evidence`, `object_store`, `object_store_prefix`) all remain on
`ServeState`. This item does not touch `JobRegistry`, `Job`, `run_job`, or any code in
`bajutsu/serve/jobs.py`.

## Alternatives considered

- **Extract every remaining cluster in one pass** (auth, provider settings, storage seams, run-I/O
  locations, upload-sandbox state, evidence/object-store config, all at once). Rejected for this
  item: `ServeState` is referenced throughout the operations layer, and a single PR touching every
  remaining group would be large and hard to review in one pass — the same reasoning BE-0198 gave
  for carving out only the job registry first. The auth and provider-settings clusters are the next
  two most self-contained groups (each already has its own lock(s) and a small, well-named method
  set), so extracting them together is one reviewable step; further extraction of the storage-seam
  and other groups can follow as separate items.
- **Leave `ServeState` as is and rely on its field-group comments.** Rejected: the comments already
  describe the auth and provider-settings groups accurately (as BE-0198's motivation observed for the
  job registry before its extraction), but the grouping stays implicit, and the copy-on-read/write
  discipline that prevents `provider_settings` dict aliasing is enforced only by convention across
  three methods rather than by a class boundary that a new caller cannot easily bypass.
- **Move only the fields into a plain nested dataclass without moving the methods.** Rejected: a
  bag of fields with no methods leaves the lock-discipline invariant scattered across
  `ServeState`'s own method bodies exactly as today, moving data without moving the responsibility —
  the same objection BE-0198 raised against free functions over a shared dict.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Extract an auth/session type (e.g. `AuthConfig` / `SessionManager`) owning `token`,
      `sessions`, `oauth`, `oauth_allowed_users`, `oauth_admins`, `oauth_viewers`, and the
      `check_token` / `issue_session` / `valid_session` methods
- [ ] Extract a `ProviderSettingsManager` owning `provider_settings`, `provider_settings_store`,
      `_provider_lock`, `_persist_lock`, and the `org_provider_settings` /
      `put_org_provider_settings` / `set_org_provider_choice` methods, preserving the
      copy-on-read/copy-on-write discipline exactly
- [ ] Migrate `operations/config.py`'s `_persist_provider_settings` to call the manager's public
      surface instead of reaching into `ServeState`'s locks directly
- [ ] Leave `ServeState` as a coordinator holding both new managers (alongside the existing
      `job_registry`) and route call sites through them consistently
- [ ] Add focused unit tests for each extracted type constructed standalone (no full `ServeState`)
- [ ] Update `docs/architecture.md` / `docs/ja/architecture.md` if the serve module list changes

## References

- `bajutsu/serve/state.py:287`–`627` (the `ServeState` dataclass)
- `bajutsu/serve/state.py:387`–`401` (`token`, `sessions`, `oauth`, `oauth_allowed_users`,
  `oauth_admins`, `oauth_viewers`)
- `bajutsu/serve/state.py:528`–`538` (`check_token` / `issue_session` / `valid_session`)
- `bajutsu/serve/state.py:414`–`420` (`provider_settings`, `provider_settings_store`)
- `bajutsu/serve/state.py:451`–`456` (`_provider_lock`, `_persist_lock`)
- `bajutsu/serve/state.py:540`–`578` (`org_provider_settings` / `put_org_provider_settings` /
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
