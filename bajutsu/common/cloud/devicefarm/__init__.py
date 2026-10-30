"""AWS Device Farm batch submitter core (BE-0235; iOS support BE-0238; serve fan-out BE-0336).

Device Farm is a *batch* device cloud: it does not lend a device to drive over the network — it runs
*your* commands on a host that already has the reserved device connected (over `adb` on Android, over
the Xcode toolchain on iOS). So this is not a runtime provider (there is no device to acquire); it is
glue that ferries Bajutsu to the Device Farm host and back. Bajutsu runs *inside* Device Farm exactly
as it does anywhere — the same deterministic core, the same pass/fail from machine-checkable
assertions — so the verdict this module surfaces comes from **Bajutsu's own manifest**, never from
Device Farm's run classification.

This module is the reusable core, so the serve fan-out (BE-0336) and the CLI wrapper
(`scripts/devicefarm_submit.py`) share one submitter and one verdict path. The flow, all outside the
deterministic `run`/CI verdict path:

1. `render_test_spec` — the custom-environment test spec that installs deps, runs `bajutsu run`
   (the adb backend on Android, the XCUITest backend on iOS) for each scenario, and copies `runs/`
   into ``$DEVICEFARM_LOG_DIR`` so the artifacts come back.
2. `build_package` — bundle the Bajutsu payload (source/wheel + config + scenarios) for upload.
3. `submit_and_collect` — upload the app artifact (an Android `.apk` or an iOS `.ipa`), the test
   package, and the spec; schedule the run; poll it to completion; download the artifacts; and derive
   the verdict via `verdict_from_manifest`.

The AWS SDK (boto3) is reached only through the `DeviceFarmClient` / `Transfer` seams, so this module
imports without the ``aws`` extra and its logic is unit-tested against an in-memory fake; the real
boto3 client and the presigned-URL transfer that fill those seams live in the CLI wrapper
(`scripts/devicefarm_submit.py`) and serve's startup bootstrap (`bajutsu/serve/batch_bootstrap.py`).
Raw-adb access on the Device Farm host is a by-product of its toolchain rather than a first-class
guarantee (the first-class path is Appium); this module documents that so a future Device Farm change
does not silently break it.
"""

from ._functions import _BAJUTSU as _BAJUTSU
from ._functions import _DF_PLATFORM_ATTR as _DF_PLATFORM_ATTR
from ._functions import _HARD_CAP_SECONDS as _HARD_CAP_SECONDS
from ._functions import _PACKAGE_EXCLUDE_PREFIXES as _PACKAGE_EXCLUDE_PREFIXES
from ._functions import _PACKAGE_EXCLUDES as _PACKAGE_EXCLUDES
from ._functions import _PLATFORM_RUN as _PLATFORM_RUN
from ._functions import _POLL_INITIAL_SECONDS as _POLL_INITIAL_SECONDS
from ._functions import _POLL_INTERVAL_SECONDS as _POLL_INTERVAL_SECONDS
from ._functions import _TEST_UPLOAD_TYPES as _TEST_UPLOAD_TYPES
from ._functions import _UV as _UV
from ._functions import _VENV as _VENV
from ._functions import (
    APP_UPLOAD_TYPE,
    XCTEST_PACKAGE_DIR,
    Platform,
    TestType,
    build_package,
    collect_run,
    device_selection_for,
    host_backend,
    is_package_excluded,
    render_test_spec,
    stop_run,
    submit_and_collect,
    verdict_from_manifest,
)
from ._functions import _next_poll_delay as _next_poll_delay
from ._functions import _python_bootstrap_commands as _python_bootstrap_commands
from ._functions import _safe_extract as _safe_extract
from ._functions import _store_artifact as _store_artifact
from ._functions import _upload_one as _upload_one
from ._functions import _wait_run as _wait_run
from ._platform_run import _PlatformRun as _PlatformRun
from ._shared import REQUIREMENTS_TXT
from .device_farm_client import DeviceFarmClient
from .device_farm_error import DeviceFarmError
from .http_transfer import HttpTransfer
from .run_interrupted import Interrupt, RunInterrupted
from .transfer import Transfer
from .verdict import Verdict

__all__ = [
    "APP_UPLOAD_TYPE",
    "REQUIREMENTS_TXT",
    "XCTEST_PACKAGE_DIR",
    "DeviceFarmClient",
    "DeviceFarmError",
    "HttpTransfer",
    "Interrupt",
    "Platform",
    "RunInterrupted",
    "TestType",
    "Transfer",
    "Verdict",
    "build_package",
    "collect_run",
    "device_selection_for",
    "host_backend",
    "is_package_excluded",
    "render_test_spec",
    "stop_run",
    "submit_and_collect",
    "verdict_from_manifest",
]
