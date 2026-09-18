"""`bajutsu repl` — a manual shell against a running target: read the tree, act on an id (BE-0423)."""

from __future__ import annotations

import atexit
import sys

import typer

from bajutsu.cli._shared import (
    DEFAULT_CONFIG,
    _load_effective,
    _resolve_browser,
    _select_actuator_or_exit,
    _start_launch_server_or_exit,
    _with_headed,
)
from bajutsu.common.config import WEB_ENGINES, Effective
from bajutsu.common.devices import errors as device_errors
from bajutsu.common.drivers import base
from bajutsu.common.platform_lifecycle import Environment, WebEnvironment, environment_for
from bajutsu.common.platform_lifecycle.environments.xcuitest_live import (
    XcuitestLiveEnvironment,
    is_webdriver_endpoint,
)
from bajutsu.common.runner import launch_driver
from bajutsu.common.scenario import Preconditions
from bajutsu.repl.loop import repl_loop
from bajutsu.repl.session import ReplSession
from bajutsu.repl.tui import run as run_tui


def _close_owned_session(env: Environment, driver: base.Driver, eff: Effective) -> None:
    """Close whatever session *this* process owns, leaving a device-backed app running.

    An operator who leaves the shell expects the Simulator or handset still showing the screen they
    were inspecting, so the local `xcuitest` / `adb` teardown — which terminates the app — is
    deliberately not run. The two sessions this process does own would otherwise outlive it: the
    web backend's browser, and the WebDriver session on the `--udid https://…` live route, which
    stays reserved on the grid until it expires. Each is closed through its own environment's
    `teardown`, the very call a run makes, rather than a second spelling of it here.
    """
    if isinstance(env, WebEnvironment | XcuitestLiveEnvironment):
        env.teardown(driver, eff)


def repl(
    target_name: str = typer.Option(..., "--target"),
    udid: str = typer.Option("booted"),
    backend: str = typer.Option(""),
    erase: bool | None = typer.Option(
        None,
        "--erase/--no-erase",
        help="erase the device before launching (app must be installed); default: erase locally, "
        "no-erase on the live `--udid https://…` route, which does not support it",
    ),
    headed: bool | None = typer.Option(
        None,
        "--headed/--no-headed",
        help="web backend: inspect a visible (headed, slow-motion) browser instead of headless; "
        "default leaves the target's `headless` config",
    ),
    browser: str = typer.Option(
        "",
        "--browser",
        help=f"web backend: rendering engine to inspect — {' / '.join(WEB_ENGINES)}; "
        "default leaves the target's `browser` config (chromium)",
    ),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Open a manual shell against the running app: read the element tree, act on an id.

    `tree` prints the current screen's elements with the `id` / `label` / `traits` a selector
    matches against, `tap <id>` acts on one of them, and a second `tree` shows what changed — with
    no AI call and no scenario written. Type `help` for the command set, `exit` to leave.
    """
    eff = _load_effective(config, target_name)
    # A headless browser leaves an operator with no screen to watch change, which matters more for
    # this shell than for `record` — see `_with_headed` / `_resolve_browser` for the precedence.
    eff = _with_headed(eff, headed)
    eff = _resolve_browser(eff, browser)
    actuator, _ = _select_actuator_or_exit(backend, eff, [])
    # Resolve through the selected environment's own device lookup — ios/fake via simctl, adb via
    # its serial resolver, web and the live `--udid https://…` route passing the value straight
    # through — rather than hard-coding simctl here, which would shell out to `simctl`/`xcodebuild`
    # tooling for `--backend adb` and crash off-macOS, or reject the live route's URL outright.
    udid = environment_for(actuator, udid).resolve_device(udid)
    # The live WebDriver route's device is already booted with its build installed (BE-0238); erase
    # is a simctl operation that route explicitly rejects (`XcuitestLiveEnvironment.start`), so an
    # unset `--erase` defaults to off there instead of the local route's on, and an operator who asks
    # for it anyway gets a clean CLI error instead of an `UnsupportedAction` traceback.
    is_live_route = is_webdriver_endpoint(udid)
    if erase is None:
        erase = not is_live_route
    elif erase and is_live_route:
        typer.echo("repl: --erase is not supported on the live `--udid https://…` route")
        raise typer.Exit(2)
    # Bring the app's target server up if the config declares launchServer — without it a web
    # target opens the browser on a host that is not listening, and every `tree` reads the error
    # page. Stopped when this command exits (atexit), the way `record` and `crawl` stop theirs.
    stop_server, _exec_decision = _start_launch_server_or_exit(eff)
    atexit.register(stop_server)

    # Unlike `record` / `crawl`, which keep stdout for their one final result line and narrate
    # progress to stderr, this shell's output *is* the product: every command's answer goes to
    # stdout, same as the prompt it answers.
    def say(msg: str) -> None:
        typer.echo(msg)

    say(f"⚙️  preparing {target_name} on {actuator} (this can take a moment) …")
    # One environment drives both the launch and the exit, as `crawl`'s lane builder does: a second
    # one built from the raw udid would close whichever device that udid still names, which stops
    # being the device that came up as soon as the launch had to replace a vanished Simulator.
    env = environment_for(actuator, udid)
    try:
        driver, _readiness = launch_driver(
            udid, eff, actuator, Preconditions(erase=erase), environment=env
        )
    except device_errors.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    say(f"✅ {target_name} is up on {actuator} — type `help` for the command set, `exit` to leave")
    try:
        # The ncurses-style TUI needs a real terminal on both ends; piped input/output (a script, a
        # test harness) falls back to the plain line-at-a-time loop, the way most terminal tools do.
        if sys.stdin.isatty() and sys.stdout.isatty():
            run_tui(ReplSession(driver))
        else:
            repl_loop(ReplSession(driver), input, say)
    finally:
        # A teardown hiccup here must never replace a real bug propagating out of `repl_loop` —
        # `dispatch`'s own contract is that a genuine defect is *not* swallowed (COMMAND_ERRORS),
        # and a `finally` block that raises would erase exactly that exception for whatever catches
        # it above this function.
        try:
            _close_owned_session(env, driver, eff)
        except Exception as e:
            say(f"warning: closing the session failed: {e}")


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(repl)
