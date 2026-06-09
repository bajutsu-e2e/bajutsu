[English](../selectors.md) · **日本語**

# セレクタと決定的解決（決定性の核）

> 「どの要素を操作・検証するか」をどう指定し、どう一意に確定するか。Bajutsu の決定性は
> **ここ** に集約される。すべての実行系（orchestrator / drivers / assertions）がこのモジュールに
> 依存する。
>
> 実装: `bajutsu/drivers/base.py`。

関連: [concepts の決定性原則](concepts.md#3-決定性ファースト4-つの具体策) ・ [scenarios の DSL](scenarios.md#アサーション-dsl) ・ [drivers](drivers.md)

---

## 正規化された要素（`Element`）

idb の出力を、ドライバが共通の `Element`（TypedDict）へ正規化する。
解決とアサーションはこの正規化形だけを見る（バックエンド差はドライバ側で吸収済み）。

```python
class Element(TypedDict):
    identifier: str | None        # accessibilityIdentifier
    label: str | None             # accessibilityLabel
    traits: list[str]             # 正規化トレイト（下記）
    value: str | None             # accessibility value
    frame: tuple[float, float, float, float]  # x, y, w, h（points）
```

### 正規化トレイト（`Trait`）

状態アサーションが見る共通トークン。ドライバは少なくとも次を正規化する:

| トークン | 意味 | 使うアサーション |
|---|---|---|
| `button` / `link` | 種別 | `traits` セレクタ・doctor の actionable 判定 |
| `notEnabled` | 無効状態 | `enabled` / `disabled` |
| `selected` | 選択 / トグル ON | `selected` |

（idb は `enabled: false` → `notEnabled`、`selected: true` → `selected` に正規化する。
種別文字列は `AX` 接頭辞を外し先頭小文字化: `AXButton` → `button`。`drivers/idb.py` 参照）

## セレクタ（`Selector`）

要素のアドレス指定。**指定したフィールドはすべて AND** で適用される。

| フィールド | 意味 | 安定性 |
|---|---|---|
| `id` | `accessibilityIdentifier` の完全一致 | ★ 第一候補 |
| `idMatches` | id の glob パターン（複数マッチ前提。例 `"list.row.*"`） | 集合操作用 |
| `label` | `accessibilityLabel` の完全一致 | 補助・曖昧解消のみ |
| `labelMatches` | label の部分一致 / 正規表現（`re.search`） | 補助 |
| `traits` | トレイトで絞る（部分集合判定。例 `["button"]`） | 補助 |
| `value` | accessibility value の完全一致 | 補助 |
| `within` | コンテナでスコープ限定（幾何: 候補の frame が `within` の解決先の内側にあること。ネスト可） | 一意化 |
| `index` | 複数マッチ時の n 番目（負数可） | 最終手段・フレーキー |

> `id` / `idMatches` のマッチは `fnmatch.fnmatchcase`（大小区別あり glob）、`labelMatches` は
> `re.search`（正規表現・部分一致）、`traits` は「指定集合 ⊆ 要素のトレイト集合」。

### オーサリング表現と実行時表現

- シナリオ YAML 側のセレクタは `scenario.py` の `Selector`（pydantic、`idMatches` 等の alias を持つ）。
- 解決に渡るのは `drivers/base.py` の `Selector`（TypedDict）。
- 変換は `Selector.as_selector()`（`None` を除いて TypedDict 化）。

## 解決セマンティクス

`query()` で得た要素リストにセレクタを適用して候補を絞る。3 つの公開関数がある。

### `matches(el, sel) -> bool`

1 要素が要素単位の条件を満たすか（AND）。`within` は要素横断（空間）の制約で、`find_all` 側で解決する。

### `find_all(elements, sel) -> list[Element]`

一致する **すべて** の要素。`idMatches` トリガーや `count` アサーション、`exists` 判定に使う
（複数マッチを許す）。

### `resolve_unique(elements, sel) -> Element`

**単一アクション用に、ちょうど 1 件へ確定する**。Bajutsu の決定性で最も重要な関数。

| 候補数 | 挙動 |
|---|---|
| 0 件 | `ElementNotFound`（即時アクションは失敗、`wait_for` 経由はタイムアウト） |
| 1 件 | 解決成功 |
| 2 件以上 | `AmbiguousSelector` を送出 — 「たまたま最初の一致を叩く」非決定性を**構造的に排除** |

例外として `index` が指定されたときだけ、複数候補から n 番目を選ぶ（範囲外は `ElementNotFound`）。
`index` は順序変化で壊れるため最終手段。集合を扱いたいときは `idMatches` + `count`（[scenarios](scenarios.md#アサーション-dsl)）。

```python
# drivers/base.py（抜粋）
def resolve_unique(elements, sel):
    candidates = find_all(elements, sel)
    if "index" in sel:
        ...                         # n 番目（範囲外は ElementNotFound）
    if not candidates:
        raise ElementNotFound(...)
    if len(candidates) > 1:
        raise AmbiguousSelector(...)  # within か index で一意化が必要
    return candidates[0]
```

例外階層: `SelectorError`（基底） ← `ElementNotFound` / `AmbiguousSelector`。
orchestrator と assertions はこれを捕捉して「ステップ失敗」「アサーション失敗」に翻訳する
（例外を上に投げない）。

### バックエンドに依らず一元化される

idb は使える semantic tap を持たないため、抽象側は **常に `query()` で
候補数を検証してから** 操作し、確定した要素の frame 中心をタップする。これで「曖昧なら失敗」の
挙動が idb / fake で同一になる（各ドライバの `tap` 実装は [drivers](drivers.md) 参照）。

`id` は idb の要素ツリー（`AXUniqueId`）から直接得られ、`Element.identifier` に正規化される。
そのため `id` セレクタは正規化形に対して直接解決できる。

## アサーション評価

実装: `bajutsu/assertions.py`。`evaluate(elements, assertions) -> list[AssertionResult]` が
各アサーションを評価し、`passed(results)` が AND を取る。**評価は総関数**で、解決失敗（not-found /
ambiguous）も例外でなく「失敗した `AssertionResult`」として返す（そのままレポートに載る）。

```python
@dataclass(frozen=True)
class AssertionResult:
    ok: bool
    kind: str        # "exists" / "value" / ...
    detail: str      # 何を検査したか（レポート用）
    reason: str      # 失敗理由（ok のとき空）
```

種別ごとの仕組み:

| 種別 | 解決 | 判定 |
|---|---|---|
| `exists` | `find_all` で 1 件以上か | `found != negate`（負論理で不在検証） |
| `value` | `resolve_unique`（曖昧 / 不在は失敗） | `value` を `equals`/`contains`/`matches` で比較 |
| `label` | 同上 | `label` を同様に比較 |
| `count` | `find_all` の件数 | `equals`/`atLeast`/`atMost` |
| `enabled` | `resolve_unique` | `notEnabled` トレイトが **無い** |
| `disabled` | `resolve_unique` | `notEnabled` トレイトが **有る** |
| `selected` | `resolve_unique` | `selected` トレイトが有る |

> `exists` だけ `find_all`（複数許容）で、他の単一要素アサーションは `resolve_unique`（曖昧は失敗）。
> つまり「2 件あるのに値を検証しようとした」場合も決定的に失敗する。
