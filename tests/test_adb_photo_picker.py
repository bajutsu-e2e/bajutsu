"""`selectPhotos` on adb: reading Android's system photo picker and driving it by resource id.

The fixtures are UI Automator dumps of the real picker on an API 34 `google_apis` emulator with four
seeded images: a multi-selection picker before and after two taps, and a single-selection picker.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from bajutsu.common.backend_cli import adb
from bajutsu.common.drivers import base
from bajutsu.common.drivers.adb import AdbDriver, HierarchyRead
from bajutsu.common.drivers.adb.photo_picker import read_picker_state

_FIXTURES = Path(__file__).parent / "fixtures" / "android_photo_picker"
MULTI = (_FIXTURES / "multi_unselected.xml").read_text(encoding="utf-8")
MULTI_SELECTED = (_FIXTURES / "multi_two_selected.xml").read_text(encoding="utf-8")
SINGLE = (_FIXTURES / "single.xml").read_text(encoding="utf-8")
# The app's own screen once the picker has handed its result back. Its one view shares the grid's
# thumbnail id on purpose: only the picker's own package may contribute cells.
APP = (
    "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">"
    '<node resource-id="com.example.app:id/icon_thumbnail" class="android.widget.ImageView" '
    'package="com.example.app" text="" content-desc="" selected="false" bounds="[0,0][100,100]" />'
    "</hierarchy>"
)

# Row-major cell frames as the multi-selection dump lays them out: three per row, newest first.
CELLS = [
    (0.0, 640.0, 354.0, 354.0),
    (362.0, 640.0, 355.0, 355.0),
    (725.0, 640.0, 355.0, 355.0),
    (0.0, 1003.0, 354.0, 354.0),
]


def _root(xml: str) -> ET.Element:
    return ET.fromstring(xml.split("?>", 1)[1])


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)


PICKER_FOCUS = (
    "  mCurrentFocus=Window{9599840 u0 com.google.android.providers.media.module/"
    "com.android.providers.media.photopicker.PhotoPickerActivity}\n"
)
APP_FOCUS = "  mCurrentFocus=Window{f1c4b25 u0 com.example.app/com.example.app.MainActivity}\n"


def _driver(
    reads: list[str], *, api_level: int | None = 34, focus: str = PICKER_FOCUS
) -> tuple[AdbDriver, list[list[str]]]:
    """A driver whose reads replay `reads` in order (the last one repeats), recording every tap.

    The focus query answers `focus`; it is left out of the recorded calls, which hold the taps alone.
    """
    it: Iterator[str] = iter(reads)
    last = [reads[-1]]

    def fetch(_since: float | None) -> HierarchyRead:
        last[0] = next(it, last[0])
        return HierarchyRead(last[0])

    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if args == adb.focused_window_cmd("U"):
            return focus
        calls.append(args)
        return ""

    return AdbDriver("U", run=run, fetch_hierarchy=fetch, api_level=api_level), calls


def _tap(frame: base.Frame) -> list[str]:
    x, y = base.frame_center(frame)
    return adb.tap_cmd("U", x, y)


def test_reads_a_multi_selection_grid_in_row_major_order() -> None:
    state = read_picker_state(_root(MULTI))
    assert state.present
    assert state.multi_select
    assert [c.frame for c in state.cells] == CELLS
    assert not any(c.selected for c in state.cells)
    # The add button appears only once something is picked.
    assert state.add_button is None


def test_reads_selection_and_the_add_button_after_two_picks() -> None:
    state = read_picker_state(_root(MULTI_SELECTED))
    assert [c.selected for c in state.cells] == [True, True, False, False]
    assert state.add_button == (807.0, 2176.0, 231.0, 133.0)


def test_a_single_selection_picker_shows_no_check_marks() -> None:
    state = read_picker_state(_root(SINGLE))
    assert state.present
    assert not state.multi_select
    assert len(state.cells) == 4


def test_an_apps_own_view_never_counts_as_a_picker_cell() -> None:
    state = read_picker_state(_root(APP))
    assert not state.present
    assert state.cells == ()


def test_picks_two_cells_then_confirms_with_add() -> None:
    # Two agreeing reads settle the grid; each tap reads back selected; the add button confirms;
    # the picker closing ends the step.
    driver, calls = _driver([MULTI, MULTI, MULTI_SELECTED, MULTI_SELECTED, MULTI_SELECTED, APP])
    driver.select_photos([0, 1], timeout=5)
    assert calls == [_tap(CELLS[0]), _tap(CELLS[1]), _tap((807.0, 2176.0, 231.0, 133.0))]
    drained = driver.drain_actuations()
    assert [a.target for a in drained.records] == [
        "photo picker cell 0",
        "photo picker cell 1",
        "button_add",
    ]


def test_a_single_selection_picker_confirms_on_its_one_tap() -> None:
    cell = read_picker_state(_root(SINGLE)).cells[2]
    driver, calls = _driver([SINGLE, SINGLE, APP])
    driver.select_photos([2], timeout=5)
    assert calls == [_tap(cell.frame)]


def test_a_single_selection_picker_refuses_two_indices_before_tapping() -> None:
    driver, calls = _driver([SINGLE, SINGLE])
    with pytest.raises(base.ElementNotFound, match="allows one image"):
        driver.select_photos([0, 1], timeout=5)
    assert calls == []


def test_an_index_beyond_the_grid_fails_naming_what_it_found() -> None:
    driver, calls = _driver([MULTI])
    with pytest.raises(base.ElementNotFound, match=r"needs 5 .*found 4 cell"):
        driver.select_photos([4], timeout=0)
    assert calls == []


def test_no_picker_on_screen_fails_the_grid_wait() -> None:
    driver, _ = _driver([APP])
    with pytest.raises(base.ElementNotFound, match="no photo picker"):
        driver.select_photos([0], timeout=0)


def test_a_tap_that_never_registers_times_out() -> None:
    # The cell never reads back selected, so the step fails rather than racing on to the next tap.
    driver, calls = _driver([MULTI, MULTI], api_level=None)
    with pytest.raises(base.ElementNotFound, match="cell 0 to read back selected"):
        driver.select_photos([0], timeout=0.05)
    assert calls == [_tap(CELLS[0])]


def test_a_picker_that_never_takes_input_focus_is_never_tapped() -> None:
    # The tree already shows the picker, but input still goes to the app beneath it, so a cell tap
    # would land on the app's own view under the cell.
    driver, calls = _driver([MULTI, MULTI], focus=APP_FOCUS)
    with pytest.raises(base.ElementNotFound, match="never took input focus"):
        driver.select_photos([0], timeout=0.05)
    assert calls == []


def test_below_api_33_refuses_before_any_device_work() -> None:
    driver, calls = _driver([MULTI], api_level=32)
    with pytest.raises(base.UnsupportedAction, match="API 33"):
        driver.select_photos([0], timeout=5)
    assert calls == []


def test_adb_advertises_select_photos() -> None:
    assert base.Capability.SELECT_PHOTOS in AdbDriver.CAPABILITIES
