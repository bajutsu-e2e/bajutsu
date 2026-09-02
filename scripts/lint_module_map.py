#!/usr/bin/env python3
"""Check that ``docs/architecture.md``'s module table still matches the ``bajutsu/`` package.

The "Module list and roles" table is the map a session reads before opening any code, and it is
hand-written prose: each row explains what a module is *for*, which no generator can produce. What
a generator can do is compare the table's row set against the tree, and the comparison is worth
running — when this check was first written the table named every subpackage but well under half
of the top-level modules, so a reader looking for ``adb.py``, ``screenshots.py``, or
``object_store.py`` found nothing and concluded, wrongly, that the map covered everything.

Three rules, in order of what the tree can support today:

1. Every name the table lists exists. A row pointing at a module that was renamed or removed sends
   a reader somewhere empty, which costs more than no row at all.
2. Every *top-level* subpackage appears, either as its own row or through one of the files
   inside it. A nested one (``cli/commands/``, ``serve/server/``) is not compared, because the
   table documents those subtrees in a row's role prose rather than in the cell this reads. The
   one exception is ``common/``: it holds no code of its own, only subpackages shared across
   features, so this descends one level into it and checks each ``common/<subpackage>/`` the
   same way it checks a top-level one — otherwise a single row anywhere under ``common/`` would
   satisfy this rule forever.
3. Every top-level module appears — except the ones in ``GRANDFATHERED`` below.

Rule 3 carries an allowlist because the modules missing when the check landed predate it, and
rewriting the table for all of them at once is a change no reviewer could check carefully. The
allowlist keeps the gap from growing: a *new* top-level module fails until the table describes it.
Delete an entry when its row lands, and delete the allowlist when the last one goes.

Run it with ``make lint-module-map`` (in ``make check``). Pure and offline — it reads the tree and
one Markdown file, with no network and no large language model (LLM).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ARCHITECTURE = Path("docs/architecture.md")
PACKAGE = Path("bajutsu")

# The table lives under this heading and ends at the next second-level heading.
_SECTION = "## Module list and roles"

# The first cell of a table row. A cell may name several modules, separated by "·", so every
# backticked token in it counts — `analysis/` · `serve/flakiness.py` documents both.
_ROW_RE = re.compile(r"^\|([^|]+)\|", re.MULTILINE)
_NAME_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_./]*)`")

# Top-level modules that predate this check. Every one is a real gap in the table, to be closed
# opportunistically by the next change that touches the module — not in one sweeping rewrite.
GRANDFATHERED = frozenset(
    {
        "adb.py",
        "adb_resident.py",
        "artifact_perms.py",
        "deprecations.py",
        "device_errors.py",
        "device_id.py",
        "device_os.py",
        "diagnostics.py",
        "dom.py",
        "elements.py",
        "handoff.py",
        "notify.py",
        "object_store.py",
        "record_capture.py",
        "run_files.py",
        "run_id.py",
        "run_root.py",
        "screenshots.py",
        "stall_diagnostics.py",
        "totp.py",
        "web_network.py",
        "webview.py",
        "zorder.py",
    }
)


def table_names(architecture: Path) -> set[str]:
    """The module and package names the architecture page's module table lists.

    Names are relative to the package root, exactly as the table writes them — ``drivers/base.py``
    for a module, ``scenario/`` for a package.

    Raises:
        ValueError: if the page has no "Module list and roles" section, which would otherwise make
            every rule pass against an empty table.
    """
    text = architecture.read_text(encoding="utf-8")
    if _SECTION not in text:
        raise ValueError(f"{architecture}: no '{_SECTION}' section found")
    section = text.split(_SECTION, 1)[1].split("\n## ", 1)[0]
    names: set[str] = set()
    for cell in _ROW_RE.findall(section):
        names.update(_NAME_RE.findall(cell))
    return names


def missing_from_tree(names: set[str], package: Path) -> list[str]:
    """The table's names that no longer exist in the package."""
    return sorted(name for name in names if not (package / name).exists())


def undocumented_packages(names: set[str], package: Path) -> list[str]:
    """The subpackages the table never mentions, by name or through a file inside them.

    ``common/`` is the one top-level package this descends into: it holds no code of its own,
    only a growing set of subpackages shared across features (``common/drivers/``,
    ``common/evidence/``, and so on, per the feature-first reorg), so a single mention of
    ``common`` would satisfy this rule forever and rule 2 would stop protecting almost anything —
    the exact regression this check was written against. Every other nested subpackage
    (``cli/commands/``, ``serve/server/``) stays undescended, per the docstring above.
    """
    mentioned = {name.split("/", 1)[0] for name in names if "/" in name}
    mentioned_under_common = {
        name.split("/", 2)[1]
        for name in names
        if name.startswith("common/") and name.count("/") >= 2
    }
    offenders = [
        f"{d.name}/"
        for d in package.iterdir()
        if d.is_dir()
        and d.name != "__pycache__"
        and any(d.glob("*.py"))
        and d.name not in mentioned
    ]
    common = package / "common"
    if common.is_dir():
        offenders.extend(
            f"common/{d.name}/"
            for d in common.iterdir()
            if d.is_dir()
            and d.name != "__pycache__"
            and any(d.glob("*.py"))
            and d.name not in mentioned_under_common
        )
    return sorted(offenders)


def undocumented_modules(names: set[str], package: Path) -> list[str]:
    """The top-level modules the table never mentions, minus the grandfathered ones.

    A dunder module (``__main__.py``) is a Python entry-point convention rather than a feature of
    the tool, so the table is not expected to describe one.
    """
    return sorted(
        p.name
        for p in package.glob("*.py")
        if not p.name.startswith("__") and p.name not in names and p.name not in GRANDFATHERED
    )


def stale_grandfathered(package: Path, names: set[str] | None = None) -> list[str]:
    """Allowlist entries that are gone, or already documented, so the allowlist cannot rot.

    An entry whose module has since earned a table row must leave the list. While it stays, rule 3
    no longer protects that module: deleting the row again would pass the gate, and the module
    would drop off the map a second time with nothing to catch it.
    """
    documented = names or set()
    return sorted(
        name for name in GRANDFATHERED if not (package / name).exists() or name in documented
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check the architecture page's module table.")
    parser.add_argument("--architecture", type=Path, default=ARCHITECTURE)
    parser.add_argument("--package", type=Path, default=PACKAGE)
    args = parser.parse_args(argv)

    if not args.package.is_dir():
        print(f"{args.package}: not a directory", file=sys.stderr)
        return 1
    try:
        names = table_names(args.architecture)
    except (OSError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1

    failures = 0
    for label, offenders, remedy in (
        (
            "names a module that does not exist",
            missing_from_tree(names, args.package),
            "remove or correct the row",
        ),
        (
            "never mentions these subpackages",
            undocumented_packages(names, args.package),
            "add a row describing what the package is for",
        ),
        (
            "never mentions these top-level modules",
            undocumented_modules(names, args.package),
            "add a row describing what the module is for",
        ),
        (
            "allowlists a module that is gone, or that the table now documents",
            stale_grandfathered(args.package, names),
            f"drop the entry from GRANDFATHERED in {Path(__file__).name}",
        ),
    ):
        if not offenders:
            continue
        failures += len(offenders)
        print(f"{args.architecture}: {label} — {remedy}:", file=sys.stderr)
        for name in offenders:
            print(f"  {name}", file=sys.stderr)

    if failures:
        print(f"lint-module-map: {failures} problem(s)", file=sys.stderr)
        return 1
    print(f"lint-module-map: {len(names)} table entries match {args.package}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
