"""`VersionSpec` (BE-0452 unit 1): npm's range grammar, three-component comparison, the prerelease
rejection, and the major versions a range covers.

The desugaring table pins node-semver's own README examples, so a reader can check each row against
the grammar Bajutsu adopts rather than against a notation of its own.
"""

from __future__ import annotations

import pytest

from bajutsu.common.devices.version import Version, VersionSpec


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("18", Version(18, 0, 0)),
        ("18.2", Version(18, 2, 0)),
        ("18.2.1", Version(18, 2, 1)),
        ("34", Version(34, 0, 0)),  # an Android API level
        ("130.0.6723.31", Version(130, 0, 6723)),  # Chrome: the fourth component is dropped
        ("18.0-beta", Version(18, 0, 0)),  # a beta runtime compares by its numbers alone
        ("v17.4", Version(17, 4, 0)),
    ],
)
def test_version_parse_reads_three_components(text: str, expected: Version) -> None:
    assert Version.parse(text) == expected


@pytest.mark.parametrize("text", ["", "iOS 18", "beta", "x"])
def test_version_parse_rejects_text_without_a_number(text: str) -> None:
    with pytest.raises(ValueError, match="not a version"):
        Version.parse(text)


def test_versions_order_numerically_not_lexically() -> None:
    assert Version.parse("9") < Version.parse("10")
    assert Version.parse("17.10") > Version.parse("17.9")
    assert str(Version(18, 2)) == "18.2.0"


# Each row is a range from node-semver's README and versions just inside and just outside what its
# documented desugaring admits.
@pytest.mark.parametrize(
    ("spec", "inside", "outside"),
    [
        # comparators, space-separated for "and"
        (">=17 <19", ["17", "17.0.0", "18.9.9"], ["16.9", "19", "19.0.1"]),
        (">1.2.3", ["1.2.4"], ["1.2.3"]),
        ("<=1.2.3", ["1.2.3"], ["1.2.4"]),
        ("=1.2.3", ["1.2.3"], ["1.2.4", "1.2.2"]),
        ("1.2.3", ["1.2.3"], ["1.2.4"]),
        (">= 1.2.3", ["1.2.3"], ["1.2.2"]),  # whitespace after the operator
        ("v1.2.3", ["1.2.3"], ["1.2.4"]),
        # hyphen ranges
        ("1.2.3 - 2.3.4", ["1.2.3", "2.3.4"], ["1.2.2", "2.3.5"]),
        ("1.2 - 2.3.4", ["1.2.0"], ["1.1.9"]),  # partial first: >=1.2.0
        ("1.2.3 - 2.3", ["2.3.9"], ["2.4.0"]),  # partial second: <2.4.0
        ("1.2.3 - 2", ["2.9.9"], ["3.0.0"]),  # partial second: <3.0.0
        ("33 - 35", ["33", "35", "35.9"], ["32", "36"]),
        # x-ranges and partial versions
        ("*", ["0.0.0", "999"], []),
        ("", ["0.0.0", "1.2.3"], []),
        ("1.x", ["1.0.0", "1.9.9"], ["2.0.0", "0.9.9"]),
        ("1.2.x", ["1.2.0", "1.2.9"], ["1.3.0"]),
        ("1.X", ["1.5"], ["2"]),
        ("1.*", ["1.5"], ["2"]),
        ("1", ["1.0.0", "1.9.9"], ["2.0.0"]),
        ("18", ["18", "18.6.1"], ["17.9", "19"]),
        ("1.2", ["1.2.0", "1.2.9"], ["1.3.0"]),
        ("1.x.3", ["1.0.0", "1.9.9"], ["2.0.0"]),  # every component after a wildcard is one
        # comparators over a partial version
        (">1", ["2.0.0"], ["1.9.9"]),
        (">1.2", ["1.3.0"], ["1.2.9"]),
        (">=1.x", ["1.0.0"], ["0.9.9"]),
        ("<1.2", ["1.1.9"], ["1.2.0"]),
        ("<=1.2", ["1.2.9"], ["1.3.0"]),
        ("<=1", ["1.9.9"], ["2.0.0"]),
        ("<=17.4", ["17.4.9"], ["17.5"]),
        ("=1.2", ["1.2.5"], ["1.3"]),
        (">=*", ["0.0.0"], []),
        ("<=*", ["0.0.0"], []),
        ("<*", [], ["0.0.0", "1"]),
        (">*", [], ["0.0.0", "1"]),
        # tilde
        ("~1.2.3", ["1.2.3", "1.2.9"], ["1.2.2", "1.3.0"]),
        ("~1.2", ["1.2.0", "1.2.9"], ["1.3.0"]),
        ("~1", ["1.0.0", "1.9.9"], ["2.0.0"]),
        ("~0.2.3", ["0.2.3"], ["0.3.0"]),
        ("~0.2", ["0.2.0"], ["0.3.0"]),
        ("~0", ["0.0.0", "0.9.9"], ["1.0.0"]),
        ("~17.4", ["17.4", "17.4.9"], ["17.3.9", "17.5"]),
        ("~>1.2", ["1.2.5"], ["1.3.0"]),
        ("~*", ["5"], []),
        # caret
        ("^1.2.3", ["1.2.3", "1.9.9"], ["1.2.2", "2.0.0"]),
        ("^0.2.3", ["0.2.3", "0.2.9"], ["0.3.0"]),
        ("^0.0.3", ["0.0.3"], ["0.0.4", "0.0.2"]),
        ("^1.2.x", ["1.2.0", "1.9.9"], ["2.0.0"]),
        ("^0.0.x", ["0.0.0", "0.0.9"], ["0.1.0"]),
        ("^0.0", ["0.0.9"], ["0.1.0"]),
        ("^1.x", ["1.0.0", "1.9.9"], ["2.0.0"]),
        ("^0.x", ["0.0.0", "0.9.9"], ["1.0.0"]),
        ("^17", ["17", "17.9"], ["16.9", "18"]),
        ("^*", ["5"], []),
        # unions
        ("17 || 19", ["17.5", "19.0"], ["18", "20"]),
        ("17 || >=19", ["17", "19", "30"], ["18"]),
        ("1.2.7 || >=1.2.9 <2.0.0", ["1.2.7", "1.2.9", "1.4.6"], ["1.2.8", "2.0.0"]),
        # build metadata is ignored, as npm ignores it
        ("1.2.3+build.5", ["1.2.3"], ["1.2.4"]),
        # a set no version satisfies
        (">5 <3", [], ["4", "5", "3"]),
        (">=5 <5", [], ["5"]),
    ],
)
def test_range_admits_what_its_desugaring_admits(
    spec: str, inside: list[str], outside: list[str]
) -> None:
    parsed = VersionSpec.parse(spec)
    for v in inside:
        assert parsed.contains(v), f"{spec!r} should contain {v}"
    for v in outside:
        assert not parsed.contains(v), f"{spec!r} should not contain {v}"


