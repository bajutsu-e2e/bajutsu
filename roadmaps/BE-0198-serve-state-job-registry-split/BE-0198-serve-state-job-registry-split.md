**English** · [日本語](BE-0198-serve-state-job-registry-split-ja.md)

# BE-0198 — Split the JobRegistry out of ServeState

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0198](BE-0198-serve-state-job-registry-split.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0198") |
| Implementing PR | [#812](https://github.com/bajutsu-e2e/bajutsu/pull/812) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`ServeState`, the dataclass at the center of `serve`, has grown into a catch-all. One type holds the
run-artifact directories, six swappable storage/log/session seams, the authentication policy, the AI
provider settings, the observability counters, *and* the job registry (the `jobs` dict plus its `_seq`
counter and `_lock`).

This item carves the job-registry responsibility out into a focused `JobRegistry` object that
`ServeState` holds. That responsibility covers registering jobs, assigning ids, and enforcing the
concurrency caps. The most self-contained slice of `ServeState`'s behavior then becomes independently
readable and testable, without changing any endpoint's behavior. `serve` is outside the deterministic
`run`/CI gate, so the change is a pure readability and maintainability refactor.

## Motivation

`ServeState` (`bajutsu/serve/jobs.py:174`–`447`) is one dataclass carrying at least six unrelated
groups of responsibility:

1. **Run I/O locations**: `runs_dir`, `scenarios_dir`, `baselines_dir`, `uploads_dir`, `cwd`,
   `base_cwd`, `root`.
2. **Swappable storage / delivery seams**: `artifacts`, `scenarios`, `baselines`, `secrets`,
   `executor`, `logbus`, `sessions`, `repository`, plus the per-org store factory `org_stores` and
   the `for_org` / `StoreBundle` machinery (BE-0015 multi-tenancy).
3. **Authentication & authorization**: `token` / `check_token`, `issue_session` / `valid_session`,
   the OAuth client, and the `oauth_allowed_users` / `oauth_admins` / `oauth_viewers` allowlists
   (BE-0051, BE-0015 7b/7c).
4. **AI provider settings**: `provider_settings` with `provider_settings_snapshot` /
   `set_provider_setting`, and the `ant_login_proc` / `ant_login_lock` sign-in state (BE-0183,
   BE-0175).
5. **Observability**: `active_jobs` / `in_flight_by_org` for the `/metrics` endpoint (BE-0169).
6. **The job registry**: `jobs`, `_seq`, `_lock`, and the methods `_register` / `register` /
   `try_register`, which assign each job its id and enforce the global / per-user / per-org
   concurrency caps (BE-0051, BE-0015 7c-3, BE-0016 Tier B).

The sixth group is the most separable. Only the registration and counting methods (`_register`,
`register`, `try_register`, `active_jobs`, `in_flight_by_org`) touch `jobs`, `_seq`, and `_lock`, and
those methods read almost nothing else on `ServeState`. The caps (`max_concurrent`,
`max_concurrent_per_user`, `max_concurrent_per_org`) travel with the methods, and the `logbus` that a
registered job needs is the only external dependency.

Because the group shares a dataclass with everything else, the registry's most important invariant is
stated in prose in a docstring, not enforced by a type whose whole surface is that invariant. The
invariant is: *"id assignment and the cap check happen atomically under one lock, so two concurrent
dispatches can't both slip past a cap"* (`try_register`, `bajutsu/serve/jobs.py:409`). Testing the cap
logic today means constructing a full `ServeState`, which resolves stores, secrets, and a launch
directory in `__post_init__`, even though the caps depend on none of that.

Two lower-level design defects compound the problem:

- The lock named `_lock` guards *two unrelated things*: the `jobs` dict and the `provider_settings`
  dict (`provider_settings_snapshot` / `set_provider_setting` take the same `_lock`,
  `bajutsu/serve/jobs.py:364`–`375`). A reader cannot tell from the name what the lock protects, and
  two responsibilities contend on one lock for no reason.
- `_seq` is a mutable counter whose only correct mutation site is `_register`. A `JobRegistry` makes
  `_register` the counter's sole owner by construction.

This is a size-M effort. The extraction is mostly mechanical: move five methods and three fields onto a
new class, then delegate. It touches a widely referenced type, so it must preserve `ServeState`'s
public surface. `serve` is deliberately outside the deterministic gate (it is Tier‑2 tooling, not the
`run` verdict), so no prime directive is at stake. The payoff is that the scarce-device concurrency
logic, which several BE items (BE-0051, BE-0015, BE-0016) layered onto one dataclass, finally lives in
a type that names and owns exactly that invariant.

## Detailed design

The refactor preserves behavior. Every `serve` endpoint, the executor, and `run_job` observe the same
behavior, ids are still assigned from a monotonic sequence, and the concurrency caps still reject at
the same thresholds. The work breaks down into these mutually exclusive units:

- **Introduce a `JobRegistry` type.** The type owns `jobs`, the id sequence, and its own lock, and it
  exposes exactly the registration and counting surface: `register(job)`, `try_register(job)` (which
  takes the caps and returns `None` when any cap is hit), `active_jobs()`, and `in_flight_by_org()`.
  The atomic "count-then-insert under one lock" invariant lives entirely inside this type, so the
  class boundary expresses the guarantee, and a docstring on a shared dataclass no longer does. The
  registry is the sole owner of the id counter, so `_seq` stops being a free-floating field on
  `ServeState`.
- **Decide where the concurrency caps live.** The three caps (`max_concurrent`,
  `max_concurrent_per_user`, `max_concurrent_per_org`) are configuration, not job-registry state. Two
  options exist:
  - pass the caps into `try_register`, keeping the registry a pure mechanism with caps supplied per
    call;
  - construct the registry with the caps, fixing them at build time from `serve()`'s flags.

  Pick one and state it. The call sites in the operations layer decide which reads more clearly.
- **Hold a `JobRegistry` on `ServeState` and delegate.** Either `ServeState` keeps thin forwarding
  methods (`register` / `try_register` / `active_jobs` / `in_flight_by_org`) that delegate to the
  registry, **or** callers reach `state.job_registry` directly. Again, pick one and apply it
  consistently, so the operations layer reads uniformly. Either way, `ServeState` no longer *defines*
  the job-registry fields and lock.
- **Give `provider_settings` its own lock.** Once `jobs` moves out, the shared `_lock` guards only
  `provider_settings`. Rename it to a lock that names what it protects (or move it with the
  provider-settings group), so no reader has to discover that one lock covered two unrelated
  dictionaries. This removes the false contention between job registration and Settings-panel writes.
- **Keep the `Job` dataclass where it is.** `Job` is the job's own record, and the executor and
  `run_job` consume it. Only its *registry* (the `dict`, the id assignment, and the caps) moves. This
  item does not touch `Job`, `run_job`, `_boot_devices`, `_build_app`, or the persistence helpers.
- **Add focused unit tests for the registry.** With the caps logic in its own type, tests exercise id
  monotonicity and each cap (global, per-user for an identified actor, per-org) directly against a
  `JobRegistry`, without building a full `ServeState`. Per the project's no-mock rule, the tests use
  no mocks: they construct plain `Job` values and a fake `logbus` (an in-memory bus already exists).

Out of scope (named so the boundary is explicit): the storage-seam group, the auth group, and the
AI-settings group each remain on `ServeState`. Splitting those further is a plausible follow-up, but
it is *not* part of this item. The job registry is the cleanest, most self-contained seam to carve
first, and doing it alone keeps the PR reviewable.

## Alternatives considered

- **Leave `ServeState` as one dataclass and rely on the docstrings that already describe each field
  group.** Rejected: the grouping is real but implicit, and the most important invariant (the atomic
  cap check) is prose on a shared type, not a class boundary. The extraction makes the invariant
  structural and lets a test exercise the cap logic without standing up the whole of `serve`.
- **Split every responsibility group at once (storage, auth, AI settings, registry) into separate
  collaborators.** Rejected for this item: a full decomposition is a large, cross-cutting change to a
  type referenced throughout the operations layer, and a reviewer would struggle to read it in one
  pass. The job registry is the most separable slice, because it shares almost no state with the rest.
  Carving it first delivers most of the readability win at the lowest risk, and later items can peel
  off the next group if it proves worthwhile.
- **Keep the fields on `ServeState` but move only the methods to free functions taking the dict.**
  Rejected: free functions over a shared dict give the invariant no home and give `_seq` no single
  owner. They would leave the mutable counter and its lock scattered on `ServeState` exactly as today,
  moving code without moving the responsibility.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Introduce a `JobRegistry` type owning `jobs` / the id sequence / its own lock, exposing
      `register` / `try_register` / `active_jobs` / `in_flight_by_org`
- [x] Decide and apply where the concurrency caps live (per-call vs. registry construction)
- [x] Hold a `JobRegistry` on `ServeState` and route the call sites through it consistently
      (thin delegation or direct access)
- [x] Give `provider_settings` its own named lock, freeing `_lock` from guarding two concerns
- [x] Add unit tests for id monotonicity and each concurrency cap directly against `JobRegistry`

### Log

- Extracted `JobRegistry` from `ServeState` in `bajutsu/serve/jobs.py`: the new type owns `jobs`,
  the id sequence, and its own lock, and exposes `register` / `try_register` / `active_jobs` /
  `in_flight_by_org`, with the atomic count-then-insert invariant enforced at its boundary. The
  concurrency caps stay on `ServeState` (config it already owns and `/metrics` reads) and are passed
  into `try_register` per call, so the registry is a pure mechanism. `ServeState` holds the registry,
  forwards the four methods, and exposes `jobs` as a read-through so existing lookups are unchanged;
  the former shared `_lock` is renamed `_provider_lock` now that it guards only `provider_settings`.
  Added unit tests exercising the registry directly (id monotonicity, the register-twice guard, and
  each cap) without standing up a full `ServeState`. Behavior-preserving; `serve` stays outside the
  deterministic gate.

## References

- `bajutsu/serve/jobs.py:174`–`447` (the `ServeState` dataclass)
- `bajutsu/serve/jobs.py:249`, `:311`, `:312` (`jobs`, `_seq`, `_lock`)
- `bajutsu/serve/jobs.py:388`–`426` (`_register` / `register` / `try_register` — the registry
  behavior and the atomic cap check)
- `bajutsu/serve/jobs.py:364`–`386` (`provider_settings_snapshot` / `set_provider_setting` /
  `active_jobs` / `in_flight_by_org` — the other users of `_lock`)
- Concurrency caps introduced by BE-0051 (global / shared token), BE-0015 (7c-3 per-user), BE-0016
  (Tier B per-org)
- Sibling decompositions on the same topic: BE-0143 (run-command decomposition), BE-0172 (run-loop
  step decomposition), BE-0092 (crawl coordinator extraction)
