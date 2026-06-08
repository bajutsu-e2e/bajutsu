"""Tests for identifier recovery (bajutsu/idmap.py)."""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.idmap import apply, load_idmap


def _el(
    label: str | None = None,
    value: str | None = None,
    traits: list[str] | None = None,
    identifier: str | None = None,
) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "value": value,
        "traits": traits or [],
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def test_load_and_apply_recovers_identifiers() -> None:
    idmap = load_idmap(
        "home.title: { role: staticText, label: Home }\n"
        'counter.value: { role: staticText, labelMatches: "^Count:" }\n'
        "counter.increment: { role: button, label: \"+\" }\n"
    )
    screen = [
        _el(label="Home", traits=["staticText"]),
        _el(label="Count: 2", value="2", traits=["staticText"]),
        _el(label="+", traits=["button"]),
    ]
    out = apply(screen, idmap)
    ids = [e["identifier"] for e in out]
    assert ids == ["home.title", "counter.value", "counter.increment"]


def test_role_disambiguates_same_label() -> None:
    # "Home" as a staticText (title) and a radioButton (tab) — role keeps them apart.
    idmap = load_idmap("home.title: { role: staticText, label: Home }\n")
    out = apply(
        [_el(label="Home", traits=["staticText"]), _el(label="Home", traits=["radioButton"])],
        idmap,
    )
    assert out[0]["identifier"] == "home.title"
    assert out[1]["identifier"] is None


def test_ambiguous_match_is_left_unresolved() -> None:
    # Two equally-matching elements -> assign neither (selector layer will report it).
    idmap = load_idmap("ambiguous: { role: button }\n")
    out = apply([_el(traits=["button"]), _el(traits=["button"])], idmap)
    assert [e["identifier"] for e in out] == [None, None]


def test_existing_identifiers_are_preserved() -> None:
    # A backend that already provides identifiers (idb) is unaffected.
    idmap = load_idmap("recovered: { role: button }\n")
    out = apply([_el(traits=["button"], identifier="real.id")], idmap)
    assert out[0]["identifier"] == "real.id"


def test_empty_idmap_returns_input() -> None:
    screen = [_el(label="x")]
    assert apply(screen, {}) is screen
