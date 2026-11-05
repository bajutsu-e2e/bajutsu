"""The browser kinds `runsOn.browser.kind` names, and how each launches (BE-0468)."""

from __future__ import annotations

import pytest

from bajutsu.common.devices import facts
from bajutsu.common.devices.browsers import BROWSER_KINDS, browser_launch
from bajutsu.common.drivers.playwright._functions import _channel


@pytest.mark.parametrize(
    ("kind", "launch"),
    [
        ("chromium", ("chromium", None)),
        ("chrome", ("chromium", "chrome")),
        ("edge", ("chromium", "msedge")),
        ("firefox", ("firefox", None)),
        ("webkit", ("webkit", None)),
    ],
)
def test_each_kind_launches_an_engine_on_its_channel(
    kind: str, launch: tuple[str, str | None]
) -> None:
    assert browser_launch(kind) == launch


def test_every_kind_has_a_launch_and_an_unknown_one_names_the_kinds() -> None:
    assert [browser_launch(k)[0] for k in BROWSER_KINDS]
    with pytest.raises(ValueError, match="invalid browser 'safari': use one of chromium, chrome"):
        browser_launch("safari")


def test_only_a_channel_browser_passes_a_channel_to_launch() -> None:
    assert _channel("msedge") == {"channel": "msedge"}
    assert _channel(None) == {}


def test_the_browser_facts_name_the_kind() -> None:
    read = facts.read_web("edge", lambda kind: {"edge": "131.0.2903.70"}[kind])
    assert (read.udid, read.model, read.release) == ("web-edge", "edge", "131.0.2903.70")
