"""Resident UI Automator server channel (BE-0245): reach the on-device server over adb forward + HTTP.

The resident server (BajutsuAndroidUIAutomatorServer, PR-B) keeps one `UiAutomation` session warm and
answers `GET /source` with `UiDevice.dumpWindowHierarchy` XML, skipping the ≈ 2.4 s per-invocation
`uiautomator dump` startup. This module is the Python end of that channel: it starts the server for a
device lease, forwards a host port to it, fetches the hierarchy, and narrows the whole-screen dump to
the active window so `parse_hierarchy` produces the same Elements the dump path does. Everything above
`AdbDriver._describe()` — the transient-empty retry, `_settle`, selectors — is unchanged; only the
transport differs. A startup or channel failure raises `AdbResidentError`, which the driver catches to
fall back to `uiautomator dump` rather than reading a failed channel as an empty screen.
"""

from ._functions import _ACT_PUBLISH_HEADER as _ACT_PUBLISH_HEADER
from ._functions import _NATIVE_Z_HEADER as _NATIVE_Z_HEADER
from ._functions import _NO_ENDPOINT_STATUS as _NO_ENDPOINT_STATUS
from ._functions import _READ_MARK_HEADER as _READ_MARK_HEADER
from ._functions import _STALE_STATUS as _STALE_STATUS
from ._functions import _SYSTEM_DECOR_PACKAGES as _SYSTEM_DECOR_PACKAGES
from ._functions import _act_read as _act_read
from ._functions import _apk_digest as _apk_digest
from ._functions import _channel as _channel
from ._functions import _default_spawn as _default_spawn
from ._functions import _is_stale as _is_stale
from ._functions import _parse_forward_port as _parse_forward_port
from ._functions import _parse_mark as _parse_mark
from ._functions import _parse_native_z as _parse_native_z
from ._functions import _warn_once as _warn_once
from ._functions import _warned as _warned
from ._functions import (
    act,
    fetch_clock,
    fetch_source,
    gesture,
    narrowed_root,
    server_apks_built,
    set_picker_value,
    type_text,
)
from ._process import _Process as _Process
from ._shared import _REPO_ROOT as _REPO_ROOT
from ._shared import _SERVER_APK as _SERVER_APK
from ._shared import _TEST_APK as _TEST_APK
from ._shared import logger
from .keepalive import _KEEPALIVE_IDLE_RECONNECT_S as _KEEPALIVE_IDLE_RECONNECT_S
from .keepalive import Keepalive
from .resident_channel import ResidentChannel
from .resident_server import (
    ActProbe,
    ClockProbe,
    Fetch,
    GestureProbe,
    PickerProbe,
    ResidentServer,
    Spawn,
    TypeProbe,
)

__all__ = [
    "ActProbe",
    "ClockProbe",
    "Fetch",
    "GestureProbe",
    "Keepalive",
    "PickerProbe",
    "ResidentChannel",
    "ResidentServer",
    "Spawn",
    "TypeProbe",
    "act",
    "fetch_clock",
    "fetch_source",
    "gesture",
    "logger",
    "narrowed_root",
    "server_apks_built",
    "set_picker_value",
    "type_text",
]
