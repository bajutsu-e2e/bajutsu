"""One step: exactly one action, plus its optional modifiers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from pydantic import Field, PrivateAttr, field_validator, model_validator

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
    SelectPhotos,
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
    select_photos: SelectPhotos | None = Field(default=None, alias="selectPhotos")
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
    # A target group (BE-0437): names `target` once for a run of ordinary nested steps, expanded
    # away at load time (`_expand_target_groups`) into the same flat, per-step `target:` form
    # BE-0428 already validates and runs. An action, like `web`/`app`, so it obeys the one-action
    # rule — a step cannot combine `steps` with a leaf action.
    steps: list[Step] | None = None
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
    # the one-action rule; allowed on every step but `use`, control-flow included.
    from_: str | None = Field(default=None, alias="from")
    # Which of the enclosing scenario's `targets` this step runs against (BE-0428). A modifier, not
    # an action, like `from_` above — required or optional depending on `len(scenario.targets)`, a
    # rule `Step` itself cannot enforce since it cannot see the enclosing scenario
    # (`_check_target_requirements` does, from `Scenario`'s own validator).
    target: str | None = None
    # The scenario's `primaryTarget` an omitted `target` resolved to at load time (BE-0436). Private
    # rather than written back onto `target`, so `model_dump()` — the serve editor's splice and the
    # run's `scenario.yaml` snapshot — keeps the step as terse as the author wrote it.
    _resolved_target: str | None = PrivateAttr(default=None)

    @property
    def resolved_target(self) -> str | None:
        """The target this step runs against: its own `target`, else the primary it resolved to."""
        return self.target or self._resolved_target

    def resolve_target(self, target: str | None) -> None:
        """Record the primary target an omitted `target` defaults to, or None for none (BE-0436)."""
        self._resolved_target = target

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

    @model_validator(mode="after")
    def _target_group(self) -> Self:
        # A target group (BE-0437) never reaches the runner — expansion replaces it with its own
        # nested steps before the scenario finishes loading. `target` is what it exists to fix
        # once, so it is required unconditionally, not only once the scenario declares two or more
        # targets like a leaf action's. Every other modifier (`_MODIFIERS` minus `target`) is read
        # off the one step the runner executes; setting one here would vanish with expansion,
        # silently, so it is refused at load time instead.
        if self.steps is None:
            return self
        if not self.target:
            raise ValueError("steps: target is required on a target group (§6.2)")
        if not self.steps:
            raise ValueError("steps: a target group's steps must not be empty (§6.2)")
        for field in _MODIFIERS:
            if field == "target":
                continue
            if getattr(self, field) is not None:
                alias = type(self).model_fields[field].alias or field
                raise ValueError(f"steps: {alias} is not supported on a target group (§6.2)")
        for child in self.steps:
            if child.steps is not None:
                raise ValueError(
                    "steps: a target group cannot nest directly inside another target group (§6.2)"
                )
            if child.target is not None:
                raise ValueError(
                    "steps: a step nested directly inside a target group must omit target — "
                    "the group already fixed it (§6.2)"
                )
        return self

    @model_validator(mode="after")
    def _no_modifiers_on_use(self) -> Self:
        if self.use is not None:
            present = [
                (field.alias or name)
                for name, field in type(self).model_fields.items()
                if name in _MODIFIERS and getattr(self, name) is not None
            ]
            if present:
                raise ValueError(
                    f"use steps take no modifiers, got {', '.join(present)} — expansion replaces "
                    "the step with the component's own steps, which would silently discard them"
                )
        return self


_STEP_ACTIONS = tuple(f for f in Step.model_fields if f not in _MODIFIERS)
