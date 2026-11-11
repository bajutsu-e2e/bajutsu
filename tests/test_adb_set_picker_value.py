"""`setPickerValue` on the adb backend: the resident server walks a `NumberPicker` (BE-0480).

The host resolves the wheel exactly as it resolves a tap target and sends its identity, never a
coordinate; the device steps the wheel one row at a time and answers how the walk ended. These tests
cover the channel client's reply mapping over real HTTP and `AdbDriver.set_picker_value`'s handling of
each reply, with the channel stubbed — the walk itself is Kotlin and runs only on a device.
"""

from __future__ import annotations

import http.server
import socket
import threading
import urllib.parse
from collections.abc import Callable
from pathlib import Path

import pytest

from bajutsu.common.backend_cli import adb_resident
from bajutsu.common.drivers import base
from bajutsu.common.drivers.adb import (
    ActOutcome,
    ActRequest,
    AdbActUncertain,
    AdbActUnsupported,
    AdbDriver,
    AdbPickerRefused,
    AdbResidentError,
    HierarchyRead,
)

# A form with one `NumberPicker` wheel and a button, the shape the Views showcase picker screen dumps.
_WHEEL = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node index="0" resource-id="com.example:id/form_school" class="android.widget.NumberPicker"
      text="" content-desc="" bounds="[100,200][500,800]">
      <node index="0" resource-id="android:id/numberpicker_input" class="android.widget.EditText"
        text="高校" content-desc="" bounds="[100,400][500,600]" />
    </node>
    <node index="1" resource-id="com.example:id/done" class="android.widget.Button" text="Done"
      clickable="true" bounds="[0,900][200,1000]" />
  </node>
