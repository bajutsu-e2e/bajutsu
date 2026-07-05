"""Scenario spec models — the structure normalized from natural language.

Validated strictly with pydantic (extra="forbid" rejects unknown keys). The deterministic
runner executes this structure with no AI. The models are split by domain so a new action,
assertion, or evidence rule adds to one focused module instead of a single large file (BE-0043):

- ``_base``      — the strict pydantic base, shared validators, token grammars
- ``selector``   — how a step/assertion addresses an element
- ``actions``    — one-shot device actions (gestures, text, device control, http)
- ``assertions`` — machine checks and the wait conditions that reuse them
- ``steps``      — the step aggregator, the macro/extract modifiers, if/forEach control flow
- ``evidence``   — capturePolicy triggers/rules, redaction, network filter
- ``mocks``      — deterministic network stubs
- ``scenario``   — preconditions, the scenario, its component, the scenario-file wrapper

The full public API is re-exported here, so ``from bajutsu.scenario.models import X`` (and
``from bajutsu.scenario import X``) keep working unchanged.
"""

from __future__ import annotations

from bajutsu.scenario.models._base import Point
from bajutsu.scenario.models.actions import (
    Background,
    ClearClipboard,
    ClearKeychain,
    ClearStatusBar,
    Email,
    EmailExtract,
    EmailMatch,
    Foreground,
    HttpRequest,
    LongPress,
    OverrideStatusBar,
    Pinch,
    Push,
    Relaunch,
    Rotate,
    SetClipboard,
    SetLocation,
    Swipe,
    Totp,
    TypeText,
    VarTarget,
)
from bajutsu.scenario.models.assertions import (
    Assertion,
    ClipboardMatch,
    CountMatch,
    CountOp,
    EventMatch,
    ExcludeRegion,
    Exists,
    GoldenMatch,
    Gone,
    RequestMatch,
    ResponseSchemaMatch,
    SelectorRegion,
    TextMatch,
    VisualMatch,
    Wait,
    WaitRequest,
)
from bajutsu.scenario.models.evidence import (
    CaptureRule,
    Network,
    NetworkFilter,
    Redact,
    Trigger,
)
from bajutsu.scenario.models.mocks import Mock, MockResponse
from bajutsu.scenario.models.scenario import (
    Component,
    DismissAlerts,
    Preconditions,
    Scenario,
    ScenarioFile,
)
from bajutsu.scenario.models.selector import Selector
from bajutsu.scenario.models.steps import _STEP_ACTIONS as STEP_ACTIONS
from bajutsu.scenario.models.steps import Extract, ForEach, If, Step, Use, Web

__all__ = [
    "STEP_ACTIONS",
    "Assertion",
    "Background",
    "CaptureRule",
    "ClearClipboard",
    "ClearKeychain",
    "ClearStatusBar",
    "ClipboardMatch",
    "Component",
    "CountMatch",
    "CountOp",
    "DismissAlerts",
    "Email",
    "EmailExtract",
    "EmailMatch",
    "EventMatch",
    "ExcludeRegion",
    "Exists",
    "Extract",
    "ForEach",
    "Foreground",
    "GoldenMatch",
    "Gone",
    "HttpRequest",
    "If",
    "LongPress",
    "Mock",
    "MockResponse",
    "Network",
    "NetworkFilter",
    "OverrideStatusBar",
    "Pinch",
    "Point",
    "Preconditions",
    "Push",
    "Redact",
    "Relaunch",
    "RequestMatch",
    "ResponseSchemaMatch",
    "Rotate",
    "Scenario",
    "ScenarioFile",
    "Selector",
    "SelectorRegion",
    "SetClipboard",
    "SetLocation",
    "Step",
    "Swipe",
    "TextMatch",
    "Totp",
    "Trigger",
    "TypeText",
    "Use",
    "VarTarget",
    "VisualMatch",
    "Wait",
    "WaitRequest",
    "Web",
]
