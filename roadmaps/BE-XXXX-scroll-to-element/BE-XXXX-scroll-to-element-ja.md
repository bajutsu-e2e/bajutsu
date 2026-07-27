[English](BE-XXXX-scroll-to-element.md) · **日本語**

# BE-XXXX — `scroll` アクション：要素が現れるまでスクロールする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-scroll-to-element-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md), [BE-0227](../BE-0227-web-swipe-scroll-fidelity/BE-0227-web-swipe-scroll-fidelity-ja.md), [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md) |
<!-- /BE-METADATA -->

## はじめに

新しいシナリオアクション `scroll` は、画面外の要素を決定的にビューポート内へ引き出します。スクロール
可能な領域を一方向にスクロールし、各スクロールのあとに要素ツリーを再取得して、指定した対象が画面内に
入った瞬間に停止します。対象が現れないまま上限に達したときは、明示的に失敗します。各スクロールは**慣性を
持ちません**。1 ステップで進む距離は画面に対する一定の割合に固定され、勢いを残さないため、1 回の
スクロールが対象をビューポートの外へ飛び越してしまうことがありません。このアクションは iOS
（XCUITest）、Android（adb）、web（Playwright）の各バックエンドで、単一の `Driver` インターフェイスの
背後で同じように動きます。そのため、fold より下のコントロールへスクロールするシナリオは、3 つの
バックエンドで同じ記述のまま同じように動きます。

## 動機