</hierarchy>"""

_SEL: base.Selector = {"id": "form_school", "traits": [base.Trait.PICKER_WHEEL]}
_IDENTITY = ("com.example:id/form_school", "", "", "android.widget.NumberPicker")


def _picker(replies: list[object]) -> tuple[Callable[[ActRequest, str], ActOutcome], list[str]]:
    """A `PickerFn` serving `replies` in order (holding the last), recording each value it was sent.

    A bool is the device's unconfirmed answer (False is the stale reply), an `ActOutcome` states
    the confirmation explicitly, and an `AdbResidentError` instance is raised instead.
    """
    seq = list(replies)
    sent: list[str] = []

    def picker(request: ActRequest, value: str) -> ActOutcome:
        assert request.identity == _IDENTITY
        sent.append(value)
        reply = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(reply, AdbResidentError):
            raise reply
        if isinstance(reply, ActOutcome):
            return reply
        return ActOutcome(acted=bool(reply), published_mark=None)

    return picker, sent


def _driver(picker: Callable[[ActRequest, str], ActOutcome] | None, **kw: object) -> AdbDriver:
    return AdbDriver("U", run=lambda _a: _WHEEL, set_picker_value=picker, **kw)  # type: ignore[arg-type]


# --- AdbDriver.set_picker_value: one test per reply the endpoint can give ---


def test_a_landed_walk_sends_the_wheel_identity_and_records_the_actuation() -> None:
    picker, sent = _picker([True])
    driver = _driver(picker)
    assert base.Capability.PICKER_WHEEL in driver.capabilities()
    driver.set_picker_value(_SEL, "大学")
    assert sent == ["大学"]
    (record,) = driver.drain_actuations().records
    assert (record.gesture, record.via, record.accepted) == ("setPickerValue", "identity", True)
    # The wheel moved on the device, so nothing cached may stand in for the next read.
    assert driver._settled_key is None


def test_a_confirmed_walk_seeds_the_next_read_from_the_returned_tree() -> None:
    outcome = ActOutcome(
        acted=True, published_mark=5000.0, read=HierarchyRead(_WHEEL, 5001.0, root=None)
    )
    picker, _ = _picker([outcome])
    driver = _driver(picker, fetch_clock=lambda: 4000.0)
    driver.set_picker_value(_SEL, "大学")
    assert driver._seeded_tree is not None
    assert driver._catchup is None


def test_a_covered_wheel_fails_without_reaching_the_device() -> None:
    # A later sibling over the wheel's center is drawn on top, as a sheet over the form would be.
    covered = _WHEEL.replace(
        "  </node>\n</hierarchy>",
        '    <node index="2" resource-id="com.example:id/sheet" class="android.widget.FrameLayout"'
        ' bounds="[0,300][1080,700]" />\n  </node>\n</hierarchy>',
    )
    picker, sent = _picker([True])
    driver = AdbDriver("U", run=lambda _a: covered, set_picker_value=picker)
    with pytest.raises(base.ElementNotTappable, match="covered"):
        driver.set_picker_value(_SEL, "大学")
    assert sent == []


def test_a_read_the_device_cannot_address_is_read_again_then_named() -> None:
    picker, sent = _picker([True])
    driver = _driver(picker)
    # Every read comes back unaddressable, as a seeded tree is: no identity recorded for it.
    driver._device_ordinal = lambda *_a: None  # type: ignore[method-assign]
    with pytest.raises(base.ElementNotFound, match="cannot address"):
        driver.set_picker_value(_SEL, "大学")
    assert sent == []


def test_a_stale_reply_re_resolves_and_retries() -> None:
    picker, sent = _picker([False, True])
    _driver(picker).set_picker_value(_SEL, "大学")
    assert sent == ["大学", "大学"]


def test_a_stale_loop_that_never_settles_fails_naming_the_selector() -> None:
    picker, sent = _picker([False])
    with pytest.raises(base.ElementNotFound, match="answered stale 3 times"):
        _driver(picker).set_picker_value(_SEL, "大学")
    assert len(sent) == AdbDriver._STALE_MAX_ATTEMPTS


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("not-a-picker-wheel", "not an android.widget.NumberPicker"),
        ("picker-value-absent", "has no row '大学'"),
        ("picker-walk-exhausted", "ran out of budget"),
        ("picker-immovable", "refused to scroll either way"),
        ("failed", "could not land the wheel"),
    ],
)
def test_each_refusal_fails_with_its_own_message(reason: str, message: str) -> None:
    picker, _ = _picker([AdbPickerRefused(reason, "detail from the device")])
    with pytest.raises(base.ElementNotFound, match=message) as failed:
        _driver(picker).set_picker_value(_SEL, "大学")
    assert "detail from the device" in str(failed.value)


def test_a_server_without_the_endpoint_latches_for_the_lease_and_keeps_taps() -> None:
    picker, sent = _picker([AdbActUnsupported("resident server has no /setPickerValue endpoint")])
    tapped: list[str] = []

    def act(request: ActRequest) -> ActOutcome:
        tapped.append(request.kind)
        return ActOutcome(acted=True, published_mark=None)

    driver = _driver(picker, act=act)
    with pytest.raises(base.UnsupportedAction, match="rebuild"):
        driver.set_picker_value(_SEL, "大学")
    with pytest.raises(base.UnsupportedAction, match="predates"):
        driver.set_picker_value(_SEL, "大学")
    assert len(sent) == 1  # latched: the second step never probed again
    driver.tap({"id": "done"})
    assert tapped == ["tap"]  # `/act` keeps serving taps


def test_a_lost_reply_fails_rather_than_continuing_as_landed() -> None:
    picker, _ = _picker([AdbActUncertain("reply lost")])
    driver = _driver(picker, fetch_clock=lambda: 4000.0)
    with pytest.raises(base.ElementNotFound, match="did not confirm"):
        driver.set_picker_value(_SEL, "大学")
    # The wheel may have moved, so the next read waits for the screen to catch up.
    assert driver._catchup is not None


def test_no_channel_fails_without_dragging_the_wheel() -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        return _WHEEL

    driver = AdbDriver("U", run=run)
    with pytest.raises(base.UnsupportedAction, match="coordinate drag cannot promise"):
        driver.set_picker_value(_SEL, "大学")
    assert not [c for c in calls if "input" in c]


def test_an_unreachable_channel_fails_as_unsupported() -> None:
    picker, _ = _picker([AdbResidentError("resident picker request unreachable")])
    with pytest.raises(base.UnsupportedAction, match="could not be reached"):
        _driver(picker).set_picker_value(_SEL, "大学")


def test_a_channel_lost_mid_lease_fails_as_unsupported() -> None:
    picker, sent = _picker([True])
    driver = _driver(picker)
    driver._act_unavailable = True  # what `_read_source` latches when the read channel dies
    with pytest.raises(base.UnsupportedAction, match="not serving this lease"):
        driver.set_picker_value(_SEL, "大学")
    assert sent == []


# The showcase screen's real shape: a `NumberPicker`'s own node carries no resource-id, so every wheel
# shares one bare identity, and each id sits on a container — one wheel alone, then three in a date
# picker. The device counts peers over the whole tree, so the third date wheel is ordinal 3 of 4.
_SCREEN = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node index="0" resource-id="com.example:id/form_school" class="android.widget.FrameLayout"
      bounds="[0,100][1080,500]">
      <node index="0" class="android.widget.NumberPicker" bounds="[400,100][680,500]" />
    </node>
    <node index="1" resource-id="com.example:id/form_birthdate" class="android.widget.LinearLayout"
      bounds="[0,600][1080,1100]">
      <node index="0" class="android.widget.NumberPicker" bounds="[100,650][360,1050]" />
      <node index="1" class="android.widget.NumberPicker" bounds="[400,650][660,1050]" />
      <node index="2" class="android.widget.NumberPicker" bounds="[700,650][960,1050]" />
    </node>
  </node>
</hierarchy>"""


