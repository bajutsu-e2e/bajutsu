"""The Author view's left column can always scroll instead of clipping (issue #1715).

`.left` is a tile leaf, and `.tile-leaf` clips (`overflow:hidden`). Nothing inside it may therefore
keep a height larger than the leaf offers: whatever overflows is cut off with no scrollbar and
becomes unreachable. The chain that avoids this has three links — `.left>.card` shrinks,
`.left>.card>.panel` scrolls what is left over, and no card opts out of shrinking. Author's controls
card did opt out (`flex:0 0 auto`), which at a 1280x600 window clipped the Load button entirely and
made the view unusable.

These checks pin the chain in the stylesheet, where the defect lived. They cannot observe layout —
only a browser can, and the fix was measured in one — so they guard the specific declaration that
regressed rather than the rendered result.
"""

from __future__ import annotations

import re
from pathlib import Path

import bajutsu

_TEMPLATES = Path(bajutsu.__file__).parent / "templates"
_SERVE_CSS = (_TEMPLATES / "serve.css").read_text(encoding="utf-8")
# Comments quote declarations verbatim (including this fix's own rationale), so strip them before
# matching or a quoted `flex:0 0 auto` would read as a live rule.
_NO_COMMENTS = re.sub(r"/\*.*?\*/", "", _SERVE_CSS, flags=re.DOTALL)


def _without_media_blocks(css: str) -> str:
    """`css` with every `@media` block removed, so only the unconditional rules remain.

    The narrow tier overrides some of these selectors for its single-column stack, where the tiler
    is skipped and the page itself scrolls. Only the desktop rules describe the clipped-tile-leaf
    layout this module is about, so a narrow-tier override must not be mistaken for one of them.
    """
    out: list[str] = []
    i = 0
    while True:
        opening = re.search(r"@media[^{]*\{", css[i:])
        if not opening:
            out.append(css[i:])
            return "".join(out)
        out.append(css[i : i + opening.start()])
        depth = 0
        j = i + opening.end() - 1
        while j < len(css):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        i = j + 1


_DESKTOP_RULES = _without_media_blocks(_NO_COMMENTS)


def _declarations(selector: str) -> str:
    """The declaration body of the one unconditional rule with this exact selector."""
    pattern = re.escape(selector).replace(r">", r"\s*>\s*")
    matches: list[str] = re.findall(rf"(?:^|[}}\n]){pattern}\s*{{([^}}]*)}}", _DESKTOP_RULES)
    assert matches, f"serve.css has no unconditional rule for `{selector}`"
    assert len(matches) == 1, f"serve.css declares `{selector}` {len(matches)} times; expected one"
    return matches[0]


def test_author_controls_card_may_shrink() -> None:
    """The controls card shrinks when `.left` is shorter than the card's natural height.

    `flex:0 0 auto` is the regression: with shrinking off the card holds its content height even
    when the leaf is shorter, so `.panel` never receives a bounded height to scroll inside and the
    surplus is clipped away. The basis stays `auto` — sizing to content is deliberate, so that Run
    and Load sit above Steps and the editor whenever there is room for them.
    """
    flex = re.search(r"\bflex\s*:\s*([^;}]+)", _declarations(".left>.card.au-controls-card"))
    assert flex, "the Author controls card sets no `flex`, so it inherits `.left>.card`'s `flex:1`"
    # Unpacking a shorthand written in any other arity would raise ValueError, which reads as a
    # broken test rather than the stylesheet change that actually caused it.
    parts = flex.group(1).split()
    assert len(parts) == 3, (
        f"expected the three-value `flex` shorthand on the controls card, got `{flex.group(1)}`"
    )
    grow, shrink, basis = parts
    assert (grow, basis) == ("0", "auto"), (
        f"the controls card should still size to its content (`0 … auto`), got `{flex.group(1)}`"
    )
    assert shrink != "0", (
        "the controls card refuses to shrink, so a short viewport clips it with no scrollbar "
        "and the Load button becomes unreachable (issue #1715)"
    )


def test_left_column_card_bounds_its_scroll_host() -> None:
    """`.left>.card` shrinks and drops its min-height, so the panel inside can be bounded.

    `min-height:0` is what lets a flex item shrink below its content at all; without it the card
    would clip exactly as the un-shrinkable controls card did, whatever its `flex-shrink`.
    """
    card = _declarations(".left>.card")
    assert re.search(r"\bmin-height\s*:\s*0\b", card), (
        "`.left>.card` must keep `min-height:0` or its children cannot be bounded (issue #1715)"
    )


def test_left_column_panel_is_the_scroll_host() -> None:
    """`.left>.card>.panel` scrolls its own overflow, so the clipping tile leaf never has to."""
    panel = _declarations(".left>.card>.panel")
    assert re.search(r"\boverflow(-y)?\s*:\s*auto\b", panel), (
        "`.left>.card>.panel` must scroll; otherwise overflow reaches `.tile-leaf`, which clips it"
    )
    assert re.search(r"\bmin-height\s*:\s*0\b", panel), (
        "`.left>.card>.panel` needs `min-height:0` to shrink below its content and scroll"
    )
