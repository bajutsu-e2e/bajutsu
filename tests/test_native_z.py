"""`nativeZ`, the app-measured front-to-back position on `Element` (BE-0355).

The field is diagnostic only: no selector matches on it and no occlusion check reads it. A backend
reports a number exactly where the app under test measured one for that element, through the iOS
responder or the Android extra-data helper, and `None` everywhere else. These tests pin all of it:
the honest absence a backend with no cooperating app owes its reader, the two reporting paths'
matching rules, and that nothing which already decided occlusion now consults the field.
"""

from __future__ import annotations

import http.client
import io
import json
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from bajutsu.adb_resident import _parse_native_z
from bajutsu.common.evidence.golden import compare_element, load_golden, save_golden
from bajutsu.dom import parse_dom
from bajutsu.drivers import base
from bajutsu.drivers.adb import parse_hierarchy, parse_hierarchy_with_identities
from bajutsu.drivers.fake import FakeDriver
from bajutsu.drivers.xcuitest import XcuitestDriver, _Reply
from bajutsu.zorder import ZOrderResponder

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


# ---------------------------------------------------------------------------
# The iOS reporting path (BE-0355 Unit 2)
# ---------------------------------------------------------------------------


class _FakeZOrder:
    """A responder that answers whatever a test seeds, counting how often it was asked."""

    def __init__(self, positions: dict[str, float]) -> None:
        self._positions = positions
        self.calls = 0

    def positions(self) -> dict[str, float]:
        self.calls += 1
        return self._positions


def _xcuitest_driver(zorder: _FakeZOrder | None, items: list[dict[str, Any]]) -> XcuitestDriver:
    def transport(method: str, path: str, body: Any) -> Any:
        return _Reply(status="ok", elements=items, raw=b"")

    return XcuitestDriver(transport=transport, zorder=zorder)


def _runner_item(identifier: str, handle: str) -> dict[str, Any]:
    return {
        "handle": handle,
        "identifier": identifier,
        "label": None,
        "value": None,
        "traits": [],
        "frame": [0, 0, 10, 10],
    }


def test_xcuitest_carries_a_measured_position_onto_its_element() -> None:
    driver = _xcuitest_driver(
        _FakeZOrder({"a": 3.0}), [_runner_item("a", "h1"), _runner_item("b", "h2")]
    )
    by_id = {el["identifier"]: el["nativeZ"] for el in driver.query()}
    assert by_id == {"a": 3.0, "b": None}


def test_xcuitest_reports_no_position_without_a_responder() -> None:
    driver = _xcuitest_driver(None, [_runner_item("a", "h1")])
    assert driver.query()[0]["nativeZ"] is None


def test_xcuitest_leaves_a_repeated_identifier_unmeasured() -> None:
    # Two elements answering to one identifier: whichever position the app reported belongs to one
    # of them, and nothing here can say which — so neither takes it.
    driver = _xcuitest_driver(
        _FakeZOrder({"dup": 5.0}), [_runner_item("dup", "h1"), _runner_item("dup", "h2")]
    )
    assert [el["nativeZ"] for el in driver.query()] == [None, None]


def test_zorder_responder_drops_an_identifier_the_app_repeated() -> None:
    payload = {
        "elements": [
            {"identifier": "a", "nativeZ": 1.0},
            {"identifier": "b", "nativeZ": 2.0},
            {"identifier": "b", "nativeZ": 9.0},
            {"identifier": "c", "nativeZ": "not a number"},
            {"identifier": None, "nativeZ": 4.0},
            "not a record",
        ]
    }
    assert _responder_reading(payload) == {"a": 1.0}


def test_zorder_responder_stops_asking_after_the_first_refusal() -> None:
    responder = ZOrderResponder(port=1, token="t")
    calls = [0]

    def refuse(*_args: object, **_kwargs: object) -> None:
        calls[0] += 1
        raise urllib.error.URLError("connection refused")

    with mock.patch("urllib.request.urlopen", refuse):
        assert responder.positions() == {}
        assert responder.positions() == {}
    assert calls[0] == 1


def test_zorder_responder_keeps_asking_after_a_connect_phase_timeout() -> None:
    # urllib's do_open wraps a connect-phase OSError timeout in URLError — only a read-phase
    # timeout raises the bare TimeoutError the other clause catches. A connect that just missed
    # the budget (the app still coming up) is a hiccup, not a permanently absent responder.
    responder = ZOrderResponder(port=1, token="t")
    calls = [0]

    def connect_timeout(*_args: object, **_kwargs: object) -> None:
        calls[0] += 1
        raise urllib.error.URLError(TimeoutError("timed out"))

    with mock.patch("urllib.request.urlopen", connect_timeout):
        assert responder.positions() == {}
        assert responder.positions() == {}
    assert calls[0] == 2


def test_zorder_responder_keeps_asking_after_a_busy_main_thread() -> None:
    # A 503 means a responder that demonstrably exists just missed this one request (the app's
    # main thread was busy) — unlike a refused connection, that is not the permanent "no
    # responder" answer the negative cache exists for.
    responder = ZOrderResponder(port=1, token="t")
    calls = [0]

    def busy(*_args: object, **_kwargs: object) -> None:
        calls[0] += 1
        raise urllib.error.HTTPError("url", 503, "busy", Message(), None)

    with mock.patch("urllib.request.urlopen", busy):
        assert responder.positions() == {}
        assert responder.positions() == {}
    assert calls[0] == 2


