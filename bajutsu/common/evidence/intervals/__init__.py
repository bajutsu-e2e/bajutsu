"""Interval (lifecycle) evidence: video and device logs captured around a step.

These are subprocess child processes started before an action and stopped after the step settles —
`simctl` on iOS, `adb` on Android (the twin providers). Command builders are pure and unit-tested;
process spawning is injected so the start/stop lifecycle is testable without a device. Web is
driver-native and lives in the Playwright driver, not here.

- video: `simctl io <udid> recordVideo` (iOS) / `adb shell screenrecord` (Android) — finalized with
  SIGINT (a hard kill would leave a truncated mp4). Android records device-side and is pulled off
  after stop, since `screenrecord` cannot stream to a host file.
- deviceLog: `simctl spawn <udid> log stream` (iOS) / `adb logcat` (Android) streamed to a file —
  stopped with SIGTERM.
"""

from ._functions import _ANCHOR_UNCORRECTED_MSG as _ANCHOR_UNCORRECTED_MSG
from ._functions import _BEGIN as _BEGIN
from ._functions import _END as _END
from ._functions import _ORIGIN_SLACK as _ORIGIN_SLACK
from ._functions import _ORIGIN_STARTUP_CEILING as _ORIGIN_STARTUP_CEILING
from ._functions import _RECORDING_STARTED as _RECORDING_STARTED
from ._functions import _SCREENRECORD_GROWTH_POLL as _SCREENRECORD_GROWTH_POLL
from ._functions import _VIDEO_FINALIZE_TIMEOUT as _VIDEO_FINALIZE_TIMEOUT
from ._functions import _VIDEO_START_TIMEOUT as _VIDEO_START_TIMEOUT
from ._functions import _VIDEO_START_TIMEOUT_ENV as _VIDEO_START_TIMEOUT_ENV
from ._functions import (
    ADB_PROVIDER,
    Spawn,
    adopt,
    app_trace_cmd,
    device_log_cmd,
    parse_app_trace,
    record_video_cmd,
    spawn,
    start_app_trace,
    start_device_log,
    start_logcat,
    start_screenrecord,
    start_video,
)
from ._functions import _await_screenrecord_first_bytes as _await_screenrecord_first_bytes
from ._functions import _await_screenrecord_growing as _await_screenrecord_growing
from ._functions import _await_screenrecord_started as _await_screenrecord_started
from ._functions import _await_screenrecord_stopped as _await_screenrecord_stopped
from ._functions import _confirm_screenrecord_growing as _confirm_screenrecord_growing
from ._functions import _logger as _logger
from ._functions import _measured_start as _measured_start
from ._functions import _parse_ts as _parse_ts
from ._functions import _screenrecord_baseline_size as _screenrecord_baseline_size
from ._functions import _screenrecord_file_size as _screenrecord_file_size
from ._functions import _screenrecord_pids as _screenrecord_pids
from ._functions import _video_start_timeout as _video_start_timeout
from ._null_proc import _NullProc as _NullProc
from ._shared import (
    INTERVAL_KINDS,
    SCREENRECORD_BIT_RATE,
    SCREENRECORD_SIZE,
    SCREENRECORD_TIME_LIMIT_S,
    STARTERS,
)
from ._subprocess_proc import _STDERR_POLL as _STDERR_POLL
from ._subprocess_proc import _STDERR_READ_SIZE as _STDERR_READ_SIZE
from ._subprocess_proc import _SubprocessProc as _SubprocessProc
from .interval import _STOP_TIMEOUT as _STOP_TIMEOUT
from .interval import PROVIDER, Interval
from .proc import Proc

__all__ = [
    "ADB_PROVIDER",
    "INTERVAL_KINDS",
    "PROVIDER",
    "SCREENRECORD_BIT_RATE",
    "SCREENRECORD_SIZE",
    "SCREENRECORD_TIME_LIMIT_S",
    "STARTERS",
    "Interval",
    "Proc",
    "Spawn",
    "adopt",
    "app_trace_cmd",
    "device_log_cmd",
    "parse_app_trace",
    "record_video_cmd",
    "spawn",
    "start_app_trace",
    "start_device_log",
    "start_logcat",
    "start_screenrecord",
    "start_video",
]
