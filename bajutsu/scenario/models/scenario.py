"""The top-level shapes a scenario file is made of.

Preconditions, the alert-guard control, the scenario and its reusable component, and the
scenario-file wrapper that ties them together.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from bajutsu.drivers.base import PERMISSION_SERVICES
from bajutsu.scenario.models._base import _Model
from bajutsu.scenario.models.assertions import Assertion
from bajutsu.scenario.models.evidence import CaptureRule, Network, Redact
from bajutsu.scenario.models.mocks import Mock
from bajutsu.scenario.models.steps import Interrupt, Step

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


class DismissAlerts(_Model):
    """Per-scenario control of the system-alert guard.

    The vision-based dismissal of OS prompts (e.g. iOS "Save Password?", a permission request) that
    the app-scoped accessibility tree cannot see or tap. The guard is ON by default and fires only when a step (or `expect`) is blocked: it
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
    # Provenance (BE-0044): the original natural-language goal `record` authored this scenario
    # from. Authoring metadata only — `run` never reads it. Kept None (pruned) when unset.
    from_: str | None = Field(default=None, alias="from")
    tags: list[str] = Field(default_factory=list)
    # Per-scenario OS permission state (BE-0276), applied before the app process starts: grant or
    # revoke a permission up front so the runtime prompt never appears (iOS `simctl privacy`,
    # Android `pm grant`/`pm revoke`). Deterministic and AI-free, unlike the vision dismissAlerts
    # guard below, which reacts to a prompt only after it appears. Kept as a plain `dict[str, str]`
    # (validated below against the vocabulary) rather than a `Literal`-keyed dict, so it stays
    # assignable to the `Mapping[str, str]` the platform-lifecycle `start()` seam expects.
    permissions: dict[str, str] = Field(default_factory=dict)
    # Handlers for interstitial screens that surface at an unpredictable point (BE-0314): each entry
    # names a `condition` (the assertion DSL `if` uses) and the `steps` that clear it. The runner
    # checks each opportunistically against trees it has already fetched, wherever the screen appears
    # — so an author need not predict the one spot to place an `if`. Appended to the target config's
    # own `interrupts` (config entries first), mirroring how `dismissAlerts` layers config under
    # scenario. Empty (the default) means no scenario-level handler, so it prunes from a dump.
    interrupts: list[Interrupt] = Field(default_factory=list)
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