縦に長い画面で画面外の要素へ到達することは、それ自体が第一級の要求です。設定リストの末尾にある
ログアウトボタン、フィードの 20 行目、縦長のフォームの下にある送信ボタンなどがこれにあたります。
それにもかかわらず、bajutsu にはこれを直接表現する方法がありません。現在の移植可能な書き方は、手で
調整した `swipe` の連なりに `wait` を続けるものですが、showcase 自身のフィクスチャがその連なりの
脆さを記録しています。[`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml)
では、作者は各 `swipe` をスクロール中も見え続ける行にアンカーし、1 スワイプの余白を取ってリストを
たどり、さらにもう 1 回スワイプを足しています。そのコメントは「リストの fling がより短く収まる場所でも
対象に届くように……ソフトウェアレンダリングの CI エミュレータは、ハードウェアアクセラレーションのある
端末より 1 スワイプで進む量が少ない。この余分な 1 ステップが、速い端末で対象を上へ行き過ぎさせずに、
その差を埋める」と述べています。つまり作者は、端末やレンダリング速度で変わるスクロールの勢いを、手で
補正しているのです。

このスクロールの勢いこそが脆さの根本であり、そのため「慣性なしでスクロールする」ことは、利便性では
なく決定性の要件になります。fling のジェスチャは速度を与えます。指が離れたあとに fling が進む距離は、
プラットフォームのスクロール物理と端末のフレームレートに依存します。同じジェスチャでも、速い端末は
遅い端末より遠くまで運びます。この行き過ぎに巻き込まれた対象は、ある端末では fold より下に、別の端末
では上に着地します。そのため、手元では動くスワイプ回数が CI では失敗したり、`wait` が見つける前に対象を
上へ通り過ぎたりします。慣性のないスクロールは、この変動要因を取り除きます。各ステップは画面に対する
一定の距離だけ進んでから停止するので、各ステップ後の再取得は、そのステップが対象を残したビュー
ポートのなかで確実に対象をとらえます。これは prime directive 2 が固定の `sleep` にすでに適用している
のと同じ理屈（予測できない持続時間を、確認できる条件で置き換える）を、スクロール距離に当てはめた
ものです。

この欠落は、単に便利機能が足りないというだけでなく、現時点での移植性の非対称でもあります。adb
バックエンドは、画面外のアクション対象に向かって内部ですでにスクロールし、リトライ回数で区切って
再取得します（[BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)、
[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) の `_scroll_into_view`）。しかし
[`docs/drivers.md`](../../docs/drivers.md) は、この回復を **adb 限定**であり、移植可能な書き方ではなく
意図的に堅牢化の安全網だと明記しています。XCUITest と Playwright は、対象が最初から画面外にあると
`tap` を即座に失敗させます。したがって、*同じ*シナリオが Android では数回のスワイプののちに通る一方、
iOS と web では失敗しえます。そして文書化された移植可能な答えは、手で調整した `swipe` の連なりのまま
です。明示的でバックエンドを横断する `scroll` アクションは、作者が一度書けばどのバックエンドでも同じに
読める 1 つの決定的な構文で、この非対称を解消します。

## 詳細設計

### `scroll` アクション

```yaml
# 画面外の行をビューポートに引き出してからタップする。
- scroll:
    to: { id: notice.row.20 }   # 引き出す要素（ループの停止条件）
    direction: down             # up | down | left | right（既定: down）
- tap: { id: notice.row.20 }
```

```yaml
# 画面全体ではなく特定のスクロール領域をスクロールし、試行回数を区切る。
- scroll:
    to: { label: "Log out", traits: [button] }
    direction: down
    within: { id: settings.list }   # ジェスチャを行うスクロールコンテナ
    maxScrolls: 25                   # 上限（既定 15）。見つからない対象は上限で失敗する
```

このアクションは対象セレクタと方向を持ち、加えて 2 つの任意の指定を取ります。

- **`to`**（必須）：アクションが引き出す[セレクタ](../../docs/selectors.md)です。ループの停止条件で
  あり、その条件は単なる存在よりも厳しくなっています。`to` が解決し、*かつ*その frame がビューポートの
  内側にある瞬間に、アクションは戻ります。存在するだけでは不十分です。バックエンドは画面外の要素を
  ツリーに残すことがあるからです。web バックエンドの `query()` は、スクロールで画面外へ出ただけの DOM
  ノードも返します。一方、native の lazy リストは、画面外の行をツリーから完全に外します。そのため
  `wait: { for: … }` の述語（存在だけ）では、スクロールで流れ去った web 要素を「すでに見つかった」と
  報告し、一度もスクロールしません。frame がビューポート内にあることを要求すれば、reveal は本物になり、
  バックエンド間で同一になります。frame は取得済みのどの要素にもすでに載っています。frame を比べる相手
  となるビューポートの範囲を与えることは、バックエンドごとの小さな関心事であり、Unit 3 で扱います。
- **`direction`**（既定 `down`）：コンテンツをどちらへスクロールしていくか（ビューポートが進む方向で
  あり、指のジェスチャの逆）です。`down` はコンテンツをさらに下へスクロールして、fold より下から
  始まる部分を引き出し、指を上へ動かすスワイプとして実現されます（ビューポートが下へ動くにつれて
  コンテンツは上へ滑ります）。`up`・`left`・`right` は他の軸を引き出します。`direction` はスクロール
  方向を指し、指の方向ではないので、`direction` が指の方向を指す `swipe` とは逆向きに読めます。方向は
  推論ではなく明示です。そのためアクションは、曖昧なリストをどちらへ動かすべきかを決して推測しません
  （prime directive 2）。
- **`within`**（任意）：ジェスチャを行うスクロールコンテナです。各スクロールはこのコンテナにアンカー
  され、コンテンツの末尾はこのコンテナのサブツリーから判定します。省略すると、アクションは画面全体を
  スクロールします。`within` は、入れ子のスクロールビューの内側にある対象（画面全体もスクロールする
  画面の内側にあるリスト）に、外側の面を動かすのではなく到達させるための指定です。
- **`maxScrolls`**（任意、既定 15）：アクションが失敗するまでのスクロールステップの最大回数です。
  ループを区切り、現れない対象が永遠にスクロールし続けるのではなく決定的に失敗するようにします。

### バックエンドを横断する慣性なしのスクロール

アクションの 1 ステップのスクロールは勢いを与えてはなりません。3 つのバックエンドは、既存の
`Driver.scroll`（[`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py)）の背後で、それぞれ異なる
プリミティブでこの保証に到達します。

