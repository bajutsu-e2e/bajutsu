# /// script
# requires-python = ">=3.13"
# dependencies = ["sudachipy>=0.6.9", "sudachidict-core>=20240409"]
# ///
"""Report candidate 敬体 register drift in Japanese docs (BE-0434).

``CLAUDE.md`` requires 敬体 (ですます調) for every roadmap ``*-ja.md`` item and every page under
``docs/ja/``. This script lists each sentence whose final predicate carries no 敬体 marker, grouped
by file and line, so a contributor converting a file can find every line to touch — and confirm
afterwards that only the candidates they kept on purpose remain.

Detection works by exclusion, not enumeration: a sentence passes when the auxiliary chain at its end
holds a です / ます morpheme (matched by lemma) or ends in ください; anything else is a candidate.
Listing 常体 endings instead never converged (GitHub Issue #1842). The analyzer over-flags on
purpose — a 体言止め label is a candidate too — and a human discards the legitimate ones.

It is a review aid, not a gate: it always exits 0 once it has scanned, and stays out of
``make check``, because the corpus still carries thousands of candidates. The morphological analyzer
(``sudachipy``) is an ephemeral dependency declared in the PEP 723 block above, so ``uv run`` builds
an isolated environment for it and the project's own dependencies never change. Deterministic and
offline, with no model anywhere near it.

Usage::

    uv run scripts/ja_register_check.py                      # the whole ja corpus
    uv run scripts/ja_register_check.py roadmaps/BE-0089-*/  # one item
    uv run scripts/ja_register_check.py --summary docs/ja    # per-file counts only
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Morpheme:
    """One analyzer token, reduced to the fields the register check reads."""

    surface: str
    lemma: str
    normalized: str
    pos: tuple[str, ...]


# The seam in front of the analyzer: tests pass a fake, and only `main` loads sudachipy.
Tokenizer = Callable[[str], list[Morpheme]]


@dataclass(frozen=True)
class Candidate:
    """A sentence whose final predicate carries no 敬体 marker."""

    line: int
    sentence: str


@dataclass
class _Block:
    """One paragraph or list item, its hard-wrapped lines joined, with each char's source line."""

    text: str = ""
    line_of: list[int] = field(default_factory=list)

    def append(self, segment: str, line: int) -> None:
        segment = segment.strip()
        if not segment:
            return
        # Japanese needs no space at a wrap; only two ASCII chars meeting across one do.
        if self.text and self.text[-1].isascii() and segment[0].isascii():
            self.text += " "
            self.line_of.append(line)
        self.text += segment
        self.line_of.extend([line] * len(segment))


_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_CHECKBOX = re.compile(r"^\[[ xX]\]")
_LINK_DEF = re.compile(r"^\[[^\]]+\]:\s")
_HEADING = re.compile(r"^(#{1,6})(?:\s|$)")
# A line opening with a block-level HTML tag. Autolinks (`<https://…>`) and inline tags such as
# `<kbd>` open prose lines too, so a bare leading `<` is not enough to drop one; a block tag name
# never starts either. Such a line is still prose when text sits between its tags (`<p>本文。</p>`).
_HTML_BLOCK = re.compile(
    r"^</?(details|summary|div|p|img|picture|source|video|table|thead|tbody|tr|td|th|br|hr"
    r"|section|figure|figcaption|center)\b",
    re.IGNORECASE,
)
_FENCE = re.compile(r"^(`{3,}|~{3,})")
# Lines arrive one at a time and the scanner carries a multi-line comment as state, so DOTALL
# changes nothing here; it keeps the pattern correct if a caller ever hands it a joined block.
_INLINE_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_CODE_SPAN = re.compile(r"`[^`]*`")
_TAG = re.compile(r"<[^>]+>")
# The `[English](…) · **日本語**` language switch atop every bilingual page: navigation, not prose.
_NAV_ITEM = r"(?:\*\*[^*]+\*\*|\[[^\]]+\]\([^)]*\))"
_NAV_LINE = re.compile(rf"^{_NAV_ITEM}(?:\s*·\s*{_NAV_ITEM})+$")
_PROGRESS_HEADINGS = frozenset({"Progress", "進捗"})


