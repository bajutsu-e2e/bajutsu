[English](../selectors.md) · **日本語**

# セレクタと決定的解決（決定性の核）

> 「どの要素を操作または検証するか」をどう指定し、どう一意に確定するかを説明します。Bajutsu の決定性はこのモジュールに集約されています。すべての実行系（orchestrator / drivers / assertions）がここに依存します。
>
> 実装: `bajutsu/common/drivers/base/_functions.py`。

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
- 解決に渡るのは `common/drivers/base/selector.py` の `Selector`（TypedDict）です。
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

> **トレードオフ**：iOS では `other` が、このドライバが名前を付けていない実在のコントロールも含みます。`checkBox`、`radioButton`、`popUpButton`、`stepper`、`datePicker` などは、汎用ラッパーと同じく `typeName` の `default:` 節に落ちます（`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`）。そうしたコントロールが、同じ label を持つ分類済みの兄弟要素と衝突すると、`AmbiguousSelector` を送出せず分類済みの側を黙って残します。影響するのは同一セレクタでの衝突だけであり、分類済みの兄弟要素がなく単独で解決される未分類コントロールには影響しません。`UIDatePicker` は、この隙間が実質的な損失にならない例です。`other` のコンテナの下にあるホイールはそれぞれ `pickerWheel` に分類されるため、[`setPickerValue`](scenarios.md#setpickervalue) はホイールを直接指定できます。日付ピッカーの値を設定するために、親要素の分類を埋める必要はありません。

```python
# common/drivers/base/_functions.py（抜粋）
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

`resolve_unique` が判定するのは一致件数だけです。その一致した要素が実際に画面上で到達可能かどうかは判定しません。要素はセレクタに一意に一致し、有効な frame を持ちながら、固定ヘッダーやトースト、あるいは薄暗いモーダルの背景の下に置かれていることがあります。その場合、タップは対象ではなく遮蔽物に当たってしまいます。`tap` / `double_tap` / `long_press`（そして `type` / `clear` / `delete` / `select` の内部にあるフォーカスタップ）は、操作前にこれを確認するようになりました。各プラットフォームがもっとも自然に提供する手段（iOS のネイティブな `isHittable`、web の `document.elementFromPoint` によるヒットテスト、adb のドキュメント順による幾何学的な近似、`Driver.is_tappable`）を使います。この確認に失敗すると、オーケストレータが小さく回数を区切ったスクロールを試します。まず `down` 方向へ最大 3 回、それでも対象に到達できなければ `up` 方向へ最大 6 回まで試したうえで（`up` は `down` が残した分をまず巻き戻してからでないと自分の分の前進ができないため、上限を広げています）、操作をもう一度だけ再試行します。それでも対象へ到達できなければ、`ElementNotFound` ではなく `ElementNotTappable` を送出します。呼び出し側が「ツリーに存在すらしない」と誤解しかねない `ElementNotFound` の代わりです。

XCUITest バックエンドでは、拒まれた **tap** は失敗する前にもう 1 段だけ進みます。iOS は、包んでいる
コントロールより大きく膨らんだ container を報告することがあるからです。SwiftUI の `Stepper` は
アクセシビリティ要素がフォームの行全体に広がり、内側のボタンには問題なく届くのに container が拒まれます。
ドライバは対象の**名前付きの**子孫（文書順で後にあり、frame の内側にあり、identifier を持つもの）を
調べます。到達できるものがちょうど 1 つなら選択の余地がないのでそこへタップし、`substitution:
soleHittableDescendant` として記録します。レポートと `trace` のタイムラインのどちらも、操作した要素が
セレクタの名指した要素ではないと述べます。0 個または複数なら、作者に予測できない選択が生じるので、
どれかを選ばずに失敗し、メッセージが候補を名指します。実際の `Stepper` は到達できる子を 2 つ持つので、
`tap: { id: log.count }` にはそのあいだで唯一の意味がありません。これを行うのは `tap` だけです。
long-press を子へ向けるのは、同じ意図が目標に届くことではなく、別の意図だからです。

`ElementNotTappable` は `SelectorError` の派生ではなく、その兄弟です。セレクタ自体は解決しているため、解決失敗と同じ扱いにまとめると「何が一致したか」と「到達可能か」という別の問いをぼかしてしまいます。orchestrator のステップ実行 catch は、`SelectorError` を扱うのと同じ形でこれを扱います。クリーンなステップ失敗であり、クラッシュではありません。

この回数を区切ったスクロールは、作者が予見できない遮蔽（一時的なオーバーレイ、位置が定まりきっていないスティッキーヘッダーなど）に対する安全網です。明示的な[`scroll` アクション](scenarios.md#scroll)の代替ではありません。対象が最初から画面外にあるとすでに知っている作者は、それでも自分で `scroll` を書きます。このチェックが働くのは、すでに解決できた対象に対してだけです。

### バックエンドに依らず一元化される

adb（Android）、playwright（web）、fake ドライバは semantic tap を持たないため、いずれも**常に `query()` で候補数を検証してから**操作し、確定した要素の frame 中心をタップします。XCUITest も同じ検証を経てから、座標ではなく識別子を指定して直接タップします。すべてのアクションが同じ `resolve_unique` を通るため、「曖昧なら失敗」の挙動はすべてのバックエンドで同一です（各ドライバの `tap` 実装は [drivers](drivers.md) を参照してください）。

`id` は各バックエンドが自分自身のアクセシビリティ id から取得します。XCUITest は `accessibilityIdentifier`、adb は `resource-id`（パッケージ接頭辞を除去）、web は `data-testid` です。いずれも `Element.identifier` に正規化されるため、`id` セレクタは正規化形に対して直接解決できます。

## 別言語（Swift・Kotlin）への移植契約

[BE-0408](../../roadmaps/BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)
は `find_all` と `resolve_unique` を Swift と Kotlin へ移植します。デバイス側の実行器は、これにより
ホストへ往復せずセレクタを解決できます。2つの移植先は、このモジュールの Python 実装が選ぶのと同じ
要素へ解決しなければなりません。曖昧な一致に対しても、同じように失敗しなければなりません。本節は、
コードを読むだけでは見落としうる規則をまとめます。移植先が保つべき、閉じてはならない相違も2つ挙げ
ます。

`tests/fixtures/be0408/` は、この契約を機械的に検証できる形で保持します。`selector_resolution.json` は
`find_all` と `resolve_unique` を42件のケースで再生します。`android_derived_label.json` は Android
のラベル導出規則を9件のケースで再生します。`tests/test_selector_fixtures.py` が、各ケースをこの
モジュール自身の関数へ照合します。この照合が壊れる変更は、移植先へ届く前に高速ゲートで失敗します。

### 2つの異なるマッチングエンジン

`idMatches` と `labelMatches` は、マッチングエンジンを共有しません。`idMatches` は
`fnmatch.fnmatchcase` を使います。大文字と小文字を区別する、完全アンカーの glob です。
`labelMatches` は `re.search` を使います。アンカーなしの正規表現です。glob ライブラリの既定の
アンカー方式は、正規表現エンジンの既定と異なることが多いです。移植の際は、各フィールドの
エンジンが Python と同じアンカー方式になっているかを確認してください。`idMatches` は完全
アンカー、`labelMatches` はアンカーなしです。

### フィールドが存在することと、値が非 null であることは別

シナリオ側の `Selector` モデルは、解決へ渡す前にすべての `None` フィールドを落とします。これを
行うのは `as_selector()` で、`bajutsu/common/scenario/models/selector.py` に定義されています。
ワイヤー形式でフィールドが欠けていることは、常に「指定されていない」ことを意味します。「空として
指定された」ことは意味しません。移植先は、セレクタのフィールドへ明示的な null を渡されたとき、
それを「存在するが空」の条件として扱ってはなりません。

### `find_all` は例外を送出せず、要素は文書順で返る

`find_all` に曖昧性の概念はありません。一致したすべての要素を、入力リストと同じ順で返します。
`within` は、その結果をコンテナの frame の内側にある要素へ絞り込みます。この包含関係は幾何的
です。辺を含み、親ポインタを持たないフラットな要素リストの上で判定します。`within` セレクタは、
何段でもネストできます。境界の場合として、候補自身の frame がコンテナの frame と一致するケース
も包含に含まれます。自分自身をスコープする要素はこの判定を満たします。バグとして扱わず、想定内の
挙動として扱ってください。

それ自身が何にも一致しない `within` セレクタは、空のスコープ集合を作ります。結果全体も空に
なります。スコープなしで通過させることは決してありません。エラーになることもありません。
`contains` はスコープがゼロ件のときには一度も走らず、空の並びに対する `any()` はどの候補
についても偽になるためです。「コンテナが見つからないなら、スコープなしとみなして通す」と
いう扱いを選んだ移植先は、ホスト側では失敗するセレクタを解決してしまいます。この契約全体が
排除しようとしている、片側だけ解決してもう片側は失敗する、という食い違いそのものです。

### 重複を1件へ畳む鍵

`resolve_unique` は候補を数える前に、内容が同一の候補を1件の代表へ畳みます。ある既知の XCUITest
の癖がこの畳み込みを動機づけています。標準のアラートボタンが、見分けのつかない状態で二重登録
されることがあるためです。畳み込みの鍵は次のとおりです。

- `identifier`
- `label`
- トレイトの**集合**（順序は問いません）
- `value`
- frame の完全一致

`nativeZ` は意図的にこの鍵から外れています。この項目は診断専用で、識別には使いません。他の
すべてのフィールドが一致する2件の候補は、`nativeZ` の値がどれだけ離れていても畳まれます。

畳み込みが残すのは、同じ鍵を持つ候補のうち**最初の1件**です。順序は `find_all` の文書順に
従います。`index` は、この生き残った候補の順序で位置を数えます。移植先が素直なデータ構造を
選ぶと、ここでつまずきます。反復順序を保証しないハッシュマップ、たとえば Swift の
`Dictionary` を使うと、畳み込みが実際に発生した画面で、実行のたびに違う候補が生き残ります。
この規則が守ろうとしている画面そのもので、`index` 指定のタップが非決定的になってしまいます。
移植先は、挿入順を保つ構造か、それと同等の仕組みを畳み込みに使う必要があります。

### `other` トレイトの除外と、`index` が数える対象

`resolve_unique` は、曖昧性を判定する前に `other` トレイトを持つ候補をすべて除外します。この
除外には2つの例外があります。セレクタ自身が `other` を対象にしている場合、または残る候補が
すべて `other` の場合です。`index` はその後、除外後の集合の中で位置を数えます。生の `find_all`
の結果を数えるのではありません。除外された `other` の候補は、後続の位置を1つもずらしてはなり
ません。候補数がゼロに対する `index` は、「範囲外」であり「一致なし」ではありません。移植先の
独自のエラー分類も、この2種類を区別する必要があります。Python のメッセージ本文は実装固有です。
移植先が必要とするのは分類だけで、その文言までは要りません。

### Android のラベル導出規則

`text` や `content-desc` を持たない、クリック可能な Android のノードは、自分の子孫からラベルを
導出します。結合は深さ優先の行きがけ順で進みます。これは `_derived_label` で、
`bajutsu/common/drivers/adb/_functions.py` に定義されています。ネストしたクリック可能な子孫は、
部分木ごとまるごとスキップされます。その子孫は、それ自身が1つの独立したコントロールだからです。
自分のラベルを自分で持ちます。その子孫のテキストは、親のラベルへ折り込まれません。クリック不可の
コンテナは、ラベルを一切導出しません。導出が働くのはクリック可能なノードだけです。

**結合が取り込むのは子孫の `text` だけです。`content-desc` は取り込みません。**
`content-desc` はこのドライバ自身の値チャネルです。ショーケースアプリはアサーションの状態を
ここへ映します。ラベルへの結合に含めると、その値が漏れだしかねません。`text` を持たず
`content-desc` だけを持つ子孫は、結合に何も寄与しません。
`android_derived_label.json` の `descendant_content_desc_is_never_folded_into_the_join` が
これを固定しています。`content-desc` も取り込む移植は、端末へ届く前にこのケースで失敗します。

この規則はセレクタ解決より1段手前で働きます。セレクタが後で照合する `Element` そのものを
組み立てる段階だからです。デバイス側の Android 実行器の側も、自分のアクセシビリティ読み取り
から同じラベルへ到達しなければなりません。ホストの `/source` 読み取りが到達するのと同じ
ラベルです。そうでなければ、ラベルに基づくセレクタが、実行の片側では解決し、もう片側では
失敗しかねません。

### 移植先が保つべき、閉じてはならない2つの相違

既存の Swift の挙動のうち2つは、意図的にこのモジュールと異なります。新しい移植先は、どちらも
Python 側の挙動へ揃えてはなりません。

`PositionPath.framesEqual` は、frame の4つの値それぞれに1ポイント分の遊びを許します。
`BajutsuKit/Sources/BajutsuRunner/PositionPath.swift` にあります。このモジュール自身の重複
畳み込みの鍵は、代わりに frame を完全一致で比較します。2つの frame が測るタイミングは異なり
ます。重複畳み込みの frame は、1回のアトミックなスナップショットから得られます。そのため、
そこでは完全一致こそが正しい判定です。`PositionPath` の frame は、候補ごとに独自のライブな
再取得から得られます。そこでの遊びは、再取得が持ち込みうる計測ノイズを吸収します。2つの鍵が
比較するフィールドは揃えてください。両者の間の遊びの有無までは揃えないでください。

`PositionPath.attributesMatch` は `traits` を順序付き配列として比較します。このモジュール自身
の `matches` は、代わりに `traits` を集合として扱います。この2つは別の問いに答えています。
`PositionPath` が問うのは、特定の、すでに記録済みの要素がまだ同じ要素かどうかです。自分の
トレイトを常に一定の順序で報告するデバイスなら、その順序まで比較しても安全です。一方、新規の
`resolve_unique` の呼び出しには、比較すべき記録済みの順序がそもそもありません。だからこそ、
このフィールドを意味どおりの集合として扱います。

## アサーション評価

実装: `bajutsu/common/assertions/evaluate/_functions.py`。BE-0250 で単一モジュールから分割し、BE-0411 でクラスごとにパッケージ化しました。`evaluate(elements, assertions) -> list[AssertionResult]` が各アサーションを評価し、`passed(results)` が AND を取ります。**評価は総関数**で、解決失敗（not-found / ambiguous）も例外でなく「失敗した `AssertionResult`」として返します（そのままレポートに載ります）。

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
