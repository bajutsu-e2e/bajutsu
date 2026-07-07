**English** · [日本語](BE-XXXX-serve-state-job-registry-split-ja.md)

# BE-XXXX — Split the JobRegistry out of ServeState

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-state-job-registry-split.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`ServeState`, the dataclass at the center of `serve`, has grown into a catch-all: in one type it
holds the run-artifact directories, six swappable storage/log/session seams, the authentication
policy, the AI provider settings, the observability counters, *and* the job registry (the
`jobs` dict plus its `_seq` counter and `_lock`). This item carves the job-registry
responsibility — registering jobs, assigning ids, and enforcing the concurrency caps — out into a
focused `JobRegistry` object that `ServeState` holds, so the most self-contained slice of
`ServeState`'s behavior becomes independently readable and testable without changing any endpoint's
behavior. `serve` is outside the deterministic `run`/CI gate, so this is a pure readability and
maintainability refactor.

## Motivation

`ServeState` (`bajutsu/serve/jobs.py:174`–`447`) is one dataclass carrying at least six unrelated
groups of responsibility:

1. **Run I/O locations** — `runs_dir`, `scenarios_dir`, `baselines_dir`, `uploads_dir`, `cwd`,
   `base_cwd`, `root`.
2. **Swappable storage / delivery seams** — `artifacts`, `scenarios`, `baselines`, `secrets`,
   `executor`, `logbus`, `sessions`, `repository`, plus the per-org store factory `org_stores` and
   the `for_org` / `StoreBundle` machinery (BE-0015 multi-tenancy).
3. **Authentication & authorization** — `token` / `check_token`, `issue_session` /
   `valid_session`, the OAuth client and the `oauth_allowed_users` / `oauth_admins` /
   `oauth_viewers` allowlists (BE-0051, BE-0015 7b/7c).
4. **AI provider settings** — `provider_settings` with `provider_settings_snapshot` /
   `set_provider_setting`, and the `ant_login_proc` / `ant_login_lock` sign-in state (BE-0183,
   BE-0175).
5. **Observability** — `active_jobs` / `in_flight_by_org` for the `/metrics` endpoint (BE-0169).
6. **The job registry** — `jobs`, `_seq`, `_lock`, and the methods `_register` / `register` /
   `try_register` that assign each job its id and enforce the global / per-user / per-org
   concurrency caps (BE-0051, BE-0015 7c-3, BE-0016 Tier B).

The sixth group is the most separable. `jobs` / `_seq` / `_lock` are touched *only* by the
registration and counting methods (`_register`, `register`, `try_register`, `active_jobs`,
`in_flight_by_org`), and those methods read almost nothing else on `ServeState` — the caps
(`max_concurrent`, `max_concurrent_per_user`, `max_concurrent_per_org`) travel with them, and the
`logbus` a registered job needs is the only external dependency. Yet because they live on the same
dataclass as everything else, the job-registry invariant that matters most — *"id assignment and
the cap check happen atomically under one lock so two concurrent dispatches can't both slip past a
cap"* (`try_register`, `bajutsu/serve/jobs.py:409`) — is stated in prose in a docstring rather than
enforced by a type whose whole surface is that invariant. Testing the cap logic today means
constructing a full `ServeState` (which resolves stores, secrets, and a launch directory in
`__post_init__`) even though the caps depend on none of that.

Two lower-level smells compound this. The lock named `_lock` guards *two unrelated things* — the
`jobs` dict and the `provider_settings` dict (`provider_settings_snapshot` /
`set_provider_setting` take the same `_lock`, `bajutsu/serve/jobs.py:364`–`375`) — so a reader
cannot tell from the name what the lock protects, and two responsibilities contend on one lock for
no reason. And `_seq` is a mutable counter whose only correct mutation site is `_register`; a
`JobRegistry` makes that the counter's sole owner by construction.

This is a size-M effort. The extraction is mostly mechanical — move five methods and three fields
onto a new class, then delegate — but it touches a widely-referenced type, so it must preserve
`ServeState`'s public surface. `serve` is deliberately outside the deterministic gate (it is Tier‑2
tooling, not the `run` verdict), so no prime directive is at stake; the payoff is that the
scarce-device concurrency logic, which several BE items (BE-0051, BE-0015, BE-0016) layered onto
one dataclass, finally lives in a type that names and owns exactly that invariant.

## Detailed design

