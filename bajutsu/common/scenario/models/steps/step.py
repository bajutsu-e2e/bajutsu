"""One step: exactly one action, plus its optional modifiers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from pydantic import Field, field_validator, model_validator

from bajutsu.common.scenario.models._base import (
    _CONTROL_FLOW_ACTIONS,
    _exactly_one,
    _Model,
    _validate_capture,
)
from bajutsu.common.scenario.models.actions import (
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
    Generate,
    HandleSystemAlert,
    HttpRequest,
    LongPress,
    Manual,
    OverrideStatusBar,
    Pinch,
    Push,
    Relaunch,
    Rotate,
    Scroll,
    SelectOption,
    SelectText,
    SetClipboard,
    SetLocation,
    SetPickerValue,
    Swipe,
    TapPoint,
    Totp,
    TypeText,
)
from bajutsu.common.scenario.models.assertions import Assertion, Wait
from bajutsu.common.scenario.models.selector import Selector

from ._shared import _MODIFIERS
from .extract import Extract
from .use import Use

if TYPE_CHECKING:
    from .app import App
    from .for_each import ForEach
    from .if_ import If
    from .web import Web


# `name` becomes a filesystem path segment twice over — the run's step_id
# (`orchestrator/loop.py`) and the editor's artifact lookup (`serve/operations/reads.py`) both
# build `f"{sid}/{name}"` and join it onto a real directory with no confinement check downstream.
# Rejecting a path separator or a bare `.`/`..` here, once, at load time, closes the traversal for
# every consumer instead of patching each join site.
_UNSAFE_STEP_NAME_CHARS = ("/", "\\")


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
    set_picker_value: SetPickerValue | None = Field(default=None, alias="setPickerValue")
    swipe: Swipe | None = None
    drag: Drag | None = None
    scroll: Scroll | None = None
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
    generate: Generate | None = None
    email: Email | None = None
    clear_keychain: ClearKeychain | None = Field(default=None, alias="clearKeychain")
    clear_clipboard: ClearClipboard | None = Field(default=None, alias="clearClipboard")
    set_clipboard: SetClipboard | None = Field(default=None, alias="setClipboard")
    background: Background | None = None
    foreground: Foreground | None = None
    override_status_bar: OverrideStatusBar | None = Field(default=None, alias="overrideStatusBar")
    clear_status_bar: ClearStatusBar | None = Field(default=None, alias="clearStatusBar")
    handle_system_alert: HandleSystemAlert | None = Field(default=None, alias="handleSystemAlert")
    web: Web | None = None
    app: App | None = None
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
    # Which of the enclosing scenario's `targets` this step runs against (BE-0428). A modifier, not
    # an action, like `from_` above — required or optional depending on `len(scenario.targets)`, a
    # rule `Step` itself cannot enforce since it cannot see the enclosing scenario
    # (`_check_target_requirements` does, from `Scenario`'s own validator).
    target: str | None = None

    @field_validator("capture")
    @classmethod
    def _cap(cls, v: list[str] | None) -> list[str] | None:
        return _validate_capture(v) if v is not None else v

    @field_validator("name")
    @classmethod
    def _safe_name(cls, v: str | None) -> str | None:
        if v is not None and (any(c in v for c in _UNSAFE_STEP_NAME_CHARS) or v in (".", "..")):
            raise ValueError(f"name must not contain a path separator or be '.'/'..' (§6.2): {v!r}")
        return v

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


_STEP_ACTIONS = tuple(f for f in Step.model_fields if f not in _MODIFIERS)
