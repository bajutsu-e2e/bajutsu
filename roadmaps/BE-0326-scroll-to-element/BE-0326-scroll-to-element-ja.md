[English](BE-0326-scroll-to-element.md) · **日本語**

# BE-0326 — `scroll` アクション：要素が現れるまでスクロールする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0326](BE-0326-scroll-to-element-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0326") |
| 実装 PR | [#1391](https://github.com/bajutsu-e2e/bajutsu/pull/1391), [#1398](https://github.com/bajutsu-e2e/bajutsu/pull/1398), [#1489](https://github.com/bajutsu-e2e/bajutsu/pull/1489) |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md), [BE-0227](../BE-0227-web-swipe-scroll-fidelity/BE-0227-web-swipe-scroll-fidelity-ja.md), [BE-0251](../BE-0251-driver-base-helper-hoist/BE-0251-driver-base-helper-hoist-ja.md), [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md) |
<!-- /BE-METADATA -->

## はじめに

新しいシナリオアクション `scroll` は、画面外の要素を決定的にビューポートへ引き出します。
スクロール可能な領域を一方向に進め、各ステップのあとに要素ツリーを再取得します。
指定した対象が画面内に入ると停止します。
上限に達すると失敗します。
各スクロールは慣性を持ちません。
1 ステップの前進量は画面に対する一定の割合に固定され、勢いは残りません。
1 回のスクロールが対象をビューポート外へ飛ばすことはありません。
このアクションは iOS（XCUITest）、Android（adb）、web（Playwright）で同じように動きます。
いずれも単一の `Driver` インタフェースの背後にあります。
fold より下のコントロールへスクロールするシナリオは、3 つのバックエンドで同じ記述のまま動きます。

## 動機

縦に長い画面で画面外の要素へ到達することは、第一級の要求です。
設定リスト末尾のログアウトボタンがその例です。
フィードの後半の行や、縦長フォーム下の送信ボタンも同様です。
それにもかかわらず、bajutsu にはこれを直接表す方法がありません。
いまの移植可能な書き方は、手で調整した `swipe` の連なりに `wait` を続ける形です。
showcase のフィクスチャが、その連なりの脆さを記録しています。
参照は [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml) です。
作者は各 `swipe` を、スクロール中も見え続ける行にアンカーしなければなりません。
さらに 1 スワイプ分の余白を取ってリストをたどらなければなりません。
コメントは理由をこう述べます。
fling の進み方は端末と描画速度で変わります。
ソフトウェアレンダリングの CI エミュレータは、実機より 1 スワイプの進みが少ないです。
余分な 1 ステップが、速い端末で対象を上へ行き過ぎさせずにその差を埋めます。
作者はスクロールの勢いを手で補正しています。

この勢いが脆さの根本です。
慣性なしのスクロールは利便ではなく、決定性の要件です。
fling は速度を与えます。
指が離れたあとの進みは、スクロール物理とフレームレートに依存します。
同じジェスチャでも、速い端末は遅い端末より遠くまで運びます。
行き過ぎに巻き込まれた対象は、ある端末では fold より下に着地します。
別の端末では上に着地します。
手元で通るスワイプ回数でも、CI では失敗しえます。
`wait` が見つける前に対象を上へ通り過ぎることもあります。
慣性のないスクロールは、この変動を取り除きます。
各ステップは一定距離だけ進んでから止まります。
各ステップ後の再取得は、そのビューポート内で対象をとらえます。
prime directive 2 は、同じ理由で固定の `sleep` を禁じています。
予測できない持続時間を、確認できる条件で置き換えます。
ここではその考えをスクロール距離に当てはめます。

この欠落は移植性の非対称でもあります。
adb バックエンドは、画面外のアクション対象へ向けてすでに内部スクロールします。
リトライ回数の上限のもとで再取得します。
詳細は [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md) です。
実装は [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) の `_scroll_into_view` にあります。
[`docs/drivers.md`](../../docs/drivers.md) は、この回復を adb 専用と記しています。
堅牢化の安全網であり、移植可能な書き方ではありません。
XCUITest と Playwright は、対象が最初から画面外だと `tap` を即座に失敗させます。
同じシナリオが Android ではリトライののちに通ることがあります。
iOS と web では失敗することがあります。
文書上の移植可能な答えは、手で調整した `swipe` の連なりのままです。
明示的でバックエンド横断の `scroll` アクションが、この非対称を解消します。
作者は決定的な構文を一度書けば、どこでも同じように読めます。

## 詳細設計

### `scroll` アクション

```yaml
# 画面外の行をビューポートに引き出してからタップする。
- scroll:
    to: { id: notice.row.20 }   # 引き出す要素（ループの停止条件）
    direction: down             # up | down | left | right（デフォルト: down）
- tap: { id: notice.row.20 }
```

```yaml
# 特定のスクロール領域をスクロールし、試行回数を区切る。
- scroll:
    to: { label: "Log out", traits: [button] }
    direction: down
    within: { id: settings.list }   # ジェスチャを行うスクロールコンテナ
    maxScrolls: 25                   # 上限（デフォルト 15）。見つからなければ失敗
```

このアクションは対象セレクタと方向を持ちます。
加えて 2 つの任意指定を取ります。

- **`to`**（必須）：引き出す[セレクタ](../../docs/selectors.md)です。
  ループの停止条件になります。
  単なる存在より厳しい条件です。
  `to` が解決し、かつその frame の中心点がビューポート内にある瞬間に、アクションは戻ります。
  存在するだけでは不十分です。
  バックエンドは画面外の要素をツリーに残すことがあります。
  web バックエンドの `query()` は、画面外の Document Object Model（`DOM`）ノードも返します。
  一方、native の lazy リストは画面外の行をツリーから外します。
  `wait: { for: … }` の述語は存在だけを見ます。
  その wait は、流れ去った web 要素を「見つかった」とみなし、スクロールしません。
  中心点は、`frame_center` が座標ベースの `tap` に対して解決する点と同じです（[BE-0251](../BE-0251-driver-base-helper-hoist/BE-0251-driver-base-helper-hoist-ja.md)）。
  そのヘルパーは [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) にあります。
  後続の `tap` が画面内に必要とするのは、frame 全体ではなくその点です。
  frame 全体をビューポート内に要求すると、ビューポートより高い対象で失敗します。
  単なる交差を要求すると、タップ対象の中心がまだ隠れたまま停止します。
  中心点による判定は、この両方の問題を避けます。
  frame は取得済みの各要素にすでに載っています。
  比較相手のビューポート範囲は Unit 3 が与えます。

- **`direction`**（デフォルト `down`）：コンテンツをたどる方向です。
  ビューポートが進む方向を指します。
  指のジェスチャの逆です。
  `down` はコンテンツをさらに下へ進め、fold より下を引き出します。
  ドライバは、指を上へ動かすスワイプとして実現します。
  ビューポートが下へ動くにつれ、コンテンツは上へ滑ります。
  `up`、`left`、`right` は他の軸です。
  `direction` はスクロール方向を指すため、`swipe` とは逆向きに読めます。
  `swipe` の `direction` は指の方向です。
  方向は明示であり、アクションはリストの進む向きを推測しません。

- **`within`**（任意）：ジェスチャを行うスクロールコンテナです。
  各スクロールは、このコンテナにアンカーします。
  コンテンツ末尾は、このコンテナのサブツリーから判定します。
  省略すると、画面全体をスクロールします。
  `within` を使えば、入れ子スクロールの内側の対象へ届きます。
  外側の面を動かさずに済みます。

- **`maxScrolls`**（任意、デフォルト 15）：失敗までのスクロール回数の上限です。
  現れない対象が永遠にスクロールし続けるのを防ぎます。
  上限に達すると、決定的に失敗します。

### バックエンド横断の慣性なしスクロール

1 ステップのスクロールは勢いを残してはなりません。
3 つのバックエンドは、それぞれ別の低レベル手段でこの条件を満たします。
いずれも [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) の `Driver.scroll` の背後にあります。

- **web（Playwright）**：すでに慣性がありません。
  デスクトップでは wheel を使い、モバイルでは 1 本指のタッチドラッグを使います。
  参照は [BE-0227](../BE-0227-web-swipe-scroll-fidelity/BE-0227-web-swipe-scroll-fidelity-ja.md) です。
  実装は [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py) にあります。
  wheel のデルタは、その大きさだけ動きます。
  合成タッチドラッグは fling を残しません。
  区切ったステップで駆動すれば足ります。

- **Android（adb）**：`input swipe` でスクロールします。
  有限の持続時間が、ジェスチャの速さを決めます。
  実装は [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) にあります。
  短い持続時間は fling を起こします。
  同じ距離でも長い持続時間なら、ゆっくりしたドラッグになります。
  リストは勢いなく追従します。
  アクションは、ジェスチャの終了と同時にコンテンツが止まる長さを使います。

- **iOS（XCUITest）**：常駐ランナー経由の実ドラッグでスクロールします。
  実装は [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) にあります。
  素早いフリックは勢いを与えます。
  終点で少し止まってから離すドラッグは、ほぼゼロ速度で離れます。
  スクロールビューは、ドラッグが残した位置に収まります。

実装では、`Driver.scroll` の引数を足しても、別メソッドを置いても構いません。
アクションが必要とする契約は変わりません。
区切られた距離だけ進み、勢いは持ち越しません。
[ドライバ適合スイート](../../docs/ja/architecture.md#driver-conformance-suitebe-0114) がこの契約を固定します。
Unit 6 がこのスイートを扱います。

### コンテンツ末尾の検出

領域がすでに末尾なら、存在しない対象は `maxScrolls` より速く失敗するべきです。
`to` の打ち間違いや、データ変更で消えた行がその例です。
各スクロールのあと、アクションは領域の要素サブツリーを比較します。
`within` を省略したときは、ツリー全体を比較します。
スクロールがサブツリーを変えなくなったら、コンテンツは底に達しています。
対象はないので、アクションはただちに失敗します。
同一スクロールを上限まで繰り返しません。
adb はいま、固定のリトライ回数で scroll-into-view を区切っています。
その定数は [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) の `_SCROLL_RETRIES` です。
`maxScrolls` は、その区切りの移植版です。
底打ち領域でのツリー差分による即時失敗は、このアクションの新機能です。
既存 adb 信号の一般化ではありません。
比較は、停止条件の確認ですでに取得したツリーを再利用します。
コンテンツ末尾の検出は、追加の `query()` を出しません。
参照は [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md) です。

### 作業分解（`MECE`）

Mutually Exclusive, Collectively Exhaustive（`MECE`）な作業単位は次のとおりです。

1. **シナリオスキーマ。**

   [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py) に `Scroll` を追加します。
   フィールドは `to: Selector` です。
   フィールドは `direction: Literal["up","down","left","right"] = "down"` です。
   フィールドは `within: Selector | None` です。
   フィールドは `max_scrolls: int = Field(default=15, alias="maxScrolls")` です。
   snake_case の属性に camelCase の alias を付けます。
   同じモジュールの `save_body` と `battery_level` に合わせます。
   [`bajutsu/scenario/models/steps.py`](../../bajutsu/scenario/models/steps.py) の `Step` に配線します。
   `max_scrolls > 0` を要求します。
   モデルは `Swipe` と `Drag` の隣に置きます。
   これらのモデルは、同じ方向リテラルをすでに持っています。

2. **オーケストレータのハンドラ。**

   [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py) に `_do_scroll` を追加します。
   回数を区切ったスクロールと再取得のループにします。
   `to` が解決し、その frame の `frame_center` がビューポート内かを確認します。
   そうでなければ、慣性なしの 1 ステップを行い、再取得します。
   端点は既存の `_scroll_gesture` から取ります。
   アンカーは `within` の中心、または画面中心です。
   コンテンツ方向 `direction` を、`_scroll_gesture` が期待する指ジェスチャへ変換します。
   `down` の reveal は、指を画面上へ動かすスワイプです。
   これは [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml) と同じ対応です。
   そのフィクスチャは、手で `swipe … direction: up` と書いています。
   `to` が画面内に入った最初のツリーで戻ります。
   `maxScrolls` を使い切るか、末尾を検出したら失敗します。
   固定の `sleep` は使いません。
   ループは条件待ちです。
   [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) の `for` 分岐と同じ構造です。

3. **慣性なしのドライバ `API` とビューポート範囲。**

   `Driver.scroll`（または対になるメソッド）に、区切りステップ・勢いなしの契約を与えます。
   バックエンドごとに実現します。
   Playwright は変更不要で、すでに契約を満たします。
   adb は、持続時間の長い `input swipe` を使います。
   XCUITest は、指を離す前に止めるドラッグを使います。
   ビューポート内かの停止条件には、真のビューポート寸法も要ります。
   ツリーだけから一様には導けません。
   native では取得ツリーが画面内要素だけなので、[`bajutsu/elements.py`](../../bajutsu/elements.py) の `screen_size_from_elements` が近似になります。
   web のツリーは、画面外の `DOM` ノードを含みます。
   コンテンツの extent は、ビューポートを超えます。
   Playwright は、確認用に真のビューポートを露出します。
   `window.innerWidth` と `window.innerHeight` を使います。
   コンテンツ extent には頼りません。
   [`bajutsu/drivers/fake.py`](../../bajutsu/drivers/fake.py) の `FakeDriver` に、最小のビューポートモデルを持たせます。
   端末なしでループを試せるようにします。

4. **コンテンツ末尾の検出。**

   ハンドラ内で、連続するスクロール間のサブツリーを比較します。
   変化しなくなったら、すぐに失敗します。
   取得済みツリーを再利用します。
   追加の `query()` は出しません。

5. **codegen。**

   `interrupts` と違い、`scroll` は 3 つのうち 2 つで native 構文に対応します。
   Playwright のロケータは、操作前に自動でビューへ入れます。
   UI Automator には `UiScrollable.scrollIntoView` があります。
   XCUITest には、単一の堅牢な対応がありません。
   そこでは、ラベル付きの `TODO` を出します。
   [`bajutsu/codegen/`](../../bajutsu/codegen/) の各ターゲットに配線します。
   共有シナリオウォークは [BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md) に従います。

6. **ドライバ適合。**

   [`tests/driver_conformance.py`](../../tests/driver_conformance.py) に scroll-into-view のケースを追加します。
   スクロールしてはじめて現れる画面外対象を使います。
   `scroll` がそれを引き出すことを検証します。解決した frame の `frame_center` がビューポートに収まります。
   ビューポートより高い対象も加え、その中心がビューポートに入った時点で `scroll` が成功することを検証します。
   使い切った領域にない対象が失敗することを検証します。
   慣性なしでバックエンド横断の契約を証明します。
   FakeDriver、Playwright、XCUITest、adb で成り立ちます。
   参照は [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) です。
   共有仕様で、BE-0210 の非対称を解消します。

7. **ドキュメントとフィクスチャ。**

   [`docs/scenarios.md`](../../docs/scenarios.md) と日本語版に `scroll` を書きます。
   `swipe` と `drag` の隣に置きます。
   `scroll`（対象を引き出す）の使い分けを示します。
   `swipe`（固定ジェスチャ）の使い分けを示します。
   `drag`（つかんだハンドルを動かす）の使い分けを示します。
   [`docs/drivers.md`](../../docs/drivers.md) の adb 専用注記を更新します。
   移植可能なアクションを指すようにします。
   `scroll` の `direction` はスクロール方向だと明示します。
   `swipe` の `direction` は指の方向だと明示します。
   [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml) の `swipe` の連なりを、1 つの `scroll` に書き換えます。
   目玉フィクスチャが、アクションを端から端まで示すようにします。

8. **テスト。**

   スキーマのパースと検証（デフォルト、`max_scrolls > 0`）をカバーします。
   `FakeDriver` 上のハンドラループをカバーします。
   ケースは次のとおりです。
   N 回のスクロールで対象が見つかります。
   対象が見つからず、`maxScrolls` で失敗します。
   コンテンツ末尾で、ただちに失敗します。
   画面全体ではなく、`within` がスクロールされます。

### prime directive の保持

- **AI は判定しません。**

  停止条件は、`query()` に対するセレクタ解決です。
  機械が確認できる述語であり、モデル呼び出しではありません。
  このアクションは AI の面を足しません。

- **決定性が第一です。**

  固定の `sleep` はありません。
  アクションは、回数を区切った条件待ちです。
  慣性なしのスクロールは、端末依存の行き過ぎを取り除きます。
  その行き過ぎが、いまの `swipe` の連なりを不安定にしています。
  使い切った上限や、底に達した領域は失敗します。
  ランは停滞しません。

- **アプリに依存しません。**

  `scroll` は、`Driver` インタフェース上の 1 つの汎用アクションです。
  アプリごとのコードはありません。
  シナリオの意味が、バックエンドごとに分かれることもありません。

## 検討した代替案

- **`wait` にスクロール挙動を足す（`wait: { for: X, scroll: down }`）。**

  却下します。
  いまの `wait` は純粋な観測です。
  何も actuate しません。
  run ループはそれに依存しています。
  `_wait` は最後に取得したツリーを返し、呼び出し側が `after` に再利用します。
  wait では何も actuate しないからです。
  参照は [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) です。
  参照は [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md) です。
  ジェスチャを wait に畳み込むと、その不変条件が壊れます。
  観測と操作の境界がぼやけます。
  別アクションにすれば、`wait` は観測専用のままです。
  操作はシナリオ内で明示されます。

- **scroll-into-view を全アクションで暗黙にする。**

  BE-0210 を全バックエンドへ広げる形です。
  主案としては却下します。
  暗黙の自動スクロールは、意図を隠します。
  シナリオは、スクロールしたと述べません。
  ランナーは、方向を推測しなければなりません。
  その非決定性こそ、[`docs/drivers.md`](../../docs/drivers.md) が adb 経路を安全網に留めている理由です。
  明示アクションは、方向と対象を述べます。
  シナリオは自己記述的で決定的になります。
  暗黙の adb 網をあとから安全網として重ねることはできます。
  明示アクションの代わりにはなりません。

- **`swipe` に `to` や `until` を持たせて再利用する。**

  却下します。
  `swipe` はすでに、方向スクロールと座標ドラッグの 2 形を持っています。
  検証が、その混在を防いでいます。
  3 つ目の「見えるまで繰り返す」形は、1 つの動詞に 3 つの挙動を負わせます。
  別の動詞 `scroll` のほうが、呼び出し箇所で読みやすくなります。
  各動詞の契約を 1 つに保てます。

- **`scroll.direction` を `swipe` の指方向の規約に合わせる。**

  検討しました。
  ジェスチャ動詞をまたいで、リテラルの意味を揃えたいためです。
  デフォルトとしては却下します。
  `scroll` に手を伸ばす作者は、スクロールの語彙で考えます。
  「下へスクロール」で、リストのさらに下を意味させたいのです。
  下のコンテンツを出すのに指方向 `up` を書くのは、意外な読み方です。
  そこで `scroll.direction` は、スクロール方向を指します。
  Unit 7 のドキュメントで、`swipe` との対比を示します。
  反転は、暗黙の落とし穴ではなく明示になります。
  共有リテラルの衝突を避ける別名は、実装時の判断として残します。
  対比の記述で足りなければ、その別名を使います。

- **1 ステップの移動量 `amount` ノブ（`swipe` が持つもの）。**

  先送りします。
  最初のスライスには含めません。
  デフォルトの画面相対ステップは、慣性なしで確実になるよう選んでいます。
  `amount` を出すと、大きなステップで行き過ぎが戻る可能性があります。
  調整の必要が現れたら、あとから足せます。
  アクションの形を変える必要はありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。
> チェックリストは *詳細設計* の `MECE` な作業分解に対応します。
> ログには変更内容と時期（古い順）を PR へのリンクとともに記録します。

- [x] Unit 1 — `Scroll` スキーマを `Step` に配線
- [x] Unit 2 — `_do_scroll` の有界ループ
- [x] Unit 3 — 慣性なし `Driver.scroll` と `FakeDriver` ビューポート
- [x] Unit 4 — 取得済みツリーを使う末尾検出
- [x] Unit 5 — codegen（Playwright、UI Automator、XCUITest `TODO`）
- [x] Unit 6 — ドライバ適合ケース（要素を引き出す／使い切った領域で失敗）
- [x] Unit 7 — ドキュメントと notices.yaml の書き換え
- [x] Unit 8 — テスト（スキーマ、FakeDriver ループ、`within`、コンテンツ末尾）
- [x] Unit 9 — 読み取りが操作に遅れるバックエンドでのコンテンツ末尾の確認

### ログ

- 慣性なしの契約を各バックエンドで実現します。web はすでに慣性がありません（wheel／タッチドラッグ）。
  adb は長めの `input swipe` でパンします。XCUITest の常駐ランナーには、離す前に終点でドラッグを保持する
  `/scroll` ルートを追加します。Appium の実機ドライバは長めの duration でドラッグします。
- 停止条件には真のビューポートが必要ですが、取得したツリーからは求められません。遅延リストはビューポート外の
  行をバッファとしてツリーに残すので、`screen_size_from_elements` は画面を超過します。超過したビューポートは、
  画面外の中心を画面内と誤判定してしまいます。さらに、ジェスチャの起点を画面外へ押し出し、XCUITest ランナーを
  不安定にしていました。真のビューポートは `ViewportProvider` プロトコルが各バックエンドで報告します。
  Playwright は `window.innerWidth/innerHeight` から、adb は `wm size` から求めます。XCUITest は新設の
  `/screen` ルートでアプリウィンドウの `frame` を返します。Appium の実機ドライバは WebDriver のウィンドウ
  矩形から、`FakeDriver` は自身のモデルから求めます。
- ドライバ適合ケースは、4 つのバックエンドすべてで画面外の行を引き出します。ビューポートより高い要素を
  その中心が画面に入った時点で明らかにするケースと、対象がないときに失敗するケースも含みます。各バックエンドが
  真のビューポートを報告するので、ネイティブのレーンでもスキップは要りません。
- `scroll` ハンドラとそのループは、独立した新規モジュール
  `bajutsu/orchestrator/actions/handlers/scroll.py` に置きます。
- コンテンツ末尾の判定には、1 回の照会では足りない条件がもう 1 つ必要でした。Android は、ジェスチャが
  内容を動かした後にアクセシビリティ更新を公開します。公開までに読み取ったツリーはスクロール前の状態を
  返すため、1 回の読み取りでは末尾に達した領域と区別できません。CI のエミュレータを計測して、どちらで
  あったかが決まりました。変化がなかった 14 ステップのすべてで画面のピクセルが動いており、末尾に達した
  ものは 1 つもありませんでした。`conformance (adb)` は対象を引き出す 12 件のうち 8 件で失敗していました。ループは
  末尾と判定する前に領域が動くまで再読み取りするようになり、その上限は各バックエンドが自分で申告します
  （`ReadLagProvider`）。adb は上限を申告します。同期的なバックエンドは申告せず、最初の変化なしの
  読み取りで失敗するので、末尾の判定は従来どおり即座に下ります。適合テストのスクロール 3 件を 6 回
  繰り返して、いずれも成功しました。
- adb のパンを明示的な接点の列（`input motionevent` の down、move、up）に書き換える案は、単一の
  `input swipe` と比較して計測し、採用しませんでした。どちらも 12 件のうち 8 件で失敗し、原因が
  ジェスチャではなかったからです。ローカルのハードウェア支援エミュレータでは、明示的な接点の列のほうが
  悪化しました。接点の間隔が近くフリングが伸びるため、連続する 2 つの照会済みビューポートの間を対象が
  通り抜けてしまいます。
- [#1489](https://github.com/bajutsu-e2e/bajutsu/pull/1489)：ユニット 7 の書き換えを、CI でカバーする
  残りの共有シナリオに広げました。`controls`、`modals`、`system` は、それぞれ手作業で調整した `swipe`
  連鎖を、シナリオが読む最も下の要素を狙う 1 つの `scroll` に置き換えます。`gestures.yaml` は意図的に
  `swipe` のままです。`ui-test-coverage` がこれをネイティブの XCUITest にコンパイルしますが、そこでは
  `scroll` が `// TODO` しか出力しないためです。

## 参考

- [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py)：新しい `Scroll` の隣の `Swipe` / `Drag`
- [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py)：`_do_swipe` / `_scroll_gesture`
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py)：条件待ちループ。`wait` は観測専用
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py)：慣性なし契約の `Driver.scroll`
- [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml)：動機となる手調整の `swipe` 連なり
- [`docs/drivers.md`](../../docs/drivers.md)：scroll-into-view を adb 専用と記す注記
- [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)：一般化する adb の `_scroll_into_view`
- [BE-0227](../BE-0227-web-swipe-scroll-fidelity/BE-0227-web-swipe-scroll-fidelity-ja.md)：web の慣性なしスクロール
- [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md)：末尾検出のツリー再利用
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)：バックエンド横断の共有契約
