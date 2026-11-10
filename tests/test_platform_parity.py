"""The iOS/Android parity table in docs/platform-parity.md matches what each driver declares.

The table is the working list for closing the gaps between the two mobile backends. Each mark is
checked against the backend's static `CAPABILITIES` (read through `backends.capabilities_for`, the
same source the preflight uses), so a gap cannot open or close without the page changing with it,
and a new capability token cannot ship without a row. The launch-settings table on the same page is
checked the same way against the route support table (`settings_support`, BE-0467), and the
four-backend table in docs/drivers.md gets the row-coverage and ✅-matches-declared checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu.common import backends
from bajutsu.common.capability import settings_support
from bajutsu.common.drivers import base

_DOCS = Path(__file__).resolve().parents[1] / "docs"
_PAGES = {"en": _DOCS / "platform-parity.md", "ja": _DOCS / "ja" / "platform-parity.md"}
_MARKER = "<!-- parity-table -->"
_SETTINGS_MARKER = "<!-- settings-table -->"
# Column order after the token column; each maps to the actuator whose CAPABILITIES it describes.
_BACKENDS = ("xcuitest", "adb")
_MARKS = frozenset({"✅", "gap", "n/a", "per-run"})


def _all_tokens() -> set[str]:
    named = {
        value
        for name, value in vars(base.Capability).items()
        if name.isupper() and isinstance(value, str)
    }
    return named | {base.permission_capability(s) for s in base.PERMISSION_SERVICES}


def _rows(page: Path, marker: str = _MARKER) -> dict[str, tuple[str, str, str]]:
    """Key → (iOS mark, Android mark, note) for the body rows after *marker*."""
    lines = page.read_text(encoding="utf-8").splitlines()
    start = lines.index(marker) + 3  # skip the marker, the header, and the separator row
    rows: dict[str, tuple[str, str, str]] = {}
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        token = cells[0].strip("`")
        assert token not in rows, f"{page.name}: duplicate row for {token!r}"
        rows[token] = (cells[1], cells[2], cells[3])
    return rows


@pytest.fixture(scope="module", params=sorted(_PAGES))
def rows(request: pytest.FixtureRequest) -> dict[str, tuple[str, str, str]]:
    return _rows(_PAGES[request.param])


def test_rows_name_every_capability_token(rows: dict[str, tuple[str, str, str]]) -> None:
    tokens = _all_tokens()
    assert set(rows) == tokens, (
        f"missing rows: {sorted(tokens - set(rows))}; unknown rows: {sorted(set(rows) - tokens)}"
    )


def test_every_mark_is_from_the_vocabulary(rows: dict[str, tuple[str, str, str]]) -> None:
    bad = {t: m for t, (*marks, _) in rows.items() for m in marks if m not in _MARKS}
    assert not bad, f"marks outside {sorted(_MARKS)}: {bad}"


@pytest.mark.parametrize("column", range(len(_BACKENDS)), ids=_BACKENDS)
def test_check_mark_matches_the_declared_capabilities(
    rows: dict[str, tuple[str, str, str]], column: int
) -> None:
    declared = backends.capabilities_for(_BACKENDS[column])
    marked = {t for t, cells in rows.items() if cells[column] == "✅"}
    assert marked == set(declared) & set(rows), (
        f"{_BACKENDS[column]}: marked ✅ but not declared: {sorted(marked - declared)}; "
        f"declared but not marked ✅: {sorted((declared & set(rows)) - marked)}"
    )


def test_every_non_supported_mark_carries_a_note(rows: dict[str, tuple[str, str, str]]) -> None:
    bare = [t for t, (ios, android, note) in rows.items() if {ios, android} != {"✅"} and not note]
    assert not bare, f"rows with a gap, n/a, or per-run mark need a note: {bare}"


# docs/drivers.md keeps its own four-backend capability table. It had fallen 12 rows behind the
# tokens before this check covered it, so it gets the same coverage and ✅-matches-declared checks.
_DRIVERS_PAGES = {"en": _DOCS / "drivers.md", "ja": _DOCS / "ja" / "drivers.md"}
_DRIVERS_HEADER_TAIL = "| xcuitest | adb | playwright | fake |"
_DRIVERS_BACKENDS = ("xcuitest", "adb", "playwright", "fake")


def _drivers_rows(page: Path) -> dict[str, list[str]]:
    """Token → one mark per backend, from the table whose header ends with the four backends."""
    lines = page.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.endswith(_DRIVERS_HEADER_TAIL)) + 2
    rows: dict[str, list[str]] = {}
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows[cells[0].strip("`")] = cells[-len(_DRIVERS_BACKENDS) :]
    return rows


@pytest.mark.parametrize("lang", sorted(_DRIVERS_PAGES))
def test_drivers_page_table_matches_every_backend(lang: str) -> None:
    rows = _drivers_rows(_DRIVERS_PAGES[lang])
    tokens = _all_tokens()
    assert set(rows) == tokens, (
        f"missing rows: {sorted(tokens - set(rows))}; unknown rows: {sorted(set(rows) - tokens)}"
    )
    wrong = [
        (token, backend, marks[i])
        for token, marks in rows.items()
        for i, backend in enumerate(_DRIVERS_BACKENDS)
        if (marks[i] == "✅") != (token in backends.capabilities_for(backend))
    ]
    assert not wrong, f"marks that disagree with the declared capabilities: {wrong}"


def test_both_languages_carry_the_same_marks() -> None:
    en, ja = (_rows(_PAGES[lang]) for lang in ("en", "ja"))
    marks_en = {t: cells[:2] for t, cells in en.items()}
    marks_ja = {t: cells[:2] for t, cells in ja.items()}
    assert marks_en == marks_ja


# The launch-settings table: one column per platform route, keyed like `runsOn.ignoredSettings`.
# Permission keys are left out because the capability table already carries them, and a route reads
# them from those same tokens (`route_settings`).
_ROUTES: tuple[settings_support.Route, ...] = ("ios-simulator", "android")
_APPLIED = frozenset({"supported", "satisfied"})


def _setting_keys() -> set[str]:
    return {k for k in settings_support.SETTING_KEYS if not k.startswith("permissions.")}


@pytest.mark.parametrize("lang", sorted(_PAGES))
def test_settings_table_matches_the_route_support_table(lang: str) -> None:
    rows = _rows(_PAGES[lang], _SETTINGS_MARKER)
    keys = _setting_keys()
    assert set(rows) == keys, (
        f"missing rows: {sorted(keys - set(rows))}; unknown rows: {sorted(set(rows) - keys)}"
    )
    wrong = [
        (key, route, cells[i])
        for key, cells in rows.items()
        for i, route in enumerate(_ROUTES)
        if (cells[i] == "✅") != (settings_support.route_settings(route)[key] in _APPLIED)
    ]
    assert not wrong, f"marks that disagree with the route support table: {wrong}"
    bad = [k for k, (ios, android, note) in rows.items() if not {ios, android} <= _MARKS]
    assert not bad, f"marks outside {sorted(_MARKS)}: {bad}"
    bare = [k for k, (ios, android, note) in rows.items() if {ios, android} != {"✅"} and not note]
    assert not bare, f"rows with a gap or n/a mark need a note: {bare}"


def test_both_languages_carry_the_same_settings_marks() -> None:
    en, ja = (_rows(_PAGES[lang], _SETTINGS_MARKER) for lang in ("en", "ja"))
    assert {k: c[:2] for k, c in en.items()} == {k: c[:2] for k, c in ja.items()}
