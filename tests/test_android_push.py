"""The Android `push` route's host side (BE-0498): the payload rules, the APNs mapping, the broadcast,
the result parser, the bounded runner, and the run-time size re-check after substitution."""

from __future__ import annotations

import base64
import subprocess
from typing import Any

import pytest
from pydantic import ValidationError

from bajutsu.common.backend_cli import adb
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.scenario import Push, Scenario, Step, load_scenarios
from bajutsu.common.scenario.models.actions.push import PUSH_PAYLOAD_LIMIT, push_payload_size

SECRET = "s3cr3t-token-value"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode("ascii")


# --- the Push model's payload rules ---


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "needs an `aps` key"),
        ({"aps": "x"}, "`aps` must be an object"),
        ({"aps": {"alert": 5}}, "`aps.alert` must be a string or an object"),
        ({"aps": {"alert": {"title": {"a": 1}}}}, "`aps.alert.title` must be a string"),
        ({"aps": {"alert": {"body": 3}}}, "`aps.alert.body` must be a string"),
    ],
)
def test_push_rejects_a_malformed_payload_at_load(payload: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Push(payload=payload)


def test_push_accepts_every_valid_alert_shape() -> None:
    Push(payload={"aps": {}})
    Push(payload={"aps": {"alert": "hi"}, "k": 1})
    Push(payload={"aps": {"alert": {"title": "t", "body": "b", "subtitle": 9}}})
    Push(payload={"aps": {"content-available": 1}})


def test_push_limit_counts_the_default_json_text() -> None:
    at_limit: dict[str, Any] = {"aps": {}, "x": ""}
    at_limit["x"] = "a" * (PUSH_PAYLOAD_LIMIT - push_payload_size(at_limit))
    assert push_payload_size(at_limit) == PUSH_PAYLOAD_LIMIT
    Push(payload=at_limit)
    with pytest.raises(ValidationError, match=r"is 4,097 bytes; the limit is 4,096"):
        Push(payload={**at_limit, "x": at_limit["x"] + "a"})


def test_push_limit_counts_an_escaped_character_as_its_escape() -> None:
    # `json.dumps` escapes a character above code point 127 to `\uXXXX`: six characters, not one.
    assert push_payload_size({"aps": {}, "x": "é"}) == len('{"aps": {}, "x": "\\u00e9"}')


def test_an_unquoted_date_fails_at_load_naming_its_path_not_its_value() -> None:
    yaml = "- name: x\n  steps:\n    - push: { payload: { aps: { alert: hi }, meta: { at: 2024-01-01 } } }\n"
    with pytest.raises(ValidationError) as info:
        load_scenarios(yaml)
    assert "'meta.at' is not JSON-serializable" in str(info.value)


# --- mapping (every row of the mapping table) ---


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"aps": {"alert": "hello"}}, (None, "hello", {})),
        ({"aps": {"alert": {"title": "T"}}}, ("T", None, {})),
        ({"aps": {"alert": {"body": "B"}}}, (None, "B", {})),
        ({"aps": {"alert": {"title": "T", "body": "B"}}}, ("T", "B", {})),
        ({"aps": {}}, (None, None, {})),
        ({"aps": {"alert": {"subtitle": "S"}}}, (None, None, {})),
        (
            {"aps": {"alert": "a", "badge": 3, "sound": "x"}, "id": "42", "n": 7, "o": {"k": [1]}},
            (None, "a", {"id": "42", "n": "7", "o": '{"k": [1]}'}),
        ),
        (
            {"aps": {}, "ok": True, "none": None, "jp": "日本"},
            (None, None, {"ok": "true", "none": "null", "jp": "日本"}),
        ),
    ],
)
def test_map_push_payload(
    payload: dict[str, Any], expected: tuple[str | None, str | None, dict[str, str]]
) -> None:
    assert adb.map_push_payload(payload) == expected


# --- the broadcast and its result ---


