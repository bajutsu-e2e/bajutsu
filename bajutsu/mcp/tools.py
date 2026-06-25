"""MCP tool definitions: bajutsu_run and bajutsu_doctor."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastmcp import FastMCP

from bajutsu.backends import select_actuator
from bajutsu.config import Effective, load_config, resolve
from bajutsu.doctor import render, score
from bajutsu.drivers.base import Driver


def _load_effective(config_path: Path, target: str) -> Effective:
    """Load config and resolve effective settings. Raises ValueError on failure."""
    if not config_path.exists():
        raise ValueError(f"config not found: {config_path}")
    cfg = load_config(config_path.read_text(encoding="utf-8"))
    try:
        return resolve(cfg, target)
    except KeyError as e:
        raise ValueError(e.args[0] if e.args else str(e)) from None


def _parse_verdict(stdout: str) -> str:
    """Extract the manifest path from `run`'s final stdout line.

    `run` ends its stdout with a single deterministic verdict line — `"PASS  <manifest>"` or
    `"FAIL  <manifest>"`, the token and path separated by two spaces — while progress, usage, and
    warnings go to stderr (any pre-run note also lands on stdout, so the verdict is the *last*
    non-empty line). Stripping only the leading PASS/FAIL token keeps a path that contains spaces
    intact. Returns the manifest path, or "" when stdout has no verdict line.
    """
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return ""
    verdict = lines[-1].strip()
    for token in ("PASS  ", "FAIL  "):
        if verdict.startswith(token):
            return verdict[len(token) :].strip()
    return ""


def make_driver(actuator: str, udid: str) -> Driver:
    """Instantiate a driver for the given actuator and device — thin delegation to the backends registry."""
    from bajutsu.backends import make_driver as _make

    return _make(actuator, udid)


def register_tools(mcp: FastMCP, config_path: Path) -> None:
    """Register ``bajutsu_doctor`` and ``bajutsu_run`` as MCP tools on *mcp*.

    Both tools close over ``config_path`` so callers need only pass ``target``
    (and optional tuning parameters) at invocation time.
    """

    @mcp.tool()
    def bajutsu_doctor(target: str, udid: str = "booted") -> str:
        """Score the current screen's accessibility convention readiness.

        Returns the grade (Ready / Partial / Blocked) and a breakdown of
        id coverage, namespace conformance, and duplicates.
        """
        eff = _load_effective(config_path, target)
        backends = eff.backend
        actuator = select_actuator(backends)
        driver = make_driver(actuator, udid)
        elements = driver.query()
        s = score(elements, eff.id_namespaces)
        return render(s)

    @mcp.tool()
    def bajutsu_run(
        target: str,
        scenario: str = "",
        udid: str = "booted",
        tag: str = "",
        exclude: str = "",
        erase: bool = False,
        workers: int = 1,
    ) -> str:
        """Run E2E scenarios deterministically. Pass/fail is machine-only.

        Returns a summary with the manifest path. The scenario parameter is
        a path to a *.yaml file; if omitted, all scenarios in the target's
        configured directory are run.
        """
        cmd = [
            sys.executable,
            "-m",
            "bajutsu",
            "run",
            "--target",
            target,
            "--config",
            str(config_path),
            "--udid",
            udid,
            "--workers",
            str(workers),
        ]
        if scenario:
            cmd.extend(["--scenario", scenario])
        if tag:
            cmd.extend(["--tag", tag])
        if exclude:
            cmd.extend(["--exclude", exclude])
        if erase:
            cmd.append("--erase")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        # `run` prints a deterministic final stdout line (PASS/FAIL + manifest path); parse that
        # rather than substring-matching its prose, so the MCP layer isn't coupled to the wording.
        manifest_path = _parse_verdict(result.stdout)

        ok = result.returncode == 0
        parts = ["PASS" if ok else "FAIL"]
        if manifest_path:
            parts.append(manifest_path)
        if not ok and result.stderr:
            parts.append(result.stderr.strip())

        return "  ".join(parts[:2]) + ("\n" + parts[2] if len(parts) > 2 else "")
