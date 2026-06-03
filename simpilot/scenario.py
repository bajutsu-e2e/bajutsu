"""シナリオ仕様 — 自然言語から正規化された構造（DESIGN.md §6）。

pydantic で厳格にスキーマ検証する（`extra="forbid"` で未知キーを弾く）。
`run`（Tier2）はこの構造を AI 非依存で実行する（§3.1）。
セレクタ実体は `simpilot.drivers.base.Selector`（TypedDict）へ変換して §5 の解決へ渡す。
"""

from __future__ import annotations

from typing import Any, Literal, Self, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from simpilot.drivers import base

Point = tuple[float, float]

# capture トークン文法（§9）。
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
    """要素の指定（§5）。指定したフィールドは AND で適用される。"""

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
        """§5 の解決（`base.resolve_unique`）に渡せる TypedDict へ変換する。"""
        return cast("base.Selector", self.model_dump(exclude_none=True, by_alias=True))


class Preconditions(_Model):
    """各テスト前の環境構築（§6.1）。"""

    erase: bool = True
    launch_args: list[str] = Field(default_factory=list, alias="launchArgs")
    launch_env: dict[str, str] = Field(default_factory=dict, alias="launchEnv")
    deeplink: str | None = None
    locale: str | None = None
    setup: str | None = None


# --- アクション（§6.2 / §6.3）-------------------------------------------------


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
    until: Literal["screenChanged"] | Gone | None = None
    timeout: float

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.for_ is None) == (self.until is None):
            raise ValueError("wait は for か until のどちらか一方（§6.3）")
        return self


class Relaunch(_Model):
    env: dict[str, str] | None = None
    args: list[str] | None = None


# --- アサーション（§6.4）-----------------------------------------------------


class Exists(_Model):
    """`exists: { <selector>, negate? }`（セレクタ inline + 任意 negate。§6.4）。"""

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
    """`value` / `label`（§6.4）。equals / contains / matches のいずれか 1 つ。"""

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
    """`count`（§6.4）。equals / atLeast / atMost のいずれか 1 つ。"""

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
    """機械チェック 1 件（§6.4）。同時に指定できる種別は 1 つのみ。"""

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


# --- ステップ（§6.2）---------------------------------------------------------


class Step(_Model):
    """1 アクション + 任意の修飾子（capture / name）。§6.2。"""

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


# --- 証跡ルール（§9 A）-------------------------------------------------------


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
    """1 シナリオ（§6.1）。"""

    name: str
    preconditions: Preconditions = Field(default_factory=Preconditions)
    steps: list[Step]
    expect: list[Assertion] = Field(default_factory=list)
    capture_policy: list[CaptureRule] = Field(default_factory=list, alias="capturePolicy")
    redact: Redact | None = None


Selector.model_rebuild()


def load_scenarios(text: str) -> list[Scenario]:
    """YAML 文字列（シナリオの配列）を検証済み `Scenario` のリストへ（§6.1）。"""
    data = yaml.safe_load(text)
    if not isinstance(data, list):
        raise ValueError("シナリオファイルはシナリオの配列（§6.1）")
    return [Scenario.model_validate(item) for item in data]
