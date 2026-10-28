"""The `eval` step's operation vocabulary: parsing, argument checks, and the type flow (BE-0472).

One parser serves both checks the design names. At load time it runs with `defer_tokens=True`: an
argument that still holds a `${...}` token cannot be judged yet, so only the operation's name (and
so the type flow) is checked for it. At run time the handler substitutes the tokens and parses again
with nothing deferred, so the same rule fails the step with the same message.

Every message names the operation's position and name and never quotes an argument or a value: an
argument may be a substituted `${secrets.*}`, and a step's failure reason reaches every report.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

ValueKind = Literal["string", "list"]
SortOrder = Literal["asc", "desc"]
FilterKind = Literal["matches", "equals", "contains", "notEmpty"]

_PLACEHOLDER_START = "${"
_INDEX = re.compile(r"\d+")


class EvalOpError(ValueError):
    """An `eval` operation the parser or the type flow rejects; the message carries no value."""


@dataclass(frozen=True)
class Split:
    """`split` — a string into a list, on a literal separator or a regular expression."""

    by: str
    regex: bool


@dataclass(frozen=True)
class Join:
    """`join` — a list into a string, with a separator that may be empty."""

    by: str


@dataclass(frozen=True)
class Filter:
    """`filter` — keep the elements one predicate accepts (or rejects, with `not`)."""

    kind: FilterKind
    arg: str
    negate: bool


@dataclass(frozen=True)
class Pick:
    """`pick` — one element, by `first` / `last` or a zero-based index."""

    at: Literal["first", "last"] | int


@dataclass(frozen=True)
class Sort:
    """`sort` — by Unicode code point, or by decimal value when `numeric`."""

    order: SortOrder
    numeric: bool


@dataclass(frozen=True)
class RegexOp:
    """`regex` — capture `group` of the first match."""

    pattern: str
    group: int


@dataclass(frozen=True)
class Replace:
    """`replace` — every occurrence of `find`, literal or a regular expression."""

    find: str
    with_: str
    regex: bool


@dataclass(frozen=True)
class Substring:
    """`substring` — the characters from `start` up to `end`, both counted from zero."""

    start: int
    end: int | None


@dataclass(frozen=True)
class Case:
    """`case` — the string in lower or upper case."""

    to: Literal["lower", "upper"]


@dataclass(frozen=True)
class Bare:
    """An operation with no arguments: `trim`, `json`, `count`, `unique`, `reverse`."""

    name: Literal["trim", "json", "count", "unique", "reverse"]


EvalOp = Split | Join | Filter | Pick | Sort | RegexOp | Replace | Substring | Case | Bare

# Input and output kind per operation: the table the load-time type flow walks.
SIGNATURES: Mapping[str, tuple[ValueKind, ValueKind]] = {
    "split": ("string", "list"),
    "join": ("list", "string"),
    "filter": ("list", "list"),
    "pick": ("list", "string"),
    "count": ("list", "string"),
    "sort": ("list", "list"),
    "unique": ("list", "list"),
    "reverse": ("list", "list"),
    "json": ("string", "list"),
    "regex": ("string", "string"),
    "replace": ("string", "string"),
    "substring": ("string", "string"),
    "trim": ("string", "string"),
    "case": ("string", "string"),
}
_BARE_NAMES = frozenset({"trim", "json", "count", "unique", "reverse", "sort"})
_FILTER_KINDS: tuple[FilterKind, ...] = ("matches", "equals", "contains", "notEmpty")


class _Deferred(Exception):
    """An argument still holds a `${...}` token, so its value cannot be checked yet."""


class _Args:
    """Typed reads of one operation's arguments, each failing with a value-free message."""

    def __init__(self, where: str, args: Mapping[str, object], *, defer: bool) -> None:
        self._where = where
        self._args = args
        self._defer = defer

    def fail(self, why: str) -> EvalOpError:
        return EvalOpError(f"{self._where}: {why}")

    def over(self, args: Mapping[str, object]) -> _Args:
        """The same operation's reader over its argument mapping."""
        return _Args(self._where, args, defer=self._defer)

    def only(self, *allowed: str) -> None:
        extra = sorted(set(self._args) - set(allowed))
        if extra:
            raise self.fail(f"unknown argument(s) {extra}; allowed: {list(allowed)}")

    def deferred(self, value: object) -> bool:
        return self._defer and isinstance(value, str) and _PLACEHOLDER_START in value

    def text(self, key: str, *, required: bool = True, default: str = "") -> str:
        value = self._args.get(key)
        if value is None:
            if required:
                raise self.fail(f"`{key}` is required")
            return default
        if not isinstance(value, str):
            raise self.fail(f"`{key}` must be a string")
        return value

    def flag(self, key: str) -> bool:
        # A flag is a literal: a token that turns a predicate inside out at run time is a scenario
        # nobody can read, so `${...}` is not accepted here.
        value = self._args.get(key, False)
        if not isinstance(value, bool):
            raise self.fail(f"`{key}` must be true or false")
        return value

    def index(self, key: str, *, required: bool = True) -> int | None:
        value = self._args.get(key)
        if value is None:
            if required:
                raise self.fail(f"`{key}` is required")
            return None
        if self.deferred(value):
            raise _Deferred
        return self.as_index(value, f"`{key}`")

    def as_index(self, value: object, label: str) -> int:
        # A substituted `${vars.*}` arrives as text, so a decimal string counts as an index too.
        if isinstance(value, bool):
            raise self.fail(f"{label} must be a non-negative integer")
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and _INDEX.fullmatch(value):
            return int(value)
        raise self.fail(f"{label} must be a non-negative integer")

    def pattern(self, value: str, label: str) -> str:
        if self.deferred(value):
            raise _Deferred
        try:
            re.compile(value)
        except re.error:
            raise self.fail(f"{label} is not a valid regular expression") from None
        return value

    def defer_if_token(self, value: str) -> None:
        if self.deferred(value):
            raise _Deferred


