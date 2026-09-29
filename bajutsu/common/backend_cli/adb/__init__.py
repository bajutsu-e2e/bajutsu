"""adb wrapper — clean-state / launch / deeplink / input / screencap / device list.

The Android environment ([BE-0007]) is the twin of the iOS `simctl` sequence: a clean state is
`pm clear <package>` (the `erase` equivalent), launch is `am start`, and a deeplink is an
`am start -a android.intent.action.VIEW`. Command builders are pure and unit-tested; execution
goes through an injectable runner so the device-touching part stays thin and swappable in tests —
the same shape as `simctl.py`.

adb carries everything an operation needs in its argv (intent extras included), so the runner is
the plain ``argv -> stdout`` form, not simctl's ``(argv, env)`` — no launch env
is forwarded through the parent process.
"""

from ._functions import _ABS_MT_POSITION_X as _ABS_MT_POSITION_X
from ._functions import _ABS_MT_POSITION_Y as _ABS_MT_POSITION_Y
from ._functions import _ABS_MT_PRESSURE as _ABS_MT_PRESSURE
from ._functions import _ABS_MT_SLOT as _ABS_MT_SLOT
from ._functions import _ABS_MT_TRACKING_ID as _ABS_MT_TRACKING_ID
from ._functions import _ADD_DEVICE as _ADD_DEVICE
from ._functions import _AXIS_MAX as _AXIS_MAX
from ._functions import _BTN_TOUCH as _BTN_TOUCH
from ._functions import _EV_ABS as _EV_ABS
from ._functions import _EV_KEY as _EV_KEY
from ._functions import _EV_SYN as _EV_SYN
from ._functions import _EVENT_INDEX as _EVENT_INDEX
from ._functions import _GESTURE_STEPS as _GESTURE_STEPS
from ._functions import _GESTURE_TRACKING_IDS as _GESTURE_TRACKING_IDS
from ._functions import _MT_TRACKING_ID_LIFT as _MT_TRACKING_ID_LIFT
from ._functions import _RESULT_CODE_RE as _RESULT_CODE_RE
from ._functions import _RESULT_DATA_RE as _RESULT_DATA_RE
from ._functions import _SYN_REPORT as _SYN_REPORT
from ._functions import _TAP_TRACKING_IDS as _TAP_TRACKING_IDS
from ._functions import _TOUCH_PRESSURE as _TOUCH_PRESSURE
from ._functions import (
    CLIPBOARD_ACTION,
    CLIPBOARD_RESULT_OK,
    EXIT_INFO_CRASH_REASONS,
    RESIDENT_DEVICE_PORT,
    RESIDENT_INSTRUMENTATION,
    RESIDENT_TEST_METHOD,
    VIDEO_DEVICE_PATH,
    booted_serials,
    cat_cmd,
    checked_serial,
    clear_primary_clip_cmd,
    deeplink_cmd,
    device_catalog,
    device_error,
    devices_cmd,
    double_tap_cmd,
    dump_cmd,
    dumpsys_surfaceflinger_latency_cmd,
    exit_info_cmd,
    extract_crash_block,
    file_size_cmd,
    force_stop_cmd,
    forward_cmd,
    forward_remove_cmd,
    geo_fix_cmd,
    get_primary_clip_cmd,
    get_prop_cmd,
    getevent_probe_cmd,
    id_u_cmd,
    install_cmd,
    instrument_cmd,
    keycombination_cmd,
    keyevent_cmd,
    keyevents_cmd,
    launch_cmd,
    launch_marker_cmd,
    logcat_cmd,
    logcat_crash_dump_cmd,
    logcat_tail_cmd,
    newest_exit_info,
    package_path_cmd,
    parse_clipboard_result,
    parse_file_size,
    parse_touch_device,
    pidof_cmd,
    pinch_contacts,
    pm_clear_cmd,
    pm_grant_cmd,
    pm_revoke_cmd,
    pull_cmd,
    real_run,
    resolve_activity_cmd,
    resolve_serial,
    reverse_cmd,
    reverse_remove_cmd,
    rm_cmd,
    root_cmd,
    rotate_contacts,
    scale_to_touch,
    screencap_cmd,
    screenrecord_cmd,
    screenrecord_pids_cmd,
    sendevent_double_tap_cmd,
    sendevent_gesture_cmd,
    set_primary_clip_cmd,
    shell_cmd,
    swipe_cmd,
    tap_cmd,
    text_script,
    tombstones_cmd,
    uninstall_cmd,
    unroot_cmd,
    wait_for_device_cmd,
    wm_size_cmd,
)
from ._functions import _adb as _adb
from ._functions import _b64 as _b64
from ._functions import _clamp as _clamp
from ._functions import _clipboard_broadcast_cmd as _clipboard_broadcast_cmd
from ._functions import _contact_down as _contact_down
from ._functions import _contact_move as _contact_move
from ._functions import _Contacts as _Contacts
from ._functions import _event_index as _event_index
from ._functions import _num as _num
from ._functions import _parse_devices as _parse_devices
from ._functions import _Point as _Point
from ._functions import _rotate_point as _rotate_point
from ._functions import _tap_events as _tap_events
from ._shared import (
    KEYCODE_A,
    KEYCODE_BACK,
    KEYCODE_C,
    KEYCODE_CTRL_LEFT,
    KEYCODE_DEL,
    RESIDENT_SERVER_PACKAGE,
    RESIDENT_TEST_PACKAGE,
    RunFn,
)
from .device_error import DeviceError
from .env import SERVICE_TO_ANDROID_PERMISSIONS, Env
from .touch_device import TouchDevice

