"""Finding and forwarding an Android app's WebView DevTools sockets (BE-0482); no device."""

from __future__ import annotations

import subprocess

import pytest

from bajutsu.common.backend_cli import adb
from bajutsu.common.drivers.webview import WebViewTransportError
from bajutsu.common.drivers.webview import cdp_discovery as d

_PS = """\
  PID NAME
    1 init
 4242 com.example.app
 4300 com.example.app:webview
 4400 com.example.appendix
 4500 com.other
"""

# A listening socket and its accepted connection share a path; other apps' sockets and plain paths mix in.
_UNIX = """\
Num       RefCount Protocol Flags    Type St Inode Path
0000000000000000: 00000002 00000000 00010000 0001 01 101 @webview_devtools_remote_4300
0000000000000000: 00000003 00000000 00000000 0001 03 102 @webview_devtools_remote_4300
0000000000000000: 00000002 00000000 00010000 0001 01 103 @webview_devtools_remote_4500
0000000000000000: 00000002 00000000 00010000 0001 01 104 /dev/socket/zygote
0000000000000000: 00000002 00000000 00010000 0001 01 105 @chrome_devtools_remote
"""


def test_command_lines() -> None:
    assert adb.ps_names_cmd("emulator-5554") == [
        "adb",
        "-s",
        "emulator-5554",
        "shell",
        "ps",
        "-A",
        "-o",
        "PID,NAME",
    ]
    assert adb.proc_net_unix_cmd("emulator-5554") == [
        "adb",
        "-s",
        "emulator-5554",
        "shell",
        "cat",
        "/proc/net/unix",
    ]
    assert adb.forward_abstract_cmd("emulator-5554", "webview_devtools_remote_1") == [
        "adb",
        "-s",
        "emulator-5554",
        "forward",
        "tcp:0",
        "localabstract:webview_devtools_remote_1",
    ]


def test_package_pids_keeps_the_main_process_and_named_children_only() -> None:
    # `com.example.appendix` shares the prefix but is another package.
    assert d.package_pids(_PS, "com.example.app") == {4242, 4300}


def test_devtools_sockets_keeps_the_packages_sockets_once_each() -> None:
    assert d.devtools_sockets(_UNIX, {4242, 4300}) == ["webview_devtools_remote_4300"]


def _runner(outputs: dict[str, str]) -> tuple[list[list[str]], adb.RunFn]:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        for key, out in outputs.items():
            if key in argv:
                return out
        return ""

    return calls, run


def test_find_sockets_finds_a_webview_in_a_child_process() -> None:
    _, run = _runner({"ps": _PS, "/proc/net/unix": _UNIX})
    assert d.find_sockets(run, "emulator-5554", "com.example.app") == [
        "webview_devtools_remote_4300"
    ]


def test_find_sockets_names_debugging_as_the_cause_when_no_socket_is_served() -> None:
    _, run = _runner({"ps": _PS, "/proc/net/unix": ""})
    with pytest.raises(WebViewTransportError, match=r"enableWebViewDebugging"):
        d.find_sockets(run, "emulator-5554", "com.example.app")


def test_find_sockets_reports_an_app_that_is_not_running() -> None:
    _, run = _runner({"ps": "  PID NAME\n"})
    with pytest.raises(WebViewTransportError, match="no running process"):
        d.find_sockets(run, "emulator-5554", "com.example.app")


def test_adb_failures_become_transport_errors() -> None:
    def failing(argv: list[str]) -> str:
        raise subprocess.CalledProcessError(1, argv, stderr="device offline")

    def missing(_argv: list[str]) -> str:
        raise FileNotFoundError("adb")

    with pytest.raises(WebViewTransportError, match="device offline"):
        d.find_sockets(failing, "emulator-5554", "com.example.app")
    with pytest.raises(WebViewTransportError, match="could not run adb"):
        d.find_sockets(missing, "emulator-5554", "com.example.app")


def test_forward_socket_returns_the_port_adb_picked() -> None:
    calls, run = _runner({"forward": "40123\n"})
    assert d.forward_socket(run, "emulator-5554", "webview_devtools_remote_4300") == 40123
    assert calls[0][-1] == "localabstract:webview_devtools_remote_4300"
    _, silent = _runner({})
    with pytest.raises(WebViewTransportError, match="no host port"):
        d.forward_socket(silent, "emulator-5554", "webview_devtools_remote_4300")


def test_remove_forward_is_best_effort(caplog: pytest.LogCaptureFixture) -> None:
    calls, run = _runner({})
    d.remove_forward(run, "emulator-5554", 40123)
    assert calls == [adb.forward_remove_cmd("emulator-5554", 40123)]

    def gone(argv: list[str]) -> str:
        raise subprocess.CalledProcessError(1, argv)

    d.remove_forward(gone, "emulator-5554", 40123)
    assert "forward --remove failed" in caplog.text


def test_a_timed_out_adb_call_is_a_transport_error_and_a_quiet_teardown() -> None:
    def hung(argv: list[str]) -> str:
        raise subprocess.TimeoutExpired(argv, 5)

    with pytest.raises(WebViewTransportError, match="failed"):
        d.find_sockets(hung, "emulator-5554", "com.example.app")
    d.remove_forward(hung, "emulator-5554", 40123)  # must not escape a block's `finally`
