"""Shared presigned PUT-URL signing for worker uploads (BE-0110 evidence, BE-0160 artifacts/scenarios).

The control plane holds the object store's credentials; a worker asks for a presigned PUT URL per
file and uploads over plain HTTP, so it needs no cloud credentials of its own. Each destination
(evidence / artifacts / scenarios) fixes its own base *prefix* server-side; this helper re-validates
every worker-supplied relative key against it, so a worker can never write outside the prefix.
"""

from __future__ import annotations

from typing import Any

from bajutsu.object_store import ObjectStore, content_type_for
from bajutsu.serve.helpers import valid_relative_key


def sign_put_urls(
    store: ObjectStore, prefix: str, files: Any
) -> tuple[dict[str, str] | None, tuple[dict[str, Any], int] | None]:
    """Sign one presigned PUT URL per relative file under *prefix*.

    *prefix* is fixed server-side (it ends with ``/``); each *files* entry is re-validated and keyed
    as ``<prefix><file>``, so a worker-supplied key can't escape it. The Content-Type is bound into
    each URL from the file extension, so the worker must PUT with the same type.

    Returns:
        ``(urls, None)`` on success, or ``(None, error)`` when *files* is not a list or any entry is
        not a safe relative key — the caller returns *error* (an ``({"error": ...}, status)`` pair)
        verbatim.
    """
    if not isinstance(files, list):
        return None, ({"error": "files must be a list"}, 400)
    urls: dict[str, str] = {}
    for rel in files:
        if not isinstance(rel, str) or not valid_relative_key(rel):
            return None, ({"error": f"invalid file path: {rel!r}"}, 400)
        urls[rel] = store.presigned_put_url(f"{prefix}{rel}", content_type=content_type_for(rel))
    return urls, None
