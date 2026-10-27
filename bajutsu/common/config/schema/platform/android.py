"""The Android target's `app`, `runsOn`, `driver`, and `run` extension (BE-0452)."""

from __future__ import annotations

from pydantic import Field, field_validator

from .._model import _Model
from ._shared import AppCommon, RangeField, Reinstall, RunsOnGroup, check_list


class AndroidLaunch(_Model):
    """`app.launch` on Android: intent extras, and a URL the app opens once it has launched."""

    # Android reads the launch environment as intent extras.
    env: dict[str, str] = Field(default_factory=dict)
    deeplink: str | None = None


class AndroidApp(AppCommon):
    """`app` on Android: the package under test, how to build and install it, and how to launch it."""

    id: str = Field(min_length=1)
    path: str | None = None
    build: str | None = None
    reinstall: Reinstall | None = None
    launch: AndroidLaunch = Field(default_factory=AndroidLaunch)
    # Runtime permissions granted before launch (`pm grant`, BE-0210), so a prompt never blocks a run.
    grant_permissions: list[str] = Field(default_factory=list, alias="grantPermissions")


class AndroidDestination(_Model):
    """`runsOn.android`: the emulator's Android Virtual Device (AVD) name and API level."""

    avd: str | list[str] | None = None
    api_level: RangeField = Field(default=None, alias="apiLevel")

    @field_validator("avd")
    @classmethod
    def _valid_avd(cls, v: str | list[str] | None) -> str | list[str] | None:
        return check_list("runsOn.android.avd", v)


class AndroidRunsOn(RunsOnGroup):
    """`runsOn` on Android: the one destination an Android target runs on (BE-0469)."""

    android: AndroidDestination = Field(default_factory=AndroidDestination)


class AndroidDriver(_Model):
    """`driver` on Android: how Bajutsu drives the device through adb."""

    # Measure each opted-in view's `View.getZ()` (BE-0355); off by default because the walk costs
    # 20-100ms on every read and answers nothing unless the app links `BajutsuAndroid`.
    native_z: bool = Field(default=False, alias="nativeZ")


class AndroidRun(_Model):
    """The Android fields of `run`: none beyond the shared run policy."""
