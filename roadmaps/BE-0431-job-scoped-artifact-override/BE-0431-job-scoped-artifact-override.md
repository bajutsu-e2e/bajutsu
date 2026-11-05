**English** · [日本語](BE-0431-job-scoped-artifact-override-ja.md)

# BE-0431 — Job-scoped artifact overrides, independent of the active config binding

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0431](BE-0431-job-scoped-artifact-override.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0431") |
| Implementing PR | [#2034](https://github.com/bajutsu-e2e/bajutsu/pull/2034) |
| Topic | Configuration sourcing |
| Related | [BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory.md), [BE-0413](../BE-0413-worker-app-binary-delivery/BE-0413-worker-app-binary-delivery.md), [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md), [BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads.md), [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md) |
<!-- /BE-METADATA -->

## Introduction

A hosted `bajutsu serve` runs each job against two inputs:

- The `appPath` of the [target](../../docs/glossary.md#target-app-device). This is the prebuilt app
  binary that the config names.
- The scenarios in the directory that the same config names.

Today a job gets either input from outside what the org's serve already holds in exactly one way:
rebinding the org's **active config**. A rebind is not a per-job choice. It has two effects:

- For a caller with no session, such as a shared-token or CI request, it replaces the **deployment's
  fallback** binding. This is the one record that every sessionless caller reads next.
- It also writes the org's **remembered configuration**
  ([BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory.md)). A colleague's next
  session inherits that configuration on first use.

[BE-0413](../BE-0413-worker-app-binary-delivery/BE-0413-worker-app-binary-delivery.md) then ships the
bound tree's binary to whichever worker leases the job.

This item adds **per-job artifact overrides**. A request to `run` names an already-stored `binary`
artifact, a `scenarios` artifact, or both. Each is named by its sha256. Every job that the request
dispatches resolves against the named artifacts alone.

A leg that the request does not name keeps resolving through the org's active config binding, exactly
as it does today. The binding itself stays untouched, and so does every other job or session that
runs against it.

## Motivation

Continuous integration (CI) triggers a run for a change under review. A GitHub pull request commonly
changes two things at once: the application code and the scenarios that exercise it. The run that CI
wants is "this build's binary, against this branch's scenarios".

Today's paths for this run are
[`POST /api/compose`](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md)
and the legacy single-zip `POST /api/upload`. Both rebind the org's active config before every
dispatch. That rebind surfaces two distinct problems.

**First problem: contention on the fallback binding.** Every CI request is sessionless, because it
carries no login cookie. Every CI bind therefore replaces the same **deployment fallback** binding
([BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory.md)). Two concurrent CI
dispatches against one deployment contend for that one record.

`_register_and_dispatch` freezes a job's `cwd` and `bundle` at registration. The loss lands in the
window between a caller's bind and its own `run`. The other caller's bind arrives in between, and the
run resolves against that bind instead. Neither caller meant to touch the other's job.

**Second problem: a CI bind leaks beyond the CI caller.** The same rebind also writes the org's
**remembered configuration** (BE-0393 unit 6). A colleague's *next* session inherits that
configuration on first use. A build meant for one CI run can therefore become the binary that a human
web-UI session opens against, with no bind of that member's own in between.

A third gap sits underneath both.
[BE-0413](../BE-0413-worker-app-binary-delivery/BE-0413-worker-app-binary-delivery.md) ships a binary
to a remote worker in one case alone. The case applies when the leased job's binding is an
[`Upload`](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md).
`_register_and_dispatch` is the single tail that every `start_*` funnels through, and it makes the
`binding.upload is not None` check before it stamps `Job.bundle`.

Two kinds of job carry no `bundle` at all:

- A job dispatched off a Git-sourced config.
- A job off a local config whose scenario travels as server-stored `materials`.

The remote worker of such a job writes the config and scenario text into a fresh workspace
(`_materialize` in `bajutsu/serve/server/worker_job.py`). It then has no way to place a binary there.
The app must already sit on that worker's disk, or the config's `build:` command must fetch it. A
`build:` command is a shell command, ungoverned outside the sandboxing that an uploaded bundle's server
commands already get
([BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md)).
A binary that the control plane already holds as a content-addressed artifact has no deterministic,
credential-free path onto that worker today.

All three gaps trace to one design choice. Delivery is entangled with *which config is bound*, while
the unit that a caller actually wants to vary is the job. Untangling them lets a CI run and a human
web UI session share one org without stepping on each other. It also lets a materials-based job
receive a binary the same way an uploaded bundle's job already does.

**Verifiable outcome.** Dispatch two sessionless `run` jobs through the API at the same time against
one deployment. Each job names its own `binary` artifact sha256 and its own `scenarios` artifact
sha256, both uploaded moments earlier. Both jobs finish, and each has run its own scenarios against
its own binary. Each run's manifest records the two sha256 values it resolved against. The
deployment's fallback binding is unchanged: a sessionless `GET /api/config` reports the same value
before and after both jobs return.

This fails today. With no per-job path, each caller would first have to rebind that one shared
fallback. The second bind then changes what the first caller's job resolves against, instead of
leaving it alone.

## Detailed design

The work is mutually exclusive and collectively exhaustive (MECE) across three units:

- naming standalone artifacts per job
- delivering them to wherever the job runs
- the tests that pin the seam down

### Unit 1 — Per-job artifact references that never touch the active binding

[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md) already lets a
`binary` artifact and a `scenarios` artifact each be uploaded and stored on its own. The sha256 of each
artifact addresses its content, and `bind_artifact` writes the artifact without binding it as anything's
active config. This item adds per-job references to that store.

`start_run` accepts two optional fields, `binaryArtifact` and `scenariosArtifact`. Each holds a sha256
hex digest that names an artifact of that kind already stored for the caller's org. `valid_sha256`
validates the shape of the digest.

#### Which legs a request names

A request may name either field, both, or neither. **An unnamed leg is not a gap to fill.** The job
resolves it through the org's active config binding, byte for byte as it does today. A request that
names nothing behaves exactly as the same request did before this item landed.

That fallback keeps the two fields independent. CI that rebuilds the app but not the scenarios names
`binaryArtifact` alone. The pull request that adds a scenario names both.

#### Resolving the scenario from the override

Naming a `scenariosArtifact` changes where the requested scenario is **resolved from**. The binary leg
has no counterpart to this change.

Today `start_run` resolves the request's `scenario` against the bound source before any job exists.
`scope.runnable(...)` globs the bound tree's scenarios directory. On a hosted deployment, it reads the
text out of the org's scenario store and ships it as `materials`. A scenario that lives only in the
override would fail that lookup with the existing 400. The pull request that adds a scenario is
exactly that case, so without this sub-unit the leg closes nothing.

When the request names a `scenariosArtifact`, the scope resolves against **the override's own entry
listing** instead. Serve can read that listing without a worker, because `bind_artifact` already wrote
the artifact into serve's content-addressed cache (`local_artifact_dir`).

The confinement that makes the lookup safe is unchanged. The client string is reduced to a basename
and checked by `valid_scenario_ref` against a trusted listing. No client-controlled value becomes a
path (BE-0051).

Two consequences follow. Both are design, not detail.

- The resulting `Runnable` carries **no** `materials` for the scenario. The worker receives the whole
  artifact through the lease and places it, instead of receiving one scenario's text inline.
  `start_run` today derives `on_worker` from `bool(materials)`. The flag must come from the job's
  topology instead: a job with an override is workspace-relative even though it ships no materials.
- This item **requires the `scenarios` override to be a zip**. It refuses a single YAML file with a
  clear error. A zip's entries carry their own names, and the request's `scenario` is matched against
  those names. A single-file artifact carries no name at dispatch. The request fields hold a digest
  and nothing else. Compose's own single-file path works only because `POST /api/compose` takes a
  separate filename alongside the bytes.

#### The role that gates the fields

The two fields stay at the *editor* role that `POST /api/run` already requires. They do not rise to the
*admin* role that guards the binding and artifact-upload endpoints.

Those endpoints sit at admin for a reason other than choosing a binary. They **repoint what future runs
serve**, for every caller in the org. A per-job override chooses for one job and writes no shared
record, which is the property this whole item is built on. Gating the fields at admin would charge a
per-job choice the price of a shared-state change. It would also put the override out of reach of the
CI caller that the Motivation is about.

#### The dispatch-side existence gate

The dispatch-side gate cannot reuse `artifact_exists` as it stands. That helper deliberately reads a
store error as "not confirmed present". It then returns the same `200 {"exists": false}` that a real
miss returns. A gate built on it unchanged would read a transient error as a confirmed miss and return
exactly the 400 that this item forbids.

This item extracts the three-state probe (present, confirmed absent, unconfirmed) behind
`artifact_exists`. `artifact_exists` remains the thin `GET /api/artifacts/exists` handler, and it
narrows the probe back to today's two states.

The dispatch gate reads the three-state result for each named leg:

- A confirmed absence fails the request with 400 before any job is registered. A job never fails
  later and opaquely, once a worker tries to fetch the artifact.
- An unconfirmed check returns a retryable 503. It never returns a 400 that asserts the artifact was
  never uploaded.

`GET /api/artifacts/exists` itself keeps its current two-state contract. It returns
`200 {"exists": false}` for both a confirmed miss and an unconfirmed check. Its existing dedup callers
are safe reading either as "upload it again", so no client behavior changes there.

#### Independence from the active binding

The references travel as new `Job` fields, independent of `Job.bundle`. `Job.bundle` stays exactly
what BE-0413 built: the tree that an `Upload` binding resolves to. Nothing here changes
`state.binding_for`, `bind_upload_config`, or `remember_org_config_source`.

Two jobs that name different artifacts never contend for the same record. Neither does one job that
names an artifact while the org's bound config stays what it was.

#### The storage key as a stable contract

An artifact's storage key is a pure function of four inputs: the deployment's object-store prefix, the
org, the artifact kind, and the sha256. `artifact_store_key` computes it, under the org's
`uploads/<kind>/` prefix in `bajutsu/serve/upload_artifacts.py`. This item treats that key scheme as a
stable contract, not an implementation detail.

Take a CI runner with its own bucket access as an example. It knows the deployment's prefix, its own
org, the kind segment, and the sha256 it computed. All four are needed, not the digest alone. With
them, the runner can write the artifact's bytes directly to that key and skip
`POST /api/artifacts/binary` entirely.

The dispatch API needs the resulting sha256 alone, never the transport that put the bytes there.
`GET /api/artifacts/exists` still answers whether a given sha256 is already stored, whichever path
wrote it. A caller with its own credentials and all four inputs gets the same upload-skip as it would
through the API.

#### A prerequisite this item does not supply

This item does not supply a credential that a CI job can present. Writing the bytes has an answer that
needs no serve credential at all: the direct-to-key write above. Naming the sha256 at dispatch does
not have such an answer.

- `POST /api/run` requires the *editor* role.
- `POST /api/artifacts/binary` requires the *admin* role.
- On a deployment with GitHub OAuth configured, the shared token narrows to worker traffic alone, so a
  raw bearer token reaches neither endpoint (`docs/self-hosting.md`).

Such a deployment has no machine identity to offer a CI job today. This item assumes whatever
credential the deployment already trusts for `POST /api/run`, which is the shared token on a
token-authenticated deployment. It adds two fields to that request.

Giving a CI job an identity of its own on an OAuth deployment is a separate decision about
authentication, with its own threat model. It belongs in its own item and should not ride in on a
delivery mechanism.

### Unit 2 — Deliver the overrides to wherever the job runs

A job reaches one of two dispatch topologies, and an override leg lands differently in each:

| Topology | `binaryArtifact` | `scenariosArtifact` |
|---|---|---|
| `run` on a hosted deployment (BE-0106's `DbQueueExecutor`) | placed at the job's own target `appPath`, inside a job-scoped workspace | placed into that target's scenarios directory, in the same workspace |
| `run` on a single-process `serve` (`LocalExecutor`) | refused | refused |

The hosted worker topology is the one that this item exists to serve. The rest of this unit describes
it before returning to the refusal. The cloud-batch fan-out `run-set` is a deliberate non-goal, for a
reason that this unit gives once the worker path is set out.

#### Signing and fetching

At lease, `worker_lease` signs a presigned GET for each named override, the same way it already signs
`bundle_urls` (BE-0413) and `baseline_urls` (BE-0160). The lease returns the signed urls as two more
keys, `binary_url` and `scenarios_url`.

Each sha256 that the job carries is re-validated as a full hex digest before it becomes a storage key.
That key stays scoped to the leased job's own org and is never a value that the worker supplies.

Suppose a job carries an override and the lease signs no url for it, because no object store is
configured. The worker then fails the job immediately. BE-0413 already takes the same posture when a
bundle's lease signs no url (`bajutsu/serve/cli/worker.py`: "job needs bundle …, but the lease signed
no url for it"). The failure is stated up front rather than surfacing opaquely at install time.

The worker downloads and hashes each artifact before use. It reuses BE-0413's streamed
download-and-verify path.

#### Placing the binary

The binary goes where a composed `binary` leg already goes. The worker resolves `appPath` from the
job's own config. It resolves it for the one target that the job names, not for every target that the
config declares. The reason is that the placement loop of `materialize_composition`
(`_place_scenarios_and_binaries_and_check_coherence`) writes one binary at *every* non-web target's
`appPath`.

The resolution is platform-general. An Android target's `appPath` is covered exactly like an iOS
target's.

The bytes go through the unzip-or-copy branch of `_place_binary`. They go under the same BE-0051 path
confinement that `validate_bundle_config` gives a composed tree. The write overwrites whatever an
`Upload`'s tree, or a Git checkout, would otherwise have placed there. It is also the first delivery
path that a materials-based job (one with no `bundle` at all) has ever had for a binary that it does
not already carry on disk.

#### Placing the scenarios

The scenarios artifact lands in the directory that the target's config names. This item **replaces**
that directory's contents instead of merging into them.

Replacement needs its own step. A reader may expect it to come for free, but it does not.
`extract_bundle` writes a zip's entries into a destination and deletes nothing.
`materialize_composition` ends up with a replaced tree only because it extracts into a freshly created
empty directory. On the worker, the destination already holds whatever the bound tree put there. The
worker therefore removes the target's scenarios directory and recreates it empty before extracting. It
does so under the same BE-0051 confinement that every other placement gets.

Replacement is what makes the run match the branch that asked for it. A merge would leave a scenario
that the branch deleted still sitting in the directory. A fan-out over every scenario in the target's
directory would then run it, and the run would correspond to no commit.

The deletion is safe to state this bluntly. The directory being emptied is inside the job's own
workspace, never the bound tree. The workspace key below gives an overridden job a tree of its own.

#### The XCUITest runner

The app under test is covered. On iOS, however, the app is not the only product that a run installs.
XCUITest also needs a runner. The override does not carry one, and for the common case it does not
need to.

- The runner is app-agnostic. One built `.xctestrun` and its products drive whatever app a run targets
  ([BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)). The runner does not change when
  a build changes.
- A Simulator target that names no `xcuitest.testRunner` resolves to the runner shipped inside the
  Bajutsu wheel
  ([BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md)). A worker that
  installed the wheel already holds it, so nothing about the runner travels per job.
- A target that does pin `xcuitest.testRunner` has that path rebased against its own config's
  directory, confined by BE-0051. This is how an uploaded bundle carries its own runner inside the tree
  that BE-0413 already delivers.

One case stays open, and this item does not close it. A real-device target (`xcuitest.deviceType:
device`) must pin a signed runner that Bajutsu cannot ship
([BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build.md)). A
materials-based job against a real device therefore still has no delivery path for that runner. This is
the same gap that this item closes for the app binary.

The runner belongs to the deployment rather than to the build, so a per-job override is the wrong shape
for it. Delivering a signed runner is its own problem, left to an item that can size it.

#### The workspace key

The worker's workspace outlives the job. `work` is one directory for the worker's whole lifetime.
Placing an override while leaving today's workspace key unchanged would let the override contaminate
the next job leased on that worker. A job with no override, or one off the same bundle, would start
against the leftover with nothing announcing it. The overrides therefore participate in the workspace
key itself:

| Job | Workspace |
|---|---|
| bundle job, no override | `.bundles/<org>/<bundle id>` — exactly today's key, unchanged |
| bundle job, one or both overrides | keyed by `(bundle id, override identity)` — its own tree |
| materials-based job, one or both overrides | a directory keyed by `(materials identity, override identity)`, instead of the worker's shared working directory |
| materials-based job, no override | the worker's shared working directory — exactly today, unchanged |

The **override identity** in that key is a digest over the named legs. It is built the way
`_composition_id` already builds a compose cache key: the shas are joined in a fixed kind order, and
an absent leg joins as an empty segment. No real 64-character digest can collide with that empty
segment.

The **materials identity** beside it is a digest over what a materials-based job already carries in its
own spec: its config text and its scenario text. It keeps two such jobs apart when their configs name
different `appPath`s.

Keying the job's tree this way must not cost the bundle a second download, and stated carelessly it
would. `_bundle_workspace` keys the bundle tree by bundle id today, precisely "so a second job off the
same bundle reuses it and fetches nothing". Folding the override into *that* key would make every new
build a fresh key. The result is a full re-fetch and re-extract of the bundle zip, on the exact
workload that this item exists to speed up.

The two keys therefore stay separate. The bundle cache keeps its bundle-id key and is still fetched
once. The job's tree is a per-override directory built **from** that cached bundle, so an override
costs a local copy rather than a network round trip. The disk cost that it inherits is BE-0413's.
`docs/self-hosting.md` already tells an operator to manage that cost by pruning the bundle cache.

A job that names no override keeps today's key exactly, so nothing about existing behavior changes.
Only a job that carries an override gets a tree of its own. That makes the placement job-scoped by
construction. It also lets the scenarios step above empty a directory without ever touching the bound
tree. The design follows BE-0413's own principle that an artifact's identity is a digest of its
contents, so two different binaries are two different trees.

#### The single-process topology

A local, single-process `serve` is a different topology. It runs the job itself, in the operator's own
project directory. `_register_and_dispatch` freezes `job.cwd` from the binding in every topology, so the
discriminator is not the binding kind. The discriminator is the executor: `LocalExecutor`, versus
BE-0106's `DbQueueExecutor` on a hosted deployment.

This item **refuses** both override fields on a `LocalExecutor` deployment with a clear error. The
alternative would overwrite the operator's build output at `appPath` or the scenarios that the operator
is editing. Serve owns no workspace to isolate the placement into. The contamination that the workspace
key solves on the worker therefore has no local answer. Mutating an operator's project directory
unannounced is the side effect that prime directive 2 rules out.

An operator on a single-process `serve` already has direct filesystem access. The operator can point
`appPath` wherever they want without these fields. The refusal keeps the item scoped to the topology
whose problem it exists to solve: the hosted split, where a worker runs the job and serve owns the
workspace.

#### The cloud-batch fan-out

The cloud-batch fan-out `run-set` carries neither override. The reason is not that the legs are hard to
deliver there. **On the split topology that this item targets, `run-set` does not run at all today.**

- The Device Farm package is built from `state.devicefarm_package_root`, which `serve()` sets at
  startup.
- The worker builds its own `ServeState` without that field. A cloud-batch job leased from the
  database queue therefore falls back to an ephemeral workspace holding only a config and a scenario.
- Device Farm's `APPIUM_PYTHON_TEST_PACKAGE` validation then rejects the job. The package root must
  carry Bajutsu's own `tests/` and `pyproject.toml`.
- The same field is `None` on a Bajutsu installed from a wheel rather than a checkout.

`bajutsu/serve/jobs.py` states this limitation in the batch dispatcher's own docstring.

Designing an override branch onto a path that does not execute would be designing against nothing. The
same docstring already names the two prerequisites that the path is missing:

- the package root wired into the worker's state
- **a host-portable app artifact path**

This item supplies the second. A `binary` artifact is addressed by sha256, fetched through a signed
url, and verified on the worker. It is exactly the host-portable path that a worker-side `run-set`
would need, and it exists here whether or not `run-set` ever uses it. Extending the overrides to
`run-set` belongs to the item that wires the package root. That item can build on this one instead of
duplicating it.

#### Failure and provenance

A 404 or 410 on a fetch ends the job the way BE-0413 treats a bundle that is not there (`bundle
unavailable`). A hash mismatch is different. BE-0413 classifies it as transient, so the lease lapses and
another attempt can succeed. One truncated download does not end the job for good.

Either way, a run never starts against the wrong binary or the wrong scenarios. Nor does it fall back,
unannounced, to whatever the binding would otherwise have resolved to.

The run's manifest records each overridden sha256 as provenance, alongside BE-0073's existing bundle
provenance. Which artifacts a job actually installed stays answerable after the fact.

### Unit 3 — Tests and documentation

The gate covers each seam without a network or a Simulator:

- `start_run` refuses a `binaryArtifact` or a `scenariosArtifact` the org's artifact store does
  not hold, before registering a job.
- A transient object-store error during the dispatch gate's existence check returns 503 from that
  gate, not a 400 claiming the artifact was never uploaded.
- A request naming neither field dispatches exactly as it does today, resolving both legs through the
  org's binding.
- A request naming `binaryArtifact` alone keeps resolving its scenarios through the binding, and one
  naming `scenariosArtifact` alone keeps resolving its `appPath` through the binding.
- A request naming a `scenariosArtifact` runs a scenario that exists **only** in that artifact and
  not in the org's scenario store or the bound tree — the pull-request case, which returns 400 today.
- That same job ships no scenario `materials`, and still runs workspace-relative, so the flag driving
  workspace-relative paths does not regress to `bool(materials)`.
- A scenario name absent from the override's listing is refused with the existing confinement error,
  and a name carrying a path separator is reduced to its basename before the lookup.
- A single-YAML `scenarios` override is refused with a clear error rather than landing under a
  generated name the request's `scenario` could never match.
- A job carrying either override field is accepted at the *editor* role, the same role
  `POST /api/run` already requires.
- `worker_lease` signs `binary_url` and `scenarios_url` for a job carrying the matching override,
  scoped to the leased job's org.
- A job carrying an override whose lease signs no url for it fails immediately, the same way
  a bundle job with no signed url does.
- A materials-based job with no `bundle` places the fetched artifact at its config's `appPath` and runs.
- An Android target's override lands at its own `appPath`, the same seam an iOS target uses.
- An override on a config with iOS and Android targets lands at the job's own target's `appPath`
  alone, leaving the other's untouched.
- A `.app` zip-bundle artifact extracts into a directory at `appPath` rather than landing as a zip
  file.
- A zip `scenarios` override replaces the target's scenarios directory, so a scenario present in the
  bound tree but absent from the override does not run — the case a plain extraction would miss,
  since it deletes nothing.
- A bundle job carrying an override reuses the cached bundle rather than re-fetching its zip, so the
  per-override tree costs no second download.
- A job with both a bound `Upload` and the two overrides installs the overrides, not the bound tree's
  own binary and scenarios.
- Two concurrent jobs naming different artifacts each install their own, and neither changes the org's
  active config binding.
- Two materials-based jobs naming the same binary override, whose configs name different `appPath`s,
  get separate workspaces.
- Two jobs sharing a binary override but naming different scenarios overrides get separate workspaces,
  since the override identity covers both legs.
- A job with no override that leases after one that had it, on the same worker, runs against
  its own binding's binary and scenarios rather than the leftover — a sequential-on-one-worker failure
  the "two concurrent jobs" bullet above does not cover.
- A 404 on an override fetch fails the job, leaving nothing installed behind; a hash mismatch
  instead leaves the lease to lapse, so a retry can succeed instead of the download ending the job
  for good.
- A job dispatched on a `LocalExecutor` deployment gets either override field refused, leaving the
  operator's `appPath` binary and scenarios directory untouched.
- A `run-set` request carrying either override field is refused, so the non-goal is enforced rather
  than left to a reader of the prose.

`docs/self-hosting.md`, `docs/cli.md`, and their Japanese mirrors gain a paragraph on the two
override fields and what a job's manifest records for them.

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| Name an artifact by a raw object-storage path (`prefix/org/<path>`), trusted by location alone | Drops content-addressing: the object at that path can change after the fact, so a run's manifest can no longer say which bytes it installed. It also reopens the path-validation surface BE-0413's sha-revalidation and BE-0051's confinement already close, for no capability a sha256 reference does not already give. |
| Override the `binary` leg alone, leaving scenarios to a rebind | Closes only half of the motivating case. A pull request that changes code commonly adds or edits a scenario in the same commit, so its CI run would still have to rebind the org's active config for the scenarios — reinstating both contention on the deployment fallback binding and the write into the org's remembered configuration that this item exists to remove. |
| Extend the per-job override to the `config` leg as well | The config decides which targets exist, what each target's `appPath` is, and where its scenarios live, so overriding it changes the very inputs the other two legs and both topologies resolve against. A job's named target might not exist in the overridden config at all. That needs an answer this item's motivation does not yet demand. The two fields here are optional and independent, so a `configArtifact` stays a purely additive change for a later item. |
| Accept a single-YAML `scenarios` override, as `POST /api/compose` does | Compose can take one YAML file because the same request carries its filename alongside the bytes; the dispatch fields carry a digest and nothing else. A single-file override would therefore have to land under a generated name, which the request's own `scenario` value could never match — so the job would resolve to a scenario that is not the one the caller uploaded. Requiring a zip keeps the entry names inside the artifact, where the resolution needs them. |
| Gate the two override fields at the *admin* role, matching the artifact and binding endpoints | Those endpoints sit at admin because they repoint what *future* runs serve for the whole org, not because they choose a binary. A per-job override chooses for one job and writes no shared record. Charging it the price of a shared-state change would also put it out of reach of the CI caller this item is written for, which authenticates against `POST /api/run`. |
| Fold the override identity into the bundle cache key | The bundle tree is keyed by bundle id today so a second job off the same bundle fetches nothing. Folding the override in makes every new build a distinct key, forcing a full re-download and re-extract of the bundle zip on precisely the CI workload this item is meant to serve. Keeping the two keys separate buys the same isolation for a local copy. |
| Merge a `scenarios` override into the bound tree's directory instead of replacing it | A merge keeps whatever the bound tree held, so a scenario the branch deleted would still be present, and a fan-out over the target's whole directory would run it. The run would then correspond to no commit, which is the opposite of what a CI verdict needs. |
| Extend the overrides to the `run-set` cloud-batch fan-out | `run-set` does not execute on the split serve-and-worker topology this item targets: the worker's `ServeState` carries no `devicefarm_package_root`, so Device Farm's package validation rejects the job before any override matters. Building a branch onto a path that does not run would be designing against nothing. The item that wires that package root can extend the overrides, using the host-portable artifact path this item already provides. |
| Require a rebind (`bind`/`compose`) before every dispatch, as today's two paths do | Makes the org's active config the unit of change: every sessionless CI caller contends for the one deployment fallback binding instead of each getting the artifacts its own job asked for, and the bind additionally writes the org's remembered configuration, which a member's next session inherits. |
| Add a presigned-PUT upload endpoint for artifacts, as the one supported transport | Not needed to close the motivating gap: `POST /api/artifacts/binary` plus `GET /api/artifacts/exists` already let a caller dedupe and upload today. A presigned-PUT variant would save a round trip through the control plane's own disk for a large binary, but that is a follow-on optimization, not a blocker — this item's dispatch-time contract is the resulting sha256, not how it arrived. |
| Re-place `appPath` from the bundle/Git tree (or delete the leftover) for every job that carries no override | A materials-based job has no source tree to re-place *from*, so that path needs a delete step and a per-topology branch, where keying the workspace makes the isolation structural and needs neither. |
| Carry the XCUITest runner as a third override leg | The runner is app-agnostic and does not change when a build changes ([BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)), so it belongs to the deployment, not to the job: a Simulator target already resolves the wheel-bundled runner ([BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md)), and an uploaded bundle carries a pinned `xcuitest.testRunner` inside its own tree. What is genuinely missing — a signed runner for a real-device, materials-based job ([BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build.md)) — is a per-deployment delivery problem a per-job override would not solve. |
| Offer the overrides on `record` and `crawl` too | The Motivation is entirely CI dispatch toward a verdict, which `run` serves, and argues nothing for either Tier-1 authoring path: `record` explores toward a natural-language goal with AI and writes a scenario, and `crawl` explores breadth-first and writes a screen map that `docs/cli.md` states is never a pass/fail gate. `record`'s output also outlives its job — the authored scenario is persisted to the org's scenario store (`Job.record_save`, `out_path`) — so a durable artifact authored against a transient per-job binary would carry no record of which binary shaped it, where a `run`'s manifest stamps the overridden sha256. A later item can add these paths with its own argument. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — `binaryArtifact` and `scenariosArtifact` request fields, each optional and
      existence-checked. Both travel on `Job` independent of `Job.bundle`. An unnamed leg still
      resolves through the org's binding. A named `scenariosArtifact` moves the request's `scenario`
      lookup onto the override's entry listing, ships no scenario materials, and requires a zip.
- [x] Unit 2 — Sign and deliver each named override on the worker topology, clearing the target's
      scenarios directory before extracting, and keying the job's tree separately from the bundle
      cache. Refuse both fields on a `LocalExecutor` deployment and on `run-set`, with provenance
      recorded on the run's manifest.
- [x] Unit 3 — Tests for each seam, plus the `self-hosting` / `cli` documentation.

Log:

- [#2034](https://github.com/bajutsu-e2e/bajutsu/pull/2034) — Units 1–3 in one change. `start_run` takes
  `binaryArtifact` / `scenariosArtifact`, gated by a three-state `artifact_presence` probe (400 on a
  confirmed miss, 503 when the store cannot answer). The lease signs `binary_url` / `scenarios_url`.
  The worker builds an override-keyed tree and places the overrides with `place_overrides`. Three
  deviations from the text above, each forced by today's code:
  - `job_spec` never carried `provenance`, so a worker-run job stamped none into its manifest. The
    spec now carries it, which also fixes that gap for bundle jobs (BE-0073).
  - The scenario lookup reads the artifact through `_fetch_artifact` (local cache, then the object
    store), since a replica other than the uploading one holds no local copy.
  - The worker's tree key also covers the target, because the binary lands at that target's
    `appPath` alone and two targets of one config would otherwise share a tree.

## References

- [BE-0393 — Per-org config memory, restored into each session](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory.md)
  — defines which binding a bind actually moves: the session's own slot, or the deployment's fallback
  when the caller has none.
- [BE-0413 — Deliver an uploaded app binary to the worker that runs the job](../BE-0413-worker-app-binary-delivery/BE-0413-worker-app-binary-delivery.md)
  — the presigned-GET delivery and download-verify path this item reuses for a standalone artifact.
- [BE-0073 — Upload a config + scenarios + app-binary bundle as a zip and run it from the web UI](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)
  — the run-manifest provenance block this item's overridden sha256 values join.
- [BE-0268 — Upload config, scenarios, and app binary as independent content-addressed artifacts](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md)
  — the standalone, content-addressed `binary` and `scenarios` artifacts this item references without
  binding them.
- [BE-0325 — Reuse the active composition when uploading only the legs that changed](../BE-0325-compose-incremental-artifact-upload/BE-0325-compose-incremental-artifact-upload.md)
  — the compose-time convenience this item's per-job override is a non-mutating alternative to.
- [BE-0160 — Credential-free worker uploads via presigned URLs](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads.md)
  — the presigned-URL brokering `binary_url` and `scenarios_url` extend.
- [BE-0090 — Govern and sandbox command execution from uploaded bundle configs](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution.md)
  — the `build:` governance a materials-based job's own binary fetch relies on today, absent this item.
- [BE-0106 — Post-completion worker model](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)
  — the lease protocol the two override urls join alongside `bundle_urls` and `baseline_urls`.
- [BE-0292 — Bundle the XCUITest runner so testRunner is optional](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md)
  — why a Simulator run's runner needs no delivery, so this item's overrides cover the app and its
  scenarios alone.
- [BE-0336 — serve-driven Device Farm dispatch with bounded per-scenario fan-out](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md)
  — the cloud-batch fan-out this item names as a non-goal, because its package root is never wired
  into a worker's state and the fan-out therefore does not run on the split topology at all.
