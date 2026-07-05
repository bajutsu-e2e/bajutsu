"""The single declarative source of truth for what each backend/capability needs (BE-0164).

One mapping of backend family (`idb` / `playwright` / `xcuitest` / `fake`) and optional
capability (`ai` / `visual` / `mcp`) to: the pip extra to sync and the external tools to have on
PATH, each with how to install it when missing (a Homebrew formula, a `playwright install
<browser>`, a pip extra, or a manual-only hint). ``preflight``'s remedy strings and the
config-aware installer (``provision``) both read from here, so the same fact is never hardcoded
in two places that can drift.

Pure data plus a pure ``remedy`` renderer — no subprocess and no config — so it stays in the
deterministic core and is trivially testable. A new backend plugs its requirements in here rather
than forking the installer or the preflight (prime directive #3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never


@dataclass(frozen=True)
class Extra:
    """Provided by syncing a pip extra (``uv sync --extra <name>``)."""

    name: str


@dataclass(frozen=True)
class Brew:
    """Provided by a Homebrew formula (macOS only): ``brew install <formula>``."""

    formula: str


@dataclass(frozen=True)
class Playwright:
    """Provided by Playwright's own downloader: ``playwright install <browser>``."""

    browser: str


@dataclass(frozen=True)
class Manual:
    """No automatic install exists — ``hint`` tells the user what to do (e.g. install Xcode)."""

    hint: str


# How a piece is obtained when missing. The installer dispatches on the concrete type; `preflight`
# renders it into a remedy line via `remedy`.
InstallMethod = Extra | Brew | Playwright | Manual


@dataclass(frozen=True)
class Tool:
    """An external piece a backend needs, and how to obtain it.

    ``exe`` is the ``command -v`` probe name (also the label `preflight`/`doctor` shows);
    ``install`` is how the installer provisions it when the probe misses.
    """

    exe: str
    install: InstallMethod


@dataclass(frozen=True)
class Requirement:
    """What a backend or capability needs: an optional pip extra plus external tools."""

    extra: str | None = None
    tools: tuple[Tool, ...] = ()


def remedy(method: InstallMethod) -> str:
    """The one-line remedy for an install method — the command to run, or a manual hint verbatim."""
    match method:
        case Extra(name):
            return f"`uv sync --extra {name}`"
        case Brew(formula):
            return f"`brew install {formula}`"
        case Playwright(browser):
            return f"`uv run playwright install {browser}`"
        case Manual(hint):
            return hint
        case _:  # pragma: no cover - exhaustive; a new InstallMethod is a mypy error here
            assert_never(method)


def playwright_browser(engine: str) -> Tool:
    """The browser tool for a Playwright engine, built on demand rather than baked in.

    The engine is chosen per run, so it is not a static entry in the web backend's tool list — a
    `firefox` run needs `firefox`, not `chromium`.
    """
    return Tool(engine, Playwright(engine))


# Backend actuator (bajutsu.backends actuator names) -> what it needs. `idb`'s python client comes
# from the `idb` extra (so `preflight` probes it as an Extra tool); `idb_companion` is the separate
# Homebrew formula. The web browser is engine-specific, so it is not listed here (see
# `playwright_browser`). `fake` needs nothing; a planned-but-unbuilt actuator (adb) is simply absent.
BACKENDS: dict[str, Requirement] = {
    "idb": Requirement(
        extra="idb",
        tools=(
            Tool("idb", Extra("idb")),
            Tool("idb_companion", Brew("facebook/fb/idb-companion")),
        ),
    ),
    "playwright": Requirement(extra="web"),
    "xcuitest": Requirement(
        tools=(Tool("xcodebuild", Manual("Xcode — `xcode-select --install`")),)
    ),
    "fake": Requirement(),
}

# Optional capability -> its pip extra (BE-0111 / BE-0048 / BE-0017). Centralized here for one
# source of truth; the installer wires the `ai` extra from a config's AI provider, the others are
# available for an explicit request.
CAPABILITIES: dict[str, Requirement] = {
    "ai": Requirement(extra="ai"),
    "visual": Requirement(extra="visual"),
    "mcp": Requirement(extra="mcp"),
}
