"""Framework-agnostic serve operations (BE-0015).

The orchestration behind each serve endpoint, lifted out of the stdlib HTTP handler so the local
stdlib server and the hosted-backend FastAPI control plane share **one** implementation —
local/server parity reaching the request-handling layer, not just the four swap-in seams. Each
function takes the `ServeState` plus already-parsed inputs and returns ``(payload, status)``; the
HTTP shells own only the transport-specific parts (auth / CSRF / cookies / headers, JSON encoding,
SSE streaming, static asset serving)."""

from __future__ import annotations

from bajutsu.serve.authz import (
    forbidden_for_role,
    login,
    oauth_callback,
    oauth_login,
    required_role,
    role_allows,
    role_for,
)
from bajutsu.serve.operations._common import (
    _default_driver_factory,
    _device_args,
    _resolve_org_or_forbid,
)
from bajutsu.serve.operations.capture import (
    finish_capture,
    mark_capture,
    start_capture,
)
from bajutsu.serve.operations.config import (
    _confined_config_path,
    _valid_key_env_name,
    active_key_env,
    api_key_info,
    bind_config,
    bind_git_config,
    config_info,
    provider_info,
    set_api_key,
    set_provider,
)
from bajutsu.serve.operations.dispatch import (
    _bool_flag,
    _boot_targets,
    _register_and_dispatch,
    start_crawl,
    start_record,
    start_run,
)
from bajutsu.serve.operations.doctor import doctor_check
from bajutsu.serve.operations.enrich import start_enrich
from bajutsu.serve.operations.evidence import generate_upload_urls
from bajutsu.serve.operations.lint import lint_scenario, scenario_schema
from bajutsu.serve.operations.reads import (
    _find_sid,
    _primary_backend,
    _step_action_fields,
    _step_artifacts,
    _valid_step_id,
    approve_baseline,
    browse_fs,
    cancel_job,
    job_view,
    list_scenarios,
    list_targets_payload,
    read_scenario,
    resolve_scenario_pick,
    run_file,
    runs_payload,
    save_scenario,
    simulators_payload,
    stats_html,
)
from bajutsu.serve.operations.sse import (
    _job_event_pairs,
    _job_sse_frames,
    _terminal_payload,
    format_sse,
    job_log_events,
    job_sse,
)
from bajutsu.serve.operations.upload import (
    _safe_filename,
    bind_upload_config,
)
from bajutsu.serve.operations.worker import (
    worker_heartbeat,
    worker_lease,
    worker_result,
)
from bajutsu.serve.operations.worker_uploads import (
    worker_artifact_urls,
    worker_scenario_url,
)

__all__ = [
    "_bool_flag",
    "_boot_targets",
    "_confined_config_path",
    "_default_driver_factory",
    "_device_args",
    "_find_sid",
    "_job_event_pairs",
    "_job_sse_frames",
    "_primary_backend",
    "_register_and_dispatch",
    "_resolve_org_or_forbid",
    "_safe_filename",
    "_step_action_fields",
    "_step_artifacts",
    "_terminal_payload",
    "_valid_key_env_name",
    "_valid_step_id",
    "active_key_env",
    "api_key_info",
    "approve_baseline",
    "bind_config",
    "bind_git_config",
    "bind_upload_config",
    "browse_fs",
    "cancel_job",
    "config_info",
    "doctor_check",
    "finish_capture",
    "forbidden_for_role",
    "format_sse",
    "generate_upload_urls",
    "job_log_events",
    "job_sse",
    "job_view",
    "lint_scenario",
    "list_scenarios",
    "list_targets_payload",
    "login",
    "mark_capture",
    "oauth_callback",
    "oauth_login",
    "provider_info",
    "read_scenario",
    "required_role",
    "resolve_scenario_pick",
    "role_allows",
    "role_for",
    "run_file",
    "runs_payload",
    "save_scenario",
    "scenario_schema",
    "set_api_key",
    "set_provider",
    "simulators_payload",
    "start_capture",
    "start_crawl",
    "start_enrich",
    "start_record",
    "start_run",
    "stats_html",
    "worker_artifact_urls",
    "worker_heartbeat",
    "worker_lease",
    "worker_result",
    "worker_scenario_url",
]
