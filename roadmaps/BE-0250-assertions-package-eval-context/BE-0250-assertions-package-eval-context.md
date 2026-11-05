**English** · [日本語](BE-0250-assertions-package-eval-context-ja.md)

# BE-0250 — Split assertions into a package and thread evaluation contexts as one EvalContext

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0250](BE-0250-assertions-package-eval-context.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0250") |
| Implementing PR | [#1093](https://github.com/bajutsu-e2e/bajutsu/pull/1093), [#1100](https://github.com/bajutsu-e2e/bajutsu/pull/1100), [#1106](https://github.com/bajutsu-e2e/bajutsu/pull/1106), [#1113](https://github.com/bajutsu-e2e/bajutsu/pull/1113) |
| Topic | Codebase quality & technical debt |
| Related | [BE-0172](../BE-0172-run-loop-step-decomposition/BE-0172-run-loop-step-decomposition.md) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/assertions.py` is 970 lines long. It mixes five responsibilities that share a file
because each ends up in an `AssertionResult`:

- the `evaluate` / `evaluate_one` dispatcher
- the per-kind UI evaluators
- network-timeline matching, shared with the web mock router and `until: {request}` waits
- a self-contained image-preprocessing subsystem for `visual` assertions
- JSON-Schema loading and validation for `responseSchema`

This item is a behavior-preserving refactor with two parts. First, it splits the module into a
package along those seams. Second, it replaces the per-kind contexts (`visual_context`,
`schema_context`, `golden_context`, `clipboard`), which five layers pass along today,
with a single `EvalContext` value. After the change, a new context-bearing assertion kind no longer
requires a five-file edit.

## Motivation

One module today performs five concerns that have little to do with each other: selector
resolution, network-timeline matching, image geometry and pixel I/O, JSON-Schema file loading with
path-traversal containment, and top-level dispatch. This contradicts the single-responsibility
spirit the rest of the codebase follows. `bajutsu/orchestrator/actions/` splits action handlers by
group, and `bajutsu/visual.py` already owns the pixel-compare engine. Concretely, the module holds
these pieces:

- **The dispatcher.** `evaluate` / `evaluate_one` (847–970) hold the `if a.X is not None: return
  _eval_X(...)` chain and the one-to-one `request` assignment.
- **Per-kind UI evaluators.** `_eval_exists`, `_eval_text`, `_eval_count`, and `_eval_state`
  (133–202), plus `_eval_request`, `_eval_event`, and `_eval_request_sequence` (278–418). Each is
  a small, pure check against a `query()` snapshot or the network timeline.
- **Network matching.** The web mock router and `until: {request}` waits share this code, so it
  serves more than assertions. It comprises `match_request`, `count_matching`, and `request_label`
  (205–275), plus the bipartite `_assign_requests` (497–519). The latter uses Kuhn's augmenting
  paths, so a broad `request` matcher never steals the only exchange that a more specific matcher
  needs.
- **A visual image-preprocessing subsystem of about 240 lines.** It does coordinate math,
  cropping, masking, and Pillow file I/O: `_visual_scale`, `_frame_to_px`, `_resolve_mask`,
  `_prepare_visual_comparison`, `_resolve_masks`, `_resolve_baselines`, and `_eval_visual`
  (537–780). It imports `shutil` and `PIL.Image` directly into a module that otherwise handles UI
  and network assertions.
- **JSON-Schema file loading, path-containment checks, and validation.** `_load_schema` and
  `_validate_instance` (421–494) implement this.

Adding a new assertion kind that needs a per-run context is also shotgun surgery today. Each
context travels as its own loose parameter through five layers:

- `evaluate` (914–923) and `evaluate_one` (847–856) each carry `visual_context`, `schema_context`,
  `clipboard`, and `golden_context` as four separate keyword-only parameters.
- `run_scenario` (164–168) and `_run_step_body` (107–118) in `bajutsu/orchestrator/loop.py` carry
  the same four, plus `mailbox`, as separate signature parameters.
- `bajutsu/runner/pipeline.py` constructs the three context objects individually (100–105,
  138–162) and passes them all down.

A new context-bearing assertion kind touches five places: the `Assertion` model,
`_ASSERTION_KINDS` (`bajutsu/scenario/models/_base.py:30`), a new `_eval_X` function, a new branch
in the 14-way `if` chain, and a new parameter in all five of those signatures. The whole edit serves
one new field.

Neither the assertion verdicts nor any observable run behavior changes. Assertions decide pass/fail
on the deterministic Tier-2 gate (prime directive 1), so this proposal is purely structural.
Parity tests verify it by proving identical `AssertionResult`s before and after the split.

## Detailed design

The work is MECE across four independent units. Each unit preserves behavior and can land on its
own.

- **Split `bajutsu/assertions.py` into an `assertions/` package.**
  - `assertions/network.py` holds `match_request`, `count_matching`, `request_label`,
    `_assign_requests`, and `_request_assignment_result`. `evaluate_one` calls the last (`assertions.py:522`)
    alongside `_assign_requests` in `evaluate_one` and builds its result from `request_label`.
    The web mock router and `until: {request}` already depend on `match_request` and
    `count_matching`, so the matcher is shared. It arguably belongs next to
    `NetworkExchange` in `bajutsu/network.py`. Either placement is fine while the import graph
    stays acyclic. The implementation PR calls this out explicitly.
  - `assertions/visual.py` holds `_visual_scale`, `_frame_to_px`, `_resolve_mask`, `_shift`,
    `_Prepared`, `_prepare_visual_comparison`, `_resolve_masks`, `_resolve_baselines`,
    `_eval_visual`, `VisualContext`, and `VisualEvidence`. It sits next to `bajutsu/visual.py`, the
    pixel-compare engine, in spirit. Together the two own everything related to visual assertions.
  - `assertions/schema.py` holds `SchemaContext`, `_load_schema`, `_validate_instance`, and
    `_eval_response_schema`.
  - `assertions/evaluate.py` holds the thin dispatcher (`evaluate`, `evaluate_one`, `passed`). It
    also holds the small UI-kind evaluators that do not justify their own module: `_eval_exists`,
    `_eval_text`, `_eval_count`, `_eval_state`, `_eval_request`, `_eval_event`,
    `_eval_request_sequence`, `_eval_clipboard`, and `_eval_golden`. `GoldenContext`
    (`assertions.py:105`) lives here too. It sits next to its only evaluator, `_eval_golden`, which
    already imports the compare logic from `bajutsu/golden.py`. It gets no module of its own.
  - `bajutsu/assertions.py` (or `assertions/__init__.py`) re-exports the public surface. Existing
    `from bajutsu import assertions` and `bajutsu.assertions.evaluate(...)` call sites keep
    working. This is a pure module reorganization, not a public-API change.

- **Bundle the per-run contexts into one `EvalContext`.** Introduce a frozen `EvalContext` with
  four fields: `visual: VisualContext | None`, `schema: SchemaContext | None`,
  `golden: GoldenContext | None`, and `clipboard_reader: Callable[[], str | None] | None`. The implementation decides the exact
  field shape. Today `_clipboard_for` reads `clipboard` on demand, gated on whether the block has a `clipboard` assertion. The replacement must
  preserve that laziness and must not force a read on every step.

  One `EvalContext` parameter then threads end-to-end through `evaluate`, `evaluate_one`,
  `run_scenario`, `_run_step_body`, and the construction in `pipeline.py`. This removes the
  four-separate-parameter duplication at each of those five layers.

- **Replace the 14-way `if` chain in `evaluate_one` with a data-driven registry.** Introduce a
  `{field_name: eval_fn}` mapping keyed off `_ASSERTION_KINDS`. It mirrors the `_HANDLERS: dict[str,
  ActionHandler]` registry that `bajutsu/orchestrator/actions/_registry.py` already uses for step
  actions: handlers self-register via `@_handler(kind)`, and `_do_action` dispatches them. Dispatch
  becomes a lookup instead of a chain of `is not None` checks. Adding a kind means registering a
  function, not editing the chain.

- **Derive `_ASSERTION_KINDS` from the `Assertion` model.** Today the tuple lives, hand-maintained,
  in `bajutsu/scenario/models/_base.py`. The replacement is
  `tuple(f for f in Assertion.model_fields if f != "from_")`. It mirrors the existing precedent
  for `_RUNTIME_ACTIONS` in `bajutsu/orchestrator/actions/_registry.py`
  (`tuple(a for a in STEP_ACTIONS if a != "use")`), which is itself derived from the `Step` model.
  A new assertion kind then needs a new field on `Assertion` and a registry entry. It no longer
  needs a hand-edited tuple.

Each unit keeps `make check` green on its own. Each adds parity tests (same `AssertionResult`
output before and after) and changes no assertion's observable behavior.

## Alternatives considered

- **Introduce `EvalContext` but keep the flat module.** This is a partial win. It removes the
  five-layer parameter threading, but the god-module problem remains: one file still does selector
  resolution, network-timeline matching, image geometry and pixel I/O, and schema file I/O. The
  package split is what separates those concerns, so that each can be read, tested, and changed
  independently. Bundling only the contexts solves the shallower of the two problems.

- **Split the module but leave the contexts as loose parameters.** Rejected for the mirror reason.
  The package split makes each file's job clear. A new context-bearing assertion kind would still
  mean editing five signatures. Both units are worth doing together. Each is small enough to land
  as its own PR within this item.

- **Redesign assertion evaluation around a plugin or protocol interface instead of a registry
  dict.** Rejected as heavier than the problem calls for. The `_HANDLERS` dict pattern is already
  proven for step actions. It is simpler and introduces no new abstraction to learn. It keeps
  evaluation as plain functions, whereas an interface would require every assertion kind to
  implement a class.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Split `bajutsu/assertions.py` into an `assertions/` package (`network.py`, `visual.py`, `schema.py`, `evaluate.py`), re-exporting the existing public surface.
- [x] Bundle `visual_context`/`schema_context`/`golden_context`/`clipboard` into one `EvalContext`, threaded end-to-end through `evaluate` → `evaluate_one` → `run_scenario` → `_run_step_body` → `pipeline.py`.
- [x] Replace the 14-way `if` chain in `evaluate_one` with a `{field_name: eval_fn}` registry, mirroring `bajutsu/orchestrator/actions/_registry.py`'s `_HANDLERS`.
- [x] Derive `_ASSERTION_KINDS` from `Assertion.model_fields` instead of hand-maintaining the tuple.

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
- Unit 3 (dispatch registry) — the 14-way `if a.X is not None` chain in `evaluate_one` is replaced by a
  `_EVALUATORS: dict[str, _Evaluator]` registry (in `assertions/evaluate.py`), self-registered per kind via
  `@_evaluator(kind)` and dispatched by scanning `_ASSERTION_KINDS` for the one set field — mirroring
  `orchestrator/actions/_registry.py`'s `_HANDLERS`/`_handler`/`_action_of`. Each entry is a thin adapter that
  narrows its own field (`assert a.X is not None`) and delegates to the unchanged per-kind `_eval_*`, so one dict
  holds every kind under strict typing. Behavior-preserving (exactly one kind is set per scenario validation, so
  scan order is immaterial); the existing assertion suite plus a new registry-covers-every-kind guard are the parity
  net. PR [#1106](https://github.com/bajutsu-e2e/bajutsu/pull/1106).
- Unit 4 (derive the kind tuple) — the hand-maintained `_ASSERTION_KINDS` tuple in
  `scenario/models/_base.py` is replaced by `tuple(f for f in Assertion.model_fields if f != "from_")`,
  defined in `scenario/models/assertions.py` next to the `Assertion` model and re-exported as
  `ASSERTION_KINDS` — mirroring `_STEP_ACTIONS`'s derivation from the `Step` model. A new assertion kind
  is now a single field edit plus a registry entry, not also a parallel tuple edit. Behavior-preserving
  (the derived tuple equals the old hand tuple, field-declaration order preserved); a new
  derivation-parity guard is the net. PR [#1113](https://github.com/bajutsu-e2e/bajutsu/pull/1113).

## References

- [BE-0172](../BE-0172-run-loop-step-decomposition/BE-0172-run-loop-step-decomposition.md) —
  the adjacent run-path decomposition (step loop and per-scenario runner); this item is the sibling
  decomposition on the assertion-evaluation side of the same deterministic `run` path.
- `bajutsu/assertions.py` (970 lines) — the module this item splits.
- `bajutsu/orchestrator/actions/_registry.py` — the existing `_HANDLERS` dispatch-registry pattern
  this item mirrors for assertion dispatch.
- `bajutsu/scenario/models/_base.py:30` (`_ASSERTION_KINDS`) — the hand-maintained tuple this item
  proposes deriving from the model instead.
