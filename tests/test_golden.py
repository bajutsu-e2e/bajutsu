"""Tests for golden element-tree comparison (BE-0006)."""

from __future__ import annotations

import json
from pathlib import Path

from bajutsu.drivers.base import Element, Frame

# ---------------------------------------------------------------------------
# Helpers — construct Element dicts inline (no fixtures)
# ---------------------------------------------------------------------------


def _el(
    identifier: str | None = None,
    label: str | None = None,
    traits: list[str] | None = None,
    value: str | None = None,
    frame: Frame = (0.0, 0.0, 100.0, 50.0),
) -> Element:
    return Element(
        identifier=identifier,
        label=label,
        traits=traits or [],
        value=value,
        frame=frame,
    )


# ---------------------------------------------------------------------------
# compare_element — field-level comparison
# ---------------------------------------------------------------------------


class TestCompareElement:
    def test_identical_elements_produce_no_mismatches(self) -> None:
        from bajutsu.evidence.golden import compare_element

        el = _el(identifier="ctrl.toggle", label="Toggle", traits=["switch"], value="1")
        assert compare_element(el, el) == []

    def test_identifier_mismatch(self) -> None:
        from bajutsu.evidence.golden import compare_element

        expected = _el(identifier="ctrl.toggle")
        actual = _el(identifier="ctrl.switch")
        mismatches = compare_element(expected, actual)
        assert len(mismatches) == 1
        assert mismatches[0].field == "identifier"
        assert mismatches[0].expected == "ctrl.toggle"
        assert mismatches[0].actual == "ctrl.switch"

    def test_label_mismatch(self) -> None:
        from bajutsu.evidence.golden import compare_element

        expected = _el(identifier="ctrl.toggle", label="Toggle")
        actual = _el(identifier="ctrl.toggle", label="Switch")
        mismatches = compare_element(expected, actual)
        assert len(mismatches) == 1
        assert mismatches[0].field == "label"

    def test_value_mismatch(self) -> None:
        from bajutsu.evidence.golden import compare_element

        expected = _el(identifier="ctrl.toggle", value="1")
        actual = _el(identifier="ctrl.toggle", value="0")
        mismatches = compare_element(expected, actual)
        assert len(mismatches) == 1
        assert mismatches[0].field == "value"

    def test_traits_compared_as_set_order_independent(self) -> None:
        from bajutsu.evidence.golden import compare_element

        expected = _el(identifier="ctrl.button", traits=["button", "notEnabled"])
        actual = _el(identifier="ctrl.button", traits=["notEnabled", "button"])
        assert compare_element(expected, actual) == []

    def test_traits_mismatch(self) -> None:
        from bajutsu.evidence.golden import compare_element

        expected = _el(identifier="ctrl.toggle", traits=["switch"])
        actual = _el(identifier="ctrl.toggle", traits=["button"])
        mismatches = compare_element(expected, actual)
        assert len(mismatches) == 1
        assert mismatches[0].field == "traits"

    def test_multiple_mismatches_reported(self) -> None:
        from bajutsu.evidence.golden import compare_element

        expected = _el(identifier="ctrl.toggle", label="Toggle", value="1")
        actual = _el(identifier="ctrl.switch", label="Switch", value="0")
        mismatches = compare_element(expected, actual)
        assert len(mismatches) == 3
        fields = {m.field for m in mismatches}
        assert fields == {"identifier", "label", "value"}


# ---------------------------------------------------------------------------
# frame_is_sane — tolerant geometry check
# ---------------------------------------------------------------------------

SCREEN: Frame = (0.0, 0.0, 393.0, 852.0)


class TestFrameIsSane:
    def test_normal_frame_within_screen(self) -> None:
        from bajutsu.evidence.golden import frame_is_sane

        assert frame_is_sane((10.0, 100.0, 200.0, 44.0), SCREEN) is True

    def test_zero_width_rejected(self) -> None:
        from bajutsu.evidence.golden import frame_is_sane

        assert frame_is_sane((10.0, 100.0, 0.0, 44.0), SCREEN) is False

    def test_zero_height_rejected(self) -> None:
        from bajutsu.evidence.golden import frame_is_sane

        assert frame_is_sane((10.0, 100.0, 200.0, 0.0), SCREEN) is False

    def test_outside_screen_bounds_rejected(self) -> None:
        from bajutsu.evidence.golden import frame_is_sane

        assert frame_is_sane((400.0, 0.0, 100.0, 44.0), SCREEN) is False

    def test_edge_aligned_accepted(self) -> None:
        from bajutsu.evidence.golden import frame_is_sane

        assert frame_is_sane((0.0, 0.0, 393.0, 852.0), SCREEN) is True


