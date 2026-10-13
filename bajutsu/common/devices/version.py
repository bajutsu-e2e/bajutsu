"""Version ranges in npm's node-semver grammar, for the device conditions a target declares (BE-0452).

A target's `runsOn.os` (iOS), `runsOn.apiLevel` (Android), and `runsOn.browser.version` (web) take
a range such as `>=17 <19`, `^17`, `33 - 35`, or `17 || >=19`. The grammar is npm's in full rather
than a notation of Bajutsu's own, so there is nothing Bajutsu-specific to explain; the one departure
is that prerelease tags are not used (see `VersionSpec.parse`).

Every version compares on three components. A shorter one gains zeros (`18.2` reads as `18.2.0`, an
API level `34` as `34.0.0`) and a longer one drops the rest (Chrome's `130.0.6723.31` reads as
`130.0.6723`), so a device's version and a range's bound always compare on the same footing.

This module is pure: it parses and compares, and lists the major versions a range covers. Forming
runs from those majors, and deciding what an open range means for a pool, belong to the run
resolution that reads it. Unlike `DeviceOS`, which deliberately has no ordering, a `Version` orders:
the comparison here selects devices by a declaration, and never branches behaviour per OS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Op = Literal["<", "<=", ">", ">=", "="]

# One version as a range writes it: up to three components, each a number or an x-wildcard, with an
# optional prerelease (rejected below) and build metadata (ignored, as npm ignores it).
_PART = r"(?:0|[1-9]\d*|[xX*])"
_XVERSION = (
    rf"v?(?P<major>{_PART})(?:\.(?P<minor>{_PART})(?:\.(?P<patch>{_PART}))?)?"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?"
)
_COMPARATOR_RE = re.compile(rf"(?P<op>~>|~|\^|<=|>=|<|>|=)?(?P<ver>{_XVERSION})")
_HYPHEN_RE = re.compile(
    rf"(?P<lo>{_XVERSION.replace('?P<', '?P<lo_')})\s+-\s+(?P<hi>{_XVERSION.replace('?P<', '?P<hi_')})"
)
# npm allows whitespace between an operator and its version (`>= 1.2`); joining them first lets the
# comparator set split on whitespace alone.
_OP_SPACE_RE = re.compile(r"(~>|~|\^|<=|>=|<|>|=)\s+")
# The leading numeric components of an observed version string; anything after them (a prerelease
# tag, a build suffix, a fourth component) is not part of the comparison.
_OBSERVED_RE = re.compile(r"\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


@dataclass(frozen=True, order=True)
class Version:
    """A version reduced to the three components every comparison uses."""

    major: int
    minor: int = 0
    patch: int = 0

    @classmethod
    def parse(cls, text: str) -> Version:
        """Read a device's observed version, such as `18.2`, `34`, or `130.0.6723.31`.

        A shorter version gains zeros and a longer one drops the rest. A suffix after the numbers,
        such as a beta runtime's prerelease tag, is ignored, so a beta compares by its numbers alone.

        Raises:
            ValueError: The text does not start with a version number.
        """
        match = _OBSERVED_RE.match(text)
        if match is None:
            raise ValueError(f"not a version: {text!r}")
        major, minor, patch = (int(g) if g is not None else 0 for g in match.groups())
        return cls(major, minor, patch)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class _Bound:
    version: Version
    inclusive: bool


@dataclass(frozen=True)
class VersionRange:
    """One side of a `||` union: every comparator of a set, intersected into one interval.

    `lower` / `upper` are None where the set states no bound on that side. `empty` marks a set no
    version satisfies, such as `>5 <3` or npm's `<*`.
    """

    lower: _Bound | None
    upper: _Bound | None
    empty: bool = False

    @property
    def bounded(self) -> bool:
        """Whether the range states a bound on both sides, such as `>=17 <19`, `18`, or `^17`.

        A range open on either side, such as `>=18`, `<19`, or `*`, says where a scenario applies
        rather than which runs must happen; the run resolution treats the two kinds differently.
        """
        return self.empty or (self.lower is not None and self.upper is not None)

    def contains(self, version: Version) -> bool:
        """Whether `version` satisfies every comparator of this set."""
        if self.empty:
            return False
        if self.lower is not None:
            lo = self.lower
            if version < lo.version or (version == lo.version and not lo.inclusive):
                return False
        if self.upper is not None:
            hi = self.upper
            if version > hi.version or (version == hi.version and not hi.inclusive):
                return False
        return True

    def majors(self) -> tuple[int, ...]:
        """The major versions this range covers, in ascending order.

        A major counts as covered when at least one version within it satisfies the range, so
        `>=17 <19` covers 17 and 18, and `<=18.2` would cover 18 too.

        Raises:
            ValueError: The range is open on a side, so the majors it covers are unbounded or start
                from an arbitrary 0; such a range takes its majors from the devices available.
        """
        if self.empty:
            return ()
        if self.lower is None or self.upper is None:
            raise ValueError("an open range covers no fixed set of major versions")
        first = self.lower.version.major
        hi = self.upper
        # An exclusive upper bound at `M.0.0` excludes every version of major M; any other upper
        # bound leaves at least `M.0.0` inside it.
        if not hi.inclusive and hi.version == Version(hi.version.major):
            last = hi.version.major - 1
        else:
            last = hi.version.major
        return tuple(range(first, last + 1))


def _intersect(comparators: list[tuple[Op, Version]]) -> VersionRange:
    lower: _Bound | None = None
    upper: _Bound | None = None
    for op, v in comparators:
        if op in (">", ">=", "="):
            cand = _Bound(v, op != ">")
            # The tighter lower bound wins: the higher version, or the exclusive one at a tie.
            if lower is None or (cand.version, not cand.inclusive) > (
                lower.version,
                not lower.inclusive,
            ):
                lower = cand
        if op in ("<", "<=", "="):
            cand = _Bound(v, op != "<")
            if upper is None or (cand.version, cand.inclusive) < (upper.version, upper.inclusive):
                upper = cand
    empty = False
    if lower is not None and upper is not None:
        # Versions are integer triples, so `>a.b.c` admits nothing below `a.b.(c+1)`; comparing
        # that least admitted version makes `>18.9.0 <18.9.1` empty rather than a sliver.
        lo = lower.version
        least = lo if lower.inclusive else Version(lo.major, lo.minor, lo.patch + 1)
        empty = least > upper.version or (least == upper.version and not upper.inclusive)
    return VersionRange(lower, upper, empty)


# A partial version as written: the numbers present, with `None` for an x-wildcard or an absent part.
_Partial = tuple[int | None, int | None, int | None]


def _partial(
    major: str, minor: str | None, patch: str | None, pre: str | None, text: str
) -> _Partial:
    if pre is not None:
        raise ValueError(
            f"version range {text!r} names a prerelease ({pre!r}); prerelease tags are not used"
        )
    parts: list[int | None] = []
    wild = False
    for g in (major, minor, patch):
        # npm reads every component after a wildcard as a wildcard too (`1.x.3` is `1.x`).
        if g is None or g in ("x", "X", "*"):
            wild = True
        parts.append(None if wild or g is None else int(g))
    return (parts[0], parts[1], parts[2])


def _desugar(op: str, p: _Partial) -> list[tuple[Op, Version]] | None:
    """Expand one comparator to plain ones, following node-semver; None means it matches nothing."""
    major, minor, patch = p
    if op == "~>":
        op = "~"
    if major is None:
        # `*`, `x`, `>=*`, `<=*`, `~*`, and `^*` match every version; `<*` and `>*` match none.
        return None if op in ("<", ">") else []
    if op == "~":
        lo = Version(major, minor or 0, patch or 0)
        hi = Version(major + 1) if minor is None else Version(major, minor + 1)
        return [(">=", lo), ("<", hi)]
    if op == "^":
        lo = Version(major, minor or 0, patch or 0)
        if major != 0 or minor is None:
            hi = Version(major + 1)
        elif minor != 0 or patch is None:
            hi = Version(0, minor + 1)
        else:
            hi = Version(0, 0, patch + 1)
        return [(">=", lo), ("<", hi)]
    if op in ("", "="):
        if minor is None:
            return [(">=", Version(major)), ("<", Version(major + 1))]
        if patch is None:
            return [(">=", Version(major, minor)), ("<", Version(major, minor + 1))]
        return [("=", Version(major, minor, patch))]
    # A comparison operator over a partial version rounds to the edge of the wildcard: `>1.2`
    # means past every 1.2.x, so `>=1.3.0`, and `<=1` means up to every 1.x, so `<2.0.0`.
    floor = Version(major, minor or 0, patch or 0)
    if patch is None and op in (">", "<="):
        past = Version(major + 1) if minor is None else Version(major, minor + 1)
        return [(">=" if op == ">" else "<", past)]
    comparison: dict[str, Op] = {"<": "<", "<=": "<=", ">": ">", ">=": ">="}
    return [(comparison[op], floor)]


def _hyphen(lo: _Partial, hi: _Partial) -> list[tuple[Op, Version]]:
    out: list[tuple[Op, Version]] = []
    if lo[0] is not None:
        out.append((">=", Version(lo[0], lo[1] or 0, lo[2] or 0)))
    if hi[0] is not None:
        if hi[1] is None:
            out.append(("<", Version(hi[0] + 1)))
        elif hi[2] is None:
            out.append(("<", Version(hi[0], hi[1] + 1)))
        else:
            out.append(("<=", Version(hi[0], hi[1], hi[2])))
    return out


def _parse_set(part: str, text: str) -> VersionRange:
    part = part.strip()
    hyphen = _HYPHEN_RE.fullmatch(part)
    if hyphen is not None:
        g = hyphen.groupdict()
        lo = _partial(g["lo_major"], g["lo_minor"], g["lo_patch"], g["lo_pre"], text)
        hi = _partial(g["hi_major"], g["hi_minor"], g["hi_patch"], g["hi_pre"], text)
        return _intersect(_hyphen(lo, hi))
    comparators: list[tuple[Op, Version]] = []
    for token in _OP_SPACE_RE.sub(r"\1", part).split():
        match = _COMPARATOR_RE.fullmatch(token)
        if match is None:
            raise ValueError(f"invalid version range {text!r}: cannot read {token!r}")
        g = match.groupdict()
        p = _partial(g["major"], g["minor"], g["patch"], g["pre"], text)
        expanded = _desugar(g["op"] or "", p)
        if expanded is None:
            return VersionRange(None, None, empty=True)
        comparators.extend(expanded)
    return _intersect(comparators)


@dataclass(frozen=True)
class VersionSpec:
    """A parsed version range: a union of comparator sets, as npm's node-semver reads it.

    `alternatives` holds one `VersionRange` per side of `||`, in the order written, so a caller that
    treats a bounded side and an open side differently can read each by its own rule.
    """

    text: str
    alternatives: tuple[VersionRange, ...]

    @classmethod
    def parse(cls, text: str) -> VersionSpec:
        """Parse a range in npm's grammar: comparators, x-ranges, hyphen, tilde, caret, and `||`.

        Args:
            text: The range as written, such as `>=17 <19` or `17 || >=19`. An empty string, like
                `*`, matches every version.

        Raises:
            ValueError: The text is not a range, or names a prerelease such as `18.0.0-beta`.
                Prerelease tags are not used: a device's beta runtime compares by its numbers alone,
                so a range naming one could never select what it appears to.
        """
        alternatives = tuple(_parse_set(part, text) for part in text.split("||"))
        return cls(text, alternatives)

    def contains(self, version: Version | str | int) -> bool:
        """Whether `version` satisfies any side of the union.

        Args:
            version: A `Version`; an observed version string read through `Version.parse`; or an
                integer such as an Android API level, read as that major version.
        """
        if isinstance(version, int):
            v = Version(version)
        elif isinstance(version, str):
            v = Version.parse(version)
        else:
            v = version
        return any(alt.contains(v) for alt in self.alternatives)

    @property
    def bounded(self) -> bool:
        """Whether every side of the union is bounded on both sides (see `VersionRange.bounded`)."""
        return all(alt.bounded for alt in self.alternatives)

    def majors(self) -> tuple[int, ...]:
        """The major versions the bounded sides of the range cover, ascending and without repeats.

        An open side contributes nothing here: which majors it covers depends on the devices
        available, so the caller takes them from the pool. `17 || >=19` therefore lists `(17,)`.
        """
        covered = {m for alt in self.alternatives if alt.bounded for m in alt.majors()}
        return tuple(sorted(covered))

    def __str__(self) -> str:
        return self.text
