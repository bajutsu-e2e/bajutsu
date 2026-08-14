"""`nativeZ`, the app-measured front-to-back position on `Element` (BE-0355).

The field is diagnostic only: no selector matches on it and no occlusion check reads it. Its whole
contract today is an honest absence — every backend carries the key and reports `None`, because the
iOS and Android paths that would measure a real value are that item's still-open Units 2 and 3.
These tests pin both halves: nothing derives a value from the element tree's own document order, and
nothing that already decided occlusion now consults the field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bajutsu.dom import parse_dom
from bajutsu.drivers import base
from bajutsu.drivers.adb import parse_hierarchy
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence.golden import compare_element, load_golden, save_golden

_HIERARCHY = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hierarchy rotation="0">'
    '<node resource-id="com.example:id/ok" class="android.widget.Button" text="OK"'
    ' content-desc="" bounds="[0,0][100,50]" />'
    "</hierarchy>"
)


def _element(identifier: str, frame: base.Frame, native_z: float | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": None,
        "traits": [],
        "value": None,
        "frame": frame,
        "nativeZ": native_z,
    }


# ---------------------------------------------------------------------------
# native_z_from_json — the one rule every reader of persisted evidence shares
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(3, 3.0), (2.5, 2.5), (0, 0.0), (-1.5, -1.5)],
)
def test_native_z_from_json_reads_a_number_as_a_float(raw: object, expected: float) -> None:
    value = base.native_z_from_json(raw)
    assert value == expected
    assert isinstance(value, float)  # an int on the wire still reaches the driver API as a float


@pytest.mark.parametrize("raw", [None, "3.0", [3.0], {}, True, False, 10**400])
def test_native_z_from_json_degrades_a_non_number_to_none(raw: object) -> None:
    # A malformed value must read as the same absence an uninstrumented app reports, not as a
    # position. Two traps are worth pinning. `True` is an `int` subclass, so a naive numeric check
    # would turn it into 1.0 and hand a reader a stacking order that was never measured. `10**400`
    # is a number JSON can hold and a float cannot, so a bare `float()` would raise `OverflowError`
    # out of a loader whose whole contract is to survive a value it cannot use.
    assert base.native_z_from_json(raw) is None


# ---------------------------------------------------------------------------
# Every backend's parser reports the honest absence
# ---------------------------------------------------------------------------


def test_adb_parse_reports_no_native_z() -> None:
    (el,) = parse_hierarchy(_HIERARCHY)
    assert el["nativeZ"] is None


def test_dom_parse_reports_no_native_z() -> None:
    record: dict[str, Any] = {
        "identifier": "auth.submit",
        "role": "button",
        "label": "Login",
        "value": None,
        "frame": [0, 0, 100, 40],
    }
    (el,) = parse_dom([record])
    assert el["nativeZ"] is None


# ---------------------------------------------------------------------------
# FakeDriver — the seam a nativeZ-aware reader is exercised through on the fast gate
# ---------------------------------------------------------------------------


def test_fake_driver_reports_a_seeded_native_z() -> None:
    driver = FakeDriver(screen=[_element("front", (0.0, 0.0, 10.0, 10.0), native_z=8.0)])
    (el,) = driver.query()
    assert el["nativeZ"] == 8.0


def test_fake_driver_keeps_a_seeded_native_z_across_a_scroll() -> None:
    # The scrollable mode rebuilds each element to translate its frame; a seeded position must
    # survive that rebuild, since scrolling moves an element without restacking it.
    driver = FakeDriver(
        screen=[_element("row", (0.0, 200.0, 100.0, 40.0), native_z=4.0)],
        viewport=(320.0, 100.0),
    )
    driver.scroll((160.0, 80.0), (160.0, 20.0))
    (el,) = driver.query()
    assert el["nativeZ"] == 4.0
    assert el["frame"][1] < 200.0  # the frame did move, so this is not a no-op read


# ---------------------------------------------------------------------------
# The occlusion decisions BE-0355 leaves untouched
# ---------------------------------------------------------------------------


def test_topmost_at_point_ignores_native_z() -> None:
    # `topmost_at_point` stays the document-order proxy it documents itself as. A target seeded
    # far in front of a later element that overlaps it is still reported as covered: were the
    # heuristic quietly reading `nativeZ`, this would come back `None`. Folding a measured position
    # into this decision is deliberately left to a future item, once a backend measures one.
    target = _element("target", (0.0, 0.0, 100.0, 20.0), native_z=99.0)
    covering = _element("sticky-header", (0.0, 0.0, 300.0, 10.0), native_z=0.0)
    result = base.topmost_at_point([target, covering], base.frame_center(target["frame"]), target)
    assert result is covering


def test_is_tappable_ignores_native_z() -> None:
    target = _element("target", (0.0, 0.0, 100.0, 20.0), native_z=99.0)
    covering = _element("cover", (0.0, 0.0, 300.0, 10.0), native_z=0.0)
    driver = FakeDriver(screen=[target, covering])
    assert driver.is_tappable({"id": "target"}) is False


# ---------------------------------------------------------------------------
# Golden files — recorded before the field existed, and never compared on it
# ---------------------------------------------------------------------------


def test_golden_loads_a_file_recorded_before_native_z_existed(tmp_path: Path) -> None:
    # Every golden checked in today predates the field. Requiring it would reject them all, so the
    # loader treats it as optional and fills the same absence a live driver reports.
    path = tmp_path / "controls.json"
    path.write_text(
        json.dumps(
            {
                "ctrl.ok": {
                    "identifier": "ctrl.ok",
                    "label": "OK",
                    "traits": ["button"],
                    "value": None,
                    "frame": [0.0, 0.0, 100.0, 44.0],
                }
            }
        ),
        encoding="utf-8",
    )
    assert load_golden(path)["ctrl.ok"]["nativeZ"] is None


def test_golden_recording_omits_native_z(tmp_path: Path) -> None:
    # The loader ignores the field, so the recorder must not write it either: a value that no
    # comparison reads would otherwise churn every re-recording once Units 2 and 3 measure one.
    path = tmp_path / "controls.json"
    save_golden([_element("ctrl.ok", (0.0, 0.0, 100.0, 44.0), native_z=7.0)], ["ctrl.ok"], path)

    assert "nativeZ" not in json.loads(path.read_text(encoding="utf-8"))["ctrl.ok"]
    assert load_golden(path)["ctrl.ok"]["nativeZ"] is None


def test_golden_comparison_ignores_native_z() -> None:
    # A golden pins the recorded contract; `nativeZ` is a reading of the moment, so a layout change
    # that moves an element forward must not fail an assertion about its identity or state.
    expected = _element("ctrl.ok", (0.0, 0.0, 100.0, 44.0), native_z=1.0)
    actual = _element("ctrl.ok", (0.0, 0.0, 100.0, 44.0), native_z=7.0)
    assert compare_element(expected, actual) == []
