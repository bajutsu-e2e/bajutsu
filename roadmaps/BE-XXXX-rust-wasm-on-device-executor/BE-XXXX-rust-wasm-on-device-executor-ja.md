[English](BE-XXXX-rust-wasm-on-device-executor.md) · **日本語**

# BE-XXXX — 共有Rust/WASMコアによるシナリオのオンデバイス実行

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-rust-wasm-on-device-executor-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform support |
| 関連 | [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)、[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)、[BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)、[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)、[BE-0365](../BE-0365-in-app-control-channel/BE-0365-in-app-control-channel-ja.md)、[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)は、
[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)
単独では届かなかった、250〜500ミリ秒という1ステップあたりの目標へ至る道筋として、端末側の
ステップ実行プロトコルを定めています。`wait`や画面に閉じた`assert`は、条件を1回ポーリングする
たびにホスト・デバイス間の往復を1回払うため、BE-0408はセレクタ解決と条件評価をデバイス側へ
移します。実装はSwiftとKotlinへの独立した移植という形を取り、iOS側の実行機を
[BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)、
Android側の実行機を
[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)
がそれぞれ提案しています。本項目は、同じ目標に対する別の実装戦略を提案します。判定ロジックを
Rustで一度だけ書き、ホストが呼ぶネイティブなPython拡張(PyO3経由)と、2つの端末ランタイムが
埋め込むWebAssembly(WASM)の両方へコンパイルします。SwiftとKotlinという、同じ規則を持つ2つの
独立した再実装を保守し続ける代わりの選択です。本項目はBE-0408が扱う範囲も広げます。共有コアは
シナリオの制御フロー(`if`、`for_each`、アラートガードのリトライ)もデバイス側で解釈します。
個々のステップだけでなく、シナリオ全体をステップごとの往復なしに実行します。

## 動機

[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)は、
1回の`tap`ステップが目標の250〜500ミリ秒に対してiOSで0.95〜1.07秒、Androidで3.25〜3.32秒
かかることを計測しました。支配的なコストはホスト・デバイス間の往復そのものであり、ホストは
50ミリ秒間隔で条件をポーリングするたびに、HTTPラウンドトリップを1回払います。BE-0408は、
セレクタ解決とwait・assertの評価をデバイス側へ移すことでこれに対処します。実装はSwiftと
Kotlinへそれぞれ独立に移植する形です。

この独立移植には、BE-0408自身が名指ししている決定性のリスクが伴います。ホスト側のPython実装
(`find_all`・`resolve_unique`、
[`bajutsu/common/drivers/base/_functions.py:152,274`](../../bajutsu/common/drivers/base/_functions.py))、
iOS向けのSwift移植、Android向けのKotlin移植という3つの独立した実装が、同じセレクタに対して
常に同じ判定を返し続けなければなりません。BE-0408は
[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)の
ドライバ適合スイートを拡張してズレを検知する計画ですが、これは起きた後で検知する対策です。
ズレが起こる可能性そのものを取り除くものではありません。セレクタ解決の規則を将来変更するたびに、
変更した人は3つの言語で同じ変更を行う必要があります。BE-0408が対象を2バックエンドに絞っても、
この継続コストは消えません。

もう1つのコストは、段階的ロールアウトが最終段階に至るまでに得られる往復削減の幅です。
BE-0408はまず`wait`と`assert`の評価をデバイスへ移し、第4段階に至って初めてステップ列を
1回の`POST /scenario`呼び出しにまとめます。それまでの間、システムアラートがステップを
ブロックしたときの1回リトライ判定のような、ステップをまたいだ判定は、第4段階に至っても
ホストの役目のまま残ります。BE-0408が制御フローそのものをホスト側の範囲としているためです。
最初からシナリオ全体を、制御フローも含めてランタイムへ渡せば、ホスト・デバイス間の往復は
フェーズの境界と区間証跡の開始・終了の合図だけに縮みます。これはBE-0408の第4段階が目指す
到達点と同じであり、中間段階を経ずに至ります。

