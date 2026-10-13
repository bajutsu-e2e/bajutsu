"""The Android target's `app`, `runsOn`, `driver`, and `run` extension (BE-0452)."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from .._model import _Model
from ._shared import AppCommon, LaunchEnv, check_list, check_range, coerce_range


class AndroidApp(AppCommon):
    """`app` on Android: the package under test, how to build and install it, and how to launch it."""

    id: str = Field(min_length=1)
    path: str | None = None
    build: str | None = None
    # Android reads the launch environment as intent extras.
    launch: LaunchEnv = Field(default_factory=LaunchEnv)
    # Runtime permissions granted before launch (`pm grant`, BE-0210), so a prompt never blocks a run.
    grant_permissions: list[str] = Field(default_factory=list, alias="grantPermissions")


class AndroidRunsOn(_Model):
    """`runsOn` on Android: the emulator's Android Virtual Device (AVD) name and API level."""

    avd: str | list[str] | None = None
    api_level: str | None = Field(default=None, alias="apiLevel")

    @field_validator("api_level", mode="before")
    @classmethod
    def _valid_api_level_number(cls, v: Any) -> Any:
        return coerce_range(v)

    @field_validator("api_level")
    @classmethod
    def _valid_api_level(cls, v: str | None) -> str | None:
        return check_range(v)

    @field_validator("avd")
    @classmethod
    def _valid_avd(cls, v: str | list[str] | None) -> str | list[str] | None:
        return check_list("runsOn.avd", v)


class AndroidDriver(_Model):
    """`driver` on Android: how Bajutsu drives the device through adb."""

    # Measure each opted-in view's `View.getZ()` (BE-0355); off by default because the walk costs
    # 20-100ms on every read and answers nothing unless the app links `BajutsuAndroid`.
    native_z: bool = Field(default=False, alias="nativeZ")


class AndroidRun(_Model):
    """The Android fields of `run`: none beyond the shared run policy."""
