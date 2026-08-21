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
        "nativeZ": None,
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


def test_index_negative_counts_from_the_end() -> None:
    # A negative index selects from the end (Python semantics): -1 is the last match, -2 the one
    # before it. Deterministic on an ordered candidate list, so it disambiguates like a positive one.
    assert resolve_unique(SCREEN, {"idMatches": "result.row.*", "index": -1})["identifier"] == (
        "result.row.2"
    )
    assert resolve_unique(SCREEN, {"idMatches": "result.row.*", "index": -2})["identifier"] == (
        "result.row.1"
    )


def test_index_out_of_range_raises_at_either_end() -> None:
    # Two candidates: valid indices are 0..1 and -2..-1. A positive index at/over the count, or a
    # negative one below -count, is out of range and fails loudly rather than wrapping or clamping.
    # (Message captured then asserted outside the `except`, keeping this file pytest-free — its
    # docstring promises it also runs directly.)
    for bad in (2, -3):
        message = ""
        try:
            resolve_unique(SCREEN, {"idMatches": "result.row.*", "index": bad})
        except ElementNotFound as e:
            message = str(e)
        assert "範囲外" in message, f"index {bad} は範囲外で ElementNotFound になるべき"


def test_index_on_no_candidates_is_out_of_range() -> None:
    # An index against zero matches has no valid slot (0 is already ==len), so it fails as out of
    # range — never an IndexError leaking from the raw list access.
    message = ""
    try:
        resolve_unique(SCREEN, {"id": "nope", "index": 0})
    except ElementNotFound as e:
        message = str(e)
    assert "範囲外" in message, "候補ゼロへの index は範囲外であるべき"


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


# --- identical-content duplicate collapsing (a UIAlertController button double-registered on
# XCUITest: same identifier/label/traits/value/frame, indistinguishable and stable for the
# alert's lifetime, so `index` cannot pick a "real" one) ---


def test_identical_duplicates_resolve_without_index() -> None:
    ok_button = el("alert.ok", "OK", ["button"], frame=(201.0, 470.0, 134.0, 44.0))
    screen = [ok_button, el("alert.ok", "OK", ["button"], frame=(201.0, 470.0, 134.0, 44.0))]
    got = resolve_unique(screen, {"id": "alert.ok"})
    assert got["identifier"] == "alert.ok"
    assert got["frame"] == (201.0, 470.0, 134.0, 44.0)


def test_duplicates_differing_in_frame_still_ambiguous() -> None:
    # Same identifier/label/traits but a different frame is a genuine 2+ match — not the ghost
    # duplicate this collapsing targets — so it must still raise.
    screen = [
        el("alert.ok", "OK", ["button"], frame=(201.0, 470.0, 134.0, 44.0)),
        el("alert.ok", "OK", ["button"], frame=(201.0, 300.0, 134.0, 44.0)),
    ]
    try:
        resolve_unique(screen, {"id": "alert.ok"})
    except AmbiguousSelector:
        return
    raise AssertionError("frame が異なる候補は畳まれず AmbiguousSelector になるべき")


def test_duplicates_with_differently_ordered_traits_still_collapse() -> None:
    # `matches` already treats `traits` as a set (`issubset`); the same two traits reported in a
    # different order are the same content, not a genuine difference to key the collapse on.
    screen = [
        el("alert.ok", "OK", ["button", "notEnabled"], frame=(201.0, 470.0, 134.0, 44.0)),
        el("alert.ok", "OK", ["notEnabled", "button"], frame=(201.0, 470.0, 134.0, 44.0)),
    ]
    got = resolve_unique(screen, {"id": "alert.ok"})
    assert got["identifier"] == "alert.ok"


def test_id_candidates_normalizes_scalar_and_list() -> None:
    from bajutsu.drivers.base import id_candidates

    assert id_candidates("x") == ["x"]
    assert id_candidates(["x", "y"]) == ["x", "y"]


# --- `other`-trait ties are dropped before judging ambiguity (§traits) ---
# A generic wrapper (e.g. iOS's catch-all XCUIElementTypeOther) commonly repeats a real element's
# label; such a duplicate shouldn't force every scenario to add `within`/`index` just to route
# around it.


def test_other_trait_duplicate_is_ignored_on_label_tie() -> None:
    screen: list[Element] = [
        el("real.button", "設定", ["button"]),
        el("wrapper", "設定", ["other"]),  # generic container repeating the button's label
    ]
    assert resolve_unique(screen, {"label": "設定"})["identifier"] == "real.button"


def test_other_trait_duplicate_ignored_leaves_find_all_unfiltered() -> None:
    # find_all (backs `count` / `exists`) is unaffected — only resolve_unique's ambiguity
    # judgment drops `other` ties.
    screen: list[Element] = [
        el("real.button", "設定", ["button"]),
        el("wrapper", "設定", ["other"]),
    ]
    assert len(find_all(screen, {"label": "設定"})) == 2


def test_all_other_candidates_still_raise_ambiguous() -> None:
    # When every tied candidate is `other`, there is nothing non-`other` to fall back to — still
    # a genuine ambiguity.
    screen: list[Element] = [
        el("a", "重複", ["other"]),
        el("b", "重複", ["other"]),
    ]
    try:
        resolve_unique(screen, {"label": "重複"})
    except AmbiguousSelector:
        return
    raise AssertionError("すべて other なら曖昧のままであるべき")


def test_explicit_other_trait_selector_is_not_filtered() -> None:
    # A selector that explicitly asks for `other` elements opts back into judging them normally.
    screen: list[Element] = [
        el("a", "重複", ["other"]),
        el("b", "重複", ["other"]),
    ]
    try:
        resolve_unique(screen, {"label": "重複", "traits": ["other"]})
    except AmbiguousSelector:
        return
    raise AssertionError("traits で other を明示指定した場合も曖昧は曖昧のまま")


def test_index_counts_over_the_other_filtered_candidates() -> None:
    # index must count the same, already `other`-filtered set the ambiguity message reports —
    # not the raw find_all result, where a dropped `other` would shift later positions by one.
    screen: list[Element] = [
        el("a", "設定", ["button"]),
        el("b", "設定", ["button"]),
        el("wrapper", "設定", ["other"]),  # generic container repeating the buttons' label
    ]
    assert resolve_unique(screen, {"label": "設定", "index": 0})["identifier"] == "a"
    assert resolve_unique(screen, {"label": "設定", "index": 1})["identifier"] == "b"
    message = ""
    try:
        resolve_unique(screen, {"label": "設定", "index": 2})
    except ElementNotFound as e:
        message = str(e)
    assert "範囲外" in message, "index 2 は other を除いた候補2件の範囲外であるべき"


def test_explicit_other_trait_selector_disambiguates_by_index_too() -> None:
    # traits: ["other"] opts the whole `other`-drop heuristic out (matching
    # test_explicit_other_trait_selector_is_not_filtered above), so index here counts the two
    # matched `other` elements themselves rather than a heuristic-reduced set.
    screen: list[Element] = [
        el("a", "重複", ["other"]),
        el("b", "重複", ["other"]),
    ]
    sel = {"label": "重複", "traits": ["other"]}
    assert resolve_unique(screen, {**sel, "index": 0})["identifier"] == "a"
    assert resolve_unique(screen, {**sel, "index": 1})["identifier"] == "b"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"\n{len(fns)} passed")
