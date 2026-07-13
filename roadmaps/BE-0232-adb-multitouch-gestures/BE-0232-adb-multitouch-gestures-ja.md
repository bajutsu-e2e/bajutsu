[English](BE-0232-adb-multitouch-gestures.md) · **日本語**

# BE-0232 — adb ドライバでマルチタッチジェスチャ（pinch / rotate）を実行する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0232](BE-0232-adb-multitouch-gestures-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0232") |
| 実装 PR | [#947](https://github.com/bajutsu-e2e/bajutsu/pull/947) |
| トピック | プラットフォーム対応（iOS / Android / Web / Flutter） |
| 関連 | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md), [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md), [BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation-ja.md) |
<!-- /BE-METADATA -->

## はじめに

マルチタッチの抽象は、ツール全体にすでに備わっています。`Driver.pinch` と `Driver.rotate` は
ドライバのプロトコル（[`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py)）の一部で、
`multiTouch` capability で保護されています。事前 capability チェック
（[BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md)）は、
単一タッチのバックエンドに対する pinch や rotate をデバイス操作の前に却下します。共有シナリオ
[`demos/showcase/scenarios/gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml)
は両方を実行し、iOS の `xcuitest (multi-touch)` ジョブで現在も緑です。この共有シナリオを実行
できない唯一のバックエンドが adb です。Android ドライバの `pinch` と `rotate` は
`UnsupportedAction` を送出し、capability セットは `multiTouch` を欠いています。adb の操作が
`input` コマンドを通じて単一タッチにとどまってきたためです。本項目は、adb ドライバに本物の
二本指ジェスチャを実行させ、既存の共有シナリオを Android でもそのまま走らせます。これにより、
Android の実機 e2e レーン
（[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)）から
最後まで外れていたシナリオが埋まります。

## 動機

BE-0208 の Android e2e レーンは、単一タッチの限界まで共有シナリオを走らせるようになりました。
タブ操作（[BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation-ja.md)）、深い
スクロールと segmented control（BE-0208 ユニット 5）、root 化した `sendevent` によるダブルタップ、
ランタイム権限（BE-0208 ユニット 6）です。残っているのは `gestures_multitouch` の 1 本だけで、
その理由は具体的です。pinch や rotate は 2 つの接触点を同時に動かす必要があり、これは `input`
コマンドでは表現できません。そのため、iOS が XCUITest で走らせている共有シナリオに Android 版の
対応物がなく、3 つ目のバックエンドの実機カバレッジが iOS のシナリオ集合より 1 本だけ足りない状態
です。

このギャップはシナリオ側にあるのではなく、ドライバ層の capability にあります。これはまさに
prime directive 3（アプリ非依存）が位置づける場所です。シナリオは一度だけ記述され
（`pinch: { sel: …, scale: 2.0 }`）、`multiTouch` を表明するすべてのバックエンドで解決できなければ
なりません。プラットフォームが 2 つの接触点を同時に合成できるかどうかは、ドライバの内側にあります。
前例はすぐ近くにあります。
[BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md) は、
ダブルタップのために root 化したデバイス向けの `sendevent` 経路をすでに adb ドライバに教えています。
`input tap` を 2 回連ねるとプラットフォームのダブルタップ受付時間を超過したためです。この経路が、
本項目が拡張する仕組みを確立しました。エミュレータのタッチ入力ノード（最も番号の小さい
`virtio_input_multi_touch_*` の `eventN`、BE-0208 ユニット 5）を見つけ、画面座標をタッチデバイスの
生の範囲へ換算し、protocol B の接触点を 1 回の `adb shell` の往復で `sendevent` の行として発行する
という仕組みです。ダブルタップは接触スロットを 1 つ使い、pinch や rotate は 2 つ使います。

決定性の契約は全体を通じて保たれます。ジェスチャの結果は推測ではなく機械的に検証できます。showcase
のジェスチャ画面は、ミラーしたアクセシビリティ値を `idle` から `pinched` や `rotated` へ、
プラットフォーム自身のレコグナイザが発火したときにだけ反転させます。したがってアサーションは、
二本指の操作が実際に届いたことを証明します。この経路に LLM は入らず、待ちはそれらの値に対する条件
待ちであって固定の待ち時間ではありません（prime directive 1 と 2）。

