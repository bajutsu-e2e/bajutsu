[English](BE-0326-scroll-to-element.md) · **日本語**

# BE-0326 — `scroll` アクション：要素が現れるまでスクロールする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0326](BE-0326-scroll-to-element-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0326") |
| 実装 PR | [#1391](https://github.com/bajutsu-e2e/bajutsu/pull/1391) |
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

- **`to`**（必須）：引き出す[セレクタ](../../docs/selectors.md)
  ループの停止条件。
  単なる存在より厳しい条件。
  `to` が解決し、かつその frame の中心点がビューポート内にある瞬間に戻る。
  存在するだけでは不十分。
  バックエンドは画面外の要素をツリーに残すことがある。
  web バックエンドの `query()` は、画面外の Document Object Model（`DOM`）ノードも返す。
  一方、native の lazy リストは画面外の行をツリーから外す。
  `wait: { for: … }` の述語は存在だけを見る。
  その wait は、流れ去った web 要素を「見つかった」とみなし、スクロールしない。
  中心点は、`frame_center` が座標ベースの `tap` に対して解決する点と同じ（[BE-0251](../BE-0251-driver-base-helper-hoist/BE-0251-driver-base-helper-hoist-ja.md)）。
  そのヘルパーは [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) にある。
  後続の `tap` が画面内に必要とするのは frame 全体ではなく、その点。
  frame 全体をビューポート内に要求すると、ビューポートより高い対象で失敗する。
  単なる交差を要求すると、タップ対象の中心がまだ隠れたまま停止する。
  中心点による判定は、この両方の問題を避ける。
  frame は取得済みの各要素にすでに載っている。
  比較相手のビューポート範囲は Unit 3 が与える。

- **`direction`**（デフォルト `down`）：コンテンツをたどる方向
  ビューポートが進む方向。
  指のジェスチャの逆。
  `down` はコンテンツをさらに下へ進め、fold より下を引き出す。
  ドライバは指を上へ動かすスワイプとして実現する。
  ビューポートが下へ動くにつれ、コンテンツは上へ滑る。
  `up`・`left`・`right` は他の軸。
  `direction` はスクロール方向を指すため、`swipe` とは逆向きに読める。
  `swipe` の `direction` は指の方向。
  方向は明示であり、アクションはリストの進む向きを推測しない。

- **`within`**（任意）：ジェスチャを行うスクロールコンテナ
  各スクロールはこのコンテナにアンカーする。
  コンテンツ末尾はこのコンテナのサブツリーから判定する。
  省略時は画面全体をスクロールする。
  `within` は入れ子スクロールの内側の対象へ届く。
  外側の面を動かさずに済む。

- **`maxScrolls`**（任意、デフォルト 15）：失敗までのスクロール回数上限
  現れない対象が永遠にスクロールし続けるのを防ぐ。
  上限に達すると決定的に失敗する。

### バックエンド横断の慣性なしスクロール

1 ステップのスクロールは勢いを残してはなりません。
3 つのバックエンドは、それぞれ別の低レベル手段でこの条件を満たします。
いずれも [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) の `Driver.scroll` の背後にあります。

- **web（Playwright）**：すでに慣性なし。
  デスクトップでは wheel、モバイルでは 1 本指のタッチドラッグ。
  参照は [BE-0227](../BE-0227-web-swipe-scroll-fidelity/BE-0227-web-swipe-scroll-fidelity-ja.md)。
  実装は [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py)。
  wheel のデルタはその大きさだけ動く。
  合成タッチドラッグは fling を残さない。
  区切ったステップで駆動すれば足りる。

- **Android（adb）**：`input swipe` でスクロールする。
  有限の持続時間がジェスチャの速さを決める。
  実装は [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)。
  短い持続時間は fling を起こす。
  同じ距離でも長い持続時間はゆっくりしたドラッグになる。
  リストは勢いなく追従する。
  アクションは、ジェスチャ終了と同時にコンテンツが止まる長さを使う。

- **iOS（XCUITest）**：常駐ランナー経由の実ドラッグでスクロールする。
  実装は [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py)。
  素早いフリックは勢いを与える。
  終点で少し止まってから離すドラッグは、ほぼゼロ速度で離れる。
  スクロールビューはドラッグが残した位置に収まる。

実装では `Driver.scroll` の引数でも、別メソッドでも構いません。
アクションが必要とする契約は一定です。
区切られた距離だけ進み、勢いは持ち越さない。
[ドライバ適合スイート](../../docs/ja/architecture.md#driver-conformance-suitebe-0114) がこの契約を固定します。
Unit 6 がこのスイートを扱います。

### コンテンツ末尾の検出

領域がすでに末尾なら、存在しない対象は `maxScrolls` より速く失敗するべきです。
`to` の打ち間違いや、データ変更で消えた行がその例です。
各スクロールのあと、アクションは領域の要素サブツリーを比較します。
`within` を省略したときはツリー全体を比較します。
スクロールがサブツリーを変えなくなったら、コンテンツは底に達しています。
対象はないので、アクションはただちに失敗します。
同一スクロールを上限まで繰り返しません。
adb はいま、固定のリトライ回数で scroll-into-view を区切っています。
その定数は [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) の `_SCROLL_RETRIES` です。
`maxScrolls` はその区切りの移植版です。
底打ち領域でのツリー差分による即時失敗は、このアクションの新機能です。
既存 adb 信号の一般化ではありません。
比較は、停止条件確認ですでに取得したツリーを再利用します。
コンテンツ末尾の検出は追加の `query()` を出しません。
参照は [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md) です。

### 作業分解（`MECE`）

Mutually Exclusive, Collectively Exhaustive（`MECE`）な作業単位は次のとおりです。

1. **シナリオスキーマ。**

   [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py) に `Scroll` を追加する。
   フィールドは `to: Selector`。
   フィールドは `direction: Literal["up","down","left","right"] = "down"`。
   フィールドは `within: Selector | None`。
   フィールドは `max_scrolls: int = Field(default=15, alias="maxScrolls")`。
   snake_case 属性に camelCase の alias を付ける。
   同じモジュールの `save_body` と `battery_level` に合わせる。
   [`bajutsu/scenario/models/steps.py`](../../bajutsu/scenario/models/steps.py) の `Step` に配線する。
   `max_scrolls > 0` を要求する。
   モデルは `Swipe` と `Drag` の隣に置く。
   これらのモデルは同じ方向リテラルをすでに持つ。

2. **オーケストレータのハンドラ。**

   [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py) に `_do_scroll` を追加する。
   回数を区切ったスクロールと再取得のループにする。
   `to` が解決し、その frame の `frame_center` がビューポート内かを確認する。
   そうでなければ慣性なしの 1 ステップを行い、再取得する。
   端点は既存の `_scroll_gesture` から取る。
   アンカーは `within` の中心、または画面中心。
   コンテンツ方向 `direction` を、`_scroll_gesture` が期待する指ジェスチャへ変換する。
   `down` の reveal は指を画面上へ動かすスワイプ。
   これは [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml) と同じ対応。
   そのフィクスチャは手で `swipe … direction: up` と書いている。
   `to` が画面内に入った最初のツリーで戻る。
   `maxScrolls` を使い切るか、末尾を検出したら失敗する。
   固定の `sleep` は使わない。
   ループは条件待ち。
   [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) の `for` 分岐と同じ構造。

3. **慣性なしのドライバ `API` とビューポート範囲。**

   `Driver.scroll`（または対になるメソッド）に、区切りステップ・勢いなしの契約を与える。
   バックエンドごとに実現する。
   Playwright は変更不要で、すでに契約を満たす。
   adb は持続時間の長い `input swipe`。
   XCUITest は指を離す前に止めるドラッグ。
   ビューポート内かの停止条件には、真のビューポート寸法も要る。
   ツリーだけから一様には導けない。
   native では取得ツリーが画面内要素だけなので、[`bajutsu/elements.py`](../../bajutsu/elements.py) の `screen_size_from_elements` が近似になる。
   web のツリーは画面外の `DOM` ノードを含む。
   コンテンツの extent はビューポートを超える。
   Playwright は確認用に真のビューポートを露出する。
   `window.innerWidth` と `window.innerHeight` を使う。
   コンテンツ extent には頼らない。
   [`bajutsu/drivers/fake.py`](../../bajutsu/drivers/fake.py) の `FakeDriver` に最小のビューポートモデルを持たせる。
   端末なしでループを試せるようにする。

4. **コンテンツ末尾の検出。**

   ハンドラ内で連続スクロール間のサブツリーを比較する。
   変化しなくなったらすぐに失敗する。
   取得済みツリーを再利用する。
   追加の `query()` は出さない。

5. **codegen。**

   `interrupts` と違い、`scroll` は 3 つのうち 2 つで native 構文に対応する。
   Playwright のロケータは操作前に自動でビューへ入れる。
   UI Automator には `UiScrollable.scrollIntoView` がある。
   XCUITest には単一の堅牢な対応がない。
   そこではラベル付きの `TODO` を出す。
   [`bajutsu/codegen/`](../../bajutsu/codegen/) の各ターゲットに配線する。
   共有シナリオウォークは [BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md) に従う。

6. **ドライバ適合。**

   [`tests/driver_conformance.py`](../../tests/driver_conformance.py) に scroll-into-view のケースを追加する。
   スクロールしてはじめて現れる画面外対象を使う。
   `scroll` がそれを引き出すことを検証する：解決した frame の `frame_center` がビューポートに収まる。
   ビューポートより高い対象も加え、その中心がビューポートに入った時点で `scroll` が成功することを検証する。
   使い切った領域にない対象が失敗することを検証する。
   慣性なしでバックエンド横断の契約を証明する。
   FakeDriver、Playwright、XCUITest、adb で成り立つ。
   参照は [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)。
   共有仕様で BE-0210 の非対称を解消する。

7. **ドキュメントとフィクスチャ。**

   [`docs/scenarios.md`](../../docs/scenarios.md) と日本語版に `scroll` を書く。
   `swipe` と `drag` の隣に置く。
   `scroll`（対象を引き出す）の使い分けを示す。
   `swipe`（固定ジェスチャ）の使い分けを示す。
   `drag`（つかんだハンドルを動かす）の使い分けを示す。
   [`docs/drivers.md`](../../docs/drivers.md) の adb 専用注記を更新する。
   移植可能なアクションを指すようにする。
   `scroll` の `direction` はスクロール方向だと明示する。
   `swipe` の `direction` は指の方向だと明示する。
   [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml) の `swipe` 連なりを 1 つの `scroll` に書き換える。
   目玉フィクスチャがアクションを端から端まで示すようにする。

8. **テスト。**

   スキーマのパースと検証（デフォルト、`max_scrolls > 0`）をカバーする。
   `FakeDriver` 上のハンドラループをカバーする。
   ケース：N 回のスクロールで対象が見つかる。
   ケース：対象が見つからず `maxScrolls` で失敗する。
   ケース：コンテンツ末尾でただちに失敗する。
   ケース：画面全体ではなく `within` がスクロールされる。

### prime directive の保持

- **AI は判定しない。**
  停止条件は `query()` に対するセレクタ解決。
  機械が確認できる述語であり、モデル呼び出しではない。
  このアクションは AI の面を足さない。

- **決定性が第一。**
  固定の `sleep` はない。
  アクションは回数を区切った条件待ち。
  慣性なしのスクロールは端末依存の行き過ぎを取り除く。
  その行き過ぎが、いまの `swipe` 連なりを不安定にしている。
  使い切った上限や底に達した領域は失敗する。
  ランは停滞しない。

- **アプリに依存しない。**
  `scroll` は `Driver` インタフェース上の 1 つの汎用アクション。
  アプリごとのコードはない。
  シナリオの意味がバックエンドごとに分かれることもない。

## 検討した代替案

- **`wait` にスクロール挙動を足す（`wait: { for: X, scroll: down }`）。**

  却下。
  いまの `wait` は純粋な観測。
  何も actuate しない。
  run ループはそれに依存している。
  `_wait` は最後に取得したツリーを返し、呼び出し側が `after` に再利用する。
  wait では何も actuate しないからである。
  参照は [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py)。
  参照は [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md)。
  ジェスチャを wait に畳み込むとその不変条件が壊れる。
  観測と操作の境界がぼやける。
  別アクションにすれば `wait` は観測専用のまま。
  操作はシナリオ内で明示される。

- **scroll-into-view を全アクションで暗黙にする。**

  BE-0210 を全バックエンドへ広げる形。
  主案としては却下。
  暗黙の自動スクロールは意図を隠す。
  シナリオはスクロールしたと述べない。
  ランナーは方向を推測しなければならない。
  その非決定性こそ、[`docs/drivers.md`](../../docs/drivers.md) が adb 経路を安全網に留めている理由。
  明示アクションは方向と対象を述べる。
  シナリオは自己記述的で決定的になる。
  暗黙の adb 網をあとから安全網として重ねることはできる。
  明示アクションの代わりにはならない。

- **`swipe` に `to` や `until` を持たせて再利用する。**

  却下。
  `swipe` はすでに方向スクロールと座標ドラッグの 2 形を持つ。
  検証がその混在を防ぐ。
  3 つ目の「見えるまで繰り返す」形は、1 動詞に 3 挙動を負わせる。
  別動詞 `scroll` の方が呼び出し箇所で読みやすい。
  各動詞の契約を 1 つに保てる。

- **`scroll.direction` を `swipe` の指方向規約に合わせる。**

  検討した。
  ジェスチャ動詞をまたいでリテラルの意味を揃えたいため。
  デフォルトとしては却下。
  `scroll` に手を伸ばす作者はスクロール語彙で考える。
  「下へスクロール」でリストのさらに下を意味したい。
  下のコンテンツを出すのに指方向 `up` を書くのは意外な読み方。
  そこで `scroll.direction` はスクロール方向を指す。
  Unit 7 のドキュメントで `swipe` との対比を示す。
  反転は暗黙の落とし穴ではなく明示になる。
  共有リテラルの衝突を避ける別名は、実装時の判断として残す。
  対比記述で足りなければその別名を使う。

- **1 ステップの移動量 `amount` ノブ（`swipe` が持つもの）。**

  先送り。
  最初のスライスには含めない。
  デフォルトの画面相対ステップは、慣性なしで確実になるよう選んでいる。
  `amount` を出すと大きなステップで行き過ぎが戻りうる。
  調整の必要が現れたら、あとから足せる。
  アクションの形は変えなくてよい。

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

## 参考

- [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py) — 新しい `Scroll` の隣の `Swipe` / `Drag`
- [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py) — `_do_swipe` / `_scroll_gesture`
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — 条件待ちループ。`wait` は観測専用
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — 慣性なし契約の `Driver.scroll`
- [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml) — 動機となる手調整の `swipe` 連なり
- [`docs/drivers.md`](../../docs/drivers.md) — scroll-into-view を adb 専用と記す注記
- [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md) — 一般化する adb の `_scroll_into_view`
- [BE-0227](../BE-0227-web-swipe-scroll-fidelity/BE-0227-web-swipe-scroll-fidelity-ja.md) — web の慣性なしスクロール
- [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md) — 末尾検出のツリー再利用
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) — バックエンド横断の共有契約
