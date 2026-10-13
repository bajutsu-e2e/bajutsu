"""The web target's `app`, `runsOn`, `driver`, and `run` extension (BE-0452)."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from .._model import _Model
from ._shared import AppCommon, LaunchEnv, check_list, check_range, coerce_range

# Playwright rendering engines a web target can drive (BE-0076). Chromium is the default, and all
# three run headless on Linux.
WEB_ENGINES = ("chromium", "firefox", "webkit")


class WebApp(AppCommon):
    """`app` on the web: the page under test."""

    url: str = Field(min_length=1)
    # The web run passes the launch environment nowhere; Playwright code generation reads it.
    launch: LaunchEnv = Field(default_factory=LaunchEnv)


class WebBrowser(_Model):
    """`runsOn.browser`: which engine runs the page, and the version range it must fall in."""

    engine: str | list[str] = "chromium"
    version: str | None = None

    @field_validator("engine")
    @classmethod
    def _valid_engine(cls, v: str | list[str]) -> str | list[str]:
        # Fail a misspelled engine at load, not as an AttributeError at driver start (BE-0076).
        for engine in [v] if isinstance(v, str) else v:
            if engine not in WEB_ENGINES:
                raise ValueError(f"invalid browser {engine!r}: use one of {', '.join(WEB_ENGINES)}")
        listed = check_list("runsOn.browser.engine", v)
        assert listed is not None
        return listed

    @field_validator("version", mode="before")
    @classmethod
    def _valid_version_number(cls, v: Any) -> Any:
        return coerce_range(v)

    @field_validator("version")
    @classmethod
    def _valid_version(cls, v: str | None) -> str | None:
        return check_range(v)

    @model_validator(mode="after")
    def _no_version_across_engines(self) -> Self:
        # Engines number their versions differently, so one range cannot mean the same thing for each.
        if isinstance(self.engine, list) and self.version is not None:
            raise ValueError(
                "runsOn.browser.version cannot be combined with a list of engines: "
                "each engine numbers its versions differently"
            )
        return self


class WebRunsOn(_Model):
    """`runsOn` on the web: the browser, and the device mode a context emulates (BE-0228)."""

    browser: WebBrowser = Field(default_factory=WebBrowser)
    # A Playwright device preset name, resolved when the driver starts so loading never imports
    # Playwright; "desktop" is the plain desktop context.
    emulate: str = "desktop"


class WebDriver(_Model):
    """`driver` on the web: how Bajutsu shows the browser it drives."""

    headless: bool = True


class WebRun(_Model):
    """The web fields of `run`: none beyond the shared run policy."""
