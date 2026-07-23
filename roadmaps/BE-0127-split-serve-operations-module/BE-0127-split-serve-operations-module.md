**English** · [日本語](BE-0127-split-serve-operations-module-ja.md)

# BE-0127 — Split the serve operations god-module

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0127](BE-0127-split-serve-operations-module.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0127") |
| Implementing PR | [#619](https://github.com/bajutsu-e2e/bajutsu/pull/619) |
| Topic | Hosting the web UI |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/serve/operations.py` has grown into a 1,376-line, roughly 669-statement god-module that
answers every `serve` endpoint from one file. This item splits it along the tab/resource axes the
web UI itself already exposes, into a set of cohesive modules, and tightens the module's
concentration of `Any`-typed parameters along the way.

## Motivation

`operations.py`'s own module docstring describes its role precisely: "the orchestration behind
each serve endpoint, lifted out of the stdlib HTTP handler so the local stdlib server and the
hosted-backend FastAPI control plane share **one** implementation." That framework-agnostic-facade
design is sound (it is what gives local/self-hosted and cloud-hosted `serve` parity — see
BE-0011 and BE-0051), but the facade itself has become one undifferentiated file. Reading top to
bottom, it answers config/provider/API-key management, doctor/preflight checks, live-log SSE
streaming, run/record/crawl dispatch, config-bundle upload, capture-session management, scenario
resolution, and enrichment — each a distinct resource area with its own section comment already
marking the boundary (e.g. `# --- Doctor / preflight (BE-0024) ---` at
`bajutsu/serve/operations.py:464`, `# --- Capture (BE-0012) ---` at `:1042`). A single file this
size is slow to navigate, concentrates nearly all of the module's `Any` typing (52 occurrences)
in one place regardless of which resource a change touches, and means every contributor working
on any one `serve` feature edits the same file, which increases merge-conflict surface for
parallel sessions (see `docs/ai-development.md`'s working-in-parallel guidance). This is a size-L
effort: the module is large, but the resource boundaries are already visible in its own comments,
so the split is a matter of relocating already-cohesive groups of functions, not redesigning the
facade's public contract.

## Detailed design

The refactor is behavior-preserving: every function currently exported from `operations.py` keeps
its signature and behavior, so both HTTP shells (the local stdlib server and the hosted FastAPI
control plane) that call into it need no changes beyond their import paths. This is the concrete
counterpart to the broader serve-scope-boundary item, which addresses *what* belongs in `serve`
at all; this item addresses *how* what already belongs there is organized. The split follows the
resource axes already implicit in the file's section comments:

- **Config/provider/API-key module** — `config_info`, `bind_config`, `bind_git_config`,
  `set_api_key`, `set_provider`, `api_key_info`, `provider_info`, `_valid_key_env_name`,
  `_active_key_env`, `_confined_config_path` (`bajutsu/serve/operations.py:131`–`270`, `:523`–
  `:647`).
- **Doctor/preflight module** — `doctor_check` and its helpers under the
  `# --- Doctor / preflight (BE-0024) ---` section (`bajutsu/serve/operations.py:464`–`520`).
- **Run/record/crawl dispatch module** — `start_run`, `start_record`, `start_crawl`,
  `_register_and_dispatch`, `_boot_targets`, `_device_args`, `_bool_flag`
  (`bajutsu/serve/operations.py:153`–`178`, `:659`–`985`).
- **Live-log SSE module** — `format_sse`, `job_log_events`, `_job_event_pairs`,
  `_terminal_payload`, `job_sse`, `_job_sse_frames` (`bajutsu/serve/operations.py:408`–`463`).
- **Config-bundle upload module** — `_safe_filename`, `bind_upload_config` under the
  `# --- Upload a bundle as the active config (BE-0073) ---` section
  (`bajutsu/serve/operations.py:785`–`855`).
- **Capture-session module** — `start_capture`, `mark_capture`, `finish_capture`,
  `_default_driver_factory` under the `# --- Capture (BE-0012) ---` section
  (`bajutsu/serve/operations.py:1042`–`1205`).
- **Scenario/run-artifact reads module** — `list_scenarios`, `read_scenario`, `_step_artifacts`,
  `_step_action_fields`, `_valid_step_id`, `_find_sid`, `job_view`, `run_file`, `runs_payload`,
  `save_scenario`, `approve_baseline`, `resolve_scenario_pick`, `browse_fs`, `simulators_payload`,
  `list_targets_payload`, `_primary_backend`, `cancel_job`
  (`bajutsu/serve/operations.py:96`–`131`, `:182`–`308`, `:364`–`408`, `:985`–`1042`, `:1205`–
  `:1288`).
- **Enrichment module** — `start_enrich` under its own delimited section
  (`bajutsu/serve/operations.py:1288`–`1376`).

Each new module keeps the same `(payload, status)` return convention and takes `ServeState` plus
already-parsed inputs, matching the existing contract described in the module's docstring. A thin
`operations/__init__.py` (or equivalent) re-exports the full public surface so callers migrate
gradually rather than in one atomic rename. Tightening the `Any`s is scoped per new module as it's
extracted — each split is a natural point to replace a still-`Any` parameter (typically JSON
request bodies) with a `TypedDict` or a narrower type, without doing so across the whole facade at
once.

## Alternatives considered

- **Leave `operations.py` as one file and rely on its section comments for navigation.** Rejected:
  the comments already exist and demonstrably have not prevented the file from growing to 1,376
  lines; comments do not reduce merge-conflict surface or make any one resource area
  independently reviewable.
- **Split by HTTP verb (GET/POST) rather than by resource.** Rejected: a verb-based split would
  scatter a single resource's read and write operations (e.g. `read_scenario` and `save_scenario`)
  across different files, which is less cohesive for a contributor working on one feature than the
  resource-based split above.
- **Rewrite the facade as classes (one per resource) instead of module-level functions.**
  Rejected: the existing function-based, explicit-`ServeState`-parameter style is simple to test
  and matches the rest of the codebase's conventions; introducing classes here would be a second,
  unrelated design change bundled into what should be a pure reorganization.
- **Do the split and the `Any`-tightening as two separate efforts.** Considered, but rejected in
  favor of tightening incrementally within each split: waiting for a dedicated typing pass after
  the split lands would mean re-reading every relocated function a second time, whereas tightening
  the type as each cohesive group is extracted is a natural, low-cost addition to the same change.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Extract the config/provider/API-key module (`operations/config.py`)
- [x] Extract the doctor/preflight module (`operations/doctor.py`)
- [x] Extract the run/record/crawl dispatch module (`operations/dispatch.py`)
- [x] Extract the live-log SSE module (`operations/sse.py`)
- [x] Extract the config-bundle upload module (`operations/upload.py`)
- [x] Extract the capture-session module (`operations/capture.py`)
- [x] Extract the scenario/run-artifact reads module (`operations/reads.py`)
- [x] Extract the enrichment module (`operations/enrich.py`)
- [x] Extract the worker HTTP API module (`operations/worker.py`) — added since the proposal was
      written (BE-0106 grew the file); split out on the same resource axis as the eight above
- [x] Add a re-export shim so existing callers (both HTTP shells) migrate without a single
      atomic rename — the package `operations/__init__.py` re-exports the full public surface, so
      every `ops.<name>` caller keeps working unchanged. This is kept as the permanent
      framework-agnostic facade rather than removed: it is the very seam that gives local/self-hosted
      and cloud-hosted `serve` one shared implementation, and removing it would scatter the facade
      across submodule-direct imports.

- 2026-07-03: split `operations.py` (1,438 lines) into the `operations/` package — nine resource
  submodules plus a shared `_common.py` (the three cross-cutting private helpers `_device_args`,
  `_resolve_org_or_forbid`, `_default_driver_factory`) and the re-export facade `__init__.py`.
  Behavior-preserving: every function's signature and body are unchanged, so both HTTP shells and
  all tests reach the surface through `ops.<name>` as before. Tightened `_primary_backend`'s
  `config: Any` to `config: Config` as the reads module was extracted. Added
  `tests/serve/test_operations_package.py` pinning the facade contract. ([#619](https://github.com/bajutsu-e2e/bajutsu/pull/619))

## References

- `bajutsu/serve/operations.py:1`–`1376` (1,376 lines, ~669 statements, 52 `Any` occurrences)
- `bajutsu/serve/operations.py:464` (`# --- Doctor / preflight (BE-0024) ---`), `:1042`
  (`# --- Capture (BE-0012) ---`), `:785` (upload-bundle section), `:1288` (enrichment section) —
  the section comments the split follows
- Related: BE-0011 (local web UI serve), BE-0051 (serve hardening for hosting)
- See also the serve-scope-boundary item, the broader design-level counterpart to this concrete
  module split
- Originates from the 2026-07-02 codebase-analysis report (technical debt).
