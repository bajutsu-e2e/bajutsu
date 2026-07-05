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
from typing import cast

import typer

from bajutsu import coverage as _coverage
from bajutsu import crawl as _crawl
from bajutsu.cli._shared import DEFAULT_CONFIG, _load_effective
from bajutsu.drivers import base
from bajutsu.scenario import load_scenarios_dir


def _visited_screens(runs_dir: Path) -> frozenset[str]:
    """The screen fingerprints the run set rendered.

    Each per-step `elements.json` is one rendered screen; fingerprint it with the same
    `crawl.fingerprint` the crawl uses, so a visited screen matches a discovered one. Read-only; a
    malformed/partial file is skipped, not fatal.
    """
    seen: set[str] = set()
    for els in sorted(runs_dir.glob("*/*/elements.json")):
        try:
            data = json.loads(els.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                continue
        except (OSError, ValueError):
            continue
        elements = [e for e in data if isinstance(e, dict)]
        if elements:
            seen.add(_crawl.fingerprint(cast(list[base.Element], elements)).value)
    return frozenset(seen)


def _discovered_screens(screenmap_path: Path) -> list[_coverage.ScreenRef] | None:
    """The screens a crawl discovered, from its `screenmap.json` nodes.

    Each node's label is its first stable id, or the short fingerprint when the screen carries none.
    Returns None when the file can't be read as JSON (so the caller skips the dimension with a
    warning, like the other evidence readers); a node that isn't a dict or carries no `fingerprint`
    is skipped, and an unexpected top-level shape yields no nodes — read-only, never fatal.
    """
    try:
        data = json.loads(screenmap_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    nodes = data.get("nodes") if isinstance(data, dict) else None
    refs: list[_coverage.ScreenRef] = []
    for n in nodes or []:
        if not isinstance(n, dict) or not n.get("fingerprint"):
            continue
        fp = str(n["fingerprint"])
        ids = n.get("ids") or []
        refs.append(_coverage.ScreenRef(fingerprint=fp, label=ids[0] if ids else fp[:7]))
    return refs


def coverage(
    target_name: str = typer.Option(..., "--target"),
    config: str = typer.Option(DEFAULT_CONFIG),
    runs: str = typer.Option(
        "",
        "--runs",
        help="a runs dir — adds endpoint coverage (network.json vs asserted) "
        "and observed-id coverage (elements.json vs declared namespaces)",
    ),
    crawl: str = typer.Option(
        "",
        "--crawl",
        help="a crawl's screenmap.json (or its run dir) — with --runs, adds screens-visited "
        "coverage (how many discovered screens the run set reached)",
    ),
    as_json: bool = typer.Option(False, "--json", help="emit the report as JSON instead of text"),
    html: str = typer.Option(
        "", "--html", help="also write a self-contained HTML coverage report to this path"
    ),
) -> None:
    """Statically map which of the target's declared id namespaces its scenario suite touches.

    Per-namespace coverage, the gap list (untested namespaces), and off-namespace ids. With
    `--runs`, also fold in two run-evidence dimensions: endpoint coverage (which observed endpoints
    in `network.json` the suite's network assertions cover) and observed-id coverage (which declared
    namespaces the runs actually rendered ids under, from each `elements.json`). With `--crawl` (and
    `--runs`), add screens-visited coverage: how many of a crawl's discovered screens the run set
    reached. With `--html`, also write a self-contained HTML report of the same figures. Read-only
    and advisory: it never runs a scenario and never gates CI — it exits 0 even with gaps; only a
    missing config / scenarios dir or an unreadable scenario exits 2.
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
        scenarios = load_scenarios_dir(scenarios_dir)
    except (OSError, ValueError) as e:
        typer.echo(f"failed to load scenarios: {e}")
        raise typer.Exit(2) from None

    report = _coverage.coverage(scenarios, eff.id_namespaces)
    endpoints = None
    observed_ids = None
    screens = None
    runs_path = Path(runs) if runs else None
    if runs_path is not None and runs_path.is_dir():
        endpoints = _coverage.endpoint_coverage(scenarios, _coverage.read_exchanges(runs_path))
        observed_ids = _coverage.observed_id_coverage(
            _coverage.read_observed_ids(runs_path), eff.id_namespaces
        )
    elif runs:  # don't silently ignore the flag — warn (to stderr) and proceed without run evidence
        typer.echo(f"--runs not found, skipping run-evidence coverage: {runs}", err=True)
    if crawl:
        # The denominator (discovered screens) comes from --crawl; the numerator (screens visited)
        # comes from --runs evidence, so the dimension needs both. A run dir is also accepted.
        crawl_path = Path(crawl)
        screenmap = crawl_path / "screenmap.json" if crawl_path.is_dir() else crawl_path
        if not screenmap.is_file():
            typer.echo(f"--crawl screenmap not found, skipping screens coverage: {crawl}", err=True)
        elif runs_path is None or not runs_path.is_dir():
            typer.echo(
                "--crawl needs --runs to know which screens were visited; skipping", err=True
            )
        else:
            discovered = _discovered_screens(screenmap)
            if discovered is None:  # unreadable/invalid map — skip the dimension, don't crash
                typer.echo(
                    f"--crawl screenmap unreadable, skipping screens coverage: {crawl}", err=True
                )
            else:
                screens = _coverage.screen_coverage(discovered, _visited_screens(runs_path))
    if html:
        # Write the report first; the stdout below (text or JSON) stays the same with or without it,
        # so a confirmation goes to stderr rather than polluting a piped `--json` payload. Create the
        # parents of a nested path, and fail cleanly (exit 2, like the errors above) on an unwritable
        # location rather than crashing with a traceback.
        html_path = Path(html)
        try:
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text(
                _coverage.render_html(
                    report,
                    endpoints=endpoints,
                    observed=observed_ids,
                    screens=screens,
                    target=target_name,
                ),
                encoding="utf-8",
            )
        except OSError as e:
            typer.echo(f"failed to write HTML report to {html_path}: {e}", err=True)
            raise typer.Exit(2) from None
        typer.echo(f"wrote HTML coverage report: {html_path}", err=True)
    if as_json:
        out: dict[str, object] = dataclasses.asdict(report)
        if endpoints is not None:
            out["endpoints"] = dataclasses.asdict(endpoints)
        if observed_ids is not None:
            out["observed_ids"] = dataclasses.asdict(observed_ids)
        if screens is not None:
            out["screens"] = dataclasses.asdict(screens)
        typer.echo(json.dumps(out, indent=2))
    else:
        text = _coverage.render(report)
        if endpoints is not None:
            text += "\n" + _coverage.render_endpoints(endpoints)
        if observed_ids is not None:
            text += "\n" + _coverage.render_observed_ids(observed_ids)
        if screens is not None:
            text += "\n" + _coverage.render_screens(screens)
        typer.echo(text)


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(coverage)
