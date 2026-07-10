"""Drop-in theme discovery for the serve Web UI (BE-0191 unit 2).

A theme is declarative only — a CSS block of the tokens documented in
``bajutsu/templates/serve.themes.css`` plus a small manifest (display name and a ``dark`` /
``light`` kind). It carries no JavaScript, so a dropped-in theme sits at the same trust level as
the operator's scenarios and config while limiting the surface to CSS. Serve scans the ``--themes``
directory once at startup; the discovered CSS is folded into the inlined theme stylesheet and the
manifest is handed to the client so the picker (unit 3) can list the options. ``ui.default_theme``
is the serve-only initial selection, read here rather than modeled in the core ``Config`` — the
same split the ``orgs:`` block uses (BE-0129).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from bajutsu import _yaml

_log = logging.getLogger(__name__)

ThemeKind = Literal["dark", "light"]

# The *leading* `/* bajutsu-theme … */` comment carries the manifest; name/kind are read from it
# and the id comes from the filename. Both fields are optional — a theme with neither still
# registers. Anchored with `\A` so a copyright/license comment before the manifest isn't silently
# treated as the manifest (`.search` with `\A` still works without `.match`).
_MANIFEST_COMMENT = re.compile(r"\A/\*(.*?)\*/", re.DOTALL)
_NAME = re.compile(r"name:\s*(.+?)\s*(?:\n|kind:|$)", re.IGNORECASE)
_KIND = re.compile(r"kind:\s*([a-z]+)", re.IGNORECASE)


@dataclass(frozen=True)
class ThemeManifest:
    """What the picker needs to list a theme: its `[data-theme]` id, display name, and kind."""

    id: str
    name: str
    kind: ThemeKind


@dataclass(frozen=True)
class DiscoveredTheme:
    """A drop-in theme: its manifest plus the raw CSS block to inline verbatim."""

    manifest: ThemeManifest
    css: str


# The in-repo pair, always offered first and never replaced by a drop-in (BE-0191 unit 2).
BUILTIN_THEMES: tuple[ThemeManifest, ...] = (
    ThemeManifest(id="midnight", name="Midnight", kind="dark"),
    ThemeManifest(id="daylight", name="Daylight", kind="light"),
)


def _parse_manifest(theme_id: str, css: str) -> ThemeManifest:
    """Read the leading manifest comment, defaulting the name to the id and the kind to dark.

    A malformed or absent manifest degrades to those defaults rather than dropping the theme —
    silently discarding an operator's file would hide their mistake instead of surfacing it.
    """
    comment = _MANIFEST_COMMENT.search(css)
    block = comment.group(1) if comment else ""
    name_match = _NAME.search(block)
    kind_match = _KIND.search(block)
    # An unknown kind (or none) degrades to the dark default rather than rejecting the theme.
    kind: ThemeKind = "light" if kind_match and kind_match.group(1).lower() == "light" else "dark"
    return ThemeManifest(
        id=theme_id,
        name=name_match.group(1) if name_match else theme_id,
        kind=kind,
    )


def discover_themes(themes_dir: Path | None) -> list[DiscoveredTheme]:
    """Every `*.css` under *themes_dir*, as discovered themes ordered by id (stable across runs).

    Returns an empty list when no directory is given or it does not exist, so an absent ``--themes``
    is a no-op rather than an error.
    """
    if themes_dir is None or not themes_dir.is_dir():
        return []
    themes = []
    for path in sorted(themes_dir.glob("*.css")):
        try:
            css = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            # An unreadable / non-UTF-8 drop-in is an operator mistake worth surfacing — but it must
            # not 500 the whole index (this runs per render). Skip it with a warning, don't crash.
            _log.warning("skipping unreadable theme %s: %s", path, e)
            continue
        manifest = _parse_manifest(path.stem, css)
        if manifest.id in {b.id for b in BUILTIN_THEMES}:
            # A drop-in whose id matches a built-in would silently override it (its CSS appended
            # after the built-in block at equal specificity wins), the opposite of "never replaced".
            _log.warning(
                "skipping drop-in theme %s: id %r collides with a built-in theme", path, manifest.id
            )
            continue
        if f'[data-theme="{manifest.id}"]' not in css:
            # docs/cli.md says the [data-theme="<id>"] selector "must match" the filename stem, but
            # nothing in the CSS format enforces it. A mismatch registers the theme in the picker
            # while it silently applies nothing when selected — surfacing it keeps the fail-loudly
            # convention the rest of this loop follows.
            _log.warning(
                'drop-in theme %s: no `[data-theme="%s"]` rule found — '
                "it will list in the picker but never visually apply",
                path,
                manifest.id,
            )
        themes.append(DiscoveredTheme(manifest=manifest, css=css))
    return themes


def theme_manifests(themes_dir: Path | None) -> list[ThemeManifest]:
    """The built-in manifests followed by the discovered ones — the full picker list."""
    return [*BUILTIN_THEMES, *(t.manifest for t in discover_themes(themes_dir))]


def parse_theme_tokens(contract_css: str) -> dict[str, dict[str, dict[str, str]]]:
    """Extract the design-token contract from the serve.themes.css CSS blocks (BE-0191 unit 6).

    Returns a dict with keys ``"colors"`` and ``"transitions"``, each holding a dict of token
    metadata — including ``"default"`` values filled from the ``:root``/midnight fallback block.
    CSS-token parsing is the single source of truth here: the comment-stripping, token discovery,
    and default-fill all share the same comment-free text so there is no duplication.
    """
    # Strip /* … */ comment blocks once; both the token-discovery scan and the default-fill scan
    # operate on this same text so there is no risk of the two getting out of sync.
    css_no_comments = re.sub(r"/\*.*?\*/", "", contract_css, flags=re.DOTALL)

    # Discover all CSS custom property declarations (--name:) and categorize by name prefix.
    tokens_found: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"--([\w-]+)\s*:", css_no_comments):
        token_name = f"--{match.group(1)}"
        if token_name.startswith("--motion-"):
            # Infer type from the trailing segment so a token whose name merely *contains* a
            # keyword as a substring (e.g. --motion-release containing "ease") is not misclassified.
            last_seg = token_name.rsplit("-", 1)[-1]
            if last_seg in ("enter", "leave"):
                t = "keyframe"
            elif last_seg == "ease":
                t = "easing"
            else:
                t = "duration"
            tokens_found[token_name] = {"type": t, "default": ""}
        else:
            tokens_found[token_name] = {"description": "", "default": ""}

    # Fill defaults from the :root/midnight block (the CSS uses double-quoted attribute selectors).
    root_match = re.search(
        r':root(?:\s*,\s*\[data-theme="midnight"\])?\s*{([^}]*)}', css_no_comments
    )
    if root_match:
        for match in re.finditer(r"--([\w-]+)\s*:\s*([^;]+);", root_match.group(1)):
            token_name = f"--{match.group(1)}"
            value = match.group(2).strip()
            if token_name in tokens_found:
                tokens_found[token_name]["default"] = value

    colors = {k: v for k, v in tokens_found.items() if not k.startswith("--motion-")}
    transitions = {k: v for k, v in tokens_found.items() if k.startswith("--motion-")}
    return {"colors": colors, "transitions": transitions}


def read_default_theme(config_path: Path | None) -> str | None:
    """The `ui.default_theme` from *config_path*, or None when unset / no config / unparseable.

    A serve-only key: it is read straight from the raw YAML here, never added to the core ``Config``
    (which would reject it under ``extra="forbid"`` on the deterministic run path). A missing or
    malformed *config file* yields None rather than raising — the authoritative config loader
    surfaces such errors on its own path, so the theme default must not be the thing that turns them
    into a startup traceback. But a malformed ``ui:`` block itself (wrong shape, a typo'd key, a
    non-string value) is warned about rather than resolving silently to None — the same fail-loudly
    stance ``orgs:`` takes with its strict validation and ``discover_themes`` takes on a bad file.
    """
    if config_path is None:
        return None
    try:
        data = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    ui = data.get("ui") if isinstance(data, dict) else None
    if ui is None:
        return None
    if not isinstance(ui, dict):
        _log.warning(
            "ignoring `ui:` in %s: expected a mapping, got %s", config_path, type(ui).__name__
        )
        return None
    unknown = sorted(k for k in ui if k != "default_theme")
    if unknown:
        _log.warning(
            "ignoring unknown ui.* key(s) in %s: %s", config_path, ", ".join(map(str, unknown))
        )
    default = ui.get("default_theme")
    if default is not None and not isinstance(default, str):
        _log.warning(
            "ignoring ui.default_theme in %s: expected a string, got %s",
            config_path,
            type(default).__name__,
        )
        return None
    return default
