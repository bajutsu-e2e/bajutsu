"""Tests for idb version capture and the pin comparison (BE-0005).

Pure logic over injected probes — no subprocess, no Simulator. The version a run is driven
against becomes a recorded, comparable input rather than whatever happens to be installed.
"""

from __future__ import annotations

from bajutsu import idb_version


def test_parse_version_extracts_dotted_number_from_tool_output() -> None:
    # idb_companion / idb print the version embedded in a line; pull the dotted number out.
    assert idb_version.parse_version("idb_companion 1.1.8") == "1.1.8"
    assert idb_version.parse_version("1.1.8\n") == "1.1.8"
    assert idb_version.parse_version("idb 1.1.8 (build 42)") == "1.1.8"
    assert idb_version.parse_version("com.facebook.idb 0.0.3") == "0.0.3"


def test_parse_version_returns_none_when_no_version_present() -> None:
    assert idb_version.parse_version("") is None
    assert idb_version.parse_version("no number here") is None


def test_satisfies_minimum_constraint() -> None:
    assert idb_version.satisfies("1.1.8", ">=1.1.8")
    assert idb_version.satisfies("1.2.0", ">=1.1.8")
    assert not idb_version.satisfies("1.1.7", ">=1.1.8")


def test_satisfies_compares_numerically_not_lexically() -> None:
    # "1.10.0" > "1.9.0" numerically, even though it sorts earlier as a string.
    assert idb_version.satisfies("1.10.0", ">=1.9.0")
    assert not idb_version.satisfies("1.9.0", ">=1.10.0")


def test_satisfies_range_with_multiple_constraints() -> None:
    spec = ">=1.1.0,<2.0.0"
    assert idb_version.satisfies("1.5.0", spec)
    assert not idb_version.satisfies("2.0.0", spec)
    assert not idb_version.satisfies("1.0.9", spec)


def test_satisfies_exact_and_other_operators() -> None:
    assert idb_version.satisfies("1.1.8", "==1.1.8")
    assert not idb_version.satisfies("1.1.9", "==1.1.8")
    assert idb_version.satisfies("1.1.7", "<=1.1.8")
    assert idb_version.satisfies("1.1.9", ">1.1.8")


def test_is_valid_spec_accepts_operators_and_ranges() -> None:
    assert idb_version.is_valid_spec(">=1.1.8")
    assert idb_version.is_valid_spec(">=1.1.0,<2.0.0")
    assert idb_version.is_valid_spec("==1.1.8")


def test_is_valid_spec_rejects_malformed_pins() -> None:
    assert not idb_version.is_valid_spec("1.1.8")  # bare version, no operator
    assert not idb_version.is_valid_spec("~=1.1")  # unsupported operator
    assert not idb_version.is_valid_spec("")
    assert not idb_version.is_valid_spec(">=1.1.0,garbage")


def test_satisfies_is_false_for_unparseable_installed_version() -> None:
    # A version we can't read can't be claimed to satisfy a pin — fail the comparison, don't guess.
    assert not idb_version.satisfies("unknown", ">=1.1.8")


def test_probe_reads_both_versions_through_injected_runner() -> None:
    def fake_run(args: list[str]) -> str:
        if args[0] == "idb_companion":
            return "idb_companion 1.1.8\n"
        return "idb 1.1.8\n"

    v = idb_version.probe(run=fake_run)
    assert v.companion == "1.1.8"
    assert v.client == "1.1.8"


def test_probe_degrades_to_none_when_a_tool_is_missing() -> None:
    # idb is an optional extra; a host without it must yield unknown (None), never crash —
    # the version is provenance, not a gate.
    def missing(args: list[str]) -> str:
        raise FileNotFoundError(args[0])

    v = idb_version.probe(run=missing)
    assert v.companion is None
    assert v.client is None


def test_probe_returns_none_for_unparseable_output() -> None:
    v = idb_version.probe(run=lambda args: "garbage with no version")
    assert v.companion is None
    assert v.client is None
