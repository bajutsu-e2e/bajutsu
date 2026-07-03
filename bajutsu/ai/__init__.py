"""Vendor-neutral AI backend seam (BE-0104).

Bajutsu treats a platform as a backend behind one `Driver` interface; the same idea applies to
the AI paths — *an AI provider is a backend behind one interface*. The Tier-1 authoring /
investigation paths (`record`, `triage`, `--dismiss-alerts`, `crawl`, MCP enrich) talk to a model
only through the `AiBackend` protocol and the normalized request / response types defined in
`base`, so a Claude-using feature can be re-pointed at a different model family without touching its
call site. `anthropic` is the reference adapter; `registry` is the name → adapter extension point.

The seam's model call (`AiBackend.create_message`) never runs on the deterministic `run` / CI gate
(DESIGN §2 / §3.1) — it is reached only from Tier-1 authoring / investigation paths. Two cheap,
model-free lookups *are* imported more broadly: `bajutsu.config` validates `ai.provider` against
`known_providers()` for every command, and `run --dismiss-alerts`'s alert guard (itself a Tier-1
path within `run`) calls `credential_gap` to decide whether to construct the vision locator at all.
Neither calls a model or bears on pass/fail.
"""

from __future__ import annotations

from bajutsu.ai.base import (
    AiBackend,
    AnyTool,
    ContentBlock,
    ContentPart,
    ImagePart,
    Message,
    MessageRequest,
    MessageResponse,
    NamedTool,
    TextBlock,
    TextPart,
    ToolChoice,
    ToolDef,
    ToolUseBlock,
)
from bajutsu.ai.registry import create_backend, credential_gap, known_providers, register

__all__ = [
    "AiBackend",
    "AnyTool",
    "ContentBlock",
    "ContentPart",
    "ImagePart",
    "Message",
    "MessageRequest",
    "MessageResponse",
    "NamedTool",
    "TextBlock",
    "TextPart",
    "ToolChoice",
    "ToolDef",
    "ToolUseBlock",
    "create_backend",
    "credential_gap",
    "known_providers",
    "register",
]
