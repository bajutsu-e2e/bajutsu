"""`bajutsu record` — explore the app with AI toward a goal and author a scenario."""

from __future__ import annotations

import atexit
import os
from pathlib import Path

import typer

from bajutsu import device_errors
from bajutsu import simctl as _simctl
from bajutsu.agents.claude import MODEL as _RECORD_MODEL
from bajutsu.agents.factory import make_agent
from bajutsu.ai import announce_ai
from bajutsu.analytics import usage as _usage
from bajutsu.cli._shared import (
    DEFAULT_CONFIG,
    _ai_redactor,
    _build_alert_guard,
    _install_usage_ledger,
    _load_effective_with_source,
    _refuse_out_in_checkout,
    _require_ai_credential,
    _resolve_browser,
    _resolve_language,
    _select_actuator_or_exit,
    _start_launch_server_or_exit,
    _warn_onscreen_secrets,
    _with_headed,
)
from bajutsu.cli.handoff import make_handoff
from bajutsu.config import WEB_ENGINES, Effective
from bajutsu.handoff import HumanHandoffUnavailable
from bajutsu.platform_lifecycle import environment_for
from bajutsu.record import record as record_loop
from bajutsu.runner import launch_driver
from bajutsu.scenario import Preconditions, dump_scenarios


def _secret_tokens(eff: Effective) -> list[tuple[str, str]]:
    """`(value, "${secrets.NAME}")` pairs for each declared secret with a non-empty env value.

    The counterpart to `run`'s forward secret resolution (`_resolve_secrets`): `record` uses these
    to rewrite a recorded literal back to its token (BE-0120). An env var that is unset *or empty*
    is skipped — an empty value has no literal to tokenize, and matching one would splice the token
    between every character. (`run` keeps the empty binding but its redactor drops empty values the
    same way, so the effect is identical.) Longest value first so a value that is a substring of
    another is substituted before it, never leaving a partial literal in the written scenario.
    """
    pairs = [
        (os.environ[name], f"${{secrets.{name}}}") for name in eff.secrets if os.environ.get(name)
    ]
    return sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)


def _record_out_path(
    eff: Effective,
    out: str,
    name: str,
    goal: str,
    target_name: str,
    *,
    checkout_root: Path | None,
) -> Path:
    """Where `record` writes the authored scenario.

    `--out` when given, else an auto-named, never-overwriting `*.yaml` under the target's configured
    `scenarios` dir (same naming as the web UI's Record tab).

    A Git source is **read-only** (BE-0063): an explicit `--out` may not land inside the checkout, and
    with no `--out` the file auto-names under the **current directory** (the configured `scenarios`
    dir is inside the SHA-keyed cache), so an authored scenario is always a reviewable local file.
    """
    from bajutsu.serve import scenario_out_path, unique_scenario_path

    if out:
        target = Path(out)
    else:
        # The auto-name base: the current directory for a Git source (its configured `scenarios` dir
        # is inside the read-only cache), else the local config's configured dir.
        base = (
            Path()
            if checkout_root is not None
            else (Path(eff.evidence_dirs.scenarios) if eff.evidence_dirs.scenarios else None)
        )
        if base is None:
            typer.echo(
                f"target '{target_name}' has no scenarios dir "
                f"(set targets.{target_name}.scenarios, or pass --out)"
            )
            raise typer.Exit(2)
        target = unique_scenario_path(scenario_out_path(base, name or goal))
    # Guard whatever we resolved — including the auto-named cwd path, in case `record` runs from
    # inside the checkout itself — so a generated scenario never lands in the read-only cache.
    _refuse_out_in_checkout(target, checkout_root)
    return target


