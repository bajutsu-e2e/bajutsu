"""The Claude-free path is zero-config, and the gate proves it (BE-0101).

"You can use Bajutsu without Claude" is a stated, tested property here, not an accident of the
architecture. Two guards, both in the fast Linux gate (no device, no real API, no key):

1. an **import-time guard** — importing the deterministic CLI path must not pull in an AI SDK, so a
   stray top-level `import anthropic` (the likeliest way zero-config silently regresses) fails here;
2. a **no-AI-setup regression** over the Claude-free command set from `capabilities` — every one
   must reach and run without a credential and without constructing a model client.

Driven off the classification, so a newly added Claude-free command is covered by construction.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bajutsu import capabilities
from bajutsu.cli import app

runner = CliRunner()

# AI SDKs that must never be imported just by loading the deterministic CLI path — the model client
# is constructed lazily, inside the Claude entry points only.
_FORBIDDEN_AT_IMPORT = ("anthropic",)


def test_importing_the_cli_pulls_in_no_ai_sdk() -> None:
    """A clean child interpreter: `import bajutsu.cli` must not load an AI SDK.

    Runs in a subprocess so an SDK another test already imported into this session can't mask a
    real top-level import. The env is stripped of AI credentials too, so nothing is read at import.
    """
    code = (
        "import os\n"
        "for v in ('ANTHROPIC_API_KEY','BAJUTSU_AI_PROVIDER','BAJUTSU_BEDROCK_MODEL','BAJUTSU_AGENT'):\n"
        "    os.environ.pop(v, None)\n"
        "import sys\n"
        "import bajutsu.cli  # runs the command scan — every commands/<name>.py is imported\n"
        f"forbidden = set({_FORBIDDEN_AT_IMPORT!r})\n"
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] in forbidden)\n"
        "sys.stdout.write(','.join(leaked))\n"
        "sys.exit(1 if leaked else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, (
        "importing the deterministic CLI path loaded an AI SDK at module top "
        f"(exit {result.returncode}).\n"
        f"leaked: {result.stdout.strip() or '(none)'}\n"
        f"stderr: {result.stderr.strip() or '(none)'}\n"
        "Keep the AI client lazy — construct it inside the Claude entry points, not at import."
    )


@pytest.fixture
def _no_ai_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """No key, no provider, no agent, no .env — and any model-client construction fails loudly."""
    monkeypatch.setattr("bajutsu.cli.load_dotenv", lambda *a, **k: None)
    for var in (
        "ANTHROPIC_API_KEY",
        "BAJUTSU_AI_PROVIDER",
        "BAJUTSU_BEDROCK_MODEL",
        "BAJUTSU_AGENT",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "anthropic.Anthropic",
        lambda *a, **k: pytest.fail("a Claude-free path constructed an Anthropic client"),
    )
    monkeypatch.setattr(
        "bajutsu.agents.make_agent",
        lambda *a, **k: pytest.fail("a Claude-free path constructed an authoring agent"),
    )


@pytest.mark.usefixtures("_no_ai_setup")
@pytest.mark.parametrize("command", capabilities.claude_free())
def test_claude_free_command_help_needs_no_ai_setup(command: str) -> None:
    """Every Claude-free command is reachable with zero AI setup (registered + importable clean)."""
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0
    assert "no AI credential" not in result.output


def _fake_config(tmp_path: Path) -> tuple[Path, Path]:
    scn = tmp_path / "s.yaml"
    scn.write_text("- name: demo\n  steps:\n    - tap: { id: home.title }\n", encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [fake] }\n"
        "targets:\n"
        "  demo: { bundleId: com.example.demo, idNamespaces: [home] }\n",
        encoding="utf-8",
    )
    return cfg, scn


@pytest.mark.usefixtures("_no_ai_setup")
def test_run_executes_deterministically_without_any_ai_setup(tmp_path: Path) -> None:
    """`run` (the flagship Claude-free path) executes end-to-end with no AI setup.

    The alert guard is on by default, so this also pins that it *degrades* to a no-op rather than
    failing closed when there is no credential — never the exit-2 "no AI credential" gate, and never
    a constructed client (the fixture fails if either the SDK or an agent is built).
    """
    cfg, scn = _fake_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "run",
            "--scenario",
            str(scn),
            "--target",
            "demo",
            "--backend",
            "fake",
            "--config",
            str(cfg),
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
    )
    # A missing credential must never be a run-blocking error: the deterministic run proceeds and
    # the guard no-ops. (Exit 1 here is the scenario's own assertion against the empty fake screen,
    # which is exactly the point — the failure is deterministic, not an AI-setup failure.)
    assert result.exit_code != 2
    assert "no AI credential" not in result.output
    assert "the alert guard will no-op" in result.output
