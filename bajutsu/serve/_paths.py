"""Shared filesystem paths for the serve package.

Centralises the ``templates/`` directory constant so ``handler.py`` (two ``.parent`` hops) and
``operations/theme_editor.py`` (three hops, one package deeper) don't have to maintain
independently hand-counted ``Path(__file__).parent…`` chains that would silently diverge if
either module moved.
"""

from __future__ import annotations

from pathlib import Path

# The bajutsu/templates/ directory, anchored to this file's known package location.
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
