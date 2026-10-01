"""The `targets`/`target` routing rule (BE-0428), extracted so it can run more than once.

`Scenario`'s own `model_validator` calls `_check_target_requirements` once at load time. Three
later points rebuild an already-validated `Scenario` in place — `apply_setups` and
`expand_components` (a plain attribute assignment) and `with_lifecycle_phases` (a `model_copy`) —
and Pydantic re-runs a `model_validator` against none of them, so each calls this same function
again on its own result rather than trusting the one load-time pass to have seen the steps it just
spliced in.

`_expand_target_groups` (BE-0437) runs right before each of those same four points, replacing every
target group with its own nested, target-stamped steps. Once it has run, `_check_target_requirements`
itself needs no change: every step it walks is already the flat, per-step `target:` form, whether an
author wrote it by hand or a group produced it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from bajutsu.common.scenario.models.assertions import Assertion
    from bajutsu.common.scenario.models.steps import Step

    from .scenario import Scenario

# How a step's own `target` is held, by where the step sits: "required" once two or more targets
# are declared (a top-level step, or one under an `if`/`forEach`) — subject to the scenario's own
# `primaryTarget` fallback (BE-0436); "forbidden" inside `web:`/`app:`; "optional" inside an
# `interrupts` entry's recovery `steps`, where an omitted value stays on the runner whose interrupt
# guard fired — the entry's own target, or an enclosing `if`/`forEach` step's (BE-0438).
_StepTargetMode = Literal["required", "forbidden", "optional"]


def _step_label(step: Step) -> str:
    return repr(step.name) if step.name is not None else "<unnamed step>"


def _expand_steps(steps: list[Step], *, group_target: str | None, inside_web: bool) -> list[Step]:
    """Expand every target group (BE-0437) in *steps*, recursively; the flat result.

    *group_target* is the target a directly-enclosing group already fixed, to stamp onto a child
    that omits its own (`Step`'s own validator guarantees every such child does). It is None
    anywhere outside a group — a scenario's own top-level lists, and a `then`/`else`/`forEach`
    body, which are each a fresh scope requiring their own explicit target or group — where a
    step's `target` is left exactly as the author wrote it, for the existing per-step rule to
    check afterward.
    """
    out: list[Step] = []
    for step in steps:
        if step.steps is not None:
            if inside_web:
                raise ValueError(
                    f"{_step_label(step)}: a target group is not allowed nested inside a web: or "
                    "app: block — every step there already runs against the block's own device"
                )
            out.extend(_expand_steps(step.steps, group_target=step.target, inside_web=False))
            continue
        stamped = (
            step.model_copy(update={"target": group_target}) if group_target is not None else step
        )
        out.append(_expand_nested(stamped, inside_web=inside_web))
    return out


def _expand_nested(step: Step, *, inside_web: bool) -> Step:
    """*step*, with a target group expanded away inside its own `if`/`forEach`/`web`/`app` body."""
    updates: dict[str, object] = {}
    if step.if_ is not None:
        else_ = step.if_.else_
        updates["if_"] = step.if_.model_copy(
            update={
                "then": _expand_steps(step.if_.then, group_target=None, inside_web=inside_web),
                "else_": (
                    _expand_steps(else_, group_target=None, inside_web=inside_web)
                    if else_ is not None
                    else None
                ),
            }
        )
    if step.for_each is not None:
        updates["for_each"] = step.for_each.model_copy(
            update={
                "steps": _expand_steps(
                    step.for_each.steps, group_target=None, inside_web=inside_web
                )
            }
        )
    if step.web is not None:
        updates["web"] = step.web.model_copy(
            update={"steps": _expand_steps(step.web.steps, group_target=None, inside_web=True)}
        )
    if step.app is not None:
        updates["app"] = step.app.model_copy(
            update={"steps": _expand_steps(step.app.steps, group_target=None, inside_web=True)}
        )
    return step.model_copy(update=updates) if updates else step


def _expand_target_groups(scenario: Scenario) -> None:
    """Replace every target group (BE-0437) with its own nested, target-stamped steps, in place.

    A pure load-time authoring convenience: `bajutsu run`, the report, and the CLI never see a
    target group, only the flat per-step `target:` form BE-0428 already validates and runs. Walks
    the same four step lists `_check_target_requirements` walks (`steps`, `before`, every `after`
    rule's `steps`, every `interrupts` entry's `steps`), and the same `if`/`forEach`/`web`/`app`
    nesting.

    Raises:
        ValueError: A target group sits nested directly inside a `web:`/`app:` block's own steps.
    """
    scenario.steps = _expand_steps(scenario.steps, group_target=None, inside_web=False)
    scenario.before = _expand_steps(scenario.before, group_target=None, inside_web=False)
    for rule in scenario.after:
        rule.steps = _expand_steps(rule.steps, group_target=None, inside_web=False)
    for entry in scenario.interrupts:
        entry.steps = _expand_steps(entry.steps, group_target=None, inside_web=False)


def _check_target(
    target: str | None,
    *,
    known: set[str],
    context: str,
    default: str | None = None,
    required: bool = True,
) -> str | None:
    # One rule for every surface that may select a target — a step, an `expect` entry, and an
    # `interrupts` entry — with *context* naming the offender ("step 'tap login'", "expect entry").
    # Returns the primary an omitted *target* resolved to under two or more declared targets
    # (BE-0436), else None. `required=False` additionally lets an omitted value through with no
    # such resolution — used only where no step-level fallback applies (BE-0438's `interrupts`
    # entries and their recovery steps, and a `use:` / `group:` step whose expansion resolves
    # instead, BE-0446); a named one is still checked either way.
    n = len(known)
    if n >= 2:
        if target is None:
            if default is not None:
                return default
            if not required:
                return None
            raise ValueError(f"{context}: target is required — the scenario declares {n} targets")
        if target not in known:
            raise ValueError(
                f"{context}: target {target!r} is not one of the scenario's declared targets "
                f"{sorted(known)}"
            )
    elif n == 1:
        (single,) = known
        if target is not None and target != single:
            raise ValueError(
                f"{context}: target {target!r} does not match the scenario's one declared "
                f"target {single!r}"
            )
    elif target is not None:
        raise ValueError(f"{context}: target is set but the scenario declares no targets")
    return None


def _check_primary_target(scenario: Scenario) -> None:
    # Pinned to the first declared name rather than any declared target: `_lease_set` /
    # `_target_runtimes` already treat that entry as the primary for leasing, crash recovery, and
    # evidence context, so allowing another would let the file's "primary" and the runner's diverge
    # silently (BE-0436). Under a device group that name is the first member of the first group
    # (BE-0447).
    primary = scenario.primary_target
    if primary is None:
        return
    names = scenario.target_names
    if not names:
        raise ValueError("primaryTarget is set but the scenario declares no targets")
    if primary != names[0]:
        raise ValueError(
            f"primaryTarget {primary!r} must be the first entry of targets ({names[0]!r})"
        )


def _repeated(names: list[str]) -> list[str]:
    return sorted({n for n in names if names.count(n) > 1})


def _check_device_groups(scenario: Scenario) -> None:
    """Enforce the device-group and `installs` rules a scenario alone can decide (BE-0447).

    A name appears once across every group, and an array holds two or more names, since a
    one-name array adds nothing over a bare name. Every `installs` entry is a declared name. A
    group of two or more that does not hold the primary lists at least one member in `installs`:
    with no primary to anchor it, which member starts on the device must never be implicit.
    Whether a group's members can actually share one device needs the config, so run preflight
    checks that instead.
    """
    groups = scenario.device_groups
    names = scenario.target_names
    if dupes := _repeated(names):
        raise ValueError(f"targets contains a duplicate name {dupes}: {scenario.targets}")
    for group in scenario.targets:
        if isinstance(group, list) and len(group) < 2:
            raise ValueError(
                f"targets group {group} holds fewer than two names — write a single target as a "
                "bare name, not an array"
            )
    unknown = [name for name in scenario.installs if name not in names]
    if unknown:
        raise ValueError(
            f"installs names {unknown}, which are not among the scenario's declared targets {names}"
        )
    if dupes := _repeated(scenario.installs):
        raise ValueError(f"installs contains a duplicate name {dupes}: {scenario.installs}")
    listed = set(scenario.installs)
    for group in groups[1:]:
        if len(group) >= 2 and not listed.intersection(group):
            raise ValueError(
                f"targets group {group} names none of its members in installs — list the member "
                "that starts on the device, so the others install only through installApp"
            )


def _check_step_target(
    step: Step,
    *,
    known: set[str],
    mode: _StepTargetMode,
    default: str | None,
    caller: str | None = None,
) -> None:
    # *caller* is an enclosing `group:` step's own `target`, which expansion will stamp onto this
    # step when it omits one (BE-0446): the step then needs no target of its own, and one it names
    # must agree.
    context = f"step {_step_label(step)}"
    if mode == "forbidden":
        if step.target is not None:
            raise ValueError(
                f"{context}: target is not allowed on a step nested inside a "
                "web: or app: block (it always runs against the block's own device)"
            )
        step.resolve_target(None)
        return
    if step.use is not None or step.group is not None:
        # `expand_components` replaces this step with the steps it produces and stamps its own
        # `target` onto each one that omits it (BE-0446), so only membership is checked here. An
        # omitted one resolves nothing: the expanded steps resolve for themselves, at the
        # post-expansion pass.
        _check_target(step.target, known=known, context=context, required=False)
        _check_caller_agrees(step.target, caller, context=context)
        step.resolve_target(None)
        return
    if caller is not None:
        _check_target(step.target, known=known, context=context, required=False)
        _check_caller_agrees(step.target, caller, context=context)
        step.resolve_target(None)
        return
    # Assigned even when None: a step copied from a scenario that resolved it (`apply_setups`'s
    # deep-copied prelude) must not keep that scenario's primary. A recovery step (`mode ==
    # "optional"`) never resolves through `primaryTarget` — an omitted one stays on the runner
    # whose interrupt guard fired, which `_StepRunner._route` reads off `step.target` directly
    # (BE-0438) — so it always resolves to `None` here regardless of the scenario's own primary.
    step.resolve_target(
        _check_target(
            step.target,
            known=known,
            context=context,
            default=default if mode == "required" else None,
            required=mode == "required",
        )
    )


def _check_caller_agrees(target: str | None, caller: str | None, *, context: str) -> None:
    # The same equal-accepted, different-refused rule `expand_components` applies to what it
    # stamps (BE-0446), raised here too so a static reader that never expands (`bajutsu lint`, the
    # serve editor) still sees it.
    if caller is not None and target is not None and target != caller:
        raise ValueError(
            f"{context}: target {target!r} conflicts with the enclosing group's target {caller!r}"
        )


def _reject_assertion_target(a: Assertion, *, context: str) -> None:
    # Only an expect entry may set target — it would otherwise restate or contradict the target
    # the enclosing step already fixes for an inline `assert:` list or an `if`'s `condition`.
    # `Interrupt.condition` is held to the same rule: the entry itself carries `target`, which
    # fixes the tree its condition polls (BE-0438).
    if a.target is not None:
        raise ValueError(f"{context}: target is only allowed on a top-level expect entry")


# The count folds in the nested `walk_steps` recursion, which needs this scope's `known` and
# `default`; the outer body itself is a flat pass over the scenario's step lists (BE-0386).
def _check_target_requirements(scenario: Scenario) -> None:  # noqa: C901
    """Enforce `target`'s requirement against the number of declared target names, recursively.

    Zero or one declared targets: every step's/assertion's `target` must be omitted, or must name
    that one target. Two or more: every step — including an `if` / `forEach` / `web` / `app`
    wrapper, not only a leaf action — and every top-level `expect` entry must set `target`
    explicitly, naming one of the declared targets. A step nested inside a `web:` or `app:` block
    is the one exception — it must omit `target` outright, since it always runs against the device
    the enclosing block already resolved (`web:`'s own `WebContextDriver`, or `app:`'s unchanged
    native driver). An `interrupts` entry's own `target` is the one field that stays optional at
    any count: an omitted value watches the primary target (BE-0438). A step in that entry's
    recovery `steps` may omit `target` too, running on the entry's own target (or an enclosing
    `if`/`forEach` step's), but one it does set must still name a declared target. An `Assertion`
    reached through an inline `assert:` list, an `if`'s `condition`, or an `interrupts` entry's
    `condition` must never set `target` — only one reached through the scenario's top-level
    `expect` block may. A `use:` or `group:` step is checked only for membership, never required
    or resolved: expansion replaces it with the steps it produces, stamping its `target` onto them,
    and the pass `expand_components` runs afterwards holds each of those to the rule above
    (BE-0446). A `group:` step's own `steps` are walked with its `target` as their caller: a
    child omitting `target` needs none of its own, and one naming a different target is refused.

    A scenario that sets `primaryTarget` (which must be the first declared name) lifts the
    two-or-more requirement (BE-0436): a step or top-level `expect` entry that omits `target` runs
    against the primary. A step records that on its private `resolved_target`, never on `target` itself, so a
    re-serialized step stays as terse as its author wrote it. The resolution is flat — a nested
    `if` / `forEach` step that omits `target` resolves to the primary, never to its wrapper's own.
    This fallback never reaches an `interrupts` recovery step, which resolves through its own
    entry instead (the paragraph above), whether or not `primaryTarget` is set.
    """
    # Routing sees the flattened names: a device group changes which device a member runs on,
    # never which names a step may address (BE-0447).
    known = set(scenario.target_names)
    _check_device_groups(scenario)
    _check_primary_target(scenario)
    default = scenario.primary_target

    def walk_steps(steps: list[Step], *, mode: _StepTargetMode, caller: str | None = None) -> None:
        # *caller* reaches through `if` / `forEach` bodies the way expansion's stamp does, and a
        # `web:` / `app:` block drops it, whose steps must omit `target` anyway (BE-0446).
        for step in steps:
            _check_step_target(step, known=known, mode=mode, default=default, caller=caller)
            if step.assert_ is not None:
                for a in step.assert_:
                    _reject_assertion_target(a, context=f"step {_step_label(step)}: assert")
            if step.if_ is not None:
                _reject_assertion_target(
                    step.if_.condition, context=f"step {_step_label(step)}: if condition"
                )
                walk_steps(step.if_.then, mode=mode, caller=caller)
                if step.if_.else_ is not None:
                    walk_steps(step.if_.else_, mode=mode, caller=caller)
            if step.for_each is not None:
                walk_steps(step.for_each.steps, mode=mode, caller=caller)
            if step.group is not None:
                # Expansion has not yet stamped the group's `target` onto its children, so it is
                # passed down as their caller rather than checking them as bare steps, which would
                # reject a valid `group:` naming one under two targets (BE-0446).
                walk_steps(step.group.steps, mode=mode, caller=step.target or caller)
            if step.web is not None:
                walk_steps(step.web.steps, mode="forbidden")
            if step.app is not None:
                # `app:` reuses the same native driver throughout, unlike `web:`'s separate
                # `WebContextDriver` — but a nested step still always runs against the device the
                # enclosing step already routed to, so the same omit-`target` rule applies
                # (BE-0428).
                walk_steps(step.app.steps, mode="forbidden")

    walk_steps(scenario.steps, mode="required")
    walk_steps(scenario.before, mode="required")
    for rule in scenario.after:
        walk_steps(rule.steps, mode="required")
    for entry in scenario.interrupts:
        _check_target(entry.target, known=known, context="interrupts entry", required=False)
        walk_steps(entry.steps, mode="optional")
        _reject_assertion_target(entry.condition, context="interrupts entry: condition")
    for a in scenario.expect:
        # An omitted entry needs no stamp: `_evaluate_expect` already groups it under the run's
        # primary, which `_check_primary_target` just pinned to this same name.
        _check_target(a.target, known=known, context="expect entry", default=default)


def _scenarios_declaring_targets(scenarios: list[Scenario]) -> list[str]:
    """The names of every scenario in *scenarios* declaring two or more `targets` (BE-0428).

    A single declared target poses no *multi*-driver routing hazard: every step must already omit
    `target` or match that one name, so no step can be misrouted to the wrong one of several live
    drivers, since only one is ever leased. It stays unchecked against the run's own resolved
    `--target`, though — naming a target the operator didn't select is a stale-scenario footgun
    this item leaves to the CLI unit's own membership check (BE-0428), not a hazard this guard
    covers. Multi-target execution needs the CLI/launch/runner support units 2-5 of BE-0428 add;
    until those land, every caller that would execute or emit against the named scenario —
    `bajutsu run`'s `run_all`, `bajutsu audit --repeat`, `bajutsu codegen`, and serve's own Codegen
    endpoint alike — refuses a scenario this names, rather than silently leasing one target and
    running (or emitting) every step against it regardless of which target each step actually
    declared.
    """
    return sorted({s.name for s in scenarios if len(s.target_names) >= 2})
