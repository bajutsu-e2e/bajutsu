"""Compile-time expansion: components (`use`), data-driven rows, and reusable setups.

All of this runs before the deterministic run loop, so after expansion no `use` steps remain
and each data row is its own scenario — the runner is unaffected.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable
from typing import Any, Protocol, cast, runtime_checkable

from bajutsu.common.scenario import interp
from bajutsu.common.scenario.models import (
    Component,
    Scenario,
    Step,
    _check_http_extract_vars,
    _check_target_requirements,
    _expand_target_groups,
)

# A `group` invocation's id must stay unique across every `expand_components` call for one
# scenario, not just within one call: a `setup` prelude expands through its own, separate call
# (`run/cli.py`'s `_setup_steps`) before `apply_setups` splices its steps onto the scenario's own,
# which then expands through a second call. A counter scoped to one call would hand out the same
# id to the prelude's last group and the scenario's first, and `_fold_groups` (report/rows.py)
# would merge the two if they land adjacent. A module-wide counter never repeats a value, so this
# never happens; the exact numbers need not be stable across runs, since nothing outside one
# render depends on them.
_group_id_counter = itertools.count()


@runtime_checkable
class ScopedResolve(Protocol):
    """A `resolve` that also knows the scope a resolved ref's own steps expand under.

    `expand_components` takes a plain `Callable[[str], Component]`; one that *also* implements this
    gets its nested expansion re-scoped per ref. That is what keeps a file-scoped component name
    (BE-0422) invisible inside a component file the same scenario references: crossing into a file
    swaps to a resolver bound to no local names, so a bare ref there is always undefined.
    """

    def __call__(self, ref: str) -> Component: ...

    def scope_for(self, ref: str) -> Callable[[str], Component]:
        """The resolver *ref*'s own steps expand under."""
        ...


def _interp_steps(steps: list[Step], bindings: dict[str, str]) -> list[Step]:
    """Substitute `bindings` into each step (via a model_dump round-trip) and re-validate.

    Aliases are preserved (by_alias) so the dump re-parses cleanly.
    """
    out: list[Step] = []
    for st in steps:
        dumped = st.model_dump(by_alias=True, exclude_none=True)
        out.append(Step.model_validate(interp.interpolate(dumped, bindings)))
    return out


def _check_group_position(name: str, body: str | None, group_ctx: str | None) -> None:
    """Refuse a `group` that expansion reached inside a body or another `group`."""
    if body is not None:
        # The load-time nesting rule (`models/scenario/_group_nesting.py`) catches a `group`
        # written in a body; this is the one a component carries in (BE-0446).
        raise ValueError(f"group {name!r} must not nest inside {body}")
    if group_ctx is not None:
        # A `group` reached while already inside another `group` — directly nested, or arriving
        # through a `use` call made from inside a `group`. A load-time validator
        # (`models/scenario/`) catches the directly-nested case statically; this is the only place
        # that sees the `use`-mediated one, since a `Scenario` as loaded holds no component bodies
        # to walk.
        raise ValueError(f"group {name!r} is nested inside group {group_ctx!r}")


def _merge_target(
    caller: str | None, own: str | None, path: tuple[str, ...], what: str
) -> str | None:
    """The target a step resolves to under *caller*, refusing one naming another (BE-0446).

    An equal value is accepted — a shared component may name its target so its file stands on
    its own — but a different one is a guess either way, so it fails at load time instead.
    """
    if caller is None or own is None or own == caller:
        return caller if caller is not None else own
    raise ValueError(
        f"{' > '.join((*path, what))}: target {own!r} conflicts with the caller's target {caller!r}"
    )


def _refuse_set_primary_target(steps: list[Step], path: tuple[str, ...]) -> None:
    # A target group names one target for its run of steps; a routing move inside it would change
    # the target every later step follows (BE-0447), as the scenario-level expansion also refuses.
    if any(step.set_primary_target is not None for step in steps):
        raise ValueError(
            f"{' > '.join(path)}: setPrimaryTarget is not allowed inside a target group"
        )


def _stamp(step: Step, caller: str | None, path: tuple[str, ...]) -> Step:
    """*step* with *caller*'s target stamped on when it omits its own (BE-0446)."""
    if step.set_primary_target is not None:
        # Takes its target as an argument; a stamped modifier would name a device it has none of
        # (BE-0447).
        return step
    label = f"step {step.name!r}" if step.name is not None else "step <unnamed step>"
    target = _merge_target(caller, step.target, path, label)
    return step if target == step.target else step.model_copy(update={"target": target})


