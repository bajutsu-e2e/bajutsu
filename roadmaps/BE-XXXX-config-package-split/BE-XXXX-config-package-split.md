**English** · [日本語](BE-XXXX-config-package-split-ja.md)

# BE-XXXX — Split config into a package and group Effective into sub-records

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-config-package-split.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

[`bajutsu/config.py`](../../bajutsu/config.py) is 882 lines mixing four distinct jobs in one
module: the pydantic **input schema** (`MockServer` / `LaunchServer` / `Mailbox` /
`XcuitestConfig` / `PricingEntry` / `AiSettings` / `DoctorConfig` / `NotifyEndpoint` /
`Defaults` / `TargetConfig` / `Config`, lines 59–452), the **resolved output types**
(`AiConfig` / `IosConfig` / `WebConfig` / `AndroidConfig` / `Effective`, lines 26–49 and
455–637), the **resolution/merge logic** that turns one into the other
(`_merge_redact` / `_merge_ai` / `_platform_for_backend` / `_effective_platform` /
`_platform_config` / `resolve` / `parse_config_dict` / `load_config`, lines 709–883), and
**path rebasing plus the narrowing accessors** (`Effective.rebased`, lines 580–636, and
`require_ios` / `require_web` / `require_android` plus the soft accessors, lines 639–707).
This item splits `config.py` into a `bajutsu/config/` package along those four seams and, in
the same pass, groups `Effective`'s ~30 fields into a handful of cohesive sub-records. Both
moves are behavior-preserving: no call site outside the package changes.

## Motivation

A single 882-line module doing schema validation, output typing, merge/derivation, and
path rebasing is hard to navigate — a reader looking for "how is `doctor_ok_coverage`
resolved" has to skim past pydantic validators for unrelated fields to find `resolve()`.
The four jobs also have a one-directional dependency (resolution reads the schema and
produces the output types; nothing in the schema or output types depends on resolution),
which is exactly the shape earlier module splits in this codebase (e.g.
[BE-0206](../BE-0206-serve-state-module-split/BE-0206-serve-state-module-split.md)) have used
to justify separating a file along its consumer boundaries.

`Effective` (lines 512–569) compounds the problem: it is a roughly 30-field god object whose
fields span identity (`target`, `platform_config`, `backend`, `device`, `locale`), launch
(`launch_env`, `launch_args`, `setup`, `launch_server`, `ready_when`), evidence directories
(`scenarios`, `baselines`, `schemas`, `goldens`), AI (`ai`), doctor thresholds
(`doctor_ok_coverage`, `doctor_fail_coverage`), run defaults (`dismiss_alerts`, `erase`,
`network`), notification (`notify`), routing (`requires`), and secrets/mailbox/mock-server
(`redact`, `secrets`, `mailbox`, `mock_server`). Every consumer of `Effective` receives all
30 fields regardless of which subset it actually reads, and `resolve()` (lines 821–859) is a
38-line flat constructor call assembling them one by one. `Effective.platform_config` is
already a discriminated `PlatformConfig` union (line 509) that forces `isinstance` /
`require_*` narrowing before a caller can read a platform's own knobs — a pattern worth
preserving and extending to the other field clusters, rather than the stringly
`cfg["targets"][name]["..."]` access this design deliberately avoids.

Two `config ↔ backends` import cycles are also load-bearing today: `_check_platform` (line
177) and `_platform_for_backend` (line 752) both import `bajutsu.backends` inside the
function body, with a comment explaining the cycle. Moving the resolution logic — the only
half of the module that needs `bajutsu.backends` — into its own module breaks the cycle at
the package boundary instead of inside each function.

## Detailed design