- **web（Playwright）**はすでに慣性がありません。`scroll` はデスクトップコンテキストで wheel を回し、
  モバイルコンテキストでは 1 本指のタッチドラッグを行います（[BE-0227](../BE-0227-web-swipe-scroll-fidelity/BE-0227-web-swipe-scroll-fidelity-ja.md)、
  [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py)）。wheel のデルタはその大きさ
  ちょうどをスクロールし、合成したタッチドラッグは fling を残しません。ステップを区切って駆動する
  以外に変更は要りません。
- **Android（adb）**は `input swipe` でスクロールし、その有限の持続時間パラメータがジェスチャの速さを
  決めます（[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)）。短い持続時間は fling を起こし、
  同じ距離でも長い持続時間はゆっくりしたドラッグになり、リストは勢いなくそれに追従します。アクションは、
  ジェスチャが終わるとコンテンツが止まるだけの長さの持続時間で adb のスクロールを駆動します。
- **iOS（XCUITest）**は常駐ランナーを通じた実際のドラッグでスクロールします
  （[`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py)）。素早いフリックは勢いを与え
  ますが、終点で少し止まってから指を離すドラッグは、ほぼゼロの速度で離れるため、スクロールビューは
  ドラッグが残した位置に収まります。

慣性なしの保証を `Driver.scroll` の新しい引数（速度、あるいは「settle」フラグ）として表すか、別の
`Driver` メソッドとして表すかは、実装時の選択です。アクションが依存する契約は「勢いを持ち越さずに、
区切られた距離だけ進んで止まる」ことです。この契約をどのバックエンドでも同一に固定する場所は
[ドライバ適合スイート](../../docs/ja/architecture.md#driver-conformance-suitebe-0114)です（後述の Unit 6）。

### コンテンツ末尾の検出

本当に存在しない対象（`to` の打ち間違い、データ変更で消えた行）は、領域がすでに末尾に達していれば
`maxScrolls` 回よりも速く失敗するべきです。各スクロールのあと、アクションは領域の要素サブツリー
（`within` を省略したときはツリー全体）を直前のものと比較します。あるスクロールがそれを変化させなく
なったとき、コンテンツは底に達しており対象はそこにないので、アクションは同一のスクロールを上限まで
繰り返さず、ただちに失敗します。adb は現在、自身の scroll-into-view を固定のリトライ回数
（[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) の `_SCROLL_RETRIES`）で区切っており、
`maxScrolls` はその区切りの移植版にあたります。底に達した領域でツリー差分により早期失敗することは、
既存の adb 信号の一般化ではなく、このアクションが新たに足す機能です。この比較は、ループが停止条件の
確認のためにすでに取得したツリーを再利用するので、コンテンツ末尾の検出は追加の `query()` を発生
させません（[BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md)）。

### 作業分解（MECE）

1. **シナリオスキーマ。** [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py)
   に `Scroll` モデル（`to: Selector`、`direction: Literal["up","down","left","right"] = "down"`、
   `within: Selector | None`、`max_scrolls: int = Field(default=15, alias="maxScrolls")`）を追加します。
   `max_scrolls` は、同じモジュールの `save_body` / `battery_level` にならった、camelCase の alias を持つ
   snake_case 属性です。そして [`bajutsu/scenario/models/steps.py`](../../bajutsu/scenario/models/steps.py)
   の `Step` アグリゲータに配線します。`maxScrolls > 0` を検証します。このモデルは、同じ
   `up`/`down`/`left`/`right` リテラルをすでに宣言する既存の `Swipe`・`Drag` モデルの隣に置きます。
2. **オーケストレータのハンドラ。** [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py)
   に `_do_scroll` を追加します。回数を区切ったスクロールして再取得するループで、`to` が現在のツリーで
   解決し、*かつ*その frame がビューポート内にあるかを確認し、そうでなければ慣性なしのスクロールを
   1 ステップ行って再取得します。ステップの端点は既存の `_scroll_gesture` ヘルパーから取り、`within`
   コンテナの中心または画面中心にアンカーします。ハンドラは、アクションのコンテンツ方向 `direction` を
   `_scroll_gesture` が期待する指のジェスチャへ変換します。`down` の reveal は指を画面の上へ動かす
   スワイプであり、[`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml)
   が手で `swipe … direction: up` と書いているのと同じ対応です。`to` が画面内に入った最初のツリーで
   戻り、`maxScrolls` を使い切るかコンテンツ末尾を検出したときに失敗します。固定の `sleep` はありません。
   このループは条件待ちであり、構造としては
   [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) の `for` 分岐と同じです。
