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
    "tap",
    "double_tap",
    "long_press",
    "type",
    "swipe",
    "pinch",
    "rotate",
    "wait",
    "assert_",
    "relaunch",
    "set_location",
    "push",
    "use",
    "http",
    "clear_keychain",
    "clear_clipboard",
    "background",
    "override_status_bar",
    "clear_status_bar",
)
_ASSERTION_KINDS = (
    "exists",
    "value",
    "label",
    "count",
    "enabled",
    "disabled",
    "selected",
    "request",
    "visual",
)


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


def _exactly_one(obj: _Model, fields: tuple[str, ...], context: str) -> list[str]:
    """Return the list of set fields from *fields*; raise if not exactly one is set."""
    present = [f for f in fields if getattr(obj, f) is not None]
    if len(present) != 1:
        raise ValueError(f"exactly one of {list(fields)} required ({context}): {present or 'none'}")
    return present


def _validate_capture(tokens: list[str]) -> list[str]:
    for t in tokens:
        kind, _, mod = t.partition(".")
        if kind not in _CAPTURE_KINDS:
            raise ValueError(f"unknown capture kind: {t!r} (§9)")
        if mod and mod not in _CAPTURE_MODS:
            raise ValueError(f"unknown capture modifier: {t!r} (§9)")
    return tokens


class Selector(_Model):
    """How to address an element. Provided fields are combined with AND."""

    id: str | None = None
    id_matches: str | None = Field(default=None, alias="idMatches")
    label: str | None = None
    label_matches: str | None = Field(default=None, alias="labelMatches")
    traits: list[str] | None = None
    value: str | None = None
    within: Selector | None = None
    index: int | None = None

    @model_validator(mode="after")
    def _non_empty(self) -> Self:
        if not self.model_dump(exclude_none=True, by_alias=True):
            raise ValueError("selector requires at least one condition (§5)")
        return self

    def as_selector(self) -> base.Selector:
        """Convert to the TypedDict consumed by base.resolve_unique."""
        return cast("base.Selector", self.model_dump(exclude_none=True, by_alias=True))