# ---------------------------------------------------------------------------
# compare_golden — compare a golden dict against query() results
# ---------------------------------------------------------------------------


class TestCompareGolden:
    def test_all_controls_match(self) -> None:
        from bajutsu.evidence.golden import compare_golden

        golden = {
            "ctrl.toggle": _el(identifier="ctrl.toggle", traits=["switch"], value="1"),
            "ctrl.button": _el(identifier="ctrl.button", traits=["button"]),
        }
        actual = [
            _el(identifier="ctrl.toggle", traits=["switch"], value="1"),
            _el(identifier="ctrl.button", traits=["button"]),
            _el(identifier="other.element"),
        ]
        result = compare_golden(golden, actual, SCREEN)
        assert result.mismatches == []
        assert result.missing == []
        assert result.frame_failures == []

    def test_missing_control_reported(self) -> None:
        from bajutsu.evidence.golden import compare_golden

        golden = {
            "ctrl.toggle": _el(identifier="ctrl.toggle", traits=["switch"]),
        }
        actual = [_el(identifier="other.element")]
        result = compare_golden(golden, actual, SCREEN)
        assert result.missing == ["ctrl.toggle"]

    def test_field_mismatch_reported(self) -> None:
        from bajutsu.evidence.golden import compare_golden

        golden = {
            "ctrl.toggle": _el(identifier="ctrl.toggle", traits=["switch"]),
        }
        actual = [_el(identifier="ctrl.toggle", traits=["button"])]
        result = compare_golden(golden, actual, SCREEN)
        assert len(result.mismatches) == 1
        assert result.mismatches[0].field == "traits"

    def test_bad_frame_reported(self) -> None:
        from bajutsu.evidence.golden import compare_golden

        golden = {
            "ctrl.toggle": _el(
                identifier="ctrl.toggle",
                traits=["switch"],
                frame=(10.0, 100.0, 200.0, 44.0),
            ),
        }
        actual = [
            _el(
                identifier="ctrl.toggle",
                traits=["switch"],
                frame=(10.0, 100.0, 0.0, 0.0),
            )
        ]
        result = compare_golden(golden, actual, SCREEN)
        assert len(result.frame_failures) == 1
        assert result.frame_failures[0] == "ctrl.toggle"


# ---------------------------------------------------------------------------
# load_golden / save_golden — JSON persistence
# ---------------------------------------------------------------------------


class TestLoadGolden:
    def test_load_roundtrips_saved_golden(self, tmp_path: Path) -> None:
        from bajutsu.evidence.golden import load_golden, save_golden

        elements = [
            _el(
                identifier="ctrl.toggle",
                label="Toggle",
                traits=["switch"],
                value="1",
                frame=(10.0, 200.0, 300.0, 44.0),
            ),
            _el(
                identifier="ctrl.button",
                label="Button",
                traits=["button"],
                frame=(10.0, 260.0, 300.0, 44.0),
            ),
            _el(identifier="other.unrelated"),
        ]
        golden_path = tmp_path / "controls.json"
        save_golden(elements, ["ctrl.toggle", "ctrl.button"], golden_path)

        loaded = load_golden(golden_path)
        assert set(loaded.keys()) == {"ctrl.toggle", "ctrl.button"}
        assert loaded["ctrl.toggle"]["label"] == "Toggle"
        assert loaded["ctrl.toggle"]["traits"] == ["switch"]
        assert loaded["ctrl.button"]["traits"] == ["button"]

    def test_save_skips_elements_not_in_ids(self, tmp_path: Path) -> None:
        from bajutsu.evidence.golden import load_golden, save_golden

        elements = [
            _el(identifier="ctrl.toggle", traits=["switch"]),
            _el(identifier="other.thing"),
        ]
        golden_path = tmp_path / "controls.json"
        save_golden(elements, ["ctrl.toggle"], golden_path)

        loaded = load_golden(golden_path)
        assert "other.thing" not in loaded
        assert "ctrl.toggle" in loaded

    def test_load_validates_element_shape(self, tmp_path: Path) -> None:
        import pytest

        from bajutsu.evidence.golden import load_golden

        bad = {"ctrl.toggle": {"identifier": "ctrl.toggle"}}
        golden_path = tmp_path / "bad.json"
        golden_path.write_text(json.dumps(bad))

        with pytest.raises(ValueError, match=r"missing.*field"):
            load_golden(golden_path)

    def test_load_rejects_bad_frame_length(self, tmp_path: Path) -> None:
        import pytest

        from bajutsu.evidence.golden import load_golden

        bad = {
            "ctrl.toggle": {
                "identifier": "ctrl.toggle",
                "label": None,
                "traits": [],
                "value": None,
                "frame": [0.0, 0.0],
            }
        }
        golden_path = tmp_path / "bad_frame.json"
        golden_path.write_text(json.dumps(bad))

        with pytest.raises(ValueError, match=r"frame.*4-element"):
            load_golden(golden_path)

    def test_load_rejects_key_identifier_mismatch(self, tmp_path: Path) -> None:
        import pytest

        from bajutsu.evidence.golden import load_golden

        bad = {
            "ctrl.toggle": {
                "identifier": "ctrl.other",
                "label": None,
                "traits": [],
                "value": None,
                "frame": [0.0, 0.0, 100.0, 50.0],
            }
        }
        golden_path = tmp_path / "bad_id.json"
        golden_path.write_text(json.dumps(bad))

        with pytest.raises(ValueError, match=r"does not match"):
            load_golden(golden_path)

    def test_saved_json_is_human_readable(self, tmp_path: Path) -> None:
        from bajutsu.evidence.golden import save_golden

        elements = [_el(identifier="ctrl.toggle", traits=["switch"])]
        golden_path = tmp_path / "controls.json"
        save_golden(elements, ["ctrl.toggle"], golden_path)

        raw = golden_path.read_text()
        assert "\n" in raw  # indented, not single-line


