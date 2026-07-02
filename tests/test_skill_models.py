"""Deterministic check that each in-repo skill's ``model:`` frontmatter is a known model id (BE-0103).

BE-0103 makes the economical model choice automatic by baking a default ``model:`` into every
in-repo skill's ``SKILL.md`` frontmatter (heavy → ``opus``, medium → ``sonnet``). The convention
itself stays advisory — a session can always upshift, and the gate never dictates which model to
run. The one machine-checkable surface worth pinning is that the value is a *valid, known* id, so a
typo fails here locally instead of silently falling back to some default at run time.

This walks the real ``.claude/skills`` tree: every ``model:`` present must be recognized, and the
skills that BE-0103 wired must still declare one (so removing the field is a visible regression, not
a silent drift back to always-max).
"""

from __future__ import annotations

from pathlib import Path

import yaml

SKILLS = Path(__file__).resolve().parent.parent / ".claude" / "skills"

# The syntactic allow-list of recognized ``model:`` ids — not the tier → model assignment. Which
# tier a task uses (heavy → opus, and so on) is guidance documented in docs/ai-development.md, and a
# skill's own frontmatter is what actually selects its model; re-pointing a tier means editing that
# doc table and the skill, not this set. This set exists only so a typo'd id fails loudly here.
# Aliases are Claude Code's stable names; ``inherit`` keeps the session's current model; a
# fully-qualified ``claude-…`` id is also accepted (see ``_is_known_model``).
KNOWN_ALIASES = frozenset({"opus", "sonnet", "haiku", "fable", "opusplan", "default", "inherit"})

# Sentinel: the frontmatter has no ``model:`` key at all (as opposed to a present-but-empty one).
_ABSENT = object()


def _is_known_model(value: str) -> bool:
    """Whether ``value`` is a recognized alias or a fully-qualified ``claude-…`` id."""
    return value in KNOWN_ALIASES or (
        value.startswith("claude-") and all(c.isalnum() or c in ".-" for c in value)
    )


def _frontmatter(skill_md: Path) -> dict[str, object]:
    """Parse the leading ``---``-fenced YAML block of a ``SKILL.md`` (empty dict if absent)."""
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    _, _, rest = text.partition("---")
    block, sep, _ = rest.partition("\n---")
    if not sep:
        return {}
    parsed = yaml.safe_load(block)
    return parsed if isinstance(parsed, dict) else {}


def _declared_model(fm: dict[str, object]) -> object:
    """The frontmatter's declared model, or ``_ABSENT`` when there is no ``model:`` key.

    A present-but-empty ``model:`` (YAML parses a bare key or ``""`` to ``None`` / empty string) is
    returned as ``""`` so it reads as a declared-but-invalid id and fails loudly, rather than being
    mistaken for "no default declared".
    """
    if "model" not in fm:
        return _ABSENT
    value = fm["model"]
    return "" if value is None else str(value)


def _skill_files() -> list[Path]:
    return sorted(SKILLS.glob("*/SKILL.md"))


def test_every_declared_model_is_known() -> None:
    """A declared ``model:`` the harness won't recognize (typo, or an empty value) fails loudly."""
    bad = {
        md.parent.name: model
        for md in _skill_files()
        if (model := _declared_model(_frontmatter(md))) is not _ABSENT
        and not _is_known_model(str(model))
    }
    assert not bad, f"unknown model id in skill frontmatter: {bad}"


def test_tiered_skills_declare_a_model() -> None:
    """The skills BE-0103 tiered still declare a non-empty ``model:``, so dropping the default is a
    visible regression rather than a silent drift back to running everything at always-max."""
    tiered = {"implement-be", "ideation", "japanese-tech-writing"}
    declared = {
        md.parent.name
        for md in _skill_files()
        if (model := _declared_model(_frontmatter(md))) is not _ABSENT and model
    }
    missing = tiered - declared
    assert not missing, f"tiered skills must declare a model: (BE-0103): {sorted(missing)}"