class Preconditions(_Model):
    """Per-test environment setup."""

    # Wipe the whole simulator (simctl erase) before the test — apps, data, settings. Off by
    # default: the app is reinstalled fresh each run (see `reinstall`), so a full wipe is only
    # needed when a test wants a pristine device (no other apps / default settings).
    erase: bool = False
    # How the app is (re)installed before each run, when the app config gives an `appPath`:
    #   clean     — uninstall then install (fresh app + data; the default)
    #   overwrite — install over the existing app (keeps its data container)
    reinstall: Literal["clean", "overwrite"] = "clean"
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
            raise ValueError("pinch scale must be positive (>1 zooms in, <1 zooms out) (§6.2)")
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
            raise ValueError("swipe cannot mix {on,direction} with {from,to} (§6.2)")
        if self.on is not None and self.direction is not None:
            return self
        if self.from_ is not None and self.to is not None:
            return self
        raise ValueError("swipe requires either {on,direction} or {from,to} completely (§6.2)")


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
    url_matches: str | None = Field(
        default=None, alias="urlMatches"
    )  # regex/substring over the URL
    path: str | None = None  # exact path (query ignored)
    path_matches: str | None = Field(default=None, alias="pathMatches")  # regex over path
    status: int | None = None
    body_matches: str | None = Field(
        default=None, alias="bodyMatches"
    )  # regex/substring over request body
    count: int | None = None

    @model_validator(mode="after")
    def _has_criterion(self) -> Self:
        if all(
            v is None
            for v in (
                self.method,
                self.url,
                self.url_matches,
                self.path,
                self.path_matches,
                self.status,
                self.body_matches,
            )
        ):
            raise ValueError(
                "request requires at least one of method/url/urlMatches/path/pathMatches/status/bodyMatches"
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
            raise ValueError("wait requires exactly one of 'for' or 'until' (§6.3)")
        return self


class Relaunch(_Model):
    env: dict[str, str] | None = None
    args: list[str] | None = None


class SetLocation(_Model):
    """Override the simulated device's GPS location (simctl location set)."""

    lat: float
    lon: float


class Push(_Model):
    """Deliver a simulated push notification (simctl push) with this APNs payload
    (e.g. {"aps": {"alert": "..."}}) to the app under test."""

    payload: dict[str, Any]


class HttpRequest(_Model):
    """Issue an HTTP request (for test-data setup, webhook triggers, API calls).

    The response status is checked against ``status`` (if given); a mismatch
    fails the step. ``saveBody`` stores the response body text as
    ``vars.<saveBody>`` for subsequent ``${vars.*}`` interpolation."""

    method: str = "GET"
    url: str
    headers: dict[str, str] | None = None
    body: str | None = None
    status: int | None = None
    save_body: str | None = Field(default=None, alias="saveBody")


class ClearKeychain(_Model):
    """Reset the Simulator's keychain (saved passwords, certificates)."""


class ClearClipboard(_Model):
    """Clear the Simulator's pasteboard."""


class Background(_Model):
    """Send the app to the background by pressing the Home button (simctl ui home)."""


class OverrideStatusBar(_Model):
    """Override the Simulator's status bar for deterministic screenshots.

    All fields are optional; only the provided fields are overridden."""

    time: str | None = None
    battery_level: int | None = Field(default=None, alias="batteryLevel")
    battery_state: str | None = Field(default=None, alias="batteryState")
    cellular_bars: int | None = Field(default=None, alias="cellularBars")
    wifi_bars: int | None = Field(default=None, alias="wifiBars")


class ClearStatusBar(_Model):
    """Remove any status bar overrides (restore the live status bar)."""


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
            raise ValueError("value/label requires exactly one of equals/contains/matches (§6.4)")
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
            raise ValueError("count requires exactly one of equals/atLeast/atMost (§6.4)")
        return self


class ExcludeRegion(_Model):
    """A rectangular region to ignore during visual comparison (e.g. status bar, clock)."""

    x: float
    y: float
    w: float
    h: float


class VisualMatch(_Model):
    """Visual regression assertion — compare a screenshot to a baseline image."""

    baseline: str
    threshold: float = 0.0  # allowed diff percentage (0.0 = exact match)
    exclude: list[ExcludeRegion] | None = None


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
    visual: VisualMatch | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        _exactly_one(self, _ASSERTION_KINDS, "§6.4")
        return self


# --- Steps ---


class Use(_Model):
    """Invoke a reusable component, substituting its declared params with `with`. The
    `use` step is expanded away (replaced by the component's steps) before the run, so it
    is a compile-time macro, not a runtime action — determinism is unaffected."""

    component: str
    with_: dict[str, str] = Field(default_factory=dict, alias="with")


class Extract(_Model):
    """Capture a UI element's property into a runtime variable (``vars.*``)."""

    sel: Selector
    prop: Literal["value", "label", "identifier"] = "value"


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
    clear_keychain: ClearKeychain | None = Field(default=None, alias="clearKeychain")
    clear_clipboard: ClearClipboard | None = Field(default=None, alias="clearClipboard")
    background: Background | None = None
    override_status_bar: OverrideStatusBar | None = Field(default=None, alias="overrideStatusBar")
    clear_status_bar: ClearStatusBar | None = Field(default=None, alias="clearStatusBar")
    capture: list[str] | None = None
    extract: dict[str, Extract] | None = None
    name: str | None = None

    @field_validator("capture")
    @classmethod
    def _cap(cls, v: list[str] | None) -> list[str] | None:
        return _validate_capture(v) if v is not None else v

    @model_validator(mode="after")
    def _one_action(self) -> Self:
        _exactly_one(self, _STEP_ACTIONS, "§6.2")
        return self


# --- Evidence rules ---


class Trigger(_Model):
    action: str | None = None
    id_matches: str | None = Field(default=None, alias="idMatches")
    event: Literal["screenChanged"] | None = None
    result: Literal["error"] | None = None

    @model_validator(mode="after")
    def _one(self) -> Self:
        _exactly_one(self, ("action", "event", "result"), "§9 A")
        if self.id_matches is not None and self.action is None:
            raise ValueError("idMatches requires action (§9 A)")
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


class DismissAlerts(_Model):
    """Per-scenario control of the system-alert guard — the vision-based dismissal of OS
    prompts (e.g. iOS "Save Password?", a permission request) that idb cannot see or tap.

    The guard is ON by default and fires only when a step (or `expect`) is blocked: it
    screenshots, asks the locator where to tap, taps the prompt away, and retries once.
    Two on-disk forms (the bare boolean is shorthand for `{ enabled: <bool> }`):
        dismissAlerts: false                  — disable the guard for this scenario
        dismissAlerts: { instruction: "..." } — keep it on, but tap the named button
                                                 (e.g. "tap Allow" to grant a prompt)
    """

    enabled: bool = True
    # When set, the locator taps the button this names instead of the default dismissive one
    # (e.g. "tap Allow"); a per-scenario instruction wins over the CLI `--alert-instruction`.
    instruction: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_bool(cls, data: Any) -> Any:
        return {"enabled": data} if isinstance(data, bool) else data


class Scenario(_Model):
    """One scenario."""

    name: str
    description: str | None = None
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
    # The alert guard runs on by default; unset means "on, dismiss the prompt" (see
    # DismissAlerts). Kept None when unset so a dumped scenario stays clean.
    dismiss_alerts: DismissAlerts | None = Field(default=None, alias="dismissAlerts")

    @model_validator(mode="after")
    def _one_data_source(self) -> Self:
        if self.data is not None and self.data_file is not None:
            raise ValueError("data and dataFile are mutually exclusive")
        return self


class Component(_Model):
    """A reusable, parameterized sequence of steps. `params` are the names a caller must
    supply via `use: { with: {...} }`; the steps reference them as `${params.<name>}`."""

    params: list[str] = Field(default_factory=list)
    steps: list[Step]


Selector.model_rebuild()


class ScenarioFile(_Model):
    """A scenario file: an optional file-level `description` plus the scenarios it defines.

    Two on-disk forms are accepted: the bare list of scenarios (no file description), or a
    `{description: "...", scenarios: [...]}` mapping.
    """

    description: str | None = None
    scenarios: list[Scenario]


def load_scenario_file(text: str) -> ScenarioFile:
    """Parse a scenario file (a list of scenarios, or a `{description, scenarios}` mapping)."""
    data = _yaml.safe_load(text)
    if isinstance(data, list):
        return ScenarioFile.model_validate({"scenarios": data})
    if isinstance(data, dict):
        return ScenarioFile.model_validate(data)
    raise ValueError(
        "scenario file must be a list of scenarios or a {description, scenarios} mapping (§6.1)"
    )


def load_scenarios(text: str) -> list[Scenario]:
    """Parse a scenario file into validated Scenario objects (any file-level description dropped)."""
    return load_scenario_file(text).scenarios


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
            raise ValueError(f"component nesting too deep (>{max_depth}): {' -> '.join(stack)}")
        out: list[Step] = []
        for st in steps:
            if st.use is None:
                out.append(st)
                continue
            ref = st.use.component
            if ref in stack:
                raise ValueError(f"component cycle detected: {' -> '.join([*stack, ref])}")
            if ref not in cache:
                cache[ref] = resolve(ref)
            comp = cache[ref]
            args = st.use.with_
            missing = sorted(set(comp.params) - set(args))
            unknown = sorted(set(args) - set(comp.params))
            if missing:
                raise ValueError(f"component {ref!r} missing required params: {missing}")
            if unknown:
                raise ValueError(f"component {ref!r} has unknown params: {unknown}")
            substituted = _interp_steps(comp.steps, {f"params.{k}": v for k, v in args.items()})
            dumps = [s.model_dump(by_alias=True, exclude_none=True) for s in substituted]
            residual = sorted(t for t in interp.find_tokens(dumps) if t.startswith("params."))
            if residual:
                raise ValueError(f"component {ref!r} references undeclared params: {residual}")
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
    out = cast(
        "dict[str, Any]", interp.interpolate(dumped, {f"row.{k}": v for k, v in row.items()})
    )
    kv = ", ".join(f"{k}={v}" for k, v in row.items())
    out["name"] = (
        f"{scenario.name} [row {index + 1}: {kv}]" if kv else f"{scenario.name} [row {index + 1}]"
    )
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


def dump_scenario_file(scenarios: list[Scenario], description: str | None = None) -> str:
    """Serialize a scenario file. With a file-level `description`, emits the `{description,
    scenarios}` mapping form; otherwise the bare list (round-trips through load_scenario_file)."""
    body = [scenario_dict(s) for s in scenarios]
    if description:
        return _yaml.safe_dump({"description": description, "scenarios": body})
    return _yaml.safe_dump(body)


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
