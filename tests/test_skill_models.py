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

# Accepted ``model:`` values. Claude Code reads either a stable alias (the tier → id mapping in
# docs/ai-development.md pins these) or a fully-qualified model id; ``inherit`` keeps the session's
# current model. Re-point a tier at a new model by changing the alias in a skill, not this set.
KNOWN_ALIASES = frozenset({"opus", "sonnet", "haiku", "fable", "opusplan", "default", "inherit"})


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


def _skill_files() -> list[Path]:
    return sorted(SKILLS.glob("*/SKILL.md"))


def test_every_declared_model_is_known() -> None:
    """No skill may pin a ``model:`` the harness won't recognize — a typo fails loudly, not silently."""
    bad = {
        md.parent.name: fm["model"]
        for md in _skill_files()
        if (fm := _frontmatter(md)).get("model") is not None
        and not _is_known_model(str(fm["model"]))
    }
    assert not bad, f"unknown model id in skill frontmatter: {bad}"


def test_tiered_skills_declare_a_model() -> None:
    """The skills BE-0103 tiered still declare a ``model:``, so dropping the default is a visible
    regression rather than a silent drift back to running everything at always-max."""
    tiered = {"implement-be", "ideation", "japanese-tech-writing"}
    present = {md.parent.name for md in _skill_files() if _frontmatter(md).get("model") is not None}
    missing = tiered - present
    assert not missing, f"tiered skills missing a model: default (BE-0103): {sorted(missing)}"
