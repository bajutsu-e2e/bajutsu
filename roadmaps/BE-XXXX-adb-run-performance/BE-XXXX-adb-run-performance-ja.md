[English](BE-XXXX-adb-run-performance.md) · **日本語**

# BE-XXXX — adb のシナリオ実行を高速化する（uiautomator dump のボトルネック）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-adb-run-performance-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | プラットフォーム拡張（Android / Web / Flutter） |
| 関連 | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md), [BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation-ja.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Android の adb バックエンド（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)）で
シナリオを実行すると、同じシナリオを iOS の idb バックエンドで実行したときよりも明らかに遅く、
Android を対象にシナリオを書いて試行錯誤する体験が重く感じられます。この項目では、その原因を
1 つの支配的なコストに絞り込み、段階的な改善を提案します。まず、ランナーが画面を読み取る回数を
減らす 2 つの低リスクな改善、そして本命として、Android の読み取りを iOS より 1 桁遅くしている
「読み取りごとの起動コスト」を取り除く常駐 UI Automator サーバの導入です。ここで挙げる改善は
いずれも決定性の契約を崩しません。読み取りは条件待ちのままで固定 `sleep` を持ち込まず、曖昧な
セレクタは即座に失敗し続け、`run` の経路に LLM は入りません。

## 動機

遅さの原因はデバイスを「操作する」ことにではなく、「読み取る」ことにあります。adb バックエンドの
画面読み取り（`AdbDriver.query()`、[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)）は毎回
`adb exec-out uiautomator dump /dev/tty`（[`dump_cmd`](../../bajutsu/adb.py)）を呼び出しますが、この
コマンドには大きな**呼び出しごとの固定コスト**があります。

ローカルの arm64 エミュレータ（API 34）で、ランチャー画面（約 12 KB の小さなツリー）を対象に
計測した結果は次のとおりです。

| 操作 | 実時間 |
|---|---|
| `adb exec-out uiautomator dump /dev/tty`（`query()` の経路） | **約 2.3〜2.5 秒** |
| `uiautomator dump --compressed` | 約 2.0〜2.4 秒（改善せず） |
| デバイス上のファイルへ dump し `cat` する | 約 2.4〜2.5 秒（改善せず） |
| `adb shell input tap`（タップそのもの） | 約 0.07 秒 |
| adb の素の往復（`getprop`） | 約 0.04 秒 |
| iOS の `idb describe-all`（参考・リポジトリ記載値、[`waits.py`](../../bajutsu/orchestrator/waits.py)） | 約 0.1〜0.3 秒 |

ここから 2 つの事実がわかります。第 1 に、このコストは**ツリーのサイズ・圧縮・出力先に依存しない**
ため、XML の転送やツリーの走査ではなく、`uiautomator dump` の呼び出しごとのオーバーヘッドだと
いうことです。各呼び出しは、新しいインストゥルメンテーションのプロセスを起動し、`UiAutomation`
セッションを接続し、アイドルを待ち、ダンプし、セッションを破棄します。第 2 に、操作はすでに十分
速い（タップは 0.07 秒）ため、iOS と Android の差はまるごと読み取りにあります。すなわち、adb の
1 回の読み取りが約 2.4 秒であるのに対し idb は約 0.1〜0.3 秒で、**1 回の読み取りあたり 10〜20 倍の
差**です。

