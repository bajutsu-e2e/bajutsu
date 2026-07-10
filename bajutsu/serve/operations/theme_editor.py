"""Theme editor support for the serve Web UI (BE-0191 unit 6).

Exposes the design-token contract to the client so the in-UI editor can generate its form from it,
and persists an edited theme into the ``--themes`` directory (unit 2) so it becomes a discoverable
drop-in shared across sessions. The local-draft and export/import paths live entirely in the client;
this module is the one server-side persistence tier.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from bajutsu.serve import themes
from bajutsu.serve._paths import TEMPLATES_DIR
from bajutsu.serve.state import ServeState

_log = logging.getLogger(__name__)

_CONTRACT_PATH = TEMPLATES_DIR / "serve.themes.css"

# A theme id derives from its name so the on-disk filename, the manifest, and the `[data-theme]`
# selector are guaranteed to agree (discover_themes warns when they don't). Anything that isn't a
# lowercase alnum run collapses to a single hyphen.
_SLUG_SEP = re.compile(r"[^a-z0-9]+")
# A token name must be a plain custom property, and its value must not carry the `{ } ;` delimiters
# that would let it break out of the `[data-theme]{ … }` rule it is interpolated into. This mirrors
# the client's `safeThemeToken` guard: a theme is operator-trusted, so this is a corruption guard,
# not a security boundary.
_SAFE_NAME = re.compile(r"^--[\w-]+$")
_UNSAFE_VALUE = re.compile(r"[{};]")


def _slug(name: str) -> str:
    """The theme id for *name*: lowercased, non-alnum runs hyphenated, edges trimmed (``""`` if none)."""
    return _SLUG_SEP.sub("-", name.lower()).strip("-")


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


def upload_theme(
    state: ServeState, body: dict[str, Any], actor: str | None
) -> tuple[dict[str, Any], int]:
    """Persist an edited theme into the ``--themes`` directory as a discoverable drop-in.

    The client sends ``{name, kind, tokens}``; the server is the authority on the file format — it
    derives the ``[data-theme]`` id from the sanitized name (so the selector always matches the
    filename stem), composes the canonical manifest + rule, guards every token name/value against
    breaking out of the rule, and — the one place unit 2's live-reload exclusion is lifted —
    invalidates the cached index render so the new theme lists on the next request.

    Args:
        state: Serve state; ``themes_dir`` must be set (the ``--themes`` flag) for a target to exist.
        body: The editor payload — ``name`` (required), ``kind`` (``dark``/``light``, default
            ``dark``), and ``tokens`` (a non-empty ``{custom-property: value}`` map).
        actor: The authenticated uploader, logged for the write; the theme is not org-scoped.

    Returns:
        A ``(payload, status)`` pair. On success ``{"ok": True, "id": <slug>, "overwritten": bool}``
        with 200; on a bad request an ``{"error": …}`` body with 400.
    """
    if state.themes_dir is None:
        return {
            "error": "theme upload requires the serve instance to be started with --themes <dir>"
        }, 400

    name = str(body.get("name", "") or "").strip()
    theme_id = _slug(name)
    if not theme_id:
        return {"error": "the theme name must contain at least one letter or digit"}, 400
    if theme_id in {b.id for b in themes.BUILTIN_THEMES}:
        return {"error": f"'{theme_id}' collides with a built-in theme; choose another name"}, 400

    kind = body.get("kind")
    kind = kind if kind in ("dark", "light") else "dark"

    tokens = body.get("tokens")
    if not isinstance(tokens, dict) or not tokens:
        return {"error": "no theme tokens supplied"}, 400
    rules = []
    for raw_k, raw_v in tokens.items():
        k, v = str(raw_k), str(raw_v).strip()
        if not _SAFE_NAME.match(k) or _UNSAFE_VALUE.search(v):
            return {"error": f"invalid token {raw_k!r}"}, 400
        rules.append(f"  {k}: {v};")

    # Keep the display name from closing the manifest comment early or spilling onto the kind line.
    display = name.replace("*/", "").replace("\n", " ").strip() or theme_id
    css = (
        f"/* bajutsu-theme\nname: {display}\nkind: {kind}\n*/\n"
        f'[data-theme="{theme_id}"]{{\n' + "\n".join(rules) + "\n}\n"
    )

    dest = state.themes_dir / f"{theme_id}.css"
    overwritten = dest.exists()
    try:
        state.themes_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(css, encoding="utf-8")
    except OSError as e:
        _log.error("failed to write theme %s: %s", dest, e)
        return {"error": "failed to write theme"}, 500

    # The discovered set is folded into the lru_cache'd index render (BE-0191 unit 2), so a fresh
    # drop-in would otherwise stay invisible until restart. Imported lazily to avoid a serve.handler
    # ↔ operations import cycle at module load.
    from bajutsu.serve import handler as _handler

    _handler._index_html.cache_clear()

    _log.info("theme %r uploaded by %s (overwrite=%s)", theme_id, actor or "?", overwritten)
    return {"ok": True, "id": theme_id, "overwritten": overwritten}, 200
