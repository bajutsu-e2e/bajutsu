[English](BE-0234-adb-run-performance.md) · **日本語**

# BE-0234 — adb のシナリオ実行を高速化する（uiautomator dump のボトルネック）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0234](BE-0234-adb-run-performance-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0234") |
| 実装 PR | [#987](https://github.com/bajutsu-e2e/bajutsu/pull/987)、[#1944](https://github.com/bajutsu-e2e/bajutsu/pull/1944)（最後のユニットを判断によって閉じる） |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md), [BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation-ja.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Android の adb バックエンド（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)）で
シナリオを実行すると、同じシナリオを iOS の idb バックエンドで実行したときより明らかに遅くなります。
そのため、Android を対象にシナリオを書いて試行錯誤する体験が重く感じられます。

この項目では、原因を 1 つの支配的なコストに絞り込み、段階的な改善を提案します。ランナーが画面を
読み取る回数を減らす低リスクな改善が 2 つあります。本命は常駐 UI Automator サーバの導入です。
Android の読み取りを iOS より 1 桁遅くしている「読み取りごとの起動コスト」を、このサーバが取り除きます。

ここで挙げる改善は、いずれも決定性の契約を崩しません。読み取りは条件待ちのままで、曖昧な
セレクタは即座に失敗し続けます。`run` の経路に LLM は入りません。

## 動機

遅さの原因は、デバイスを「操作する」ことにはありません。「読み取る」ことにあります。adb バックエンドの
画面読み取り（`AdbDriver.query()`、[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)）は、毎回
`adb exec-out uiautomator dump /dev/tty`（[`dump_cmd`](../../bajutsu/adb.py)）を呼び出します。この
コマンドには、大きな**呼び出しごとの固定コスト**があります。

計測は、ローカルの arm64 エミュレータ（API 34）で行いました。対象は、ランチャー画面（約 12 KB の
小さなツリー）です。

| 操作 | 実時間 |
|---|---|
| `adb exec-out uiautomator dump /dev/tty`（`query()` の経路） | **約 2.3〜2.5 秒** |
| `uiautomator dump --compressed` | 約 2.0〜2.4 秒（改善せず） |
| デバイス上のファイルへ dump し `cat` する | 約 2.4〜2.5 秒（改善せず） |
| `adb shell input tap`（タップそのもの） | 約 0.07 秒 |
| adb の素の往復（`getprop`） | 約 0.04 秒 |
| iOS の `idb describe-all`（参考・リポジトリ記載値、[`waits.py`](../../bajutsu/orchestrator/waits.py)） | 約 0.1〜0.3 秒 |

この数字から、2 つの事実がわかります。

- **コストは、ツリーのサイズ、圧縮、出力先に依存しません。** したがって、XML の転送やツリーの走査が
  原因ではありません。原因は、`uiautomator dump` の呼び出しごとのオーバーヘッドです。各呼び出しは、
  新しいインストゥルメンテーションのプロセスを起動し、`UiAutomation` セッションを接続し、アイドルを
  待ってダンプし、セッションを破棄します。
- **操作はすでに十分速いです。** タップは 0.07 秒なので、iOS と Android の差はまるごと読み取りに
  あります。adb の読み取りは約 2.4 秒、idb は約 0.1〜0.3 秒で、1 回の読み取りあたり 10〜20 倍の
  差です。

この読み取りごとのコストは、1 ステップが何回読み取るかによって増幅されます。ランナー
（[`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py)）の読み取りは次のとおりです。

- `after = active_driver.query()` は、**すべてのステップの末尾で無条件に**実行されます。結果が
  使われるかどうかにかかわらず読み取ります。結果は `screenChanged`、`extract`、要素を伴うキャプチャに
  使われますが、単純な `tap` ステップはそのいずれも持ちません。
- `before = active_driver.query()` は、シナリオの `capturePolicy` に `screenChanged` のルールが
  1 つでもあると、すべてのステップで実行されます。
- 操作自体も、ドライバ内部で少なくとも 1 回読み取ります（`_settle()` → `_resolve()`）。画面遷移の
  途中では、読み取りがさらに増えます。

1 ステップあたりの現実的な読み取り回数は、下限として次のとおりです。

| ステップ | 読み取り回数（下限） | 約 2.4 秒/回 での実時間 |
|---|---|---|
| `tap`（`screenChanged` ポリシーなし） | settle 1 + after 1 = **2** | 約 4.8 秒 |
| `tap`（`screenChanged` ポリシーあり） | before 1 + settle 1 + after 1 = **3** | 約 7.2 秒 |
| `assert` | 本体 1 + after 1 = **2** | 約 4.8 秒 |

この表の `settle 1` は*ベストケース*です。`_settle()` は 1 回読み取り、直前の読み取りから
identifier／frame のキーが変わっていなければ、即座に返ります。しかし `tap` は、しばしば画面を実際に
変えます。キーが変わると、`_settle()` はツリーが動かなくなるのを待って、最大 `_SETTLE_MAX_POLLS`
（= 3）回まで追加で読み取ります。画面を変える `tap` は、`settle` だけで最大
**4 回（約 9.6 秒）**かかりえます。表の回数は下限であり、遷移の多いステップはさらにかかります。

10 ステップのシナリオでは、下限でも**ダンプだけでおよそ 48 秒**を費やします。遷移があれば、それ以上です。
このコストが Android でシナリオを書く体験を重くしており、その負荷はすべて読み取りに由来します。

この性能ギャップはバックエンド内部のものなので、prime directive 3（アプリ非依存）の枠内に
収まります。修正は adb ドライバと共有ランナーの内部にとどまり、どのシナリオも変えず、アプリごとの
特別扱いも加えません。決定性の契約も保ちます。条件に基づく読み取りは条件待ちのままで、固定 `sleep`
は使いません。曖昧な一致は、即座に失敗し続けます。

## 詳細設計

**実装したスコープ：** 作業単位 1〜3 と 5 を、下記の実装 PR で入れました。読み取り回数の削減、adb の
`_settle` 調整、計測、リグレッションガードです。

**作業単位 4（常駐 UI Automator サーバ）は、後続の項目に切り出しました。** 検証に実機が必要で、
計装をパッケージする作業も伴うため、この項目の高速ゲート内の変更には収まりません。この分割は、
下記「検討した代替案」があらかじめ許した道筋（「作業単位 1〜3 に絞る」）です。作業単位 4 の記述は
連続性のためここに残しますが、実際に作るのは後続の項目です。

作業は段階に分けます。安価で自己完結した改善を先に入れ、計測の足場を固めます。アーキテクチャの
変更は、フォールバックの背後で最後に入れます。

対象は、adb ドライバ（[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)）と共有ランナー
（[`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py)）です。作業単位 3 では、
常駐サーバという依存も加わります。どの共有シナリオも変えず、どの経路にも LLM を加えません。

各単位は、ドライバ conformance スイート
（[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）と
Android の e2e レーン（[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)）
で検証します。実機での前後のステップ時間も記録します。

### 作業分解（MECE）

1. **基準値の確立と、ステップごとの読み取り回数カウンタ。** 何も変える前に、代表的な共有シナリオの
   実機での実時間を記録します。あわせて、ステップごとの読み取り回数を計測できるようにします
   （デバッグログか、実行サマリの 1 行）。これにより、後続の各単位の効果を、主張ではなく計測で示せます。
   基準値は、作業単位 2〜4 を測る物差しです。動機で提起した「では、どれだけ遅いのか」という問いにも
   答えます。

2. **結果が使われないときは画面を読まない（ランナー、低リスク）。** 各ステップ末尾の
   `after = active_driver.query()`（[`loop.py`](../../bajutsu/orchestrator/loop.py)）を
   **遅延・条件付き**にします。読み取るのは、結果が実際に消費されるときだけです。シナリオが
   `wants_screen_changed` である、ステップに `extract` がある、発火したキャプチャが `elements` を
   必要とする、のいずれかです。どれにも当たらない単純な `tap`／`assert` ステップは、読み取りを
   丸ごと省きます。これで 1 ステップあたり約 2.4 秒を削れます。冗長な読み取りを除くだけで、どの
   条件待ちも変えないため、決定性はそのままです。

   `before` の読み取りには、*より強い*扱いをします。`screenChanged` のキャプチャルールは、
   ステップにスコープされません。[`evidence_rules.py`](../../bajutsu/orchestrator/evidence_rules.py)
   の `_rule_fires` は、`screen_changed` の真偽だけを見ており、ステップの種別や id は見ません。
   シナリオが `wants_screen_changed` であれば、すべてのステップの `before` が本当に必要です。
   ステップごとの条件で省く余地はありません。

   あるステップ末尾の `after`（[`loop.py`](../../bajutsu/orchestrator/loop.py):449）と、次の
   ステップの `before`（[`loop.py`](../../bajutsu/orchestrator/loop.py):417）のあいだでは、
   デバイスを操作するものが何もありません。両者は*同一の*デバイス状態を観測します。そこで
   `before` を条件付けるのではなく、**直前のステップの `after` をこのステップの `before` として
   再利用**します。`before` の読み取りは、ステップごとに条件付けるのではなく、シナリオ全体で
   （ほぼ）ゼロに落ちます。直前の `after` がない最初のステップだけは、1 回読み取ります。

   どちらの変更もバックエンド非依存で、idb も速くなります。idb は読み取りがすでに安いため、
   相対的な効果は小さくなります。

3. **遅い読み取りに合わせて `_settle` を調整する（adb、低リスク）。** adb ドライバの settle 定数
   （`_SETTLE_POLL_S = 0.05`、`_SETTLE_MAX_POLLS = 3`、[`adb.py`](../../bajutsu/drivers/adb.py)）は、
   idb から受け継いだものです。idb では読み取りが安く、ポーリング間隔が支配的です。adb では読み取り
   自体が約 2.4 秒なので、ポーリング間隔は事実上効きません。本当のレバーは、settle の読み取り**回数**です。

   遅い読み取りに合わせて settle の上限を調整し、その根拠を残します。これにより、静止した画面は
   1 回の読み取りで settle します。本当にアニメーション中の画面だけが、追加の読み取りを払います。
   「ツリーが動かなくなるまで待つ」という意味は保ちます。依然として条件待ちで、固定 sleep は
   ありません。

   単発の余計な読み取りも統合します。対象は、[`gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py)、
   [`alerts.py`](../../bajutsu/alerts.py)、[`crawl.py`](../../bajutsu/crawl.py) にある
   `screen_size_from_elements(driver.query())` です。画面サイズを、すでに読んだツリーから再利用できる
   ところは、再利用します。

4. **ダンプごとの起動を常駐 UI Automator サーバで置き換える（adb、本命）。** 作業単位 2〜3 が越え
   られない下限があります。`uiautomator dump` の**呼び出しごと**の約 2.4 秒です。この単位は、その
   下限を取り除きます。実行のあいだ**常駐する UI Automator インストゥルメンテーション**を保ち、
   階層をローカルのソケットか HTTP 経由で問い合わせます。読み取りのたびに、新しいインストゥルメン
   テーションの起動を払わずに済みます。Appium の UiAutomator2 ドライバも、同じ方式を採ります。
   期待される結果は、読み取りが約 2.4 秒から約 0.1〜0.3 秒へ下がり、iOS との 10〜20 倍の差が
   縮まることです。

   常駐サーバは、**一様でアプリに依存しない**部品です。テスト対象がどのアプリであっても、それを
   駆動します。BajutsuKit と BajutsuAndroid の on-device SDK
   （[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md)）と同じ意味で
   アプリ非依存であり、アプリごとの特別扱いではありません。

   この単位が差し替えるのは、`AdbDriver._describe()` の内部だけです。パース（`parse_hierarchy`）、
   正規化、transient-empty のリトライ、セレクタ／解決の契約は、すべて変わりません。常駐サーバが
   使えないときのフォールバックとして、既存の `uiautomator dump` 経路を残します。どのデバイスも、
   今より悪くはなりません。

5. **改善をリグレッションから守る。** 前後のステップ時間を、この項目の進捗ログに記録します。
   冗長な読み取りや遅い経路を再び持ち込む変更を捉えられるよう、ドライバ conformance スイートと
   e2e レーンを拡張します。

   実時間の CI ゲートは、スコープ外とします。実時間は環境に依存し、ゲートが不安定になるためです。
   守りの手段は、計測の記録と、安定して置ける箇所での読み取り回数のアサーションにとどめます。

## 検討した代替案

- **`uiautomator dump --compressed`。** 主たる修正としては却下します。計測では約 2.0〜2.4 秒で、
  効果がありません。コストはツリーサイズではなく起動にあるためです。しかも、セレクタ層が必要と
  するノードを落とします。付随的な微調整として併用する余地はありますが、ボトルネックには効きません。

- **`/dev/tty` の代わりにデバイス上のファイルへ dump し、pull／`cat` する。** 却下します。計測では
  約 2.4〜2.5 秒で同一です。出力先はコストではありません。

- **ポーリングを減らす、または固定 sleep で遅延を覆い隠す。** 明確に却下します。固定 `sleep` は
  prime directive 2 に反します。条件が要求するより読み取り回数を減らせば、実行が非決定的になります。
  正しい修正は、必要な読み取りを 1 回ずつ安くし、**不要な**読み取りだけを落とすことです。条件待ちを
  弱めることは、決してしません。

- **作業単位 4（常駐サーバ）を独立した BE 項目に分ける。** 妥当な案です。常駐サーバの設計が
  ふくらむ場合（インストゥルメンテーション APK のパッケージング、そのライフサイクル、依存の増加）は、
  独立した項目に昇格させ、この項目は作業単位 1〜3 に絞るべきです。いまは 1 つにまとめます。
  すべての単位が、1 つの動機（読み取りに律速される Android の実行）と、1 つの物差し（作業単位 1 の
  ステップごとの実時間）を共有しているためです。1 つの項目で段階的に入れるほうが、端から端まで
  計測を正直に保てます。

- **この差を Android に内在するものとして受け入れる。** 却下します。10〜20 倍という数字は、
  呼び出し**ごと**の起動コストです。Android の階層を読むこと自体に内在する性質ではありません。
  常駐セッションは、その起動を一度だけ払います。それがまさに作業単位 4 の内容です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] ステップごとの読み取り回数カウンタを用意する。ランナーはステップごとに発行した画面読み取りの
  回数とシナリオ全体の合計をログに出す（下記の削減を測る物差し）。実機での実時間の基準値は、実機で
  この変更を動かすときに記録する。
- [x] ステップ末尾の `after` 読み取りを遅延させ（`screenChanged` のキャプチャ、`extract`、wait
  タイムアウト診断のいずれかがツリーを必要とするときだけ読む）、次ステップの `before` は（同一状態
  なので）直前ステップの `after` を再利用して読み直さない。
- [x] 遅い読み取りに合わせて `_settle` を調整する。adb では約 2.4 秒の読み取り自体が settle ループの
  間隔を作るので、ポーリング間隔は 0 にする（ポーリング回数は settle の条件を保つため 3 のまま）。
  そのほかの単発の読み取り（共有のジェスチャ／アラート／crawl ハンドラにある
  `screen_size_from_elements(driver.query())`）は、呼び出し地点に再利用できる既読ツリーがないため、
  統合するにはドライバ側に直近読み取りのキャッシュが要る。これは下記の常駐サーバの作業とともに保留
  にする。
- [x] ~~ダンプごとの起動を常駐 UI Automator サーバで置き換え、`uiautomator dump` をフォール
  バックに残す。~~
  - 後続の項目に切り出しました。検証に実機が必要で、計装をパッケージする作業も伴うため、
    この項目の高速ゲート内の変更には収まりません。
    [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)
    として着地しました。
- [x] 高速ゲートで読み取り回数を検査して改善を守る（ランナー側の削減は
  `tests/orchestrator/test_read_count.py`、adb の `_settle` の読み取りは `tests/test_adb.py`）。
  実時間のゲートはスコープ外のまま（環境に依存し、不安定になる）。

## 参考

[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend-ja.md),
[BE-0210 — Android on-device actuation fidelity](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md),
[BE-0223 — adb でタブバーを駆動してすべての Android タブに到達する](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation-ja.md),
[BE-0233 — adb クリップボードの実機忠実度](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md),
[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md),
[BE-0208 — Android on-device e2e in CI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md),
[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py),
[`bajutsu/adb.py`](../../bajutsu/adb.py),
[`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py),
[`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py)
