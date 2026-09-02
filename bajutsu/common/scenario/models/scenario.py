"""The top-level shapes a scenario file is made of.

Preconditions, the alert-guard control, the scenario and its reusable component, and the
scenario-file wrapper that ties them together.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import AliasChoices, BeforeValidator, Field, field_validator, model_validator

from bajutsu.common.drivers.base import PERMISSION_SERVICES
from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.models.assertions import Assertion
from bajutsu.common.scenario.models.evidence import CaptureRule, Network, Redact
from bajutsu.common.scenario.models.mocks import Mock
from bajutsu.common.scenario.models.steps import AfterRule, Interrupt, Step
from bajutsu.common.scenario.system_alerts import SystemAlertChoice, SystemAlertPrompt
from bajutsu.deprecations import reject_renamed_key

# The grant/revoke actions a `permissions` entry may take (BE-0276); the service side of the
# vocabulary (`PERMISSION_SERVICES`) lives in `drivers.base` since every backend's capability
# advertisement already depends on it — reused here rather than duplicated.
_PERMISSION_ACTIONS = ("grant", "revoke")

# The scenario file's schema version, mirroring the report manifest's SCHEMA_VERSION (BE-0119).
# Bump only for a load-breaking change: removing a required field's meaning, or a change an older
# bajutsu would misinterpret rather than merely reject. A purely additive optional field needs no
# bump — an older bajutsu simply lacks the new behavior. `load_scenario_file` compares a file's
# declared `schema` against this before validating, so a newer file fails with a clear upgrade path
# instead of an opaque extra="forbid" error.
SCHEMA_VERSION = 1


class Preconditions(_Model):
    """Per-test environment setup."""

    # Wipe the whole simulator (simctl erase) before the test — apps, data, settings. The app is
    # reinstalled fresh each run (see `reinstall`), so a full wipe is only needed when a test wants a
    # pristine device (no other apps / default settings). None (unset) inherits the target config's
    # `erase` and then the built-in off (BE-0177); an explicit true/false pins it for this scenario.
    # `run` resolves this to a concrete bool before dispatch, so `None` behaves as off downstream.
    erase: bool | None = None
    # How the app is (re)installed before each run, when the app config gives an `appPath`:
    #   clean     — uninstall then install (fresh app + data; the default)
    #   overwrite — install over the existing app (keeps its data container)
    reinstall: Literal["clean", "overwrite"] = "clean"
    launch_args: list[str] = Field(default_factory=list, alias="launchArgs")
    launch_env: dict[str, str] = Field(default_factory=dict, alias="launchEnv")
    deeplink: str | None = None
    locale: str | None = None
    setup: str | None = None

    def resolved_locale(self, target_locale: str) -> str:
        """The locale this scenario runs under: its own override, else the target config's `locale`.

        The one place the precedence lives, so everything that acts on it agrees — the app's launch
        arguments, the Simulator's own system language, and the system-alert label lookup that
        predicts what SpringBoard renders (BE-0320). Takes the target's value rather than the whole
        config, keeping the scenario schema a portable inner contract.
        """
        return self.locale or target_locale


class SystemAlertRule(_Model):
    """One entry of `systemAlertHandling.rules`: the choice to make on one named prompt.

    `prompt` and `choice` reuse the vocabulary the proactive `handleSystemAlert` step already takes
    (its `prompt`/`choice` form) instead of a literal button label, so the same rule grants or denies
    the prompt under any locale `bajutsu.common.scenario.system_alerts` covers. The reactive guard identifies
    which alert is on screen from this prompt's own two labels — not from an ordering trick over
    `labels`, which cannot record which answer belongs to which prompt (see `SystemAlertHandling`
    below).
    """

    prompt: SystemAlertPrompt
    choice: SystemAlertChoice


class SystemAlertHandling(_Model):
    """Per-scenario control of the reactive system-alert guard.

    Handling of OS prompts (e.g. a notification or App Tracking Transparency request) that the
    app-scoped accessibility tree cannot see or tap, fired reactively when a step (or `expect`) is
    blocked or a guarded wait finds an alert. The guard is ON by default. On the iOS XCUITest backend
    it clears the prompt deterministically and natively (BE-0315), reusing BE-0316's SpringBoard query
    + tap — no screenshot and no model round trip; where the native path cannot act it falls back to
    the vision guard (a screenshot the locator reads). This is the *reactive* counterpart to the
    *proactive* `handleSystemAlert` step (BE-0316): the step taps a named button at an author-chosen
    point, this guard clears prompts automatically wherever they surface.

    Each key reaches exactly one of those two paths (BE-0401): `rules` and `labels` steer the native
    path, `visionInstruction` steers the fallback. On-disk forms — the bare boolean carries on and
    off, so a mapping always means on:
        systemAlertHandling: false                       — disable the guard for this scenario
        systemAlertHandling: { rules: [{ prompt: notifications, choice: grant }] } — answer a named
                                                             prompt by its own choice, regardless of
                                                             which label it shares with another
        systemAlertHandling: { labels: ["Allow", "OK"] } — tap the first of these labels present on
                                                             the alert
        systemAlertHandling: { visionInstruction: "tap Allow" } — free text only the vision fallback
                                                             reads
    Within the native path a rule names a prompt and a label names a button, so `rules` is consulted
    first; an alert whose prompt no rule names falls through to `labels`, and to the built-in
    dismissive labels when no layer supplies any.
    """

    # Ordered answers to specific covered prompts, by name rather than by button text. The
    # guard identifies the alert on screen from a rule's own prompt (its resolved label pair), so
    # ordering only matters for two rules whose pairs could both match the same alert — none of the
    # prompts `system_alerts.py` covers today can. Checked before `labels` below, since a prompt name
    # is the more specific declaration; an alert no rule identifies falls through to it.
    rules: list[SystemAlertRule] = Field(default_factory=list)
    # The ordered candidate button labels the native path taps instead of the default dismissive one,
    # taking the first that is present on the alert (via BE-0316's `handle_system_alert`). Empty means
    # no layer named a button, which is what the built-in dismissive labels stand in for — they never
    # extend a supplied list, so a scenario naming its buttons and meeting an alert carrying none of
    # them drops to the vision fallback rather than tapping a button the author never named.
    labels: list[str] = Field(default_factory=list)
    # Free text only the AI vision fallback reads ("tap Allow"), for an alert the native path cannot
    # name — including every alert on a backend with no native path at all. The native path compares
    # labels exactly, so it never reads this; `labels` above is what steers it (BE-0401).
    vision_instruction: str | None = Field(default=None, alias="visionInstruction")
    # How often (seconds) the reactive guard polls the native system-alert presence query while a
    # wait is pending, on its own wall clock decoupled from the wait's condition poll (BE-0315). A
    # heuristic trading detection latency against runner load, so it is a knob rather than hard-coded;
    # None inherits the built-in default (one second).
    poll_interval: float | None = Field(default=None, alias="pollInterval")

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_keys(cls, data: Any) -> Any:
        # `extra="forbid"` already rejects each of these, but with Pydantic's generic "extra fields
        # not permitted", which names no replacement. BE-0401 removed them with no alias, so the
        # error is the whole migration path an author gets — name the key that replaces each.
        if not isinstance(data, dict):
            return data
        if "instruction" in data:
            replacement = "labels" if isinstance(data["instruction"], list) else "visionInstruction"
            raise ValueError(
                "systemAlertHandling.instruction was removed (BE-0401); use "
                f"'{replacement}' instead — 'labels' is the ordered button labels the native "
                "path taps, 'visionInstruction' the free text only the vision fallback reads"
            )
        if "enabled" in data:
            raise ValueError(
                "systemAlertHandling.enabled was removed (BE-0401); write the boolean directly "
                "(`systemAlertHandling: false` to disable, a mapping to configure the policy)"
            )
        return data

    @field_validator("labels")
    @classmethod
    def _non_empty_labels(cls, v: list[str]) -> list[str]:
        # An empty list, or an empty entry among real ones, would fall through to the layers above and
        # then to the default dismissive policy — so a typo answers the opposite of what the author
        # wrote. Fail instead, the same reason an ambiguous selector fails rather than tapping its
        # first match.
        if not v:
            raise ValueError("systemAlertHandling.labels must name at least one button label")
        if any(not s.strip() for s in v):
            raise ValueError("systemAlertHandling.labels must not contain an empty label")
        return v

    @field_validator("vision_instruction")
    @classmethod
    def _non_empty_vision_instruction(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("systemAlertHandling.visionInstruction must not be empty")
        return v

    @field_validator("poll_interval")
    @classmethod
    def _positive_interval(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("pollInterval must be positive")
        return v

    @field_validator("rules")
    @classmethod
    def _unique_prompts(cls, v: list[SystemAlertRule]) -> list[SystemAlertRule]:
        # Silently taking the first of two rules naming the same prompt would hide an authoring
        # mistake — the same reason an ambiguous selector fails rather than tapping its first match.
        seen = [r.prompt for r in v]
        dupes = sorted({p for p in seen if seen.count(p) > 1})
        if dupes:
            raise ValueError(
                f"systemAlertHandling.rules names {dupes} more than once; "
                "each prompt takes exactly one rule"
            )
        return v


def _coerce_system_alert_handling(v: Any) -> Any:
    """`true` is the empty policy (on, no declarations); everything else reaches the union as-is."""
    return {} if v is True else v


# The on-disk type of a `systemAlertHandling` field, shared by the scenario and the target config so
# the two layers accept exactly the same forms. The boolean carries on and off (BE-0401): `false`
# disables the guard, a mapping is on and holds the policy, and `None` (the key absent) inherits the
# layer above. `{ enabled: false, rules: [...] }` — a policy the runtime discarded without a word —
# is not representable.
SystemAlertHandlingField = Annotated[
    Literal[False] | SystemAlertHandling | None,
    BeforeValidator(_coerce_system_alert_handling),
]


class Scenario(_Model):
    """One scenario."""

    name: str
    description: str | None = None
    # Provenance (BE-0044): the original natural-language goal `record` authored this scenario
    # from. Authoring metadata only — `run` never reads it. Kept None (pruned) when unset.
    from_: str | None = Field(default=None, alias="from")
    tags: list[str] = Field(default_factory=list)
    # Per-scenario OS permission state (BE-0276), applied before the app process starts: grant or
    # revoke a permission up front so the runtime prompt never appears (iOS `simctl privacy`,
    # Android `pm grant`/`pm revoke`). Deterministic and AI-free, unlike the vision
    # systemAlertHandling guard below, which reacts to a prompt only after it appears. Kept as a
    # plain `dict[str, str]` (validated below against the vocabulary) rather than a `Literal`-keyed
    # dict, so it stays assignable to the `Mapping[str, str]` the platform-lifecycle `start()` seam
    # expects.
    permissions: dict[str, str] = Field(default_factory=dict)
    # Handlers for interstitial screens that surface at an unpredictable point (BE-0314): each entry
    # names a `condition` (the assertion DSL `if` uses) and the `steps` that clear it. The runner
    # checks each opportunistically against trees it has already fetched, wherever the screen appears
    # — so an author need not predict the one spot to place an `if`. Appended to the target config's
    # own `interrupts` (config entries first), mirroring how `systemAlertHandling` layers config
    # under scenario. Empty (the default) means no scenario-level handler, so it prunes from a dump.
    interrupts: list[Interrupt] = Field(default_factory=list)
    data: list[dict[str, str]] | None = None
    data_file: str | None = Field(default=None, alias="dataFile")
    preconditions: Preconditions = Field(default_factory=Preconditions)
    # Setup that runs as its own phase before `steps` (BE-0392), not spliced into it the way a
    # `preconditions.setup` prelude is: it gets its own report section, and its failure aborts the
    # scenario before `steps` runs rather than surfacing as an ordinary step failure. Prepended to
    # the target config's own `before`, the config-then-scenario order `interrupts` follows. Empty
    # (the default) means no scenario-level prelude, so it prunes from a dump.
    before: list[Step] = Field(default_factory=list)
    steps: list[Step]
    expect: list[Assertion] = Field(default_factory=list)
    # Teardown rules keyed to the run's own verdict (BE-0392), run after `steps`/`expect` on every
    # path out of them — including the one a failing step took, which trailing `steps` never reach.
    # Merged scenario-then-config, the reverse of `before`: this scenario's own cleanup releases what
    # it created before the app-wide one tears down around it.
    after: list[AfterRule] = Field(default_factory=list)
    capture_policy: list[CaptureRule] = Field(default_factory=list, alias="capturePolicy")
    network: Network | None = None
    mocks: list[Mock] = Field(default_factory=list)
    redact: Redact | None = None
    # The alert guard runs on by default; unset means "on, tap the prompt's default button" (see
    # SystemAlertHandling). Kept None when unset so a dumped scenario stays clean. The former
    # `alertHandling` / `dismissAlerts` spellings were deleted with no alias (BE-0401); the
    # validator below names the replacement.
    system_alert_handling: SystemAlertHandlingField = Field(
        default=None,
        validation_alias=AliasChoices("systemAlertHandling"),
        serialization_alias="systemAlertHandling",
    )
    # Dismiss a blocking TipKit tip when one is in the way. Off unless asked for, unlike the alert
    # guard above: a tip is sometimes the very thing a scenario asserts on, and dismissing it by
    # default would silently break that scenario. None keeps a dumped scenario clean. The `ios`
    # prefix is load-bearing: TipKit is an Apple framework, so unlike `systemAlertHandling` — whose
    # OS-prompt idea every platform has some form of — this key is inert on Android and web, and the
    # name says so at the call site rather than leaving an author to find out from a no-op.
    ios_tip_kit_handling: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("iosTipKitHandling"),
        serialization_alias="iosTipKitHandling",
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_renamed_alert_keys(cls, data: Any) -> Any:
        # `systemAlertHandling` renamed `alertHandling`, which had itself renamed `dismissAlerts`
        # (BE-0317 / BE-0327). Both aliases were deleted rather than carried a third time (BE-0401),
        # so name the canonical key here instead of leaving Pydantic's generic extra-field error.
        for old in ("alertHandling", "dismissAlerts"):
            reject_renamed_key(data, surface="scenario", old=old, new="systemAlertHandling")
        return data

    @field_validator("permissions")
    @classmethod
    def _validate_permissions(cls, v: dict[str, str]) -> dict[str, str]:
        for service, action in v.items():
            if service not in PERMISSION_SERVICES:
                raise ValueError(f"unknown permission service: {service!r}")
            if action not in _PERMISSION_ACTIONS:
                raise ValueError(f"unknown permission action: {action!r} (expected grant|revoke)")
        return v

    @model_validator(mode="after")
    def _one_data_source(self) -> Self:
        if self.data is not None and self.data_file is not None:
            raise ValueError("data and dataFile are mutually exclusive")
        return self


class Component(_Model):
    """A reusable, parameterized sequence of steps.

    `params` are the names a caller must supply via `use: { with: {...} }`; the steps reference them
    as `${params.<name>}`.
    """

    params: list[str] = Field(default_factory=list)
    steps: list[Step]


class ScenarioFile(_Model):
    """A scenario file: an optional file-level `description` plus the scenarios it defines.

    Two on-disk forms are accepted: the bare list of scenarios (no file description), or a
    `{description: "...", scenarios: [...]}` mapping.
    """

    # Named `schema` on disk (aliased to avoid shadowing BaseModel.schema). A file omitting it is
    # implicitly version 1; the version gate in load_scenario_file runs before this field validates
    # (BE-0119).
    schema_version: int = Field(default=SCHEMA_VERSION, alias="schema")
    description: str | None = None
    scenarios: list[Scenario]
