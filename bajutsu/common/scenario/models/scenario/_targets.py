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


def _check_target(target: str | None, *, known: set[str], context: str) -> None:
    # One rule for both surfaces that may select a target — a step and an `expect` entry — with
    # *context* naming the offender ("step 'tap login'", "expect entry").
    n = len(known)
    if n >= 2:
        if target is None:
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


def _check_step_target(step: Step, *, known: set[str], inside_web: bool) -> None:
    context = f"step {_step_label(step)}"
    if inside_web:
        if step.target is not None:
            raise ValueError(
                f"{context}: target is not allowed on a step nested inside a "
                "web: block (it always runs against the block's own resolved target)"
            )
        return
    if step.use is not None and len(known) >= 2:
        # `expand_components` replaces this step wholesale with the component's own steps,
        # discarding this step's own `target` — a component author's steps would then decide the
        # target instead of the value this `use:` step names, silently, rather than the required
        # field it looks like. Refused until a later BE-0428 unit decides whether/how `target`
        # propagates into an expansion.
        raise ValueError(
            f"{context}: use: is not yet supported when the scenario declares "
            f"{len(known)} targets — its own target would be discarded by expansion"
        )
    _check_target(step.target, known=known, context=context)


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
    that one target. Two or more: every step — including an `if` / `forEach` / `web` wrapper, not
    only a leaf action — and every top-level `expect` entry must set `target` explicitly, naming
    one of the declared targets. A step nested inside a `web:` block is the one exception — it must
    omit `target` outright, since it always runs against the target the enclosing `web:` step
    already resolved. An `Assertion` reached through an inline `assert:` list, an `if`'s
    `condition`, or an `interrupts` entry's `condition` must never set `target` — only one reached
    through the scenario's top-level `expect` block may. Two open questions this item has not yet
    resolved fail closed instead of guessing: a `use:` step (its own `target` would be discarded by
    expansion) and a non-empty `interrupts` (its `condition` has no target of its own to poll) are
    both refused outright once the scenario declares two or more targets.
    """
    known = set(scenario.targets)
    if len(known) != len(scenario.targets):
        raise ValueError(f"targets contains a duplicate name: {scenario.targets}")

    def walk_steps(steps: list[Step], *, inside_web: bool) -> None:
        for step in steps:
            _check_step_target(step, known=known, inside_web=inside_web)
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
        _check_target(a.target, known=known, context="expect entry")


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
