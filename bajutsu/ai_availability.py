"""Is Claude reachable? — one answer across every provider and agent backend (BE-0101).

The provider registry's `credential_gap()` (BE-0104) answers availability for the **SDK** path (the
resolved provider's credential — the Anthropic key, or a Bedrock model id). But the `--agent
claude-code` backend reaches Claude through the `claude` CLI,
whose availability condition is different (the binary present). This module unifies the two behind
one resolver so the `serve` and `doctor` surfaces gate on a single helper and stay correct whichever
backend / provider a target selects — a thin generalization of the existing seam, not a new
subsystem.

Kept SDK-free at import time (it only forwards to `credential_gap`, which reads env, and probes the
CLI with `shutil.which`), so it stays on the zero-config deterministic path (BE-0101).
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping

from bajutsu.agents import AGENT_ENV
from bajutsu.ai import credential_gap
from bajutsu.anthropic_client import AiConfig, key_env

Which = Callable[[str], str | None]

CLAUDE_BINARY = "claude"

# The gap tokens this resolver can return, on top of credential_gap's "anthropic-key" /
# "bedrock-model": the Claude Code CLI backend is unreachable because its binary is absent.
CLAUDE_CODE_MISSING = "claude-code-missing"


def availability(
    *,
    agent_kind: str,
    ai: AiConfig | None = None,
    which: Which = shutil.which,
) -> str | None:
    """What Claude is missing for the resolved backend / provider, or None when it is reachable.

    Args:
        agent_kind: the resolved authoring backend (`agents.resolve_kind()` — "api" or "claude-code").
        ai: the resolved `ai` config block (BE-0047), consulted for the SDK path's provider / key.
        which: binary-lookup probe, injectable so the CLI-backend check is testable without a real
            `claude` install.

    Returns:
        A gap token — `"anthropic-key"` / `"bedrock-model"` (SDK path, from `credential_gap`) or
        `"claude-code-missing"` (CLI backend) — or None when Claude can be reached. The `claude-code`
        backend is judged by binary presence only: its login state is stored outside a stable public
        contract, and a mistaken "not logged in" verdict would disable a working setup, so a genuine
        auth failure is left to surface loudly at call time instead of being guessed here.
    """
    if agent_kind == "claude-code":
        return None if which(CLAUDE_BINARY) is not None else CLAUDE_CODE_MISSING
    return credential_gap(ai)


def message(gap: str, ai: AiConfig | None = None) -> str:
    """A specific, actionable one-liner for a gap from `availability`, for `serve` / `doctor`."""
    if gap == CLAUDE_CODE_MISSING:
        return (
            f"the Claude Code CLI is not installed — install `{CLAUDE_BINARY}`, "
            "or switch to the Anthropic API / Bedrock provider."
        )
    if gap == "bedrock-model":
        return (
            "the Bedrock provider needs a provider-prefixed model id "
            "(set ai.model, or $BAJUTSU_BEDROCK_MODEL); AWS credentials authenticate it."
        )
    return f"set ${key_env(ai)} (the Anthropic API key), configure Bedrock, or sign in to the Claude Code CLI."


def from_env(
    env: Mapping[str, str],
    *,
    ai: AiConfig | None = None,
    which: Which = shutil.which,
) -> str | None:
    """`availability` for the `serve` process, resolving the backend from its environment.

    `serve` sets `$BAJUTSU_AGENT` from the Settings AI-provider selector, so a spawned `record` /
    `crawl` job inherits one choice; this reads the same signal to report what that job would face.
    """
    agent_kind = env.get(AGENT_ENV) or "api"
    return availability(agent_kind=agent_kind, ai=ai, which=which)