3. **慣性なしのドライバプリミティブとビューポートの範囲。** `Driver.scroll`（または対になるメソッド）
   に、上で述べた「区切られたステップ、勢いなし」の保証を与え、バックエンドごとに実装します。Playwright
   は変更なし（すでに満たしている）、adb は持続時間の長い `input swipe`、XCUITest は指を離す前に止める
   ドラッグです。ビューポート内かの停止条件は、真のビューポート寸法も必要とし、これはツリーから一様には
   導けません。native バックエンドでは、取得したツリーは画面内の要素だけを持つので
   `screen_size_from_elements`（[`bajutsu/elements.py`](../../bajutsu/elements.py)）がすでにビューポートを
   近似します。しかし web バックエンドのツリーは画面外の DOM ノードを含むため、そのコンテンツの extent は
   ビューポートを超えてしまいます。そこで Playwright バックエンドは、そのコンテンツ extent に頼るのでは
   なく、確認のために真のビューポート（`window.innerWidth` / `innerHeight`）を露出する必要があります。
   `FakeDriver`（[`bajutsu/drivers/fake.py`](../../bajutsu/drivers/fake.py)）には最小限のスクロール可能
   ビューポートのモデルを持たせ、端末なしでループをテストできるようにします。
4. **コンテンツ末尾の検出。** ハンドラ内で、連続するスクロールのあいだで領域のサブツリーを比較し、
   スクロールがそれを変化させなくなったら早期に失敗します。すでに取得したツリーを再利用します
   （追加の `query()` なし）。
5. **codegen。** `interrupts` と違い、`scroll` は 3 つのうち 2 つのターゲットで native の構文に対応
   します。Playwright のロケータはアクション前に自動でスクロールしてビューに入れ、UI Automator には
   `UiScrollable.scrollIntoView` があります。XCUITest には単一の堅牢な等価物がないため、そこでは
   ラベル付きの `// TODO` を出力します。共有のシナリオウォーク（[BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md)）に
   従って、[`bajutsu/codegen/`](../../bajutsu/codegen/) の各ターゲットに配線します。
