"""Theme editor support for the serve Web UI (BE-0191 unit 6).

Exposes the design-token contract to the client so the in-UI editor can generate its form from it.
Server-side persistence (upload into the ``--themes`` directory, reusing the BE-0073 seam) lands in
part 2 of unit 6; the local-draft and export/import paths live entirely in the client.
"""

from __future__ import annotations

import logging
from typing import Any

from bajutsu.serve import themes
from bajutsu.serve._paths import TEMPLATES_DIR
from bajutsu.serve.state import ServeState

_log = logging.getLogger(__name__)

_CONTRACT_PATH = TEMPLATES_DIR / "serve.themes.css"


def get_theme_contract(_state: ServeState) -> tuple[dict[str, Any], int]:
    """The design-token contract (BE-0191 unit 1) exposed as JSON for the editor.

    Reads serve.themes.css and delegates to ``themes.parse_theme_tokens``, which handles token
    discovery, type inference, and default-fill in a single comment-stripped pass — no grammar
    duplication here. Follows the operations ``(payload, status)`` convention so a read failure
    can report a non-200 status rather than a 200 with an error body.

    ``_state`` is accepted for dispatch-signature uniformity (every ``ops.*`` handler takes the
    serve state) but is not read here — the contract is a bundled static file, not state-derived.
    """
    try:
        contract_css = _CONTRACT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        _log.error("failed to read theme contract: %s", e)
        return {"error": "contract not available", "colors": {}, "transitions": {}}, 500

    return themes.parse_theme_tokens(contract_css), 200