def test_push_broadcast_cmd_base64_encodes_every_field() -> None:
    cmd = adb.push_broadcast_cmd("emulator-5554", "com.demo", "T i", "B'", {"k": "v"})
    assert cmd == [
        "adb",
        "-s",
        "emulator-5554",
        "shell",
        "am",
        "broadcast",
        "-a",
        adb.PUSH_ACTION,
        "-p",
        "com.demo",
        "--es",
        "title",
        _b64("T i"),
        "--es",
        "body",
        _b64("B'"),
        "--es",
        "data",
        _b64('{"k": "v"}'),
    ]


def test_push_broadcast_cmd_omits_an_unset_or_empty_title_and_body() -> None:
    cmd = adb.push_broadcast_cmd("emulator-5554", "com.demo", None, "", {})
    assert "title" not in cmd
    assert "body" not in cmd
    assert cmd[-3:] == ["--es", "data", _b64("{}")]


def test_parse_push_result_accepts_ok() -> None:
    adb.parse_push_result("Broadcasting: Intent { … }\nBroadcast completed: result=1\n")


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        ("Broadcast completed: result=4", "notifications: grant"),
        ("Broadcast completed: result=0", "Bajutsu.startPush"),
        ("Broadcast completed: result=7", "no BajutsuAndroid push receiver"),
        ("", "not running"),
    ],
)
def test_parse_push_result_names_the_cause(reply: str, message: str) -> None:
    out = f"Broadcasting: Intent {{ act=dev.bajutsu.PUSH (has extras) }}\n{reply}\n"
    with pytest.raises(adb.DeviceError, match=message) as info:
        adb.parse_push_result(out)
    assert "has extras" not in str(info.value)  # the am reply, which echoes the intent, stays out


def test_parse_push_result_passes_a_handler_message_through_verbatim() -> None:
    out = f'Broadcast completed: result=5, data="{_b64("model not ready")}"'
    with pytest.raises(adb.DeviceError, match=r"push handler threw: model not ready$"):
        adb.parse_push_result(out)


def test_parse_push_result_survives_a_handler_failure_without_data() -> None:
    with pytest.raises(adb.DeviceError, match=r"\(no message\)"):
        adb.parse_push_result("Broadcast completed: result=5")


# --- the bounded runner ---


def _argv() -> list[str]:
    return adb.push_broadcast_cmd("emulator-5554", "com.demo", SECRET, None, {})


