"""BE-0225 unit 5: the `bajutsu project` subcommands (add / ls / rm / use).

A headless view of the same project hub `serve` exposes over HTTP (BE-0225 unit 3): the
`LocalProjectRegistry` JSON store beside the runs directory, or the DB `Repository` when
`BAJUTSU_DATABASE_URL` is set. Every command resolves to the single `default` org locally, and
edits the same on-disk store a running `serve` would — so a project registered from the CLI is
visible in the web hub and vice versa.
"""

from __future__ import annotations

import typer

from bajutsu.cli._projects import open_registry, source_from_config

project_app = typer.Typer(
    add_completion=False,
    help="Manage the config project hub: register, list, switch, and remove projects.",
)

# The store is a sibling of the runs directory (serve wires `LocalProjectRegistry(runs_dir.parent /
# "projects.json")`), so the CLI takes the same `--runs` root to point at the same file.
_RUNS = typer.Option("runs", "--runs", help="runs root whose sibling holds the project store")


@project_app.command("add")
def add(
    name: str = typer.Argument(..., help="project name (unique within the org)"),
    config: str = typer.Option(..., "--config", help="config source: a local path or a Git spec"),
    runs: str = _RUNS,
) -> None:
    """Register a project bound to a config source, or rebind an existing one by name.

    The first project registered becomes the active binding, matching the API's auto-activation.
    """
    from bajutsu.serve.orgs import DEFAULT_ORG

    # The web hub addresses a project by splicing its name into REST paths (`/api/projects/{name}/…`),
    # so a '/' would make it unreachable there — reject it here as `register_project` does, keeping the
    # CLI-hub round-trip intact.
    if "/" in name:
        raise typer.BadParameter("name must not contain '/'", param_hint="name")
    registry = open_registry(runs)
    had_active = registry.resolve_active(org_id=DEFAULT_ORG) is not None
    registry.add(org_id=DEFAULT_ORG, name=name, source=source_from_config(config))
    if not had_active:
        registry.set_active(org_id=DEFAULT_ORG, name=name)
    typer.echo(f"registered project {name!r}")


@project_app.command("ls")
def ls(runs: str = _RUNS) -> None:
    """List registered projects; the active one is marked with a leading '*'."""
    from bajutsu.serve.orgs import DEFAULT_ORG

    registry = open_registry(runs)
    active = registry.resolve_active(org_id=DEFAULT_ORG)
    active_id = active.id if active is not None else None
    projects = registry.list_projects(org_id=DEFAULT_ORG)
    if not projects:
        typer.echo("no projects registered")
        return
    for p in projects:
        marker = "*" if p.id == active_id else " "
        source = p.source if isinstance(p.source, dict) else {}
        kind = source.get("kind", "?")
        typer.echo(f"{marker} {p.name}  ({kind})")


@project_app.command("rm")
def rm(
    name: str = typer.Argument(..., help="project name to deregister"),
    runs: str = _RUNS,
) -> None:
    """Deregister a project. Its runs are retained on disk; only the binding is removed."""
    from bajutsu.serve.orgs import DEFAULT_ORG

    registry = open_registry(runs)
    if registry.get(org_id=DEFAULT_ORG, name=name) is None:
        typer.echo(f"no project named {name!r}", err=True)
        raise typer.Exit(1)
    registry.remove(org_id=DEFAULT_ORG, name=name)
    typer.echo(f"removed project {name!r}")


@project_app.command("use")
def use(
    name: str = typer.Argument(..., help="project name to make active"),
    runs: str = _RUNS,
) -> None:
    """Make a project the active binding — the one new runs are stamped to."""
    from bajutsu.serve.orgs import DEFAULT_ORG

    registry = open_registry(runs)
    if registry.get(org_id=DEFAULT_ORG, name=name) is None:
        typer.echo(f"no project named {name!r}", err=True)
        raise typer.Exit(1)
    registry.set_active(org_id=DEFAULT_ORG, name=name)
    typer.echo(f"active project is now {name!r}")


def register(app: typer.Typer) -> None:
    """Register the `project` subcommand group on the Typer app."""
    app.add_typer(project_app, name="project")
