"""The cross-command bring-up helpers consolidated into `cli/_shared.py` (BE-0260)."""

from __future__ import annotations

import pytest
import typer

from bajutsu.cli._shared import (
    _ai_redactor,
    _build_alert_guard,
    _build_alert_locator,
    _select_actuator_or_exit,
)
from bajutsu.config import Effective, load_config, resolve


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


def test_default_config_is_the_single_config_source_constant() -> None:
    # `_shared` re-exports the constant rather than owning a second copy, so a rename of the
    # default config filename lands once in `config_source` (BE-0251).
    from bajutsu import config_source
    from bajutsu.cli import _shared

    assert _shared.DEFAULT_CONFIG is config_source.DEFAULT_CONFIG