def record(
    target_name: str = typer.Option(..., "--target"),
    goal: str = typer.Option(..., "--goal", help="natural-language goal to author"),
    out: str = typer.Option(
        "", "--out", help="explicit output path (overrides the target's configured scenarios dir)"
    ),
    name: str = typer.Option(
        "", "--name", help="file name for the authored scenario (auto-named from the goal if blank)"
    ),
    udid: str = typer.Option("booted"),
    backend: str = typer.Option(""),
    erase: bool = typer.Option(
        True, "--erase/--no-erase", help="erase the device before launching (app must be installed)"
    ),
    dismiss_alerts: bool = typer.Option(
        True,
        "--dismiss-alerts/--no-dismiss-alerts",
        help="dismiss unexpected OS prompts while authoring (on by default; uses the same API key)",
    ),
    max_steps: int = typer.Option(
        30,
        "--max-steps",
        help="cap the number of authoring turns (and therefore worst-case token spend); "
        "default 30 (BE-0194)",
    ),
    screenshot: bool = typer.Option(
        True,
        "--screenshot/--no-screenshot",
        help="send a screenshot each turn; --no-screenshot records elements-only (cheaper) when the "
        "app is fully instrumented (BE-0194)",
    ),
    alert_instruction: str = typer.Option(
        "", "--alert-instruction", help="how to handle a prompt instead of dismissing it"
    ),
    headed: bool | None = typer.Option(
        None,
        "--headed/--no-headed",
        help="web backend: author against a visible (headed, slow-motion) browser instead of "
        "headless; default leaves the target's `headless` config",
    ),
    browser: str = typer.Option(
        "",
        "--browser",
        help=f"web backend: rendering engine to author against — {' / '.join(WEB_ENGINES)}; "
        "default leaves the target's `browser` config (chromium)",
    ),
    language: str = typer.Option(
        "",
        "--language",
        help="AI output language for the authored prose (from: provenance, reasoning) — "
        "ja / en / auto; overrides `ai.language`, default leaves the config (auto follows the goal)",
    ),
    upload_exec: str = typer.Option(
        "",
        "--upload-exec",
        hidden=True,
        help="internal: serve sets this for an uploaded bundle to govern its launchServer command "
        "(deny | reuse | sandbox); empty = ungoverned local/Git run (BE-0090)",
    ),
    handoff: str = typer.Option(
        "auto",
        "--handoff",
        hidden=True,
        help="human-in-the-loop handoff responder (BE-0179): auto (interactive when stdin is a TTY, "
        "else none) | prompt | stream (serve sets this) | off",
    ),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Explore the app with AI toward a goal and write the recorded scenario.

    With `--out` it writes there; otherwise it auto-names a file under the target's configured
    `scenarios` dir — or, for a read-only Git `--config`, under the current directory (BE-0063).
    """
    eff, _source, checkout_root = _load_effective_with_source(config, target_name)
    # --headed/--no-headed overrides the target's `headless` config (web backend only; iOS ignores it).
    eff = _with_headed(eff, headed)
    # --browser overrides the target's `browser` config (web backend only; flag > config > chromium).
    eff = _resolve_browser(eff, browser)
    # --language overrides the target's `ai.language` (flag > config > auto), BE-0188.
    eff = _resolve_language(eff, language)
    out_path = _record_out_path(eff, out, name, goal, target_name, checkout_root=checkout_root)
    # Attribute this session's AI tokens/cost to the `record` command (BE-0196) — reporting only,
    # never on a verdict path; the ledger only persists when configured.
    _install_usage_ledger(eff, "record", scenario=out_path.stem)
    before = _usage.snapshot()
    before_cat = _usage.snapshot_by_category()  # for the per-category breakdown (BE-0194 §4)
    # Fail closed (BE-0047): the authoring agent and the alert guard both reach the model via the
    # resolved SDK provider (Anthropic / Bedrock / ant), so a missing credential is an actionable
    # error here, not a quiet fallback.
    _require_ai_credential(eff)
    # Disclose that on-screen secrets are not redacted from the screenshots sent to the AI or
    # stored under runs/ (BE-0151), before the authoring loop starts.
    _warn_onscreen_secrets(eff)
    # Mask the textual model inputs (element trees, the alert instruction) before they leave the
    # process; the screenshot is sent as-is — images cannot be pixel-masked (BE-0047).
    redactor = _ai_redactor(eff)
    authoring_agent = make_agent(ai=eff.ai, redactor=redactor)
    actuator, _ = _select_actuator_or_exit(backend, eff, [])
    alert_guard = None
    if dismiss_alerts:
        alert_guard = _build_alert_guard(eff, redactor, alert_instruction)
    # Web has no simctl udid (launch_driver ignores it for playwright); resolving "booted" would
    # shell out to simctl and crash off-macOS, so skip it for the web backend.
    if actuator != "playwright":
        udid = _simctl.resolve_udid(udid)

    # Bring up the app's target server (the web baseUrl host) if it declares launchServer — reused
    # if already serving, started otherwise. Stopped when this command exits (atexit).
    stop_server, _exec_decision = _start_launch_server_or_exit(eff, upload_exec=upload_exec or None)
    atexit.register(stop_server)

    # Narrate the otherwise-silent device work (reinstall + boot + launch) so the watcher
    # knows what's happening before the agent takes over. Progress goes to stderr, like the
    # record loop's own stream, leaving stdout for the final result line.
    def say(msg: str) -> None:
        typer.echo(msg, err=True)

    announce_ai(say, default_model=_RECORD_MODEL, ai=eff.ai)
    # Resolve the handoff responder before the slow device boot, so an invalid --handoff mode fails
    # fast and cleanly (BE-0179) rather than after launching, or as an uncaught traceback.
    try:
        handoff_responder = make_handoff(handoff, say=say)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from None
    say(
        f"⚙️  preparing the simulator — installing and launching {target_name} (this can take a moment) …"
    )
    try:
        driver, _readiness = launch_driver(udid, eff, actuator, Preconditions(erase=erase))
    except device_errors.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    say(f"✅ app is up — authoring toward the goal: {goal!r}")
    try:
        scenario = record_loop(
            driver,
            goal,
            authoring_agent,
            name=goal,
            max_steps=max_steps,
            with_screenshot=screenshot,
            alert_guard=alert_guard,
            secret_tokens=_secret_tokens(eff),
            # Whether to record a scenario-wide screen video while authoring is the platform's to
            # answer, behind the Environment seam (BE-0256): the simctl-backed device (xcuitest)
            # records via a simctl interval, web captures by other means during replay, and the fake
            # actuator has no device. Asking the seam is what fixes xcuitest, which a per-actuator
            # name test historically skipped silently.
            capture_video=environment_for(actuator, udid).captures_video(),
            report=say,
            # A "needs human" turn hands off to this responder; with no responder (CI, non-interactive)
            # the loop raises below rather than hanging or guessing (BE-0179).
            handoff=handoff_responder,
        )
    except HumanHandoffUnavailable as e:
        # Deterministic under automation: a flow that needs a human is a clean, labeled non-zero
        # exit, not a hang and not an AI guess (BE-0179).
        typer.echo(f"this flow needs human handoff ({e}); re-record interactively", err=True)
        raise typer.Exit(3) from None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(dump_scenarios([scenario]), encoding="utf-8")
    typer.echo(f"recorded {len(scenario.steps)} steps -> {out_path}")
    # Report what the authoring (and alert-guard) AI consumed; to stderr, like the progress
    # narration, so stdout stays the single result line. The one-line total is followed by a
    # per-category breakdown (plan / next_action / alert-guard) so a token-saving change is
    # measurable (BE-0194 §4) — reporting only, never on the pass/fail path.
    spent = _usage.snapshot() - before
    if spent.calls:
        typer.echo(spent.render(), err=True)
        for line in _usage.breakdown_lines(before_cat, _usage.snapshot_by_category()):
            typer.echo(line, err=True)


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(record)
