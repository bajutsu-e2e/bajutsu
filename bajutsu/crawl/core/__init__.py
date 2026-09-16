"""Autonomous crawl engine core (BE-0038).

Breadth-first exploration of an app over the `Driver` abstraction, producing a screen map of
the reachable screens and the transitions between them. This is the deterministic engine only —
no AI and no Simulator wiring (those land in later slices). The determinism boundary is the
whole point: a screen's *identity* (its fingerprint) and the *order* in which candidate actions
are tried are both pure functions of the element tree, so a crawl of an unchanged app explores
the same way as far as the app's own non-determinism allows. AI never decides anything here.

Traversal is a **forward walk with deterministic replay for backtracking**: app transitions are
usually irreversible, so the engine keeps acting on the screen it is already on until that screen
has no untried action left, then — only to reach another unexplored screen — resets to a clean
state and replays a recorded path to it (the same way `run` reaches any state). Walking forward
avoids paying a reset/replay for every single action. Every edge is still a replayable step, and
every node keeps a recorded path to it.

The engine scales out across **N booted simulators** (BE-0064): a *coordinator* owns the shared
screen map, frontier and budgets under one lock, while *workers* each drive their own simulator,
taking frontier entries, exploring them, and running the guide on the screen they land on — so the
guide's AI round-trips overlap across devices, the primary speedup. What parallelism relaxes is
only the *exploration order* and the recorded canonical `path_to` (which worker reaches a screen
first is scheduling-dependent); screen identity, transition/crash detection and the map's content
stay pure deterministic functions of the element tree, so the crawl is never a verdict. A single
worker (the default) walks exactly as the serial engine always did.
"""

from ._coordinator import _Coordinator as _Coordinator
from ._functions import _FRAME_BUCKET as _FRAME_BUCKET
from ._functions import _LEGACY_TYPED_ENTRY as _LEGACY_TYPED_ENTRY
from ._functions import _MAX_WORKER_DEVICE_ERRORS as _MAX_WORKER_DEVICE_ERRORS
from ._functions import _MIN_IDS_FOR_ID_FINGERPRINT as _MIN_IDS_FOR_ID_FINGERPRINT
from ._functions import _STATE_TRAITS as _STATE_TRAITS
from ._functions import (
    ACTIONABLE_TRAITS,
    INPUT_TRAITS,
    TAP_TRAITS,
    AliveCheck,
    AppCrashCapture,
    ClearBlocking,
    Guide,
    OnNode,
    Recover,
    Reset,
    Settle,
    WorkerFactory,
    blocked_controls,
    candidate_actions,
    crawl,
    fingerprint,
    is_app_alive,
    plan_key,
    screen_identity,
    value_for_field,
)
from ._functions import _action_rect as _action_rect
from ._functions import _action_targets as _action_targets
from ._functions import _bbox as _bbox
from ._functions import _deterministic_guide as _deterministic_guide
from ._functions import _fingerprint_token as _fingerprint_token
from ._functions import _frame_bucket as _frame_bucket
from ._functions import _hash as _hash
from ._functions import _id_of as _id_of
from ._functions import _input_value as _input_value
from ._functions import _is_enabled as _is_enabled
from ._functions import _logger as _logger
from ._functions import _node_of as _node_of
from ._functions import _reduce as _reduce
from ._functions import _replay as _replay
from ._functions import _traits as _traits
from ._shared import OnEvent
from ._work import _Work as _Work
from .action import Action
from .alert import Alert
from .crash import Crash
from .edge import Edge
from .fingerprint import Fingerprint
from .guide_context import GuideContext
from .node import Node
from .pruned import Pruned
from .screen_map import ScreenMap

__all__ = [
    "ACTIONABLE_TRAITS",
    "INPUT_TRAITS",
    "TAP_TRAITS",
    "Action",
    "Alert",
    "AliveCheck",
    "AppCrashCapture",
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
    "blocked_controls",
    "candidate_actions",
    "crawl",
    "fingerprint",
    "is_app_alive",
    "plan_key",
    "screen_identity",
    "value_for_field",
]
