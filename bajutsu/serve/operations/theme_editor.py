"""Theme editor support for the serve Web UI (BE-0191 unit 6).

Exposes the design-token contract to the client so the in-UI editor can generate its form from it.
Server-side persistence (upload into the ``--themes`` directory, reusing the BE-0073 seam) lands in
part 2 of unit 6; the local-draft and export/import paths live entirely in the client.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from bajutsu.serve import themes as themes_module
from bajutsu.serve.state import ServeState

_log = logging.getLogger(__name__)

# serve.themes.css lives in bajutsu/templates/. The templates dir is also computed in
# bajutsu/serve/handler.py (_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"),
# but importing it from there risks a circular import. This module sits one package deeper
# (bajutsu/serve/operations/), so the hop count differs. The two paths share the same
# anchor (bajutsu package root), but are maintained independently — something to unify
# when a shared _paths.py or importlib.resources is introduced.
_CONTRACT_PATH = Path(__file__).parent.parent.parent / "templates" / "serve.themes.css"


def get_theme_contract(_state: ServeState) -> tuple[dict[str, Any], int]:
    """The design-token contract (BE-0191 unit 1) exposed as JSON for the editor.

    Reads serve.themes.css, extracts the token list, and fills each token's default from the
    :root fallback block. Follows the operations `(payload, status)` convention so a read failure
    can report a non-200 status rather than a 200 with an error body.

    ``_state`` is accepted for dispatch-signature uniformity (every ``ops.*`` handler takes the
    serve state) but is not read here — the contract is a bundled static file, not state-derived.
    """
    try:
        contract_css = _CONTRACT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        _log.error("failed to read theme contract: %s", e)
        return {"error": "contract not available", "colors": {}, "transitions": {}}, 500

    tokens = themes_module.parse_theme_tokens(contract_css)

    # Fill defaults from the :root/midnight block, which the CSS writes with double quotes.
    root_match = re.search(r':root(?:\s*,\s*\[data-theme="midnight"\])?\s*{([^}]*)}', contract_css)
    if root_match:
        root_block = root_match.group(1)
        for match in re.finditer(r"--([\w-]+)\s*:\s*([^;]+);", root_block):
            token_name = f"--{match.group(1)}"
            value = match.group(2).strip()
            if token_name in tokens["colors"]:
                tokens["colors"][token_name]["default"] = value
            elif token_name in tokens["transitions"]:
                tokens["transitions"][token_name]["default"] = value

    return tokens, 200
