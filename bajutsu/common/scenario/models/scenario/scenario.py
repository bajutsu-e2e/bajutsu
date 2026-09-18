"""One scenario: the unit `run` executes and returns a verdict for."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import (
    AliasChoices,
    BeforeValidator,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from bajutsu.common.deprecations import reject_renamed_key
from bajutsu.common.drivers.base import PERMISSION_SERVICES
from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.models.assertions import Assertion
from bajutsu.common.scenario.models.evidence import CaptureRule, Network, Redact
from bajutsu.common.scenario.models.mocks import Mock
from bajutsu.common.scenario.models.steps import AfterRule, Interrupt, Step

from ._functions import _coerce_system_alert_handling
from ._targets import _check_target_requirements
from .preconditions import Preconditions
from .system_alert_handling import SystemAlertHandling

# The grant/revoke actions a `permissions` entry may take (BE-0276); the service side of the
# vocabulary (`PERMISSION_SERVICES`) lives in `drivers.base` since every backend's capability
# advertisement already depends on it — reused here rather than duplicated.
_PERMISSION_ACTIONS = ("grant", "revoke")


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
    # Every target this scenario drives (BE-0428): each entry names a `targets.<name>` config unit,
    # launched before the first step and torn down together with the rest after the last one. Empty
    # (the default) is today's single-target scenario, resolved entirely from the CLI's `--target` —
    # a per-step `target` is then optional and, if set, must name that one target.
    targets: list[str] = Field(default_factory=list)
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
    # Load-time provenance (BE-0417), set by a file loader after parsing. A `PrivateAttr` rather
    # than an ordinary field so it never leaks into `model_dump()` as part of the authored schema.
    _source_stem: str | None = PrivateAttr(default=None)

    @property
    def source_stem(self) -> str | None:
        """The stem of the file this scenario was loaded from, or `None` outside a file loader."""
        return self._source_stem

    def set_source_stem(self, stem: str) -> None:
        """Record the stem of the file this scenario was loaded from (BE-0417)."""
        self._source_stem = stem

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

    @model_validator(mode="after")
    def _target_requirements(self) -> Self:
        # Extracted into `_check_target_requirements` (BE-0428) so `expand_components` and
        # `with_lifecycle_phases` — which each rebuild an already-validated `Scenario` in a way
        # Pydantic never re-validates — can run the same check again on their own result.
        _check_target_requirements(self)
        return self
