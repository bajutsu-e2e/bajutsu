"""The Android target's `subject`, `runsOn`, `driver`, and `run` extension (BE-0452)."""

from __future__ import annotations

from typing import Any, Literal, Self, cast, get_args

from pydantic import Field, field_validator, model_validator

from .._model import _Model
from ._shared import (
    ANY_KIND,
    AppCommon,
    RangeField,
    Reinstall,
    RunsOnGroup,
    check_kind,
    check_list,
)


class AndroidLaunch(_Model):
    """`subject.launch` on Android: intent extras, and a URL the app opens once it has launched."""

    # Android reads the launch environment as intent extras.
    env: dict[str, str] = Field(default_factory=dict)
    deeplink: str | None = None


class AndroidApp(AppCommon):
    """`subject` on Android: the package under test, how to build and install it, and how to launch it."""

    id: str = Field(min_length=1)
    path: str | None = None
    build: str | None = None
    # The app's source repository, whose commit the run's provenance records (BE-0506).
    source_root: str | None = Field(default=None, alias="sourceRoot")
    reinstall: Reinstall | None = None
    launch: AndroidLaunch = Field(default_factory=AndroidLaunch)
    # Runtime permissions granted before launch (`pm grant`, BE-0210), so a prompt never blocks a run.
    grant_permissions: list[str] = Field(default_factory=list, alias="grantPermissions")


AndroidKind = Literal["emulator", "device"]


class AndroidDestination(_Model):
    """`runsOn.android`: the device kind, the emulator's AVD, the API level, and device state.

    AVD is the Android Virtual Device an emulator boots.
    `avd` and `apiLevel` are conditions the run matches; `locale` and `seedPhotos` are device state
    it applies. `kind` says whether the target runs on an emulator, a physical device, each of them
    in turn, or whichever of them can run the scenario (BE-0518).
    """

    # A list runs once per listed kind; `any` runs once on one of them.
    kind: AndroidKind | Literal["any"] | list[AndroidKind] = "emulator"
    avd: str | list[str] | None = None
    api_level: RangeField = Field(default=None, alias="apiLevel")
    # The app's own language, set per app with `cmd locale` before every launch (API level 33 and
    # later, BE-0478). Unset keeps the device's language, unlike iOS's built-in `en_US`: a default
    # would change every existing Android run and demand API level 33 of every device.
    locale: str | None = Field(default=None, min_length=1)
    # Images seeded into the device's shared storage before launch, in order, so a photo picker
    # addresses a known set. Paths resolve against the config file; a scenario's own list replaces
    # this one. Unlike iOS, no wipe is needed: the seed resets a directory Bajutsu owns.
    seed_photos: list[str] = Field(default_factory=list, alias="seedPhotos")

    @property
    def kinds(self) -> list[AndroidKind]:
        """Every kind the target may run on, in the order written; `any` reads as both."""
        if self.kind == ANY_KIND:
            return list(get_args(AndroidKind))
        if isinstance(self.kind, list):
            return list(self.kind)
        # `any` returned above, so the single value is one of the platform's own kinds.
        return [cast(AndroidKind, self.kind)]

    @field_validator("kind", mode="before")
    @classmethod
    def _known_kind(cls, v: Any) -> Any:
        return check_kind("runsOn.android.kind", v, get_args(AndroidKind))

    @field_validator("avd")
    @classmethod
    def _valid_avd(cls, v: str | list[str] | None) -> str | list[str] | None:
        return check_list("runsOn.android.avd", v)

    @model_validator(mode="after")
    def _no_avd_on_a_physical_device(self) -> Self:
        # A real device has no AVD, so the condition could never match it (BE-0518). `apiLevel` is
        # read from a real device too, so it stays allowed. This runs on the merged dictionary, so a
        # team-wide `avd` default reaches here too, and the target clears it with `avd: null`.
        if "device" in self.kinds and self.avd is not None:
            raise ValueError(
                "runsOn.android.avd is an emulator condition, which a target whose kind is any or "
                "includes device cannot declare: a physical device has no Android Virtual Device"
            )
        return self


class AndroidRunsOn(RunsOnGroup):
    """`runsOn` on Android: the one destination an Android target runs on (BE-0469)."""

    android: AndroidDestination = Field(default_factory=AndroidDestination)


class ResidentRecovery(_Model):
    """`driver.residentRecovery` on Android: what a timed-out resident read tries before the dump path."""

    # Reads to repeat on a fresh connection. A timeout does not show the server is gone, and a read
    # changes nothing on the device, so repeating one is safe.
    read_retries: int = Field(default=1, ge=0, le=5, alias="readRetries")
    # Whether to restart the server once the retries are spent, and before a non-ASCII `type` step
    # after the channel has been lost.
    restart: bool = True


class AndroidDriver(_Model):
    """`driver` on Android: how Bajutsu drives the device through adb."""

    # Measure each opted-in view's `View.getZ()` (BE-0355); off by default because the walk costs
    # 20-100ms on every read and answers nothing unless the app links `BajutsuAndroid`.
    native_z: bool = Field(default=False, alias="nativeZ")
    resident_recovery: ResidentRecovery = Field(
        default_factory=ResidentRecovery, alias="residentRecovery"
    )