def _split(a: _Args, raw: object) -> Split:
    if isinstance(raw, str):
        args: Mapping[str, object] = {"by": raw}
    elif isinstance(raw, Mapping):
        args = raw
    else:
        raise a.fail("takes a separator string or { by, regex }")
    a = a.over(args)
    a.only("by", "regex")
    by, regex = a.text("by"), a.flag("regex")
    a.defer_if_token(by)
    if by == "":
        raise a.fail("the separator must not be empty")
    return Split(a.pattern(by, "`by`") if regex else by, regex)


def _join(a: _Args, raw: object) -> Join:
    if not isinstance(raw, str):
        raise a.fail("takes a separator string")
    return Join(raw)


def _filter(a: _Args, raw: object) -> Filter:
    if not isinstance(raw, Mapping):
        raise a.fail("takes { matches | equals | contains | notEmpty, not? }")
    a = a.over(raw)
    a.only(*_FILTER_KINDS, "not")
    present = [k for k in _FILTER_KINDS if k in raw]
    if len(present) != 1:
        raise a.fail(f"exactly one of {list(_FILTER_KINDS)} is required")
    kind = present[0]
    negate = a.flag("not")
    if kind == "notEmpty":
        if raw["notEmpty"] is not True:
            raise a.fail("`notEmpty` must be true (use `not: true` to keep only empty elements)")
        return Filter(kind, "", negate)
    arg = a.text(kind)
    if kind == "matches":
        arg = a.pattern(arg, "`matches`")
    else:
        a.defer_if_token(arg)
    return Filter(kind, arg, negate)


def _pick(a: _Args, raw: object) -> Pick:
    if raw in ("first", "last"):
        return Pick("first" if raw == "first" else "last")
    if a.deferred(raw):
        raise _Deferred
    return Pick(a.as_index(raw, "the index"))


def _sort(a: _Args, raw: object) -> Sort:
    if raw is None:
        return Sort("asc", False)
    if isinstance(raw, str):
        args: Mapping[str, object] = {"order": raw}
    elif isinstance(raw, Mapping):
        args = raw
    else:
        raise a.fail("takes `asc`, `desc`, or { order, numeric }")
    a = a.over(args)
    a.only("order", "numeric")
    order = a.text("order", required=False, default="asc")
    if order not in ("asc", "desc"):
        raise a.fail("`order` must be asc or desc")
    return Sort("asc" if order == "asc" else "desc", a.flag("numeric"))


