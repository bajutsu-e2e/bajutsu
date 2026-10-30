"""MCP tool definitions: bajutsu_run and bajutsu_doctor."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from fastmcp import FastMCP

from bajutsu.common.backends import select_actuator
from bajutsu.common.capability.worker_capability import WorkerCapability, load_worker_capability
from bajutsu.common.config import Effective, load_config_file, resolve
from bajutsu.common.devices import errors as device_errors
from bajutsu.common.doctor import DoctorProbeError, probe_screen, render, score
from bajutsu.common.drivers import base
from bajutsu.run import INTERNAL_MODULE as INTERNAL_RUN_MODULE
from bajutsu.run.device_matrix import first_run_device


def _load_effective(config_path: Path, target: str) -> Effective:
    """Load config and resolve effective settings. Raises ValueError on failure."""
    if not config_path.exists():
        raise ValueError(f"config not found: {config_path}")
    cfg = load_config_file(config_path)
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


def _probe_device(eff: Effective, udid: str, worker: WorkerCapability) -> tuple[Effective, str]:
    """*eff* with the worker's device provider, and the device of the target's first run (BE-0452).

    The probe scores the screen of the first run the target's `runsOn` conditions form, as `doctor`
    does; a target with no condition keeps the udid spec the provider hands back.

    Raises:
        ValueError: *udid* is a URL, the provider cannot resolve a device, or no available device
            fits the first run.
    """
    if any(t.strip().startswith(("http://", "https://")) for t in udid.split(",")):
        raise ValueError(
            "udid no longer takes a URL: name the grid in an `appium` environment of the "
            "server's --worker-config (BE-0452)"
        )
    # Imported here: the provider registry reaches the platform environments.
    from bajutsu.common.runner.device_provider import acquire_device

    provider = worker.device_provider_for(eff.backend[0] if eff.backend else eff.platform)
    if provider is not None:
        eff = replace(eff, device_provider=provider)
    spec = acquire_device(eff, udid).udid_spec
    return first_run_device(eff, select_actuator(eff.backend), spec)


def register_tools(
    mcp: FastMCP,
    config_path: Path,
    *,
    worker_config: Path | None = None,
    worker: WorkerCapability | None = None,
) -> None:
    """Register ``bajutsu_doctor`` and ``bajutsu_run`` as MCP tools on *mcp*.

    Both tools close over ``config_path`` so callers need only pass ``target``
    (and optional tuning parameters) at invocation time. They close over the server's
    `--worker-config` too: `bajutsu_run` passes it on, and `bajutsu_doctor` probes the grid an
    `appium` environment names (BE-0452).
    """
    in_force = worker if worker is not None else load_worker_capability(worker_config)

    @mcp.tool()
    def bajutsu_doctor(target: str, udid: str = "booted") -> str:
        """Score the current screen's accessibility convention readiness.

        Shares `doctor`'s screen probe (BE-0199): on iOS this brings up a short-lived XCUITest
        runner, reads the tree, and tears the runner back down (BE-0290) — no runner needs to be
        resident beforehand.

        Returns the grade (Ready / Partial / Blocked) and a breakdown of
        id coverage, namespace conformance, and duplicates.
        """
        eff = _load_effective(config_path, target)
        try:
            eff, udid_spec = _probe_device(eff, udid, in_force)
        except ValueError as e:
            return str(e)
        backends = eff.backend
        actuator = select_actuator(backends)
        try:
            elements = probe_screen(actuator, udid_spec, eff)
        except DoctorProbeError as e:
            return str(e)
        except (device_errors.DeviceError, base.UnsupportedAction) as e:
            return f"could not read the screen to score: {e}"
        s = score(
            elements,
            eff.id_namespaces,
            ok_coverage=eff.doctor_thresholds.ok_coverage,
            fail_coverage=eff.doctor_thresholds.fail_coverage,
        )
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
        `paths.scenarios` directory are run. `erase=True` erases the device
        before every scenario, overriding each scenario's
        `preconditions.subject.reinstall` and the target's; False leaves
        those in force.
        """
        # The internal run module, not the public wrapper: the tool's timeout kills what it spawned,
        # and the run must die with it rather than outlive it in a session of its own (BE-0449).
        cmd = [
            sys.executable,
            "-m",
            INTERNAL_RUN_MODULE,
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
        if worker_config is not None:
            cmd.extend(["--worker-config", str(worker_config)])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)

        # `run` prints a deterministic final stdout line (PASS/FAIL + manifest path); parse that
        # rather than substring-matching its prose, so the MCP layer isn't coupled to the wording.
        manifest_path = _parse_verdict(result.stdout)

        # First line is the verdict, with the manifest path when one was parsed — never stderr, so
        # the `PASS|FAIL  <manifest>` shape can't be faked when the manifest is unavailable (e.g.
        # `run` crashed before its verdict line). stderr, if any, follows on its own line.
        verdict = "PASS" if result.returncode == 0 else "FAIL"
        out = f"{verdict}  {manifest_path}" if manifest_path else verdict
        if result.returncode != 0 and result.stderr.strip():
            out += "\n" + result.stderr.strip()
        return out
