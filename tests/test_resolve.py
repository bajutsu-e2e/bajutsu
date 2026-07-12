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


# Kept local (not shared via conftest) so this determinism-core file stays self-contained
# and runnable directly, per the module docstring.
def el(
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
    el("settings.open", "設定", ["button"]),
    el("settings.reindex", "再生成", ["button"]),
    el("result.row.1", "A", ["cell"]),
    el("result.row.2", "B", ["cell"]),
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
        el("form.login", "login", ["group"], frame=(0.0, 0.0, 100.0, 50.0)),
        el("form.signup", "signup", ["group"], frame=(0.0, 60.0, 100.0, 50.0)),
        el("row.submit", "Go", ["button"], frame=(10.0, 10.0, 30.0, 20.0)),  # inside login
        el("row.submit", "Go", ["button"], frame=(10.0, 70.0, 30.0, 20.0)),  # inside signup
    ]
    # Ambiguous on its own…
    try:
        resolve_unique(screen, {"id": "row.submit"})
        raise AssertionError("曖昧で失敗するべき")
    except AmbiguousSelector:
        pass
    # …but `within` scopes it to one section.
    assert (
        resolve_unique(screen, {"id": "row.submit", "within": {"id": "form.login"}})["frame"][1]
        == 10.0
    )
    assert (
        resolve_unique(screen, {"id": "row.submit", "within": {"id": "form.signup"}})["frame"][1]
        == 70.0
    )


def test_within_excludes_elements_outside_the_scope() -> None:
    screen: list[Element] = [
        el("box", frame=(0.0, 0.0, 50.0, 50.0)),
        el("btn", "out", ["button"], frame=(100.0, 100.0, 10.0, 10.0)),  # outside box
    ]
    assert find_all(screen, {"id": "btn", "within": {"id": "box"}}) == []


def test_within_nests() -> None:
    screen: list[Element] = [
        el("outer", frame=(0.0, 0.0, 100.0, 100.0)),
        el("inner", frame=(10.0, 10.0, 50.0, 50.0)),
        el("btn", "go", ["button"], frame=(15.0, 15.0, 5.0, 5.0)),  # inside inner ⊂ outer
    ]
    got = resolve_unique(
        screen, {"id": "btn", "within": {"id": "inner", "within": {"id": "outer"}}}
    )
    assert got["identifier"] == "btn"


def test_compile_cache_reuses_compiled_pattern() -> None:
    """_compile caches compiled regex patterns so repeated calls skip re.compile."""
    from bajutsu.drivers.base import _compile

    _compile.cache_clear()
    _compile("foo.*bar")
    _compile("foo.*bar")
    info = _compile.cache_info()
    assert info.hits == 1 and info.misses == 1


def test_label_matches_uses_regex() -> None:
    """labelMatches selector uses regex matching (via cached compile)."""
    screen: list[Element] = [
        el("a", "Settings Page", ["staticText"]),
        el("b", "Home Page", ["staticText"]),
        el("c", "About", ["staticText"]),
    ]
    found = find_all(screen, {"labelMatches": ".*Page$"})
    assert [e["identifier"] for e in found] == ["a", "b"]


def test_find_all_id_only_uses_index() -> None:
    """find_all with an id-only selector uses a cached index for O(1) lookup."""
    from bajutsu.drivers.base import _id_index

    screen: list[Element] = [
        el("a", "A", ["button"]),
        el("b", "B", ["cell"]),
        el("c", "C", ["button"]),
    ]
    # First call builds the index
    idx1 = _id_index(screen)
    assert idx1["a"] == [screen[0]]
    assert idx1["b"] == [screen[1]]
    assert idx1.get("missing") is None
    # Second call on the same list returns the cached index
    idx2 = _id_index(screen)
    assert idx2 is idx1


def test_find_all_id_index_invalidates_on_new_list() -> None:
    """The id index cache invalidates when a new element list is passed."""
    from bajutsu.drivers.base import _id_index

    screen1: list[Element] = [el("a", "A")]
    screen2: list[Element] = [el("b", "B")]
    idx1 = _id_index(screen1)
    idx2 = _id_index(screen2)
    assert idx2 is not idx1
    assert "b" in idx2
    assert "a" not in idx2


# --- id / idMatches candidate lists (BE-0221): match ANY candidate ---
# A shared scenario carries every platform's form of an id (`[stable.refresh, stable_refresh]`) so
# it runs unchanged where the native id syntax differs. Only one form is ever on screen per app.

# Compose surfaces the dotted SPEC id verbatim; the native android:id (Views) form is not present.
_COMPOSE: list[Element] = [el("stable.refresh", "更新", ["button"])]
# The Views build maps the same id to underscores; the dotted form is not present.
_VIEWS: list[Element] = [el("stable_refresh", "更新", ["button"])]


def test_id_list_matches_either_platform_form() -> None:
    # The identical selector resolves against whichever id the app actually renders.
    sel = {"id": ["stable.refresh", "stable_refresh"]}
    assert resolve_unique(_COMPOSE, sel)["label"] == "更新"
    assert resolve_unique(_VIEWS, sel)["label"] == "更新"


def test_id_list_not_found_when_no_candidate_present() -> None:
    try:
        resolve_unique([el("other")], {"id": ["stable.refresh", "stable_refresh"]})
    except ElementNotFound:
        return
    raise AssertionError("どの候補も無ければ ElementNotFound")


def test_id_list_ambiguous_when_two_forms_on_one_screen() -> None:
    # Determinism is unchanged: if both candidate forms are present, the selector is ambiguous and
    # fails fast rather than picking one — an OR never masks a 2+ match (prime directive 2).
    both = [el("stable.refresh", "A", ["button"]), el("stable_refresh", "B", ["button"])]
    try:
        resolve_unique(both, {"id": ["stable.refresh", "stable_refresh"]})
    except AmbiguousSelector:
        return
    raise AssertionError("両形が同一画面にあれば曖昧で即失敗するべき")


def test_id_list_find_all_matches_in_elements_order() -> None:
    screen = [el("a"), el("b"), el("c")]
    found = find_all(screen, {"id": ["c", "a"]})
    assert [e["identifier"] for e in found] == ["a", "c"]  # elements order, not candidate order


def test_id_matches_list_matches_any_glob() -> None:
    # `count` over a shared scenario: dotted glob for Compose, underscore glob for Views.
    compose = [el("stable.row.1"), el("stable.row.2")]
    views = [el("stable_row_1"), el("stable_row_2")]
    sel = {"idMatches": ["stable.row.*", "stable_row_*"]}
    assert len(find_all(compose, sel)) == 2
    assert len(find_all(views, sel)) == 2


def test_id_candidates_normalizes_scalar_and_list() -> None:
    from bajutsu.drivers.base import id_candidates

    assert id_candidates("x") == ["x"]
    assert id_candidates(["x", "y"]) == ["x", "y"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"\n{len(fns)} passed")
