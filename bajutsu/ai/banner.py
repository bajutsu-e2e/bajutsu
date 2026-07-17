"""One place every AI path announces which provider it started (BE-0176 follow-up).

Each Tier-1 authoring / investigation command (`record`, `crawl`, …) discloses the AI it is about to
drive before it hands work to a model, so a watcher never sees a provider start silently. *What* gets
disclosed is provider-specific — the Anthropic SDK names only provider and model, while `claude-code`
also surfaces the reasoning effort it honors and its forced-subscription auth mode — so the content
lives with each provider's adapter (`registry.announcement` → the adapter's `announce`). This module
is only the thin sink that pushes those lines through the caller's own progress stream (stderr for the
CLI, the merged crawl log for the web UI).
"""

from __future__ import annotations

from collections.abc import Callable

from bajutsu.agents.ai_config import AiConfig
from bajutsu.ai.registry import announcement


def announce_ai(
    report: Callable[[str], None],
    *,
    default_model: str,
    ai: AiConfig | None = None,
) -> None:
    """Report the resolved provider's own startup disclosure lines.

    Args:
        report: Sink for each disclosure line (the command's own progress stream — stderr for the
            CLI, the merged crawl log for the web UI).
        default_model: The command's primary-agent model id, resolved per provider so a configured
            ``ai.model`` / provider-specific override still wins.
        ai: The effective AI config; ``None`` resolves everything from the environment defaults.
    """
    for line in announcement(default_model, ai):
        report(line)
