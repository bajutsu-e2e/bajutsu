"""SimPilot CLI. Commands are implemented incrementally."""

from __future__ import annotations

import typer

app = typer.Typer(add_completion=False, help="自然言語駆動 iOS E2E テストツール（Simulator 限定）")


@app.command()
def run(
    scenario: str,
    app_name: str = typer.Option(..., "--app", help="対象アプリ（config の apps.<name>）"),
    backend: str = typer.Option("rocketsim", help="UI 操作の安定度順。先頭=最安定"),
    udid: str = typer.Option("booted"),
    workers: int = typer.Option(1, help="並列ワーカー数"),
) -> None:
    """Run a scenario deterministically (no AI)."""
    typer.echo("run: 未実装（M1）")
    raise typer.Exit(1)


@app.command()
def record(
    scenario: str,
    app_name: str = typer.Option(..., "--app"),
) -> None:
    """Explore with AI and record actions and evidence rules."""
    typer.echo("record: 未実装（M2）")
    raise typer.Exit(1)


@app.command()
def doctor(
    app_name: str = typer.Option(..., "--app"),
) -> None:
    """Report environment/permission/connection gates and a convention score."""
    typer.echo("doctor: 未実装（M1）")
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
