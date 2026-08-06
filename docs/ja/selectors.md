[English](../selectors.md) · **日本語**

# セレクタと決定的解決（決定性の核）

> 「どの要素を操作または検証するか」をどう指定し、どう一意に確定するかを説明します。Bajutsu の決定性はこのモジュールに集約されています。すべての実行系（orchestrator / drivers / assertions）がここに依存します。
>
> 実装: `bajutsu/drivers/base.py`。

関連: [concepts の決定性原則](concepts.md#3-決定性ファースト4-つの具体策) · [scenarios の DSL](scenarios.md#アサーション-dsl) · [drivers](drivers.md)

---

## 正規化された要素（`Element`）

ドライバはバックエンドの出力を共通の `Element`（TypedDict）へ正規化します。解決とアサーションはこの正規化形だけを参照します（バックエンド差はドライバ側で吸収済みです）。

```python
class Element(TypedDict):
    identifier: str | None        # 安定 id（iOS は accessibilityIdentifier・web は data-testid）
    label: str | None             # accessibilityLabel
    traits: list[str]             # 正規化トレイト（下記）
    value: str | None             # accessibility value
    frame: tuple[float, float, float, float]  # x, y, w, h（points）
```

### 正規化トレイト（`Trait`）

状態アサーションやセレクタ、各種判定が参照する共通トークンです。ドライバは少なくとも次を正規化します:

| トークン | 意味 | 用途 |
|---|---|---|
| `button` / `link` | 種別 | `traits` セレクタ、doctor の actionable 判定 |
| `notEnabled` | 無効状態 | `enabled` / `disabled` |
| `selected` | 選択 / トグル ON | `selected` |
| `other` | 汎用・未分類の要素（iOS の catch-all `XCUIElementTypeOther` など） | `resolve_unique` の曖昧判定（後述） |

> 各バックエンドが自分自身の属性をこれらのトークンへ正規化します。adb は `enabled="false"` を `notEnabled` に正規化します。`selected="true"` /
> `checked="true"` は `selected` に正規化します。XCUITest の常駐ランナーは `isEnabled` が false のとき同様に `notEnabled` に正規化します。
> バックエンドごとの正規化の詳細は [drivers](drivers.md) を参照してください。

## セレクタ（`Selector`）

要素のアドレス指定に使います。**指定したフィールドはすべて AND** で適用されます。

| フィールド | 意味 | 安定性 |
|---|---|---|
| `id` | `accessibilityIdentifier` の完全一致。**リスト**は候補の OR（いずれかに一致） | ★ 第一候補 |
| `idMatches` | id の glob パターン（複数マッチ前提。例 `"list.row.*"`）。**リスト**はいずれかの glob に一致すればよい | 集合操作用 |
| `label` | `accessibilityLabel` の完全一致 | 補助 / 曖昧解消のみ |
| `labelMatches` | label の部分一致 / 正規表現（`re.search`） | 補助 |
| `traits` | トレイトで絞る（部分集合判定。例 `["button"]`） | 補助 |
| `value` | accessibility value の完全一致 | 補助 |
| `within` | コンテナでスコープ限定（幾何: 候補の frame が `within` の解決先の内側にあること。ネスト可） | 一意化 |
| `index` | 複数マッチ時の n 番目（負数可） | 最終手段、フレーキー |

> `id` / `idMatches` のマッチは `fnmatch.fnmatchcase`（大小区別あり glob）、`labelMatches` は `re.search`（正規表現 / 部分一致）、`traits` は「指定集合 ⊆ 要素のトレイト集合」です。

> `id` / `idMatches` は**候補のリスト**も受け付けます。OR として、要素の id がいずれかの候補に一致（または glob 一致）すればマッチします（BE-0221）。これにより 1 つの共有シナリオがプラットフォームごとに異なる id 表記を持てます（例: Android Views の `android:id` は `.`/`-` を許さないので `id: [stable.refresh, stable_refresh]`）。あるアプリの画面に現れる形は常に一方だけなので決定的なままで、2 件以上一致すれば従来どおり即失敗します。[scenarios](scenarios.md#プラットフォームをまたぐ-id候補のリストbe-0221) を参照してください。

### オーサリング表現と実行時表現

- シナリオ YAML 側の[セレクタ](glossary.md#シナリオのオーサリング)は `scenario/models/selector.py` の `Selector`（pydantic、`idMatches` 等の alias を持つ）です。
- 解決に渡るのは `drivers/base.py` の `Selector`（TypedDict）です。
- 変換は `Selector.as_selector()` で行います（`None` を除いて TypedDict 化）。

## 解決セマンティクス

`query()` で得た要素リストにセレクタを適用して候補を絞ります。3 つの公開関数があります。

### `matches(el, sel) -> bool`

1 要素が要素単位の条件を満たすかを返します（AND）。`within` は要素横断（空間）の制約で、`find_all` 側で解決します。

### `find_all(elements, sel) -> list[Element]`

一致する **すべて** の要素を返します。`idMatches` トリガーや `count` アサーション、`exists` 判定に使います（複数マッチを許容します）。

### `resolve_unique(elements, sel) -> Element`

**単一アクション用に、ちょうど 1 件へ確定します。** 曖昧一致による非決定性をここで断つ、決定性の核となる関数です。

| 候補数 | 挙動 |
|---|---|
| 0 件 | `ElementNotFound`（即時アクションは失敗、待機（`wait_until`）経由はタイムアウト） |
| 1 件 | 解決成功 |
| 2 件以上 | `AmbiguousSelector` を送出。「たまたま最初の一致を叩く」非決定性を**構造的に排除**する |

`resolve_unique` は候補数を数える前に、identifier・label・traits・value・frame のすべてが一致する候補を1件へ畳みます。これは XCUITest の既知の癖への対処です。標準の `UIAlertController` のボタンは、アクセシビリティツリー上に見分けのつかない状態で二重登録されることがあり、その状態はアラートが表示され続ける間ずっと持続します。この2件の「候補」は区別する情報を何も持たないため、`index` では「本物」を選べません（実行ごとに、どちらの実体を実際にタップするかが入れ替わります）。`index` は、何らかの項目で実際に異なる候補にのみ使う手段として残ります。見分けのつかない重複に対しては、`index` は不要であり使われません。

続いて、2 件以上の一致を曖昧と判定する前に、`other` トレイトを持つ候補を除外します。汎用のラッパー要素（iOS の catch-all `XCUIElementTypeOther` など）は、実体のある要素の label をそのまま繰り返すことが多いからです。そうした重複のためだけに、シナリオへ `within` や `index` を足させたくありません。一致した候補がすべて `other` なら、それ以上除外する先がないため、そのまま曖昧判定にかけます。セレクタが `traits: ["other"]` で `other` を明示的に要求している場合も同様です。この除外は `resolve_unique` に閉じています。`find_all`（したがって `count` / `exists`）は、`other` を含めすべての一致をそのまま返します。

例外として `index` が指定されたときだけ、複数候補から n 番目を選びます（範囲外は `ElementNotFound`）。この除外は `index` の分岐より前に走ります。そのため `index` は、上記の曖昧件数と同じ、除外後の候補集合を数えます。除外前の `find_all` の結果を数えてしまうと、取り除かれた `other` の分だけ後続の位置がずれます。`index` は順序変化でも壊れるため、いずれにせよ最終手段です。集合を扱う場合は `idMatches` + `count` を使ってください（[scenarios](scenarios.md#アサーション-dsl)）。

> **トレードオフ**：iOS では `other` が、このドライバが名前を付けていない実在のコントロールも含みます。`checkBox`、`radioButton`、`popUpButton`、`stepper`、`datePicker` などは、汎用ラッパーと同じく `typeName` の `default:` 節に落ちます（`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`）。そうしたコントロールが、同じ label を持つ分類済みの兄弟要素と衝突すると、`AmbiguousSelector` を送出せず分類済みの側を黙って残します。影響するのは同一セレクタでの衝突だけであり、分類済みの兄弟要素がなく単独で解決される未分類コントロールには影響しません。

```python
# drivers/base.py（抜粋）
def resolve_unique(elements, sel):
    candidates = _collapse_identical_duplicates(find_all(elements, sel))
    if len(candidates) > 1 and "other" not in sel.get("traits", []):
        without_other = [c for c in candidates if "other" not in c["traits"]]
        if without_other:
            candidates = without_other  # other 同士の重複は除外（全滅時は残す）
    if "index" in sel:
        ...                         # 除外後の集合で n 番目（範囲外は ElementNotFound）
    if not candidates:
        raise ElementNotFound(...)
    if len(candidates) > 1:
        raise AmbiguousSelector(...)  # within か index で一意化が必要
    return candidates[0]
```

例外階層: `SelectorError`（基底） ← `ElementNotFound` / `AmbiguousSelector`。orchestrator と assertions はこれを捕捉して「ステップ失敗」「アサーション失敗」に変換します（例外を上に投げません）。

### `ElementNotTappable`: 解決はしたが到達できない対象

`resolve_unique` が判定するのは一致件数だけです。その一致した要素が実際に画面上で到達可能かどうかは判定しません。要素はセレクタに一意に一致し、有効な frame を持ちながら、固定ヘッダーやトースト、あるいは薄暗いモーダルの背景の下に置かれていることがあります。その場合、タップは対象ではなく遮蔽物に当たってしまいます。`tap` / `double_tap` / `long_press`（そして `type` / `clear` / `delete` / `select` の内部にあるフォーカスタップ）は、操作前にこれを確認するようになりました。各プラットフォームがもっとも自然に提供する手段（iOS のネイティブな `isHittable`、web の `document.elementFromPoint` によるヒットテスト、adb のドキュメント順による幾何学的な近似、`Driver.is_tappable`）を使います。この確認に失敗すると、オーケストレータが小さく回数を区切った `down` 方向のみのスクロールを最大 3 回まで試し、操作をもう一度だけ再試行します。それでも対象へ到達できなければ、`ElementNotFound` ではなく `ElementNotTappable` を送出します。呼び出し側が「ツリーに存在すらしない」と誤解しかねない `ElementNotFound` の代わりです。

`ElementNotTappable` は `SelectorError` の派生ではなく、その兄弟です。セレクタ自体は解決しているため、解決失敗と同じ扱いにまとめると「何が一致したか」と「到達可能か」という別の問いをぼかしてしまいます。orchestrator のステップ実行 catch は、`SelectorError` を扱うのと同じ形でこれを扱います。クリーンなステップ失敗であり、クラッシュではありません。

この回数を区切ったスクロールは、作者が予見できない遮蔽（一時的なオーバーレイ、位置が定まりきっていないスティッキーヘッダーなど）に対する安全網です。明示的な[`scroll` アクション](scenarios.md#scroll)の代替ではありません。対象が最初から画面外にあるとすでに知っている作者は、それでも自分で `scroll` を書きます。このチェックが働くのは、すでに解決できた対象に対してだけです。

### バックエンドに依らず一元化される

adb（Android）、playwright（web）、fake ドライバは semantic tap を持たないため、いずれも**常に `query()` で候補数を検証してから**操作し、確定した要素の frame 中心をタップします。XCUITest も同じ検証を経てから、座標ではなく識別子を指定して直接タップします。すべてのアクションが同じ `resolve_unique` を通るため、「曖昧なら失敗」の挙動はすべてのバックエンドで同一です（各ドライバの `tap` 実装は [drivers](drivers.md) を参照してください）。

`id` は各バックエンドが自分自身のアクセシビリティ id から取得します。XCUITest は `accessibilityIdentifier`、adb は `resource-id`（パッケージ接頭辞を除去）、web は `data-testid` です。いずれも `Element.identifier` に正規化されるため、`id` セレクタは正規化形に対して直接解決できます。

## アサーション評価

実装: `bajutsu/assertions/`（`evaluate.py`。BE-0250 で単一モジュールから分割）。`evaluate(elements, assertions) -> list[AssertionResult]` が各アサーションを評価し、`passed(results)` が AND を取ります。**評価は総関数**で、解決失敗（not-found / ambiguous）も例外でなく「失敗した `AssertionResult`」として返します（そのままレポートに載ります）。

```python
@dataclass(frozen=True)
class AssertionResult:
    ok: bool
    kind: str        # "exists" / "value" / ...
    detail: str      # 何を検査したか（レポート用）
    reason: str      # 失敗理由（ok のとき空）
```

種別ごとの仕組み（このページが扱う 8 種別）:

| 種別 | 解決 | 判定 |
|---|---|---|
| `exists` | `find_all` で 1 件以上か | `found != negate`（負論理で不在検証） |
| `value` | `resolve_unique`（曖昧 / 不在は失敗） | `value` を `equals`/`contains`/`matches` で比較 |
| `label` | 同上 | `label` を同様に比較 |
| `count` | `find_all` の件数 | `equals`/`atLeast`/`atMost` |
| `enabled` | `resolve_unique` | `notEnabled` トレイトが **無い** |
| `disabled` | `resolve_unique` | `notEnabled` トレイトが **有る** |
| `selected` | `resolve_unique` | `selected` トレイトが有る |
| `request` | 観測した通信を照合（要素ツリーではない） | `count` 指定時は `equals`/`atLeast`/…、無指定なら 1 件以上（[network](network.md)） |

> `exists` だけ `find_all`（複数許容）を使い、他の単一要素アサーションは `resolve_unique`（曖昧は失敗）を使います。「2 件あるのに値を検証しようとした」場合も決定的に失敗します。上の表は、このページの解決セマンティクスが関わる 8 種別（要素ツリーを読む 7 種別と、要素ではなくキャプチャした HTTP(S) 通信を検査する `request`）をカバーします。残る `event` / `requestSequence` / `responseSchema` / `visual` / `clipboard` / `golden` の 6 種別は要素解決を経由しません。全種別は [scenarios](scenarios.md#アサーション-dsl) を参照してください。