def _expand_bodies(
    step: Step,
    expand_body: Callable[[list[Step], str, str | None], list[Step]],
    body_target: str | None,
) -> dict[str, object]:
    """The `model_copy` updates that expand every body *step* holds (BE-0446).

    An `if` / `forEach` body carries *body_target* down; a `web:` / `app:` block stops it, since
    each step there must omit `target` and runs against the block's own device.
    """
    updates: dict[str, object] = {}
    if step.if_ is not None:
        else_ = step.if_.else_
        updates["if_"] = step.if_.model_copy(
            update={
                "then": expand_body(step.if_.then, "if", body_target),
                "else_": expand_body(else_, "if", body_target) if else_ is not None else None,
            }
        )
    if step.for_each is not None:
        updates["for_each"] = step.for_each.model_copy(
            update={"steps": expand_body(step.for_each.steps, "forEach", body_target)}
        )
    if step.web is not None:
        updates["web"] = step.web.model_copy(
            update={"steps": expand_body(step.web.steps, "web", None)}
        )
    if step.app is not None:
        updates["app"] = step.app.model_copy(
            update={"steps": expand_body(step.app.steps, "app", None)}
        )
    return updates


# The count folds in the nested `expand` recursion, which closes over `max_depth`; the outer body
# only drives it over each scenario's step lists (BE-0386).
def expand_components(  # noqa: C901
    scenarios: list[Scenario],
    resolve: Callable[[str], Component],
    max_depth: int = 25,
) -> None:
    """Replace every `use` step with the referenced component's steps, recursively and in place.

    Pure compile-time expansion: a component may itself `use` another, and after this no `use`
    steps remain — inside an `if` / `forEach` / `web` / `app` body too — so the run loop is
    unaffected. A `use:` or `group:` step naming `target` stamps it onto every step it produces
    that omits its own, down through `if` / `forEach` bodies and nested calls, stopping at a
    `web:` / `app:` block (BE-0446).

    Args:
        scenarios: The scenarios to expand; their `steps`, their `before` / `after` lifecycle
            steps, and every `interrupts` entry's recovery `steps` are rewritten in place.
        resolve: Maps a component name to its `Component` (e.g. by loading a shared file). One that
            also satisfies `ScopedResolve` re-scopes each ref's nested expansion, and owns whatever
            caching it needs — this function itself resolves every occurrence.
        max_depth: The deepest `use` nesting allowed before giving up on a runaway chain.

    Raises:
        ValueError: A required param is missing, an unknown param is passed, a `${params.*}` token
            references an undeclared param, a reference cycle is detected, nesting exceeds
            `max_depth`, an expanded step names a target other than its caller's, or a `group`
            lands inside a body or another `group`.
    """

    def expand(
        steps: list[Step],
        stack: list[str],
        resolve: Callable[[str], Component],
        *,
        group_ctx: str | None = None,
        group_id: int | None = None,
        level_target: str | None = None,
        body_target: str | None = None,
        path: tuple[str, ...] = (),
        body: str | None = None,
    ) -> list[Step]:
        # *level_target* is the caller target (BE-0446) stamped onto each step of this list;
        # *body_target* is the one handed to an `if` / `forEach` body below it. The two differ only
        # among a target group's direct children, whose bodies keep the enclosing caller's target
        # rather than the group's (BE-0437 leaves a body a fresh scope). *path* names the chain a
        # caller target came through, for the conflict error; *body* names the `if` / `forEach` /
        # `web` / `app` body this list sits in, if any.
        if len(stack) > max_depth:
            raise ValueError(f"component nesting too deep (>{max_depth}): {' -> '.join(stack)}")
        out: list[Step] = []
        for st in steps:
            if st.group is not None:
                _check_group_position(st.group.name, body, group_ctx)
                label = f"group: {st.group.name!r}"
                target = _merge_target(level_target, st.target, path, label)
                out.extend(
                    expand(
                        st.group.steps,
                        stack,
                        resolve,
                        group_ctx=st.group.name,
                        group_id=next(_group_id_counter),
                        level_target=target,
                        body_target=target,
                        path=(*path, label) if target is not None else path,
                    )
                )
                continue
            if st.steps is not None:
                # A target group (BE-0437) a component's own steps carry is never flattened at
                # `Component`-parse time the way a `Scenario`'s own top-level group already is —
                # `Component` carries no such validator — so it can still hold an unexpanded
                # `use:` by the time it lands here. Its target becomes the caller target of its
                # direct children, a nested `use:` included, so a step that expansion produces
                # there naming another target fails instead of silently keeping it.
                if body in ("web", "app"):
                    raise ValueError(
                        "a target group is not allowed nested inside a web: or app: block — every "
                        "step there already runs against the block's own device"
                    )
                label = f"target group {st.target!r}"
                _refuse_set_primary_target(st.steps, (*path, label))
                target = _merge_target(level_target, st.target, path, label)
                out.extend(
                    expand(
                        st.steps,
                        stack,
                        resolve,
                        group_ctx=group_ctx,
                        group_id=group_id,
                        level_target=target,
                        body_target=body_target,
                        path=(*path, label),
                        body=body,
                    )
                )
                continue
            if st.use is None:
                stamped = _stamp(st, level_target, path)
                updates = _expand_bodies(
                    stamped,
                    # An `if` / `forEach` nested in a `web:` / `app:` block keeps naming that block,
                    # so a target group anywhere inside it is refused as the load-time pass does.
                    lambda sub, kind, t: expand(
                        sub,
                        stack,
                        resolve,
                        level_target=t,
                        body_target=t,
                        path=path,
                        body=body if body in ("web", "app") else kind,
                    ),
                    body_target,
                )
                if group_ctx is not None:
                    updates |= {"report_group": group_ctx, "report_group_id": group_id}
                out.append(stamped.model_copy(update=updates) if updates else stamped)
                continue
            ref = st.use.component
            if ref in stack:
                raise ValueError(f"component cycle detected: {' -> '.join([*stack, ref])}")
            comp = resolve(ref)
            args = st.use.with_
            missing = sorted(set(comp.params) - set(args))
            unknown = sorted(set(args) - set(comp.params))
            if missing:
                raise ValueError(f"component {ref!r} missing required params: {missing}")
            if unknown:
                raise ValueError(f"component {ref!r} has unknown params: {unknown}")
            substituted = _interp_steps(comp.steps, {f"params.{k}": v for k, v in args.items()})
            dumps = [s.model_dump(by_alias=True, exclude_none=True) for s in substituted]
            residual = sorted(t for t in interp.find_tokens(dumps) if t.startswith("params."))
            if residual:
                raise ValueError(f"component {ref!r} references undeclared params: {residual}")
            # The resolved component's own steps expand under the scope *it* brings, not the
            # caller's: crossing into a component file drops the caller's file-scoped names
            # (BE-0422). Still this same recursion, so `stack` and `max_depth` keep accounting for
            # the whole chain and a real cycle raises cleanly instead of blowing the Python stack.
            nested = resolve.scope_for(ref) if isinstance(resolve, ScopedResolve) else resolve
            label = f"use: {ref}"
            target = _merge_target(level_target, st.target, path, label)
            # `group_ctx` / `group_id` carry forward unchanged, so a `use` called from inside a
            # `group` tags every step the component expands to with that same group; `body` does
            # too, since the component's steps land in the same body the `use` step sat in.
            out.extend(
                expand(
                    substituted,
                    [*stack, ref],
                    nested,
                    group_ctx=group_ctx,
                    group_id=group_id,
                    level_target=target,
                    body_target=target,
                    path=(*path, label),
                    body=body,
                )
            )
        return out

    for scenario in scenarios:
        scenario.steps = expand(scenario.steps, [], resolve)
        # The lifecycle phases (BE-0392) and an `interrupts` handler's recovery steps (BE-0314)
        # all take the ordinary step grammar, so a `use` can appear in any of them. Left
        # unexpanded it reaches the step loop as a step with no action and aborts the whole run
        # with an `AssertionError`, not one failed scenario.
        scenario.before = expand(scenario.before, [], resolve)
        for rule in scenario.after:
            rule.steps = expand(rule.steps, [], resolve)
        for entry in scenario.interrupts:
            entry.steps = expand(entry.steps, [], resolve)
        # The assignments above are plain attribute writes, which Pydantic never re-runs a
        # `model_validator` against — so a component's own steps would otherwise splice in a
        # `target` the load-time pass never saw (BE-0428), or a target group it never expanded
        # (BE-0437; see `models/scenario/_targets.py`), or an `extractBody` `var` collision only a
        # `${params.*}` substitution reveals (BE-0440; see `models/scenario/_http_extract.py`).
        _expand_target_groups(scenario)
        _check_target_requirements(scenario)
        _check_http_extract_vars(scenario)


