"""SimPilot CLI（DESIGN.md §8）。各コマンドは段階的に実装する（M1〜）。"""

from __future__ import annotations

import typer

app = typer.Typer(add_completion=False, help="自然言語駆動 iOS E2E テストツール（Simulator 限定）")


@app.command()
def run(
    scenario: str,
    app_name: str = typer.Option(..., "--app", help="対象アプリ（config の apps.<name>）"),
    backend: str = typer.Option("rocketsim", help="UI 操作の安定度順。先頭=最安定（§5）"),
    udid: str = typer.Option("booted"),
    workers: int = typer.Option(1, help="並列ワーカー数（§3.3）"),
) -> None:
    """シナリオを決定的に実行する（AI 非依存。§3.1）。"""
    typer.echo("run: 未実装（M1）")
    raise typer.Exit(1)


@app.command()
def record(
    scenario: str,
    app_name: str = typer.Option(..., "--app"),
) -> None:
    """AI で探索しつつ操作・証跡指示を記録する（§3.1 / §6.5）。"""
    typer.echo("record: 未実装（M2）")
    raise typer.Exit(1)


@app.command()
def doctor(
    app_name: str = typer.Option(..., "--app"),
) -> None:
    """環境/権限/接続ゲート + §7 充足度スコアを出す（§7.2）。"""
    typer.echo("doctor: 未実装（M1）")
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
