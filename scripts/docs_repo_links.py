"""MkDocs hook: rewrite links that escape ``docs/`` (and cross-language links) at build time.

Most ``docs/*.md`` pages link to files *outside* the published tree — ``../DESIGN.md``,
``../roadmaps/…``, ``../bajutsu/drivers/base.py`` — paths that resolve when GitHub renders the
source but that ``mkdocs build --strict`` rejects as unresolvable internal links. Rewriting those
links in the source itself would mean a large two-language diff and would replace GitHub-readable
relative links with absolute URLs in the very files we want to keep browseable on GitHub.

Instead this hook leaves the source untouched and rewrites at build time. Two cases:

1. **Escaping links** — a link is left alone when it resolves to a file that exists *inside*
   ``docs/`` (MkDocs handles it), and rewritten to ``https://github.com/…/blob/main/<path>``
   (``tree`` for a directory) otherwise. The decision is made by resolving the link against the
   page's path and testing the result on disk — never by counting ``../`` segments, because depth
   alone is ambiguous: ``../vision.md`` from ``docs/ja/concepts.md`` stays inside ``docs/`` while
   ``../roadmaps/…`` from the same page escapes it.
2. **Cross-language links** — the per-page ``[日本語](ja/foo.md)`` header link points into the
   ``docs/ja/`` subtree, which mkdocs-static-i18n consumes as the *translation* of ``foo.md`` rather
   than serving as a standalone page, so ``--strict`` cannot resolve it. These are rewritten to the
   published URL of the other-language page (an absolute ``site_url``-based link, which ``--strict``
   does not validate). The source keeps its relative link, so the header still works on GitHub.

The rewriting core (:func:`rewrite_links`) is a pure function unit-tested in the fast ``make test``
suite; :func:`on_page_markdown` is the thin MkDocs adapter. No LLM, no network — the only I/O is the
existence check against the working tree.

Limitation: the link parser stops a destination (or its optional title) at the first ``)``, so a
link whose URL or title contains a literal ``)`` is not handled — the docs use no such links, and a
balanced-parens parser is not worth the complexity for a case that does not arise.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Repository invariants the hook is wired to (like site_url, but with no clean single source in the
# mkdocs config to pull from): the GitHub org/repo, the published tree's directory, and the
# non-default language subtrees under it (mkdocs-static-i18n folder mode).
REPO_BASE = "https://github.com/bajutsu-e2e/bajutsu"
DOCS_DIR_NAME = "docs"
DOCS_PREFIX = f"{DOCS_DIR_NAME}/"
LANG_DIRS = ("ja",)

# A markdown link/image destination: ``](dest)`` or ``](dest "title")``, optionally ``](<dest>)``.
_INLINE_RE = re.compile(r"\]\(\s*(?P<dest>[^)\s]+)(?P<rest>\s+[^)]*)?\)")
# A link reference definition at the start of a line: ``[label]: dest "optional title"``.
_REFDEF_RE = re.compile(r"^(?P<head>\s{0,3}\[[^\]]+\]:\s+)(?P<dest>\S+)(?P<rest>.*)$", re.MULTILINE)
# A URL with an explicit scheme (http:, mailto:, tel:, …); never a repo-relative path.
_SCHEME_RE = re.compile(r"^[a-zA-Z][\w+.-]*:")


@dataclass(frozen=True)
class LinkContext:
    """The build facts the rewriter needs to resolve a link.

    Attributes:
        repo_root: Repository root, used to test whether a target exists under ``docs/``.
        site_url: The published site root (e.g. ``https://…/bajutsu/``), or ``None`` to skip
            cross-language rewriting (the unit tests that only exercise escaping links pass ``None``).
        directory_urls: MkDocs' ``use_directory_urls`` — picks ``page/`` vs ``page.html`` URLs.
    """

    repo_root: Path
    site_url: str | None = None
    directory_urls: bool = False


def _with_fragment(url: str, fragment: str) -> str:
    """Re-attach a ``#fragment`` (kept verbatim from the source link) to a rewritten URL."""
    return f"{url}#{fragment}" if fragment else url


def _lang_of(docs_rel: str) -> str | None:
    """The language subtree a ``docs/``-relative path belongs to, or ``None`` for the default."""
    first = docs_rel.split("/", 1)[0]
    return first if first in LANG_DIRS else None


def _cross_language_url(docs_rel: str, src_path: str, ctx: LinkContext) -> str | None:
    """The published URL for a link into a *different* (non-default) language subtree, else ``None``.

    Only links *into* a ``docs/ja/``-style subtree from a page outside it need this: a link into the
    default (root) tree validates normally. Returns ``None`` when there is no ``site_url`` to anchor
    an absolute link on.
    """
    target_lang = _lang_of(docs_rel)
    if target_lang is None or target_lang == _lang_of(src_path) or not ctx.site_url:
        return None
    # A page becomes page.html / page/ at publish; any other asset (an image, a YAML) is served at
    # its own path, so only a markdown target gets the page-URL treatment.
    if docs_rel.endswith(".md"):
        docs_rel = docs_rel[: -len(".md")] + ("/" if ctx.directory_urls else ".html")
    return f"{ctx.site_url.rstrip('/')}/{docs_rel}"


def _rewrite_dest(dest: str, src_path: str, ctx: LinkContext) -> str:
    """Rewrite one link destination, or return it unchanged when it should be left alone.

    Args:
        dest: The raw destination text (may be wrapped in ``<>`` and carry a ``#fragment``).
        src_path: The page's source path relative to ``docs/`` (e.g. ``ja/concepts.md``).
        ctx: The build facts (repo root, site URL, URL style).

    Returns:
        The destination, rewritten to an absolute URL when it escapes ``docs/`` or points at the
        other language's copy, otherwise unchanged.
    """
    inner = dest[1:-1] if dest.startswith("<") and dest.endswith(">") else dest
    # Leave alone: explicit schemes, site-absolute / protocol-relative paths, pure fragments, and
    # any leftover ``<…>`` (the literal ``BE-NNNN-<slug>`` placeholder in docs prose).
    if _SCHEME_RE.match(inner) or inner.startswith(("/", "#")) or "<" in inner or ">" in inner:
        return dest

    path, _, fragment = inner.partition("#")
    if not path:  # a bare ``#anchor`` on this page
        return dest

    repo_rel = posixpath.normpath(posixpath.join(DOCS_DIR_NAME, posixpath.dirname(src_path), path))
    if repo_rel.startswith(".."):  # escapes the repo root entirely — can't form a URL, leave it
        return dest

    target = ctx.repo_root / repo_rel
    under_docs = repo_rel == DOCS_DIR_NAME or repo_rel.startswith(DOCS_PREFIX)
    if under_docs and target.exists():
        cross = _cross_language_url(repo_rel[len(DOCS_PREFIX) :], src_path, ctx)
        if cross is None:
            return dest  # resolves inside the built tree, same language; let MkDocs handle it
        return _with_fragment(cross, fragment)

    kind = "tree" if target.is_dir() else "blob"
    return _with_fragment(f"{REPO_BASE}/{kind}/main/{repo_rel}", fragment)


def rewrite_links(markdown: str, src_path: str, *, ctx: LinkContext) -> str:
    """Rewrite every escaping or cross-language link in a page's markdown to an absolute URL.

    Args:
        markdown: The page's markdown source.
        src_path: The page's source path relative to ``docs/`` (forward slashes).
        ctx: The build facts (repo root, site URL, URL style).

    Returns:
        The markdown with escaping inline links, images, and reference definitions rewritten to
        GitHub URLs, and cross-language links rewritten to published-site URLs; links resolving
        inside ``docs/`` in the same language are left untouched.
    """

    def _inline(m: re.Match[str]) -> str:
        return f"]({_rewrite_dest(m.group('dest'), src_path, ctx)}{m.group('rest') or ''})"

    def _refdef(m: re.Match[str]) -> str:
        return f"{m.group('head')}{_rewrite_dest(m.group('dest'), src_path, ctx)}{m.group('rest')}"

    return _REFDEF_RE.sub(_refdef, _INLINE_RE.sub(_inline, markdown))


def on_page_markdown(markdown: str, *, page: Any, config: Any, **_: Any) -> str:
    """MkDocs hook entry point: rewrite repo-escaping and cross-language links before rendering.

    ``page.file.src_path`` is the source path relative to ``docs_dir`` (the i18n plugin keeps the
    language prefix, e.g. ``ja/concepts.md``), so it anchors link resolution correctly per language.
    """
    ctx = LinkContext(
        repo_root=Path(config["docs_dir"]).resolve().parent,
        site_url=config.get("site_url"),
        directory_urls=config.get("use_directory_urls", True),
    )
    return rewrite_links(markdown, page.file.src_path.replace("\\", "/"), ctx=ctx)