本項目の中心的な主張は、新しい実機がなくても確かめられます。本項目が定義するRustクレートを、
PyO3向けと`wasm32-unknown-unknown`向けの両方に同じソースからビルドします。ドライバ適合
スイートのフィクスチャ集合に対して、両方のビルドが同じ結果を返すことを、一度きりではなくCIで
継続的に確認します。BE-0408の3つの独立実装に置き換わるのは、共有ソースそのものが与える構造的な
保証ではなく、この継続検査に支えられた単一のクレートです。

## 詳細設計

### 何を移し、何を移さないか

ホストは、BE-0408がすでに割り当てている次の3つの役目をそのまま持ち続けます。

- pass/failの確定です。デバイスは、解決済みの要素の属性やアサーション対象の観測値といった
  生の証拠を返すのであって、判定そのものは返しません。ホストは、同じ共有コア(PyO3
  バインディング)を通じてその証拠から`ok`を再計算します。デバイス自身が計算した`ok`を
  そのまま信用することはありません。本項目はシナリオ展開もデバイス側へ移すため、報告された
  各ステップの判定を再計算するだけでは足りません。ホストは同じバインディングでシナリオを
  ローカルにも展開し、報告されたステップ列がその展開結果と一致することを求めます。そうしないと、
  デバイス側の制御フローに誤り(`if`の分岐違い、`for_each`の反復回数の誤り)があっても、
  デバイスが報告したステップだけを見れば`ok=true`が並び、飛ばされたステップに触れないまま
  runが合格してしまいかねません。
- レポート用の証跡の受け取りです。`manifest.json`とHTMLレポートは、今日と同じ`StepOutcome`
  の形から書き出します。
- 区間証跡です。video・deviceLogの開始・終了は`simctl io recordVideo`のようなホスト側の
  OSプロセス制御を要し、デバイス単体では起動できません。各区間証跡の開始・終了をいつ判断する
  かは、引き続きホストが決めます。

