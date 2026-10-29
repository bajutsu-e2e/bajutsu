"""The web target's `app`, `runsOn`, `driver`, and `run` extension (BE-0452)."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from bajutsu.common.devices.browsers import BROWSER_KINDS, BrowserKind

from .._model import _Model
from ._shared import AppCommon, LaunchEnv, RangeField, RunsOnGroup, check_choices, check_list


class WebApp(AppCommon):
    """`app` on the web: the page under test."""

    url: str = Field(min_length=1)
    # The web run passes the launch environment nowhere; Playwright code generation reads it.
    launch: LaunchEnv = Field(default_factory=LaunchEnv)


class WebBrowser(_Model):
    """`runsOn.browser`: which browser runs the page, its version range, and the device it emulates."""

    # A list runs once per listed browser, as the cross-browser matrix does (BE-0076, BE-0468).
    kind: BrowserKind | list[BrowserKind] = "chromium"
    version: RangeField = None
    # A Playwright device preset name (BE-0228), resolved when the driver starts so loading never
    # imports Playwright; "desktop" is the plain desktop context.
    emulate: str = "desktop"

    @model_validator(mode="before")
    @classmethod
    def _engine_became_kind(cls, data: Any) -> Any:
        # The rename keeps every run, so the message says how to rewrite rather than only rejecting.
        if isinstance(data, dict) and "engine" in data:
            raise ValueError(
                "runsOn.browser.engine was replaced by runsOn.browser.kind (BE-0468): rename the "
                "field; a kind list runs once per listed browser, as an engine list did, and kind "
                f"also takes chrome and edge ({', '.join(BROWSER_KINDS)})"
            )
        return data

    @field_validator("kind", mode="before")
    @classmethod
    def _known_kind(cls, v: Any) -> Any:
        # Fail a misspelled browser at load, not when the driver starts (BE-0076).
        return check_choices("browser", v, BROWSER_KINDS)

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, v: BrowserKind | list[BrowserKind]) -> BrowserKind | list[BrowserKind]:
        check_list("runsOn.browser.kind", list(v) if isinstance(v, list) else v)
        return v

    @model_validator(mode="after")
    def _no_version_across_kinds(self) -> Self:
        # Browsers number their versions differently, so one range cannot mean the same for each.
        if isinstance(self.kind, list) and self.version is not None:
            raise ValueError(
                "runsOn.browser.version cannot be combined with a list of browser kinds: "
                "each browser numbers its versions differently"
            )
        return self


class WebRunsOn(RunsOnGroup):
    """`runsOn` on the web: the one destination a web target runs on, a browser (BE-0469)."""

    browser: WebBrowser = Field(default_factory=WebBrowser)


class WebDriver(_Model):
    """`driver` on the web: how Bajutsu shows the browser it drives."""

    headless: bool = True


class WebRun(_Model):
    """The web fields of `run`: none beyond the shared run policy."""