The split follows the four responsibilities identified above, MECE by construction (schema
input parsing / resolved output shape / merge-and-derive logic / path-rebase-and-accessors —
every symbol in today's `config.py` falls into exactly one):

1. **`bajutsu/config/schema.py`** — the pydantic input models: `MockServer`, `LaunchServer`,
   `Mailbox`, `XcuitestConfig`, `PricingEntry`, `AiSettings`, `DoctorConfig`, `NotifyEndpoint`,
   `Defaults`, `TargetConfig`, `Config`, and their field validators (today's lines 51–452),
   plus `_check_platform` (line 170) since it validates a schema field. This module has no
   dependency on `bajutsu.backends` beyond what's already deferred inline.

2. **`bajutsu/config/effective.py`** — the resolved, frozen dataclasses: `AiConfig`,
   `IosConfig`, `WebConfig`, `AndroidConfig`, the `PlatformConfig` union, and `Effective`
   itself, including its `platform` property and `rebased` method (today's lines 26–49 and
   455–637). `Effective`'s ~30 fields are grouped into cohesive frozen sub-records the same
   way `platform_config` already narrows the platform axis:
   - `EvidenceDirs` — `scenarios`, `baselines`, `schemas`, `goldens`.
   - `RunDefaults` — `dismiss_alerts`, `erase`, `network`.
   - `DoctorThresholds` — `doctor_ok_coverage`, `doctor_fail_coverage`.

   `Effective` keeps its remaining fields (`target`, `platform_config`, `backend`, `device`,
   `locale`, `launch_env`, `launch_args`, `id_namespaces`, `reserved_namespaces`,
   `mock_server`, `setup`, `capture`, `redact`, `secrets`, `ai`, `mailbox`, `launch_server`,
   `ready_when`, `notify`, `visual_compare`, `requires`) at the top level plus the three new
   sub-record fields (`evidence_dirs`, `run_defaults`, `doctor_thresholds`), cutting the flat
   field count roughly in half. `rebased` updates to rebuild `evidence_dirs` via `replace`
   instead of four individual `at(...)` calls.

3. **`bajutsu/config/resolve.py`** — the merge and derivation logic: `_merge_redact`,
   `_merge_ai`, `_platform_for_backend`, `_effective_platform`, `_platform_config`,
   `resolve`, `parse_config_dict`, `load_config` (today's lines 709–883), plus
   `_platform_for_backend`'s and `_effective_platform`'s top-level (no longer deferred)
   import of `bajutsu.backends`. This is the only submodule that imports `bajutsu.backends`,
   so the cycle disappears at the package boundary — the deferred imports at today's lines
   177 and 752 become ordinary top-level imports.

4. **`bajutsu/config/accessors.py`** — the narrowing accessors and soft getters:
   `require_ios`, `require_web`, `require_android`, `web_base_url`, `web_engine`,
   `ios_bundle_id`, `android_package`, `idb_version_pin` (today's lines 639–707).

5. **`bajutsu/config/__init__.py`** re-exports every public name from the four submodules —
   the `bajutsu/report/__init__.py` re-export façade (its docstring: "Public API is
   re-exported here, so `from bajutsu.report import …` is unchanged") is the precedent this
   split follows — so `from bajutsu.config import Effective, resolve, require_ios, …` keeps
   working unchanged and no call site outside the new package is touched.

6. Update [`docs/architecture.md`](../../docs/architecture.md) (and the Japanese mirror)
   where the module list names `config.py`, per the BE-0113 norm of keeping that document in
   step with the code it describes.

## Alternatives considered

- **Group `Effective`'s fields into sub-records but keep one `config.py` file.** This
  captures part of the readability win (the god-object field count shrinks) without the
  larger navigation benefit: schema validation, resolved types, merge logic, and path
  rebasing still share one 800+-line file, and the `config ↔ backends` cycle's deferred
  imports stay in place. Rejected as a partial fix given the four responsibilities are
  already cleanly MECE-separable.
- **Split by file size alone (e.g. two roughly-equal halves) instead of by responsibility.**
  Rejected — an arbitrary split wouldn't break the import cycle (both cycle sites would need
  to land in whichever half needs `bajutsu.backends`, which only resolution logic does) and
  wouldn't give each module a single, nameable job.
- **Leave `config.py` as is.** The module keeps growing on both axes (new schema fields and
  new resolution logic land in the same file), and the two lazy-import workarounds stay
  load-bearing indefinitely.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `bajutsu/config/schema.py` created with the pydantic input models (pure move)
- [ ] `bajutsu/config/effective.py` created with the resolved dataclasses; `Effective`'s
      fields grouped into `EvidenceDirs` / `RunDefaults` / `DoctorThresholds` sub-records
- [ ] `bajutsu/config/resolve.py` created with the merge/derivation logic; the two
      `config ↔ backends` deferred imports become top-level imports in this module only
- [ ] `bajutsu/config/accessors.py` created with `require_*` and the soft accessors
- [ ] `bajutsu/config/__init__.py` re-exports the full public API; no call site outside the
      package changes
- [ ] `docs/architecture.md` (en/ja) updated where it names `config.py`

## References

- [`bajutsu/config.py`](../../bajutsu/config.py) — the module this item splits
- [`bajutsu/report/__init__.py`](../../bajutsu/report/__init__.py) — the re-export façade
  precedent this split follows
- [BE-0206](../BE-0206-serve-state-module-split/BE-0206-serve-state-module-split.md) — a prior
  module split along the same "one-directional dependency between responsibilities" shape
- [BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md) — keeping
  `docs/architecture.md` in step with behavior, applied here to the module-list update
- Prime directive 3 (app-agnostic core) — config is the app-agnostic seam; this item changes
  only its internal module layout, not its behavior or schema
