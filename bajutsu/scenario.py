"""Scenario spec — the structure normalized from natural language.

Validated strictly with pydantic (extra="forbid" rejects unknown keys). The
deterministic runner executes this structure with no AI. Selector instances are
converted to drivers.base.Selector (a TypedDict) and passed to resolution.
"""

from __future__ import annotations

from typing import Any, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bajutsu import _yaml
from bajutsu.drivers import base

Point = tuple[float, float]

# capture token grammar.
_CAPTURE_KINDS = {
    "screenshot",
    "elements",
    "actionLog",
    "deviceLog",
    "network",
    "video",
    "appTrace",
}
_CAPTURE_MODS = {"before", "after", "around", "onError"}

_STEP_ACTIONS = ("tap", "long_press", "type", "swipe", "wait", "assert_", "relaunch")
_ASSERTION_KINDS = ("exists", "value", "label", "count", "enabled", "disabled", "selected")


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


def _validate_capture(tokens: list[str]) -> list[str]:
    for t in tokens:
        kind, _, mod = t.partition(".")
        if kind not in _CAPTURE_KINDS:
            raise ValueError(f"未知の証跡種別: {t!r}（§9）")
        if mod and mod not in _CAPTURE_MODS:
            raise ValueError(f"未知の修飾子: {t!r}（§9）")
    return tokens


class Selector(_Model):
    """How to address an element. Provided fields are combined with AND."""

    id: str | None = None
    id_matches: str | None = Field(default=None, alias="idMatches")
    label: str | None = None
    label_matches: str | None = Field(default=None, alias="labelMatches")
    traits: list[str] | None = None
    value: str | None = None
    within: "Selector | None" = None
    index: int | None = None

    @model_validator(mode="after")
    def _non_empty(self) -> Self:
        if not self.model_dump(exclude_none=True, by_alias=True):
            raise ValueError("セレクタは少なくとも 1 条件が必要（§5）")
        return self

    def as_selector(self) -> base.Selector:
        """Convert to the TypedDict consumed by base.resolve_unique."""
        return cast("base.Selector", self.model_dump(exclude_none=True, by_alias=True))


class Preconditions(_Model):
    """Per-test environment setup."""

    erase: bool = True
    launch_args: list[str] = Field(default_factory=list, alias="launchArgs")
    launch_env: dict[str, str] = Field(default_factory=dict, alias="launchEnv")
    deeplink: str | None = None
    locale: str | None = None
    setup: str | None = None


# --- Actions ---


class LongPress(_Model):
    sel: Selector
    duration: float


class TypeText(_Model):
    text: str
    into: Selector | None = None
    submit: bool = False


class Swipe(_Model):
    on: Selector | None = None
    direction: Literal["up", "down", "left", "right"] | None = None
    from_: Point | None = Field(default=None, alias="from")
    to: Point | None = None

    @model_validator(mode="after")
    def _form(self) -> Self:
        sel_fields = self.on is not None or self.direction is not None
        pt_fields = self.from_ is not None or self.to is not None
        if sel_fields and pt_fields:
            raise ValueError("swipe は {on,direction} と {from,to} を混在できない（§6.2）")
        if self.on is not None and self.direction is not None:
            return self
        if self.from_ is not None and self.to is not None:
            return self
        raise ValueError("swipe は {on,direction} か {from,to} を完全に指定する（§6.2）")


class Gone(_Model):
    gone: Selector


class Wait(_Model):
    for_: Selector | None = Field(default=None, alias="for")
    # settled = wait until the screen stops changing (best-effort; for transition settle)
    until: Literal["screenChanged", "settled"] | Gone | None = None
    timeout: float

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.for_ is None) == (self.until is None):
            raise ValueError("wait は for か until のどちらか一方（§6.3）")
        return self


class Relaunch(_Model):
    env: dict[str, str] | None = None
    args: list[str] | None = None


# --- Assertions ---


class Exists(_Model):
    """`exists: { <selector>, negate? }` (selector inline, optional negate)."""

    sel: Selector
    negate: bool = False

    @model_validator(mode="before")
    @classmethod
    def _inline(cls, data: Any) -> Any:
        if isinstance(data, dict) and "sel" not in data:
            d = dict(data)
            negate = d.pop("negate", False)
            return {"sel": d, "negate": negate}
        return data


