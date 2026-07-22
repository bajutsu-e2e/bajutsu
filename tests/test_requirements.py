"""The single declarative mapping of backend/capability -> pip extra + external tools (BE-0164)."""

from __future__ import annotations

from bajutsu import requirements as req


def test_remedy_renders_each_install_method_as_a_command() -> None:
    assert req.remedy(req.Extra("web")) == "`uv sync --extra web`"
    assert req.remedy(req.Brew("android-platform-tools")) == (
        "`brew install android-platform-tools`"
    )
    assert req.remedy(req.Playwright("firefox")) == "`uv run playwright install firefox`"
    # A Manual method carries the full prose remedy (no auto-install), returned verbatim.
    assert req.remedy(req.Manual("Xcode — `xcode-select --install`")) == (
        "Xcode — `xcode-select --install`"
    )


def test_adb_backend_needs_the_platform_tools_formula() -> None:
    r = req.BACKENDS["adb"]
    adb_tool = next(t for t in r.tools if t.exe == "adb")
    assert adb_tool.install == req.Brew("android-platform-tools")


def test_web_backend_needs_the_web_extra_and_no_static_browser_tool() -> None:
    # The browser is engine-specific (chosen per run), so it is not a static tool — it is built
    # on demand by `playwright_browser`, not baked into the backend's tool list.
    r = req.BACKENDS["playwright"]
    assert r.extra == "web"
    assert r.tools == ()


def test_playwright_browser_builds_an_engine_specific_tool() -> None:
    tool = req.playwright_browser("webkit")
    assert tool == req.Tool("webkit", req.Playwright("webkit"))


def test_xcuitest_needs_xcode_with_no_auto_install() -> None:
    r = req.BACKENDS["xcuitest"]
    assert r.extra is None
    xcodebuild = next(t for t in r.tools if t.exe == "xcodebuild")
    assert isinstance(xcodebuild.install, req.Manual)


def test_fake_backend_needs_nothing() -> None:
    r = req.BACKENDS["fake"]
    assert r.extra is None and r.tools == ()


def test_capabilities_map_to_their_extras() -> None:
    assert req.CAPABILITIES["ai"].extra == "ai"
    assert req.CAPABILITIES["visual"].extra == "visual"
    assert req.CAPABILITIES["mcp"].extra == "mcp"
