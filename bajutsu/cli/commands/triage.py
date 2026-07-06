"""`bajutsu triage` — diagnose a failed run and suggest a minimal fix (advisory only)."""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import typer

from bajutsu import trace as _trace
from bajutsu import triage as _triage
from bajutsu import usage as _usage
from bajutsu.cli._shared import (
    DEFAULT_CONFIG,
    _ai_redactor,
    _load_effective,
    _require_ai_credential,
    _warn_onscreen_secrets,
)
from bajutsu.config import Effective, IosConfig


def triage(
    run_dir: str = typer.Argument("", help="run directory (default: the latest under runs/)"),
    scenario: str = typer.Option(
        "", "--scenario", help="only the scenario whose name contains this"
    ),
    runs: str = typer.Option("runs", help="runs root (used when run_dir is omitted)"),
    ai: bool = typer.Option(
        False,
        "--ai",
        help="diagnose with Claude (needs ANTHROPIC_API_KEY) instead of the rule-based agent",
    ),
    apply: str = typer.Option(
        "",
        "--apply",
        help="scenario source file to patch with the suggested fix (shows a dry-run diff)",
    ),
    write: bool = typer.Option(
        False,
        "--write",
        help="with --apply, write the patched file instead of only showing the diff",
    ),
    rerun: bool = typer.Option(
        False,
        "--rerun",
        help="after --write, re-run the patched scenario to verify the fix (needs --target + a device)",
    ),
    json_out: str = typer.Option(
        "",
        "--json",
        help="also write the diagnosis (and, with --apply, the fix diff + patched text) as JSON here",
    ),
    target_name: str = typer.Option("", "--target", help="target key, for --rerun"),
    backend: str = typer.Option("", "--backend", help="actuator backend, for --rerun"),
    udid: str = typer.Option("booted", "--udid", help="simulator udid, for --rerun"),
    config: str = typer.Option(DEFAULT_CONFIG, "--config", help="config path, for --rerun"),
) -> None:
    """Diagnose a failed run and suggest a minimal fix (advisory — never the pass/fail judge)."""
    path = Path(run_dir) if run_dir else _trace.latest_run(Path(runs))
    if path is None or not (path / "manifest.json").exists():
        typer.echo(f"no run found{f': {run_dir}' if run_dir else f' under {runs}/'}")
        raise typer.Exit(2)
    context = _triage.assemble(path, scenario or None)
    if context is None:
        typer.echo("no failed scenario to triage in this run")
        raise typer.Exit(0)
    # When patching a source file, diagnose against *its* text (not the run's normalized dump)
    # so a fix's `find` fragment matches the file that --apply edits — otherwise float/flow-style
    # normalization (`timeout: 2.0`, block selectors) makes fragment fixes miss.
    if apply:
        with contextlib.suppress(OSError):
            # _apply_fix reports the read failure below
            context = replace(context, scenario_yaml=Path(apply).read_text(encoding="utf-8"))
    agent: _triage.TriageAgent
    if ai:
        from bajutsu.claude_triage import ClaudeTriageAgent

        # Resolve the target's `ai` config + redactor when a target is named, so triage uses the
        # configured provider/endpoint and masks the element tree / failure text it sends (BE-0047).
        # With no target it falls back to env-only provider config and a no-op redactor.
        eff = _ai_effective(config, target_name)
        _require_ai_credential(eff)
        # The failure screenshot, when one is attached, is sent to the AI as-is; on-screen secrets
        # are not redacted (BE-0151).
        _warn_onscreen_secrets(eff)
        agent = ClaudeTriageAgent(ai=eff.ai, redactor=_ai_redactor(eff))
    else:
        agent = _triage.HeuristicTriageAgent()
    before = _usage.snapshot()
    result = agent.triage(context)
    typer.echo(_triage.render(context, result))
    # Only `--ai` triage calls the model; the heuristic agent's delta is empty, so this is silent.
    spent = _usage.snapshot() - before
    if spent.calls:
        typer.echo(spent.render(), err=True)
    if json_out:
        # `context.scenario_yaml` is the --apply file's own text (reloaded above), so the fix's
        # `find` fragment matches what a UI writes back — a dry-run preview, never a write here.
        applied = (
            _triage.apply_result(context.scenario_yaml, apply, result.fix)
            if apply and result.fix is not None
            else None
        )
        Path(json_out).write_text(
            json.dumps(
                _triage.result_payload(context, result, applied), ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
    if not apply:
        return
    wrote = _apply_fix(result, apply, write)
    if rerun:
        if not wrote:
            typer.echo("\n--rerun needs --write (nothing was applied to re-run).")
        elif not target_name:
            typer.echo("\n--rerun needs --target to run the patched scenario.")
        else:
            _verify_rerun(apply, target_name, backend, udid, config)


def _ai_effective(config: str, target_name: str) -> Effective:
    """The effective config for `--ai` triage: the named target's, or an env-only default (BE-0047).

    Triage runs against a saved run dir and may have no `--target` / config — then provider config
    comes from the environment alone and there is nothing to redact. When a target is named it
    carries the target's `ai` block, `redact` keys, and secret names.
    """
    if target_name:
        return _load_effective(config, target_name)
    from bajutsu.scenario import Redact

    return Effective(
        target="",
        platform_config=IosConfig(),
        backend=["idb"],
        device="",
        locale="",
        launch_env={},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        setup=None,
        capture=[],
        redact=Redact(),
    )


def _apply_fix(result: _triage.Triage, target: str, write: bool) -> bool:
    """Render (and optionally write) the suggested fix. Returns True only when a file was written."""
    if result.fix is None:
        typer.echo("\nno applicable structured fix for this failure (advisory only).")
        return False
    try:
        src = Path(target).read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"\ncannot read {target}: {exc}")
        raise typer.Exit(2) from None
    patched, count = _triage.apply_fix(src, result.fix)
    if count == 0:
        typer.echo(
            f"\nfix: {result.fix.summary} — `{result.fix.find}` not found in {target} (no-op)"
        )
        return False
    typer.echo(
        f"\nfix: {result.fix.summary} ({count} occurrence{'' if count == 1 else 's'} in {target})"
    )
    typer.echo(_triage.diff_fix(src, patched, target))
    if not write:
        typer.echo("dry-run — re-run with --write to apply.")
        return False
    Path(target).write_text(patched, encoding="utf-8")
    typer.echo(f"wrote {target}")
    return True


def _rerun_command(
    target: str, target_name: str, backend: str, udid: str, config: str
) -> list[str]:
    """The `bajutsu run` invocation that re-checks a patched scenario.

    Kept `--no-erase` to reuse the current device state. Built as a list so it is easy to assert in
    tests.
    """
    cmd = [
        sys.executable,
        "-m",
        "bajutsu",
        "run",
        "--scenario",
        target,
        "--target",
        target_name,
        "--config",
        config,
        "--no-erase",
    ]
    if backend:
        cmd += ["--backend", backend]
    if udid:
        cmd += ["--udid", udid]
    return cmd


def _verify_rerun(target: str, target_name: str, backend: str, udid: str, config: str) -> None:
    """Run the patched scenario and report whether the fix holds."""
    cmd = _rerun_command(target, target_name, backend, udid, config)
    typer.echo(f"\nre-running {target} to verify the fix ...")
    code = subprocess.call(cmd)
    typer.echo(
        "fix verified — the scenario now passes."
        if code == 0
        else "the scenario still fails after the fix; further diagnosis needed."
    )


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(triage)
