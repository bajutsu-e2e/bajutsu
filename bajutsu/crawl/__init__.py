"""Autonomous crawl engine (BE-0038) — breadth-first exploration over the `Driver` abstraction.

`core` is the deterministic engine (screen identity, candidate actions, the walk and the
coordinator); `serialize` turns a run's `ScreenMap`/`Action` into a JSON-friendly dict and back;
`flows` / `report` / `repro` render a crawl's output, `tabs` locates vision-only tab bars, and
`guide` is the AI proposer (periphery — never on the verdict path, prime directive #1). The public
API is re-exported here, so `from bajutsu.crawl import ScreenMap, crawl, screenmap_dict, …` is
unchanged after the flat `crawl*` modules became this package (BE-0257).
"""

from __future__ import annotations

from bajutsu.crawl.core import (
    ACTIONABLE_TRAITS,
    INPUT_TRAITS,
    TAP_TRAITS,
    Action,
    Alert,
    AliveCheck,
    ClearBlocking,
    Crash,
    Edge,
    Fingerprint,
    Guide,
    GuideContext,
    Node,
    OnEvent,
    OnNode,
    Pruned,
    Recover,
    Reset,
    ScreenMap,
    Settle,
    WorkerFactory,
    blocked_controls,
    candidate_actions,
    crawl,
    fingerprint,
    is_app_alive,
    screen_identity,
)
from bajutsu.crawl.serialize import (
    action_from_dict,
    action_to_dict,
    screenmap_dict,
    screenmap_from_dict,
)

__all__ = [
    "ACTIONABLE_TRAITS",
    "INPUT_TRAITS",
    "TAP_TRAITS",
    "Action",
    "Alert",
    "AliveCheck",
    "ClearBlocking",
    "Crash",
    "Edge",
    "Fingerprint",
    "Guide",
    "GuideContext",
    "Node",
    "OnEvent",
    "OnNode",
    "Pruned",
    "Recover",
    "Reset",
    "ScreenMap",
    "Settle",
    "WorkerFactory",
    "action_from_dict",
    "action_to_dict",
    "blocked_controls",
    "candidate_actions",
    "crawl",
    "fingerprint",
    "is_app_alive",
    "screen_identity",
    "screenmap_dict",
    "screenmap_from_dict",
]
