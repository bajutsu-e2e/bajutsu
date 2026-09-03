[English](BE-0410-step-latency-android-device-executor.md) · **日本語**

# BE-0410 — 常駐 instrumentation サーバー内の Android 端末側ステップ実行機

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0410](BE-0410-step-latency-android-device-executor-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0410") |
| トピック | Platform support |
| 関連 | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、[BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)、[BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance-ja.md)、[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)、[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)、[BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md) |
<!-- /BE-METADATA -->

## はじめに

関連する別提案は、端末側ステップ実行プロトコルを定義しています。何をホスト側から
端末側に移し、何をホスト側に残すか、そして両プラットフォームが共有するセレクタ
意味論の契約です。これは、ホスト側のドライバ調整だけでは届かない、Bajutsu の
250〜500 ミリ秒という 1 ステップあたりの目標に到達する道筋です。この項目は、
そのプロトコルの実装のうち Android 側の半分です。常駐 UI Automator サーバー
（[BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance-ja.md) が
毎回起動していた `uiautomator dump` を置き換えたもの）の内部にステップ実行機を
組み込みます。このサーバーはすでに、生きた `UiAutomation` セッションと
アクセシビリティイベントのリスナーを持つ instrumentation として動いており、
どちらもこの実行機が必要とする能力であって、新たに獲得する必要はありません。

## 動機

今日、Android のあらゆる条件待ちは常駐サーバーへの HTTP ポーリングであり、
`settledDump` は木のダンプを 2 回取って比較することで静止を確認します。この方式は、
2 回の間に何も変わっていなくても、完全な読み取りを 2 回分払います。ドライバ内部
調整の別項目はすでに、実測した `POST /act` の平均 2204 ミリ秒のほとんどを、
画面がすでに静止していれば満たしようのない固定 2000 ミリ秒の `POSTDATE_BUDGET_MS`
の待ちに帰着させています。ただしこれは、サーバー自身のログによる確認がまだ
済んでいません。
サーバーがすでに、ポーリングせずとも静止が起きたことを知るためのアクセシビリティ
イベントストリームを持っているという、その同じ観察が、この項目の設計の出発点です。
固定の予算や 2 回のダンプ比較ではなく、イベントストリームから直接応答する実行機です。

構築後にこの項目を照らし合わせるべき見積もりは次のとおりです。アクセシビリティ
ツリーの直接走査に 20〜50 ミリ秒、入力の注入に 50 ミリ秒、そしてダンプ比較ではなく
イベントストリームから得る settle の判定に数十ミリ秒、`tap` ステップ全体でおよそ
150〜300 ミリ秒です。今日の Android の基準値である 3.25〜3.32 秒と比べた数字です。
後から読む人は、構築済みの実行機に対して実際の `tap` ステップをトレースすれば
これを確かめられます。

## 詳細設計

**実装の順番。** この項目は、関連する 4 項目の厳密な順番のうち 4 番目、最後です。
ドライバ内部調整の項目、端末側プロトコルの項目、iOS 実行機の項目、この項目の順に
進みます。**この項目への着手は、iOS 実行機の項目が完了するまで行ってはいけません。**
2 つのプラットフォーム実行機をこの順に並べることで、セレクタ意味論の移植（iOS 実行機の
項目では `resolve_unique` を Swift へ、この項目では Kotlin へ）を、まず iOS 実行機の
項目で 1 度行い、そこで見つかった課題をこの項目が独立に発見し直す事態を避けられます。
この項目には、この順番の中で後続の項目がありません。

常駐サーバーには、実行機が必要とするものがすでに揃っています。生きた
`UiAutomation` セッションと、既存の `nativeZ` のノード走査ですでに使っている
アクセシビリティイベントのリスナーです。これをステップ実行機に変えるには、
次の 4 つの変更を行います。

1. **`dumpWindowHierarchy` による XML の木読み取りを、`AccessibilityNodeInfo` の
   直接走査に置き換える。** サーバーはすでに `nativeZ` のために同種の走査を
   行っています。これを、副次的な経路ではなく主たる読み取り経路として一般化し、
   両端での XML のシリアライズとパースの往復を取り除きます。
2. **`wait` と `settled` を、`TYPE_WINDOW_CONTENT_CHANGED` と `WINDOWS_CHANGED`
   イベントから評価する。** 現状の、2 回のダンプが一致するまで待つ方式を
   置き換えます。これは、ドライバ内部調整の別項目が `POSTDATE_BUDGET_MS` の
   待ちに対する対症療法として提案しているイベント駆動の判定と同じ発想です。
   ここでは、既存のダンプに基づく経路へ乗せる狭い修正としてではなく、実行機が
   画面の静止を判断する既定の方法として一般化します。
3. **タップとパンを `UiAutomation.injectInputEvent` で注入し、固定の待ちや後続の
   読み取りではなく、注入が生じさせるイベント種別で公開を確認する。**
4. **スクリーンショットを `UiAutomation.takeScreenshot()` で取得し、独立した
   `adb exec-out screencap` の subprocess 呼び出しではなく、ステップの結果と
   一緒に返す。**

セレクタの解決は、ホスト側の `resolve_unique` ロジック
（[`bajutsu/common/drivers/base.py`](../../bajutsu/common/drivers/base.py)）を、
Android 固有の derived-label フォールバック
（[`drivers/adb.py:251-282`](../../bajutsu/common/drivers/adb.py)）を含めて Kotlin に
移植します。関連プロトコル項目が定めるセレクタ意味論の共有契約に沿ったものであり、
driver conformance suite
（[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）
に対して、iOS 側実行機の移植と同じ方法で検証します。

## 検討した代替案

- **完全なイベント駆動の実行機を構築せず、ドライバ内部調整の項目が提案する
  `POSTDATE_BUDGET_MS` の対症療法だけに、この項目を折り込む。** 却下します。
  その対症療法は、今日の request/response 方式の `POST /act` 経路の内側にある
  待ち 1 つを狙ったものであり、着地の前にサーバー自身のログによる確認すら
  済んでいません。この項目は、その根底にある発想——ポーリングや予算ではなく
  イベントストリームを読む——を、サーバーの条件評価経路全体へ一般化する、
  より大きな再設計です。両者は関連していますが代替関係にはありません。
  対症療法は先に、独立して着地させられますし、この項目はその着地を前提とは
  しません。
- **2 回のダンプによる settle 比較は残したまま、`POSTDATE_BUDGET_MS` の待ちだけを
  取り除く。** これだけを対策とするのは却下します。その待ちを取り除いても、
  2 回のダンプ比較は、静止を確認するたびに完全な木の読み取りを 2 回分払い続けます。
  この項目が使うイベントストリームであれば、そこから直接応答してこの費用を
  避けられます。
- **Android の実行機の設計を、`AccessibilityNodeInfo` ではなく XML 経由にして、
  今日のホスト側パーサーとパース処理を共有する。** 却下します。端末上の走査は
  `nativeZ` のためにすでに存在しており、これを再利用すれば、XML を残した場合に
  発生し続けるシリアライズとパースの往復を、利益なく取り除けます。ホスト側の
  パーサーは、この項目が対象としない `uiautomator dump` フォールバック経路の
  ために、そのまま残します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

**順番の状態：iOS 実行機の項目の完了待ちです**（詳細設計の「実装の順番」を参照）。
それまでは下のチェックリストに着手しないでください。

- [ ] `resolve_unique` のセレクタ意味論を、derived-label フォールバックも含めて
  Kotlin に移植する。プロトコル項目のフィクスチャ拡張が着地した時点で、driver
  conformance suite
  （[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）
  で検証する。
- [ ] XML の木読み取りを、主たる読み取り経路としての `AccessibilityNodeInfo` の
  直接走査に置き換える。
- [ ] `wait` と `settled` を、2 回のダンプ比較ではなくアクセシビリティイベント
  ストリームから評価する。
- [ ] タップとパンを `UiAutomation.injectInputEvent` で注入し、イベント種別で
  確認する。
- [ ] スクリーンショットを `UiAutomation.takeScreenshot()` で取得し、ステップの
  結果とともに返す。
- [ ] この実行機に対して実際の `tap` ステップをトレースし、上記の 150〜300
  ミリ秒という見積もりと比較した結果のステップごとの壁時計をここに記録する。
- [x] `roadmap-id` ワークフローが `main` 上で 4 項目の ID を採番したら、ドライバ内部
  調整、端末側プロトコル、iOS 実行機の各項目との間で `関連` の相互リンクを補う
  （ドライバ内部調整の項目にある同じチェック項目を参照）。

## 参考

[BE-0114 — backend 非依存の挙動を検査する driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、
[BE-0210 — Android 実機アクチュエーションの忠実度](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)、
[BE-0234 — adb のシナリオ実行を高速化する](../BE-0234-adb-run-performance/BE-0234-adb-run-performance-ja.md)、
[`bajutsu/common/drivers/base.py`](../../bajutsu/common/drivers/base.py)、
[`bajutsu/common/drivers/adb.py`](../../bajutsu/common/drivers/adb.py)、
[`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt)