The refactor is behavior-preserving: every `serve` endpoint, the executor, and `run_job` observe
the same behavior, ids are still assigned from a monotonic sequence, and the concurrency caps still
reject at the same thresholds. The work breaks down into these mutually exclusive units:

- **Introduce a `JobRegistry` type** that owns `jobs`, the id sequence, and its own lock, and
  exposes exactly the registration and counting surface: `register(job)`, `try_register(job)`
  (taking the caps and returning `None` when any cap is hit), `active_jobs()`, and
  `in_flight_by_org()`. The atomic "count-then-insert under one lock" invariant lives entirely
  inside this type, so the guarantee is expressed by the class boundary rather than by a docstring
  on a shared dataclass. The registry is the sole owner of the id counter, so `_seq` stops being a
  free-floating field on `ServeState`.
- **Decide where the concurrency caps live.** The three caps (`max_concurrent`,
  `max_concurrent_per_user`, `max_concurrent_per_org`) are configuration, not job-registry state.
  Pass them into `try_register` (keeping the registry a pure mechanism, caps supplied per call) or
  construct the registry with them (caps fixed at build time from `serve()`'s flags). Pick one and
  state it; the call sites in the operations layer decide which reads more clearly.
- **Hold a `JobRegistry` on `ServeState` and delegate.** `ServeState` keeps thin forwarding methods
  (`register` / `try_register` / `active_jobs` / `in_flight_by_org`) that delegate to the registry,
  **or** callers reach `state.job_registry` directly — again, pick one and apply it consistently so
  the operations layer reads uniformly. Either way `ServeState` no longer *defines* the job-registry
  fields and lock.
- **Give `provider_settings` its own lock.** Once `jobs` moves out, the shared `_lock` guards only
  `provider_settings`; rename it to a lock that names what it protects (or move it with the
  provider-settings group), so no reader has to discover that one lock covered two unrelated
  dictionaries. This removes the false contention between job registration and Settings-panel writes.
- **Keep the `Job` dataclass where it is.** `Job` is the job's own record and is consumed by the
  executor and `run_job`; only its *registry* (the `dict` + id assignment + caps) moves. This item
  does not touch `Job`, `run_job`, `_boot_devices`, `_build_app`, or the persistence helpers.
- **Add focused unit tests for the registry.** With the caps logic in its own type, test id
  monotonicity and each cap (global, per-user for an identified actor, per-org) directly against a
  `JobRegistry`, without building a full `ServeState`. No mocks — construct plain `Job` values and a
  fake `logbus` (an in-memory bus already exists), per the project's no-mock rule.

Out of scope (named so the boundary is explicit): the storage-seam group, the auth group, and the
AI-settings group each remain on `ServeState`. Splitting those further is a plausible follow-up but
is *not* part of this item — the job registry is the cleanest, most self-contained seam to carve
first, and doing it alone keeps the PR reviewable.

## Alternatives considered

- **Leave `ServeState` as one dataclass and rely on the docstrings that already describe each
  field group.** Rejected: the grouping is real but implicit, and the most important invariant (the
  atomic cap check) is prose on a shared type rather than a class boundary. The extraction makes the
  invariant structural and lets the cap logic be tested without standing up the whole of `serve`.
- **Split every responsibility group at once (storage, auth, AI settings, registry) into separate
  collaborators.** Rejected for this item: a full decomposition is a large, cross-cutting change to
  a type referenced throughout the operations layer, and would be hard to review in one pass. The
  job registry is the most separable slice — it shares almost no state with the rest — so carving it
  first delivers most of the readability win at the lowest risk, and later items can peel off the
  next group if it proves worthwhile.
- **Keep the fields on `ServeState` but move only the methods to free functions taking the dict.**
  Rejected: free functions over a shared dict do not give the invariant a home or let `_seq` have a
  single owner; they would leave the mutable counter and its lock scattered on `ServeState` exactly
  as today, moving code without moving the responsibility.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Introduce a `JobRegistry` type owning `jobs` / the id sequence / its own lock, exposing
      `register` / `try_register` / `active_jobs` / `in_flight_by_org`
- [ ] Decide and apply where the concurrency caps live (per-call vs. registry construction)
- [ ] Hold a `JobRegistry` on `ServeState` and route the call sites through it consistently
      (thin delegation or direct access)
- [ ] Give `provider_settings` its own named lock, freeing `_lock` from guarding two concerns
- [ ] Add unit tests for id monotonicity and each concurrency cap directly against `JobRegistry`

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
