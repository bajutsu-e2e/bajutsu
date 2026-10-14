"""The iOS target's `app`, `runsOn`, `driver`, and `run` extension (BE-0452)."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from .._model import _Model
from ._shared import AppCommon, check_list, check_range, coerce_range


class IosLaunch(_Model):
    """`app.launch` on iOS: the environment and arguments the app is launched with."""

    env: dict[str, str] = Field(default_factory=dict)
    args: list[str] = Field(default_factory=list)


class IosApp(AppCommon):
    """`app` on iOS: the bundle under test, how to build and install it, and how to launch it."""

    id: str = Field(min_length=1)
    path: str | None = None
    build: str | None = None
    launch: IosLaunch = Field(default_factory=IosLaunch)


class IosRunsOn(_Model):
    """`runsOn` on iOS: the conditions a Simulator must meet, and its device state.

    `model` and `os` are conditions the run matches against the available Simulators; `locale` is
    device state the run applies. `kind` says whether the target is a Simulator or a physical device.
    """

    model: str | list[str] | None = None
    os: str | None = None
    kind: Literal["simulator", "device"] = "simulator"
    locale: str | None = None

    @field_validator("os", mode="before")
    @classmethod
    def _valid_os_number(cls, v: Any) -> Any:
        return coerce_range(v)

    @field_validator("os")
    @classmethod
    def _valid_os(cls, v: str | None) -> str | None:
        return check_range(v)

    @field_validator("model")
    @classmethod
    def _valid_model(cls, v: str | list[str] | None) -> str | list[str] | None:
        return check_list("runsOn.model", v)

    @model_validator(mode="after")
    def _no_condition_on_a_physical_device(self) -> Self:
        # A physical iPhone has no pool to read before the run, so a condition on it could only be
        # ignored; it fails at load instead. This runs on the merged dictionary, so a team-wide
        # `model` default reaches here too, and the target clears it with `model: null`.
        if self.kind == "device":
            declared = [f for f in ("model", "os") if getattr(self, f) is not None]
            if declared:
                raise ValueError(
                    f"runsOn.{declared[0]} is a device condition, which a target with "
                    "kind: device cannot declare: a physical device has no pool to match"
                )
        return self


class IosRunner(_Model):
    """`driver.runner`: the prebuilt XCUITest runner, or the command that builds it (BE-0019)."""

    test_runner: str | None = Field(default=None, alias="testRunner")
    build: str | None = None
    # The host address(es) a real device reaches the network collector on, comma-separated; the
    # former `xcuitest.hostAddress`, which moved with the rest of the runner block (BE-0452).
    host_address: str | None = Field(default=None, alias="hostAddress")


class IosDriver(_Model):
    """`driver` on iOS: how Bajutsu drives the app through XCUITest."""

    runner: IosRunner = Field(default_factory=IosRunner)


class IosRun(_Model):
    """The iOS fields of `run`, added to the run policy every platform shares."""

    tip_kit_handling: bool | None = Field(default=None, alias="tipKitHandling")
