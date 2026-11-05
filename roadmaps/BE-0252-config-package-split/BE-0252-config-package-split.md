**English** · [日本語](BE-0252-config-package-split-ja.md)

# BE-0252 — Split config into a package and group Effective into sub-records

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0252](BE-0252-config-package-split.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0252") |
| Implementing PR | [#1096](https://github.com/bajutsu-e2e/bajutsu/pull/1096) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

[`bajutsu/config.py`](../../bajutsu/config.py) is 882 lines long and mixes four distinct jobs in
one module:

- **The pydantic input schema** (lines 59–452): `MockServer`, `LaunchServer`, `Mailbox`,
  `XcuitestConfig`, `PricingEntry`, `AiSettings`, `DoctorConfig`, `NotifyEndpoint`, `Defaults`,
  `TargetConfig`, and `Config`.
- **The resolved output types** (lines 26–49 and 455–637): `AiConfig`, `IosConfig`, `WebConfig`,
  `AndroidConfig`, and `Effective`.
- **The resolution and merge logic** that turns the schema into the output types (lines 709–883):
  `_merge_redact`, `_merge_ai`, `_platform_for_backend`, `_effective_platform`,
  `_platform_config`, `resolve`, `parse_config_dict`, and `load_config`.
- **Path rebasing and the narrowing accessors.** `Effective.rebased` (lines 580–636) rebases paths.
  `require_ios`, `require_web`, `require_android`, and the soft accessors (lines 639–707) narrow
  the platform.

This item splits `config.py` into a `bajutsu/config/` package along those four seams. In the same
pass, it groups the roughly 30 fields of `Effective` into a handful of cohesive sub-records. Both
moves preserve behavior. No call site outside the package changes.

## Motivation

A single 882-line module does schema validation, output typing, merge and derivation, and path
rebasing. It is hard to navigate. A reader looking for how `doctor_ok_coverage` is resolved must
skim past pydantic validators for unrelated fields to find `resolve()`.

The four jobs also have a one-directional dependency. Resolution reads the schema and produces the
output types. Nothing in the schema or the output types depends on resolution. Earlier module
splits in this codebase, such as
[BE-0206](../BE-0206-serve-state-module-split/BE-0206-serve-state-module-split.md), used exactly
this shape to justify separating a file along its consumer boundaries.

`Effective` (lines 512–569) compounds the problem. It is a god object with roughly 30 fields, and
the fields span nine groups:

- identity: `target`, `platform_config`, `backend`, `device`, `locale`
- launch: `launch_env`, `launch_args`, `setup`, `launch_server`, `ready_when`
- evidence directories: `scenarios`, `baselines`, `schemas`, `goldens`
- AI: `ai`
- doctor thresholds: `doctor_ok_coverage`, `doctor_fail_coverage`
- run defaults: `dismiss_alerts`, `erase`, `network`
- notification: `notify`
- routing: `requires`
- secrets, mailbox, and mock server: `redact`, `secrets`, `mailbox`, `mock_server`

Every consumer of `Effective` receives all 30 fields, whichever subset it reads. `resolve()` (lines
821–859) is a 38-line flat constructor call that assembles the fields one by one.

`Effective.platform_config` is already a discriminated `PlatformConfig` union (line 509). A caller
must narrow it with `isinstance` or `require_*` before reading a platform's own knobs. That pattern
is worth preserving and extending to the other field clusters. This design deliberately avoids the
alternative, the stringly `cfg["targets"][name]["..."]` access.

Two functions also defer their `bajutsu.backends` import to the function body today. `_check_platform`
(line 177) and `_platform_for_backend` (line 752) each do `from bajutsu.backends import …` inside
the body, with no comment recording why. `bajutsu/backends.py` does not import `bajutsu.config`
anywhere. The deferral is therefore a one-way deferred import, not the breaking of a `config ↔
backends` cycle.

Resolution is the only half of the module that needs `bajutsu.backends`. Moving it into its own
module turns these two deferred imports into ordinary top-level imports, because that module becomes
the only submodule that depends on `bajutsu.backends`.

## Detailed design

The split follows the four responsibilities identified above. It is MECE by construction: schema
input parsing, resolved output shape, merge-and-derive logic, and path-rebase-and-accessors. Every
symbol in today's `config.py` falls into exactly one of them.

1. **`bajutsu/config/schema.py`** holds the pydantic input models and their field validators
   (today's lines 51–452). The models are `MockServer`, `LaunchServer`, `Mailbox`,
   `XcuitestConfig`, `PricingEntry`, `AiSettings`, `DoctorConfig`, `NotifyEndpoint`, `Defaults`,
   `TargetConfig`, and `Config`. The module also holds `_check_platform` (line 170), because that
   function validates a schema field. It has no dependency on `bajutsu.backends` beyond what is
   already deferred inline.

2. **`bajutsu/config/effective.py`** holds the resolved, frozen dataclasses: `AiConfig`,
   `IosConfig`, `WebConfig`, `AndroidConfig`, the `PlatformConfig` union, and `Effective` itself,
   including its `platform` property and `rebased` method (today's lines 26–49 and 455–637).
   `Effective`'s roughly 30 fields are grouped into cohesive frozen sub-records, the same way
   `platform_config` already narrows the platform axis:
   - `EvidenceDirs` holds `scenarios`, `baselines`, `schemas`, and `goldens`.
   - `RunDefaults` holds `dismiss_alerts`, `erase`, and `network`.
   - `DoctorThresholds` holds `doctor_ok_coverage` and `doctor_fail_coverage`.

   `Effective` keeps its remaining fields at the top level:
   - `target`, `platform_config`, `backend`, `device`, `locale`
   - `launch_env`, `launch_args`, `id_namespaces`, `reserved_namespaces`
   - `mock_server`, `setup`, `capture`, `redact`, `secrets`
   - `ai`, `mailbox`, `launch_server`, `ready_when`, `notify`, `visual_compare`, `requires`

   It adds three sub-record fields: `evidence_dirs`, `run_defaults`, and `doctor_thresholds`. The
   flat field count drops by roughly half. `rebased` rebuilds `evidence_dirs` via `replace` instead
   of four individual `at(...)` calls.

3. **`bajutsu/config/resolve.py`** holds the merge and derivation logic (today's lines 709–883):
   `_merge_redact`, `_merge_ai`, `_platform_for_backend`, `_effective_platform`,
   `_platform_config`, `resolve`, `parse_config_dict`, and `load_config`. It also holds the
   top-level import of `bajutsu.backends` that `_platform_for_backend` and `_effective_platform`
   use, which is no longer deferred. This is the only submodule that imports `bajutsu.backends`.
   The deferred imports at today's lines 177 and 752 become ordinary top-level imports, confined to
   this one submodule instead of scattered as in-function imports across the module.

4. **`bajutsu/config/accessors.py`** holds the narrowing accessors and soft getters:
   `require_ios`, `require_web`, `require_android`, `web_base_url`, `web_engine`, `ios_bundle_id`,
   `android_package`, and `idb_version_pin` (today's lines 639–707).

5. **`bajutsu/config/__init__.py`** re-exports every public name from the four submodules. The
   precedent is the `bajutsu/report/__init__.py` re-export façade. Its docstring says: "Public API
   is re-exported here, so `from bajutsu.report import …` is unchanged". With the re-exports,
   `from bajutsu.config import Effective, resolve, require_ios, …` keeps working unchanged. No call
   site outside the new package is touched.

6. **Update [`docs/architecture.md`](../../docs/architecture.md)** (and the Japanese mirror) where
   the module list names `config.py`. The BE-0113 norm requires that document to stay in step with
   the code it describes.

## Alternatives considered

- **Group `Effective`'s fields into sub-records but keep one `config.py` file.** This captures
  part of the readability win, because the god-object field count shrinks. It misses the larger
  navigation benefit. Schema validation, resolved types, merge logic, and path rebasing still share
  one file of more than 800 lines, and the deferred `bajutsu.backends` imports stay scattered in
  function bodies. Rejected as a partial fix, because the four responsibilities are already cleanly
  MECE-separable.
- **Split by file size alone (for example, two roughly equal halves) instead of by
  responsibility.** Rejected. An arbitrary split would not confine the `bajutsu.backends`
  dependency. Both deferred-import sites would have to land in whichever half needs
  `bajutsu.backends`, and only the resolution logic needs it. An arbitrary split would also not give
  each module a single, nameable job.
- **Leave `config.py` as is.** The module keeps growing on both axes: new schema fields and new
  resolution logic land in the same file. The two lazy-import workarounds stay load-bearing
  indefinitely.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `bajutsu/config/schema.py` created with the pydantic input models (pure move)
- [x] `bajutsu/config/effective.py` created with the resolved dataclasses; `Effective`'s
      fields grouped into `EvidenceDirs` / `RunDefaults` / `DoctorThresholds` sub-records
- [x] `bajutsu/config/resolve.py` created with the merge/derivation logic; the two deferred
      `bajutsu.backends` imports become top-level imports in this module only
- [x] `bajutsu/config/accessors.py` created with `require_*` and the soft accessors
- [x] `bajutsu/config/__init__.py` re-exports the full public API; no call site outside the
      package changes
- [x] `docs/architecture.md` (en/ja) updated where it names `config.py`

**Log**

- [#1096](https://github.com/bajutsu-e2e/bajutsu/pull/1096) — split `config.py` into the four-submodule package and grouped `Effective` into
  `EvidenceDirs` / `RunDefaults` / `DoctorThresholds`. The import surface is unchanged (the
  `__init__` re-export façade), but the field grouping did update every field-access site — the
  sub-records are read as `eff.evidence_dirs.scenarios` / `eff.run_defaults.erase` /
  `eff.doctor_thresholds.ok_coverage` across the runner, CLI, serve, and MCP call sites plus their
  tests. `DoctorThresholds` drops the now-redundant `doctor_` prefix (`ok_coverage` /
  `fail_coverage`). Behavior and the config schema are unchanged; the deterministic suite is the
  regression net.

## References

- [`bajutsu/config/`](../../bajutsu/config) — the package this item splits `config.py` into
- [`bajutsu/report/__init__.py`](../../bajutsu/report/__init__.py) — the re-export façade
  precedent this split follows
- [BE-0206](../BE-0206-serve-state-module-split/BE-0206-serve-state-module-split.md) — a prior
  module split along the same "one-directional dependency between responsibilities" shape
- [BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md) — keeping
  `docs/architecture.md` in step with behavior, applied here to the module-list update
- Prime directive 3 (app-agnostic core) — config is the app-agnostic seam; this item changes
  only its internal module layout, not its behavior or schema
