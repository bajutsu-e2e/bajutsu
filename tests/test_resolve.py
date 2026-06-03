"""セレクタ解決のセマンティクス（DESIGN.md §5「決定性の要」）のテスト。

方針「決定性をテストで担保」に従い、抽象の最初の凍結と同時に固定する。
依存なしで動くよう pytest / 直接実行の両対応。
"""

from __future__ import annotations

from simpilot.drivers.base import (
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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"\n{len(fns)} passed")
