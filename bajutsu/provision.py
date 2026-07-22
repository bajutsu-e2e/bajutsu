"""Config-aware environment installer (BE-0164).

Reads a project's effective config, resolves which backends its ``targets.*`` actually use and
whether an AI provider is configured, and installs exactly the pip extras and external tools those
need — not "every backend unconditionally" and not "everything". A local, developer-invoked bootstrap step:
it never runs on a hosted or uploaded-config path (the boundary BE-0090 closed), runs before any
scenario, and is never part of a pass/fail decision (prime directive #1).

``plan`` is pure (config -> ``InstallPlan``) so it is unit-testable without touching the machine;
``provision`` executes a plan idempotently, shelling out only for tools that are actually missing.
Both read their facts from the one ``requirements`` mapping shared with ``preflight``, so a new
backend plugs in there rather than forking this installer (prime directive #3).
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from bajutsu import requirements
from bajutsu.backends import resolve_actuators
from bajutsu.config import Config, load_config, resolve, web_engine
from bajutsu.config_source import DEFAULT_CONFIG
from bajutsu.requirements import Brew, Extra, Manual, Playwright, Tool

Which = Callable[[str], str | None]
Runner = Callable[[list[str]], None]
System = Callable[[], str]


def _run(cmd: list[str]) -> None:
    # Fixed argv built from the requirements mapping, never a shell string.
    subprocess.run(cmd, check=True)


def _echo(msg: str) -> None:
    sys.stderr.write(f"{msg}\n")


@dataclass(frozen=True)
class InstallPlan:
    """The extras and external tools a config resolves to needing.

    ``tools`` may include Extra-backed entries (e.g. the web `playwright` package): those are covered
    by the ``extras`` sync, so ``provision`` takes no separate action for them.
    """

    extras: tuple[str, ...]
    tools: tuple[Tool, ...]

    @property
    def is_empty(self) -> bool:
        return not self.extras and not self.tools


@dataclass(frozen=True)
class ProvisionReport:
    """What an execution did: the commands it ran and the remedies it left for the user to do."""

    ran: tuple[tuple[str, ...], ...]
    manual: tuple[str, ...]


def plan(config: Config, *, ai_configured: bool | None = None) -> InstallPlan:
    """Resolve exactly what *config*'s backends and AI provider need.

    Args:
        config: the parsed project config.
        ai_configured: force the AI-provider decision; ``None`` derives it from the config
            (any target — or the defaults — that carries an ``ai`` block).
    """
    ai = _ai_configured(config) if ai_configured is None else ai_configured
    return _build(_resolved_actuators(config), ai=ai)


def plan_for_backends(backends: list[str], *, ai: bool = False) -> InstallPlan:
    """Resolve what an explicit list of backend tokens needs, independent of any config.

    The ``make deps`` path: provision the given backends regardless of what a project's config selects.
    """
    pairs = (
        (actuator, "chromium" if actuator == "playwright" else None)
        for token in backends
        for actuator in resolve_actuators([token])
    )
    return _build(pairs, ai=ai)


def provision(
    p: InstallPlan,
    *,
    which: Which = shutil.which,
    run: Runner = _run,
    system: System = platform.system,
) -> ProvisionReport:
    """Execute a plan idempotently: sync the extras, then install only the tools that are missing.

    The subprocess seams (``which`` / ``run`` / ``system``) are injectable so the logic tests
    without running a package manager.
    """
    host = system()
    ran: list[tuple[str, ...]] = []
    manual: list[str] = []
    if p.extras:
        cmd = ["uv", "sync", *(flag for extra in p.extras for flag in ("--extra", extra))]
        run(cmd)
        ran.append(tuple(cmd))
    for tool in p.tools:
        command, note = _tool_action(tool, which=which, host=host)
        if command is not None:
            run(command)
            ran.append(tuple(command))
        elif note is not None:
            manual.append(note)
    return ProvisionReport(tuple(ran), tuple(manual))


def _tool_action(tool: Tool, *, which: Which, host: str) -> tuple[list[str] | None, str | None]:
    """The action for one tool.

    Returns ``(command, None)`` to run, ``(None, remedy)`` to leave for the user, or
    ``(None, None)`` when nothing is needed (already present, or covered by the extras sync).
    """
    match tool.install:
        case Extra():
            return None, None  # the client is installed by the extras sync
        case Playwright(browser):
            # `playwright install` is idempotent (present browser = fast no-op), so run it
            # unconditionally — the browser binary is not a PATH executable to probe. The command
            # mirrors what `remedy()` renders, so preflight's advice and the installer never drift.
            return ["uv", "run", "playwright", "install", browser], None
        case Brew(formula):
            if which(tool.exe) is not None:
                return None, None
            if host == "Darwin" and which("brew") is not None:
                return ["brew", "install", formula], None
            return None, requirements.remedy(tool.install)
        case Manual(hint):
            return (None, None) if which(tool.exe) is not None else (None, hint)
    # Exhaustive over InstallMethod; explicit terminal so no path falls through implicitly (CodeQL).
    raise AssertionError(f"unhandled InstallMethod: {tool.install!r}")  # pragma: no cover


def _build(pairs: Iterable[tuple[str, str | None]], *, ai: bool) -> InstallPlan:
    extras: list[str] = []
    tools: list[Tool] = []
    for actuator, engine in pairs:
        req = requirements.BACKENDS.get(actuator)
        if req is None:
            continue  # a planned-but-unbuilt backend (adb): nothing to install yet
        if req.extra is not None:
            extras.append(req.extra)
        tools.extend(req.tools)
        if engine is not None:
            tools.append(requirements.playwright_browser(engine))
    if ai and (ai_extra := requirements.CAPABILITIES["ai"].extra) is not None:
        extras.append(ai_extra)
    return InstallPlan(_unique(extras), _unique_tools(tools))


def _resolved_actuators(config: Config) -> Iterator[tuple[str, str | None]]:
    """Each ``(actuator, web_engine)`` a config's ``targets.*`` reference.

    Backends come from the targets only (a target inherits ``defaults.backend`` when it sets none),
    so a config with no targets references no backend and needs no backend install — matching the
    spec's "install only what those targets need". ``web_engine`` is ``None`` off the web.
    """
    for name in config.targets:
        eff = resolve(config, name)
        for actuator in resolve_actuators(eff.backend):
            yield actuator, (web_engine(eff) if actuator == "playwright" else None)


def _ai_configured(config: Config) -> bool:
    if config.defaults.ai is not None:
        return True
    return any(resolve(config, name).ai is not None for name in config.targets)


def _unique(items: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def _unique_tools(tools: list[Tool]) -> tuple[Tool, ...]:
    # Dedupe by probe name. The dict keeps each key's first-seen position; a duplicate exe always
    # maps to an identical Tool, so which duplicate's value the dict keeps is immaterial.
    return tuple({tool.exe: tool for tool in tools}.values())


def _load(config_arg: str | None) -> Config:
    path = Path(config_arg) if config_arg else Path(DEFAULT_CONFIG)
    if not path.exists():
        if config_arg is not None:
            raise SystemExit(f"config not found: {config_arg}")
        # No config in cwd: nothing backend-specific to install (the base toolchain is `make setup`).
        return Config()
    return load_config(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    """Resolve a plan from ``--config`` (or forced ``--backend``) and provision it."""
    parser = argparse.ArgumentParser(
        prog="python -m bajutsu.provision",
        description="Install the extras and external tools a project's configured backends need.",
    )
    parser.add_argument("--config", help="config path (default: ./bajutsu.config.yaml if present)")
    parser.add_argument(
        "--backend",
        action="append",
        help="force a backend regardless of config (repeatable); e.g. --backend ios",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the plan without installing")
    args = parser.parse_args(argv)

    p = plan_for_backends(args.backend) if args.backend else plan(_load(args.config))
    if p.is_empty:
        _echo("provision: nothing to install for this config.")
        return 0

    extras = " ".join(f"--extra {e}" for e in p.extras) or "(none)"
    _echo(f"provision: extras {extras}; tools {[t.exe for t in p.tools]}")
    if args.dry_run:
        return 0

    try:
        report = provision(p)
    except subprocess.CalledProcessError as e:
        # The whole job is running package managers, so surface *which* step failed as one clean
        # line rather than a raw traceback ending in CalledProcessError.
        raise SystemExit(f"provision: `{' '.join(e.cmd)}` failed (exit {e.returncode})") from e
    for cmd in report.ran:
        _echo(f"provision: ran {' '.join(cmd)}")
    for note in report.manual:
        _echo(f"provision: needs manual action — {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
