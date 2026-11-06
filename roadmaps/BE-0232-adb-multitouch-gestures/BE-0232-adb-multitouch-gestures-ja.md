[English](BE-0232-adb-multitouch-gestures.md) · **日本語**

# BE-0232 — adb ドライバでマルチタッチジェスチャの pinch と rotate を実行する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0232](BE-0232-adb-multitouch-gestures-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0232") |
| 実装 PR | [#947](https://github.com/bajutsu-e2e/bajutsu/pull/947) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md), [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md), [BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation-ja.md) |
<!-- /BE-METADATA -->

## はじめに

マルチタッチの抽象は、ツール全体にすでに備わっています。`Driver.pinch` と `Driver.rotate` は、
[ドライバ](../../docs/ja/glossary.md#driver-backend-actuator-platform)のプロトコル
（[`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py)）の一部で、`multiTouch` capability で
保護されています。事前 capability チェック
（[BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md)）は、
単一タッチのバックエンドに対する pinch や rotate を、デバイス操作の前に却下します。

共有シナリオ
[`demos/showcase/scenarios/gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml)
は、両方のジェスチャを実行します。iOS の `xcuitest (multi-touch)` ジョブでは、現在も緑です。

このシナリオを実行できない唯一のバックエンドが adb です。Android ドライバの `pinch` と `rotate` は
`UnsupportedAction` を送出し、capability セットは `multiTouch` を欠いています。adb の操作が、`input`
コマンドを通じて単一タッチにとどまってきたためです。

本項目は、adb ドライバに本物の二本指ジェスチャを実行させ、既存の共有シナリオを Android でもそのまま
走らせます。これにより、Android の実機 e2e レーン
（[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)）から最後まで
外れていたシナリオが埋まります。

## 動機

BE-0208 の Android e2e レーンは、単一タッチの限界まで共有シナリオを走らせるようになりました。

- タブ操作（[BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation-ja.md)）
- 深いスクロールと segmented control（BE-0208 ユニット 5）
- root 化した `sendevent` によるダブルタップ
- ランタイム権限（BE-0208 ユニット 6）

残っているのは `gestures_multitouch` の 1 本だけで、その理由は具体的です。pinch や rotate は 2 つの接触点を
同時に動かす必要があり、`input` コマンドでは表現できません。そのため、iOS が XCUITest で走らせている共有
シナリオに、Android 版の対応物がありません。3 つ目のバックエンドの実機カバレッジは、iOS のシナリオ集合より
1 本だけ足りない状態です。

このギャップはシナリオ側にあるのではなく、ドライバ層の capability にあります。これは、prime directive 3
（アプリ非依存）が位置づける場所そのものです。シナリオは一度だけ記述され（`pinch: { sel: …, scale: 2.0 }`）、
`multiTouch` を表明するすべてのバックエンドで解決できなければなりません。プラットフォームが 2 つの接触点を
同時に合成できるかどうかは、ドライバの内側にあります。

前例はすぐ近くにあります。
[BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md) は、ダブルタップの
ために、root 化したデバイス向けの `sendevent` 経路をすでに adb ドライバに教えています。`input tap` を 2 回
連ねると、プラットフォームのダブルタップ受付時間を超過したためです。この経路が、本項目が拡張する仕組みを
確立しました。

- エミュレータのタッチ入力ノードを見つける（最も番号の小さい `virtio_input_multi_touch_*` の `eventN`、
  BE-0208 ユニット 5）。
- 画面座標をタッチデバイスの生の範囲へ換算する。
- protocol B の接触点を、1 回の `adb shell` の往復で `sendevent` の行として発行する。

ダブルタップは接触スロットを 1 つ使い、pinch や rotate は 2 つ使います。

決定性の契約は、全体を通じて保たれます。ジェスチャの結果は、推測ではなく機械的に検証できます。showcase の
ジェスチャ画面は、ミラーしたアクセシビリティ値を `idle` から `pinched` や `rotated` へ、プラットフォーム
自身のレコグナイザが発火したときにだけ反転させます。したがって、アサーションは二本指の操作が実際に届いたことを
証明します。この経路に LLM は入りません。待ちはそれらの値に対する条件待ちであり、固定の待ち時間ではありません
（prime directive 1 と 2）。

設計を形づくる、正直な制約が 1 つあります。`sendevent` 経路は、root 化したデバイスを必要とします。BE-0210 の
ダブルタップが持つのと同じ前提です。ダブルタップと違い、二本指ジェスチャには**単一タッチのフォールバックが
ありません**。近似する手段が存在しないからです。root 化していないデバイスでは、ドライバは、静かに成功してしまう
劣化したジェスチャを出さずに、明確に失敗しなければなりません。BE-0208 の e2e レーンはエミュレータで
`adb root` を実行するので、CI はこの前提を満たします。

## 詳細設計

作業は、BE-0210 が確立した `sendevent` の仕組みに立脚し、それを接触点 1 つから 2 つへ拡張します。そのうえで
capability を表明し、共有シナリオが期待するジェスチャ画面を Android showcase に与え、そのシナリオを e2e
レーンに組み込みます。

触れるのは、adb のコマンドビルダ、adb ドライバ、Android showcase アプリ、BE-0208 の配線です。共有シナリオには
手を入れず、どの経路にも LLM を足しません。

### 作業分解（MECE）

1. **コマンドビルダに 2 接触点の `sendevent` 列を追加する。** protocol B の pinch と rotate の列を、
   [`bajutsu/adb.py`](../../bajutsu/adb.py) に、既存の `sendevent_double_tap_cmd` と並べて追加します。どちらも
   2 つのトラッキングスロット（スロット 0 とスロット 1）を、交互に挟んだ一連の `SYN_REPORT` フレームにまたがって
   一緒に動かします。ジェスチャが瞬間移動ではなく動きを伴うようにするためです。プラットフォームの
   `GestureDetector` は、スケールや回転を分類するために、その動きを必要とします。
   - pinch は、2 つの接触点を対象の中心を通る直線に沿って動かします。`scale > 1` では広げ、`scale < 1` では
     狭めます。
   - rotate は、2 つの接触点を直径上に置き、その直径を `radians` だけ振ります。

   どちらも、ダブルタップ経路の生座標換算とタッチノード探索を再利用します。デバイスなしで単体テストできる、
   純粋なコマンドビルダの関数です。
2. **adb ドライバに `pinch` と `rotate` を実装する。**
   [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) の `UnsupportedAction` のスタブを置き換えます。
   新しい実装は、`sel` を要素の frame へ解決して中心を取り、既存の `_run` シームを通じて 2 接触点の列を
   発行します。root 化したデバイスが必要です。`_rooted()` を再利用し、false のときは root の前提を明示した
   `UnsupportedAction` を送出します。二本指ジェスチャには単一タッチのフォールバックがないので、劣化した近似は
   決して出しません。
3. **`multiTouch` capability を表明する。** ドライバの静的な `CAPABILITIES` frozenset に
   `base.Capability.MULTI_TOUCH` を加えます。このセットは、事前チェック（BE-0082）が
   `backends.capabilities_for` を通じて**デバイスなしで**読み取るクラス定数なので、表明は必然的に静的です。
   root の前提は、capability セットではなく操作時（項目 2）に強制します。その帰結を平明に記します。事前チェックは
   adb で `gestures_multitouch` を通過させます。root 化していないデバイスでは、その手前ではなくジェスチャの
   ステップで速やかに失敗します。
4. **Android showcase にジェスチャ画面を与える。** `SHOWCASE_GESTURES` の launch env で切り替わる Compose の
   ジェスチャ画面を追加します。iOS の
   [`GestureView`](../../demos/showcase/ios/swiftui/Sources/GestureView.swift) に対応させます。スクロール
   しない平坦な 2 つの対象に `log.pinch` と `log.rotate` のタグを付けます。ミラーしたアクセシビリティ値は
   `idle` から始まり、Compose の `detectTransformGestures` がズームや回転を認識したときに `pinched` や
   `rotated` へ反転します。

   これらの対象は、共有シナリオの使う id をそのまま持ちます。そのため、既存の
   [`gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml) はそのまま走ります。
   権限のフローと違って、Android 用の twin シナリオは要りません。対象は a11y の Compose ターゲット
   （`showcase-compose`）に絞り、レーンに合わせます。Views の toolkit は、文書化した後続作業とします。
5. **`gestures_multitouch` を e2e レーンに組み込む。**
   [`demos/showcase/android/Makefile`](../../demos/showcase/android/Makefile) の `E2E_SCENARIOS` に追加します。
   `e2e` ターゲットはすでに `adb root` を実行するので、root の前提は満たされます。CI のドキュメント
   （[`docs/ci.md`](../../docs/ci.md) と日本語版）も更新します。これで、BE-0208 が外していた最後のシナリオが
   片付きます。
6. **テスト。** 既存のダブルタップのテストにならって、次の二つを単体テストします。
   - コマンドビルダ（ある中心、スケール、radians に対する 2 スロット protocol B のフレームの形）
   - ドライバの root ゲート（root 化していないと `UnsupportedAction`、root 化していれば patch した `_run` を
     通じて列を発行）

   必要に応じて、`multiTouch` を表明するバックエンドで pinch / rotate のケースを追加し、ドライバ conformance
   スイート
   （[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）を拡張します。

## 検討した代替案

- **`input swipe` を 2 回続けて pinch を近似する。** 却下しました。2 回の swipe は、2 つの単一タッチのドラッグを
  順番に行うだけで、2 つの同時接触ではありません。プラットフォームはスケールも回転も認識せず、何も起きないか、
  スクロールするだけです。「走る」がレコグナイザに無視されるジェスチャは、正直な `UnsupportedAction` より悪く、
  アサーションを誤った理由で成功または失敗させます。
- **`sendevent` ではなくデバイス上の UiAutomator instrumentation でマルチタッチを駆動する。** 本項目では
  却下しました。instrumentation の APK は、root なしでマルチポインタのジェスチャを合成できます。ただし、adb
  ドライバのシェルだけで完結する操作モデルから外れ、ビルドとインストールが必要なデバイス側のテスト成果物が
  増えます。すでにあるダブルタップの仕組みより、面も大きくなります。root の前提を外す将来の選択肢として、
  文書に残します。
- **`gestures_multitouch` を iOS 専用のままにし、Android のマルチタッチをスコープ外とする。** 却下しました。
  ツールがすでに端から端までモデル化している capability（`Driver.pinch` と `rotate`、`multiTouch`
  capability、事前チェック、共有シナリオ）に、恒久的なバックエンド間の非対称を残してしまいます。root 化した
  `sendevent` 経路は、レーンがすでに走らせているエミュレータでそれを埋める、範囲の限られた前例のある方法です。

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

[BE-0007：Android バックエンド](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
[BE-0208：CI での Android 実機 e2e](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)、
[BE-0210：Android 実機操作の忠実度](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)、
[BE-0082：事前 capability チェック](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md)、
[BE-0223：adb で Android の全タブへ到達する](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation-ja.md)、
[BE-0114：ドライバ conformance スイート](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、
[`bajutsu/adb.py`](../../bajutsu/adb.py)、
[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)、
[`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py)、
[`demos/showcase/scenarios/gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml)、
[`demos/showcase/ios/swiftui/Sources/GestureView.swift`](../../demos/showcase/ios/swiftui/Sources/GestureView.swift)