@pytest.mark.parametrize(
    ("raised", "message"),
    [
        (subprocess.TimeoutExpired(cmd=["adb"], timeout=10), "did not answer within 10s"),
        (
            subprocess.CalledProcessError(1, ["adb"], stderr="error: device offline\n"),
            r"adb failed \(exit 1\)\nerror: device offline$",
        ),
        (subprocess.CalledProcessError(1, ["adb"], stderr=""), r"adb failed \(exit 1\)$"),
        (FileNotFoundError(2, "No such file or directory"), "could not run adb: No such file"),
    ],
)
def test_bounded_push_run_raises_without_the_argv(
    monkeypatch: pytest.MonkeyPatch, raised: Exception, message: str
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        raise raised

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(adb.DeviceError, match=message) as info:
        adb.bounded_push_run(_argv())
    assert seen["timeout"] == adb.PUSH_BROADCAST_TIMEOUT_S
    text = str(info.value)
    assert _b64(SECRET) not in text
    assert "am broadcast" not in text
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_bounded_push_run_returns_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="result=1", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert adb.bounded_push_run(_argv()) == "result=1"


# --- Env.push ---


def test_env_push_maps_and_sends_through_a_faked_run() -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> str:
        calls.append(args)
        return "Broadcast completed: result=1"

    adb.Env("emulator-5554", run=fake_run).push(
        "com.demo", {"aps": {"alert": {"title": "T", "body": "B"}}, "id": 1}
    )
    assert calls == [
        adb.push_broadcast_cmd("emulator-5554", "com.demo", "T", "B", {"id": "1"}),
    ]


def test_env_push_takes_the_bounded_runner_under_real_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="result=1", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adb.Env("emulator-5554").push("com.demo", {"aps": {}})
    assert seen["timeout"] == adb.PUSH_BROADCAST_TIMEOUT_S


def test_env_push_prefers_an_explicit_push_run() -> None:
    calls: list[list[str]] = []

    def push_run(args: list[str]) -> str:
        calls.append(args)
        return "result=1"

    def run(args: list[str]) -> str:
        raise AssertionError("push must not go through run")

    adb.Env("emulator-5554", run=run, push_run=push_run).push("com.demo", {"aps": {}})
    assert len(calls) == 1


def _replying(reply: str) -> adb.RunFn:
    return lambda args: reply


def _raising(exc: Exception) -> adb.RunFn:
    def run(args: list[str]) -> str:
        raise exc

    return run


@pytest.mark.parametrize(
    ("push_run", "message"),
    [
        (_replying("Broadcast completed: result=0"), "no BajutsuAndroid push receiver"),
        (_replying("Broadcast completed: result=4"), "notifications: grant"),
        (
            _replying(f'Broadcast completed: result=5, data="{_b64("handler broke")}"'),
            "push handler threw: handler broke",
        ),
        (_raising(adb.DeviceError("push: the receiver did not answer")), "did not answer"),
        (_raising(adb.DeviceError("push: adb failed (exit 1)")), "exit 1"),
    ],
)
def test_env_push_raises_device_error_without_payload_text(
    push_run: adb.RunFn, message: str
) -> None:
    def run(args: list[str]) -> str:
        raise AssertionError("push must go through push_run")

    with pytest.raises(adb.DeviceError, match=message) as info:
        adb.Env("emulator-5554", run=run, push_run=push_run).push(
            "com.demo", {"aps": {"alert": SECRET}}
        )
    assert SECRET not in str(info.value)
    assert _b64(SECRET) not in str(info.value)


@pytest.mark.parametrize(
    "raised",
    [
        subprocess.TimeoutExpired(cmd=["adb"], timeout=10),
        # adb echoing an argv value on stderr must not carry the payload into the message.
        subprocess.CalledProcessError(1, ["adb"], stderr=f"error: bad extra {_b64(SECRET)}"),
    ],
)
def test_env_push_under_real_run_raises_without_payload_text(
    monkeypatch: pytest.MonkeyPatch, raised: Exception
) -> None:
    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise raised

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(adb.DeviceError) as info:
        adb.Env("emulator-5554").push("com.demo", {"aps": {"alert": SECRET}})
    assert SECRET not in str(info.value)
    assert _b64(SECRET) not in str(info.value)


# --- the run-time re-check after substitution ---


class _PushControl:
    def __init__(self) -> None:
        self.pushes: list[dict[str, object]] = []

    def push(self, payload: dict[str, object]) -> None:
        self.pushes.append(payload)


@pytest.mark.parametrize("token", ["vars.big", "secrets.BIG"])
def test_a_substituted_payload_past_the_limit_fails_its_step_not_the_run(token: str) -> None:
    big = SECRET + "x" * PUSH_PAYLOAD_LIMIT
    ctrl = _PushControl()
    scn = Scenario(
        name="s",
        steps=[Step(push=Push(payload={"aps": {"alert": f"${{{token}}}"}}))],
    )
    result = run_scenario(FakeDriver(), scn, control=ctrl, bindings={token: big})  # type: ignore[arg-type]
    assert not result.ok
    assert ctrl.pushes == []
    failure = result.failure or ""
    assert "the limit is 4,096" in failure
    assert SECRET not in failure
    assert SECRET not in repr(result)  # no step outcome or artifact carries it either


def test_a_substituted_payload_within_the_limit_is_delivered_substituted() -> None:
    ctrl = _PushControl()
    scn = Scenario(name="s", steps=[Step(push=Push(payload={"aps": {"alert": "${vars.msg}"}}))])
    result = run_scenario(FakeDriver(), scn, control=ctrl, bindings={"vars.msg": "hello"})  # type: ignore[arg-type]
    assert result.ok
    assert ctrl.pushes == [{"aps": {"alert": "hello"}}]