def test_contains_takes_a_parsed_version_too() -> None:
    assert VersionSpec.parse(">=34").contains(Version(34))


@pytest.mark.parametrize(
    "spec",
    ["18.0.0-beta", ">=17.0.0-0", "1.2.3-rc.1 - 2", "^1.2.3-alpha", "17 || 19.0.0-beta.2"],
)
def test_range_naming_a_prerelease_is_rejected(spec: str) -> None:
    with pytest.raises(ValueError, match="prerelease tags are not used"):
        VersionSpec.parse(spec)


@pytest.mark.parametrize(
    "spec",
    ["latest", ">=", "18..2", "01", ">=17 <", "1.2.3.4", "=>17", ">=17,<19", "1 -2"],
)
def test_malformed_range_is_rejected(spec: str) -> None:
    with pytest.raises(ValueError, match="invalid version range"):
        VersionSpec.parse(spec)


@pytest.mark.parametrize(
    ("spec", "majors"),
    [
        (">=17 <19", (17, 18)),
        ("18", (18,)),
        ("18.x", (18,)),
        ("^17", (17,)),
        ("~17.4", (17,)),
        ("33 - 35", (33, 34, 35)),
        ("<=18.2 >=16", (16, 17, 18)),
        (">=17 <19.0.1", (17, 18, 19)),  # 19.0.0 is inside, so 19 is covered
        (">17.9 <=18.0.0", (17, 18)),
        ("=18.2.1", (18,)),
        ("17 || 19", (17, 19)),
        (">=17 <19 || 18", (17, 18)),  # an overlap lists each major once
        ("17 || >=19", (17,)),  # an open side contributes nothing
        (">=18", ()),
        ("<19", ()),
        ("*", ()),
        (">5 <3", ()),
    ],
)
def test_majors_lists_what_the_bounded_sides_cover(spec: str, majors: tuple[int, ...]) -> None:
    assert VersionSpec.parse(spec).majors() == majors


@pytest.mark.parametrize(
    ("spec", "bounded"),
    [
        (">=17 <19", True),
        ("18", True),
        ("^17", True),
        ("33 - 35", True),
        (">=18", False),
        ("<19", False),
        ("<=17.4", False),
        ("*", False),
        (">=0", False),
        ("1.2.3 - *", False),  # a hyphen with a wildcard end has no upper bound
        ("17 || >=19", False),
        ("17 || 19", True),
    ],
)
def test_bounded_names_a_range_closed_on_both_sides(spec: str, bounded: bool) -> None:
    assert VersionSpec.parse(spec).bounded is bounded


def test_union_keeps_each_side_for_its_own_rule() -> None:
    spec = VersionSpec.parse("17 || >=19")
    assert [alt.bounded for alt in spec.alternatives] == [True, False]
    assert spec.alternatives[0].majors() == (17,)


def test_an_open_side_has_no_fixed_majors() -> None:
    open_side = VersionSpec.parse(">=18").alternatives[0]
    with pytest.raises(ValueError, match="open range"):
        open_side.majors()


def test_spec_prints_as_written() -> None:
    assert str(VersionSpec.parse(">=17  <19")) == ">=17  <19"


def test_contains_reads_an_integer_as_a_major_version() -> None:
    # An Android API level arrives as an integer.
    spec = VersionSpec.parse("33 - 35")
    assert spec.contains(34)
    assert not spec.contains(36)


@pytest.mark.parametrize("spec", [">18.9.0 <18.9.1", ">18.9.0 <=18.9.0", ">=18.9.1 <18.9.1"])
def test_exclusive_bounds_with_no_integer_version_between_are_empty(spec: str) -> None:
    parsed = VersionSpec.parse(spec)
    assert parsed.majors() == ()
    assert not parsed.contains("18.9.0")
    assert not parsed.contains("18.9.1")


def test_exclusive_lower_bound_leaves_the_next_patch_inside() -> None:
    parsed = VersionSpec.parse(">18.9.0 <18.9.2")
    assert parsed.majors() == (18,)
    assert parsed.contains("18.9.1")
