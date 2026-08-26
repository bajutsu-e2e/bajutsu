"""The `none` provider — a registered adapter that disables every AI path (BE-0394).

Writing ``ai: { provider: none }`` turns a policy that today lives only in the *absence* of a
credential into a committed, reviewable statement: no AI path may run for this repository. The
contribution is fail-closed intent, not a new capability — an unset key already produces most of the
same behavior, but it lives nowhere a reviewer can read and a key exported for `record` silently
re-enables the vision alert fallback for every `run` in the same shell.

The adapter is deliberately inert, and the raise is what makes the setting fail closed rather than
merely quiet: `bajutsu.ai.registry.create_backend` is the single construction seam every AI path
reaches, so a call site that skipped the credential check gets an exception instead of a silent round
trip. The deterministic native alert path (BE-0315) is untouched — it needs no credential, so a `run`
under this provider keeps clearing the prompts it can and only loses the vision fallback.
"""

from __future__ import annotations

from bajutsu.agents.ai_config import AiConfig
from bajutsu.ai.base import AiBackend

# The credential-gap token this provider reports. Every consumer of `credential_gap` already fails
# closed on a non-None value (BE-0047), so registering the provider gives each surface the right
# behavior without a new branch of its own — only the wording differs (see the tables that map it).
DISABLED = "ai-disabled"


def factory(ai: AiConfig | None = None) -> AiBackend:  # noqa: ARG001  # registry factory shape
    """Refuse to build a backend — the kill switch's fail-closed half (BE-0394).

    Raises:
        RuntimeError: always; the resolved provider is `none`.
    """
    raise RuntimeError(
        "AI is disabled for this target (ai.provider: none): no AI backend can be constructed. "
        "Select a provider (api-key / bedrock / ant / claude-code) to use the AI paths."
    )


def credential_gap(ai: AiConfig | None = None) -> str | None:  # noqa: ARG001  # registry probe shape
    """Always `DISABLED` — the provider can never authenticate, by construction (BE-0394)."""
    return DISABLED


def announce(
    ai: AiConfig | None,  # noqa: ARG001  # announce shape
    provider: str,
    default_model: str,  # noqa: ARG001  # announce shape
) -> list[str]:
    """This provider's startup disclosure — no model name, since none will ever be used.

    No command reaches it today: `record` and `crawl` are the only announcing paths, and both fail
    closed on the credential gap first. It exists so the default disclosure — which would name a
    resolved model — can never reach a surface that announces before checking the gap.
    """
    return [f"🤖 AI: disabled (ai.provider: {provider})"]
