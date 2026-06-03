"""ドライバ抽象 — 両バックエンド（RocketSim / idb）の要（DESIGN.md §5）。

ここが崩れると全体に波及するため、最初に凍結する契約:
- 共通型 `Point` / `Element` / `Selector`
- `Driver` Protocol（操作は actuator のみが行う。§9）
- セレクタ解決のセマンティクス（§5「決定性の要」）= 単一アクションは一意解決を要求し、
  曖昧（2 件以上）なら `AmbiguousSelector` を送出して非決定性を構造で排除する。
"""

from __future__ import annotations

import fnmatch
import re
from typing import Protocol, TypedDict, runtime_checkable

# 座標（points）。x, y。
Point = tuple[float, float]
# frame: x, y, w, h（points）。
Frame = tuple[float, float, float, float]


class Capability:
    """`Driver.capabilities()` が返す能力名（§9 actuator + フォールバック解決に使う）。

    操作の安定度順（§5 stability ladder）では `SEMANTIC_TAP` を持つ backend ほど安定。
    """

    QUERY = "query"
    SEMANTIC_TAP = "semanticTap"      # id/label で直接 tap（座標を介さない＝最安定）
    CONDITION_WAIT = "conditionWait"  # ネイティブ条件待機
    NETWORK = "network"               # ネイティブネットワーク監視
    SCREENSHOT = "screenshot"
    ELEMENTS = "elements"


class Element(TypedDict):
    """画面上の 1 要素。RocketSim / idb の出力を共通形へ正規化したもの（§5）。"""

    identifier: str | None
    label: str | None
    traits: list[str]
    value: str | None
    frame: Frame


class Selector(TypedDict, total=False):
    """要素の指定（§5）。指定した全フィールドが AND で適用される。

    安定セレクタは `id`（非ローカライズ・データ由来）。`label`/`labelMatches` は補助、
    `index` は最終手段（フレーキー注意）。命名規約は §7.3。
    """

    id: str            # accessibilityIdentifier 完全一致（第一候補）
    idMatches: str     # glob パターン（複数マッチ前提。例 "*.submit"）
    label: str         # accessibilityLabel 完全一致（補助・曖昧解消のみ）
    labelMatches: str  # label の部分一致 / 正規表現
    traits: list[str]  # 型で絞る（例 ["button"]）
    value: str         # accessibility value 一致
    within: "Selector"  # 親要素でスコープ限定（階層クエリが必要・未実装）
    index: int         # 複数マッチ時の n 番目（最終手段・フレーキー注意）


@runtime_checkable
class Driver(Protocol):
    """両バックエンド共通インターフェース（§5）。

    操作（tap/type/swipe/wait/query）は actuator のみが行う（§9）。idb のように
    semantic tap を持たない backend では、抽象側が `query()` → `resolve_unique()` で
    frame 中心を引き当ててから座標 tap する（§5 stability ladder 順 2）。
    """

    def query(self) -> list[Element]: ...
    def tap(self, sel: Selector) -> None: ...
    def long_press(self, sel: Selector, duration: float) -> None: ...
    def swipe(self, frm: Point, to: Point) -> None: ...
    def type_text(self, text: str) -> None: ...
    def wait_for(self, sel: Selector, timeout: float) -> bool: ...
    def screenshot(self, path: str) -> None: ...
    def capabilities(self) -> set[str]: ...


# --- セレクタ解決（§5「決定性の要」）---------------------------------------------


class SelectorError(Exception):
    """セレクタ解決の失敗（§5）。"""


class ElementNotFound(SelectorError):
    """候補 0 件。`wait_for` 経由ならタイムアウト、即時アクションなら失敗。"""


class AmbiguousSelector(SelectorError):
    """候補 2 件以上で一意化できない。`within` か `index` で一意化が必要。"""


def matches(el: Element, sel: Selector) -> bool:
    """Element が Selector の全条件（AND）を満たすか。

    `within` は要素ツリーの親子関係が必要なため、現状の平坦な `Element` では未対応。
    """
    if "within" in sel:
        raise NotImplementedError("`within` は階層クエリが必要（将来対応）")
    if "id" in sel and el["identifier"] != sel["id"]:
        return False
    if "idMatches" in sel and not (
        el["identifier"] is not None and fnmatch.fnmatchcase(el["identifier"], sel["idMatches"])
    ):
        return False
    if "label" in sel and el["label"] != sel["label"]:
        return False
    if "labelMatches" in sel and not (
        el["label"] is not None and re.search(sel["labelMatches"], el["label"]) is not None
    ):
        return False
    if "traits" in sel and not set(sel["traits"]).issubset(el["traits"]):
        return False
    if "value" in sel and el["value"] != sel["value"]:
        return False
    return True


def find_all(elements: list[Element], sel: Selector) -> list[Element]:
    """条件に一致する全要素（`idMatches` トリガーや `count` アサーション用。§5 / §6.4）。"""
    return [el for el in elements if matches(el, sel)]


def resolve_unique(elements: list[Element], sel: Selector) -> Element:
    """単一アクション（tap 等）向けに一意解決する（§5）。

    - 0 件 → ``ElementNotFound``
    - 2 件以上 → ``AmbiguousSelector``（「たまたま最初の一致を叩く」非決定性を排除）
    - ``index`` 指定時のみ複数候補から n 番目を選ぶ（最終手段・フレーキー注意）
    """
    candidates = find_all(elements, sel)
    if "index" in sel:
        i = sel["index"]
        if not -len(candidates) <= i < len(candidates):
            raise ElementNotFound(f"index {i} は候補 {len(candidates)} 件の範囲外: {sel!r}")
        return candidates[i]
    if not candidates:
        raise ElementNotFound(f"一致なし: {sel!r}")
    if len(candidates) > 1:
        raise AmbiguousSelector(
            f"{len(candidates)} 件一致: {sel!r} — `within` か `index` で一意化が必要"
        )
    return candidates[0]
