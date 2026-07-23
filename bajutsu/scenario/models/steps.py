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
    Back,
    Background,
    Clear,
    ClearClipboard,
    ClearKeychain,
    ClearStatusBar,
    Copy,
    Delete,
    Drag,
    Email,
    Foreground,
    HttpRequest,
    LongPress,
    Manual,
    OverrideStatusBar,
    Pinch,
    Push,
    Relaunch,
    Rotate,
    SelectOption,
    SelectText,
    SetClipboard,
    SetLocation,
    Swipe,
    TapPoint,
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


class Interrupt(_Model):
    """A handler for an interstitial screen that can appear at an unpredictable point (BE-0314).

    ``condition`` is the same assertion the ``if`` step evaluates; the runner checks it
    opportunistically against trees it has already fetched (a ``wait``'s poll tick, an act step's
    pre-action read), wherever in the step sequence the screen happens to surface, and runs
    ``steps`` to clear it when it matches. The steps share the enclosing scenario's ``vars.*``, the
    same as ``if``'s branches do.
    """

    condition: Assertion
    steps: list[Step] = Field(default_factory=list)


class Web(_Model):
    """Enter the web context: resolve a native WebView host, then run inner steps against its DOM.

    The ``within`` selector resolves natively to exactly one ``WKWebView`` element; inner ``steps``
    address the normalized DOM (``data-testid`` → ``Element.identifier``), not the native a11y tree.
    """

    within: Selector
    steps: list[Step]


class Step(_Model):
    """One action plus optional modifiers (capture / name / extract)."""

    tap: Selector | None = None
    tap_point: TapPoint | None = Field(default=None, alias="tapPoint")
    double_tap: Selector | None = Field(default=None, alias="doubleTap")
    long_press: LongPress | None = Field(default=None, alias="longPress")
    type: TypeText | None = None
    select: SelectText | None = None
    clear: Clear | None = None
    delete: Delete | None = None
    # `copy_` (alias `copy`) mirrors `assert_` / `if_` / `from_`: the YAML key is `copy`, but the
    # Python field is suffixed so it doesn't shadow pydantic `BaseModel.copy`.
    copy_: Copy | None = Field(default=None, alias="copy")
    select_option: SelectOption | None = Field(default=None, alias="selectOption")
    swipe: Swipe | None = None
    drag: Drag | None = None
    back: Back | None = None
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
    email: Email | None = None
    clear_keychain: ClearKeychain | None = Field(default=None, alias="clearKeychain")
    clear_clipboard: ClearClipboard | None = Field(default=None, alias="clearClipboard")
    set_clipboard: SetClipboard | None = Field(default=None, alias="setClipboard")
    background: Background | None = None
    foreground: Foreground | None = None
    override_status_bar: OverrideStatusBar | None = Field(default=None, alias="overrideStatusBar")
    clear_status_bar: ClearStatusBar | None = Field(default=None, alias="clearStatusBar")
    web: Web | None = None
    # A human-takeover marker (BE-0185): an operation the AI could not perform, recorded during
    # `record` and — because it has no deterministic run-time equivalent — failing loudly at `run`
    # time rather than faking a pass. A leaf action, so it obeys the one-action rule like the rest.
    manual: Manual | None = None
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
Interrupt.model_rebuild()
Web.model_rebuild()

# The action field names, derived from the model so a new action is declared in exactly one
# place — adding a `Step` field — instead of also appending to a parallel hand-maintained tuple
# (a per-action merge-conflict point). `_MODIFIERS` are the non-action fields.
_MODIFIERS = ("capture", "extract", "name", "from_")
_STEP_ACTIONS = tuple(f for f in Step.model_fields if f not in _MODIFIERS)
