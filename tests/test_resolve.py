"""Tests for selector resolution semantics (the determinism core).

Frozen together with the abstraction, per "cover determinism with tests".
Runs under pytest or directly (no dependencies).
"""

from __future__ import annotations

from bajutsu.drivers.base import (
    AmbiguousSelector,
    Element,
    ElementNotFound,
    find_all,
    resolve_unique,
)


def _el(
    identifier: str | None = None,
    label: str | None = None,
    traits: list[str] | None = None,
    value: str | None = None,
    frame: tuple[float, float, float, float] = (0.0, 0.0, 10.0, 10.0),
) -> Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or [],
        "value": value,
        "frame": frame,
    }


SCREEN: list[Element] = [
    _el("settings.open", "設定", ["button"]),
    _el("settings.reindex", "再生成", ["button"]),
    _el("result.row.1", "A", ["cell"]),
    _el("result.row.2", "B", ["cell"]),
]


def test_resolve_by_id_unique() -> None:
    assert resolve_unique(SCREEN, {"id": "settings.open"})["label"] == "設定"


def test_not_found_raises() -> None:
    try:
        resolve_unique(SCREEN, {"id": "nope"})
    except ElementNotFound:
        return
    raise AssertionError("ElementNotFound が送出されるべき")


def test_ambiguous_raises() -> None:
    try:
        resolve_unique(SCREEN, {"idMatches": "result.row.*"})
    except AmbiguousSelector:
        return
    raise AssertionError("AmbiguousSelector が送出されるべき（曖昧は即失敗）")


def test_index_disambiguates() -> None:
    got = resolve_unique(SCREEN, {"idMatches": "result.row.*", "index": 0})
    assert got["identifier"] == "result.row.1"


def test_count_via_find_all() -> None:
    assert len(find_all(SCREEN, {"idMatches": "result.row.*"})) == 2


def test_traits_subset() -> None:
    assert len(find_all(SCREEN, {"traits": ["button"]})) == 2


def test_and_of_fields() -> None:
    assert len(find_all(SCREEN, {"label": "設定", "traits": ["button"]})) == 1
    assert find_all(SCREEN, {"label": "設定", "traits": ["cell"]}) == []


def test_within_scopes_to_container() -> None:
    # Two same-id buttons, each inside a different section container.
    screen: list[Element] = [
        _el("form.login", "login", ["group"], frame=(0.0, 0.0, 100.0, 50.0)),
        _el("form.signup", "signup", ["group"], frame=(0.0, 60.0, 100.0, 50.0)),
        _el("row.submit", "Go", ["button"], frame=(10.0, 10.0, 30.0, 20.0)),   # inside login
        _el("row.submit", "Go", ["button"], frame=(10.0, 70.0, 30.0, 20.0)),   # inside signup
    ]
    # Ambiguous on its own…
    try:
        resolve_unique(screen, {"id": "row.submit"})
        raise AssertionError("曖昧で失敗するべき")
    except AmbiguousSelector:
        pass
    # …but `within` scopes it to one section.
    assert resolve_unique(screen, {"id": "row.submit", "within": {"id": "form.login"}})["frame"][1] == 10.0
    assert resolve_unique(screen, {"id": "row.submit", "within": {"id": "form.signup"}})["frame"][1] == 70.0


def test_within_excludes_elements_outside_the_scope() -> None:
    screen: list[Element] = [
        _el("box", frame=(0.0, 0.0, 50.0, 50.0)),
        _el("btn", "out", ["button"], frame=(100.0, 100.0, 10.0, 10.0)),  # outside box
    ]
    assert find_all(screen, {"id": "btn", "within": {"id": "box"}}) == []


def test_within_nests() -> None:
    screen: list[Element] = [
        _el("outer", frame=(0.0, 0.0, 100.0, 100.0)),
        _el("inner", frame=(10.0, 10.0, 50.0, 50.0)),
        _el("btn", "go", ["button"], frame=(15.0, 15.0, 5.0, 5.0)),  # inside inner ⊂ outer
    ]
    got = resolve_unique(screen, {"id": "btn", "within": {"id": "inner", "within": {"id": "outer"}}})
    assert got["identifier"] == "btn"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"\n{len(fns)} passed")