class _Scanner:
    """Split a Markdown file into prose blocks, dropping everything the register rule exempts.

    Excluded before any sentence is looked at: fenced code, tables, headings, block quotes, HTML,
    link definitions, the language-switch line, and the checklist bullets under a ``## Progress`` / ``## 進捗`` heading.
    """

    def __init__(self) -> None:
        self.blocks: list[_Block] = []
        self._current = _Block()
        self._fence: str | None = None  # the opening run of backticks or tildes
        self._in_comment = False
        self._in_progress = False
        self._in_checklist_item = False

    def feed(self, line: int, raw: str) -> None:
        remainder = self._skip_verbatim(raw)
        if remainder is None:
            return
        raw = remainder
        stripped = raw.strip()
        if not stripped or self._is_structural(stripped):
            self._flush()
            self._in_checklist_item = False
            return
        item = _LIST_ITEM.match(raw)
        if item:
            self._flush()
            body = raw[item.end() :]
            self._in_checklist_item = self._in_progress and bool(_CHECKBOX.match(body))
            if not self._in_checklist_item:
                self._current.append(body, line)
            return
        if not self._in_checklist_item:
            self._current.append(stripped, line)

    def finish(self) -> list[_Block]:
        self._flush()
        return self.blocks

    def _skip_verbatim(self, raw: str) -> str | None:
        """Consume code fences and HTML comments, which may span many lines.

        Returns the part of the line left to scan as prose, or None when all of it is verbatim.
        """
        stripped = raw.strip()
        if self._fence is not None:
            # CommonMark closes a fence only on a bare run of the same char, at least as long.
            if stripped.rstrip(self._fence[0]) == "" and len(stripped) >= len(self._fence):
                self._fence = None
            return None
        if self._in_comment:
            return self._after_comment(raw)
        fence = _FENCE.match(stripped)
        if fence:
            self._flush()
            self._fence = fence.group(1)
            return None
        if stripped.startswith("<!--"):
            self._flush()
            return self._after_comment(raw[raw.index("<!--") + 4 :])
        return self._drop_comments(raw)

    def _after_comment(self, raw: str) -> str | None:
        """Prose may follow a comment's `-->` on the same line; keep scanning it."""
        end = raw.find("-->")
        if end == -1:
            self._in_comment = True
            return None
        self._in_comment = False
        rest = self._drop_comments(raw[end + 3 :])
        return rest if rest.strip() else None

    def _drop_comments(self, raw: str) -> str:
        """Remove comments inside a prose line, and cut at one that opens here and runs on.

        A `<!--` inside inline code is text, not a comment, so the search runs on a copy with code
        spans blanked out, and the cuts are applied to the original at the same offsets.
        """
        masked = _CODE_SPAN.sub(lambda m: "_" * len(m.group()), raw)
        for match in reversed(list(_INLINE_COMMENT.finditer(masked))):
            raw = raw[: match.start()] + raw[match.end() :]
            masked = masked[: match.start()] + masked[match.end() :]
        opened = masked.find("<!--")
        if opened != -1:
            self._in_comment = True
            raw = raw[:opened]
        return raw

    def _is_structural(self, stripped: str) -> bool:
        heading = _HEADING.match(stripped)
        if heading:
            # A wrapped `#1842 …` line is prose: a heading needs whitespace after its hashes.
            if len(heading.group(1)) <= 2:
                self._in_progress = stripped[heading.end() :].strip() in _PROGRESS_HEADINGS
            return True
        return (
            stripped.startswith(("|", ">"))
            or bool(_LINK_DEF.match(stripped))
            or self._is_html_only(stripped)
            or bool(_NAV_LINE.match(stripped))
        )

    @staticmethod
    def _is_html_only(stripped: str) -> bool:
        """A block-tag line holding no text outside its tags, or a `<summary>` label."""
        tag = _HTML_BLOCK.match(stripped)
        if not tag:
            return False
        return tag.group(1).lower() == "summary" or not _TAG.sub("", stripped).strip()

    def _flush(self) -> None:
        if self._current.text:
            self.blocks.append(self._current)
        self._current = _Block()


