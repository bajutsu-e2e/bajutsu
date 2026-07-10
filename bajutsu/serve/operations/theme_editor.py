"""Theme editor support for the serve Web UI (BE-0191 unit 6).

Provides contract discovery and theme persistence: contract exposure to the client (for form
generation), local draft persistence, and server upload of custom themes to the --themes directory.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from bajutsu.serve import themes as themes_module
from bajutsu.serve.state import ServeState

_log = logging.getLogger(__name__)


def get_theme_contract(state: ServeState) -> dict[str, Any]:
    """The design-token contract (BE-0191 unit 1) exposed as JSON for the editor.

    Reads serve.themes.css, extracts the comment block (the documented token API), and returns
    the full token set with their defaults from the :root fallback block.
    """
    # Read the contract file (serve.themes.css).
    contract_path = Path(__file__).parent.parent / "templates" / "serve.themes.css"
    try:
        contract_css = contract_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        _log.error("failed to read theme contract: %s", e)
        return {"error": "contract not available", "colors": {}, "transitions": {}}

    # Parse the contract comment to get the token list.
    tokens = themes_module.parse_theme_tokens(contract_css)

    # Extract the :root default block to populate default values.
    root_match = re.search(r":root(?:\s*,\s*\[data-theme='midnight'\])?\s*{([^}]*)}", contract_css)
    if root_match:
        root_block = root_match.group(1)
        # Parse CSS custom properties: --name: value;
        for match in re.finditer(r"--([a-z0-9-]+)\s*:\s*([^;]+);", root_block):
            token_name = f"--{match.group(1)}"
            value = match.group(2).strip()

            if token_name in tokens["colors"]:
                tokens["colors"][token_name]["default"] = value
            elif token_name in tokens["transitions"]:
                tokens["transitions"][token_name]["default"] = value

    return tokens
