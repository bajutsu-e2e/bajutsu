"""The filesystem run root is named in one place (BE-0331 unit 3).

The import contract in `pyproject.toml` keeps every module but `bajutsu.evidence.sink` from reaching
`bajutsu.run_root`, the provider that derives a writable run directory. That leaves one way to reach
a run directory without the provider: rebuild its path from the literal. This check closes it, so the
two halves together state a property of the whole source tree rather than of a curated list of
writers — a module that does not exist yet is covered the moment it does.

Scoped to *deriving* a filesystem path, which is what confers the writable handle. `serve`'s `/runs/`
route prefix and its run-id pattern are URL and stdout shapes, and the `runs` database table is a
table; none of them can be written into, and none of them matches.
"""

from __future__ import annotations

import re
from pathlib import Path

import bajutsu
from bajutsu.run_files import DEFAULT_RUNS_DIR

_PACKAGE = Path(bajutsu.__file__).parent
# The one module allowed to name the root: it defines `DEFAULT_RUNS_DIR`, which every other module
# imports instead of repeating the literal.
_PROVIDER = _PACKAGE / "run_files.py"

# Built from the constant, so renaming the default root moves the check with it rather than leaving
# it hunting a stale word. Three shapes derive a filesystem path from the literal — `Path("runs")`
# (and `Path("runs/…")`), `x / "runs"`, and `"runs" / x` — plus a CLI flag whose default *is* the
# root. A quote must sit against the `/` or open the `Path(` call, which is what keeps a URL
# (`"/runs/{id}"`), a regex, a `runs` table name, and prose in a docstring out of the match.
_ROOT = re.escape(DEFAULT_RUNS_DIR)
_RUN_ROOT_LITERAL = re.compile(
    rf"""Path\(\s*(['"]){_ROOT}(?:/[^'"]*)?\1"""
    rf"""|/\s*(['"]){_ROOT}\2"""
    rf"""|(['"]){_ROOT}\3\s*/"""
    rf"""|typer\.Option\(\s*(['"]){_ROOT}\4"""
)


def test_no_module_but_the_provider_derives_the_run_root_from_a_literal() -> None:
    checked = 0
    for path in sorted(_PACKAGE.rglob("*.py")):
        if path == _PROVIDER:
            continue
        checked += 1
        source = path.read_text(encoding="utf-8")
        match = _RUN_ROOT_LITERAL.search(source)
        assert match is None, (
            f"{path.relative_to(_PACKAGE.parent)} derives the run root from the literal "
            f"{match.group(0)!r} (line {source[: match.start()].count('\n') + 1}); import "
            f"DEFAULT_RUNS_DIR / runs_root() from bajutsu.run_files instead (BE-0331)"
        )
    # A floor, so a rewrite that leaves the scan reaching no source fails here rather than passing
    # vacuously. Raise it freely; it only records that the check still has teeth.
    assert checked >= 250, f"only {checked} modules scanned — has the package layout changed?"


def test_the_literal_pattern_matches_a_derivation_and_nothing_else() -> None:
    # The check is worth only what its pattern distinguishes, and both halves matter: missing a real
    # derivation lets a writer rebuild the path, and matching a table name or a URL would push the
    # next author into working around the gate.
    for derivation in (
        'out_dir = Path("runs") / new_run_id()',
        "screenmap = Path('runs/latest')",
        'run_dir = work / "runs" / run_id',
        'runs: str = typer.Option("runs", "--runs", help="runs root")',
    ):
        assert _RUN_ROOT_LITERAL.search(derivation), derivation
    for benign in (
        '__tablename__ = "runs"',
        'op.add_column("runs", sa.Column("deleted_at"))',
        '_RUN_ID_RE = re.compile(r"^runs/([A-Za-z0-9_.-]+)/")',
        'app.mount("/runs/{run_id}", artifacts)',
        'raw_runs = body.get("runs") or []',
        '"""A run lands as `runs/<run_id>/manifest.json`."""',
        '_PACKAGE_EXCLUDES = frozenset({".git", "runs", "tmp"})',
    ):
        assert not _RUN_ROOT_LITERAL.search(benign), benign
