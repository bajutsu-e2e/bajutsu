"""Presigned evidence-upload serve operation (BE-0110 serve path).

The control plane holds the evidence store's credentials; a worker asks for a presigned PUT URL per
file and uploads over plain HTTP, so it needs no cloud credentials of its own. The server owns the
bucket and base prefix and re-validates every client-supplied segment (the run id, the optional
per-run prefix, each file path), so a worker can't write outside the run's key namespace.
"""

from __future__ import annotations

from typing import Any

from bajutsu.serve.helpers import valid_relative_key, valid_run_id
from bajutsu.serve.jobs import ServeState
from bajutsu.serve.operations.presign import sign_put_urls


def generate_upload_urls(
    state: ServeState, run_id: str, body: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    """Issue one presigned PUT URL per file for uploading *run_id*'s evidence.

    Keys nest as ``<base_prefix><evidence_prefix><run_id>/<file>`` — the server's base prefix, the
    caller's optional per-run prefix (which selects the cloud lifecycle policy), then the run id, so
    runs never collide. Returns empty ``urls`` when no evidence store is configured, so a worker can
    always ask and simply upload nothing.

    Args:
        run_id: the run whose evidence is uploaded; must be a safe path segment.
        body: ``{"files": [<relative path>, ...], "evidence_prefix": <optional prefix>}``.

    Returns:
        ``({"urls": {file: url, ...}}, 200)``, or an error payload with a 400 status when the run id,
        the prefix, or a file path is unsafe or ``files`` is not a list.
    """
    target = state.evidence
    if target is None:
        return {"urls": {}}, 200
    if not valid_run_id(run_id):
        return {"error": "invalid runId"}, 400
    raw_prefix = body.get("evidence_prefix")
    if raw_prefix is not None and not isinstance(raw_prefix, str):
        return {"error": "evidence_prefix must be a string"}, 400
    evidence_prefix = raw_prefix or ""
    if not valid_relative_key(evidence_prefix, allow_empty=True):
        return {"error": "invalid evidence_prefix"}, 400
    if evidence_prefix and not evidence_prefix.endswith("/"):
        evidence_prefix += "/"
    prefix = f"{target.base_prefix}{evidence_prefix}{run_id}/"
    urls, err = sign_put_urls(target.store, prefix, body.get("files"))
    if err is not None:
        return err
    return {"urls": urls}, 200
