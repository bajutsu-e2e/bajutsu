[English](BE-0427-rust-wasm-on-device-executor.md) · **日本語**

# BE-0427 — 共有Rust/WASMコアによるシナリオのオンデバイス実行

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0427](BE-0427-rust-wasm-on-device-executor-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0427") |
| トピック | Platform support |
| 関連 | [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)、[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)、[BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)、[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)、[BE-0365](../BE-0365-in-app-control-channel/BE-0365-in-app-control-channel-ja.md)、[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)は、端末側のステップ実行プロトコルを定めています。このプロトコルは、[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)単独では届かなかった、250〜500ミリ秒という1ステップあたりの目標へ至る道筋です。`wait`や画面に閉じた`assert`は、条件を1回ポーリングするたびにホスト・デバイス間の往復を1回払います。そこでBE-0408は、セレクタ解決と条件評価をデバイス側へ移します。実装は、SwiftとKotlinへの2つの独立した移植です。iOS側の実行機は[BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)、Android側の実行機は[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)が、それぞれ提案しています。

本項目は、同じ目標に対する別の実装戦略を提案します。判定ロジックをRustで一度だけ書きます。それを、2つのターゲットへコンパイルします。ホストが呼ぶネイティブなPython拡張(PyO3経由)と、2つの端末ランタイムが埋め込むWebAssembly(WASM)です。SwiftとKotlinという、同じ規則を持つ2つの独立した再実装を保守し続ける代わりの選択です。

本項目は、BE-0408が扱う範囲も広げます。共有コアは、シナリオの制御フロー(`if`、`for_each`、アラートガードのリトライ)もデバイス側で解釈します。個々のステップだけではありません。その結果、シナリオ全体がステップごとの往復なしに実行されます。

## 動機

[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)は、1回の`tap`ステップが、目標の250〜500ミリ秒に対して、iOSで0.95〜1.07秒、Androidで3.25〜3.32秒かかることを計測しました。支配的なコストは、ホスト・デバイス間の往復です。ホストは50ミリ秒間隔で条件をポーリングし、そのたびにHTTPラウンドトリップを1回払います。BE-0408は、セレクタ解決とwait・assertの評価をデバイス側へ移すことで、これに対処します。実装はSwiftとKotlinへそれぞれ独立に移植する形です。

この独立移植には、BE-0408自身が名指ししている決定性のリスクが伴います。次の3つの独立した実装が、同じセレクタに対して常に同じ判定を返し続けなければなりません。

- ホスト側のPython実装(`find_all`・`resolve_unique`、[`bajutsu/common/drivers/base/_functions.py:152,274`](../../bajutsu/common/drivers/base/_functions.py))
- iOS向けのSwift移植
- Android向けのKotlin移植

BE-0408は、[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)のドライバ適合スイートを拡張してズレを検知する計画です。このスイートは、起きた後でズレを検知します。ズレが起こる可能性そのものは取り除きません。セレクタ解決の規則を将来変更するたびに、変更した人は3つの言語で同じ変更を行う必要があります。BE-0408が対象を2バックエンドに絞っても(仮定の3つ目は対象外です)、この継続コストは消えません。

もう1つのコストは、段階的ロールアウトが最終段階に至るまでに得られる往復削減の幅です。BE-0408は、まず`wait`と`assert`の評価をデバイスへ移します。ステップ列を1回の`POST /scenario`呼び出しにまとめるのは、第4段階に至ってからです。それまでの間、ステップをまたいだ判定は、第4段階に至ってもホストの役目のまま残ります。システムアラートがステップをブロックしたときの1回リトライ判定が、その一例です。BE-0408が制御フローそのものをホスト側の範囲としているためです。

最初からシナリオ全体を、制御フローも含めてランタイムへ渡せば、ホスト・デバイス間の往復は、フェーズの境界と区間証跡の開始・終了の合図だけに縮みます。これはBE-0408の第4段階が目指す到達点と同じであり、中間段階を経ずに至ります。

本項目の中心的な主張は、新しい実機がなくても確かめられます。

1. 本項目が定義するRustクレートを、PyO3向けと`wasm32-unknown-unknown`向けの両方に、同じソースからビルドします。
2. ドライバ適合スイートのフィクスチャ集合に対して、両方のビルドが同じ結果を返すことを確かめます。この確認は、一度きりではなく、継続的インテグレーション(CI)で継続的に行います。

BE-0408の3つの独立実装に置き換わるのは、この継続検査に支えられた単一のクレートです。共有ソースそのものが構造的な保証を与えるわけではありません。

## 詳細設計

### 何を移し、何を移さないか

ホストは、BE-0408がすでに割り当てている次の3つの役目を、そのまま持ち続けます。

- **pass/failの確定**：デバイスは、判定そのものではなく、生の証拠を返します。解決済みの要素の属性や、アサーション対象の観測値です。ホストは、同じ共有コア(PyO3バインディング)を通じて、その証拠から`ok`を再計算します。デバイス自身が計算した`ok`をそのまま信用することはありません。

  本項目はシナリオ展開もデバイス側へ移します。そのため、報告された各ステップの判定を再計算するだけでは足りません。ホストは、同じバインディングでシナリオをローカルにも展開し、報告されたステップ列がその展開結果と一致することを求めます。そうしないと、デバイス側の制御フローの誤り(`if`の分岐違い、`for_each`の反復回数の誤り)に気付けません。デバイスが報告したステップだけを見れば`ok=true`が並び、飛ばされたステップに触れないまま、runが合格してしまいます。
- **レポート用の証跡の受け取り**：`manifest.json`とHTMLレポートは、今日と同じ`StepOutcome`の形から書き出します。
- **区間証跡**：video・deviceLogの開始・終了は、`simctl io recordVideo`のようなホスト側のオペレーティングシステム(OS)のプロセス制御を要します。デバイス単体では起動できません。そのため、各区間証跡の開始・終了をいつ判断するかは、引き続きホストが決めます。

それ以外のすべてを、共有クレートへ移します。

- シナリオの展開(`if`・`for_each`・コンポーネント展開・`vars.*`補完。今日は[`bajutsu/common/scenario/expand.py`](../../bajutsu/common/scenario/expand.py)にあります)
- セレクタ解決
- act→wait→verifyのステップループ([`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py))
- アラートガードの1回リトライ判定
- 証跡ルールの発火判定([`bajutsu/common/orchestrator/evidence_rules.py`](../../bajutsu/common/orchestrator/evidence_rules.py))

BE-0408は、シナリオ展開とステップをまたぐ判定をホスト側に残しています。そのロジックをデバイス側へ独立に移植すると、セレクタの意味論がズレる場所が4つ目として増えるだけだからです。共有された1つのバイナリはこの理由を取り除くため、本項目は同じ境界を引き継ぎません。

### 1つのクレート、2つのビルドターゲット

新設するクレート`rust/bajutsu_core/`は、シナリオ展開、セレクタ解決、ステップループ、証跡ルールの発火判定を実装します。今日のPythonの振る舞いと、規則単位で一致させます。同じソースから2通りにビルドします。

- **PyO3バインディング**：Pythonの拡張モジュールです。デバイスが返す生の証拠からステップの判定をホストが再計算するために、ホストが呼びます。ホストは、このバインディングでPlaywrightやFakeDriverを直接操作することはありません。これらのバックエンドは、既存の`run_scenario`([`bajutsu/common/orchestrator/loop/_functions.py:571`](../../bajutsu/common/orchestrator/loop/_functions.py))を通じて、そのまま動き続けます。
- **`wasm32-unknown-unknown`バイナリ**：iOSのXCTestランナープロセス(`BajutsuKit`)と、Androidのresident UI Automatorサーバー(`BajutsuAndroidUIAutomatorServer`)の両方に埋め込みます。どちらの埋め込みも、OS APIを直接呼びません。各プラットフォームが、クレートの`Driver`トレイトをWASMのホスト関数として提供します。ホスト関数は、タップ、型入力、スワイプ、ツリー読み取り、スクリーンショットです。

同じクレートから2つのターゲットをビルドしても、それだけでは、両者が同じ結果を返す保証になりません。PyO3バインディングはネイティブターゲット向けで、WASMバイナリは`wasm32-unknown-unknown`向けです。ターゲット依存の差が、順序が判定に効く箇所で、両者を分けることがあります。`resolve_unique`の重複畳み込み([`bajutsu/common/drivers/base/_functions.py:274`](../../bajutsu/common/drivers/base/_functions.py))が一例です。差の例は次のとおりです。

- `usize`の幅(64ビット対32ビット)
- 浮動小数点の丸めと文字列化
- `HashMap`の反復順

クレートは、こうしたズレに対して2つの備えを持ちます。

- 順序が判定に効く箇所を、`BTreeMap`のような決定的な型に限ります。
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)のドライバ適合スイートが、両ターゲットに対してCIで継続的に動きます。同じフィクスチャに対して両ターゲットが同一の結果を返すことを、突き合わせます。このフィクスチャは、クレートを`FakeDriver`や他のバックエンドと照らして検証するために、すでに使っているものです。

BE-0408の3つの独立実装が抱えるズレを防ぐのは、共有ソースそのものではなく、この継続的な差分検査です。

### ホスト側の変更

`run_scenario`の入り口に、分岐を1つ加えます。対象の`Driver`は、新しい`Capability`(`bajutsu/common/drivers/base/capability.py`に追加)を宣言できます。宣言している場合、ホストは、既存のステップループを回す代わりに、シナリオ全体を1回のリクエストでランタイムへ送ります。リクエストには`before`・`steps`・`expect`・`after`が入ります。

この`Capability`を宣言していないドライバの挙動は、まったく変わりません。Playwright、FakeDriver、オンデバイス実行機をまだビルドしていないxcuitest・adbターゲットは、今日の`_run_step_body`([`bajutsu/common/orchestrator/loop/_functions.py:302`](../../bajutsu/common/orchestrator/loop/_functions.py))を、そのまま通じて動きます。

ランタイムは、ステップが完了するたびに、同じ接続の上で結果を1件ずつ返します。形式は改行区切りのJSON(JavaScript Object Notation)で、ストリーミングで返します。BE-0409が自身の`POST /scenario`エンドポイントですでに定めているチャンク転送の方式を、そのまま使います。

ホストは、行を1件ずつ読み取り、既存の`StepOutcome`の形へ変換します。そして、今日`StepOutcome`を消費しているレポート書き込み、CLIのライブ出力、`serve`のログバスへ、そのまま渡します。CLIのライブ出力もWeb UIのReplayタブの見え方も変わりません。ホストは、ステップを合格と扱う前に、そのステップの行に含まれる生の証拠から、PyO3バインディング経由で`ok`を再計算します。

### iOSとAndroidのランタイム

両プラットフォームとも、`POST /scenario`エンドポイントを追加します。名前は、BE-0409がすでに提案しているものと同じにします。どちらのコア実装が採用されても、このエンドポイント周辺で積んだ作業を、2つの設計で共有できるようにするためです。

iOSでは、`APIHandler`([`BajutsuKit/Sources/BajutsuRunner/APIHandler.swift`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift))が、クレートのWASMバイナリをWASMランタイムへロードします。このランタイムは、他のXCTest操作がすでに直列化しているのと同じメインスレッド(BE-0323の非再入性)の上で動きます。`APIHandler`は、ホスト関数として、タップ・スナップショット・型入力・スワイプ・スクリーンショットを提供します。これらは[`XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)が裏付けます。

埋め込むSwift向けWASMランタイムは、未決定の実現可能性の問いです。wasmtimeのSwiftバインディングか、純Swift実装のWasmKitかを選びます。本項目の最初の作業単位が、他のiOS向け作業に先立って、この問いに答えます。

`APIHandler`の他のあらゆる呼び出しは、`operations`という直列キューを通ります。`serialized(_:)`が、`DispatchQueue.main.sync`を使って、呼び出しをこのキューへ流します([`APIHandler.swift:51,354-366`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift))。シナリオ全体を実行する`POST /scenario`は、実行中このキューを占有し続けます。そのため、`POST /scenario/cancel`や区間証跡の開始・終了の合図を同じキュー経由で届けると、それぞれが止めるべき当のシナリオが終わるまで届きません。今日、`health`と`setInterruptionPolicy`だけがこのキューを迂回しています。この2つの新しいエンドポイントにも、同じ迂回を明示的に与える必要があります。

Androidのresident UI Automatorサーバーには、Java仮想マシン(JVM)上で動くWASMランタイムを組み込みます。候補の1つはChicoryです。純JVM実装で、instrumentationテストターゲットが別途必要とするネイティブライブラリの署名手順を避けられます。サーバーは、既存の`UiAutomation`セッションと`AccessibilityNodeInfo`アクセスに裏付けられた、同じホスト関数を提供します。

固定された`POSTDATE_BUDGET_MS`の待機([`ResidentServerTest.kt:768`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt))は、クレート側のイベント駆動の待機判定に置き換わります。[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)も、同じ置き換えをすでに定めています。

### 進捗の配信、キャンセル、クラッシュ復旧

進捗は、上述のストリーミング接続を通じて、CLIとWeb UIへ届きます。本項目は新しい通信路を持ち込みません。

キャンセルには、`POST /scenario/cancel`という対になるエンドポイントを追加します。このコードベースのあらゆる通信路がすでに使っているのと同じ、run単位の認証トークンで、このエンドポイントを保護します。iOS側では、上述のとおり、直列キューの外で処理します。実行中のシナリオが、その届き先を塞がないようにするためです。

クレートのステップループは、ステップの境界ごとにキャンセルをポーリングします。この反応の粒度は、[BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel-ja.md)がすでに前提としているものと同じです。したがって、`SIGTERM`やWeb UIの停止ボタンは、今日と同じく`RunResult(ok=False, failure="cancelled")`を返します。

バックエンドのクラッシュ復旧は変わりません。ランナープロセスがシナリオの途中で落ちても、ホストには接続断として伝わります。既存の`BackendCrashError`の経路が、その接続断をすでに扱っています([BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios-ja.md)、BE-0407の`recovery.py`)。

## 検討した代替案

- **BE-0408からBE-0410がすでに提案しているとおり、SwiftとKotlinへ個別に移植する。** 最初の実装コストが小さいことは事実です。Rustのツールチェインや、どちらかのプラットフォームでのWASMランタイムを、最初に立ち上げる必要がありません。本項目がこの案を採らないのは、その場で避けたコストが、恒常的な税として戻ってくるためです。セレクタ解決の意味論に対する将来のあらゆる変更を、SwiftとKotlinの両方へ移植しなければなりません。ホスト実装を読むだけでは、どちらの移植の誤りも見つかりません。見つかるのは、適合スイートを実行したときだけです。本項目は、その恒常的なコストを消すために、一度きりのツールチェイン導入コストを払います。
- **新しいRustコアを書く代わりに、CPythonそのものをWASM化する(Pyodideなど)。** 却下します。CPythonのWASMランタイムは数十メガバイトの規模になり、XCTestランナープロセスやAndroidのinstrumentationサーバーに埋め込むには重すぎます。CPythonのグローバルインタプリタロックとスレッドモデルも、`APIHandler`がすでに持つメインスレッドへの直列化(BE-0323)と衝突します。
- **コンパイル済みのバイナリを共有する代わりに、1つの宣言的な仕様からSwiftとKotlinの移植を生成する。** 却下します。コード生成はWASMランタイムの追加を避けられますが、生成されたSwiftとKotlinは、実行時には2つの独立したネイティブ成果物のままです。等価性は、同じバイナリロジックを動かすことではなく、生成器を信頼することの上に立ちます。生成器自体も、保守すべき3つ目の成果物になります。そのコストは、本項目がWASMランタイムの組み込みに払うコストと、おおむね同じです。
- **PlaywrightやFakeDriverも含め、あらゆるバックエンドの`run_scenario`を共有コアへ置き換える。** 本項目のスコープとしては却下します。あらゆるバックエンドの実行経路を統一すると、既存の全バックエンドのテスト資産を巻き込みます。しかも、本項目自身の問いに実機で答えが出る前にです。その問いとは、オンデバイスの往復削減が成立するかどうかです。本項目のiOS・Androidの結果が出てから、後の項目が置き換えの範囲を広げられます。

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
[`docs/specs/rust-wasm-on-device-executor.md`](../../docs/specs/rust-wasm-on-device-executor.md)：
本項目が要約している設計の詳しい書き起こし、
[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py)、
[`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py)
