"""The resident server's waits must fit inside the host's timeouts.

The server is Kotlin and never runs in this gate, so its constants are read from its source. A read that
outlasts the host's timeout looks like a dead channel, and a 10 Hz screen once made one take 120.9 s
against a 5 s timeout: this is the check that the two ends cannot drift apart again.
"""

from __future__ import annotations

import re
from pathlib import Path

from bajutsu.common.backend_cli.adb_resident import budgets

_SERVER = (
    Path(__file__).resolve().parents[1]
    / "BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server"
    / "ResidentServerTest.kt"
)
_SOURCE = _SERVER.read_text(encoding="utf-8")
# Comments may name `waitForIdle`; only code can call it.
_CODE = re.sub(r"/\*.*?\*/|//[^\n]*", "", _SOURCE, flags=re.DOTALL)


def _seconds(name: str) -> float:
    """One of the server's `..._MS` constants, read from its source and converted to seconds."""
    match = re.search(rf"const val {name} = ([\d_]+)L\b", _CODE)
    assert match is not None, f"{name} not found in the server source"
    return int(match.group(1).replace("_", "")) / 1000


def test_the_server_default_budget_is_the_one_the_host_sends() -> None:
    assert _seconds("DEFAULT_WAIT_BUDGET_MS") == budgets.WAIT_BUDGET_MS / 1000


def test_a_read_leaves_room_inside_the_host_timeout_for_its_last_dump() -> None:
    # A dump cannot be interrupted, so its own time has to fit in what the waits leave. A real one took
    # tens of milliseconds on an API 34 emulator; a full second of room is a slow device's margin.
    room = budgets.READ_TIMEOUT_S - budgets.WAIT_BUDGET_MS / 1000
    assert room >= 1.0


def test_one_wait_cannot_spend_the_budget_the_read_mark_needs() -> None:
    # The first idle wait runs ahead of the mark wait, so both have to fit for the mark to get its due.
    assert (
        _seconds("IDLE_WAIT_MS") + _seconds("POSTDATE_BUDGET_MS") <= budgets.WAIT_BUDGET_MS / 1000
    )


def test_act_fits_inside_its_timeout_with_every_stage_at_its_ceiling() -> None:
    # `act()` adds a press-and-hold's own duration to the timeout, so the hold stays out of this sum.
    waits = (
        budgets.WAIT_BUDGET_MS / 1000
        + _seconds("ACT_PUBLISH_BUDGET_MS")
        + _seconds("ACT_TREE_BUDGET_MS")
    )
    assert waits <= budgets.ACTION_TIMEOUT_S - 1.0


def test_type_fits_inside_its_timeout_with_every_stage_at_its_ceiling() -> None:
    waits = budgets.WAIT_BUDGET_MS / 1000 + _seconds("FOCUS_BUDGET_MS")
    assert waits <= budgets.ACTION_TIMEOUT_S - 1.0


def test_no_idle_wait_escapes_the_budget() -> None:
    # A bare call waits for whatever `Configurator` last said, so it runs only inside `WaitBudget`,
    # right after that class has set the bound.
    assert len(re.findall(r"device\.waitForIdle\(\)", _CODE)) == 1
    budget_class = _CODE[_CODE.index("private class WaitBudget") :]
    await_idle = budget_class[budget_class.index("fun awaitIdle()") :]
    assert "device.waitForIdle()" in await_idle[: await_idle.index("fun ", 1)]


def test_every_dump_runs_under_a_bound_of_its_own() -> None:
    # `dumpWindowHierarchy` waits for idle inside itself, where no argument reaches it. `settled` is
    # the only caller, and it sets the bound before each read.
    assert _CODE.count("dumpHierarchy(device)") == 2  # `settledDump` and `settledActBounds`
    settled = _CODE[_CODE.index("private fun <T> settled") : _CODE.index("private fun settledDump")]
    assert settled.count("budget.capDumpWaits()") == settled.count("read()")