6. **ドライバ適合。** [`tests/driver_conformance.py`](../../tests/driver_conformance.py) に
   scroll-into-view のケースを追加します。スクロールしてはじめて現れる画面外の対象を持つ画面で、
   `scroll` がそれを引き出すこと、および使い切った領域に存在しない対象が失敗することを検証します。
   これが、慣性なしでバックエンドを横断する契約が FakeDriver・Playwright・XCUITest・adb で同一に
   成り立つことを証明し（[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）、
   BE-0210 の非対称を、バックエンド別ではなく共有の仕様で解消します。
7. **ドキュメントとフィクスチャ。** [`docs/scenarios.md`](../../docs/scenarios.md) とその日本語版に、
   `swipe`・`drag` の隣で `scroll` を記述し、`scroll`（対象を引き出す）と `swipe`（固定のジェスチャ）と
   `drag`（つかんだハンドルを動かす）のどれを使うかの指針を添えます。[`docs/drivers.md`](../../docs/drivers.md)
   とその日本語版にある adb 限定の scroll-into-view の注記を、移植可能なアクションを指すように更新
   します。`scroll` の `direction` はスクロール方向であり、`swipe` の `direction` は指の方向である、と
   明示的に対比を示し、一方の動詞を知る作者がもう一方でつまずかないようにします。
   [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml) の手で調整した
   `swipe` の連なりを 1 つの `scroll` ステップに書き換え、目玉のフィクスチャがアクションを端から端まで
   示すようにします。
8. **テスト。** スキーマのパースと検証（既定値、`maxScrolls > 0`）、`FakeDriver` 上でのハンドラのループ
   （N 回のスクロールで対象が見つかる、対象が見つからず `maxScrolls` で失敗する、コンテンツ末尾で
   早期に失敗する、画面全体ではなく `within` コンテナがスクロールされる）。

### prime directive の保持

- **AI は判定しない。** 停止条件は `query()` に対してセレクタが解決することであり、機械が確認できる
  述語です。モデルの呼び出しではありません。このアクションは AI の面を足しません。
- **決定性が第一。** 固定の `sleep` はありません。アクションは回数を区切った条件待ちであり、慣性なしの
  スクロールは、現在の `swipe` 連なりの書き方を不安定にしている端末依存の行き過ぎを取り除きます。
  使い切った上限や底に達した領域は、ハングするのではなく明示的に失敗します。
- **アプリに依存しない。** `scroll` は `Driver` インターフェイス上の 1 つの汎用アクションです。アプリ
  ごとのコードはなく、シナリオの意味がバックエンドごとに分かれることもありません。

## 検討した代替案

- **`wait` にスクロール挙動を足す（`wait: { for: X, scroll: down }`）。** 却下しました。現在の `wait`
  は純粋な観測です。何も actuate せず、run ループはそれに依存しています。`_wait` は最後に取得した
  ツリーを返し、呼び出し側がそれをステップの `after` スナップショットとして再利用します。まさに
  「wait では何も actuate しないから」です（[`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py)、
  [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md)）。
  ジェスチャを wait に畳み込むと、この不変条件を壊し、観測と操作の明確な境界をぼかします。別のアクション
  にすれば `wait` は観測専用のままとなり、操作はシナリオ内で明示されます。
- **scroll-into-view をすべてのアクションで暗黙にし、BE-0210 を全バックエンドへ広げる。** 主案としては
  却下しました。暗黙の自動スクロールは意図を隠し（シナリオはスクロールしたと述べません）、方向を推測
  せざるをえず、その非決定性こそ [`docs/drivers.md`](../../docs/drivers.md) がこれを移植可能な書き方では
  なく堅牢化の安全網に留めている理由です。明示的なアクションは方向と対象を述べるので、シナリオは自己
  記述的で決定的になります。暗黙の adb の安全網を他のバックエンドへ広げることは、あとから別の安全網
  として重ねてもよいものであり、明示的なアクションの代わりにはなりません。
- **新しい動詞ではなく、`swipe` に `to`/`until` セレクタを持たせて再利用する。** 却下しました。`swipe`
  はすでに 2 つの形（方向スクロールと座標ドラッグ）を、両者が混ざらないようにする検証とともに持って
  います。3 つ目の「見えるまで繰り返す」形を足すと、1 つの動詞に 3 つの異なる挙動を負わせることに
  なります。別の動詞 `scroll` は呼び出し箇所での読みやすさに優れ、各動詞の契約を 1 つに保ちます。
- **`scroll.direction` を `swipe` の指方向の規約に合わせる。** 1 つのリテラルがジェスチャ動詞をまたいで
  同じ意味になるように、と検討しました。既定としては却下しました。`scroll` に手を伸ばす作者は「リストの
  さらに下にある要素を見つけるために下へスクロールする」と考えるので、下のコンテンツを引き出すのに指の
  方向 `up` を書くのはより意外な読み方だからです。そこで `scroll.direction` はスクロール方向（直感的な
  ほう）を指すことにし、Unit 7 のドキュメントで `swipe` との対比を示して、この反転を暗黙の落とし穴では
  なく明示にします。共有リテラルが逆の意味になる衝突そのものを避ける別のフィールド名という選択肢は、この
  対比の記述で不十分だと分かった場合に備えて、実装時の判断として残しておきます。
- **1 ステップの移動量 `amount` ノブ（`swipe` が持つもの）。** 最初のスライスには含めず、先送りします。
  アクションの既定である画面相対のステップは、慣性なしで確実になるよう意図的に選ばれています。`amount`
  を露出すると、呼び出し側が大きなステップを指定して行き過ぎを再導入する余地が生まれます。ステップを
  調整する現実の必要が現れたら、アクションの形を変えずにあとから足せます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] Unit 1 — `Scroll` シナリオスキーマ（`to` / `direction` / `within` / `maxScrolls`）を `Step` に配線。
- [ ] Unit 2 — `_do_scroll`、回数を区切ったスクロールして再取得するハンドラ（条件待ち、固定 sleep なし）。
- [ ] Unit 3 — バックエンドごとの慣性なし `Driver.scroll` 保証と `FakeDriver` のビューポートモデル。
- [ ] Unit 4 — すでに取得したツリーを再利用するコンテンツ末尾の検出。
- [ ] Unit 5 — codegen（Playwright の自動スクロール、UI Automator の `scrollIntoView`、XCUITest は TODO）。
- [ ] Unit 6 — ドライバ適合ケース（対象を引き出す、使い切った領域で失敗する）。
- [ ] Unit 7 — ドキュメント（scenarios.md と ja、drivers.md の注記更新）と notices.yaml の書き換え。
- [ ] Unit 8 — テスト（スキーマ、`FakeDriver` 上のハンドラのループ、`within`、コンテンツ末尾）。

## 参考

- [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py) — 新しい `Scroll`
  モデルを隣に置く `Swipe`・`Drag` モデルと、それが再利用する方向の語彙の出どころ。
- [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py) —
  `scroll` ハンドラが再利用する端点計算 `_do_swipe` / `_scroll_gesture`。
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — `scroll` ハンドラが構造として
  なぞる条件待ちループ（`for` 分岐）。この項目は、スクロールを `wait` に畳み込まないことで、その観測
  専用の契約を保ちます。
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — 慣性なし・区切りステップの保証を得る
  `Driver.scroll` プリミティブ。
- [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml) — 手で調整した
  `swipe` の連なりと、fling の行き過ぎの脆さを記録するそのコメント。このアクションの動機であり、その
  目玉のフィクスチャになります。
- [`docs/drivers.md`](../../docs/drivers.md) — scroll-into-view を adb 限定の堅牢化の安全網だと記す注記。
  このアクションがそれを移植可能にします。
- [BE-0210 — Android on-device actuation fidelity](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md) —
  この項目が明示的でバックエンドを横断するアクションへ一般化する、adb 限定の `_scroll_into_view` 回復。
- [BE-0227 — Web swipe / scroll fidelity](../BE-0227-web-swipe-scroll-fidelity/BE-0227-web-swipe-scroll-fidelity-ja.md) —
  このアクションが土台にする、web バックエンドのすでに慣性のないスクロールプリミティブ（wheel /
  タッチドラッグ）。
- [BE-0259 — Reuse the assertion query snapshot](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md) —
  コンテンツ末尾の検出が追加の `query()` を発生させないことを可能にする、すでに取得したツリーの再利用。
- [BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) —
  このアクションの慣性なしでバックエンドを横断する挙動を、どのバックエンドでも同一に固定する共有の契約。