def test_a_date_wheel_is_sent_by_its_ordinal_over_the_whole_tree_not_the_selector_index() -> None:
    seen: list[ActRequest] = []

    def picker(request: ActRequest, value: str) -> ActOutcome:
        seen.append(request)
        return ActOutcome(acted=True, published_mark=None)

    driver = AdbDriver("U", run=lambda _a: _SCREEN, set_picker_value=picker)
    sel: base.Selector = {
        "within": {"id": "form_birthdate"},
        "traits": [base.Trait.PICKER_WHEEL],
        "index": 2,
    }
    driver.set_picker_value(sel, "2016")
    (request,) = seen
    assert request.identity == ("", "", "", "android.widget.NumberPicker")
    assert (request.index, request.count) == (3, 4)


def test_the_trace_keeps_the_picker_call_but_not_its_value() -> None:
    from bajutsu.common.drivers import tracing

    picker, _ = _picker([True])
    with tracing.open_trace() as ctx:
        driver = _driver(picker)
        driver.set_picker_value(_SEL, "秘密")
    record = next(
        r for r in ctx.records if r.category == "transport" and r.name == "setPickerValue"
    )
    assert "秘密" not in repr(record)
    assert record.response == {"acted": True}


@pytest.mark.parametrize(
    ("reason", "moved"),
    [
        ("not-a-picker-wheel", False),
        ("picker-immovable", False),
        ("picker-value-absent", True),
        ("picker-walk-exhausted", True),
    ],
)
def test_a_refused_walk_drops_the_cached_tree_only_when_the_wheel_may_have_moved(
    reason: str, moved: bool
) -> None:
    picker, _ = _picker([AdbPickerRefused(reason, "detail")])
    driver = _driver(picker, fetch_clock=lambda: 4000.0)
    driver.query()
    driver._settled_key = ()  # a proven rest state from before the step
    with pytest.raises(base.ElementNotFound):
        driver.set_picker_value(_SEL, "大学")
    assert (driver._catchup is not None) is moved
    (record,) = driver.drain_actuations().records
    assert record.accepted is False


# --- the channel client: each reply status over real HTTP ---


