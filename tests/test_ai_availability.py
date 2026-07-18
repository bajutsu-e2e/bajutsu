"""The gap-token → actionable message mapper for `serve` / `doctor` (BE-0101).

Whether Claude is reachable is answered by `bajutsu.ai.credential_gap` (covered in
`test_ai_backend.py`); this module only phrases each gap token into an actionable one-liner (BE-0246
dropped the former `availability` passthrough).
"""

from __future__ import annotations

import pytest

from bajutsu.agents import availability as ai_availability
from bajutsu.agents.anthropic_client import ANT_CLI_MISSING, ANT_CLI_UNAUTHENTICATED


@pytest.mark.parametrize(
    "gap",
    ["anthropic-key", "bedrock-model", ANT_CLI_MISSING, ANT_CLI_UNAUTHENTICATED],
)
def test_message_is_specific_and_actionable(gap: str) -> None:
    msg = ai_availability.message(gap)
    assert msg and not msg.endswith(gap)  # a phrased message, not the raw token