def read_csv(text: str) -> list[dict[str, str]]:
    """Parse CSV text into a list of {column: value} row dicts (header row required)."""
    import csv
    import io

    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def _row_name(scenario_name: str, row: dict[str, str], index: int) -> str:
    kv = ", ".join(f"{k}={v}" for k, v in row.items())
    return (
        f"{scenario_name} [row {index + 1}: {kv}]" if kv else f"{scenario_name} [row {index + 1}]"
    )


# The inverse of `_row_name`, kept beside it so a change to the per-row naming format is made with
# both directions in view. A reader that only has a *run's* recorded scenario names — the serve run
# pickers, which match them against the names a suite declares — cannot otherwise tell a data-driven
# scenario's rows from a scenario of that name. A `kv` value is interpolated CSV and may hold a `]`
# of its own, so the pattern anchors on the end of the name rather than the first closing bracket.
_ROW_NAME = re.compile(r" \[row \d+(?::.*)?\]$", re.DOTALL)


def declared_name(run_scenario: str) -> str:
    """The name a suite declares for the scenario a run recorded as *run_scenario*.

    Strips the row suffix `_row_name` appends; a name carrying none is returned unchanged, so a
    scenario that is not data-driven passes through.
    """
    return _ROW_NAME.sub("", run_scenario)


