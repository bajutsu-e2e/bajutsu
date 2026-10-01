"""XCUITest backend — semantic actuation over a loopback HTTP channel (BE-0019).

Unlike a coordinate-CLI backend that taps frame-centre coordinates, XCUITest actuates from a resident
XCTest runner living on the Simulator, so Python and that runner talk over a small `127.0.0.1`
channel — the same loopback pattern `network.py` already uses, in the Python→runner direction. This
module is the **Python side** of that channel: it builds the requests, parses the responses, and maps
failures onto the shared `Driver` exceptions. The runner itself (a generic XCTest target in
`BajutsuKit`) is a separate, on-device slice; here the transport is injectable so the request/response
logic is exercised against a fake — no Simulator on the gate.

The crux is **element addressing**: resolution stays Python-side (`resolve_unique`), so the driver
acts on exactly the element it resolved by sending that element's opaque *handle* the runner minted —
never a re-resolved predicate that could match a different element. The runner derives that handle
from the element's identity (identifier / label / traits), so a re-snapshot of an unchanged screen
re-issues the *identical* handle; a handle goes stale only when the element leaves the screen or
changes identity (BE-0312). A `stale` reply is still treated as a trigger to re-query rather than an
immediate failure (BE-0289): the actuation is re-issued only while the same selector still resolves
Python-side to a single element, and fails loudly the moment it resolves to none (`ElementNotFound`)
or many (`AmbiguousSelector`) — so the retry tolerates a transient `stale` without ever absorbing a
real disappearance.

Selection-wiring (adding `xcuitest` to `backends.IMPLEMENTED` / `make_driver`, plus the device
availability probe) lands with the runner; today the driver is constructed directly (e.g. in tests)
and `backends.capabilities_for` reads its `CAPABILITIES` without a device.
"""

from ._conn_state import _ConnState as _ConnState
from ._drain_carry import _DrainCarry as _DrainCarry
from ._functions import _ACTUATION_TIMEOUT_SECONDS as _ACTUATION_TIMEOUT_SECONDS
from ._functions import _BACKOFF_BASE_SECONDS as _BACKOFF_BASE_SECONDS
from ._functions import _KEEPALIVE_IDLE_RECONNECT_SECONDS as _KEEPALIVE_IDLE_RECONNECT_SECONDS
from ._functions import _LIVENESS_POLL_SECONDS as _LIVENESS_POLL_SECONDS
from ._functions import _MAX_ATTEMPTS as _MAX_ATTEMPTS
from ._functions import _MAX_CRASH_RECOVERIES as _MAX_CRASH_RECOVERIES
from ._functions import _MAX_HUNG_CALLS as _MAX_HUNG_CALLS
from ._functions import _RECOVERY_TIMEOUT_SECONDS as _RECOVERY_TIMEOUT_SECONDS
from ._functions import _SOCKET_TIMEOUT_SECONDS as _SOCKET_TIMEOUT_SECONDS
from ._functions import _TIPKIT_TIP_CONTAINER as _TIPKIT_TIP_CONTAINER
from ._functions import _as_float as _as_float
from ._functions import _await_health as _await_health
from ._functions import _decode as _decode
from ._functions import _http_transport as _http_transport
from ._functions import _is_retry_eligible as _is_retry_eligible
from ._functions import _is_stale as _is_stale
from ._functions import _observe_stall as _observe_stall
from ._functions import _parse_drain_fold as _parse_drain_fold
from ._functions import _parse_tap_drain_fold as _parse_tap_drain_fold
from ._functions import _raw_http_transport as _raw_http_transport
from ._functions import _runner_gone_mid_run as _runner_gone_mid_run
from ._functions import _timeout_for as _timeout_for
from ._functions import _tip_is_up as _tip_is_up
from ._functions import _to_element as _to_element
from ._functions import _with_crash_recovery as _with_crash_recovery
from ._functions import _with_retry as _with_retry
from ._health_wait import _HealthWait as _HealthWait
from ._reply import _Reply as _Reply
from ._shared import _OK as _OK
from ._shared import _TIPKIT_DISMISS_REGION as _TIPKIT_DISMISS_REGION
from ._shared import TransportFn
from ._transport_failure import _TransportFailure as _TransportFailure
from .runner_target import RunnerTarget
from .xcuitest_channel_error import XcuitestChannelError
from .xcuitest_driver import _NOT_FOUND as _NOT_FOUND
from .xcuitest_driver import _NOT_HITTABLE as _NOT_HITTABLE
from .xcuitest_driver import _STALE as _STALE
from .xcuitest_driver import _STALE_BACKOFF_BASE_SECONDS as _STALE_BACKOFF_BASE_SECONDS
from .xcuitest_driver import _STALE_MAX_ATTEMPTS as _STALE_MAX_ATTEMPTS
from .xcuitest_driver import _UNIT as _UNIT
from .xcuitest_driver import _VALUE_NOT_FOUND as _VALUE_NOT_FOUND
from .xcuitest_driver import XcuitestDriver
from .xcuitest_runner_crash_error import XcuitestRunnerCrashError

__all__ = [
    "RunnerTarget",
    "TransportFn",
    "XcuitestChannelError",
    "XcuitestDriver",
    "XcuitestRunnerCrashError",
]
