"""The iOS target's `subject`, `runsOn`, `driver`, and `run` extension (BE-0452)."""

from __future__ import annotations

from typing import Any, Literal, Self, get_args

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .._model import _Model
from ._shared import AppCommon, RangeField, Reinstall, RunsOnGroup, check_choices, check_list


class IosLaunch(_Model):
    """`subject.launch` on iOS: the environment and arguments the app is launched with."""

    env: dict[str, str] = Field(default_factory=dict)
    args: list[str] = Field(default_factory=list)
    # A URL the app opens once it has launched (BE-0452); a scenario's own value replaces it.
    deeplink: str | None = None


class IosApp(AppCommon):
    """`subject` on iOS: the bundle under test, how to build and install it, and how to launch it."""

    id: str = Field(min_length=1)
    path: str | None = None
    build: str | None = None
    reinstall: Reinstall | None = None
    launch: IosLaunch = Field(default_factory=IosLaunch)


IosKind = Literal["simulator", "device"]
# The device families `runsOn.ios.family` names, as `DeviceFacts.family` reads them (BE-0468).
IosFamily = Literal["iphone", "ipad"]


class IosDestination(_Model):
    """`runsOn.ios`: the conditions a Simulator must meet, and its device state.

    `model`, `os`, and `family` are conditions the run matches against the available devices;
    `locale` is device state the run applies. `kind` says whether the target runs on a Simulator, a
    physical device, or each of them in turn (BE-0468).
    """

    model: str | list[str] | None = None
    os: RangeField = None
    # A list runs once per listed kind; `family` is a priority list, one run on the first that fits.
    kind: IosKind | list[IosKind] = "simulator"
    family: IosFamily | list[IosFamily] | None = None
    locale: str | None = None
    # Images seeded into the Simulator's photo library on the erase path, in order, so
    # `selectPhotos` addresses a known library (BE-0452). Paths resolve against the config file; a
    # scenario's own list replaces this one, and the merged list needs an effective `run.erase`.
    seed_photos: list[str] = Field(default_factory=list, alias="seedPhotos")

    @property
    def kinds(self) -> list[IosKind]:
        """Every kind the target runs on, in the order written."""
        return list(self.kind) if isinstance(self.kind, list) else [self.kind]

    @field_validator("model")
    @classmethod
    def _valid_model(cls, v: str | list[str] | None) -> str | list[str] | None:
        return check_list("runsOn.ios.model", v)

    @field_validator("kind", "family", mode="before")
    @classmethod
    def _known_value(cls, v: Any, info: ValidationInfo) -> Any:
        allowed = get_args(IosKind) if info.field_name == "kind" else get_args(IosFamily)
        return check_choices(f"runsOn.ios.{info.field_name}", v, allowed)

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, v: IosKind | list[IosKind]) -> IosKind | list[IosKind]:
        check_list("runsOn.ios.kind", list(v) if isinstance(v, list) else v)
        return v

    @field_validator("family")
    @classmethod
    def _valid_family(
        cls, v: IosFamily | list[IosFamily] | None
    ) -> IosFamily | list[IosFamily] | None:
        # A repeat in a priority list names no fallback, so it reads as a mistake as it does in `model`.
        check_list("runsOn.ios.family", list(v) if isinstance(v, list) else v)
        return v

    @model_validator(mode="after")
    def _family_or_model(self) -> Self:
        # Both name the device a run takes, so together one would silently narrow the other. This runs
        # on the merged dictionary, so a target clears an inherited one with `null`.
        if self.family is not None and self.model is not None:
            raise ValueError(
                "runsOn.ios.family and runsOn.ios.model both name the device a run takes: "
                "declare one of them (clear an inherited one with null)"
            )
        return self

    @model_validator(mode="after")
    def _no_condition_on_a_physical_device(self) -> Self:
        # A physical iPhone's model and release are not matched before the run, so a condition on
        # them could only be ignored; it fails at load instead. `family` is read from a real device
        # too, so it stays allowed (BE-0468). This runs on the merged dictionary, so a team-wide
        # `model` default reaches here too, and the target clears it with `model: null`.
        if "device" in self.kinds:
            declared = [f for f in ("model", "os") if getattr(self, f) is not None]
            if declared:
                # Every offending field at once, so removing one never uncovers the next.
                names = " and ".join(f"runsOn.ios.{f}" for f in declared)
                verb = "is a device condition" if len(declared) == 1 else "are device conditions"
                raise ValueError(
                    f"{names} {verb}, which a target whose "
                    "kind includes device cannot declare: a physical device has no pool to match"
                )
        return self


class IosRunsOn(RunsOnGroup):
    """`runsOn` on iOS: the one destination an iOS target runs on (BE-0469)."""

    ios: IosDestination = Field(default_factory=IosDestination)


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