__all__ = [
    "CLIPBOARD_ACTION",
    "CLIPBOARD_RESULT_OK",
    "EXIT_INFO_CRASH_REASONS",
    "KEYCODE_A",
    "KEYCODE_BACK",
    "KEYCODE_C",
    "KEYCODE_CTRL_LEFT",
    "KEYCODE_DEL",
    "RESIDENT_DEVICE_PORT",
    "RESIDENT_INSTRUMENTATION",
    "RESIDENT_SERVER_PACKAGE",
    "RESIDENT_TEST_METHOD",
    "RESIDENT_TEST_PACKAGE",
    "SERVICE_TO_ANDROID_PERMISSIONS",
    "VIDEO_DEVICE_PATH",
    "DeviceError",
    "Env",
    "RunFn",
    "TouchDevice",
    "booted_serials",
    "cat_cmd",
    "checked_serial",
    "clear_primary_clip_cmd",
    "deeplink_cmd",
    "device_catalog",
    "device_error",
    "devices_cmd",
    "double_tap_cmd",
    "dump_cmd",
    "dumpsys_surfaceflinger_latency_cmd",
    "exit_info_cmd",
    "extract_crash_block",
    "file_size_cmd",
    "force_stop_cmd",
    "forward_cmd",
    "forward_remove_cmd",
    "geo_fix_cmd",
    "get_primary_clip_cmd",
    "get_prop_cmd",
    "getevent_probe_cmd",
    "id_u_cmd",
    "install_cmd",
    "instrument_cmd",
    "keycombination_cmd",
    "keyevent_cmd",
    "keyevents_cmd",
    "launch_cmd",
    "launch_marker_cmd",
    "logcat_cmd",
    "logcat_crash_dump_cmd",
    "logcat_tail_cmd",
    "newest_exit_info",
    "package_path_cmd",
    "parse_clipboard_result",
    "parse_file_size",
    "parse_touch_device",
    "pidof_cmd",
    "pinch_contacts",
    "pm_clear_cmd",
    "pm_grant_cmd",
    "pm_revoke_cmd",
    "pull_cmd",
    "real_run",
    "resolve_activity_cmd",
    "resolve_serial",
    "reverse_cmd",
    "reverse_remove_cmd",
    "rm_cmd",
    "root_cmd",
    "rotate_contacts",
    "scale_to_touch",
    "screencap_cmd",
    "screenrecord_cmd",
    "screenrecord_pids_cmd",
    "sendevent_double_tap_cmd",
    "sendevent_gesture_cmd",
    "set_primary_clip_cmd",
    "shell_cmd",
    "swipe_cmd",
    "tap_cmd",
    "text_script",
    "tombstones_cmd",
    "uninstall_cmd",
    "unroot_cmd",
    "wait_for_device_cmd",
    "wm_size_cmd",
]
