"""The cross-command bring-up helpers consolidated into `cli/_shared.py` (BE-0260)."""

from __future__ import annotations

import pytest
import typer

from bajutsu.cli._shared import (
    _ai_redactor,
    _build_alert_guard,
    _build_alert_locator,
    _select_actuator_or_exit,
    resolve_system_alert_handling_flag,
)
from bajutsu.common.config import Effective, load_config, resolve


def _eff(spec: str = "targets:\n  x:\n    bundleId: com.x\n") -> Effective:
    return resolve(load_config(spec), "x")


def test_select_actuator_or_exit_returns_actuator_and_backends() -> None:
    actuator, backends = _select_actuator_or_exit("fake", _eff(), [])
    assert actuator == "fake"
    assert backends == ["fake"]


def test_select_actuator_or_exit_exits_2_on_unknown_backend() -> None:
    with pytest.raises(typer.Exit) as excinfo:
        _select_actuator_or_exit("bogus", _eff(), [])
    assert excinfo.value.exit_code == 2


def test_build_alert_guard_no_op_without_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    # The deliberate BE-0260 alignment: with no AI credential the guard no-ops (returns None)
    # instead of constructing a client that would fall back to a hosted default.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    eff = _eff()
    assert _build_alert_locator(eff, _ai_redactor(eff)) is None
    assert _build_alert_guard(eff, _ai_redactor(eff), "") is None


def test_build_alert_guard_binds_dismiss_when_credential_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    eff = _eff()
    guard = _build_alert_guard(eff, _ai_redactor(eff), "")
    assert callable(guard)


def test_build_alert_guard_no_op_under_provider_none(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # BE-0394: the kill switch holds *with the key present in the environment* — the difference from
    # the unset-key case above, and the whole point of committing the policy to the config. The note
    # names the setting rather than an env var to export.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    eff = _eff("targets:\n  x:\n    bundleId: com.x\n    ai: { provider: none }\n")
    assert _build_alert_locator(eff, _ai_redactor(eff)) is None
    assert _build_alert_guard(eff, _ai_redactor(eff), "") is None
    out = capsys.readouterr().out
    assert "ai.provider: none" in out
    # And it says outright that nothing will be cleared. Only `record` / `crawl` reach here since
    # BE-0402, and neither has a native path to fall back on, so the note must not imply one.
    assert "no system prompt will be cleared" in out


def test_build_alert_guard_names_the_bedrock_model_gap(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Bedrock authenticates with AWS credentials but still needs a model id; with none set the guard
    # no-ops and says which setting is missing, distinct from the Anthropic-key note above.
    monkeypatch.setenv("BAJUTSU_AI_PROVIDER", "bedrock")
    monkeypatch.delenv("BAJUTSU_BEDROCK_MODEL", raising=False)
    eff = _eff()
    assert _build_alert_locator(eff, _ai_redactor(eff)) is None
    out = capsys.readouterr().out
    assert "no Bedrock model id is set" in out
    assert "no system prompt will be cleared" in out


def test_credential_gap_message_for_provider_none_names_the_setting() -> None:
    # The message `record` / `crawl` / `triage --ai` print before exiting 2: the setting, and how to
    # re-enable an AI path — never "set $ANTHROPIC_API_KEY", which would not lift the switch.
    from bajutsu.ai.disabled import DISABLED
    from bajutsu.cli._shared import _credential_gap_message

    eff = _eff("targets:\n  x:\n    bundleId: com.x\n    ai: { provider: none }\n")
    msg = _credential_gap_message(DISABLED, eff)
    assert "ai.provider: none" in msg and "ANTHROPIC_API_KEY" not in msg


def test_resolve_system_alert_handling_flag_resolves_against_each_command_default() -> None:
    # The `--alert-handling` / `--dismiss-alerts` aliases this once merged were deleted (BE-0401),
    # so the flag resolves against the unset behavior each command asks for: `run` leaves it None
    # (each scenario's own value applies), `record` / `crawl` pass default=True.
    assert resolve_system_alert_handling_flag(True) is True
    assert resolve_system_alert_handling_flag(False) is False
    assert resolve_system_alert_handling_flag(None) is None
    assert resolve_system_alert_handling_flag(None, default=True) is True
    assert resolve_system_alert_handling_flag(False, default=True) is False


def test_default_config_is_the_single_config_source_constant() -> None:
    # `_shared` re-exports the constant rather than owning a second copy, so a rename of the
    # default config filename lands once in `config_source` (BE-0251).
    from bajutsu.cli import _shared
    from bajutsu.common import config_source

    assert _shared.DEFAULT_CONFIG is config_source.DEFAULT_CONFIG