def _regex(a: _Args, raw: object) -> RegexOp:
    if not isinstance(raw, Mapping):
        raise a.fail("takes { pattern, group? }")
    a = a.over(raw)
    a.only("pattern", "group")
    group = a.index("group", required=False)
    pattern = a.pattern(a.text("pattern"), "`pattern`")
    return RegexOp(pattern, group or 0)


def _replace(a: _Args, raw: object) -> Replace:
    if not isinstance(raw, Mapping):
        raise a.fail("takes { find, with, regex? }")
    a = a.over(raw)
    a.only("find", "with", "regex")
    find, with_, regex = a.text("find"), a.text("with"), a.flag("regex")
    a.defer_if_token(with_)
    if regex:
        find = a.pattern(find, "`find`")
    else:
        a.defer_if_token(find)
    return Replace(find, with_, regex)


def _substring(a: _Args, raw: object) -> Substring:
    if not isinstance(raw, Mapping):
        raise a.fail("takes { start, end? }")
    a = a.over(raw)
    a.only("start", "end")
    start = a.index("start")
    end = a.index("end", required=False)
    assert start is not None  # required=True never returns None
    return Substring(start, end)


def _case(a: _Args, raw: object) -> Case:
    if raw not in ("lower", "upper"):
        raise a.fail("takes lower or upper")
    return Case("lower" if raw == "lower" else "upper")


_BARE_OPS: Mapping[str, Bare] = {
    "trim": Bare("trim"),
    "json": Bare("json"),
    "count": Bare("count"),
    "unique": Bare("unique"),
    "reverse": Bare("reverse"),
}

_PARSERS: Mapping[str, Callable[[_Args, object], EvalOp]] = {
    "split": _split,
    "join": _join,
    "filter": _filter,
    "pick": _pick,
    "sort": _sort,
    "regex": _regex,
    "replace": _replace,
    "substring": _substring,
    "case": _case,
}


def _parse_one(raw: object, position: int, *, defer: bool) -> tuple[str, EvalOp | None]:
    """The operation's name and its parsed form — None when a deferred token blocks the parse."""
    if isinstance(raw, str):
        name, arg, bare = raw, None, True
    elif isinstance(raw, Mapping) and len(raw) == 1:
        ((name, arg),) = raw.items()
        bare = False
    else:
        raise EvalOpError(f"op {position}: an operation is a name or a mapping with one key")
    if not isinstance(name, str) or name not in SIGNATURES:
        raise EvalOpError(f"op {position}: unknown operation; allowed: {sorted(SIGNATURES)}")
    a = _Args(f"op {position} ({name})", {}, defer=defer)
    if bare and name not in _BARE_NAMES:
        raise a.fail("takes arguments")
    if name in _BARE_NAMES and name != "sort":
        if not bare:
            raise a.fail("takes no arguments; write it as a bare name")
        return name, _BARE_OPS[name]
    try:
        return name, _PARSERS[name](a, arg)
    except _Deferred:
        return name, None


def type_flow(names: Sequence[str]) -> ValueKind:
    """The kind of value a pipeline of *names* ends with, starting from a string.

    Raises:
        EvalOpError: an operation receives the other kind.
    """
    kind: ValueKind = "string"
    for position, name in enumerate(names, start=1):
        takes, gives = SIGNATURES[name]
        if kind != takes:
            raise EvalOpError(f"op {position} ({name}): takes a {takes}, but receives a {kind}")
        kind = gives
    return kind


def check_ops(raw: Sequence[object]) -> ValueKind:
    """Load-time check of an `ops` list: arguments without tokens, then the type flow.

    Returns:
        The kind of value the pipeline produces.

    Raises:
        EvalOpError: the list is empty, an operation or argument is invalid, or the types clash.
    """
    if not raw:
        raise EvalOpError("ops must not be empty")
    names = [_parse_one(op, i, defer=True)[0] for i, op in enumerate(raw, start=1)]
    return type_flow(names)


def parse_ops(raw: Sequence[object]) -> list[EvalOp]:
    """Run-time parse of a substituted `ops` list, every argument checked.

    Raises:
        EvalOpError: as `check_ops`, now including the arguments a token hid at load time.
    """
    check_ops(raw)
    ops: list[EvalOp] = []
    for i, op in enumerate(raw, start=1):
        parsed = _parse_one(op, i, defer=False)[1]
        assert parsed is not None  # nothing is deferred without defer=True
        ops.append(parsed)
    return ops
