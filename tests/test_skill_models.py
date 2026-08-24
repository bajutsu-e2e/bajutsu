"""Deterministic check that each in-repo skill's ``model:`` frontmatter is a known model id (BE-0103).

BE-0103 makes the economical model choice automatic by baking a default ``model:`` into every
in-repo skill's ``SKILL.md`` frontmatter (heavy → ``opus``, medium → ``sonnet``). The convention
itself stays advisory — a session can always upshift, and the gate never dictates which model to
run. The one machine-checkable surface worth pinning is that the value is a *valid, known* id, so a
typo fails here locally instead of silently falling back to some default at run time.

This walks both trees: ``.claude/skills`` — the deployment Claude Code reads — and
``.apm/skills``, its single source (BE-0390). Walking the source directly is what makes the check
unconditional: ``apm install`` deploys frontmatter verbatim, but the drift step that would carry a
source-only edit into the deployment (``make lint-skills``) skips when ``apm`` is absent, so a
deployment-only walk would pass a typo introduced in a source on such a clone. Every ``model:``
present must be recognized, and the
skills that BE-0103 and BE-0380 wired must still declare one (so removing the field is a visible regression, not
a silent drift back to always-max).
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent
SKILLS = _REPO / ".claude" / "skills"
SOURCES = _REPO / ".apm" / "skills"

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
    """Every ``SKILL.md`` in the repository — deployment first, then source."""
    return sorted(SKILLS.glob("*/SKILL.md")) + sorted(SOURCES.glob("*/SKILL.md"))


def test_every_declared_model_is_known() -> None:
    """A declared ``model:`` the harness won't recognize (typo, or an empty value) fails loudly."""
    bad = {
        str(md.relative_to(_REPO)): model
        for md in _skill_files()
        if (model := _declared_model(_frontmatter(md))) is not _ABSENT
        and not _is_known_model(str(model))
    }
    assert not bad, f"unknown model id in skill frontmatter: {bad}"


def test_tiered_skills_declare_a_model() -> None:
    """The skills BE-0103 and BE-0380 tiered still declare a non-empty ``model:``, so dropping the
    default is a visible regression rather than a silent drift back to running everything at
    always-max.

    The set is only the subset BE-0103 and BE-0380 chose to pin here — not every skill that declares
    a ``model:``, nor every skill docs/ai-development.md lists a tier for. Extend it from a later
    item that wants its own skill's tier guarded the same way.

    Each tree is checked on its own rather than over the union of both: a skill's source and its
    deployment share a directory name, so one flat set would let either side satisfy the other and
    hide a one-sided loss — dropping ``model:`` from a source while its deployment still carries it.
    """
    tiered = {"fix-issue", "implement-be", "ideation", "japanese-document-writing"}
    for tree in (SKILLS, SOURCES):
        declared = {
            md.parent.name
            for md in sorted(tree.glob("*/SKILL.md"))
            if (model := _declared_model(_frontmatter(md))) is not _ABSENT and model
        }
        missing = tiered - declared
        assert not missing, (
            f"tiered skills must declare a model in {tree.relative_to(_REPO)}: {sorted(missing)}"
        )
