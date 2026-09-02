**English** · [日本語](BE-0393-per-org-config-memory-ja.md)

# BE-0393 — Per-org config memory, restored into each session

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0393](BE-0393-per-org-config-memory.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0393") |
| Implementing PR | [#1844](https://github.com/bajutsu-e2e/bajutsu/pull/1844) (Unit 5) |
| Topic | Configuration sourcing |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md), [BE-0229](../BE-0229-per-org-provider-settings-resolution/BE-0229-per-org-provider-settings-resolution.md), [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md), [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md), [BE-0375](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md), [BE-0404](../BE-0404-collapse-project-layer/BE-0404-collapse-project-layer.md) |
<!-- /BE-METADATA -->

## Introduction

This item gives each org a durable memory of the configuration it used last, and restores that
configuration into each session that needs one, so a member of an org starts work with the org's
configuration already in place. An org is Bajutsu's multi-tenancy unit: the tenant a GitHub login
signs in as, and the scope every other per-org value — registered projects, AI provider settings,
secrets, and the audit log — is already keyed by
([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)). The bound
configuration is the one such value that is neither remembered nor scoped to anything: today it is
a single process-wide binding, so a member who signs in finds whatever the process last happened to
hold — usually nothing — and uploads a bundle again to get back to work.

The item separates two questions the single binding conflates. **Where the memory lives** is the
org: which configuration an org used last is a value its members share, and one that must outlive
every session. **What a live binding is scoped to** is the session: two members of one org must be
able to work at once, and one member switching configurations must not move the ground under
another member's run. A session with no binding of its own inherits the org's remembered
configuration, so two members who change nothing are working against the same configuration and the
same target list — the shared case — while either one may bind something else without disturbing
the other. A single-tenant `serve` on a developer's machine has one org and one session, and sees no
behavior change.

## Motivation

Signing in as an org today lands the member on an empty configuration. `serve` holds the bound
configuration in one set of process-global fields — the configuration file's path, the directory its
relative paths resolve against, its Git provenance, the flag marking a Git source bound at runtime,
the uploaded bundle it came from, and the org that bound it
([`bajutsu/serve/state.py`](../../bajutsu/serve/state.py)) — and a deployment started without
`--config` has none of them set. So the first thing each session asks of a member is the upload, the
Git spec, or the file-browser pick they already did last session, before any
[target](../../docs/glossary.md#target-app-device) is even listable.

Bajutsu already has the durable memory this needs, and does not write to it. A **project** is a
named binding to a configuration source, keyed by org, holding a locator for a Git source, a local
file, or an uploaded bundle addressed by its SHA-256 content hash, plus which project the org has
active ([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)); switching to a
project rebinds the configuration live, uploads included, because an uploaded bundle's bytes are
stored durably under that hash
([BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md)). Three
gaps keep that memory empty in practice. The binds a member actually performs — the file-browser
bind, the Git bind, the bundle upload, and the composed-artifact bind — register no project at all,
so nothing records what the org just bound. The database-backed registry keeps the active project in
a process-local dictionary rather than a table, so the deployment shape that has orgs is the shape
that forgets which project was active. The launch configuration auto-registers under the `default`
org alone, so on a multi-tenant deployment no org but `default` starts with an entry.

Filling those three gaps would make an org's last configuration recoverable, and would not make
restoring it safe. One process holds one binding, so restoring org B's configuration when a member
of org B signs in would take the configuration away from org A, whose runs, recordings, and crawls
resolve their scenarios and application paths through the same fields — a member's own sign-in
becoming another tenant's failed run. The same collision happens inside one org, without any
restore: a member who binds a branch's bundle today repoints the configuration of every colleague
in that org, mid-run. The binding is therefore what has to change, and the scope it needs is the
session rather than the org — a per-org binding removes the cross-tenant collision and keeps the
within-org one, which is the collision that lands on two people trying to test the same project.

An org-scoped seam is nonetheless what the memory needs, and every seam around the binding is
already org-scoped: the storage bundle a request resolves (`for_org`), the concurrency caps, the
object-store prefix, the per-org provider settings
([BE-0229](../BE-0229-per-org-provider-settings-resolution/BE-0229-per-org-provider-settings-resolution.md)).
The binding is the last ambient global in a request path that otherwise resolves everything through
the acting org, and the project registry is the org-scoped store the memory belongs in.

Two consequences follow beyond the removed upload. A deployment stops serializing its members
behind one configuration, so two orgs — and two members of one org — can work at once instead of
overwriting each other's binding. And a binding's owning org is unambiguous, which settles the target
ownership question the current global binding forces: a configuration bound through the application
programming interface (API) belongs to the org that bound it and its `orgs:` block partitions nothing
([BE-0375](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md)),
a rule expressed today by tagging the one global binding with its owner.

This item also absorbs an unsorted roadmap idea that asked for a smaller version of the same thing:
redirecting a fresh sign-in straight to the org's active project. That idea assumed the destination
already existed; the three gaps above are why it does not, and the units below build it.

## Detailed design

Nothing here touches the deterministic `run` path or adds an AI call anywhere: a binding is data, and
restoring one is a lookup and a materialization, both machine-checkable (prime directive 1). Units 1
and 2 are a refactor with no behavior change on a single-tenant deployment. Units 3 to 5 do not
depend on them: recording each bind, persisting the active project, and restoring a bundle from the
local cache are additive to the single binding as it stands, and together they already remove the
re-upload for a member willing to switch projects by hand. Units 1, 2, 6, and 7 are what turn that
into the configuration simply being there.

### 1. One binding value instead of six scattered fields

Group the fields that together *are* the binding — `config`, `cwd`, `config_provenance`, `upload`,
`git_config_from_api`, and `config_org` — into one frozen `ConfigBinding` value, and give
`ServeState` a single field holding it. The binding is currently six independent mutable attributes
that must be written together — and are written together: each binder sets all of them, and
`release_upload` clears three of them to keep a stale bundle's directory from leaking into the next
bind. A value that is replaced atomically makes the incomplete combinations unrepresentable, and
gives unit 2 one thing to key by session instead of six. Behavior is unchanged; the ~46 reads of
`state.config` and ~24 of `state.cwd` across the serve modules move to the value's fields.

### 2. A binding per session, resolved through the session's org

Replace the single binding field with a mapping from login session **and acting org** to
`ConfigBinding`. A session is the scope a member's own work happens in, so a bind made in one
session is visible to that session alone, and every other session — a colleague's, or the same
member's on another machine — keeps what it had. A session's slots are dropped when the session ends — on the revocation the session store
already performs, and on a bound the mapping enforces itself, so a process that accumulates quiet
sessions evicts the oldest binding rather than growing without limit. The acting org joins the key
because a session may
change which org it acts as, and a binding made as one org must not answer as another: target
ownership rides on the org, so a binding that outlived an org change would hand one org's targets to
a request acting as a different one. A session's binding is dropped with the session; what survives is the org's remembered
configuration (unit 4), which the next session inherits (unit 6).

Each binding still records the org that made it, because target ownership is an org question, not a
session one: a configuration bound through the API belongs to the org that bound it, so
`targets_for` reads that org off the binding rather than off a tag on a global. The launch configuration keeps its current meaning as the **fallback binding**: a session
with no binding of its own and no configuration to inherit reads the configuration `serve` started
with, whose `orgs:` block partitions its targets between orgs exactly as today.

Not every reader of the binding is inside a request with a session. `config_content`,
`server_settings`, `active_key_env`, the usage-ledger path, and the scenario-directory closure
`_scenarios_dir_for` take no actor at all, and the deployment-wide answer is what two of them
actually want. Each of the five is therefore settled explicitly rather than by an ambient default.
Three report what a member is working against — the configuration view, the server-settings tab's
`hasConfig` / `configSource` / configuration path, and the scenario directory — and resolve the
session's binding rather than the ambient one. Two of the three take the session from their handler.
The scenario directory cannot: `_scenarios_dir_for` is reached through a closure `ServeState`
constructs once, over `self`, and hands to every org through `for_org`, so no handler is in scope
when the closure fires. The seam that becomes session-aware there is the scenario store's own
resolution, which the request already reaches with a session in hand. Two describe the deployment — the active key environment variable and the usage
ledger — and read the fallback binding, which the item states rather than leaves implied. An ambient
default is what this unit removes, so leaving one for the readers that are inconvenient would leave
the bug in place.

A run must not follow a later rebind, and today it partly can. A job's command line carries
`--config`, so the configuration file a run parses is frozen at dispatch, and a worker run carries
the configuration text itself in its materials. The working directory is not frozen with it:
`Job.cwd` defaults to `None` and no dispatcher sets it, so the spawn reads `state.cwd` at `popen`
time — a rebind between registration and spawn, a window a device boot or an on-demand build can
hold open, repoints a run that has already been accepted. This unit closes that window by capturing
the session's binding on the job at enqueue and spawning from the captured value.

### 3. Every bind records the org's project

Have each bind register a project under the acting org and mark it the org's remembered
configuration: the file-browser bind, the
Git bind, the bundle upload, and the composed-artifact bind. The registry's `add` is already
idempotent by name and rebinds an existing name's source, so re-binding the same project repeatedly
neither duplicates nor collides. This is the unit that creates the durable memory at all; without it
the mapping in unit 2 is empty at every start.

Naming: extend the identity the launch configuration already derives from a configuration path and
its provenance (`launch_project_identity`), so a Git source names itself by repository and path and
an upload by its file name, and a member who never opens the projects page still gets a legible entry.

### 4. The active project persists per org

Give the database-backed registry a durable active project per org, so a restart no longer drops it.
The file-backed local registry already persists the active project in its JSON store; the
database-backed one holds it in a process-local dictionary. Follow the shape BE-0229 uses for
provider settings, whose `provider_settings` table is keyed by `org_id` for exactly this reason: an
`active_project_id` column on the `orgs` row keeps "one active project per org" true by construction
rather than by a uniqueness rule over a wider table. Also drop the `default`-org constant from the
launch registration so a multi-tenant deployment registers the launch configuration for the orgs that
can reach it.

### 5. Restoring an uploaded bundle without an object store

Resolve the local extraction cache before requiring an object store when reactivating an
upload-sourced project. Reactivation today returns "nothing to restore from" as soon as no object
store is configured, before it looks at the cache — yet the extracted bundle sits in that cache under
its content hash, in a directory that survives a restart, and a cache hit is already the path a
configured deployment takes. The result is that the deployment shape most likely to have no object
store, a local or single-node `serve`, is the one that cannot restore a bundle it still has on disk.
The content-hash validation that guards the cache path stays exactly as it is: the hash comes from a
stored project record a client can shape, so it is checked before it becomes a path component
(BE-0243), and this unit reorders the fallbacks without relaxing that check.

### 6. Restoring lazily, on first use

Restore into a session's empty slot when a request needs a binding, from the active project of the
org that session acts as. Restore on first need rather than during sign-in for two reasons: sign-in
must not wait on a network fetch of a Git source or a bundle, and the org a session acts as can
change without a sign-in — a session-scoped org selection is in flight separately, and a trigger
written against sign-in alone would miss it. Keying the restore to "this session has no binding for
this org yet" covers both entry paths without naming either.

Restoration is best-effort and never fails a request that did not need it: a moved local file, an
unreachable repository, or an evicted bundle leaves the session with no binding and logs the reason,
which is the same posture the org seeding at startup takes with a failed database read. A failed
restore is not retried for that session and org, so a repeatedly failing source does not re-fetch on
every request.

### 7. What a member sees

Show which configuration the session is bound to, and where it came from, in the header the org
badge already lives in. Distinguish the three origins a member needs to tell apart: the
configuration they bound in this session, the one inherited as the org's remembered configuration,
and the deployment's launch configuration. A member who binds something else is changing what
colleagues' next sessions inherit, so say that where they bind it. Record a restore in the audit log
like the binds it stands in for.

### Testing

Every unit is deterministic and testable without a Simulator: a binding replaced atomically, two
sessions of one org holding different bindings at once, a bind recording its project, an active
project surviving a simulated restart, a bundle restored from the cache with no object store
configured, a fresh session inheriting the org's remembered configuration, a failed restore
degrading to "no binding" and not retrying, and a job enqueued before a rebind spawning against the
configuration and working directory it captured.

## Alternatives considered

**An explicit "restore the last configuration" button, keeping the global binding.** The smallest
change that removes the re-upload: record each bind as a project (unit 3), then let a member rebind
with one click. It leaves the cross-tenant hazard untouched by never rebinding on its own, and it
also leaves the deployment serializing every tenant behind one binding — two orgs still overwrite
each other, and the member still performs an action to get back to a configuration the deployment
already knows. Worth keeping as the interim state after units 3 to 5 land, not as the destination.

**Conditional automatic restore, keeping the global binding.** Restore only when nothing is bound, or
when the bound configuration's owner matches the acting org. This avoids the hazard in the common
case and not in the general one: two orgs active at once still contend for the single binding, and
whichever binds second decides what the first one sees. The condition would also have to be
re-checked at every read to stay sound, which is unit 2's per-session resolution written as a guard
instead of as a data structure.

**A live binding per org rather than per session.** One binding per org, shared by that org's
members, is the smaller change: the mapping's key is a value every request already resolves, and the
memory needs no separate scope because the live binding *is* the memory. It removes the cross-tenant
collision and keeps the within-org one — a member binding a branch's bundle repoints every colleague
in that org, which is precisely the case of two people testing one project. Sharing is still the
default under the chosen design, through inheritance rather than through a shared mutable slot: a
session that binds nothing sees what the org remembers, so colleagues agree unless one of them
deliberately diverges. What a per-org binding buys over that is one fewer scope to carry, at the
price of making a colleague's switch indistinguishable from an outage.

**A per-org configuration column instead of the project registry.** Storing "this org's last
configuration source" directly on the org row would skip units 3 and 4. It would also duplicate the
project registry, which already stores exactly that locator per org for all three source kinds,
already materializes each kind, and already has a page and a switcher. A second store would then
disagree with the first — a member switching projects would leave the column stale — for no gain.

**Remembering the binding in the browser.** A locator held client-side and re-sent on load needs no
server change, and puts the choice of what the server binds in a value a client can shape. The
locator is exactly the input already validated at bind time, and every restore would have to re-run
that validation against an untrusted value, at the cost of the memory being per browser rather than
per org: a member on a second machine, or a colleague in the same org, starts empty again.

**Restoring during sign-in.** Rejected for the reasons unit 6 gives: sign-in would wait on a fetch it
does not need, and a trigger written against sign-in misses an org change within a session.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1. Group the binding's fields into one `ConfigBinding` value.
- [x] 2. Key the binding by session, settle the actorless readers, and capture a job's binding at enqueue.
- [x] 3. Record a project on every bind as the acting org's remembered configuration. **Delivered
  by [BE-0404](../BE-0404-collapse-project-layer/BE-0404-collapse-project-layer.md) unit 1**, which
  collapses the requirement to one `orgs.config_source` column written by the bind itself — the
  named-project registry this unit assumed is gone.
- [x] 4. Persist the active project per org in the database, and drop the `default`-org launch
  registration. **Delivered by the same unit**: with one config source per org there is no "active"
  pointer left to persist, and no launch registration to drop.
- [x] 5. Restore an uploaded bundle from the local cache with no object store configured.
- [ ] 6. Restore the org's remembered configuration into a session lazily on first use, best-effort.
- [ ] 7. Show the session's binding and its origin, and audit a restore.

## References

- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) — orgs as the
  multi-tenancy unit, and the per-org scoping every other value already follows.
- [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) — the project registry:
  per-org named bindings, the active project, and the live rebind this item restores through.
- [BE-0229](../BE-0229-per-org-provider-settings-resolution/BE-0229-per-org-provider-settings-resolution.md)
  — the per-org, restart-surviving persistence shape unit 4 follows.
- [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md) —
  content-addressed bundle storage, and the validation unit 5 preserves.
- [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md) — the
  composed-artifact locator, the fourth bind unit 3 covers.
- [BE-0375](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md) —
  target ownership for an API-bound configuration, which unit 2 expresses through the org recorded on
  the binding.
- The `session-scoped-org-selection` proposal, in flight on its own branch and not yet numbered —
  the item that lets one session choose which of a login's orgs it acts as. This item's key is the
  pair of a session and its acting org, so the two meet there: without a session-level org choice
  the acting org is the login's single org and the pair collapses to the session, and with one the
  pair is what keeps a binding made as one org from answering as another.
- [`docs/architecture.md`](../../docs/architecture.md) — what `serve` holds today.
