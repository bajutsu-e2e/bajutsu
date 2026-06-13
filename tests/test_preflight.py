"""Environment runnability gate (the `doctor` preflight)."""

from __future__ import annotations

from collections.abc import Callable

from bajutsu import preflight


def _which(present: set[str]) -> Callable[[str], str | None]:
    return lambda exe: f"/usr/bin/{exe}" if exe in present else None


def test_idb_all_present_passes() -> None:
    checks = preflight.runnability(
        "idb", which=_which({"xcrun", "idb", "idb_companion"}), booted_count=lambda: 1
    )
    assert [c.name for c in checks] == ["xcrun", "idb", "idb_companion", "Simulator booted"]
    assert preflight.passed(checks)


def test_missing_companion_fails_with_hint() -> None:
    checks = preflight.runnability("idb", which=_which({"xcrun", "idb"}), booted_count=lambda: 1)
    assert not preflight.passed(checks)
    companion = next(c for c in checks if c.name == "idb_companion")
    assert not companion.ok and "brew install" in companion.detail


def test_no_booted_simulator_fails() -> None:
    checks = preflight.runnability(
        "idb", which=_which({"xcrun", "idb", "idb_companion"}), booted_count=lambda: 0
    )
    assert not preflight.passed(checks)
    assert not next(c for c in checks if c.name == "Simulator booted").ok


def test_fake_backend_needs_nothing() -> None:
    assert preflight.runnability("fake") == []


def test_render_marks_pass_and_fail() -> None:
    out = preflight.render(
        preflight.runnability("idb", which=_which({"xcrun"}), booted_count=lambda: 0)
    )
    assert "✓ xcrun" in out and "✗ idb" in out
