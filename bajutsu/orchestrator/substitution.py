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
    return Step.model_validate(interp.interpolate(dumped, bindings))


def _resolve_system_alert(step: Step, locale: str | None) -> Step:
    """A copy of the step with `handleSystemAlert`'s `prompt`/`choice` turned into a `sel` (BE-0320).

    The label a SpringBoard prompt shows depends on the language the run pinned the Simulator to, so
    the intent form can only be resolved once the run's locale is known. Every other step — and a
    `handleSystemAlert` that already names its button through `sel` — returns unchanged.

    A caller with no locale (`record`'s replay) leaves the step unresolved; the handler then fails it
    loudly, rather than this guessing at a language.

    Raises:
        UncoveredSystemAlertLocale: no labels are known for that locale's language.
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