class TextMatch(_Model):
    """`value` / `label`: exactly one of equals / contains / matches."""

    sel: Selector
    equals: str | None = None
    contains: str | None = None
    matches: str | None = None

    @model_validator(mode="after")
    def _one_op(self) -> Self:
        if sum(o is not None for o in (self.equals, self.contains, self.matches)) != 1:
            raise ValueError("value/label は equals/contains/matches のいずれか 1 つ（§6.4）")
        return self


class CountMatch(_Model):
    """`count`: exactly one of equals / atLeast / atMost."""

    sel: Selector
    equals: int | None = None
    at_least: int | None = Field(default=None, alias="atLeast")
    at_most: int | None = Field(default=None, alias="atMost")

    @model_validator(mode="after")
    def _one_op(self) -> Self:
        if sum(o is not None for o in (self.equals, self.at_least, self.at_most)) != 1:
            raise ValueError("count は equals/atLeast/atMost のいずれか 1 つ（§6.4）")
        return self


class Assertion(_Model):
    """One machine check. Exactly one kind may be set."""

    exists: Exists | None = None
    value: TextMatch | None = None
    label: TextMatch | None = None
    count: CountMatch | None = None
    enabled: Selector | None = None
    disabled: Selector | None = None
    selected: Selector | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        kinds = [k for k in _ASSERTION_KINDS if getattr(self, k) is not None]
        if len(kinds) != 1:
            raise ValueError(f"アサーションは 1 種類のみ（§6.4）: {kinds or 'なし'}")
        return self


# --- Steps ---


class Step(_Model):
    """One action plus optional modifiers (capture / name)."""

    tap: Selector | None = None
    long_press: LongPress | None = Field(default=None, alias="longPress")
    type: TypeText | None = None
    swipe: Swipe | None = None
    wait: Wait | None = None
    assert_: list[Assertion] | None = Field(default=None, alias="assert")
    relaunch: Relaunch | None = None
    capture: list[str] | None = None
    name: str | None = None

    @field_validator("capture")
    @classmethod
    def _cap(cls, v: list[str] | None) -> list[str] | None:
        return _validate_capture(v) if v is not None else v

    @model_validator(mode="after")
    def _one_action(self) -> Self:
        present = [a for a in _STEP_ACTIONS if getattr(self, a) is not None]
        if len(present) != 1:
            raise ValueError(f"ステップは 1 アクション（§6.2）: {present or 'なし'}")
        return self


# --- Evidence rules ---


class Trigger(_Model):
    action: str | None = None
    id_matches: str | None = Field(default=None, alias="idMatches")
    event: Literal["screenChanged"] | None = None
    result: Literal["error"] | None = None

    @model_validator(mode="after")
    def _one(self) -> Self:
        primary = [p for p in ("action", "event", "result") if getattr(self, p) is not None]
        if len(primary) != 1:
            raise ValueError("on は action / event / result のいずれか 1 つ（§9 A）")
        if self.id_matches is not None and self.action is None:
            raise ValueError("idMatches は action と併用する（§9 A）")
        return self


class CaptureRule(_Model):
    on: Trigger
    capture: list[str]

    @field_validator("capture")
    @classmethod
    def _cap(cls, v: list[str]) -> list[str]:
        return _validate_capture(v)


class Redact(_Model):
    labels: list[str] = Field(default_factory=list)
    headers: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)


class Scenario(_Model):
    """One scenario."""

    name: str
    preconditions: Preconditions = Field(default_factory=Preconditions)
    steps: list[Step]
    expect: list[Assertion] = Field(default_factory=list)
    capture_policy: list[CaptureRule] = Field(default_factory=list, alias="capturePolicy")
    redact: Redact | None = None


Selector.model_rebuild()


def load_scenarios(text: str) -> list[Scenario]:
    """Parse a YAML string (a list of scenarios) into validated Scenario objects."""
    data = _yaml.safe_load(text)
    if not isinstance(data, list):
        raise ValueError("シナリオファイルはシナリオの配列（§6.1）")
    return [Scenario.model_validate(item) for item in data]


def _prune(obj: Any) -> Any:
    """Drop None / empty-list / empty-dict entries for readable output."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            pruned = _prune(value)
            if pruned is None or pruned == [] or pruned == {}:
                continue
            out[key] = pruned
        return out
    if isinstance(obj, list):
        return [_prune(v) for v in obj]
    return obj


def dump_scenarios(scenarios: list[Scenario]) -> str:
    """Serialize scenarios back to YAML (round-trips through load_scenarios)."""
    data = [_prune(s.model_dump(mode="json", by_alias=True, exclude_none=True)) for s in scenarios]
    return _yaml.safe_dump(data)
