"""Tests for scripts/docs_repo_links.py — the MkDocs link-rewriting hook.

The hook rewrites links that escape ``docs/`` into absolute repository URLs so ``mkdocs build
--strict`` stays green, while leaving links that resolve inside the built tree untouched. The
decision is existence-based, not depth-based, so these tests pin both sides over a throwaway tree:
in-docs links (including a same-language sibling reached via ``../``) are preserved; escaping links,
images, and reference definitions are rewritten, with anchors kept and directories pointing at
``tree`` rather than ``blob``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "docs_repo_links.py"
_spec = importlib.util.spec_from_file_location("docs_repo_links", _MODULE_PATH)
assert _spec and _spec.loader
drl = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = drl
_spec.loader.exec_module(drl)

_BLOB = "https://github.com/bajutsu-e2e/bajutsu/blob/main"
_TREE = "https://github.com/bajutsu-e2e/bajutsu/tree/main"


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """A minimal repo tree: a few in-docs pages plus the out-of-docs files links point at."""
    for rel in (
        "docs/concepts.md",
        "docs/vision.md",
        "docs/scenarios.md",
        "docs/recording.md",
        "docs/README.md",
        "docs/ai-development.md",
        "docs/ja/concepts.md",
        "docs/ja/README.md",
        "docs/ja/assets/logo.png",
        "docs/api/runner.md",
        "DESIGN.md",
        "roadmaps/README.md",
        "roadmaps/proposals/BE-0015-x/BE-0015-x.md",
        "assets/icons/logo.png",
    ):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()
    (tmp_path / "demos/features/app/scenarios").mkdir(parents=True)
    return tmp_path


# (input, src_path, expected) — expected == input means "left untouched".
_CASES = [
    # Escaping links are rewritten to absolute blob URLs.
    ("[d](../DESIGN.md)", "concepts.md", f"[d]({_BLOB}/DESIGN.md)"),
    ("[d](../../DESIGN.md)", "ja/concepts.md", f"[d]({_BLOB}/DESIGN.md)"),
    ("[r](../../roadmaps/README.md)", "ja/concepts.md", f"[r]({_BLOB}/roadmaps/README.md)"),
    # Anchors and titles ride along.
    (
        "[r](../roadmaps/README.md#platform)",
        "concepts.md",
        f"[r]({_BLOB}/roadmaps/README.md#platform)",
    ),
    ('[d](../DESIGN.md "the design")', "concepts.md", f'[d]({_BLOB}/DESIGN.md "the design")'),
    # Angle-bracketed destinations.
    ("[d](<../DESIGN.md>)", "concepts.md", f"[d]({_BLOB}/DESIGN.md)"),
    # A directory target points at the tree view, not blob.
    (
        "[s](../demos/features/app/scenarios/)",
        "concepts.md",
        f"[s]({_TREE}/demos/features/app/scenarios)",
    ),
    # Images escape the same way.
    ("![logo](../assets/icons/logo.png)", "concepts.md", f"![logo]({_BLOB}/assets/icons/logo.png)"),
    # Reference definitions are rewritten too.
    (
        "[BE-0015]: ../roadmaps/proposals/BE-0015-x/BE-0015-x.md",
        "ai-development.md",
        f"[BE-0015]: {_BLOB}/roadmaps/proposals/BE-0015-x/BE-0015-x.md",
    ),
    # In-docs links are left for MkDocs to resolve.
    ("[s](scenarios.md)", "concepts.md", "[s](scenarios.md)"),
    ("[r](recording.md#foo)", "concepts.md", "[r](recording.md#foo)"),
    ("[r](api/runner.md)", "concepts.md", "[r](api/runner.md)"),
    # A same-language sibling reached via ``../`` stays inside docs/ (the depth-vs-existence case).
    ("[v](../vision.md)", "ja/concepts.md", "[v](../vision.md)"),
    ("[en](../README.md)", "ja/README.md", "[en](../README.md)"),
    ("[ja](ja/README.md)", "README.md", "[ja](ja/README.md)"),
    # External schemes, absolute paths, and bare fragments are never touched.
    ("[x](https://example.com)", "concepts.md", "[x](https://example.com)"),
    ("[x](mailto:a@b.com)", "concepts.md", "[x](mailto:a@b.com)"),
    ("[x](/foo)", "concepts.md", "[x](/foo)"),
    ("[x](#frag)", "concepts.md", "[x](#frag)"),
    # The literal ``BE-NNNN-<slug>`` placeholder in docs prose is left alone (it is not a real path).
    (
        "[BE-NNNN]: roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>.md",
        "ai-development.md",
        "[BE-NNNN]: roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>.md",
    ),
]


@pytest.mark.parametrize(("text", "src_path", "expected"), _CASES)
def test_rewrite_links(repo_root: Path, text: str, src_path: str, expected: str) -> None:
    # No site_url: cross-language rewriting is off, so the in-docs cases above stay untouched.
    ctx = drl.LinkContext(repo_root=repo_root)
    assert drl.rewrite_links(text, src_path, ctx=ctx) == expected


def test_a_page_in_docs_but_missing_file_is_rewritten(repo_root: Path) -> None:
    """A link resolving under docs/ to a file that does not exist still escapes the build, so it is
    rewritten rather than left to fail ``--strict``."""
    ctx = drl.LinkContext(repo_root=repo_root)
    out = drl.rewrite_links("[m](missing-page.md)", "concepts.md", ctx=ctx)
    assert out == f"[m]({_BLOB}/docs/missing-page.md)"


_SITE = "https://bajutsu-e2e.github.io/bajutsu/"


@pytest.mark.parametrize(
    ("text", "src_path", "expected"),
    [
        # A link from a default-language page into the ja/ subtree points at the published JA page.
        ("[日本語](ja/concepts.md)", "concepts.md", f"[日本語]({_SITE}ja/concepts.html)"),
        ("[ja](ja/concepts.md#foo)", "concepts.md", f"[ja]({_SITE}ja/concepts.html#foo)"),
        # A non-markdown asset under ja/ keeps its own path (no page-URL .html treatment).
        ("[fig](ja/assets/logo.png)", "concepts.md", f"[fig]({_SITE}ja/assets/logo.png)"),
        # A link into the default (root) tree validates normally and is left relative.
        ("[en](../scenarios.md)", "ja/concepts.md", "[en](../scenarios.md)"),
        # A same-language in-docs link is untouched.
        ("[v](../vision.md)", "ja/concepts.md", "[v](../vision.md)"),
    ],
)
def test_cross_language_links(repo_root: Path, text: str, src_path: str, expected: str) -> None:
    ctx = drl.LinkContext(repo_root=repo_root, site_url=_SITE, directory_urls=False)
    assert drl.rewrite_links(text, src_path, ctx=ctx) == expected


def test_directory_urls_uses_trailing_slash(repo_root: Path) -> None:
    ctx = drl.LinkContext(repo_root=repo_root, site_url=_SITE, directory_urls=True)
    out = drl.rewrite_links("[ja](ja/concepts.md)", "concepts.md", ctx=ctx)
    assert out == f"[ja]({_SITE}ja/concepts/)"
