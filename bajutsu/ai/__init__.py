"""Vendor-neutral AI backend seam (BE-0104).

Bajutsu treats a platform as a backend behind one `Driver` interface; the same idea applies to
the AI paths — *an AI provider is a backend behind one interface*. The Tier-1 authoring /
investigation paths (`record`, `triage`, `--alert-handling`, `crawl`, MCP enrich) talk to a model
only through the `AiBackend` protocol and the normalized request / response types defined in
`base`, so a Claude-using feature can be re-pointed at a different model family without touching its
call site. `anthropic` is the reference adapter; `registry` is the name → adapter extension point.

The seam's model call (`AiBackend.create_message`) never runs on the deterministic `run` / CI gate
(DESIGN §2 / §3.1) — it is reached only from Tier-1 authoring / investigation paths. The
deterministic core does not import this seam at all: the layer-boundary gate (BE-0112) forbids it,
so `bajutsu.config` accepts an `ai.provider` name without validating it here. An unknown provider
fails closed only when a Tier-1 path first resolves it through the registry (`create_backend` /
`credential_gap`, via `registry._provider_name`), not at config load. The one broadly-imported entry
point is that model-free `credential_gap` lookup — `run --alert-handling`'s alert guard (itself a
Tier-1 path within `run`) calls it to decide whether to construct the vision locator at all. It
calls no model and bears on pass/fail nowhere.
"""

from __future__ import annotations

from bajutsu.ai.banner import announce_ai
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
from bajutsu.ai.registry import (
    create_backend,
    credential_gap,
    known_providers,
    register,
    resolved_provider,
)

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
    "announce_ai",
    "create_backend",
    "credential_gap",
    "known_providers",
    "register",
    "resolved_provider",
]
