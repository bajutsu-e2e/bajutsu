"""Serve state container: the `ServeState` shared by the serve package and the value types it holds.

Split from `serve/jobs.py` (BE-0206): most of the serve package reads `ServeState` (and the `Job`,
`StoreBundle`, `CaptureSession` value types), while only the run/cancel execution engine — which
stays in `jobs.py` — mutates a `Job`. The runtime dependency is one-directional: `state` imports
`executor` at runtime (for the `UnqueuedExecutor` field default), while `executor` references
`ServeState`/`Job` only under `TYPE_CHECKING` and imports `jobs` lazily — avoiding a
`state ⇄ executor` cycle. The state module keeps the file from growing on two axes at once.
"""

from ._functions import _scenarios_dir_for as _scenarios_dir_for
from ._shared import _DEFAULT_ORG as _DEFAULT_ORG
from .capture_session import CaptureSession
from .config_binding import ConfigBinding
from .job import JOB_KINDS, Job, JobKind
from .job_registry import JobRegistry
from .org_provider_settings import OrgProviderSettings
from .provider_settings import ProviderSettings
from .provider_settings_manager import ProviderSettingsManager
from .serve_state import MAX_SESSION_BINDINGS, Popen, ServeState
from .session_manager import SessionManager
from .store_bundle import StoreBundle

__all__ = [
    "JOB_KINDS",
    "MAX_SESSION_BINDINGS",
    "CaptureSession",
    "ConfigBinding",
    "Job",
    "JobKind",
    "JobRegistry",
    "OrgProviderSettings",
    "Popen",
    "ProviderSettings",
    "ProviderSettingsManager",
    "ServeState",
    "SessionManager",
    "StoreBundle",
]
