"""The step: exactly one action plus optional modifiers.

Includes the macro (`use`), the runtime variable capture (`extract`), and the deterministic
control-flow steps (`if`/`forEach`) whose nested step lists make this the one module where a
forward reference to `Step` is needed.
"""

from ._shared import _MODIFIERS as _MODIFIERS
from .app import App
from .cleanup_rule import CleanupRule
from .extract import Extract
from .for_each import ForEach
from .group import Group
from .if_ import If
from .interrupt import (
    Interrupt,
    InterruptEntry,
    SystemAlertInterrupt,
    TipKitInterrupt,
    check_interrupt_entries,
    screen_entries,
    system_alert_entries,
    tip_kit_entry,
)
from .step import _STEP_ACTIONS as _STEP_ACTIONS
from .step import _UNSAFE_STEP_NAME_CHARS as _UNSAFE_STEP_NAME_CHARS
from .step import Step
from .use import Use
from .web import Web

__all__ = [
    "App",
    "CleanupRule",
    "Extract",
    "ForEach",
    "Group",
    "If",
    "Interrupt",
    "InterruptEntry",
    "Step",
    "SystemAlertInterrupt",
    "TipKitInterrupt",
    "Use",
    "Web",
    "check_interrupt_entries",
    "screen_entries",
    "system_alert_entries",
    "tip_kit_entry",
]


If.model_rebuild()
ForEach.model_rebuild()
Group.model_rebuild()
Interrupt.model_rebuild()
SystemAlertInterrupt.model_rebuild()
TipKitInterrupt.model_rebuild()
Web.model_rebuild()
App.model_rebuild()
# `Step` joins them because the split made its own reference to `Web` a deferred one: the two
# modules reference each other, so rule 5 broke that edge, and Pydantic resolves the annotation
# here, where every name is in scope.
Step.model_rebuild()
