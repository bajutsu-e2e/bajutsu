[English](BE-XXXX-android-adb-driver-conformance.md) · **日本語**

# BE-XXXX — adb backend の実機 driver conformance

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-android-adb-driver-conformance-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Driver & backend architecture |
<!-- /BE-METADATA -->

## はじめに

driver conformance suite（[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）は、backend 非依存の一つの契約です。曖昧なセレクタは最初の一致に作用せず失敗する、0 件のセレクタは成功を報告せず失敗する、`capabilities()` は観測される挙動と一致する、`wait_for` は単発の検査で、共有の `wait_until` ループがそれを条件待ちに変える、というものです。この契約（`tests/driver_conformance.py`）は現在、3 つのドライバに対して走っています。高速な Linux ゲート上の `FakeDriver`、web CI 上の Playwright（`tests/test_driver_conformance_web.py`）、そして実機上の iOS の 2 つの backend である idb と XCUITest（`tests/test_driver_conformance_ondevice.py`。`ios-e2e.yml` の `conformance (idb + xcuitest)` ジョブが駆動）です。

出荷済みのドライバのうち、この契約が一度も走っていないのが **adb backend（Android、BE-0007）** です。Android の実機レーンは機能スモーク・element ツリー golden・ピクセル VRT を実行しますが、idb・XCUITest・Playwright がそれぞれ証明されているのと同じ意味で、adb ドライバが実機上で決定性コアの不変条件を守ることを検査するジョブはありません。この項目はその欠落を埋めます。

## 動機

- **契約はカバレッジの分だけしか強くありません。** BE-0114 の眼目は、決定性コアの不変条件がすべての実アクチュエータで*同一に*成り立つこと、`FakeDriver` の上だけではないことです。検査されていない backend こそ、乖離が潜む場所です。たとえば、曖昧な adb セレクタが失敗せず最初の一致にタップする（第二原則の違反）としても、既存の Android のジョブはすべて通ってしまいます。smoke・golden・visual は構成上どれも曖昧でないセレクタを使うからです。
- **adb はほかの backend とはセレクタの解決の仕方が異なります。** adb ドライバは 2 つのチャネル（常駐 UI Automator サーバ、BE-0245、および `uiautomator dump` フォールバック）で読み取り、2 つの形式（ドット区切りとアンダースコア、BE-0221）で id を照合します。これらは iOS や web の conformance ジョブでは覆えない adb 固有の経路であり、契約の曖昧なセレクタ・0 件のケースはそこを最も固定すべき箇所です。
- **対称性は明示的に見送られたのであって、退けられたのではありません。** 進行中のプラットフォーム E2E 対称化の項目（`e2e-workflow-structural-parity` 提案）は、Android レーンを観点ごとのジョブに分割することを提案し、`conformance (adb)` ジョブの欠落を実在のカバレッジの穴として記しています。そこではワークフローの再構成ではなくテスト作成の作業なのでスコープ外としています。この項目がそのフォローアップです。

## 詳細設計

この項目はプロダクトコード（アプリ側の conformance モードとテストハーネス）を出荷するため、プライムディレクティブの下で進めます。スイートは決定的で（判定の近くに LLM は一切ありません）、固定の `sleep` ではなく条件待ちで画面を再シードし、アプリ側のフックがあっても showcase がすでに使っているのと同じ一律のオプトインの背後に置きます。ツール側にアプリごとの分岐は入れません。作業は MECE です。

1. **showcase アプリへの Android conformance モードの追加**。iOS の `ConformanceView` に対応するものです。spec チャネルで指定された識別子集合をちょうどそのとおりに描画し（各 id を要求された多重度で。曖昧な「2 つの `dup`」のケースが本物になるように）、加えて常在の readiness マーカーを置いて、readiness が空のツリーからの推測ではなく肯定的な検査になるようにします。レーンがすでにテストしている両方の Android UI ツールキット、Compose と Views（BE-0221）を覆う必要があります。両者は id の形式が異なるからです。あるいは Compose に限ると明示し、その理由を記録します。
2. **adb 経由の再シードチャネル**。iOS がアプリの Documents ディレクトリに `conformance-spec.txt` を書き込むことに対応するものです。adb での自然な形は spec の `adb push`（またはインテント/エクストラ）で、conformance 画面がそれをポーリングします。`with_screen` は spec を書き込んでから、ドライバ自身の `query()` が新しい画面を反映するまで待ちます（条件待ち、sleep なし）。push とインテントのどちらにするかは、エミュレータが最も確実に扱えるほうを実装時に決めます。
3. **adb conformance ハーネスと pytest モジュール**。同じ `tests/driver_conformance.py` の契約を走らせます。新しい `test_driver_conformance_ondevice_android.py` にするか、既存の実機モジュールをパラメータ化するかのいずれかです。`ondevice` マーカー（ゲートの既定 `-m 'not web and not ondevice'` で除外）と、シリアルの環境変数（たとえば `BAJUTSU_CONFORMANCE_SERIAL`）が未設定のときのモジュールレベル skip を持たせ、高速な Linux ゲートが拾わないようにします。idb/XCUITest と web のモジュールと同じ形です。
4. **`android-e2e.yml` への `conformance (adb)` ジョブの追加**（Linux+KVM）。ハーネスを起動済みの AVD に配線します。conformance に対応した APK をビルドし、ブートし、その 1 台のエミュレータに対してスイートを直列で走らせます。Android レーンの残りと同じく非必須で、対称化の項目が提案する観点ごとのジョブ構成（`smoke` / `golden` / `visual` / そして今回の `conformance`）に加わります。
5. **capability 一致テストのための正直な `capabilities()`**。契約は `capabilities()` を約束として扱います。`MULTI_TOUCH` / `SELECT_OPTION` の backend は実際にその操作を行い、その capability を持たない backend は黙って no-op せず `UnsupportedAction` を送出しなければなりません。これらのケースが通るには、adb ドライバの宣言する capability が実挙動と一致している必要があります。その宣言を確認し、必要なら修正することも、この作業の一部です。

## 検討した代替案

- **conformance ジョブではなく、既存の Android smoke シナリオの中で不変条件を検査する案。** 見送りました。smoke シナリオは曖昧でないセレクタで実アプリを操作するので、フィクスチャを無理に歪めない限り、曖昧なセレクタ・0 件のケースを動かせません。conformance の契約には、任意の識別子集合に再シードできるアプリ画面 ── 専用の conformance モード ── が必要で、それはまさに iOS のスイートがすでに行っていることです。
- **element ツリー golden を conformance のシグナルとして再利用する案。** 見送りました。golden が固定するのは*ツリーの形*であって、*セレクタ解決の意味論*ではありません。ドライバが golden に一致していても、曖昧な 2 つの一致のうち最初のものをタップしうるからです。それを捕らえるのは契約自身のアサーションだけです。
- **Compose だけを覆い、Views ツールキットは省く案。** 2 つのツールキットがドライバの経路を共有することを踏まえれば妥当な絞り込みですが、アンダースコアの id 形式（BE-0221）が conformance で検査されないまま残ります。実装時の判断（単位 1）として、黙って落とすのではなく、いずれにせよ記録することにします。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] showcase アプリに Android conformance モードを追加する（Compose。Views は単位 1 の判断に従う）。
- [ ] adb の再シードチャネルを追加する（spec の push/インテント + 条件待ちの `with_screen`）。
- [ ] adb conformance ハーネスと、共有契約を走らせる `ondevice` マーカー付き pytest モジュールを追加する。
- [ ] `android-e2e.yml` に `conformance (adb)` ジョブを追加する。
- [ ] capability テストのため、adb ドライバの `capabilities()` が実挙動と一致することを確認する。

## 参考

- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) — この項目が adb backend へ拡張する、backend 非依存の conformance 契約。
- [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md) — 検査対象の adb backend。
- [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee-ja.md) — conformance 画面が覆うべきドット区切り／アンダースコアの id 形式。
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md) — adb ドライバがセレクタを解決する、常駐チャネルと `uiautomator dump` の 2 経路。
- `e2e-workflow-structural-parity` 提案（BE id は自身のマージ時に採番）── Android レーンを観点ごとのジョブに分割することを提案し、この `conformance (adb)` の欠落をそこではスコープ外として記している項目。
- `tests/driver_conformance.py`、`tests/test_driver_conformance_ondevice.py`、`tests/test_driver_conformance_web.py`、[`android-e2e.yml`](../../.github/workflows/android-e2e.yml)。