そしてこの読み取りごとのコストは、**1 ステップが何回読み取るか**によって増幅されます。ランナー
（[`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py)）では次のようになっています。

- `after = active_driver.query()` が**すべてのステップの末尾で無条件に**実行されます。その結果が
  使われるかどうかにかかわらず読み取ります（結果は `screenChanged`・`extract`・要素を伴う
  キャプチャに使われますが、単純な `tap` ステップはそのいずれも持ちません）。
- `before = active_driver.query()` は、シナリオの `capturePolicy` に `screenChanged` のルールが
  1 つでもあると、すべてのステップで実行されます。
- 操作自体もドライバ内部で少なくとも 1 回読み取ります（`_settle()` → `_resolve()`）。画面遷移の
  途中ではさらに増えます。

したがって、現実的な 1 ステップあたりの読み取り回数は、**下限として**次のとおりです。

| ステップ | 読み取り回数（下限） | 約 2.4 秒/回 での実時間 |
|---|---|---|
| `tap`（`screenChanged` ポリシーなし） | settle 1 + after 1 = **2** | 約 4.8 秒 |
| `tap`（`screenChanged` ポリシーあり） | before 1 + settle 1 + after 1 = **3** | 約 7.2 秒 |
| `assert` | 本体 1 + after 1 = **2** | 約 4.8 秒 |

この表の `settle 1` は*ベストケース*です。`_settle()` は 1 回読み取り、直前の読み取りから
identifier／frame のキーが変わっていなければ即座に返しますが、`tap` はしばしば画面を実際に
変えます。キーが変わると `_settle()` はツリーが動かなくなるのを待って最大 `_SETTLE_MAX_POLLS`
（= 3）回まで追加で読み取ります。したがって画面を変える `tap` は `settle` だけで最大
**4 回（約 9.6 秒）**かかりえます。表の回数は下限であり、遷移の多いステップはさらにかかります。

そのため 10 ステップのシナリオでは、**下限でダンプだけでおよそ 48 秒、遷移があればそれ以上**を
費やす計算になります。これが Android でシナリオを書く体験を重くしている正体であり、その負荷は
すべて読み取りに由来します。

これはバックエンド内部の性能ギャップなので、prime directive 3（アプリ非依存）の枠内にきれいに
収まります。修正は adb ドライバと共有ランナーの内部にとどまり、どのシナリオも変えず、アプリ
ごとの特別扱いも加えません。決定性の契約は保ちます。条件に基づく読み取りは条件待ちのままとし
（固定 `sleep` は使わず）、曖昧な一致は即座に失敗し続けます。

## 詳細設計

安価で自己完結した改善を先に入れて計測の足場を固め、アーキテクチャの変更はフォールバックの背後
で最後に入れる、という段階構成にします。対象は adb ドライバ
（[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)）、共有ランナー
（[`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py)）、そして作業単位 3 では
常駐サーバという依存の追加です。どの共有シナリオも変えず、どの経路にも LLM を加えません。各単位は
ドライバ conformance スイート（[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）
と Android の e2e レーン（[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)）
で検証し、実機での前後のステップ時間を記録します。

### 作業分解（MECE）

1. **基準値の確立と、ステップごとの読み取り回数カウンタ。** 何も変える前に、代表的な共有シナリオ
   の実機での実時間を記録し、ステップごとの読み取り回数を計測できるようにします（デバッグログか
   実行サマリの 1 行）。これにより、後続の各単位の効果を主張ではなく計測で示します。これが作業単位
   2〜4 を測る物差しであり、動機で提起した「では、どれだけ遅いのか」という問いに答えます。

2. **結果が使われないときは画面を読まない（ランナー、低リスク）。** 各ステップ末尾の
   `after = active_driver.query()`（[`loop.py`](../../bajutsu/orchestrator/loop.py)）を
   **遅延・条件付き**にします。実際に消費されるとき——シナリオが `wants_screen_changed` である、
   ステップに `extract` がある、発火したキャプチャが `elements` を必要とする——にだけ読み取ります。
   そのいずれでもない単純な `tap`／`assert` ステップでは読み取りを丸ごと省き、1 ステップあたり
   約 2.4 秒を削ります。これは冗長な読み取りの純粋な除去であって、どの条件待ちも変えないため、
   決定性はそのままです。

   `before` の読み取りは事情が異なり、*より強い*扱いをします。`screenChanged` のキャプチャルールは
   ステップにスコープされません（[`evidence_rules.py`](../../bajutsu/orchestrator/evidence_rules.py)
   の `_rule_fires` は `screen_changed` の真偽だけを見ており、ステップの種別／id は見ません）。
   そのため、シナリオが `wants_screen_changed` であるときはすべてのステップの `before` が本当に
   必要で、そこに条件付けで省ける余地はありません。しかし、あるステップ末尾の `after`
   （[`loop.py`](../../bajutsu/orchestrator/loop.py):449）と次のステップの `before`
   （[`loop.py`](../../bajutsu/orchestrator/loop.py):417）のあいだでは、デバイスを操作するものが
   何もありません。両者は*同一の*デバイス状態を観測します。そこで `before` を条件付けるのではなく、
   **直前のステップの `after` をこのステップの `before` として再利用**し、`before` の読み取りを
   ステップごとに条件付けるのではなくシナリオ全体で（ほぼ）ゼロに落とします。最初のステップ（直前の
   `after` がない）だけは 1 回読み取ります。どちらの変更もバックエンド非依存で、idb も（読み取りが
   すでに安いため相対的には小さいものの）速くなります。

3. **遅い読み取りに合わせて `_settle` を調整する（adb、低リスク）。** adb ドライバの settle 定数
   （`_SETTLE_POLL_S = 0.05`、`_SETTLE_MAX_POLLS = 3`、[`adb.py`](../../bajutsu/drivers/adb.py)）は、
   読み取りが安くポーリング間隔が支配的な idb から受け継いだものです。adb では読み取り自体が約 2.4 秒
   なので、ポーリング間隔は事実上効かず、本当のレバーは settle の読み取り**回数**です。遅い読み取りに
   合わせて settle の上限を調整し（かつその根拠を残し）、静止した画面は 1 回の読み取りで settle し、
   本当にアニメーション中の画面だけが追加の読み取りを払うようにします。「ツリーが動かなくなるまで待つ」
   という意味は保ったまま（依然として条件待ちで、固定 sleep はなし）です。あわせて、単発の余計な
   読み取り（[`gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py)・
   [`alerts.py`](../../bajutsu/alerts.py)・[`crawl.py`](../../bajutsu/crawl.py) の
   `screen_size_from_elements(driver.query())`）も、画面サイズをすでに読んだツリーから再利用できる
   ところは統合します。

4. **ダンプごとの起動を常駐 UI Automator サーバで置き換える（adb、本命）。** 作業単位 2〜3 が越え
   られない下限は、`uiautomator dump` の**呼び出しごと**の約 2.4 秒です。これを、実行のあいだ
   **常駐する UI Automator インストゥルメンテーション**を保ち、階層をローカルのソケット／HTTP 経由で
   問い合わせることで取り除きます。読み取りのたびに新しいインストゥルメンテーションを起動する代わりに
   です——Appium の UiAutomator2 ドライバが採る方式です。期待される結果は、読み取りが約 2.4 秒から
   約 0.1〜0.3 秒へ下がり、iOS との 10〜20 倍の差が縮まることです。常駐サーバは**一様でアプリに
   依存しない**部品（テスト対象がどのアプリであってもそれを駆動する）なので、BajutsuKit／
   BajutsuAndroid の on-device SDK
   （[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md)）と同じ意味で
   アプリ非依存であり、アプリごとの特別扱いではありません。この単位が差し替えるのは
   `AdbDriver._describe()` の内部だけで、パース（`parse_hierarchy`）・正規化・transient-empty の
   リトライ・セレクタ／解決の契約はすべて変わりません。常駐サーバが使えないときのフォールバックとして
   既存の `uiautomator dump` 経路を残すので、どのデバイスも今より悪くはなりません。

5. **改善をリグレッションから守る。** 前後のステップ時間をこの項目の進捗ログに記録し、冗長な
   読み取りや遅い経路を再び持ち込む変更を捉えられるよう、ドライバ conformance スイートと e2e レーン
   を拡張します。（実時間の CI ゲートはスコープ外とします。実時間は環境依存で不安定になるため、
   ここでは計測の記録と、安定して置ける箇所での読み取り回数のアサーションにとどめます。）

## 検討した代替案

- **`uiautomator dump --compressed`。** 主たる修正としては却下します。計測では約 2.0〜2.4 秒で、
  効果がありません（コストはツリーサイズではなく起動にあります）。加えてセレクタ層が必要とする
  ノードを落とします。付随的な微調整として併用する余地はありますが、ボトルネックには効きません。

- **`/dev/tty` の代わりにデバイス上のファイルへ dump し pull／`cat` する。** 却下します。計測では
  約 2.4〜2.5 秒で同一——出力先はコストではありません。

- **ポーリングを減らす／固定 sleep で遅延を覆い隠す。** 明確に却下します。固定 `sleep` は
  prime directive 2 に反し、条件が要求するより読み取り回数を減らせば実行が非決定的になります。
  正しい修正は、必要な読み取りを 1 回ずつ安くし、**不要な**読み取りだけを落とすことであって、
  条件待ちを弱めることではありません。

- **作業単位 4（常駐サーバ）を独立した BE 項目に分ける。** 妥当な案です。常駐サーバの設計が
  ふくらむ（インストゥルメンテーション APK のパッケージング、そのライフサイクル、依存の増加）よう
  なら、独立した項目に昇格させ、この項目は作業単位 1〜3 に絞るべきです。いまは 1 つにまとめます。
  すべての単位が 1 つの動機（読み取りに律速される Android の実行）と 1 つの物差し（作業単位 1 の
  ステップごとの実時間）を共有しており、1 つの項目で段階的に入れるほうが端から端まで計測を
  正直に保てるためです。

- **この差を Android に内在するものとして受け入れる。** 却下します。10〜20 倍という数字は
  呼び出し**ごと**の起動コストであって、Android の階層を読むこと自体に内在する性質ではありません。
  常駐セッションはその起動を一度だけ払います——それがまさに作業単位 4 の内容です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 実機の基準値と、ステップごとの読み取り回数カウンタを確立する（作業単位 2〜4 の物差し）。
- [ ] ステップ末尾の `after` 読み取りを遅延・条件付きにし、次ステップの `before` は（同一状態なので）それを再利用して読み直さない。
- [ ] 遅い読み取りに合わせて `_settle` を調整し、単発の余計な読み取りを統合する。
- [ ] ダンプごとの起動を常駐 UI Automator サーバで置き換え、`uiautomator dump` をフォールバックに残す。
- [ ] 前後の時間を記録し、改善を守る（conformance／e2e、不安定な実時間ゲートは設けない）。

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