# ---------------------------------------------------------------------------
# assert_golden_tree — full flow (driver → wait → query → compare)
# ---------------------------------------------------------------------------


class TestAssertGoldenTree:
    def test_passes_when_tree_matches_golden(self, tmp_path: Path) -> None:
        from bajutsu.drivers.fake import FakeDriver
        from bajutsu.evidence.golden import assert_golden_tree, save_golden

        screen_elements = [
            _el(identifier="ctrl.title", label="Controls"),
            _el(
                identifier="ctrl.toggle",
                traits=["switch"],
                value="off",
                frame=(10.0, 200.0, 300.0, 44.0),
            ),
            _el(identifier="ctrl.button", traits=["button"], frame=(10.0, 260.0, 300.0, 44.0)),
        ]
        driver = FakeDriver(screen=screen_elements)
        golden_path = tmp_path / "controls.json"
        save_golden(screen_elements, ["ctrl.toggle", "ctrl.button"], golden_path)

        result = assert_golden_tree(
            driver,
            golden_path,
            anchor={"id": "ctrl.title"},
            screen=SCREEN,
        )
        assert result.ok

    def test_reports_mismatches(self, tmp_path: Path) -> None:
        from bajutsu.drivers.fake import FakeDriver
        from bajutsu.evidence.golden import assert_golden_tree, save_golden

        golden_elements = [
            _el(identifier="ctrl.title", label="Controls"),
            _el(
                identifier="ctrl.toggle",
                traits=["switch"],
                value="off",
                frame=(10.0, 200.0, 300.0, 44.0),
            ),
        ]
        golden_path = tmp_path / "controls.json"
        save_golden(golden_elements, ["ctrl.toggle"], golden_path)

        actual_elements = [
            _el(identifier="ctrl.title", label="Controls"),
            _el(
                identifier="ctrl.toggle",
                traits=["button"],
                value="off",
                frame=(10.0, 200.0, 300.0, 44.0),
            ),
        ]
        driver = FakeDriver(screen=actual_elements)

        result = assert_golden_tree(
            driver,
            golden_path,
            anchor={"id": "ctrl.title"},
            screen=SCREEN,
        )
        assert not result.ok
        assert len(result.mismatches) == 1
        assert result.mismatches[0].field == "traits"

    def test_raises_on_anchor_timeout(self, tmp_path: Path) -> None:
        import pytest

        from bajutsu.drivers.fake import FakeDriver
        from bajutsu.evidence.golden import assert_golden_tree, save_golden

        golden_path = tmp_path / "controls.json"
        save_golden([_el(identifier="ctrl.toggle")], ["ctrl.toggle"], golden_path)

        driver = FakeDriver(screen=[])

        with pytest.raises(TimeoutError, match="anchor"):
            assert_golden_tree(
                driver,
                golden_path,
                anchor={"id": "ctrl.title"},
                screen=SCREEN,
                timeout=0.1,
            )
