"""The serve UI's design-token contract is complete (BE-0191 unit 1).

Every color and surface style in ``serve.css`` must resolve to a custom property defined in
the theme registry's default (``:root`` / ``midnight``) block or in ``serve.css``'s own globals,
so a drop-in theme is a complete, self-contained contract and a partial theme falls back to the
default block. These checks guard that invariant: no consumer references an undefined token, no
color bypasses the system as a raw hex literal, and the pre-BE-0191 naming drift is gone.
"""

from __future__ import annotations

import re
from pathlib import Path

import bajutsu

_TEMPLATES = Path(bajutsu.__file__).parent / "templates"
_SERVE_CSS = (_TEMPLATES / "serve.css").read_text(encoding="utf-8")
_THEMES_CSS = (_TEMPLATES / "serve.themes.css").read_text(encoding="utf-8")
# The tiler/graph set some geometry tokens live via inline `style="--x:…"` (BE-0072); those are
# defined at runtime by the JS, not in a stylesheet, so treat them as defined too.
_JS = "\n".join(p.read_text(encoding="utf-8") for p in sorted(_TEMPLATES.glob("serve.*.js")))

# A `var(--x)` reference with no comma has no inline fallback, so the token must be defined
# somewhere; `var(--x, <fallback>)` supplies its own default (the tiler's geometry tokens set
# live via inline JS style work this way) and is allowed to be undefined in CSS.
_VAR_NO_FALLBACK = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*\)")
_DEFINE = re.compile(r"(--[a-z0-9-]+)\s*:")
# Hex color literals; the contract routes every color through a token instead.
_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def _root_block() -> str:
    """The `:root`/midnight block's body — the fallback source every theme degrades to.

    Scoping to this block (not the whole file) is the point: a token defined only inside another
    theme block (e.g. ``[data-theme="daylight"]``, or a drop-in theme from BE-0191 unit 2/3) does
    NOT satisfy the contract, because midnight — the fallback — would still render it unset.
    """
    match = re.search(r':root(?:,\[data-theme="midnight"\])?\s*\{([^}]*)\}', _THEMES_CSS)
    assert match, "serve.themes.css has no :root block"
    return match.group(1)


def _defined_tokens() -> set[str]:
    """Custom properties available to every element: the midnight fallback block plus serve.css globals.

    The theme registry's ``:root`` block (== midnight) is the fallback source for every theme, and
    serve.css may define non-themed globals (e.g. the monospace font stack) in its own ``:root``.
    """
    return (
        set(_DEFINE.findall(_root_block()))
        | set(_DEFINE.findall(_SERVE_CSS))
        | set(_DEFINE.findall(_JS))
    )


def test_every_referenced_token_is_defined() -> None:
    defined = _defined_tokens()
    referenced = set(_VAR_NO_FALLBACK.findall(_SERVE_CSS))
    missing = sorted(referenced - defined)
    assert not missing, f"serve.css references undefined tokens (no inline fallback): {missing}"


def test_no_raw_hex_color_in_serve_css() -> None:
    hexes = sorted(set(_HEX.findall(_SERVE_CSS)))
    assert not hexes, f"serve.css has raw hex literals that bypass the token system: {hexes}"


def test_pre_be0191_drift_names_are_gone() -> None:
    """The old names never defined by the registry (--accent/--muted/--bad/--border) are migrated."""
    drifted = [name for name in ("--accent", "--muted", "--bad", "--border") if name in _SERVE_CSS]
    assert not drifted, f"serve.css still references drifted token names: {drifted}"
