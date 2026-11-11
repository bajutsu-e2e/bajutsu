"""Find an Android app's WebView DevTools sockets and forward them to the host (BE-0482).

Every debuggable Android WebView listens on the abstract socket `webview_devtools_remote_<pid>`.
The pid changes whenever the app relaunches, so the source resolves the sockets once per `web:` block
rather than once per run. Discovery needs only the package name — no per-app process name — so the
tool stays app-agnostic. Parsers are pure; the adb calls go through the injectable runner.
"""

from __future__ import annotations

import logging
import re
import subprocess

from bajutsu.common.backend_cli import adb

from .cdp import WebViewTransportError

_logger = logging.getLogger(__name__)

_SOCKET_PREFIX = "webview_devtools_remote_"
# The last column of a `/proc/net/unix` row is the path; an abstract socket's begins with `@`.
_SOCKET_RE = re.compile(rf"@{_SOCKET_PREFIX}(\d+)$")

# The fix the missing-socket reason names, so the author reads the remedy rather than a bare symptom.
DEBUGGING_HINT = (
    "the app has not enabled WebView debugging; call `Bajutsu.enableWebViewDebugging()` from "
    "`Application.onCreate` in a debug or test build"
)


def package_pids(ps_output: str, package: str) -> set[int]:
    """The pids of `package`'s processes in `ps -A -o PID,NAME` output.

    Keeps the main process and every `<package>:<name>` child: a hybrid app may host its WebView in
    a separate `android:process`, which `pidof <package>` would miss.
    """
    pids: set[int] = set()
    for line in ps_output.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue  # the header row, or a blank line
        name = parts[1].strip()
        if name == package or name.startswith(f"{package}:"):
            pids.add(int(parts[0]))
    return pids


def devtools_sockets(proc_net_unix: str, pids: set[int]) -> list[str]:
    """The WebView DevTools socket names in `/proc/net/unix` whose pid is one of `pids`.

    Sorted and de-duplicated: a listening socket and each of its accepted connections share a path.
    """
    names: set[str] = set()
    for line in proc_net_unix.splitlines():
        m = _SOCKET_RE.search(line.strip())
        if m is not None and int(m.group(1)) in pids:
            names.add(f"{_SOCKET_PREFIX}{m.group(1)}")
    return sorted(names)


def _run(run: adb.RunFn, argv: list[str]) -> str:
    try:
        return run(argv)
    except subprocess.CalledProcessError as e:
        raise WebViewTransportError(f"`{' '.join(argv)}` failed: {e.stderr or e}") from e
    except subprocess.SubprocessError as e:  # a timeout, from a runner that bounds one
        raise WebViewTransportError(f"`{' '.join(argv)}` failed: {e}") from e
    except OSError as e:
        raise WebViewTransportError(f"could not run adb ({e})") from e


def find_sockets(run: adb.RunFn, serial: str, package: str) -> list[str]:
    """The DevTools sockets the running `package` serves, or a reason that names the cause.

    Raises:
        WebViewTransportError: adb failed, the package has no running process, or none of its
            processes serves a socket (WebView debugging is off).
    """
    pids = package_pids(_run(run, adb.ps_names_cmd(serial)), package)
    if not pids:
        raise WebViewTransportError(f"no running process for {package}; the WebView is unreachable")
    sockets = devtools_sockets(_run(run, adb.proc_net_unix_cmd(serial)), pids)
    if not sockets:
        raise WebViewTransportError(f"no WebView DevTools socket in {package}: {DEBUGGING_HINT}")
    return sockets


def forward_socket(run: adb.RunFn, serial: str, socket_name: str) -> int:
    """Forward a free host port to the abstract `socket_name`; the host port adb picked.

    Raises:
        WebViewTransportError: adb failed, or printed no port.
    """
    out = _run(run, adb.forward_abstract_cmd(serial, socket_name)).strip()
    if not out.isdigit():
        raise WebViewTransportError(f"adb forward printed no host port for {socket_name}: {out!r}")
    return int(out)


def remove_forward(run: adb.RunFn, serial: str, host_port: int) -> None:
    """Remove the forward on `host_port`, best effort: a gone device must not mask the block's result."""
    try:
        run(adb.forward_remove_cmd(serial, host_port))
    except (subprocess.SubprocessError, OSError) as e:
        _logger.warning("adb forward --remove failed for port %d: %s", host_port, e)
