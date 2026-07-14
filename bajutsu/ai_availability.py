"""Turn a provider credential gap into an actionable message for `serve` / `doctor` (BE-0101).

The provider registry's `credential_gap()` (BE-0104) answers *whether* Claude is reachable for the
resolved provider — the Anthropic key, a Bedrock model id, the `ant` CLI's sign-in (BE-0163), or the
`claude-code` CLI. Callers read that gap directly from `bajutsu.ai.credential_gap`; this module turns
the returned token into a specific, actionable one-liner, so the `serve` and `doctor` surfaces render
the same reason whichever provider a target selects (BE-0246 dropped the former `availability`
passthrough, which only forwarded to `credential_gap`).

Kept SDK-free at import time (it only maps tokens to strings), so it stays on the zero-config
deterministic path (BE-0101).
"""

from __future__ import annotations

from bajutsu.ai.claude_code import CLI_MISSING as CLAUDE_CODE_CLI_MISSING
from bajutsu.ai_config import AiConfig
from bajutsu.anthropic_client import ANT_CLI_MISSING, ANT_CLI_UNAUTHENTICATED, key_env


def message(gap: str, ai: AiConfig | None = None) -> str:
    """A specific, actionable one-liner for a gap from `bajutsu.ai.credential_gap`, for `serve` / `doctor`."""
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
