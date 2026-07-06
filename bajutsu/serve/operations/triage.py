"""Triage serve operation (BE-0147): diagnose a failed run from the Replay / History view."""

from __future__ import annotations

from typing import Any

from bajutsu.config import load_config, resolve
from bajutsu.serve.authz import _record_audit
from bajutsu.serve.helpers import triage_command, valid_run_id
from bajutsu.serve.jobs import Job, ServeState
from bajutsu.serve.operations._common import _resolve_org_or_forbid
from bajutsu.serve.operations.dispatch import _register_and_dispatch


def start_triage(
    state: ServeState, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Diagnose a failed run as a serve job — the "why did this fail?" the Replay/History view asks.

    Runs ``bajutsu triage`` over the stored run at ``runId`` (reusing the job / SSE log-stream /
    cancel machinery), writing a machine-readable ``triage.json`` into the run dir for the UI to read
    back. The verdict is never recomputed: triage only explains the run's own pass/fail and, when a
    structured fix applies, previews a diff the human writes back through the validated
    scenario-save path (``POST /api/scenario``). AI (``ai``) is opt-in and only an investigator; the
    deterministic heuristic agent is the default.
    """
    cfg = state.config
    if cfg is None:
        return {"error": "open a config first"}, 400
    run_id = str(body.get("runId") or "")
    if not valid_run_id(run_id):
        return {"error": "a valid runId is required"}, 400
    if not body.get("target") or not body.get("scenario"):
        return {"error": "target and scenario are required"}, 400
    target = str(body["target"])
    org, forbidden = _resolve_org_or_forbid(state, target, actor)
    if forbidden:
        return forbidden
    scope = state.for_org(org).scenarios.scope(target)
    if scope is None:
        return {"error": f"target '{target}' has no scenarios dir"}, 400
    # Resolve the client value to a trusted source path — never the raw string — so no client-controlled
    # path reaches the spawned argv (BE-0051), and the fix is previewed against the file the UI edits.
    runnable = scope.runnable(str(body["scenario"]))
    if runnable is None:
        return {
            "error": "scenario must be an existing .yaml inside the target's scenarios dir"
        }, 400
    # Triage reads the run dir and patches the scenario source on the host directly. The server
    # backend (scenario shipped as `materials`, artifacts in object storage) would need the run
    # artifacts downloaded to a worker first — a follow-up. Fail loudly rather than triage against
    # files that aren't on this host.
    if runnable.materials:
        return {"error": "triage in the web UI is not yet available on the server backend"}, 400
    # Resolve against base_cwd (serve's launch dir) so the run dir is absolute: the spawned triage
    # runs with cwd=state.cwd, which a Git/upload config bind can repoint away from where runs live
    # (BE-0063/BE-0073) — a relative runs_dir would then resolve against the wrong tree.
    run_dir = (state.base_cwd / state.runs_dir / run_id).resolve()
    # A missing run is a client error, not a job that fails mid-stream (and would write triage.json
    # into a nonexistent dir). The manifest is what `triage` reads, so its absence is the check.
    if not (run_dir / "manifest.json").is_file():
        return {"error": "run not found"}, 404
    # Only the JSON boolean `true` opts into AI — a string like "false" must not turn it on (it would
    # be truthy under bool()). The default is the deterministic heuristic agent.
    ai = body.get("ai") is True
    if ai:
        # Gate before dispatch so a missing credential is a clean 400, not a job that fails mid-stream.
        # The `--ai` path is opt-in and only an investigator; the run's verdict is already decided.
        from bajutsu.ai import credential_gap

        config = load_config(cfg.read_text(encoding="utf-8"))
        gap = credential_gap(resolve(config, target).ai)
        if gap:
            return {"error": f"AI triage requires an AI credential ({gap})"}, 400
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
    job, capped = _register_and_dispatch(state, Job(cmd=cmd, actor=actor, org=org))
    if capped:
        return capped
    assert job is not None
    _record_audit(
        state, actor, org, "triage", f"{target}/{body['scenario']}", {"runId": run_id, "ai": ai}
    )
    return {"jobId": job.id}, 200
