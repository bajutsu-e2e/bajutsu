**English** · [日本語](BE-0404-collapse-project-layer-ja.md)

# BE-0404 — Collapse the project layer into the org and the target

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0404](BE-0404-collapse-project-layer.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0404") |
| Implementing PR | [#1841](https://github.com/bajutsu-e2e/bajutsu/pull/1841) |
| Topic | Hosting the web UI |
| Related | [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md), [BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard.md), [BE-0275](../BE-0275-serve-projects-management-page/BE-0275-serve-projects-management-page.md), [BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md), [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md) |
<!-- /BE-METADATA -->

## Introduction

This item removes the project layer from `bajutsu serve`. An org will own its configuration
directly. A run will carry the target it ran and a free-text **label** the operator may set. The
project registry, the `projects` table, and every surface built on them will be deleted.

`bajutsu serve` nests three units of ownership today. An **org** owns **projects**, and a project
binds one configuration file, which declares one or more
[targets](../../docs/glossary.md#target-app-device). This item removes the middle unit.

A **project** today is a named binding to a configuration source, keyed by org. A user adds one
through `POST /api/projects` or the `bajutsu project` command-line interface (CLI), and switches
between projects from a picker in the `serve` header
([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)). It exists to make one
`serve` process a hub over several configurations.

The deployments this tool actually sees do not want that hub. An org is one service, and the several
things a team compares within that service are its targets: Android, iOS, and web. Switching to a
different service is switching orgs. Switching to a different configuration of the same service is
restarting `serve`, which costs a few seconds.

Removing the project layer would lose one thing worth keeping. A project is the one recorded value
that can keep two configurations' run histories apart. Unit 4 below finally makes the ordinary views
read that partition.

This item keeps the partition and drops the registry around it. The string the launcher already
computes for the auto-registered project becomes a plain column on the run row, which an operator may
override per run.

## Motivation

The project layer is unreachable in the deployment shape that has orgs, and unnecessary in the shape
that does not.

A project is created on exactly three paths:

- The launcher auto-registers the bound configuration on boot.
- `POST /api/projects` registers one.
- `bajutsu project add` registers one.

The launcher's auto-registration is hardcoded to the single `default` org
([`bajutsu/serve/operations/config.py`](../../bajutsu/serve/operations/config.py)), and so is the
CLI.

The web UI's only way to add a project is the Add form on the Projects view. The tab for that view
stays hidden while the org has no projects, and the header picker stays hidden until two or more
exist. An org with zero projects therefore cannot reach the form.

A member who signs in to a hosted org other than `default` sees an empty project list, a `None`
active project, runs recorded with a null `project_id`, and no picker. The one path left to that
member is a direct call to `POST /api/projects`.

Nothing blocks the member, because the bound configuration is process-global. The org's targets are
filtered out of it by `orgs.<name>.targets`, a separate mechanism that never consults a project.

Even a project registered through that one remaining path does not survive a restart. The
database-backed registry holds the active project in a plain dictionary rather than a column. Its own
docstring states the reason: "active" was designed as a session notion, not durable state
([`bajutsu/serve/project_registry.py`](../../bajutsu/serve/project_registry.py)).

After a restart, the active project resolves to `None` again. `POST /api/projects/<name>/run` answers
`409` ("not the active binding; switch to it first"), and the picker that would perform the switch
is hidden from that org.
[BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory.md) names the same defect:
the deployment shape that has orgs is the shape that forgets which project was active.

Meanwhile, the partition the layer exists to provide is recorded and never read. A run stamps
`runs.project_id` at enqueue. The only readers are the project surfaces themselves:

- the per-project run listing (`GET /api/projects/<name>/runs`);
- the Projects list's own latest-run summary;
- the cross-project comparison
  ([BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard.md)).

The ordinary run list, Replay, and the run-stats dashboard apply no project filter at all
([`bajutsu/serve/operations/reads.py`](../../bajutsu/serve/operations/reads.py)).

An operator who restarts `serve` against a second configuration therefore gets two distinct
`project_id` values on the run rows. The operator still sees one interleaved history in every view
that matters. The partition data exists, and the reading side never uses it.

The comparison that a team does want is not available at any price, because the axis it needs is
discarded. A run's target is validated at enqueue
([`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py)) and then
persisted nowhere:

- `RunResult` carries no target field.
- The manifest records the actuator as `backend` and the operating system as `device_runtime`, but
  never the target name.
- The `runs` table has no target column.

"The Android target passes while the iOS target fails" therefore cannot be computed from stored data.
The shipped dashboard offers "project *checkout* versus project *search*", a comparison between two
services that nobody performs.

Two observable differences would tell a later reader that this change arrived:

- An operator who restarts `serve` against a different configuration sees each configuration's runs
  separately in the ordinary run list and the run-stats dashboard, instead of one interleaved
  history.
- A member of one org sees the Android target passing and the iOS target failing side by side in a
  single view, without switching anything.

This stays within the prime directives. The label is metadata attached to a run and never an input
to a verdict. The target stamp records a value the deterministic dispatch path already resolved.
Every view this item adds is a read-only aggregation over stored run data, so no large language model
(LLM) enters the `run` or continuous-integration path.

Removing the project layer removes configuration-shaped state from the tool rather than adding any.
The app-agnostic directive is therefore unaffected: per-app differences stay in each configuration's
`targets.<name>` entries.

## Detailed design

The work is MECE across five units: re-homing the configuration binding onto the org, the run label,
the target stamp, the reading surfaces, and the removal itself. Unit 1 comes first, because the
project row is load-bearing for one path today. Unit 5 comes last, because it depends on the other
four.

### 1. The org holds one configuration source

A hosted replica recovers an uploaded configuration it did not itself receive by reading the stored
project record. `activate_uploaded_project` resolves the record's `sha256`, or the
`{config, scenarios, binary}` digests of a composed triple. It then fetches the bytes from the object
store ([BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md),
[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md)).

That is the path a member uses to run a locally built binary or a locally edited scenario against a
hosted deployment. It is the one configuration change a hosted org genuinely performs. Deleting the
`projects` table without moving that record would break the path.

So the org row gains one configuration-source column. The column holds the same discriminated record
the project row holds today: a `kind` of `git`, `file`, or `upload`, plus its locator.
`activate_uploaded_project` reads the record from the org instead of from a project.

The validation the record already receives is unchanged. The digest must match `_SHA256_RE` before
anything turns it into a path or an object-store key. The record reaches the server from a client,
which makes it untrusted.

The write moves with the read. Today an `upload`-kind record reaches the `projects` table one way
only. `bind_upload_config` binds the bundle and *returns* a `source` record. The client then persists
the record by calling `POST /api/projects`, the endpoint unit 5 deletes.

Unit 1 therefore also makes the bind itself the writer. `bind_upload_config`, and its composed-triple
sibling, stamp the record onto the acting org's row rather than handing it back for a client to
register. Without that move, the column would be read and never populated. The recovery path this
unit exists to preserve would die a different death.

One org holds one such record, not a list, and that is a deliberate loss. Today an org holds one row
per named upload, so a member can re-activate an earlier bundle by name after a colleague uploads
theirs. Collapsed to a single column, the second bind overwrites the first locator. Recovering the
earlier bundle means uploading it again.

We accept that loss for three reasons:

- The bundle's bytes stay in the object store under their content hash.
- The case is two members of one org racing on locally built binaries.
- A list of named bundles is the named-project layer returning under another name.

An org that needs to keep several bundles addressable should hold them as artifacts and compose the
one it wants. That is the seam
[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md) already
provides.

This is the mechanism
[BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory.md) proposes for per-org
configuration memory, arrived at from the opposite direction. BE-0393 reaches for the `projects`
table because that table is the durable per-org store that exists. Under this item, the requirement
collapses to a single column, and the named-project layer around it is what goes away.

The two items should land as one design rather than two. The ordering is fixed: the column must exist
before the table can be dropped.

### 2. The run label

The `runs` table gains a nullable `label` column carrying a short free-text string. The label travels
to a run the same way `project_id` travels today. The enqueue resolves it once, the `Job` carries it,
and the run row records it. A remote worker therefore stamps the label without consulting any
registry.

The default value is the string the launcher already computes. `launch_project_identity`
([`bajutsu/serve/operations/config.py`](../../bajutsu/serve/operations/config.py)) derives a
repository name from a Git-materialized configuration's provenance stamp. It derives a file stem from
a local configuration file.

That string partitions a local file cleanly. It names a Git-materialized configuration after its
repository alone, though, so two configurations from one repository fold onto one label. An explicit
project name resolves that collision today.

Unit 2 therefore extends the derivation with the in-repo configuration path, which the provenance
stamp does not carry yet. Until it does, a same-repo restart stays interleaved unless the operator
passes `--label`.

The function stays. What changes is that its result is written onto the run row instead of into a
registry.

An operator overrides the default per run with `bajutsu run --label <value>` on the CLI or a `label`
field in the `POST /api/run` body. The label is opaque to the tool. The tool never parses it, never
matches it against configuration, and never consults it for authorization.

The boundary rejects a value longer than a small bound rather than truncating it. An operator then
learns the label was refused, instead of finding a silently shortened one in the history.

### 3. The target stamp

`RunResult` gains a `target` field, and `manifest_dict` records it. The `runs` table gains a `target`
column mirrored from the manifest, the same way `scenario_hash` and `device_runtime` already are.

No injection path is designed for the target. The value already arrives in the run request, and the
dispatch path already validates it against the org's declared targets before the run starts. The unit
persists a value the dispatch path resolved rather than accepting a new one.

The target stays a column of its own rather than a reserved label value. A label is operator
free-text and untrusted by construction. A target name is declared in configuration and carries
authorization weight: `orgs.<name>.targets` decides which targets an org owns, and the dispatch path
refuses a target belonging to another org. Folding the two into one column would let an untrusted
string occupy a field that authorization treats as authoritative.

### 4. Reading by label, comparing by target

The ordinary run list, Replay, and the run-stats dashboard gain a label filter. Restarting `serve`
against a second configuration then yields two readable histories rather than one interleaved list.

The filter defaults to the label of the bound configuration, which is the behavior an operator
restarting between configurations expects. An explicit "all labels" choice restores the current
unfiltered view.

The default falls back to unfiltered when the bound label matches no run. A deployment whose history
predates the label therefore never opens onto an empty page. The fallback keeps unit 5's backfill from
hiding a migrated history behind a filter the reader cannot see.

The cross-project comparison becomes a cross-target comparison. `project_comparison.py` already runs
the single-configuration aggregation once per partition and lays the results out side by side. The
change repoints its partition key from `project_id` to `target`, and its labels from project names to
target names. The aggregation itself is reused unchanged.
[BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard.md)
factored it out of the run-stats dashboard.

### 5. Removing the project layer

With the preceding four units landed, the following are deleted:

- The `projects` table and the `runs.project_id` foreign key, together with the `ProjectRecord`
  boundary type and the `Repository` project methods.
- `bajutsu/serve/project_registry.py` in full: the `ProjectRegistry` protocol, `SqlProjectRegistry`,
  `LocalProjectRegistry`, the on-disk JSON store, and the `ServeState.project_registry` field.
- The `/api/projects` endpoints: the list, the register, the deregister, the per-project run trigger,
  the per-project run listing, and the activate that performs the live rebind.
- The `bajutsu project add` / `ls` / `use` / `rm` commands and the `run --project` flag.
- The header project picker and the top-level Projects view (the list, its Add form, and its
  navigation tab) in the `serve` shell.

A migration backfills `runs.label` from each run's project name before dropping the column. A history
recorded under the project layer then keeps the partition it had.

Runs whose `project_id` is already null receive a null label. In practice those are the runs of
hosted orgs other than `default`. Unit 4's empty-match fallback keeps them visible: an org whose every
run is unlabeled has no run matching the bound label, so its views open unfiltered on the whole
history rather than on nothing.

The `bajutsu project` commands and the `run --project` flag are removed rather than deprecated in
place. A deprecation window would mean keeping the registry alive to serve them, which is the code
this item exists to delete.

`run --project X` is not a partition, though. `_resolve_project_config`
([`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py)) turns it into
`run --config <X's source>`, statelessly and without a switch. Its help calls it the headless trigger
a continuous-integration or cron step invokes.

Its replacement is therefore an explicit `run --config <the project's source spec>`, and those call
sites must be migrated to it. `run --label` replaces only the partition, for a call site that wanted
one.

## Alternatives considered

- **Keep the project layer and fix its three gaps.** The three fixes would be: auto-register per org rather
  than only for `default`, persist the active project in a column, and add a way to create a project from
  the web UI. Rejected: the gaps are symptoms of a layer nobody reaches for. Fixing them delivers
  multi-configuration switching within one org, which the deployments this tool serves do not want.
  The run-history partition is the one part that is wanted, and it costs a single column without any
  of that machinery.
- **Delete the project layer and keep no partition at all.** Rejected: it would leave two
  configurations' runs interleaved after a restart, which is the concrete complaint that motivated
  this item. The partition is cheap enough that dropping it to simplify further trades the outcome
  for the mechanism.
- **Make the target a reserved label value, so one column serves both.** Rejected on the grounds
  given in unit 3. A label is untrusted operator free-text, and a target is a configuration-declared
  name that authorization consults. One column would let a client-supplied string stand where an
  authorization-bearing value is read.
- **Keep `runs.project_id` and rename it to `label`, without dropping the `projects` table.**
  Rejected: the foreign key is what forces a label to name a registered project, and the value of a
  free-text label is that it needs no registration. Keeping the table to hold rows that exist only to
  satisfy the foreign key preserves the maintenance cost and discards the benefit.
- **Fold this into BE-0393.** BE-0393's per-org configuration memory is unit 1 of this item. The two
  overlap on exactly one unit, and the rest of this item is outside BE-0393's scope: the label, the
  target stamp, the reading surfaces, and the removal. How the two are filed is a decision for
  BE-0393's author. The technical ordering holds either way, since the org column must exist before
  the project table is dropped.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1 — The org holds one configuration source: an org-row column carrying the discriminated
  `kind` + locator record, `bind_upload_config` and its composed-triple sibling writing it there
  instead of returning it for a client to register, and the recovery path reading it from the org.
- [x] 2 — The run label: the `runs.label` column, the launcher's default extended with the in-repo
  configuration path so two configurations from one repository do not fold, the enqueue-time
  resolution carried on the `Job`, and the `run --label` / API overrides.
- [x] 3 — The target stamp: the manifest key and the `runs.target` column.
- [x] 4 — Reading by label, comparing by target: the label filter on the run list, Replay, and the
  run-stats dashboard, defaulting to the bound label and falling back to unfiltered when it matches
  no run; the comparison repointed to the target axis.
- [x] 5 — Removing the project layer: the table, the foreign key, the registry module, the
  endpoints, the CLI commands (migrating `run --project` call sites to an explicit `run --config`),
  and the UI surfaces.

### Log

- **2026-09-01** — All five units shipped in one change. Three deviations from the design above,
  each narrower than what it replaces:
  - **The target is a `manifest_dict` argument, not a `RunResult` field.** A run resolves exactly
    one target into the `Effective` config it executes against, so the value is a run-level one and
    the runner already holds it where the report is written. Threading a per-scenario field through
    the eight `RunResult` construction sites would have recorded the same value eight times.
  - **The label reaches the manifest as well as the run row.** The design routed it through the
    `Job` alone, which is the only channel a cloud-batch run has; a local `serve` keeps no run table
    at all, so its history is read from manifests and would have had no label to filter on. The
    spawned `run --label` writes it into the manifest too, and `_persist_run` prefers the
    enqueue-time value.
  - **No backfill migration.** The design carried each run's project name into `runs.label` before
    dropping the column. Nothing was using the project layer, so migration `0019` adds and drops
    columns and moves no data.

## References

[`bajutsu/serve/project_registry.py`](../../bajutsu/serve/project_registry.py),
[`bajutsu/serve/operations/projects.py`](../../bajutsu/serve/operations/projects.py),
[`bajutsu/serve/operations/config.py`](../../bajutsu/serve/operations/config.py),
[`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py),
[`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py),
[`bajutsu/report/manifest.py`](../../bajutsu/report/manifest.py);
[architecture](../../docs/architecture.md), [configuration](../../docs/configuration.md),
[reporting](../../docs/reporting.md), [glossary](../../docs/glossary.md);
[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) (the project hub this item
removes), [BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard.md)
(the comparison whose axis this item repoints),
[BE-0275](../BE-0275-serve-projects-management-page/BE-0275-serve-projects-management-page.md) (the
top-level Projects view, with the Add form, that unit 5 removes),
[BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory.md) (per-org configuration
memory, whose mechanism is unit 1),
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the hosted schema the
`projects` table came from),
[BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md) and
[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md) (the
uploaded-configuration recovery unit 1 re-homes).
