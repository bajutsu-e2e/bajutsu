"""The `targets`/`target` routing rule (BE-0428), extracted so it can run more than once.

`Scenario`'s own `model_validator` calls `_check_target_requirements` once at load time. Three
later points rebuild an already-validated `Scenario` in place — `apply_setups` and
`expand_components` (a plain attribute assignment) and `with_lifecycle_phases` (a `model_copy`) —
and Pydantic re-runs a `model_validator` against none of them, so each calls this same function
again on its own result rather than trusting the one load-time pass to have seen the steps it just
spliced in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bajutsu.common.scenario.models.assertions import Assertion
    from bajutsu.common.scenario.models.steps import Step

    from .scenario import Scenario


def _step_label(step: Step) -> str:
    return repr(step.name) if step.name is not None else "<unnamed step>"


def _check_target(
    target: str | None, *, known: set[str], context: str, default: str | None = None
) -> str | None:
    # One rule for both surfaces that may select a target — a step and an `expect` entry — with
    # *context* naming the offender ("step 'tap login'", "expect entry"). Returns the primary an
    # omitted *target* resolved to under two or more declared targets (BE-0436), else None.
    n = len(known)
    if n >= 2:
        if target is None:
            if default is not None:
                return default
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
    # Pinned to `targets[0]` rather than any declared target: `_lease_set` / `_target_runtimes`
    # already treat that entry as the primary for leasing, crash recovery, and evidence context, so
    # allowing another would let the file's "primary" and the runner's diverge silently (BE-0436).
    primary = scenario.primary_target
    if primary is None:
        return
    if not scenario.targets:
        raise ValueError("primaryTarget is set but the scenario declares no targets")
    if primary != scenario.targets[0]:
        raise ValueError(
            f"primaryTarget {primary!r} must be the first entry of targets "
            f"({scenario.targets[0]!r})"
        )


def _check_step_target(
    step: Step, *, known: set[str], inside_web: bool, default: str | None
) -> None:
    context = f"step {_step_label(step)}"
    if inside_web:
        if step.target is not None:
            raise ValueError(
                f"{context}: target is not allowed on a step nested inside a "
                "web: or app: block (it always runs against the block's own device)"
            )
        step.resolve_target(None)
        return
    if step.use is not None and len(known) >= 2:
        # `expand_components` replaces this step wholesale with the component's own steps, so
        # `Step` refuses a `target` on it — yet two or more targets make one required. Refused
        # until a later BE-0428 unit decides whether/how `target` propagates into an expansion.
        raise ValueError(
            f"{context}: use: is not yet supported when the scenario declares "
            f"{len(known)} targets — a use: step cannot carry the target they require"
        )
    # Assigned even when None: a step copied from a scenario that resolved it (`apply_setups`'s
    # deep-copied prelude) must not keep that scenario's primary.
    step.resolve_target(_check_target(step.target, known=known, context=context, default=default))


def _reject_assertion_target(a: Assertion, *, context: str) -> None:
    # Only an expect entry may set target — it would otherwise restate or contradict the target
    # the enclosing step already fixes for an inline `assert:` list or an `if`'s `condition`.
    # `Interrupt.condition` carries no enclosing step of its own, but the proposal never names a
    # way for it to select a target either, so it is held to the same rule for now (BE-0428).
    if a.target is not None:
        raise ValueError(f"{context}: target is only allowed on a top-level expect entry")


def _check_target_requirements(scenario: Scenario) -> None:
    """Enforce `target`'s requirement against `len(scenario.targets)`, recursively.

    Zero or one declared targets: every step's/assertion's `target` must be omitted, or must name
    that one target. Two or more: every step — including an `if` / `forEach` / `web` / `app`
    wrapper, not only a leaf action — and every top-level `expect` entry must set `target`
    explicitly, naming one of the declared targets. A step nested inside a `web:` or `app:` block
    is the one exception — it must omit `target` outright, since it always runs against the device
    the enclosing block already resolved (`web:`'s own `WebContextDriver`, or `app:`'s unchanged
    native driver). An `Assertion` reached through an inline `assert:` list, an `if`'s
    `condition`, or an `interrupts` entry's `condition` must never set `target` — only one reached
    through the scenario's top-level `expect` block may. Two open questions this item has not yet
    resolved fail closed instead of guessing: a `use:` step (it takes no modifiers, so it cannot
    carry the `target` two targets require) and a non-empty `interrupts` (its `condition` has no
    target of its own to poll) are both refused outright once the scenario declares two or more
    targets.

    A scenario that sets `primaryTarget` (which must be `targets[0]`) lifts the two-or-more
    requirement (BE-0436): a step or top-level `expect` entry that omits `target` runs against the
    primary. A step records that on its private `resolved_target`, never on `target` itself, so a
    re-serialized step stays as terse as its author wrote it. The resolution is flat — a nested
    `if` / `forEach` step that omits `target` resolves to the primary, never to its wrapper's own.
    """
    known = set(scenario.targets)
    if len(known) != len(scenario.targets):
        raise ValueError(f"targets contains a duplicate name: {scenario.targets}")
    _check_primary_target(scenario)
    default = scenario.primary_target

    def walk_steps(steps: list[Step], *, inside_web: bool) -> None:
        for step in steps:
            _check_step_target(step, known=known, inside_web=inside_web, default=default)
            if step.assert_ is not None:
                for a in step.assert_:
                    _reject_assertion_target(a, context=f"step {_step_label(step)}: assert")
            if step.if_ is not None:
                _reject_assertion_target(
                    step.if_.condition, context=f"step {_step_label(step)}: if condition"
                )
                walk_steps(step.if_.then, inside_web=inside_web)
                if step.if_.else_ is not None:
                    walk_steps(step.if_.else_, inside_web=inside_web)
            if step.for_each is not None:
                walk_steps(step.for_each.steps, inside_web=inside_web)
            if step.web is not None:
                walk_steps(step.web.steps, inside_web=True)
            if step.app is not None:
                # `app:` reuses the same native driver throughout, unlike `web:`'s separate
                # `WebContextDriver` — but a nested step still always runs against the device the
                # enclosing step already routed to, so the same omit-`target` rule applies
                # (BE-0428).
                walk_steps(step.app.steps, inside_web=True)

    walk_steps(scenario.steps, inside_web=False)
    walk_steps(scenario.before, inside_web=False)
    for rule in scenario.after:
        walk_steps(rule.steps, inside_web=False)
    if scenario.interrupts and len(known) >= 2:
        # An `Interrupt` carries no enclosing step of its own to fix a target for its `condition`
        # the way an `if`'s condition has one — and its `condition` is barred from naming one
        # itself, the same as every other non-`expect` assertion. Which target's tree it should
        # poll is an open question a later BE-0428 unit resolves; refused outright for now rather
        # than accepted with no way to express it.
        raise ValueError(
            f"interrupts is not yet supported when the scenario declares {len(known)} targets — "
            "which target an interrupt's condition polls is an open question"
        )
    for entry in scenario.interrupts:
        walk_steps(entry.steps, inside_web=False)
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
    return sorted({s.name for s in scenarios if len(s.targets) >= 2})