def prose_blocks(text: str) -> list[_Block]:
    """Return the file's prose paragraphs and list items, hard wraps joined."""
    scanner = _Scanner()
    for number, raw in enumerate(text.splitlines(), start=1):
        scanner.feed(number, raw)
    return scanner.finish()


_OPENERS = "「『（"
_CLOSERS = "」』）"
_TERMINALS = "。．！？"


def split_sentences(block: _Block) -> list[Candidate]:
    """Split a block on the fullwidth terminal marks, never inside brackets or inline code.

    A tail with no terminal mark (a 体言止め label, most often) counts as one sentence. Returns each
    sentence with the line it starts on, as a ``Candidate`` used as a plain pair. A stray backtick or
    bracket would otherwise hide every sentence after it inside one, so an unbalanced block is split
    again with no tracking at all.
    """
    # The untracked fallback also splits inside the block's balanced groups, over-flagging
    # a fragment cut at a mark inside brackets — a noisier report beats a silently hidden sentence.
    sentences, balanced = _split(block, track=True)
    return sentences if balanced else _split(block, track=False)[0]


def _split(block: _Block, *, track: bool) -> tuple[list[Candidate], bool]:
    sentences: list[Candidate] = []
    text = block.text
    depth, in_code, start = 0, False, 0

    def emit(end: int) -> None:
        chunk = text[start:end]
        if chunk.strip():
            first = start + len(chunk) - len(chunk.lstrip())
            sentences.append(Candidate(block.line_of[first], chunk.strip()))

    for i, ch in enumerate(text):
        if track and ch == "`":
            in_code = not in_code
        elif track and not in_code and ch in _OPENERS:
            depth += 1
        elif track and not in_code and ch in _CLOSERS:
            depth = max(0, depth - 1)
        elif ch in _TERMINALS and depth == 0 and not in_code:
            # Emphasis closing right after the mark (`**…します。**`) belongs to this sentence.
            end = i + 1
            while end < len(text) and text[end] in "*_":
                end += 1
            emit(end)
            start = end
    emit(len(text))
    return sentences, depth == 0 and not in_code


_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
_TRAILING = " \t。．！？!?.*_~"
_PAIRS = {"）": "（", ")": "("}


def strip_trailing(sentence: str) -> str:
    """Drop Markdown links, terminal marks, and every trailing parenthetical group.

    A trailing item reference in brackets would otherwise hide the predicate before it from the morpheme walk.
    """
    text = _TAG.sub("", _LINK.sub(r"\1", _IMAGE.sub("", sentence)))
    while True:
        text = text.rstrip(_TRAILING)
        if not text or text[-1] not in _PAIRS:
            return text
        closer, opener = text[-1], _PAIRS[text[-1]]
        depth = 0
        for i in range(len(text) - 1, -1, -1):
            depth += (text[i] == closer) - (text[i] == opener)
            if depth == 0:
                # A sentence that is wholly a parenthetical is checked on what the brackets hold.
                text = text[:i] if text[:i].strip(" *_") else text[i + 1 : -1]
                break
        else:
            return text  # unbalanced: leave it rather than guess where the group opens


_SKIPPED_POS = frozenset({"補助記号", "空白", "記号"})
_KEITAI_LEMMAS = frozenset({"です", "ます"})
_REQUEST_FORMS = frozenset({"ください", "下さい"})


def is_keitai(morphemes: list[Morpheme]) -> bool:
    """Whether the sentence's final predicate is 敬体.

    Walks back past symbols and sentence-final particles (か, ね, よ), then accepts the sentence
    when the trailing auxiliary chain holds a です / ます lemma — ました splits into まし + た, so a
    suffix match would miss it — or the sentence ends in the request form ください (the plain
    くださる is 常体, so the surface is matched, not the lemma).
    """
    end = len(morphemes)
    while end and (
        morphemes[end - 1].pos[0] in _SKIPPED_POS
        or morphemes[end - 1].pos[:2] == ("助詞", "終助詞")
    ):
        end -= 1
    if end and morphemes[end - 1].surface in _REQUEST_FORMS:
        return True
    start = end
    while start and morphemes[start - 1].pos[0] == "助動詞":
        start -= 1
    return any(m.lemma in _KEITAI_LEMMAS for m in morphemes[start:end])