それ以外のすべてを共有クレートへ移します。シナリオの展開(`if`・`for_each`・コンポーネント
展開・`vars.*`補完。今日は
[`bajutsu/common/scenario/expand.py`](../../bajutsu/common/scenario/expand.py)にあります)、
セレクタ解決、act→wait→verifyのステップループ
([`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py))、
アラートガードの1回リトライ判定、証跡ルールの発火判定
([`bajutsu/common/orchestrator/evidence_rules.py`](../../bajutsu/common/orchestrator/evidence_rules.py))
です。BE-0408がシナリオ展開とステップをまたぐ判定をホスト側に残しているのは、そのロジックを
デバイス側へ独立に移植すれば、セレクタの意味論がズレる場所がもう1つ増えるだけだからです。
共有された1つのバイナリはこの理由を取り除くため、本項目は同じ境界を引き継ぎません。

### 1つのクレート、2つのビルドターゲット

新設するクレート`rust/bajutsu_core/`は、シナリオ展開、セレクタ解決、ステップループ、
証跡ルールの発火判定を、今日のPythonの振る舞いと規則単位で一致させて実装します。同じ
ソースから2通りにビルドします。

- **PyO3バインディング**です。Pythonの拡張モジュールとして、デバイスが返す生の証拠から
  ステップの判定をホストが再計算するために呼びます。ホストはこのバインディングを使って
  PlaywrightやFakeDriverを直接操作することはありません。これらのバックエンドは、既存の
  `run_scenario`
  ([`bajutsu/common/orchestrator/loop/_functions.py:571`](../../bajutsu/common/orchestrator/loop/_functions.py))
  を通じてそのまま動き続けます。
- **`wasm32-unknown-unknown`バイナリ**です。iOSのXCTestランナープロセス(`BajutsuKit`)と、
  Androidのresident UI Automatorサーバー(`BajutsuAndroidUIAutomatorServer`)の両方に埋め込み
  ます。どちらの埋め込みもOS APIを直接呼びません。各プラットフォームが、クレートの
  `Driver`トレイトをWASMのホスト関数(タップ、型入力、スワイプ、ツリー読み取り、
  スクリーンショット)として提供します。

同じクレートから2つのターゲットをビルドしても、それだけで両者が常に同じ結果を返す保証には
なりません。PyO3バインディングはネイティブターゲット向け、WASMバイナリは
`wasm32-unknown-unknown`向けであり、`usize`の幅(64ビット対32ビット)、浮動小数点の丸めと
文字列化、`HashMap`の反復順といったターゲット依存の差が、`resolve_unique`の重複畳み込み
([`bajutsu/common/drivers/base/_functions.py:274`](../../bajutsu/common/drivers/base/_functions.py))
のように順序が判定に効く箇所で、両者を分けることがあります。クレートは順序が判定に効く箇所を
`BTreeMap`のような決定的な型に限り、
[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)の
ドライバ適合スイートが、`FakeDriver`や他のバックエンドと同じフィクスチャに対して両ターゲットが
同一の結果を返すことをCIで継続的に突き合わせます。BE-0408が抱える3実装間のズレを防いでいる
のは、共有ソースそのものではなく、この継続的な差分検査です。

### ホスト側の変更

`run_scenario`の入り口に、分岐を1つ加えます。対象の`Driver`が新しい`Capability`
(`bajutsu/common/drivers/base/capability.py`に追加)を宣言している場合、ホストは既存の
ステップループを回す代わりに、シナリオ全体(`before`・`steps`・`expect`・`after`)を1回の
リクエストでランタイムへ送ります。この`Capability`を宣言していないドライバの挙動は変わり
ません。Playwright、FakeDriver、オンデバイス実行機をまだビルドしていないxcuitest・adb
ターゲットは、今日の`_run_step_body`
([`bajutsu/common/orchestrator/loop/_functions.py:302`](../../bajutsu/common/orchestrator/loop/_functions.py))
をそのまま通じて動きます。

ランタイムは、ステップが完了するたびに、同じ接続の上で結果を1件ずつ改行区切りJSONとして
ストリーミングで返します。BE-0409が自身の`POST /scenario`エンドポイントですでに定めている
チャンク転送の方式をそのまま使います。ホストはこの行を1件ずつ読み取り、既存の`StepOutcome`
と同じ形へ変換して、今日`StepOutcome`を消費しているレポート書き込み・CLIのライブ出力・
`serve`のログバスへそのまま渡します。CLIのライブ出力もWeb UIのReplayタブの見え方も変わりま
せん。ホストは、ステップを合格と扱う前に、この行に含まれる生の証拠からPyO3バインディング
経由で`ok`を再計算します。

### iOSとAndroidのランタイム

両プラットフォームとも、`POST /scenario`エンドポイントを追加します。名前はBE-0409がすでに
提案しているものと同じにします。どちらの実装が採用されても、このエンドポイント名の周辺で
積んだ作業を転用できるようにするためです。`APIHandler`
([`BajutsuKit/Sources/BajutsuRunner/APIHandler.swift`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift))
は、他のXCTest操作がすでに直列化しているのと同じメインスレッド(BE-0323の非再入性)の上で
WASMランタイムを動かし、クレートのWASMバイナリをロードします。ホスト関数としては、
[`XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)
が持つタップ・スナップショット・型入力・スワイプ・スクリーンショットを提供します。埋め込む
Swift向けWASMランタイム(wasmtimeのSwiftバインディングか、純Swift実装のWasmKitか)は未決定
であり、他のiOS向け作業に先立って最初の作業単位が答えを出す実現可能性の問いです。

`APIHandler`の他のあらゆる呼び出しは、`operations`という直列キューと、`serialized(_:)`内の
`DispatchQueue.main.sync`を通じて同じスレッドへ直列化されています
([`APIHandler.swift:51,354-366`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift))。
シナリオ全体を実行する`POST /scenario`は、実行中このキューを占有し続けます。そのため
`POST /scenario/cancel`や区間証跡の開始・終了を伝える合図を同じキュー経由で届けると、
それぞれが止めるべき当のシナリオが終わるまで届きません。今日`health`・`setInterruptionPolicy`
がこのキューを迂回しているのと同じ扱いを、この2つの新しいエンドポイントにも明示的に与える
必要があります。

Androidのresident UI Automatorサーバーには、JVM上で動くWASMランタイムを組み込みます。
候補の1つはChicoryです。純JVM実装であり、instrumentationテストターゲットが別途必要とする
ネイティブライブラリの署名手順を避けられます。サーバーは、既存の`UiAutomation`セッションと
`AccessibilityNodeInfo`アクセスを同じホスト関数として提供します。固定された
`POSTDATE_BUDGET_MS`の待機
([`ResidentServerTest.kt:768`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt))
は、クレート側のイベント駆動の待機判定に置き換わります。この置き換えは
[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)
がすでに定めているものと同じです。

### 進捗の配信、キャンセル、クラッシュ復旧

進捗は上述のストリーミング接続を通じてCLIとWeb UIへ届きます。本項目は新しい通信路を
持ち込みません。キャンセルには、`POST /scenario/cancel`という対になるエンドポイントを
追加します。このコードベースのあらゆる通信路がすでに使っているのと同じ、run単位の認証
トークンで保護し、iOS側では上述のとおり直列キューの外で処理することで、実行中のシナリオが
その届き先を塞がないようにします。クレートのステップループはステップの境界ごとにこれを
ポーリングします。この反応の粒度は
[BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel-ja.md)が
すでに前提としているものと同じです。したがって`SIGTERM`やWeb UIの停止ボタンは、今日と
同じく`RunResult(ok=False, failure="cancelled")`を返します。バックエンドのクラッシュ復旧は
変わりません。ランナープロセスがシナリオの途中で落ちても、ホストには接続断として伝わり、
既存の`BackendCrashError`の経路
([BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios-ja.md)、
BE-0407の`recovery.py`)がそのまま扱います。

## 検討した代替案

- **BE-0408からBE-0410がすでに提案しているとおり、SwiftとKotlinへ個別に移植する。** 最初の
  実装コストが小さいことは事実です。Rustのツールチェインや、どちらかのプラットフォームで
  WASMランタイムを最初に立ち上げる必要がありません。本項目がこの案を採らないのは、その場で
  避けたコストが、セレクタ解決の意味論に対する将来のあらゆる変更にかかる、恒常的な税として
  戻ってくるためです。しかもその税は2つの言語で払われ、ホスト実装だけでも適合スイートだけ
  でも、テストを実行する前には捕まえられません。本項目は、その恒常的なコストを消すために、
  一度きりのツールチェイン導入コストを払います。
- **新しいRustコアを書く代わりに、CPythonそのものをWASM化する(Pyodideなど)。** 却下します。
  CPythonのWASMランタイムは数十メガバイトの規模になり、XCTestランナープロセスやAndroidの
  instrumentationサーバーに埋め込むには重すぎます。CPythonのグローバルインタプリタロックと
  スレッドモデルも、`APIHandler`がすでに持つメインスレッドへの直列化(BE-0323)と相性が
  悪いです。
- **コンパイル済みのバイナリを共有する代わりに、1つの宣言的な仕様からSwiftとKotlinの移植を
  生成する。** 却下します。コード生成はWASMランタイムの追加を避けられますが、生成された
  SwiftとKotlinは、実行時には結局2つの独立したネイティブ成果物として存在します。等価性は、
  同じバイナリロジックを動かすことではなく、生成器を信頼することの上に立ちます。生成器
  自体も、本項目がWASMランタイムの組み込みに払うのとおおむね同じコストで保守すべき3つ目の
  成果物になります。
- **PlaywrightやFakeDriverも含め、あらゆるバックエンドの`run_scenario`を共有コアへ置き換える。**
  本項目のスコープとしては却下します。あらゆるバックエンドの実行経路を統一すれば、本項目が
  問おうとしていること、つまりオンデバイスの往復削減が実機で成立するかどうかの答えが出る前に、
  既存の全バックエンドのテスト資産を巻き込むことになります。この案は、本項目のiOS・Androidの
  結果が出てから、置き換えの範囲を広げる形で後の項目に委ねられます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 他のどの作業単位よりも先に、iOS(wasmtimeのSwiftバインディング対WasmKit)とAndroid
  (Chicoryなど純JVM実装)のWASMランタイムの実現可能性を確かめます。
- [ ] シナリオ展開(`expand_components`・`expand_data`・`apply_setups`)を、新設する
  `rust/bajutsu_core/`クレートへ移植し、既存のPythonのフィクスチャに対するゴールデン
  ファイル比較で検証します。
- [ ] セレクタ解決(`find_all`・`resolve_unique`)をクレートへ移植し、ドライバ適合スイート
  ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md))の
  フィクスチャに対して検証します。
