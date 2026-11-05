[English](BE-0270-android-adb-driver-conformance.md) · **日本語**

# BE-0270 — adb backend の実機 driver conformance

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0270](BE-0270-android-adb-driver-conformance-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0270") |
| 実装 PR | [#1119](https://github.com/bajutsu-e2e/bajutsu/pull/1119) |
| トピック | Driver & backend architecture |
<!-- /BE-METADATA -->

## はじめに

driver conformance suite（[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）は、backend 非依存の一つの契約で、次の 4 つの条項から成ります。

- 曖昧なセレクタは、最初の一致に作用せず失敗します。
- 0 件のセレクタは、成功を報告せず失敗します。
- `capabilities()` は、観測される挙動と一致します。
- `wait_for` は単発の検査であり、共有の `wait_until` ループがそれを条件待ちに変えます。

契約は `tests/driver_conformance.py` にあり、現在は次の 3 つのドライバに対して走っています。

- 高速な Linux ゲート上の `FakeDriver`。
- web CI 上の Playwright（`tests/test_driver_conformance_web.py`）。
- 実機上の iOS の 2 つの backend、idb と XCUITest（`tests/test_driver_conformance_ondevice.py`。`ios-e2e.yml` の `conformance (idb + xcuitest)` ジョブが駆動します）。

出荷済みのドライバのうち、この契約が一度も走っていないのが adb backend（Android、BE-0007）です。Android の実機レーンは、機能スモーク、element ツリー golden、ピクセル単位の visual regression test（VRT）を実行します。ところが、idb、XCUITest、Playwright がそれぞれ証明されているのと同じ意味で、adb ドライバが実機上で決定性コアの不変条件を守ることを検査するジョブはありません。この項目は、その欠落を埋めます。

## 動機

- **契約が保証するのは、実際に走らせた backend の分だけです。** BE-0114 の眼目は、決定性コアの不変条件がすべての実アクチュエータで*同一に*成り立つことであり、`FakeDriver` の上だけではありません。契約を一度も走らせていない backend は、乖離の潜む場所になります。たとえば、曖昧な adb セレクタが失敗せず最初の一致にタップする（プライムディレクティブ #2 への違反）としても、既存の Android のジョブはすべて通ってしまいます。`smoke`、`golden`、`visual` は、構成上どれも曖昧でないセレクタを使うからです。
- **adb は、ほかの backend とはセレクタの解決の仕方が異なります。** adb ドライバは、2 つのチャネル（常駐 UI Automator サーバ（BE-0245）と `uiautomator dump` フォールバック）で読み取ります。また、2 つの形式（ドット区切りとアンダースコア、BE-0221）で id を照合します。これらは、iOS や web の conformance ジョブでは覆えない adb 固有の経路です。契約の曖昧なセレクタと 0 件のケースは、この経路を最も固定すべき箇所です。
- **対称性は、明示的に見送られたのであって、退けられたのではありません。** 進行中のプラットフォーム E2E 対称化の項目（`e2e-workflow-structural-parity` 提案）は、Android レーンを観点ごとのジョブに分割します。この項目は、`conformance (adb)` ジョブの欠落を実在のカバレッジの穴として記し、ワークフローの再構成ではなくテスト作成の作業なのでスコープ外としています。本項目がそのフォローアップです。

## 詳細設計

本項目はプロダクトコード（アプリ側の conformance モードとテストハーネス）を出荷するため、プライムディレクティブの下で進めます。

- スイートは決定的で、判定の近くに LLM は一切ありません。
- 固定の `sleep` ではなく、条件待ちで画面を再シードします。
- アプリ側のフックがあっても、showcase がすでに使っているのと同じ一律のオプトインの背後に置きます。ツール側にアプリごとの分岐は入れません。

作業は、重なりのない 5 つの単位に分かれます。

1. **showcase アプリへの Android conformance モードの追加。** iOS の `ConformanceView` に対応するものです。spec チャネルが指定する識別子集合を、ちょうどそのとおりに描画します。各 id は要求された多重度で描画するので、曖昧な「2 つの `dup`」のケースが本物になります。加えて、常在の readiness マーカーを置きます。readiness が、空のツリーからの推測ではなく肯定的な検査になります。モードは、レーンがすでにテストしている両方の Android UI ツールキット、Compose と Views（BE-0221）を覆う必要があります。両者は id の形式が異なるからです。あるいは Compose に限ると明示し、その理由を記録します。
2. **adb 経由の再シードチャネル。** iOS がアプリの Documents ディレクトリに `conformance-spec.txt` を書き込むことに対応するものです。adb での自然な形は、spec の `adb push`（またはインテントのエクストラ）を、アプリの files ディレクトリへ送る方法です。conformance 画面がそれをポーリングします。`with_screen` は spec を書き込んでから、ドライバ自身の `query()` が新しい画面を反映するまで待ちます。待機は条件待ちで、sleep は使いません。push とインテントのどちらにするかは、エミュレータが最も確実に扱えるほうを実装時に決めます。
3. **adb conformance ハーネスと pytest モジュール。** 同じ `tests/driver_conformance.py` の契約を走らせます。新しい `test_driver_conformance_ondevice_android.py` にするか、既存の実機モジュールをパラメータ化するかのいずれかです。モジュールには `ondevice` マーカー（ゲートの既定 `-m 'not web and not ondevice'` で除外されます）を付けます。シリアルの環境変数（たとえば `BAJUTSU_CONFORMANCE_SERIAL`）が未設定のときは、モジュールレベルで skip します。高速な Linux ゲートはこのモジュールを拾いません。idb/XCUITest と web のモジュールと同じ形です。
4. **`android-e2e.yml` への `conformance (adb)` ジョブの追加**（Linux と KVM）。ハーネスを、起動済みの Android Virtual Device（AVD）に配線します。conformance に対応した APK をビルドし、エミュレータを起動し、その 1 台に対してスイートを直列で走らせます。Android レーンの残りと同じく非必須で、対称化の項目が提案する観点ごとのジョブ構成（`smoke`、`golden`、`visual`、そして今回の `conformance`）に加わります。
5. **capability 一致テストのための正直な `capabilities()`。** 契約は `capabilities()` を約束として扱います。`MULTI_TOUCH` や `SELECT_OPTION` の backend は実際にその操作を行います。その capability を持たない backend は、黙って何もしない代わりに `UnsupportedAction` を送出しなければなりません。これらのケースが通るには、adb ドライバが宣言する capability が実挙動と一致している必要があります。その宣言を確認し、必要なら修正することも、この作業の一部です。

## 検討した代替案

- **conformance ジョブではなく、既存の Android smoke シナリオの中で不変条件を検査する案。** 見送りました。smoke シナリオは曖昧でないセレクタで実アプリを操作するので、フィクスチャを無理に歪めない限り、曖昧なセレクタや 0 件のケースを動かせません。conformance の契約には、任意の識別子集合へ再シードできるアプリ画面が必要です。これは専用の conformance モードであり、iOS のスイートがすでに使っています。
- **element ツリー golden を conformance のシグナルとして再利用する案。** 見送りました。golden が固定するのは*ツリーの形*であって、*セレクタ解決の意味論*ではありません。ドライバが golden に一致していても、曖昧な 2 つの一致のうち最初のものをタップしうるからです。それを捕らえるのは、契約自身のアサーションだけです。
- **Compose だけを覆い、Views ツールキットは省く案。** 2 つのツールキットがドライバの経路を共有することを踏まえれば、妥当な絞り込みです。ただし、アンダースコアの id 形式（BE-0221）が conformance で検査されないまま残ります。実装時の判断（単位 1）とし、黙って落とすのではなく、いずれの結論でも記録することにしました。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] showcase アプリに Android conformance モードを追加する（Compose。Views は単位 1 の判断に従う）。
- [x] adb の再シードチャネルを追加する（spec の push/インテント + 条件待ちの `with_screen`）。
- [x] adb conformance ハーネスと、共有契約を走らせる `ondevice` マーカー付き pytest モジュールを追加する。
- [x] `android-e2e.yml` に `conformance (adb)` ジョブを追加する。
- [x] capability テストのため、adb ドライバの `capabilities()` が実挙動と一致することを確認する。

**単位 1 の判断（Compose のみ、Views は対象外。「検討した代替案」に沿って記録します）。** 契約が seed するのは素の識別子（`dup`、`ok`、`a`、`b`、`Log`、`g`、`sel`、`s`）です。2 つのツールキットの id 形式（BE-0221）が違ってくる `.` や `_` を、どれも含みません。したがって Views の画面でも、同じ id を同じ `_strip_pkg` の経路で解決するだけで、conformance のカバレッジは増えません。

さらに、spec 駆動で任意の id を描く画面を自然に表現できるのは Compose だけです。Compose の `testTag` は実行時の任意の文字列を受け取り、`testTagsAsResourceId` で `resource-id` として現れます。Views の `resource-id` は、コンパイル時の `R` エントリでなければなりません。契約の画面をそもそも描けるのは Compose の側です。

**単位 2 の判断：ファイル push ではなくインテント。** 再シードは、新しい `SHOWCASE_CONFORMANCE` の intent extra を載せて `singleTask` の Activity を起動し直し、`onNewIntent`（deeplink で実証済みの経路）で届けます。iOS の spec ファイル push は使いません。`adb push` はアプリのサンドボックスに届かないからです。インテントなら、`launchEnv` から intent extras への規約（BE-0007）にそのまま乗り、アプリ側の新しいポーリングも要りません。

空（0 件）の画面は、先頭に `,` のセンチネルを付けて表し、値が空文字列にならないようにします。adb の `am start --es KEY ""` は、値を落とすからです（末尾の空引数の癖で、clipboard チャネルも同じ癖を記しています）。

**単位 5 の所見：`capabilities()` の修正は不要。** adb ドライバが宣言する集合（`QUERY`、`ELEMENTS`、`MULTI_TOUCH`。`SELECT_OPTION` は無し）は、root 化した `google_apis` エミュレータ上で capability テストの実挙動とすでに一致します。`pinch` と `rotate` は、root 化した `sendevent` の掃引（BE-0232）で `UnsupportedAction` を出さずに作用し、`select_option` は `UnsupportedAction` を送出します。`conformance (adb)` ジョブは、smoke レーンの `gestures_multitouch` と同じく `adb root` を実行して、`MULTI_TOUCH` の前提を満たします。
### ログ

- [#1119](https://github.com/bajutsu-e2e/bajutsu/pull/1119) で実装：Compose の
  `ConformanceScreen` と `AppModel.conformanceIds`/`applyConformance`、`MainActivity`/`RootScreen`
  の配線、共有契約を走らせる `tests/test_driver_conformance_ondevice_android.py`（`am start` による
  再シードチャネル）、`android-e2e.yml` の `conformance (adb)` ジョブと `e2e-conformance` の
  Makefile ターゲット。

## 参考

- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)：この項目が adb backend へ拡張する、backend 非依存の conformance 契約。
- [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)：検査対象の adb backend。
- [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee-ja.md)：conformance 画面が覆うべきドット区切り／アンダースコアの id 形式。
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)：adb ドライバがセレクタを解決する、常駐チャネルと `uiautomator dump` の 2 経路。
- `e2e-workflow-structural-parity` 提案（BE id は自身のマージ時に採番）：Android レーンを観点ごとのジョブに分割することを提案し、この `conformance (adb)` の欠落をそこではスコープ外として記している項目。
- `tests/driver_conformance.py`、`tests/test_driver_conformance_ondevice.py`、`tests/test_driver_conformance_web.py`、[`android-e2e.yml`](../../.github/workflows/android-e2e.yml)。
