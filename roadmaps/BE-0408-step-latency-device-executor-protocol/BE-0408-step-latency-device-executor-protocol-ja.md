[English](BE-0408-step-latency-device-executor-protocol.md) · **日本語**

# BE-0408 — 端末側ステップ実行ループの土台となる、端末側ステップ実行プロトコルの追加

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0408](BE-0408-step-latency-device-executor-protocol-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0408") |
| 実装 PR | [#1949](https://github.com/bajutsu-e2e/bajutsu/pull/1949) |
| トピック | Platform support |
| 関連 | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)、[BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)、[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、条件の評価を端末側へ移すためのプロトコルとセレクタの意味論を定義します。何を端末側へ移し、何をホスト側に残すかを決めます。セレクタがホストと端末の両方で同じ要素に解決されることをどう保証するかも決めます。2つのプラットフォーム別の項目が、この定義を土台にします。1つは XCTest ランナー内の iOS 側実行機である [BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)、もう1つは常駐 instrumentation サーバー内の Android 側実行機である [BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md) です。どちらのプラットフォーム実装も、この項目のスコープには含みません。

きっかけは、ドライバ内部の調整を提案する [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md) の配下に記録したパフォーマンス調査です。調査では、実際の `tap` ステップを計測しました。Bajutsu は、証拠取得まで含めた end-to-end のステップ実行時間に 250〜500 ミリ秒という目標を掲げています。BE-0407 の削減を適用しても、iOS は1ステップあたりおよそ 0.3〜0.6 秒、Android はおよそ 0.6〜1.2 秒にとどまり、目標に届きません。どのステップも、ポーリングする条件ごとに、ホストと端末の間の往復を少なくとも1回払っているからです。残りの差を埋めるには、条件をホストからポーリングするのではなく、端末側で評価する必要があります。

## 動機

`wait for` ステップは、セレクタが一致するまで 50 ミリ秒ごとにホストからポーリングします。`wait until: settled` ステップも同様にポーリングします。ポーリング1回は、ホストと端末の間の完全な往復です。iOS では XCTest ランナーへの新規 HTTP リクエスト、Android では常駐 instrumentation サーバーへのリクエストになります。

ドライバ内部をどれだけ調整しても、このポーリングループは消えません。待っている条件の評価には端末の読み取りが必要で、今日その読み取りを行うのはホストだけだからです。画面に閉じた `assert`（`exists`、`label`、`value`、`count`、`enabled`）にも同じ限界があります。評価に必要な端末の読み取りを、ホストが握っているためです。

この評価を端末側へ移せば、ポーリングループそのものがなくなります。条件を評価するステップは、多数の往復ではなく1回の往復で済みます。ホストが条件を送り、満たされた時点で結果を1回受け取ります。

この移行は、prime directive 1（「AI は作成者であり、失敗の調査者であっても、判定者にはならない」）を緩めるものではありません。この原則が禁じるのは、AI が pass/fail を決めることです。ホストプロセス自身が条件を評価することまでは求めていません。

端末側の実行機は、セレクタを決定的に解決し、wait や `assert` を決定的に評価します。今日ホストが同じ読み取りを行う場合とまったく同じ意味で、決定的です。アクセシビリティツリーをたどるコードは、HTTP 経由でなく端末上で動いても、決定的なままです。

このプロトコル設計が達成すべき、後から確かめられる成果は次のとおりです。プラットフォーム実装が着地し、このプロトコルを実際に使ったとき、`wait for` ステップの往復回数が、50 ミリ秒ごとのポーリング1回につき1往復から、ステップ1回につき1往復に減ります。[BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md) も、要素読み取りの複数往復を単一のスナップショットにまとめて、同じ種類の成果を出しました。

## 詳細設計

**実装の順番。** この項目は、関連する4項目のうち2番目で、次の厳密な順に進みます。

1. ドライバ内部調整の項目（[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)）。
2. この項目。
3. iOS 実行機（[BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)）。
4. Android 実行機（[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)）。

**この項目への着手は、BE-0407 が完了するまで行ってはいけません。** この項目の設計は、BE-0407 が出荷し実測した基準値を土台にします。まだ動いている基準値に対してプロトコルを設計すると、後で変わる数値を前提にしてしまうおそれがあります。同じ理由で、BE-0409 もこの項目が完了するまで着手してはいけません。まだ動いているプロトコル設計に対して構築した実行機は、プロトコルが変わるたびに作り直しになります。

### 何を移し、何を移さないか

Python は、プロトコルを変更した後も、次の3つの役割をホスト側に残します。

- シナリオを、端末側の実行機が実行できるステップの列に展開すること。シナリオの形式とその展開規則は、ホスト側の所有物のままです。
- 端末が返す証拠を受け取り、`manifest.json` と HTML レポートに書き込むこと。証拠とは、解決済みの要素ツリー、座標、スクリーンショット、条件が満たされた時刻です。書き込み方は今日と変わりません。
- pass/fail を確定すること。端末側の評価結果は入力です。ホストは、すでに適用している同じ決定的な規則に照らして結果を再確認します。最終的な verdict を下すのは、端末ではなくホストです。

次のものは端末側へ移します。

- セレクタの解決。
- `wait for`、`until: gone`、`until: settled`。
- 画面に閉じた `assert`（`exists`、`label`、`value`、`count`、`enabled`）。
- 4種類のアクチュエーション（`tap`、`type`、`swipe`、`scroll`）。

`http`、`email`、`generate`、`visual`、`golden`、`request` の `assert` は、ホスト側に残します。これらはいずれも、検査対象の端末そのものを読み取らないためです。

### 共有される契約としてのセレクタ意味論

今日、セレクタの意味を定める唯一の定義は、[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py) の `find_all` と `resolve_unique` です。この定義は、`within`、`idMatches`、`labelMatches`、trait の派生を含みます。Android では、derived-label フォールバック（[`drivers/adb/_functions.py`](../../bajutsu/common/drivers/adb/_functions.py)）も含みます。

端末側の実行機は、この論理を Swift と Kotlin にそれぞれ独自に持つ必要があります。どちらの写しも、どのセレクタについても、ホスト側の写しと同じ要素に解決しなければなりません。曖昧な一致で失敗する場合も、同じ失敗の仕方をする必要があります。セレクタの一致は、prime directive 1 が求める決定的な判定です。端末側とホスト側の解決が食い違えば、性能の後退ではなく決定性の後退になります。

これを確かめる既存の手段は、[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) の driver conformance suite です。端末側のリゾルバができた時点で、同じセレクタのフィクスチャ集合をそれに対しても実行できます。そうすれば、プラットフォーム実装は同値性を主張するのではなく証明できます。

### プロトコルの段階的な導入

以下の各段階は、それぞれ独立して有用で、1種類の往復を取り除きます。プラットフォーム実装は、段階を1度の大きなプロトコル変更としてではなく、順に着地させられます。

1. **`wait for` と `until: gone` を最初に移す。** `POST /wait` リクエストにセレクタとタイムアウトを乗せます。端末側が内部でポーリングし、条件が満たされるかタイムアウトした時点で応答します。この段階だけで、wait ステップが今日払っている 50 ミリ秒ごとのホスト側ポーリングがすべてなくなります。
2. **次に `until: settled` を移す。** iOS は画面遷移シグナル（[BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)）を、ホストの中継なしに直接受け取ります。Android は、常駐サーバーがすでに監視しているアクセシビリティイベントのストリームから、自分で settle を評価します。
3. **次に、画面に閉じた `assert` の種類を移す。** 先にホストへ返した木ではなく、端末自身が読み取った木に対して評価します。
4. **ステップを1回の `POST /scenario` にまとめる。** 個々のステップ種別が端末側で解決できるようになれば、シナリオの一区間を列として送れます。区間は、ステップごとにホストへ往復することなく実行されます。ここに至って、ステップ1回のコストは往復1回以下になり、この項目の動機と一致します。

### この項目のスコープに含まれないもの

この項目が定義するのは、プロトコルとセレクタ意味論の契約だけです。プロトコルを実装するランタイムは2つあります。XCTest ランナープロセス内の iOS 側実行機と、常駐 instrumentation サーバー内の Android 側実行機です。どちらも、それぞれ別の、より大規模な提案とします。どちらも、自分のプラットフォームに固有の端末側設計を必要とするためです。具体的には、イベント注入、アクセシビリティツリーへのアクセス、スクリーンショット取得です。

## 検討した代替案

- **プロセス境界にある実行機ではなく、アプリ内実行機（BajutsuKit や Android の on-device SDK の内部）を構築する。** 却下します。理由は2つあり、互いに独立しています。
  - アプリ内の実行機は、合成タッチやキーボードイベントを注入できません。frame を伴うアクセシビリティツリーを読めず、アプリプロセスの内側からスクリーンショットも撮れません。これらができるのは XCTest ランナープロセスと Android の instrumentation だけです。権限ダイアログ、システムキーボード、`SFSafariViewController` のコンテンツといったシステム側の画面も見えません。アプリのクラッシュも観測できません。どちらも、アプリの外側にあるプロセス境界を必要とします。アプリ内 SDK の役割は今日のまま、画面遷移シグナルのような観測の補助にとどめ、アクチュエータにはしません。
  - 2つ目の理由は、アプリ非依存の原則（prime directive 3）です。アプリ内実行機は検査対象のすべてのアプリへの組み込みを必要とし、組み込んでいないアプリは端末側実行を丸ごと失います。プロセス境界にある実行機であれば、既存の XCTest ランナーや常駐 instrumentation サーバーと同じように、検査対象のアプリが何であっても駆動できます。
- **プロトコルの設計を省き、各プラットフォームに独自の通信形式を委ねる。** 却下します。両プラットフォームのセレクタ意味論と、wait と assert の種類は、`Driver` プロトコルの層ですでに統一されています。この統一を端末側実行機の層で崩せば、この提案が避けようとしているプラットフォームごとの特別扱いを、再び持ち込むことになります。
- **この提案を切り出さず、BE-0409 と BE-0410 のそれぞれに折り込む。** 却下します。セレクタ意味論の契約と段階的な導入順序は、両プラットフォームに等しく適用されます。ここで1度だけ述べ、両プラットフォームの項目から参照する形にすれば、同じ設計の2つの写しが食い違っていく事態を避けられます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

**順番の状態：BE-0407 が完了しました**（[#1944](https://github.com/bajutsu-e2e/bajutsu/pull/1944)）。これにより、この項目のブロックが解除されました（詳細設計の「実装の順番」を参照）。下のチェックリストは、すべてチェック済みです。

- [x] 段階 1（`POST /wait`）の通信形式を書き、どちらのプラットフォーム項目も
  着手する前に、両者の間で合意する。文章だけでなく、1 本の OpenAPI 3.1 文書
  （[`protocol/device-executor.openapi.yaml`](protocol/device-executor.openapi.yaml)、
  設計の経緯は [`protocol/README.md`](protocol/README.md) に記載）として出荷しました。
  `jsonschema` による検証テスト（`tests/test_be0408_protocol_fixtures.py`）で固定して
  あります。将来 BE-0409 や BE-0410 のどちらかがこの共有スキーマから外れれば、高速
  ゲートでそのまま失敗します。
- [x] `find_all` / `resolve_unique` のセレクタ意味論を、Swift と Kotlin という独立
  した 2 つの実装が境界事例について合意できるだけの精度で、共有の設計文書に移す。
  境界事例とは、曖昧な一致や trait の派生、Android の derived-label フォールバック
  などです。[`docs/ja/selectors.md`](../../docs/ja/selectors.md) に「別言語への
  移植契約」という新しい節として出荷しました。英語版は
  [`docs/selectors.md`](../../docs/selectors.md) です。ここに挙げた境界事例すべてに
  加え、移植先が意図的に揃えてはならない Swift 側の挙動も 2 つ明記しています。
  `PositionPath` の frame の許容誤差と、トレイトの順序つき比較です。
- [x] driver conformance suite
  （[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）
  を、端末側のリゾルバができた時点でそれに対して実行できるフィクスチャ集合で拡張する。
  出荷先は BE-0114 の `DriverConformanceContract` の拡張ではありません。その隣に置く
  独立したフィクスチャ集合、
  [`tests/fixtures/be0408/`](../../tests/fixtures/be0408/) です。この契約は生きた
  `Driver` を要求します。端末側のリゾルバはまだ存在しません。セレクタ解決はすでに
  バックエンド非依存です。全バックエンドが単一の `resolve_unique` を共有しています。
  バックエンドごとに同じフィクスチャを回しても、得られる情報は増えません。
  `tests/test_selector_fixtures.py` が今日、全ケースを実装本体へ照合しています。
  フィクスチャ集合自体が固定したい対象からずれることはありません。
- [x] 段階 2〜4（`settled`、画面に閉じた `assert`、`POST /scenario`）の通信形式を、
  段階 1 とプラットフォーム項目の実装から得られる知見をもとに定義する。実際の定義の
  もとにしたのは、この文書自身の段階 1 の設計と、BE-0409・BE-0410 それぞれの提案
  文書です。実装からの知見ではありません。BE-0409 はこの項目が完了するまで着手でき
  ません。実装からの知見自体が、まだ存在しないためです。出荷先は段階 1 と同じ
  OpenAPI 文書です。`POST /assert` と `POST /scenario` として出荷しました。`settled`
  は `POST /wait` の 4 つ目の mode に畳み込みました。実装からの学びをこの文書へどう
  反映するかは、`protocol/README.md` の「改訂履歴」に記しています。一度書いたきりで
  固定するものではありません。
- [x] `roadmap-id` ワークフローが `main` 上で 4 項目の ID を採番したら、BE-0407、
  BE-0409、BE-0410 との間で `関連` の相互リンクを補う（BE-0407 にある同じチェック
  項目を参照）。

ログ：

- [#1949](https://github.com/bajutsu-e2e/bajutsu/pull/1949) は、5つの単位すべてを完了しました。出荷した内容は次のとおりです。
  - 言語中立なセレクタ解決フィクスチャ（`tests/fixtures/be0408/`）と、`find_all` / `resolve_unique` / `parse_hierarchy` への再生テスト。
  - `docs/selectors.md` の「別言語（Swift・Kotlin）への移植契約」節。
  - ステージ1〜4を覆う、共有 OpenAPI 3.1 文書の草案（`protocol/device-executor.openapi.yaml` と `protocol/README.md`）。
  - この文書自身の適合性フィクスチャとテスト（`tests/fixtures/be0408/protocol/`、`tests/test_be0408_protocol_fixtures.py`）。

  プロトコル文書は、Android を iOS と同じ JSON（JavaScript Object Notation）契約に含めました。現時点では org.json を使い、BE-0410 が評価すべき `openapi-generator` の Kotlin モデル生成を推奨として記録しました。単一呼び出しでのキャンセルの代わりに、`pollBudgetMs` によるポーリング設計を採用しました。

  この PR は、陳腐化していた `bajutsu/common/drivers/base.py` / `drivers/adb.py` への参照（BE-0411 でクラスごとにパッケージ化済み）を、全体にわたって修正しました。自己矛盾していたチェックリスト項目も修正しました。ゲートを走らせる前に、CI のレビュー契約に対して3ラウンドの自己レビューを行い、25件の指摘を見つけて修正しました。大半は草案 OpenAPI スキーマ自身の構造的な厳密さに関するものでした。null を受け付けてしまうフィールド、強制されていなかった「いずれか1つだけ」規則、検証されていなかった束ねペイロードなどです。

## 参考

[BE-0105 — XCUITest の要素取得を単一スナップショット化する](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md)、
[BE-0114 — backend 非依存の挙動を検査する driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、
[BE-0310 — アクセシビリティの画面遷移通知による readiness 判定の精度向上](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)、
[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py)、
[`bajutsu/common/drivers/adb/_functions.py`](../../bajutsu/common/drivers/adb/_functions.py)、
[`protocol/device-executor.openapi.yaml`](protocol/device-executor.openapi.yaml)、
[`docs/ja/selectors.md`](../../docs/ja/selectors.md)
