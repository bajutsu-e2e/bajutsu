"""The run's logging knob: a default that changes nothing, and a level that reaches `logger.debug`."""

from __future__ import annotations

import logging

import pytest

from bajutsu import diagnostics


@pytest.fixture(autouse=True)
def _restore_package_logger() -> object:
    """Put the `bajutsu` logger back as it was: `configure` mutates process-wide state."""
    logger = logging.getLogger("bajutsu")
    before = (list(logger.handlers), logger.level, logger.propagate)
    yield
    logger.handlers, logger.level, logger.propagate = before


def test_the_default_level_leaves_debug_where_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    # A run that sets nothing must print what it printed before this module existed, so turning
    # diagnostics on is a deliberate act and never a surprise in someone's CI log.
    monkeypatch.delenv(diagnostics.LEVEL_ENV, raising=False)
    assert diagnostics.resolve_level() == logging.WARNING


def test_the_environment_names_the_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(diagnostics.LEVEL_ENV, "debug")  # any case
    assert diagnostics.resolve_level() == logging.DEBUG


def test_a_mistyped_level_falls_back_rather_than_failing_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The knob is a diagnostic aid; a typo in it must not cost the run it was meant to explain.
    monkeypatch.setenv(diagnostics.LEVEL_ENV, "louder please")
    assert diagnostics.resolve_level() == logging.WARNING


def test_an_explicit_level_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(diagnostics.LEVEL_ENV, "error")
    assert diagnostics.resolve_level("DEBUG") == logging.DEBUG


def test_configure_lets_a_driver_debug_line_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The point of the whole module: `logger.debug` in the drivers reached nobody before it.
    monkeypatch.setenv(diagnostics.LEVEL_ENV, "DEBUG")
    diagnostics.configure()
    logging.getLogger("bajutsu.drivers.adb").debug("resolved a frame")
    err = capsys.readouterr().err
    assert "DEBUG bajutsu.drivers.adb: resolved a frame" in err


def test_configure_twice_does_not_double_the_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `configure` runs on every CLI invocation, and an embedding application may call it again. Its
    # own handler is replaced, never stacked, or a long run's log would grow a copy per call.
    monkeypatch.setenv(diagnostics.LEVEL_ENV, "DEBUG")
    diagnostics.configure()
    diagnostics.configure()
    logging.getLogger("bajutsu.drivers.adb").debug("once")
    assert capsys.readouterr().err.count("once") == 1


def test_configure_leaves_the_records_reaching_the_root_logger(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Propagation must stay on, or an embedding application's own handler — and pytest's `caplog`,
    # which is how most of this suite asserts on a warning — stops seeing anything the drivers log.
    monkeypatch.setenv(diagnostics.LEVEL_ENV, "DEBUG")
    diagnostics.configure()
    with caplog.at_level(logging.WARNING):
        logging.getLogger("bajutsu.drivers.adb").warning("read lag")
    assert "read lag" in caplog.text


def test_configure_keeps_a_handler_it_did_not_install(monkeypatch: pytest.MonkeyPatch) -> None:
    # `serve` installs its own handler on the package logger; replacing that would silence the worker.
    monkeypatch.setenv(diagnostics.LEVEL_ENV, "DEBUG")
    logger = logging.getLogger("bajutsu")
    theirs = logging.NullHandler()
    logger.addHandler(theirs)
    diagnostics.configure()
    diagnostics.configure()
    assert theirs in logger.handlers
