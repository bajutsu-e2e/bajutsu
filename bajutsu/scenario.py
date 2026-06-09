"""Scenario spec — the structure normalized from natural language.

Validated strictly with pydantic (extra="forbid" rejects unknown keys). The
deterministic runner executes this structure with no AI. Selector instances are
converted to drivers.base.Selector (a TypedDict) and passed to resolution.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bajutsu import _yaml, interp
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

_STEP_ACTIONS = (
    "tap", "double_tap", "long_press", "type", "swipe", "pinch", "rotate",
    "wait", "assert_", "relaunch", "use",
)
_ASSERTION_KINDS = (
    "exists", "value", "label", "count", "enabled", "disabled", "selected", "request",
)


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


class Pinch(_Model):
    """Two-finger magnify. scale > 1 zooms in, 0 < scale < 1 zooms out."""

    sel: Selector
    scale: float

    @model_validator(mode="after")
    def _positive(self) -> Self:
        if self.scale <= 0:
            raise ValueError("pinch の scale は正の値（>1 で拡大, <1 で縮小）（§6.2）")
        return self


class Rotate(_Model):
    """Two-finger rotation. radians > 0 rotates clockwise."""

    sel: Selector
    radians: float


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


class RequestMatch(_Model):
    """Network-traffic matcher, shared by the `request` assertion and the
    `until: { request: ... }` wait. The fields (method / url / urlMatches / path /
    pathMatches / status / bodyMatches) are AND-ed; `count` is how many exchanges matched
    — exact for the assertion, a lower bound for the wait. The endpoint can be pinned by
    `url` (exact full URL) or `urlMatches` (regex/substring; query strings live here), or
    just the `path`; `bodyMatches` checks the request body. At least one match field is
    required."""

    method: str | None = None
    url: str | None = None  # exact full URL (the endpoint)
    url_matches: str | None = Field(default=None, alias="urlMatches")  # regex/substring over the URL
    path: str | None = None  # exact path (query ignored)
    path_matches: str | None = Field(default=None, alias="pathMatches")  # regex over path
    status: int | None = None
    body_matches: str | None = Field(default=None, alias="bodyMatches")  # regex/substring over request body
    count: int | None = None

    @model_validator(mode="after")
    def _has_criterion(self) -> Self:
        if all(
            v is None
            for v in (self.method, self.url, self.url_matches, self.path, self.path_matches,
                      self.status, self.body_matches)
        ):
            raise ValueError(
                "request は method/url/urlMatches/path/pathMatches/status/bodyMatches のいずれかが必要"
            )
        return self


class Gone(_Model):
    gone: Selector


class WaitRequest(_Model):
    """`until: { request: <RequestMatch> }` — wait until a matching network exchange has
    been observed by the collector (needs the run's network collector active)."""

    request: RequestMatch


class Wait(_Model):
    for_: Selector | None = Field(default=None, alias="for")
    # settled = wait until the screen stops changing (best-effort; for transition settle)
    until: Literal["screenChanged", "settled"] | Gone | WaitRequest | None = None
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
    request: RequestMatch | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        kinds = [k for k in _ASSERTION_KINDS if getattr(self, k) is not None]
        if len(kinds) != 1:
            raise ValueError(f"アサーションは 1 種類のみ（§6.4）: {kinds or 'なし'}")
        return self


# --- Steps ---


class Use(_Model):
    """Invoke a reusable component, substituting its declared params with `with`. The
    `use` step is expanded away (replaced by the component's steps) before the run, so it
    is a compile-time macro, not a runtime action — determinism is unaffected."""

    component: str
    with_: dict[str, str] = Field(default_factory=dict, alias="with")


class Step(_Model):
    """One action plus optional modifiers (capture / name)."""

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
    use: Use | None = None
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


class NetworkFilter(_Model):
    """Which observed requests to interleave into the report's Steps timeline. With
    `domains` set, only exchanges whose URL host matches one of them — exactly or as a
    parent suffix (`example.com` matches `api.example.com`) — appear in Steps; empty /
    unset shows every captured exchange. The Network tab always lists them all."""

    domains: list[str] = Field(default_factory=list)


class Network(_Model):
    """Per-scenario network settings. `filter` scopes which observed requests are
    interleaved into the report's Steps timeline."""

    filter: NetworkFilter | None = None


class MockResponse(_Model):
    """The canned response a mock returns (defaults to an empty 200)."""

    status: int = 200
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    delay_ms: float | None = Field(default=None, alias="delayMs")  # artificial latency


class Mock(_Model):
    """A deterministic network stub: when an outgoing request matches `match`, BajutsuKit
    returns `respond` instead of hitting the network (so tests don't depend on a live
    server). `match` reuses the request matcher's request-side fields (method / url /
    urlMatches / path / pathMatches / bodyMatches); status / count do not apply here."""

    match: RequestMatch
    respond: MockResponse = Field(default_factory=MockResponse)


class Scenario(_Model):
    """One scenario."""

    name: str
    tags: list[str] = Field(default_factory=list)
    data: list[dict[str, str]] | None = None
    data_file: str | None = Field(default=None, alias="dataFile")
    preconditions: Preconditions = Field(default_factory=Preconditions)
    steps: list[Step]
    expect: list[Assertion] = Field(default_factory=list)
    capture_policy: list[CaptureRule] = Field(default_factory=list, alias="capturePolicy")
    network: Network | None = None
    mocks: list[Mock] = Field(default_factory=list)
    redact: Redact | None = None

    @model_validator(mode="after")
    def _one_data_source(self) -> Self:
        if self.data is not None and self.data_file is not None:
            raise ValueError("data と dataFile は併用できない（どちらか一方）")
        return self


class Component(_Model):
    """A reusable, parameterized sequence of steps. `params` are the names a caller must
    supply via `use: { with: {...} }`; the steps reference them as `${params.<name>}`."""

    params: list[str] = Field(default_factory=list)
    steps: list[Step]


Selector.model_rebuild()


def load_scenarios(text: str) -> list[Scenario]:
    """Parse a YAML string (a list of scenarios) into validated Scenario objects."""
    data = _yaml.safe_load(text)
    if not isinstance(data, list):
        raise ValueError("シナリオファイルはシナリオの配列（§6.1）")
    return [Scenario.model_validate(item) for item in data]


def load_component(text: str) -> Component:
    """Parse a YAML string (a single component mapping) into a validated Component."""
    return Component.model_validate(_yaml.safe_load(text))


def _interp_steps(steps: list[Step], bindings: dict[str, str]) -> list[Step]:
    """Substitute `bindings` into each step (via a model_dump round-trip) and re-validate.
    Aliases are preserved (by_alias) so the dump re-parses cleanly."""
    out: list[Step] = []
    for st in steps:
        dumped = st.model_dump(by_alias=True, exclude_none=True)
        out.append(Step.model_validate(interp.interpolate(dumped, bindings)))
    return out


def expand_components(
    scenarios: list[Scenario],
    resolve: Callable[[str], Component],
    max_depth: int = 25,
) -> None:
    """Replace every `use` step with the referenced component's steps (params
    substituted), recursively and in place. A component may itself `use` another. Raises
    on a missing/unknown param, a residual `${params.*}` token (referencing an undeclared
    param), a reference cycle, or excessive nesting depth. Pure compile-time expansion —
    after this no `use` steps remain, so the run loop is unaffected."""
    cache: dict[str, Component] = {}

    def expand(steps: list[Step], stack: list[str]) -> list[Step]:
        if len(stack) > max_depth:
            raise ValueError(f"component のネストが深すぎます（>{max_depth}）: {' -> '.join(stack)}")
        out: list[Step] = []
        for st in steps:
            if st.use is None:
                out.append(st)
                continue
            ref = st.use.component
            if ref in stack:
                raise ValueError(f"component が循環参照しています: {' -> '.join([*stack, ref])}")
            if ref not in cache:
                cache[ref] = resolve(ref)
            comp = cache[ref]
            args = st.use.with_
            missing = sorted(set(comp.params) - set(args))
            unknown = sorted(set(args) - set(comp.params))
            if missing:
                raise ValueError(f"component {ref!r} の params が不足: {missing}")
            if unknown:
                raise ValueError(f"component {ref!r} に未知の params: {unknown}")
            substituted = _interp_steps(comp.steps, {f"params.{k}": v for k, v in args.items()})
            dumps = [s.model_dump(by_alias=True, exclude_none=True) for s in substituted]
            residual = sorted(t for t in interp.find_tokens(dumps) if t.startswith("params."))
            if residual:
                raise ValueError(f"component {ref!r} が未宣言の param を参照: {residual}")
            out.extend(expand(substituted, [*stack, ref]))
        return out

    for scenario in scenarios:
        scenario.steps = expand(scenario.steps, [])


def read_csv(text: str) -> list[dict[str, str]]:
    """Parse CSV text into a list of {column: value} row dicts (header row required)."""
    import csv
    import io

    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def _instantiate(scenario: Scenario, row: dict[str, str], index: int) -> Scenario:
    dumped = scenario.model_dump(by_alias=True, exclude_none=True)
    dumped.pop("data", None)
    dumped.pop("dataFile", None)
    out = cast("dict[str, Any]", interp.interpolate(dumped, {f"row.{k}": v for k, v in row.items()}))
    kv = ", ".join(f"{k}={v}" for k, v in row.items())
    out["name"] = f"{scenario.name} [row {index + 1}: {kv}]" if kv else f"{scenario.name} [row {index + 1}]"
    return Scenario.model_validate(out)


def expand_data(
    scenarios: list[Scenario],
    resolve_csv: Callable[[str], list[dict[str, str]]],
) -> list[Scenario]:
    """Expand each data-driven scenario into one scenario per data row, substituting
    `${row.<col>}` tokens. A scenario with neither `data` nor `dataFile` passes through
    unchanged. Each derived scenario keeps the original's preconditions (erase default
    intact), so every row runs in its own clean environment — isolation is preserved."""
    out: list[Scenario] = []
    for s in scenarios:
        if s.data is not None:
            rows: list[dict[str, str]] | None = s.data
        elif s.data_file is not None:
            rows = resolve_csv(s.data_file)
        else:
            rows = None
        if rows is None:
            out.append(s)
            continue
        out.extend(_instantiate(s, row, i) for i, row in enumerate(rows))
    return out


def select_scenarios(
    scenarios: list[Scenario], include: list[str], exclude: list[str]
) -> list[Scenario]:
    """Filter scenarios by tag, preserving order. A scenario is kept when it carries at
    least one `include` tag (or `include` is empty) and none of the `exclude` tags;
    `exclude` wins over `include`. Pure metadata filtering — never mutates or reorders."""
    inc, exc = set(include), set(exclude)
    out: list[Scenario] = []
    for s in scenarios:
        tags = set(s.tags)
        if exc & tags:
            continue
        if inc and not (inc & tags):
            continue
        out.append(s)
    return out


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


def scenario_dict(scenario: Scenario) -> dict[str, Any]:
    """A pruned, alias-keyed dict of one scenario (for the rich report view)."""
    return cast(
        "dict[str, Any]",
        _prune(scenario.model_dump(mode="json", by_alias=True, exclude_none=True)),
    )


def dump_scenarios(scenarios: list[Scenario]) -> str:
    """Serialize scenarios back to YAML (round-trips through load_scenarios)."""
    return _yaml.safe_dump([scenario_dict(s) for s in scenarios])


def apply_setups(
    scenarios: list[Scenario],
    default_setup: str | None,
    resolve: Callable[[str], list[Step]],
) -> None:
    """Prepend each scenario's reusable setup prelude in place.

    A scenario's `setup` precondition (falling back to the app/config default) names a
    reusable prelude; `resolve` turns that reference into a list of steps (e.g. by loading
    a shared scenario file and taking its steps). Those steps run before the scenario's
    own — so a shared login / navigation flow is written once and reused. The same
    reference is resolved at most once.
    """
    cache: dict[str, list[Step]] = {}
    for scenario in scenarios:
        ref = scenario.preconditions.setup or default_setup
        if not ref:
            continue
        if ref not in cache:
            cache[ref] = resolve(ref)
        scenario.steps = [*cache[ref], *scenario.steps]


def dump_mocks(mocks: list[Mock]) -> str:
    """Serialize a scenario's mocks to the compact JSON BajutsuKit reads from
    BAJUTSU_MOCKS (alias keys, omitting unset fields)."""
    import json

    return json.dumps([m.model_dump(by_alias=True, exclude_none=True) for m in mocks])
