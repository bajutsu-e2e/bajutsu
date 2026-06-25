"""`bajutsu coverage` — statically map a suite's id-namespace coverage (no device, no AI, BE-0050).

Read-only and advisory: it walks every scenario in the target's configured `scenarios` dir, groups
the stable ids they reference by namespace, and measures them against the target's declared
`idNamespaces` — reporting per-namespace coverage, the gap list, and off-namespace ids. It never
runs a scenario and never gates CI: a successful coverage report exits 0 *even with gaps* (only a
missing config / scenarios dir or an unreadable scenario exits 2), so it strengthens
target-readiness insight without ever deciding a verdict.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import typer

from bajutsu import coverage as _coverage
from bajutsu.cli._shared import DEFAULT_CONFIG, _load_effective, load_expanded_scenarios
from bajutsu.network import NetworkExchange


def _observed_exchanges(runs_dir: Path) -> list[NetworkExchange]:
    """Every exchange recorded across the run set.

    The union of every `network.json` under `runs_dir` (read-only; a malformed/partial file is
    skipped, not fatal).
    """
    exchanges: list[NetworkExchange] = []
    for net in sorted(runs_dir.glob("*/*/network.json")):
        try:
            data = json.loads(net.read_text(encoding="utf-8"))
            if not isinstance(data, list):  # a scalar/object file isn't an exchange list — skip it
                continue
            # Validate the whole file into a batch before extending, so a bad entry (pydantic
            # ValidationError, a ValueError) skips the file wholesale rather than leaving a partial.
            batch = [NetworkExchange.model_validate(e) for e in data if isinstance(e, dict)]
        except (OSError, ValueError):
            continue
        exchanges.extend(batch)
    return exchanges


def _observed_ids(runs_dir: Path) -> list[str]:
    """Every stable id rendered across the run set.

    The union of each element's `identifier` from every per-step `elements.json` under `runs_dir`
    (read-only; a malformed/partial file is skipped, not fatal). Null and empty identifiers are
    dropped — only elements that carry a stable id contribute.
    """
    ids: list[str] = []
    for els in sorted(runs_dir.glob("*/*/elements.json")):
        try:
            data = json.loads(els.read_text(encoding="utf-8"))
            if not isinstance(data, list):  # a scalar/object file isn't an element list — skip it
                continue
        except (OSError, ValueError):  # unreadable or invalid JSON — skip, like _observed_exchanges
            continue
        ids.extend(
            e["identifier"]
            for e in data
            if isinstance(e, dict) and isinstance(e.get("identifier"), str) and e["identifier"]
        )
    return ids


def coverage(
    target_name: str = typer.Option(..., "--target"),
    config: str = typer.Option(DEFAULT_CONFIG),
    runs: str = typer.Option(
        "",
        "--runs",
        help="a runs dir — adds endpoint coverage (network.json vs asserted) "
        "and observed-id coverage (elements.json vs declared namespaces)",
    ),
    as_json: bool = typer.Option(False, "--json", help="emit the report as JSON instead of text"),
) -> None:
    """Statically map which of the target's declared id namespaces its scenario suite touches.

    Per-namespace coverage, the gap list (untested namespaces), and off-namespace ids. With
    `--runs`, also fold in two run-evidence dimensions: endpoint coverage (which observed endpoints
    in `network.json` the suite's network assertions cover) and observed-id coverage (which declared
    namespaces the runs actually rendered ids under, from each `elements.json`). Read-only and
    advisory: it never runs a scenario and never gates CI — it exits 0 even with gaps; only a missing
    config / scenarios dir or an unreadable scenario exits 2.
    """
    eff = _load_effective(config, target_name)
    if eff.scenarios is None:
        typer.echo(
            f"target '{target_name}' has no scenarios dir (set targets.{target_name}.scenarios)"
        )
        raise typer.Exit(2)
    scenarios_dir = Path(eff.scenarios)
    if not scenarios_dir.is_dir():
        typer.echo(f"scenarios dir not found: {eff.scenarios}")
        raise typer.Exit(2)
    try:
        scenarios = [
            s for f in sorted(scenarios_dir.glob("*.yaml")) for s in load_expanded_scenarios(f)
        ]
    except (OSError, ValueError) as e:
        typer.echo(f"failed to load scenarios: {e}")
        raise typer.Exit(2) from None

    report = _coverage.coverage(scenarios, eff.id_namespaces)
    endpoints = None
    observed_ids = None
    if runs:
        runs_path = Path(runs)
        if runs_path.is_dir():
            endpoints = _coverage.endpoint_coverage(scenarios, _observed_exchanges(runs_path))
            observed_ids = _coverage.observed_id_coverage(
                _observed_ids(runs_path), eff.id_namespaces
            )
        else:  # don't silently ignore the flag — warn (to stderr) and proceed without run evidence
            typer.echo(f"--runs not found, skipping run-evidence coverage: {runs}", err=True)
    if as_json:
        out: dict[str, object] = dataclasses.asdict(report)
        if endpoints is not None:
            out["endpoints"] = dataclasses.asdict(endpoints)
        if observed_ids is not None:
            out["observed_ids"] = dataclasses.asdict(observed_ids)
        typer.echo(json.dumps(out, indent=2))
    else:
        text = _coverage.render(report)
        if endpoints is not None:
            text += "\n" + _coverage.render_endpoints(endpoints)
        if observed_ids is not None:
            text += "\n" + _coverage.render_observed_ids(observed_ids)
        typer.echo(text)


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(coverage)
