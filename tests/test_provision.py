"""Config-aware environment installer (BE-0164): pure planning + idempotent execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu import provision, requirements
from bajutsu.config import load_config
from bajutsu.requirements import Brew, Extra, Playwright, Tool

# --- plan(): resolve exactly what a config's backends + AI provider need ---------------------


def test_plan_ios_target_needs_xcodebuild_and_no_extra() -> None:
    # iOS is XCUITest-only (BE-0290): it needs Xcode's `xcodebuild` and no pip extra.
    cfg = load_config("targets:\n  demo:\n    bundleId: com.example.demo\n    backend: [ios]\n")
    p = provision.plan(cfg)
    assert p.extras == ()
    assert any(t.exe == "xcodebuild" for t in p.tools)


def test_plan_web_target_includes_the_configured_engine_browser() -> None:
    cfg = load_config(
        "targets:\n  site:\n    baseUrl: http://x/index.html\n"
        "    backend: [web]\n    browser: firefox\n"
    )
    p = provision.plan(cfg)
    assert p.extras == ("web",)
    assert Tool("firefox", Playwright("firefox")) in p.tools


def test_plan_fake_target_installs_nothing() -> None:
    cfg = load_config("targets:\n  t:\n    bundleId: com.example.t\n    backend: [fake]\n")
    assert provision.plan(cfg).is_empty


def test_plan_dedupes_across_targets() -> None:
    cfg = load_config(
        "targets:\n"
        "  a:\n    bundleId: com.example.a\n    backend: [ios]\n"
        "  b:\n    bundleId: com.example.b\n    backend: [ios]\n"
    )
    p = provision.plan(cfg)
    assert [t.exe for t in p.tools].count("xcodebuild") == 1  # shared tool listed once


def test_plan_detects_a_configured_ai_provider() -> None:
    cfg = load_config(
        "defaults:\n  ai:\n    provider: api-key\n"
        "targets:\n  demo:\n    bundleId: com.example.demo\n    backend: [ios]\n"
    )
    assert "ai" in provision.plan(cfg).extras


def test_plan_without_ai_config_omits_the_ai_extra() -> None:
    cfg = load_config("targets:\n  demo:\n    bundleId: com.example.demo\n    backend: [ios]\n")
    assert "ai" not in provision.plan(cfg).extras


def test_plan_with_no_targets_installs_no_backend() -> None:
    # Backends come from targets.*; a config that declares no target references no backend, so a
    # bare `make install` at a repo with no config installs nothing beyond the base (BE-0164).
    from bajutsu.config import Config

    assert provision.plan(Config()).is_empty
    assert provision.plan(load_config("defaults:\n  backend: [web]\n")).is_empty


def test_plan_no_targets_still_detects_a_defaults_ai_provider() -> None:
    # AI is a config-level signal (defaults.ai), independent of any target — so it is installed
    # even when no target is declared, unlike backends.
    cfg = load_config("defaults:\n  ai:\n    provider: api-key\n")
    assert provision.plan(cfg).extras == ("ai",)


def test_plan_for_backends_forces_a_specific_backend() -> None:
    # The `make deps` path: provision the given backend regardless of any config.
    p = provision.plan_for_backends(["web"])
    assert p.extras == ("web",)


def test_plan_for_backends_includes_android_adb() -> None:
    # `android` resolves to the `adb` actuator (BE-0007): its one tool is the platform-tools `adb`
    # binary, provisioned via Homebrew.
    p = provision.plan_for_backends(["android"])
    assert Tool("adb", Brew("android-platform-tools")) in p.tools


# --- provision(): idempotent execution over injectable subprocess seams ----------------------


def _captured() -> tuple[list[tuple[str, ...]], provision.Runner]:
    ran: list[tuple[str, ...]] = []
    return ran, lambda cmd: ran.append(tuple(cmd))


def test_provision_syncs_the_needed_extras() -> None:
    ran, run = _captured()
    provision.provision(provision.InstallPlan(("visual", "web"), ()), run=run)
    assert ran == [("uv", "sync", "--extra", "visual", "--extra", "web")]


def test_provision_skips_a_tool_already_on_path() -> None:
    ran, run = _captured()
    plan = provision.InstallPlan((), (Tool("adb", Brew("android-platform-tools")),))
    report = provision.provision(
        plan, which=lambda exe: f"/opt/{exe}", run=run, system=lambda: "Darwin"
    )
    assert ran == [] and report.manual == ()


def test_provision_brew_installs_a_missing_tool_on_macos() -> None:
    ran, run = _captured()
    plan = provision.InstallPlan((), (Tool("adb", Brew("android-platform-tools")),))
    provision.provision(
        plan,
        which=lambda exe: "/usr/bin/brew" if exe == "brew" else None,
        run=run,
        system=lambda: "Darwin",
    )
    assert ("brew", "install", "android-platform-tools") in ran


def test_provision_reports_manual_when_brew_is_unavailable() -> None:
    ran, run = _captured()
    plan = provision.InstallPlan((), (Tool("adb", Brew("android-platform-tools")),))
    report = provision.provision(plan, which=lambda _exe: None, run=run, system=lambda: "Linux")
    assert ran == []
    assert report.manual == (requirements.remedy(Brew("android-platform-tools")),)


def test_provision_always_runs_the_idempotent_playwright_installer() -> None:
    # The executed command mirrors `remedy(Playwright(...))` so the installer and preflight advice
    # never drift (BE-0164): `uv run playwright install <engine>`.
    ran, run = _captured()
    plan = provision.InstallPlan(("web",), (requirements.playwright_browser("chromium"),))
    provision.provision(plan, which=lambda _exe: None, run=run)
    assert ("uv", "run", "playwright", "install", "chromium") in ran
    # the executed command is exactly the rendered remedy (sans backticks) — one source of truth
    assert " ".join(ran[-1]) == requirements.remedy(Playwright("chromium")).strip("`")


def test_provision_reports_a_manual_only_tool_when_missing() -> None:
    # xcuitest's Xcode can't be auto-installed: missing -> a manual remedy, present -> skipped.
    ran, run = _captured()
    plan = provision.InstallPlan((), requirements.BACKENDS["xcuitest"].tools)
    report = provision.provision(plan, which=lambda _exe: None, run=run, system=lambda: "Darwin")
    assert ran == []
    assert any("Xcode" in note for note in report.manual)

    present = provision.provision(
        plan, which=lambda exe: f"/opt/{exe}", run=run, system=lambda: "Darwin"
    )
    assert present.manual == ()


def test_provision_extra_backed_tool_is_covered_by_the_extras_sync() -> None:
    # An Extra-backed tool (its client comes from a pip extra) needs no separate install action —
    # the extras sync covers it; only a Brew-backed tool triggers a `brew install`.
    ran, run = _captured()
    plan = provision.InstallPlan(
        ("web",),
        (
            Tool("playwright", Extra("web")),
            Tool("adb", Brew("android-platform-tools")),
        ),
    )
    provision.provision(
        plan,
        which=lambda exe: "/usr/bin/brew" if exe == "brew" else None,
        run=run,
        system=lambda: "Darwin",
    )
    assert ran == [
        ("uv", "sync", "--extra", "web"),
        ("brew", "install", "android-platform-tools"),
    ]


# --- CLI: config loading + the dry-run / forced-backend / empty paths ------------------------


def test_load_missing_explicit_config_exits(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        provision._load(str(tmp_path / "nope.yaml"))


def test_load_reads_an_existing_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "bajutsu.config.yaml"
    cfg_path.write_text("targets:\n  demo:\n    bundleId: com.example.demo\n    backend: [ios]\n")
    cfg = provision._load(str(cfg_path))
    assert "demo" in cfg.targets


def test_main_dry_run_prints_the_plan_without_installing(tmp_path: Path) -> None:
    cfg_path = tmp_path / "bajutsu.config.yaml"
    cfg_path.write_text("targets:\n  demo:\n    bundleId: com.example.demo\n    backend: [ios]\n")
    assert provision.main(["--config", str(cfg_path), "--dry-run"]) == 0


def test_main_forced_backend_dry_run(tmp_path: Path) -> None:
    assert provision.main(["--backend", "android", "--dry-run"]) == 0


def test_main_empty_config_installs_nothing(tmp_path: Path) -> None:
    cfg_path = tmp_path / "bajutsu.config.yaml"
    cfg_path.write_text("targets:\n  t:\n    bundleId: com.example.t\n    backend: [fake]\n")
    assert provision.main(["--config", str(cfg_path)]) == 0
