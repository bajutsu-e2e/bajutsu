[English](BE-0410-step-latency-android-device-executor.md) · **日本語**

# BE-0410 — 常駐 instrumentation サーバー内の Android 端末側ステップ実行機

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0410](BE-0410-step-latency-android-device-executor-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0410") |
| トピック | Platform support |
| 関連 | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、[BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)、[BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance-ja.md)、[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)、[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)、[BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md) は、端末側ステップ実行プロトコルを定義しています。このプロトコルは、ホストから端末へ移すもの、ホストに残すもの、両プラットフォームが共有するセレクタ意味論の契約を定めます。ホスト側のドライバ調整だけでは届かない、Bajutsu の 1 ステップあたり 250〜500 ミリ秒という目標へ至る道筋です。

この項目は、そのプロトコルの実装のうち Android 側の半分です。常駐 UI Automator サーバーの内部に、ステップ実行機を組み込みます。このサーバーは、[BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance-ja.md) が毎回起動していた `uiautomator dump` を置き換えるために導入したものです。サーバーはすでに、生きた `UiAutomation` セッションとアクセシビリティイベントのリスナーを持つ instrumentation として動いています。実行機が必要とする能力はこの 2 つであり、どちらも新たに獲得する必要はありません。

## 動機

今日、Android のあらゆる条件待ちは、常駐サーバーへの HTTP ポーリングです。`settledDump` は、木のダンプを 2 回取って比較することで静止を確認します。この方式は、2 回のダンプの間に何も変わっていなくても、完全な読み取りを 2 回分払います。

ドライバ内部調整の [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md) は、実測した `POST /act` の平均 2204 ミリ秒のほとんどを、固定 2000 ミリ秒の `POSTDATE_BUDGET_MS` の待ちに帰着させています。画面がすでに静止していても、この待ちは予算が尽きるまで満たされません。この追跡は、サーバー自身のログによる確認がまだ済んでいません。

この項目の設計は、同じ観察から出発します。サーバーは、静止が起きたことを示すアクセシビリティイベントのストリームをすでに持っているので、ポーリングする必要がありません。そこで、固定の予算や 2 回のダンプ比較の代わりに、イベントストリームから直接応答する実行機を提案します。

実行機を構築したら、`tap` ステップについて次の見積もりと照らし合わせます。

- アクセシビリティツリーの直接走査に 20〜50 ミリ秒
- 入力の注入に 50 ミリ秒
- ダンプ比較ではなくイベントストリームから得る settle の判定に数十ミリ秒
- 合計でおよそ 150〜300 ミリ秒（今日の Android の基準値である 3.25〜3.32 秒と比べた数字）

後から読む人は、構築済みの実行機に対して実際の `tap` ステップをトレースすれば、この見積もりを確かめられます。

## 詳細設計

**実装の順番。** この項目は、関連する 4 項目のうち 4 番目、最後で、順番は厳密です。BE-0407、BE-0408、iOS 実行機の [BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)、この項目の順に進みます。この順番の中で、この項目に後続の項目はありません。

**この項目への着手は、BE-0409 が完了するまで行ってはいけません。** 2 つのプラットフォーム実行機をこの順に並べると、セレクタ意味論の移植を、この項目より先に BE-0409 で 1 度だけ行えます。移植とは、iOS 実行機の項目では `resolve_unique` を Swift へ、この項目では Kotlin へ移すことです。BE-0409 の移植で見つかった課題を、この項目が独立に発見し直す事態は避けられます。

常駐サーバーには、実行機が必要とするものがすでに揃っています。生きた `UiAutomation` セッションと、アクセシビリティイベントのリスナーです。既存の `nativeZ` のノード走査が、すでにこの 2 つを使っています。サーバーをステップ実行機に変えるには、次の 4 つの変更を行います。

1. **`dumpWindowHierarchy` による XML の木読み取りを、`AccessibilityNodeInfo` の直接走査に置き換える。** サーバーはすでに `nativeZ` のために同種の走査を行っています。この変更は、その走査を副次的な経路ではなく主たる読み取り経路へ一般化し、両端での XML のシリアライズとパースの往復を取り除きます。
2. **`wait` と `settled` を、`TYPE_WINDOW_CONTENT_CHANGED` と `WINDOWS_CHANGED` イベントから評価する。** 現状の、2 回のダンプが一致するまで待つ方式を置き換えます。BE-0407 は、同じイベント駆動の判定を `POSTDATE_BUDGET_MS` の待ちに対する対症療法として提案しています。この項目は、それを、実行機が画面の静止を判断する既定の方法へ一般化します。既存のダンプに基づく経路へ乗せる狭い修正ではありません。
3. **タップとパンを `UiAutomation.injectInputEvent` で注入する。** 公開の確認には、注入が生じさせるイベント種別を使います。固定の待ちや後続の読み取りは使いません。
4. **スクリーンショットを `UiAutomation.takeScreenshot()` で取得する。** ステップの結果と一緒に返します。独立した `adb exec-out screencap` の subprocess 呼び出しは使いません。

セレクタの解決は、ホスト側の `resolve_unique` ロジック（[`bajutsu/common/drivers/base.py`](../../bajutsu/common/drivers/base.py)）を Kotlin に移植します。移植には、Android 固有の derived-label フォールバック（[`drivers/adb.py:251-282`](../../bajutsu/common/drivers/adb.py)）を含めます。BE-0408 が定めるセレクタ意味論の共有契約に沿った移植です。driver conformance suite（[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）が、iOS 実行機の移植と同じ方法で、この移植を検証します。

## 検討した代替案

- **完全なイベント駆動の実行機を構築せず、BE-0407 が提案する `POSTDATE_BUDGET_MS` の対症療法に、この項目を折り込む。** 却下します。対症療法が狙うのは、今日の request/response 方式の `POST /act` 経路の内側にある、待ち 1 つです。着地の前に、サーバー自身のログによる確認も必要です。この項目は、より大きな再設計です。根底にある発想、つまりポーリングや予算ではなくイベントストリームを読むことを、サーバーの条件評価経路全体へ一般化します。両者は関連していますが、代替関係にはありません。対症療法は先に、独立して着地させられます。この項目は、その着地を前提としません。
- **2 回のダンプによる settle 比較は残したまま、`POSTDATE_BUDGET_MS` の待ちだけを取り除く。** これだけを対策とするのは却下します。その待ちを取り除いても、2 回のダンプ比較は、静止を確認するたびに完全な木の読み取りを 2 回分払い続けます。この項目が使うイベントストリームは、直接応答することで、この費用を避けられます。
- **Android の実行機の設計を、`AccessibilityNodeInfo` ではなく XML 経由にして、今日のホスト側パーサーとパース処理を共有する。** 却下します。端末上の走査は、`nativeZ` のためにすでに存在しています。これを再利用すれば、シリアライズとパースの往復を取り除けます。XML を残せばこの往復は残り続けますが、残すだけの利益はありません。ホスト側のパーサーは、この項目が対象としない `uiautomator dump` フォールバック経路のために、そのまま残します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

**順番の状態：BE-0409 の完了待ちです**（詳細設計の「実装の順番」を参照）。
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
  （BE-0407 にある同じチェック項目を参照）。

## 参考

[BE-0114 — backend 非依存の挙動を検査する driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、
[BE-0210 — Android 実機アクチュエーションの忠実度](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)、
[BE-0234 — adb のシナリオ実行を高速化する](../BE-0234-adb-run-performance/BE-0234-adb-run-performance-ja.md)、
[`bajutsu/common/drivers/base.py`](../../bajutsu/common/drivers/base.py)、
[`bajutsu/common/drivers/adb.py`](../../bajutsu/common/drivers/adb.py)、
[`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt)
