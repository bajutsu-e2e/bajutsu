"""The step: exactly one action plus optional modifiers.

Includes the macro (`use`), the runtime variable capture (`extract`), and the deterministic
control-flow steps (`if`/`forEach`) whose nested step lists make this the one module where a
forward reference to `Step` is needed.
"""

from ._shared import _MODIFIERS as _MODIFIERS
from .after_rule import AfterRule
from .app import App
from .extract import Extract
from .for_each import ForEach
from .if_ import If
from .interrupt import Interrupt
from .step import _STEP_ACTIONS as _STEP_ACTIONS
from .step import _UNSAFE_STEP_NAME_CHARS as _UNSAFE_STEP_NAME_CHARS
from .step import Step
from .use import Use
from .web import Web

__all__ = ["AfterRule", "App", "Extract", "ForEach", "If", "Interrupt", "Step", "Use", "Web"]


If.model_rebuild()
ForEach.model_rebuild()
Interrupt.model_rebuild()
Web.model_rebuild()
App.model_rebuild()
# `Step` joins them because the split made its own reference to `Web` a deferred one: the two
# modules reference each other, so rule 5 broke that edge, and Pydantic resolves the annotation
# here, where every name is in scope.
Step.model_rebuild()
