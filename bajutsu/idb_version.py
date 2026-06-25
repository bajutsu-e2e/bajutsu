"""Capture and pin the idb version a run is driven against (BE-0005).

idb is the only on-device backend, so the whole on-device path rests on the `idb` client and
the `idb_companion` binary, each maintained on its own cadence. A new runtime an older companion
can't drive, or a companion upgrade that reshapes `describe-all` JSON, breaks a run without any
Bajutsu change. This module makes the version a recorded, comparable input: `probe` reads what's
installed (degrading to None where idb isn't present — it's provenance, never a pass/fail gate),
and `satisfies` checks it against a config-declared range so `doctor` can flag a mismatch up front.

Version comparison is a dependency-free numeric compare: idb versions are simple dotted numbers,
so a tuple-of-ints comparison is exact and avoids leaning on a transitive `packaging`.
"""

from __future__ import annotations

import operator
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

RunFn = Callable[[list[str]], str]

_VERSION_RE = re.compile(r"\d+(?:\.\d+)+")
# A constraint is an operator (longest-first so `>=` wins over `>`) and a dotted version.
_CONSTRAINT_RE = re.compile(r"^(>=|<=|==|>|<)\s*(\d+(?:\.\d+)*)$")


@dataclass(frozen=True)
class IdbVersions:
    """The installed idb versions, captured as run provenance. None = not present / unreadable."""

    companion: str | None
    client: str | None


def parse_version(text: str) -> str | None:
    """Pull the dotted version number out of a tool's `--version` line, or None if absent."""
    m = _VERSION_RE.search(text)
    return m.group(0) if m else None


def _as_tuple(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(p) for p in version.split("."))
    except ValueError:
        return None


_OPS = {">=": operator.ge, "<=": operator.le, "==": operator.eq, ">": operator.gt, "<": operator.lt}


def is_valid_spec(spec: str) -> bool:
    """Whether every comma-separated constraint in `spec` parses (e.g. ">=1.1.0,<2.0.0").

    The config layer validates a declared pin with this so a typo fails loudly at load,
    not as a later crash.
    """
    return all(_CONSTRAINT_RE.match(c.strip()) is not None for c in spec.split(","))


def satisfies(installed: str, spec: str) -> bool:
    """Whether `installed` meets every comma-separated constraint in `spec` (e.g. ">=1.1.0,<2.0.0").

    A version that can't be parsed never satisfies a pin — we fail the comparison rather than guess.
    """
    got = _as_tuple(installed)
    if got is None:
        return False
    for raw in spec.split(","):
        m = _CONSTRAINT_RE.match(raw.strip())
        if m is None:
            raise ValueError(f"invalid idb version constraint: {raw.strip()!r}")
        op, want_raw = m.group(1), m.group(2)
        want = _as_tuple(want_raw)
        assert want is not None  # the regex admits only dotted integers
        # Pad to equal length so 1.1 and 1.1.0 compare equal, then lean on tuple ordering.
        n = max(len(got), len(want))
        if not _OPS[op](got + (0,) * (n - len(got)), want + (0,) * (n - len(want))):
            return False
    return True


def _version_run(args: list[str]) -> str:
    # Deliberately not the driver's `_real_run`: a `--version` banner may land on stderr, and some
    # CLIs exit non-zero for it, so merge both streams and don't `check` — we only want whatever
    # version string was emitted. Probing never raises on a non-zero exit (a missing tool still does,
    # and `_version_of` turns that into None).
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    return proc.stdout + proc.stderr


def _version_of(args: list[str], run: RunFn) -> str | None:
    # Any failure to read a version (tool absent, unparseable output) is reported as unknown rather
    # than raised: the version is recorded provenance, so a missing idb must not break a run/`doctor`.
    try:
        return parse_version(run(args))
    except (OSError, subprocess.SubprocessError):
        return None


def probe(run: RunFn = _version_run) -> IdbVersions:
    """Read the installed `idb_companion` and `idb` client versions (None where unavailable)."""
    return IdbVersions(
        companion=_version_of(["idb_companion", "--version"], run),
        client=_version_of(["idb", "--version"], run),
    )
