"""Host-side screen recording for the uiautomator codegen lane's connectedAndroidTest.

That lane runs the generated test through Gradle's `connectedAndroidTest` with no bajutsu runtime at
test time, so it cannot call `bajutsu.evidence.intervals.start_screenrecord` the way `bajutsu run`
does. This script is the CI twin: it calls the same function directly instead of reimplementing its
start/stop/pull lifecycle in shell, so the finalize sequence (SIGINT, the device-side pgrep-stdout
poll, the pull, the device-side cleanup) lives in one place rather than two that can drift apart
(observed drift: PR #1493 review).

Backgrounded by the Makefile's `e2e-codegen` recipe; a SIGINT sent after the Gradle test exits
triggers the same finalize `bajutsu run` uses, then this process exits.
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path
from types import FrameType

from bajutsu import adb
from bajutsu.evidence import intervals

# Bounds a multi-minute recording well inside the ~15min build+test budget (screenrecord's own
# default is 180s) and keeps the mp4 small enough for /sdcard and the artifact upload (screenrecord's
# default bit rate is 20 Mbps at the AVD's full 1080x2400 panel).
_TIME_LIMIT_S = 900
_SIZE = "540x1200"
_BIT_RATE = 2_000_000


def main() -> None:
    """Start recording; on SIGINT/SIGTERM, finalize, pull the mp4, and exit."""
    serial = adb.resolve_serial(sys.argv[1])
    target = Path(sys.argv[2])
    target.parent.mkdir(parents=True, exist_ok=True)
    interval = intervals.start_screenrecord(
        serial, target, time_limit=_TIME_LIMIT_S, size=_SIZE, bit_rate=_BIT_RATE
    )

    def _stop(_signum: int, _frame: FrameType | None) -> None:
        interval.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    signal.pause()


if __name__ == "__main__":
    main()
