"""Runtime rewrites of a step before it executes: ${...} token substitution, and locale resolution.

Only the executed copy sees the real value; the caller keeps the original for the
manifest/report, so the recorded scenario shows the token, never the secret."""

from __future__ import annotations

from collections.abc import Mapping

from bajutsu.common.scenario import Assertion, Step, interp


def _interp_step(step: Step, bindings: Mapping[str, str]) -> Step:
    """A copy of the step with ${...} tokens (e.g. secrets.*) substituted, for execution.

    Only the executed action sees the real value — the caller keeps the original step for
    the manifest/report, so the recorded scenario shows the token, never the secret."""
    if not bindings:
        return step
    # Fast path: model_dump_json() is Rust-backed in Pydantic v2 and much cheaper than
    # model_dump() (which builds Python dicts). Most steps contain no tokens at all, so
    # a quick substring check on the JSON avoids the heavier serialisation + walk.
    if "${" not in step.model_dump_json(by_alias=True, exclude_none=True):
        return step
    dumped = step.model_dump(by_alias=True, exclude_none=True)
    if not interp.find_tokens(dumped) & bindings.keys():
        return step
    interpolated = interp.interpolate(dumped, bindings)
    # `eval` substitutes its own tokens (BE-0472): a substituted argument is re-checked by the
    # handler, which fails the step with a message that quotes no value, where re-validating it here
    # would raise a ValidationError carrying the substituted (possibly secret) text. The step runner
    # also reads the written form to decide whether the result derives from a secret.
    if "eval" in dumped:
        interpolated["eval"] = dumped["eval"]
    # `push` follows the same rule (BE-0498): the model validates the authored payload, and the
    # substituted one is attached afterward, so a ValidationError never quotes a substituted value.
    # The `push` handler re-measures the substituted payload against the size limit. `parseJson`
    # (BE-0516) is restored the same way and substitutes its own tokens in its handler, one path
    # segment at a time. Either one nested in a control-flow body (`forEach`, `if`, `group`, …) is
    # restored too and left authored: each nested step comes back through here when it runs, and is
    # substituted then.
    pushed = interpolated.get("push")
    _restore_authored(interpolated, dumped)
    result = Step.model_validate(interpolated)
    if pushed is not None:
        assert result.push is not None  # the authored `push` was put back above
        result = result.model_copy(
            update={"push": result.push.model_copy(update={"payload": pushed["payload"]})}
        )
    return result


# The actions `_restore_authored` puts back, each with the keys that identify its object.
_AUTHORED_ACTIONS = {"push": {"payload"}, "parseJson": {"from", "extract"}}


def _restore_authored(interpolated: object, authored: object) -> None:
    """Put the authored `push` / `parseJson` back wherever one sits in *interpolated*, at any depth.

    `interp.interpolate` rewrites values only, so *interpolated* has *authored*'s exact shape and the
    two walk in step. Each action is an object with exactly the keys listed (the models forbid
    extras), which keeps a same-named key in other data, such as a mock's JSON body, substituted.
    """
    if isinstance(interpolated, dict) and isinstance(authored, dict):
        for key, value in interpolated.items():
            original = authored.get(key)
            keys = _AUTHORED_ACTIONS.get(key)
            if keys is not None and isinstance(original, dict) and original.keys() == keys:
                interpolated[key] = original
            else:
                _restore_authored(value, original)
    elif isinstance(interpolated, list) and isinstance(authored, list):
        for item, original in zip(interpolated, authored, strict=False):
            _restore_authored(item, original)


def _resolve_system_alert(step: Step, locale: str | None) -> Step:
    """A copy of the step with `handleSystemAlert`'s `prompt`/`choice` turned into a `sel` (BE-0320).

    The label a SpringBoard prompt shows depends on the language the run pinned the Simulator to, so
    the intent form can only be resolved once the run's locale is known. Every other step — and a
    `handleSystemAlert` that already names its button through `sel` — returns unchanged.

    A caller with no locale (`record`'s replay) leaves the step unresolved, rather than this guessing
    at a language; the handler then answers by the prompt's position rule (BE-0445), or fails the
    step loudly for a prompt that has none.

    Raises:
        UncoveredSystemAlertLocale: no labels are known for that locale's language, and the prompt
            has no position rule to fall back to.
    """
    hsa = step.handle_system_alert
    if hsa is None or hsa.prompt is None or locale is None:
        return step
    return step.model_copy(update={"handle_system_alert": hsa.resolved(locale)})


def _interp_asserts(asserts: list[Assertion], bindings: Mapping[str, str]) -> list[Assertion]:
    """Substitute ${...} tokens in a list of assertions (for scenario-level `expect`)."""
    if not bindings:
        return asserts
    # Fast path: if no assertion contains a token marker, skip the whole list.
    if not any("${" in a.model_dump_json(by_alias=True, exclude_none=True) for a in asserts):
        return asserts
    return [
        Assertion.model_validate(
            interp.interpolate(a.model_dump(by_alias=True, exclude_none=True), bindings)
        )
        for a in asserts
    ]