- [ ] ステップループ、アラートガードの1回リトライ判定、証跡ルールの発火判定をクレートへ
  移植し、Rust側のフェイクドライバに対して検証します。
- [ ] PyO3バインディングと新しい`Capability`をビルドし、デバイスの報告したステップ列を
  ホスト側のローカル展開と突き合わせる確認を含めて、この`Capability`を宣言しないドライバの
  挙動を変えないまま、ホスト側の判定再計算の経路を`run_scenario`へ配線します。
- [ ] WASMターゲットをビルドし、iOSの`APIHandler`の新しい`POST /scenario`エンドポイントへ
  配線して、1つの単純なシナリオをエンドツーエンドで動かします。
- [ ] 改行区切りJSONによる進捗ストリーミングを、CLIのライブ出力と`serve`のログバスへ配線
  します。
- [ ] `POST /scenario/cancel`を、直列キューの外で処理する形で追加し(実行中のシナリオが
  経路を塞がないようにします)、
  [BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel-ja.md)
  の既存のキャンセル挙動に対して検証します。
- [ ] Androidのresident UI Automatorサーバーに対して、同じWASM統合を繰り返します。
- [ ] `controls.yaml`(BE-0407・BE-0409・BE-0410が使う物差しと同じもの)を両プラットフォーム
  の新しい経路で計測し、BE-0409・BE-0410自身が掲げる1タップあたりの目標(iOS 200〜350ミリ秒、
  Android 150〜300ミリ秒)に対する結果を記録します。
- [ ] 計測結果から、本項目の方式とBE-0408からBE-0410の方式のどちらを採用するかを決め、
  両方の項目の`Status`をそれに応じて更新します。
- [ ] `roadmap-id`ワークフローが本項目の実際の`BE-NNNN`を`main`上で採番したら、BE-0407、
  BE-0408、BE-0409、BE-0410との間で`関連`の相互リンクを補います。

## 参考

[BE-0407 — 証跡取得の重複排除とドライバ内部の調整によるステップ遅延の削減](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)、
[BE-0408 — 端末側ステップ実行プロトコルの追加](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)、
[BE-0409 — iOS端末側ステップ実行機](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)、
[BE-0410 — Android端末側ステップ実行機](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)、
[BE-0114 — ドライバ適合スイート](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、
[`docs/specs/rust-wasm-on-device-executor.md`](../../docs/specs/rust-wasm-on-device-executor.md) —
本項目が要約している設計の詳しい書き起こし、
[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py)、
[`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py)
