"""`bajutsu runner build --device` — build the per-user signed XCUITest device runner (BE-0456)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

import typer

runner_app = typer.Typer(help="Build the generic XCUITest runner.", no_args_is_help=True)


@runner_app.command("build")
def build(
    device: Annotated[
        bool,
        typer.Option(
            "--device", help="Build the signed runner for a real iPhone or iPad (required today)"
        ),
    ] = False,
    signing: Annotated[
        Path | None,
        typer.Option(
            "--signing",
            help="Signing file; defaults to $BAJUTSU_SIGNING_FILE, then "
            "~/.config/bajutsu/signing.yaml",
        ),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out", help="Also copy the whole Products directory here (for Device Farm packaging)"
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Rebuild over a matching cached build")
    ] = False,
) -> None:
    """Build the signed device runner once; a run with no testRunner then resolves to it."""
    from bajutsu.common.platform_lifecycle.environments.device_runner import build as device_build
    from bajutsu.common.platform_lifecycle.environments.device_runner import signing as signing_file
    from bajutsu.common.platform_lifecycle.environments.device_runner.errors import (
        DeviceRunnerError,
    )
    from bajutsu.common.platform_lifecycle.environments.device_runner.staging import (
        runner_source_root,
    )

    if out is not None and out.exists() and (not out.is_dir() or any(out.iterdir())):
        # Merging over an older copy would leave files a signed bundle no longer lists in its
        # CodeResources, which the device then rejects; deleting a directory the user named is
        # not this command's call either.
        typer.echo(f"--out {out} must be an empty or absent directory")
        raise typer.Exit(1)
    if not device:
        # The flag leaves room to fold the Simulator build (`make runner-bundle`) in later.
        typer.echo("only --device is supported today: bajutsu runner build --device")
        raise typer.Exit(2)
    try:
        path = signing_file.find_signing_file(signing)
        if path is None:
            typer.echo(
                "no signing file found; write one at "
                f"{signing_file.default_signing_path()} or pass --signing "
                "(see docs/ios-device-cloud.md)"
            )
            raise typer.Exit(1)
        source_root = runner_source_root()
        if source_root is None:
            typer.echo("this install ships no XCUITest runner sources to build from")
            raise typer.Exit(1)
        runner = device_build.build_device_runner(
            signing_file.load_signing(path), source_root=source_root, force=force
        )
        if out is not None:
            # The `.xctestrun` names its test bundles by relative path, so the whole Products
            # directory travels together.
            shutil.copytree(runner.parent, out, dirs_exist_ok=True, symlinks=True)
    except (DeviceRunnerError, OSError) as exc:
        typer.echo(f"error: {exc}")
        raise typer.Exit(1) from exc
    typer.echo(str(runner))
    if out is not None:
        typer.echo(f"copied Products to {out}")


def register(app: typer.Typer) -> None:
    """Register the `runner` command group on the Typer app."""
    app.add_typer(runner_app, name="runner")
