"""Triage serve operation (BE-0147): diagnose a failed run from the Replay / History view."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bajutsu.common.config import load_config_file, resolve
from bajutsu.serve.authz import _record_audit
from bajutsu.serve.commands import triage_command
from bajutsu.serve.helpers import valid_run_id
from bajutsu.serve.operations._common import _resolve_org_or_forbid
from bajutsu.serve.operations.dispatch import _register_and_dispatch
from bajutsu.serve.state import Job, ServeState

if TYPE_CHECKING:
    from pathlib import Path

    from bajutsu.common.config import AiConfig

_logger = logging.getLogger(__name__)


def start_triage(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None, session: str | None = None
) -> tuple[Any, int]:
    """Diagnose a failed run as a serve job — the "why did this fail?" the Replay/History view asks.

    Runs ``bajutsu triage`` over the stored run at ``runId`` (reusing the job / SSE log-stream /
    cancel machinery), writing a machine-readable ``triage.json`` into the run dir for the UI to read
    back. A server with a queue hands the job to a worker on its own machine (BE-0449). The verdict
    is never recomputed: triage only explains the run's own pass/fail and, when a structured fix
    applies, previews a diff the human writes back through the validated scenario-save path
    (``POST /api/scenario``). AI (``ai``) is opt-in and only an investigator; the
    deterministic heuristic agent is the default.
    """
    binding = state.binding_for(session, state.org_of(actor))
    cfg = binding.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    run_id = str(body.get("runId") or "")
    if not valid_run_id(run_id):
        return {"error": "a valid runId is required"}, 400
    if not body.get("target") or not body.get("scenario"):
        return {"error": "target and scenario are required"}, 400
    target = str(body["target"])
    org, forbidden = _resolve_org_or_forbid(state, target, actor, session)
    if forbidden:
        return forbidden
    scope = state.for_org(org).scenarios.scope(target, session=session, org=org)
    if scope is None:
        return {"error": f"target '{target}' has no scenarios dir"}, 400
    # Resolve the client value to a trusted source path — never the raw string — so no client-controlled
    # path reaches the spawned argv (BE-0051), and the fix is previewed against the file the UI edits.
    runnable = scope.runnable(str(body["scenario"]))
    if runnable is None:
        return {
            "error": "scenario must be an existing .yaml inside the target's scenarios dir"
        }, 400
    # The job names the run dir, the config, and the scenario source by their absolute paths on the
    # server's disk, so the worker that runs it (BE-0449) must share this machine, as a local start's
    # does, and writes `triage.json` straight into the run dir. A hosted scenario shipped as
    # `materials`, with its run in object storage, would need the run downloaded to the worker and
    # the result uploaded back — not built yet. Fail loudly rather than triage against files that
    # aren't on this host.
    if runnable.materials:
        return {"error": "triage in the web UI is not yet available on the server backend"}, 400
    # Resolve against base_cwd (serve's launch dir) so the run dir is absolute: the spawned triage
    # runs with the session's bound cwd, which a Git/upload config bind can repoint away from where
    # runs live (BE-0063/BE-0073) — a relative runs_dir would then resolve against the wrong tree.
    run_dir = (state.base_cwd / state.runs_dir / run_id).resolve()
    # A missing run is a client error, not a job that fails mid-stream (and would write triage.json
    # into a nonexistent dir). The manifest is what `triage` reads, so its absence is the check.
    if not (run_dir / "manifest.json").is_file():
        return {"error": "run not found"}, 404
    # Only the JSON boolean `true` opts into AI — a string like "false" must not turn it on (it would
    # be truthy under bool()). The default is the deterministic heuristic agent.
    ai = body.get("ai") is True
    # Gate before dispatch so a missing credential is a clean 400, not a job that fails mid-stream.
    # The `--ai` path is opt-in and only an investigator; the run's verdict is already decided.
    if ai and (refused := _ai_credential_refusal(state, cfg, target, org)) is not None:
        return refused
    cmd = triage_command(
        str(run_dir),
        # --target carries the target's `ai` config + redaction rules for the AI path (BE-0047); the
        # heuristic path needs neither, so it's omitted to keep the argv minimal.
        target=target if ai else "",
        ai=ai,
        apply_path=runnable.arg,
        json_out=str(run_dir / "triage.json"),
        config=str(cfg),
    )
    # Every path in the argv is absolute, so an uploaded bundle would be dead weight on the worker.
    job, capped = _register_and_dispatch(
        state, Job(cmd=cmd, actor=actor, org=org), binding=binding, ship_bundle=False
    )
    if capped:
        return capped
    assert job is not None
    _record_audit(
        state, actor, org, "triage", f"{target}/{body['scenario']}", {"runId": run_id, "ai": ai}
    )
    return {"jobId": job.id}, 200


def _ai_credential_refusal(
    state: ServeState, cfg: Path, target: str, org: str
) -> tuple[dict[str, str], int] | None:
    """The 400 an `--ai` triage gets when its provider cannot authenticate, or None when it can."""
    from bajutsu.common.agents import availability as ai_availability
    from bajutsu.common.ai import credential_gap

    config = load_config_file(cfg)
    ai_config = resolve(config, target).ai
    gap = credential_gap(ai_config)
    if gap == "anthropic-key" and _stored_ai_key_reaches(state, org, ai_config):
        gap = None
    if gap is None:
        return None
    # The phrased message, not the raw token — see the same guard in `enrich.py` (BE-0394).
    return {"error": f"AI triage cannot run: {ai_availability.message(gap, ai_config)}"}, 400


def _stored_ai_key_reaches(state: ServeState, org: str, ai_config: AiConfig | None) -> bool:
    """Whether *org*'s stored AI key reaches the triage job under the variable it reads.

    A store that keeps the key out of this process's environment (the database store of a local
    start, BE-0449) still hands it to the job: the lease relays it to the worker, which lays it
    over the child's environment. Only the masked description is read here, never the
    plaintext, and a store that cannot be read counts as holding none. The key travels under the bound config's ``ai.keyEnv``, so a target that names
    another variable does not count it.
    """
    from bajutsu.common.agents.anthropic_client import key_env
    from bajutsu.serve.operations.config import AI_API_KEY_SECRET

    if state.env_var_for_secret(AI_API_KEY_SECRET) != key_env(ai_config):
        return False
    try:
        return state.for_org(org).secrets.describe(AI_API_KEY_SECRET) is not None
    # Any store failure, whatever its backend raises, counts as no stored key: the caller then gives
    # the phrased 400 rather than a 500. The log names the org alone, never a value.
    except Exception:
        _logger.warning("could not read the stored AI key of org %s", org, exc_info=True)
        return False