設計を形づくる、正直な制約が 1 つあります。`sendevent` 経路は root 化したデバイスを必要とします
（BE-0210 のダブルタップが持つのと同じ前提です）。そしてダブルタップと違い、二本指ジェスチャには
**単一タッチのフォールバックがありません**。近似する手段が存在しないのです。root 化していない
デバイスでは、ドライバは、静かに成功してしまう劣化したジェスチャを出すのではなく、明確に失敗しな
ければなりません。BE-0208 の e2e レーンはエミュレータで `adb root` を実行するので、CI はこの前提を
満たします。

## 詳細設計

作業は、BE-0210 が確立した `sendevent` の仕組みに立脚し、それを接触点 1 つから 2 つへ拡張し、
capability を表明し、共有シナリオが期待するジェスチャ画面を Android showcase に与え、そのシナリオを
e2e レーンに組み込みます。触れるのは adb のコマンドビルダ、adb ドライバ、Android showcase アプリ、
そして BE-0208 の配線です。共有シナリオには手を入れず、どの経路にも LLM を足しません。

### 作業分解（MECE）

1. **コマンドビルダに 2 接触点の `sendevent` 列を追加する。** protocol B の pinch と rotate の列を
   [`bajutsu/adb.py`](../../bajutsu/adb.py) に、既存の `sendevent_double_tap_cmd` と並べて追加します。
   どちらも 2 つのトラッキングスロット（スロット 0 とスロット 1）を、交互に挟んだ一連の
   `SYN_REPORT` フレームにまたがって一緒に動かします。ジェスチャが瞬間移動ではなく動きを伴うように
   するためです。プラットフォームの `GestureDetector` は、スケールや回転を分類するためにその動きを
   必要とします。pinch は 2 つの接触点を対象の中心を通る直線に沿って動かし、`scale > 1` では広げ、
   `scale < 1` では狭めます。rotate は 2 つの接触点を直径上に置き、その直径を `radians` だけ振ります。
   どちらもダブルタップ経路の生座標換算とタッチノード探索を再利用します。デバイスなしで単体テスト
   できる純粋なコマンドビルダの関数です。
2. **adb ドライバに `pinch` と `rotate` を実装する。**
   [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) の `UnsupportedAction` のスタブを、
   `sel` を要素の frame へ解決して中心を取り、既存の `_run` シームを通じて 2 接触点の列を発行する実装
   に置き換えます。root 化したデバイスが必要です。`_rooted()` を再利用し、false のときは root の前提を
   明示した `UnsupportedAction` を送出します。二本指ジェスチャには単一タッチのフォールバックがない
   ので、劣化した近似は決して出しません。
3. **`multiTouch` capability を表明する。** ドライバの静的な `CAPABILITIES` frozenset に
   `base.Capability.MULTI_TOUCH` を加えます。このセットは、事前チェック（BE-0082）が
   `backends.capabilities_for` を通じて**デバイスなしで**読み取るクラス定数なので、表明は必然的に
   静的です。root の前提は capability セットではなく操作時（項目 2）に強制します。その帰結を平明に
   記します。`gestures_multitouch` は adb で事前チェックを通過し、root 化していないデバイスでは、
   その手前ではなくジェスチャのステップで速やかに失敗します。
