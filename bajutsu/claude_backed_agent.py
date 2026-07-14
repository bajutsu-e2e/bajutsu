"""Shared base for the Claude-backed agents and locators (BE-0246 Unit 3).

The seven `Claude*` classes (`ClaudeAgent`, the two triage agents, the enrichment agent, the
action proposer, and the tab/alert locators) each held the same backend/config plumbing: the
`_backend` / `_ai` / `_redactor` / `_model` attributes, a byte-identical lazy `_ensure_backend`, and
a `usage.record` call with the same provider/model arguments. This base holds that plumbing once so
each subclass keeps only what it genuinely adds (a language suffix, an effort, a token cap). It is
the shared home the pre-BE-0104 `anthropic_client.ensure_client` was meant to be before the neutral
`AiBackend` seam (BE-0104) changed the shape it wrapped.
"""

from __future__ import annotations

from bajutsu import usage
from bajutsu.ai import AiBackend, MessageResponse, create_backend, resolved_provider
from bajutsu.ai_config import AiConfig, resolve_model
from bajutsu.redaction import Redactor


class ClaudeBackedAgent:
    """Backend/usage plumbing shared by the vendor-neutral Claude-backed classes (BE-0104).

    Args:
        default_model: The class's model constant, resolved against `ai` unless `model` pins one.
        model: An explicit model id that overrides `default_model` resolution when given.
        redactor: Present for the six classes that mask secrets before the model; the tab locator
            sends no free text and leaves it ``None``.
    """

    def __init__(
        self,
        *,
        backend: AiBackend | None,
        ai: AiConfig | None,
        default_model: str,
        model: str | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self._backend = backend
        self._ai = ai
        self._redactor = redactor
        self._model = resolve_model(default_model, ai) if model is None else model

    def _ensure_backend(self) -> AiBackend:
        if self._backend is None:
            self._backend = create_backend(ai=self._ai)
        return self._backend

    def _record_usage(
        self, response: MessageResponse, category: str = usage.CATEGORY_OTHER
    ) -> None:
        """Record one response's token usage under `category`, attributed to this class's provider/model."""
        usage.record(
            response.usage, category, provider=resolved_provider(self._ai), model=self._model
        )