def test_zorder_responder_latches_on_a_permanent_http_error() -> None:
    # 401 (wrong token) and 404 (no such route) both mean this responder will never answer, the
    # same as a refused connection — so this latches too.
    responder = ZOrderResponder(port=1, token="t")
    calls = [0]

    def unauthorized(*_args: object, **_kwargs: object) -> None:
        calls[0] += 1
        raise urllib.error.HTTPError("url", 401, "unauthorized", Message(), None)

    with mock.patch("urllib.request.urlopen", unauthorized):
        assert responder.positions() == {}
        assert responder.positions() == {}
    assert calls[0] == 1


def test_zorder_responder_reports_nothing_for_a_non_object_reply() -> None:
    # Loopback is not isolated between apps, so the reply need not be bajutsu's own responder —
    # a malformed top-level shape must degrade to the same absence, not raise into the caller's
    # own element query.
    for payload in ([1, 2, 3], "not an object", None, {"elements": "not a list"}):
        assert _responder_reading(payload) == {}


def test_zorder_responder_keeps_asking_after_a_truncated_reply() -> None:
    # http.client.HTTPException (a malformed status line, a body shorter than its own
    # Content-Length) is not an OSError, so it needs its own clause — and a truncated reply can
    # come from the app terminating mid-write at scenario end, not only from "no responder ever",
    # so it must not latch either.
    responder = ZOrderResponder(port=1, token="t")
    calls = [0]

    def truncated(*_args: object, **_kwargs: object) -> None:
        calls[0] += 1
        raise http.client.IncompleteRead(b"")

    with mock.patch("urllib.request.urlopen", truncated):
        assert responder.positions() == {}
        assert responder.positions() == {}
    assert calls[0] == 2


def test_zorder_responder_keeps_asking_after_a_non_utf8_body() -> None:
    # json.loads raises UnicodeDecodeError, not JSONDecodeError, when the body isn't even valid
    # UTF-8 — a ValueError subclass but not the specific one the JSON-malformed clause names.
    responder = ZOrderResponder(port=1, token="t")
    body = io.BytesIO(b"\x80\x81not utf-8")
    with mock.patch("urllib.request.urlopen", mock.MagicMock(return_value=_ctx(body))):
        assert responder.positions() == {}
        assert responder.positions() == {}


def _responder_reading(payload: object) -> dict[str, float]:
    responder = ZOrderResponder(port=1, token="t")
    body = io.BytesIO(json.dumps(payload).encode())
    with mock.patch("urllib.request.urlopen", mock.MagicMock(return_value=_ctx(body))):
        return responder.positions()


def _ctx(body: io.BytesIO) -> Any:
    ctx = mock.MagicMock()
    ctx.__enter__.return_value = body
    ctx.__exit__.return_value = False
    return ctx


# ---------------------------------------------------------------------------
# The Android reporting path (BE-0355 Unit 3)
# ---------------------------------------------------------------------------

_TWO_ROWS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hierarchy rotation="0">'
    '<node resource-id="com.example:id/a" class="android.widget.Button" text="A"'
    ' package="com.example" content-desc="" bounds="[0,0][100,50]" />'
    '<node resource-id="com.example:id/b" class="android.widget.Button" text="B"'
    ' package="com.example" content-desc="" bounds="[0,50][100,100]" />'
    "</hierarchy>"
)


def test_adb_matches_a_measured_position_onto_the_node_it_names() -> None:
    native_z = {"0,50,100,100|android.widget.Button|com.example|0": 8.0}
    els, _ = parse_hierarchy_with_identities(_TWO_ROWS, native_z)
    assert [el["nativeZ"] for el in els] == [None, 8.0]


def test_adb_tells_two_identical_rows_apart_by_their_occurrence() -> None:
    # The four accessibility fields `_identity` uses are deliberately not unique, so the key counts
    # how many nodes agreeing on bounds, class, and package came first — the same count the device
    # made walking the same tree.
    twins = (
        '<hierarchy rotation="0">'
        '<node class="android.widget.TextView" package="com.example" bounds="[0,0][10,10]" />'
        '<node class="android.widget.TextView" package="com.example" bounds="[0,0][10,10]" />'
        "</hierarchy>"
    )
    key = "0,0,10,10|android.widget.TextView|com.example"
    els, _ = parse_hierarchy_with_identities(twins, {f"{key}|1": 4.0})
    assert [el["nativeZ"] for el in els] == [None, 4.0]


def test_adb_reports_no_position_when_the_device_measured_none() -> None:
    els, _ = parse_hierarchy_with_identities(_TWO_ROWS, {})
    assert [el["nativeZ"] for el in els] == [None, None]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, {}),
        ("", {}),
        ("0,0,1,1|C|p|0=2.5", {"0,0,1,1|C|p|0": 2.5}),
        ("0,0,1,1|C|p|0=2.5;0,1,1,2|C|p|0=0.0", {"0,0,1,1|C|p|0": 2.5, "0,1,1,2|C|p|0": 0.0}),
        ("garbled", {}),
        ("0,0,1,1|C|p|0=NaN", {}),
        ("0,0,1,1|C|p|0=Infinity", {}),
        ("=3.0", {}),
    ],
)
def test_native_z_header_degrades_a_malformed_reading(
    header: str | None, expected: dict[str, float]
) -> None:
    assert _parse_native_z(header) == expected