4. **Android showcase にジェスチャ画面を与える。** `SHOWCASE_GESTURES` の launch env で切り替わる
   Compose のジェスチャ画面を、iOS の
   [`GestureView`](../../demos/showcase/ios/swiftui/Sources/GestureView.swift) に対応させて追加します。
   スクロールしない平坦な 2 つの対象に `log.pinch` と `log.rotate` のタグを付け、ミラーした
   アクセシビリティ値を `idle` から始め、Compose の `detectTransformGestures` がズームや回転を認識
   したときに `pinched` や `rotated` へ反転させます。これらの対象が共有シナリオの使う id をそのまま
   持つので、既存の
   [`gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml) はそのまま
   走ります。権限のフローと違って、Android 用の twin シナリオは要りません。対象は a11y の Compose
   ターゲット（`showcase-compose`）に絞り、レーンに合わせます。Views の toolkit は文書化した後続作業
   とします。
5. **`gestures_multitouch` を e2e レーンに組み込む。**
   [`demos/showcase/android/Makefile`](../../demos/showcase/android/Makefile) の `E2E_SCENARIOS` に
   追加します。`e2e` ターゲットはすでに `adb root` を実行するので、root の前提は満たされます。CI の
   ドキュメント（[`docs/ci.md`](../../docs/ci.md) と日本語版）も更新します。これで BE-0208 が外して
   いた最後のシナリオが片付きます。
6. **テスト。** コマンドビルダ（ある中心、スケール、radians に対する 2 スロット protocol B のフレーム
   の形）と、ドライバの root ゲート（root 化していないと `UnsupportedAction`、root 化していれば
   patch した `_run` を通じて列を発行）を、既存のダブルタップのテストにならって単体テストします。
   必要に応じて、`multiTouch` を表明するバックエンドで pinch / rotate のケースを追加し、ドライバ
   conformance スイート
   （[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）を拡張
   します。

## 検討した代替案

- **`input swipe` を 2 回続けて pinch を近似する。** 却下しました。2 回の swipe は 2 つの単一タッチの
  ドラッグを順番に行うだけで、2 つの同時接触ではないので、プラットフォームはスケールも回転も認識
  しません。何も起きないか、スクロールするだけです。「走る」がレコグナイザに無視されるジェスチャは、
  正直な `UnsupportedAction` より悪く、アサーションを誤った理由で成功または失敗させます。
- **`sendevent` ではなくデバイス上の UiAutomator instrumentation でマルチタッチを駆動する。** 本項目
  では却下しました。instrumentation の APK は root なしでマルチポインタのジェスチャを合成できますが、
  adb ドライバのシェルだけで完結する操作モデルから外れ、ビルドとインストールが必要なデバイス側の
  テスト成果物を増やし、すでにあるダブルタップの仕組みより大きな面になります。root の前提を外す将来
  の選択肢として文書に残します。
- **`gestures_multitouch` を iOS 専用のままにし、Android のマルチタッチをスコープ外とする。** 却下
  しました。ツールがすでに端から端までモデル化している capability（`Driver.pinch` と `rotate`、
  `multiTouch` capability、事前チェック、共有シナリオ）に、恒久的なバックエンド間の非対称を残して
  しまいます。root 化した `sendevent` 経路は、レーンがすでに走らせているエミュレータでそれを埋める、
  範囲の限られた前例のある方法です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `bajutsu/adb.py` に 2 接触点の protocol B の pinch / rotate 列を追加する。
- [x] adb ドライバに `pinch` / `rotate` を実装する（root 化したデバイス限定、フォールバックなし）。
- [x] adb ドライバの静的 capability セットに `multiTouch` を表明し、root の前提を文書化する。
- [x] Compose の `SHOWCASE_GESTURES` ジェスチャ画面（`log.pinch` / `log.rotate` のミラー）を追加する。
- [x] `gestures_multitouch` を BE-0208 の e2e レーンに組み込み、`docs/ci.md`（と日本語版）を更新する。
- [x] コマンドビルダとドライバの root ゲートを単体テストする。（conformance スイートの拡張は見送り。
      共有シナリオ `gestures_multitouch` が pinch / rotate を実機の adb で既に動かすためです。）

**ログ**

- [#947](https://github.com/bajutsu-e2e/bajutsu/pull/947) で実装：`bajutsu/adb.py` の 2 スロット protocol B の `sendevent_gesture_cmd` と
  `pinch_contacts` / `rotate_contacts` の幾何、`_two_finger_gesture` により root 化したデバイス限定で
  ゲートした adb ドライバの `pinch` / `rotate`（単一タッチのフォールバックなし）、ドライバの静的
  capability セットへの `MULTI_TOUCH` の表明、Compose の `SHOWCASE_GESTURES` ジェスチャ画面、Android
  の e2e レーンへの `gestures_multitouch` の組み込み、ビルダと幾何と root ゲートの単体テスト。

## 参考

[BE-0007 — Android バックエンド](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
[BE-0208 — CI での Android 実機 e2e](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)、
[BE-0210 — Android 実機操作の忠実度](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)、
[BE-0082 — 事前 capability チェック](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md)、
[BE-0223 — adb で Android の全タブへ到達する](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation-ja.md)、
[BE-0114 — ドライバ conformance スイート](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、
[`bajutsu/adb.py`](../../bajutsu/adb.py)、
[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)、
[`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py)、
[`demos/showcase/scenarios/gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml)、
[`demos/showcase/ios/swiftui/Sources/GestureView.swift`](../../demos/showcase/ios/swiftui/Sources/GestureView.swift)