def _instantiate_rows(scenario: Scenario, rows: list[dict[str, str]]) -> list[Scenario]:
    """Build one scenario per data row, substituting `${row.*}` tokens.

    The source scenario is dumped once (data/dataFile dropped) and that base dict is reused for
    every row — `interp.interpolate` returns fresh containers and never mutates its input, so the
    cache is safe — instead of re-dumping the (unchanging) source on every row. Each row is still
    re-validated through `Scenario.model_validate`, so the per-row validation guarantee is
    unchanged: we never skip validation for a row substitution touched.
    """
    base = scenario.model_dump(by_alias=True, exclude_none=True)
    base.pop("data", None)
    base.pop("dataFile", None)

    def instantiate(row: dict[str, str], index: int) -> Scenario:
        out = cast(
            "dict[str, Any]", interp.interpolate(base, {f"row.{k}": v for k, v in row.items()})
        )
        out["name"] = _row_name(scenario.name, row, index)
        return Scenario.model_validate(out)

    return [instantiate(row, i) for i, row in enumerate(rows)]


def expand_data(
    scenarios: list[Scenario],
    resolve_csv: Callable[[str], list[dict[str, str]]],
) -> list[Scenario]:
    """Expand each data-driven scenario into one scenario per data row.

    `${row.<col>}` tokens are substituted per row. A scenario with neither `data` nor `dataFile`
    passes through unchanged. Each derived scenario keeps the original's preconditions (erase
    default intact), so every row runs in its own clean environment — isolation is preserved.

    Args:
        scenarios: The scenarios to expand.
        resolve_csv: Loads a `dataFile` reference into a list of `{column: value}` rows.

    Returns:
        The scenarios with every data-driven one replaced by its per-row instances, in order.
    """
    out: list[Scenario] = []
    for s in scenarios:
        if s.data is not None:
            rows: list[dict[str, str]] | None = s.data
        elif s.data_file is not None:
            rows = resolve_csv(s.data_file)
        else:
            rows = None
        if rows is None:
            out.append(s)
            continue
        out.extend(_instantiate_rows(s, rows))
    return out


def apply_setups(
    scenarios: list[Scenario],
    default_setup: str | None,
    resolve: Callable[[str], list[Step]],
) -> None:
    """Prepend each scenario's reusable setup prelude, in place.

    A scenario's `setup` precondition (falling back to the app/config default) names a reusable
    prelude; those steps run before the scenario's own, so a shared login / navigation flow is
    written once and reused. The same reference is resolved at most once.

    Args:
        scenarios: The scenarios to prepend setups to; their `steps` are rewritten in place.
        default_setup: The setup reference used when a scenario declares none. None means none.
        resolve: Maps a setup reference to its list of steps (e.g. by loading a shared file).
    """
    cache: dict[str, list[Step]] = {}
    for scenario in scenarios:
        ref = scenario.preconditions.setup or default_setup
        if not ref:
            continue
        if ref not in cache:
            cache[ref] = resolve(ref)
        # A fresh copy per scenario: validation stamps each omitted-`target` step with this
        # scenario's own `primaryTarget` (BE-0436), and a shared instance would carry whichever
        # scenario sharing the setup happened to be validated last.
        scenario.steps = [*(st.model_copy(deep=True) for st in cache[ref]), *scenario.steps]
        # A plain attribute write, which Pydantic never re-runs a `model_validator` against — a
        # prelude's own steps, authored with no notion of this scenario's `targets`, would
        # otherwise splice in a `target` the load-time pass never saw (BE-0428), or a target group
        # it never expanded (BE-0437; see `models/scenario/_targets.py`). The `extractBody`/
        # `saveBody` re-check (BE-0440; see `models/scenario/_http_extract.py`) is the same
        # defense for a `resolve` that hands back steps no full `Scenario.model_validate` ever
        # saw: the check is purely intra-step, so it can never fire *between* the prelude and this
        # scenario's own steps — only against a `var` collision inside one of the prelude's own
        # `http` steps that skipped validation this way.
        _expand_target_groups(scenario)
        _check_target_requirements(scenario)
        _check_http_extract_vars(scenario)
