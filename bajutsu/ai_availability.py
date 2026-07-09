"""Is Claude reachable? — one answer across every provider (BE-0101).

The provider registry's `credential_gap()` (BE-0104) answers availability for the SDK path: the
resolved provider's credential — the Anthropic key, a Bedrock model id, or the `ant` CLI's
sign-in (BE-0163). Every AI entry point now resolves through one `ai.provider`, so this module is a
thin wrapper that turns a gap token into a specific, actionable message for the `serve` and
`doctor` surfaces — they gate on one helper and stay correct whichever provider a target selects.

Kept SDK-free at import time (it only forwards to `credential_gap`, which reads env / probes the
`ant` CLI), so it stays on the zero-config deterministic path (BE-0101).
"""

from __future__ import annotations

from bajutsu.ai import credential_gap
from bajutsu.ai.claude_code import CLI_MISSING as CLAUDE_CODE_CLI_MISSING
from bajutsu.anthropic_client import ANT_CLI_MISSING, ANT_CLI_UNAUTHENTICATED, AiConfig, key_env


def availability(ai: AiConfig | None = None) -> str | None:
    """What Claude is missing for the resolved provider, or None when it is reachable.

    Args:
        ai: the resolved `ai` config block (BE-0047), consulted for the provider / credential.

    Returns:
        A gap token from `credential_gap` — ``"anthropic-key"`` / ``"bedrock-model"`` /
        ``"ant-cli-missing"`` / ``"ant-cli-unauthenticated"`` / ``"claude-code-cli-missing"`` — or
        None when Claude can be reached.
    """
    return credential_gap(ai)


def message(gap: str, ai: AiConfig | None = None) -> str:
    """A specific, actionable one-liner for a gap from `availability`, for `serve` / `doctor`."""
    if gap == CLAUDE_CODE_CLI_MISSING:
        return (
            "the Claude Code CLI (`claude`) is not installed — install Claude Code and sign in "
            "(`claude setup-token`, or an interactive login; on a headless host set "
            "$CLAUDE_CODE_OAUTH_TOKEN to a token minted elsewhere), or switch to the Anthropic API / "
            "Bedrock / ant provider."
        )
    if gap == ANT_CLI_MISSING:
        return (
            "the Anthropic CLI (`ant`) is not installed — install it and run `ant auth login`, "
            "or switch to the Anthropic API / Bedrock provider."
        )
    if gap == ANT_CLI_UNAUTHENTICATED:
        # Also returned when the token probe fails to exec or times out, not only a genuine
        # sign-out — so the wording covers both, with `ant auth login` as the primary fix.
        return (
            "the Anthropic CLI (`ant`) has no active credential or could not be read — "
            "run `ant auth login`."
        )
    if gap == "bedrock-model":
        return (
            "the Bedrock provider needs a provider-prefixed model id "
            "(set ai.model, or $BAJUTSU_BEDROCK_MODEL); AWS credentials authenticate it."
        )
    return f"set ${key_env(ai)} (the Anthropic API key), configure Bedrock, or sign in with `ant auth login`."
