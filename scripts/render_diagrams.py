#!/usr/bin/env python3
"""Render the docs' mermaid diagrams to checked-in SVGs (BE-XXXX).

A diagram lives in the markdown as a normal ```mermaid fence — the same source GitHub and
mkdocs-material both render live — preceded by a marker comment naming where its static render
goes:

    <!-- mermaid-svg: assets/diagrams/architecture-data-flow.svg -->
    ```mermaid
    flowchart TB
        ...
    ```

The marker's path is resolved relative to the markdown file's own directory and is exactly the
path the page's ``![alt](path)`` image reference should use, so English and Japanese pages (whose
diagrams usually carry different-language labels) each own their own path — nothing forces them to
share a file. The checked-in SVG is what actually renders on the page (consistent in GitHub's
markdown viewer and the built site, no client-side mermaid.js dependency); the mermaid fence stays
in a collapsed ``<details>`` alongside it purely so the diagram stays editable from the page.

This is a manual, opt-in step (``make docs-diagrams``) — not part of ``make check`` or ``make
docs`` — because it shells out to Node (``npx @mermaid-js/mermaid-cli``), which this
Python/uv-native repo does not otherwise depend on, and because mermaid-cli needs a one-time
headless Chrome fetch:

    npx --yes puppeteer browsers install chrome-headless-shell

Run it again whenever a mermaid fence changes; the SVGs are committed like any other doc asset
(``docs/assets/logo.png`` is the existing precedent), so a plain ``make docs`` never needs Node.

Usage::

    python scripts/render_diagrams.py            # render every marked diagram under docs/
    python scripts/render_diagrams.py FILE.md...  # only the diagrams in these files
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"

_MARKER_RE = re.compile(
    r"<!--\s*mermaid-svg:\s*(?P<path>\S+?)\s*-->\s*\n```mermaid\n(?P<source>.*?)\n```",
    re.DOTALL,
)


def _find_markdown_files(paths: list[str]) -> list[Path]:
    if paths:
        return [Path(p).resolve() for p in paths]
    return sorted(DOCS_DIR.rglob("*.md"))


def _render_one(source: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".mmd", delete=False) as f:
        f.write(source)
        mmd_path = Path(f.name)
    try:
        subprocess.run(
            [
                "npx",
                "--yes",
                "@mermaid-js/mermaid-cli",
                "-i",
                str(mmd_path),
                "-o",
                str(out_path),
                "-b",
                "white",
            ],
            check=True,
        )
    finally:
        mmd_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    rendered = 0
    for md_file in _find_markdown_files(argv):
        text = md_file.read_text(encoding="utf-8")
        for match in _MARKER_RE.finditer(text):
            out_path = (md_file.parent / match["path"]).resolve()
            print(f"{md_file.relative_to(REPO_ROOT)} -> {out_path.relative_to(REPO_ROOT)}")
            _render_one(match["source"], out_path)
            rendered += 1
    print(f"rendered {rendered} diagram(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
