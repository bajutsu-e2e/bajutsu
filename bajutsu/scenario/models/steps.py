"""The step: exactly one action plus optional modifiers.

Includes the macro (`use`), the runtime variable capture (`extract`), and the deterministic
control-flow steps (`if`/`forEach`) whose nested step lists make this the one module where a
forward reference to `Step` is needed.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from bajutsu.scenario.models._base import (
    _CONTROL_FLOW_ACTIONS,
    _exactly_one,
    _Model,
    _validate_capture,
)
from bajutsu.scenario.models.actions import (
    Background,
    ClearClipboard,
    ClearKeychain,
    ClearStatusBar,
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
)
from bajutsu.scenario.models.assertions import Assertion, Wait
from bajutsu.scenario.models.selector import Selector


class Use(_Model):
    """Invoke a reusable component, substituting its declared params with `with`.

    The `use` step is expanded away (replaced by the component's steps) before the run, so it is a
    compile-time macro, not a runtime action — determinism is unaffected.
    """

    component: str
    with_: dict[str, str] = Field(default_factory=dict, alias="with")


class Extract(_Model):
    """Capture a UI element's property into a runtime variable (``vars.*``)."""

    sel: Selector
    prop: Literal["value", "label", "identifier"] = "value"


class If(_Model):
    """Conditional execution.

    Evaluate an assertion as the condition, then run ``then`` steps if it passes or ``else`` steps
    otherwise.
    """

    condition: Assertion
    then: list[Step] = Field(default_factory=list)
    else_: list[Step] | None = Field(default=None, alias="else")


class ForEach(_Model):
    """Iterate over elements matching a selector.

    Each element's identifier is stored as ``vars.<as>`` and the nested steps are executed.
    """

    sel: Selector
    as_: str = Field(alias="as")
    steps: list[Step] = Field(default_factory=list)


class Step(_Model):
    """One action plus optional modifiers (capture / name / extract)."""

    tap: Selector | None = None
    double_tap: Selector | None = Field(default=None, alias="doubleTap")
    long_press: LongPress | None = Field(default=None, alias="longPress")
    type: TypeText | None = None
    swipe: Swipe | None = None
    pinch: Pinch | None = None
    rotate: Rotate | None = None
    wait: Wait | None = None
    assert_: list[Assertion] | None = Field(default=None, alias="assert")
    relaunch: Relaunch | None = None
    set_location: SetLocation | None = Field(default=None, alias="setLocation")
    push: Push | None = None
    use: Use | None = None
    http: HttpRequest | None = None
    totp: Totp | None = None
    clear_keychain: ClearKeychain | None = Field(default=None, alias="clearKeychain")
    clear_clipboard: ClearClipboard | None = Field(default=None, alias="clearClipboard")
    set_clipboard: SetClipboard | None = Field(default=None, alias="setClipboard")
    background: Background | None = None
    foreground: Foreground | None = None
    override_status_bar: OverrideStatusBar | None = Field(default=None, alias="overrideStatusBar")
    clear_status_bar: ClearStatusBar | None = Field(default=None, alias="clearStatusBar")
    if_: If | None = Field(default=None, alias="if")
    for_each: ForEach | None = Field(default=None, alias="forEach")
    capture: list[str] | None = None
    extract: dict[str, Extract] | None = None
    name: str | None = None
    # Provenance (BE-0044): the natural-language phrase `record` normalized this step from. Pure
    # authoring metadata — `run` never reads it. A modifier, not an action, so it doesn't disturb
    # the one-action rule; allowed on every step, control-flow included.
    from_: str | None = Field(default=None, alias="from")

    @field_validator("capture")
    @classmethod
    def _cap(cls, v: list[str] | None) -> list[str] | None:
        return _validate_capture(v) if v is not None else v

    @model_validator(mode="after")
    def _one_action(self) -> Self:
        _exactly_one(self, _STEP_ACTIONS, "§6.2")
        return self

    @model_validator(mode="after")
    def _no_modifiers_on_control_flow(self) -> Self:
        action = next((a for a in _CONTROL_FLOW_ACTIONS if getattr(self, a) is not None), None)
        if action is not None:
            if self.capture is not None:
                raise ValueError(f"capture is not supported on {action} steps")
            if self.extract is not None:
                raise ValueError(f"extract is not supported on {action} steps")
        return self


If.model_rebuild()
ForEach.model_rebuild()

# The action field names, derived from the model so a new action is declared in exactly one
# place — adding a `Step` field — instead of also appending to a parallel hand-maintained tuple
# (a per-action merge-conflict point). `_MODIFIERS` are the non-action fields.
_MODIFIERS = ("capture", "extract", "name", "from_")
_STEP_ACTIONS = tuple(f for f in Step.model_fields if f not in _MODIFIERS)