def find_candidates(text: str, tokenize: Tokenizer) -> list[Candidate]:
    """Every Japanese prose sentence in ``text`` whose final predicate is not 敬体."""
    found: list[Candidate] = []
    for block in prose_blocks(text):
        for sentence in split_sentences(block):
            core = strip_trailing(sentence.sentence)
            if _JAPANESE.search(core) and not is_keitai(tokenize(core)):
                found.append(sentence)
    return found


def in_scope(path: Path) -> bool:
    """A roadmap ``*-ja.md`` item or a page under ``docs/ja/`` — the scope the 敬体 rule names."""
    parts = path.parts
    under_docs_ja = any(parts[i : i + 2] == ("docs", "ja") for i in range(len(parts) - 1))
    return path.suffix == ".md" and (path.name.endswith("-ja.md") or under_docs_ja)


def collect_paths(args: Iterable[str]) -> list[Path]:
    """Expand directories to their in-scope Markdown; with no args, the whole ja corpus.

    Raises:
        FileNotFoundError: an argument names nothing on disk.
    """
    args = list(args)
    if not args:
        return sorted(REPO.glob("roadmaps/*/*-ja.md")) + sorted(REPO.glob("docs/ja/**/*.md"))
    paths: list[Path] = []
    for arg in args:
        path = Path(arg)
        if path.is_dir():
            paths.extend(p for p in sorted(path.rglob("*.md")) if in_scope(p))
        elif path.is_file():
            paths.append(path)
        else:
            raise FileNotFoundError(arg)
    return paths


def sudachi_tokenizer() -> Tokenizer:  # pragma: no cover - needs the ephemeral analyzer
    """Load sudachipy lazily: the dev environment never installs it (see the module docstring)."""
    try:
        from sudachipy import Dictionary, SplitMode
    except ImportError as exc:
        raise SystemExit(
            "sudachipy is not installed; run this through `uv run scripts/ja_register_check.py` "
            "(or `make ja-register-check`), which installs it from the script's PEP 723 block."
        ) from exc
    tokenizer = Dictionary(dict="core").tokenizer(mode=SplitMode.C)

    def tokenize(text: str) -> list[Morpheme]:
        return [
            Morpheme(
                m.surface(), m.dictionary_form(), m.normalized_form(), tuple(m.part_of_speech())
            )
            for m in tokenizer.tokenize(text)
        ]

    return tokenize


def _display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def report(paths: list[Path], tokenize: Tokenizer, *, summary: bool) -> str:
    """Render the candidates per file, then a total line."""
    out: list[str] = []
    total = files = 0
    for path in paths:
        found = find_candidates(path.read_text(encoding="utf-8"), tokenize)
        if not found:
            continue
        total, files = total + len(found), files + 1
        shown = _display(path)
        if not summary:
            out.extend(f"{shown}:{c.line}: {c.sentence}" for c in found)
        out.append(f"{shown}: {len(found)} candidate(s)")
    out.append(f"Total: {total} candidate(s) in {files} of {len(paths)} file(s)")
    return "\n".join(out)


def main(argv: list[str] | None = None, tokenize: Tokenizer | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("paths", nargs="*", help="files or directories (default: the ja corpus)")
    parser.add_argument("--summary", action="store_true", help="print per-file counts only")
    args = parser.parse_args(argv)
    try:
        paths = collect_paths(args.paths)
    except FileNotFoundError as exc:
        print(f"no such file or directory: {exc}", file=sys.stderr)
        return 2
    print(report(paths, tokenize or sudachi_tokenizer(), summary=args.summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
