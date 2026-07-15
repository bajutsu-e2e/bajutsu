**English** · [日本語](BE-0250-assertions-package-eval-context-ja.md)

# BE-0250 — Split assertions into a package and thread evaluation contexts as one EvalContext

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0250](BE-0250-assertions-package-eval-context.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0250") |
| Implementing PR | [#1093](https://github.com/bajutsu-e2e/bajutsu/pull/1093), [#1100](https://github.com/bajutsu-e2e/bajutsu/pull/1100) |
| Topic | Codebase quality & technical debt |
| Related | [BE-0172](../BE-0172-run-loop-step-decomposition/BE-0172-run-loop-step-decomposition.md) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/assertions.py` is 970 lines mixing five responsibilities that only share a home because
they all end up in an `AssertionResult`: the `evaluate`/`evaluate_one` dispatcher, the per-kind UI
evaluators, network-timeline matching shared with the web mock router and `until: {request}` waits,
a self-contained image-preprocessing subsystem for `visual` assertions, and JSON-Schema loading and
validation for `responseSchema`. This item is a behavior-preserving refactor that splits the module
into a package along those seams and replaces the five-layer threading of per-kind contexts
(`visual_context`, `schema_context`, `golden_context`, `clipboard`) with a single `EvalContext`
value, so a new context-bearing assertion kind stops requiring a five-file edit.

## Motivation

One module today performs selector resolution, network-timeline matching, image geometry and pixel
I/O, JSON-Schema file loading with path-traversal containment, and top-level dispatch — five
concerns with little to do with each other, at odds with the single-responsibility spirit the rest
of the codebase already follows (`bajutsu/orchestrator/actions/` splits action handlers by group;
`bajutsu/visual.py` already owns the pixel-compare engine). Concretely:

- **The dispatcher.** `evaluate`/`evaluate_one` (847–970) — the `if a.X is not None: return
  _eval_X(...)` chain plus the one-to-one `request` assignment.
- **Per-kind UI evaluators.** `_eval_exists`/`_eval_text`/`_eval_count`/`_eval_state` (133–202) and
  `_eval_request`/`_eval_event`/`_eval_request_sequence` (278–418) — small, pure checks against a
  `query()` snapshot or the network timeline.
- **Network matching**, shared with the web mock router and `until: {request}` waits, not just
  assertions: `match_request`/`count_matching`/`request_label` (205–275) plus the bipartite
  `_assign_requests` (497–519, Kuhn's augmenting paths, so a broad `request` matcher never steals the
  only exchange a more specific one needs).
- **A ~240-line visual image-preprocessing subsystem** doing coordinate math, cropping, masking, and
  Pillow file I/O: `_visual_scale`/`_frame_to_px`/`_resolve_mask`/`_prepare_visual_comparison`/
  `_resolve_masks`/`_resolve_baselines`/`_eval_visual` (537–780), importing `shutil` and `PIL.Image`
  directly into what is otherwise a UI/network assertion module.
- **JSON-Schema file loading, path-containment checks, and validation**: `_load_schema`/
  `_validate_instance` (421–494).

Separately, adding a new assertion kind that needs a per-run context is shotgun surgery today,
because each context is threaded as its own loose parameter through five layers: `evaluate`
(914–923) and `evaluate_one` (847–856) both carry `visual_context`, `schema_context`, `clipboard`,
and `golden_context` as four separate keyword-only parameters; `bajutsu/orchestrator/loop.py`'s
`run_scenario` (164–168) and `_run_step_body` (107–118) carry the same four (plus `mailbox`) as
separate signature parameters; and `bajutsu/runner/pipeline.py` constructs the three context objects
individually (100–105, 138–162) before passing them all down. A new context-bearing assertion kind
therefore touches the `Assertion` model, `_ASSERTION_KINDS`
(`bajutsu/scenario/models/_base.py:30`), a new `_eval_X` function, a new branch in the 14-way `if`
chain, and a new parameter threaded through all five of those signatures — for one new field.

Neither the assertion verdicts nor any observable run behavior changes here: assertions decide
pass/fail on the deterministic Tier-2 gate (prime directive 1), so this proposal is purely
structural, verified by parity tests that prove identical `AssertionResult`s before and after the
split.

## Detailed design

The work is MECE across four independent units, each behavior-preserving and independently
landable:

- **Split `bajutsu/assertions.py` into an `assertions/` package.**
  - `assertions/network.py` — `match_request`, `count_matching`, `request_label`,
    `_assign_requests`, `_request_assignment_result` (`assertions.py:522`, called alongside
    `_assign_requests` in `evaluate_one` and building its result from `request_label`). This is a shared matcher (the web mock router and `until: {request}` already
    depend on `match_request`/`count_matching`), so it is arguably better co-located with
    `bajutsu/network.py`'s `NetworkExchange`; either placement is fine as long as the import cycle
    stays acyclic — call this out explicitly in the implementation PR.
  - `assertions/visual.py` — `_visual_scale`, `_frame_to_px`, `_resolve_mask`, `_shift`, `_Prepared`,
    `_prepare_visual_comparison`, `_resolve_masks`, `_resolve_baselines`, `_eval_visual`,
    `VisualContext`, `VisualEvidence`. Next to `bajutsu/visual.py` (the pixel-compare engine) in
    spirit, so the two together own everything visual-assertion related.
  - `assertions/schema.py` — `SchemaContext`, `_load_schema`, `_validate_instance`,
    `_eval_response_schema`.
  - `assertions/evaluate.py` — the thin dispatcher (`evaluate`, `evaluate_one`, `passed`) and the
    small UI-kind evaluators (`_eval_exists`, `_eval_text`, `_eval_count`, `_eval_state`,
    `_eval_request`, `_eval_event`, `_eval_request_sequence`, `_eval_clipboard`, `_eval_golden`) that
    don't warrant their own module. `GoldenContext` (`assertions.py:105`) lives here too, next to
    its only evaluator `_eval_golden` (which already imports the compare logic from
    `bajutsu/golden.py`), rather than in its own module.
  - `bajutsu/assertions.py` (or `assertions/__init__.py`) re-exports the public surface so existing
    `from bajutsu import assertions` / `bajutsu.assertions.evaluate(...)` call sites are unaffected —
    this is a pure module reorganization, not a public-API change.

- **Bundle the per-run contexts into one `EvalContext`.** Introduce a frozen
  `EvalContext(visual: VisualContext | None, schema: SchemaContext | None, golden: GoldenContext |
  None, clipboard_reader: Callable[[], str | None] | None)` (exact field shape TBD at
  implementation time — `clipboard` is read lazily today, gated on whether the block actually has a
  `clipboard` assertion, via `_clipboard_for`; the replacement must preserve that laziness, not force
  a read on every step) and thread *one* `EvalContext` parameter end-to-end through `evaluate` →
  `evaluate_one` → `run_scenario` → `_run_step_body` → `pipeline.py`'s construction, removing the
  four-separate-parameter duplication at each of those five layers.

- **Replace the 14-way `if` chain in `evaluate_one` with a data-driven registry.** Introduce a
  `{field_name: eval_fn}` mapping keyed off `_ASSERTION_KINDS`, mirroring the `_HANDLERS: dict[str,
  ActionHandler]` registry that `bajutsu/orchestrator/actions/_registry.py` already uses for step
  actions (self-registering handlers via `@_handler(kind)`, dispatched by `_do_action`). Dispatch
  becomes a lookup instead of a chain of `is not None` checks, and adding a kind means registering a
  function rather than editing the chain.

- **Derive `_ASSERTION_KINDS` from the `Assertion` model** instead of hand-maintaining the tuple in
  `bajutsu/scenario/models/_base.py`: `tuple(f for f in Assertion.model_fields if f != "from_")`,
  mirroring the existing precedent for `_RUNTIME_ACTIONS` in
  `bajutsu/orchestrator/actions/_registry.py` (`tuple(a for a in STEP_ACTIONS if a != "use")`),
  which is itself derived from the `Step` model. A new assertion kind then only needs a new field on
  `Assertion` plus a registry entry, not also a hand-edited tuple.

Each unit keeps `make check` green on its own and adds parity tests (same `AssertionResult` output
before and after) rather than changing any assertion's observable behavior.

## Alternatives considered

- **Introduce `EvalContext` but keep the flat module.** A partial win — it removes the five-layer
  parameter threading — but leaves the god-module problem: one file still does selector resolution,
  network-timeline matching, image geometry/pixel I/O, and schema file I/O. The package split is
  what actually separates those concerns so each can be read, tested, and changed independently;
  bundling only the contexts without splitting the module solves the shallower of the two problems.

- **Split the module but leave the contexts as loose parameters.** Rejected for the mirror reason:
  the package split makes each file's job clear, but a new context-bearing assertion kind would
  still mean editing five signatures. Both units are worth doing together, and each is independently
  small enough to land as its own PR within this item.

- **Redesign assertion evaluation around a plugin/protocol interface instead of a registry dict.**
  Rejected as heavier than the problem calls for: the `_HANDLERS` dict pattern already proven for
  step actions is simpler, has no new abstraction to learn, and keeps evaluation as plain functions
  rather than requiring every assertion kind to implement a class interface.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Split `bajutsu/assertions.py` into an `assertions/` package (`network.py`, `visual.py`, `schema.py`, `evaluate.py`), re-exporting the existing public surface.
- [x] Bundle `visual_context`/`schema_context`/`golden_context`/`clipboard` into one `EvalContext`, threaded end-to-end through `evaluate` → `evaluate_one` → `run_scenario` → `_run_step_body` → `pipeline.py`.
- [ ] Replace the 14-way `if` chain in `evaluate_one` with a `{field_name: eval_fn}` registry, mirroring `bajutsu/orchestrator/actions/_registry.py`'s `_HANDLERS`.
- [ ] Derive `_ASSERTION_KINDS` from `Assertion.model_fields` instead of hand-maintaining the tuple.

### Log

- Unit 1 (package split) — `bajutsu/assertions.py` split into `bajutsu/assertions/` (`_common`, `network`,
  `visual`, `schema`, `evaluate`), the public surface re-exported from `__init__`. Behavior-preserving;
  the existing assertion/network suites plus a re-export/acyclicity guard are the parity net.
  PR [#1093](https://github.com/bajutsu-e2e/bajutsu/pull/1093).
- Unit 2 (EvalContext) — a frozen `EvalContext(visual, schema, golden, clipboard)` (in `assertions/evaluate.py`,
  re-exported from `__init__`) replaces the four loose keyword-only parameters threaded in lockstep through
  `evaluate` / `evaluate_one` / `_evaluate_expect` / `run_scenario` / `_run_step_body` / `pipeline.py`. `clipboard`
  stays a resolved value (not a reader): `_clipboard_for` still gates and reads it once per block, so the poll loop
  never re-reads it. Step-level asserts keep receiving only golden + clipboard (no per-step screenshot exists for
  `visual` / `responseSchema`), preserved by dropping those two fields at that call site. Behavior-preserving; the
  existing assertion/network/loop suites plus new frozen-context / field-routing / step-drop guards are the parity net.
  PR [#1100](https://github.com/bajutsu-e2e/bajutsu/pull/1100).

## References

- [BE-0172](../BE-0172-run-loop-step-decomposition/BE-0172-run-loop-step-decomposition.md) —
  the adjacent run-path decomposition (step loop and per-scenario runner); this item is the sibling
  decomposition on the assertion-evaluation side of the same deterministic `run` path.
- `bajutsu/assertions.py` (970 lines) — the module this item splits.
- `bajutsu/orchestrator/actions/_registry.py` — the existing `_HANDLERS` dispatch-registry pattern
  this item mirrors for assertion dispatch.
- `bajutsu/scenario/models/_base.py:30` (`_ASSERTION_KINDS`) — the hand-maintained tuple this item
  proposes deriving from the model instead.