class _PickerHandler(http.server.BaseHTTPRequestHandler):
    status = 200
    body = b"ok\n"
    drop_reply = False
    last_path: str | None = None

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/setPickerValue":
            self.send_error(404)
            return
        type(self).last_path = self.path
        if self.drop_reply:
            self.close_connection = True
            self.wfile.close()
            return
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def serve() -> object:
    servers: list[http.server.HTTPServer] = []

    def start(status: int = 200, body: bytes = b"ok\n", *, drop: bool = False) -> int:
        _PickerHandler.status, _PickerHandler.body, _PickerHandler.drop_reply = status, body, drop
        server = http.server.HTTPServer(("127.0.0.1", 0), _PickerHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_port

    yield start
    for server in servers:
        server.shutdown()


def _request(since: float | None = None) -> ActRequest:
    return ActRequest("setPickerValue", _IDENTITY, 0, 1, since=since, duration_ms=None)


def test_the_client_sends_the_identity_and_value_and_no_coordinate(
    serve: Callable[..., int],
) -> None:
    port = serve()
    outcome = adb_resident.set_picker_value(port, _request(since=42.0), "大学")
    assert outcome == ActOutcome(acted=True, published_mark=None)
    sent = urllib.parse.parse_qs(urllib.parse.urlparse(_PickerHandler.last_path or "").query)
    assert sent["rid"] == [_IDENTITY[0]] and sent["cls"] == [_IDENTITY[3]]
    assert sent["value"] == ["大学"] and sent["since"] == ["42.0"]
    assert (sent["index"], sent["count"]) == (["0"], ["1"])
    assert not {"x", "y", "bounds", "kind"} & sent.keys()


def test_the_client_reports_a_stale_wheel_rather_than_raising(serve: Callable[..., int]) -> None:
    assert adb_resident.set_picker_value(serve(409), _request(), "大学").acted is False


@pytest.mark.parametrize(
    "reason",
    ["not-a-picker-wheel", "picker-value-absent", "picker-walk-exhausted", "picker-immovable"],
)
def test_the_client_maps_each_refusal_to_its_reason(serve: Callable[..., int], reason: str) -> None:
    port = serve(422, f"{reason}: what the device saw\n".encode())
    with pytest.raises(AdbPickerRefused) as refused:
        adb_resident.set_picker_value(port, _request(), "大学")
    assert (refused.value.reason, refused.value.detail) == (reason, "what the device saw")


@pytest.mark.parametrize(
    ("status", "body"), [(500, b"the wheel left the screen\n"), (422, b"odd\n")]
)
def test_an_unclassified_failure_is_still_a_refusal(
    serve: Callable[..., int], status: int, body: bytes
) -> None:
    with pytest.raises(AdbPickerRefused) as refused:
        adb_resident.set_picker_value(serve(status, body), _request(), "大学")
    assert refused.value.reason == "failed" and f"HTTP {status}" in refused.value.detail


def test_the_client_names_a_server_without_the_endpoint(serve: Callable[..., int]) -> None:
    with pytest.raises(AdbActUnsupported, match="no /setPickerValue endpoint"):
        adb_resident.set_picker_value(serve(404), _request(), "大学")


def test_the_client_separates_a_lost_reply_from_one_never_sent(serve: Callable[..., int]) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    with pytest.raises(AdbResidentError) as never_sent:
        adb_resident.set_picker_value(closed, _request(), "大学", timeout=0.2)
    assert not isinstance(never_sent.value, (AdbActUncertain, AdbPickerRefused))
    with pytest.raises(AdbActUncertain, match="reply was lost"):
        adb_resident.set_picker_value(serve(drop=True), _request(), "大学")


def test_the_lease_channel_hands_the_wheel_to_the_picker_probe(tmp_path: Path) -> None:
    server_apk, test_apk = tmp_path / "s.apk", tmp_path / "t.apk"
    server_apk.write_bytes(b"s")
    test_apk.write_bytes(b"t")
    seen: list[tuple[int, str]] = []

    def probe(port: int, request: ActRequest, value: str) -> ActOutcome:
        seen.append((port, value))
        return ActOutcome(acted=True, published_mark=None)

    class _Proc:
        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            return 0

    resident = adb_resident.ResidentServer(
        "S",
        run=lambda argv: "4321\n" if "forward" in argv else "",
        spawn=lambda argv: _Proc(),  # type: ignore[arg-type,return-value]
        fetch=lambda port, since: HierarchyRead("<hierarchy/>"),
        picker_probe=probe,
        server_apk=server_apk,
        test_apk=test_apk,
    )
    channel = resident.start()
    assert channel.set_picker_value is not None
    assert channel.set_picker_value(_request(), "大学").acted is True
    assert seen == [(4321, "大学")]
