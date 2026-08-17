[English](BE-0356-xcuitest-picker-wheel-adjust.md) · **日本語**

# BE-0356 — XCUITest 向けに pickerWheel の値を決定的に設定するステップを追加する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0356](BE-0356-xcuitest-picker-wheel-adjust-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0356") |
| トピック | シナリオ記述機能 |
<!-- /BE-METADATA -->

## はじめに

常駐する XCUITest ランナーは、`UIPickerView` や `UIDatePicker` のホイールをすでに要素として認識しています。`typeName(_:)` がこれを `pickerWheel` として分類します（`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`）。しかし、その値を設定するステップは存在しません。この項目では、XCUITest 自身が提供する `XCUIElement.adjust(toPickerWheelValue:)` と同じ方法でホイールを指定した値へ動かす新しいステップ `setPickerValue` を追加し、XCUITest バックエンドにおけるホイール型ピッカー自動化に残る最後の隙間を埋めます。

```yaml
- setPickerValue: { sel: { id: some.picker.identifier }, value: "大学" }
```

## 動機

テキストフィールドの `inputView` の背後に `UIPickerView` やホイール型の `UIDatePicker` を表示するフォームは、iOS では珍しくありません。サードパーティ製フォームライブラリの `.picker(PickerContext)` スタイルは素の `UIPickerView` を描画しますし、年月を入力する項目は、`UIDatePicker` を非公開の `mode` の raw value でホイール専用表示へ切り替える形でよく実装されます。どちらも奇異な実装ではなく、テキストフィールドへ入力して `value` アサーションで検証するのとまったく同じように、シナリオが入力し検証すべきごく普通の iOS フォームパターンです。

シナリオがすでに使えるどのステップも、ホイールを厳密な値へ設定できません。`tap` は座標ではなく解決済みのハンドルに作用するため、特定の行を狙えません。ホイールの個々の値は、それぞれ独立した要素として存在しないからです。`swipe` / `drag` / `scroll` は、シナリオのドメイン特化言語（DSL）の中で座標を扱うドラッグ系のステップです。いずれも範囲指定または方向指定のドラッグで、ホイールをおおまかに目的の値へ向けて回すことはできても、座標ドラッグである以上、ちょうどそこで止まる保証はありません。結果の値をアサーションで検証できるかどうかが、ドラッグの距離が行の高さへたまたま一致するかどうかに左右されてしまいます。DSL のもう1つの座標系ステップである `tapPoint` も同様です。固定した1点をタップするだけなので、ホイールがすでに表示している値には当たっても、表示していない値へ動かすことはできません。これは、プライムディレクティブ2がほかのすべてのステップに対してすでに禁じている座標ドラッグの危険性と同じです。曖昧な操作や近似的な操作は、成り行き任せの結果を成功として扱うのではなく、失敗としなければなりません。ところが、ホイールの値を設定するシナリオには、今日それに代わるステップがありません。

XCUITest 自身は、この問題を `XCUIElement.adjust(toPickerWheelValue:)` で解決しています。この呼び出しは座標ではなく、解決済みのホイール要素に対して直接作用します。呼び出しの形は、このコードベースにおける `tap` の実装モデルと同じくハンドルベースです。`XcuitestDriver.tap` はセレクタをハンドルへ解決してから、そのハンドルに対して actuate します（`bajutsu/drivers/xcuitest.py`）。`swipe` / `drag` のような座標ベースではありません。`setPickerValue` が `tap` と同じ形を踏襲するのはこの理由からであり、既存のどのステップにも埋められない隙間を埋めます。

## 詳細設計

**スキーマ。** `Step`（`bajutsu/scenario/models/steps.py`）は判別共用体ではなく、すべてのアクションが optional field として並び、`_one_action` が「ちょうど1つだけ設定されていること」を強制します。`bajutsu/scenario/models/actions.py` に既存の `Drag` / `Swipe` と同じパターンで `SetPickerValue(sel: Selector, value: str)` を定義し、`Step` に `set_picker_value` フィールドを1つ追加すれば、必要なスキーマ変更はそれで終わりです。`_STEP_ACTIONS`（`Step.model_fields` から導出）、`STEP_ACTIONS`（`bajutsu/scenario/models/__init__.py`）、「ちょうど1つ」というバリデーション、`_RUNTIME_ACTIONS`（`bajutsu/orchestrator/actions/_registry.py`）、`_ACTION_KEYS`（`bajutsu/analysis/impact.py`）は、いずれもそのフィールドから自動的に追従します。

**複数コンポーネントのホイールへのアドレス指定。** ホイール専用モードの `UIDatePicker` は、年と月を2つの独立したコンポーネントとしてレイアウトし、それぞれが `pickerWheel` 型の要素になります。ランナーのスナップショット走査（`XcuitestElementProvider.swift` の `SnapshotNode` ツリー）はすでに要素の子を汎用的に再帰しているため、この2つのコンポーネントは今日すでに個別のノードとして現れており、親の `UIDatePicker` だけが現れるわけではありません。`Selector`（`bajutsu/scenario/models/selector.py`）は、まさにこの種の絞り込みのために `within` / `traits` / `index` をすでに持っています。`handleSystemAlert` が複数のボタンから1つを選ぶのに使っているのと同じ `index` の使い方です。`setPickerValue` の `sel` は常にこうしたコンポーネントの1つを指すため、`value` は単純な文字列のままにできます。`within: { id: birthdate.picker }, traits: [pickerWheel], index: 0` で年のホイールを、`index: 1` で月のホイールを選び、`value: "2016年"` と `value: "4月"` はそれぞれ別のステップになります。これは、ほかのすべてのセレクタベースのステップがすでに持っているアドレス指定の仕組みを再利用するものであり、`setPickerValue` 専用の第二の指定方法を新たに増やすものではありません。

**実行時のディスパッチ。** `bajutsu/orchestrator/actions/handlers/gestures.py` に、`_do_tap` と同じ形で `@_handler("set_picker_value")` の関数を追加し、`driver.set_picker_value(step.set_picker_value.sel, step.set_picker_value.value)` を呼びます。これを追加しないと、フィールドが設定された瞬間に `_registry.py` の `_do_action` が `AssertionError("unhandled action")` を送出します。スキーマの追加だけでは配線されない、唯一の実行時の部品がこのハンドラです。

**ドライバプロトコル。** `Driver`（`bajutsu/drivers/base.py`）に `set_picker_value(self, sel: Selector, value: str) -> None` を追加し、具象 `Driver` のすべてに実装します。`XcuitestDriver` は `tap` と同じ方法で実装します。`_resolve_handle` が `sel` をハンドルへ解決し、`_actuate` が `tap` と同じ stale ハンドル再試行ループでそれをランナーへ送ります。`adb.py` と `playwright.py` は `base.UnsupportedAction` を送出する実装にします。これは、ネイティブな `<select>` を持たない2つのバックエンド（`xcuitest.py` と `adb.py`）に対して `select_option` がすでに使っている形と同じですが、対応が裏返っています。ピッカーホイールは iOS 固有の UI コントロールであり、web 専用の `<select>` とは逆の関係にあるため、今度は Android と web が実装の不備ではなく設計上非対応になります。`webview.py` の `WebContextDriver` も自身の `select_option` と同じく `UnsupportedAction` を送出します（`WKWebView` の DOM にもピッカーホイールはありません）。`xcuitest_live.py` の `XcuitestLiveDriver`（BE-0238 が追加したデバイスクラウド向けの W3C WebDriver 経路で、常駐ランナーの `XcuitestDriver` とは別に存在するもう1つの XCUITest 系実装）も、この項目では `UnsupportedAction` を送出します。同じプラットフォームである以上 Appium の XCUITest ドライバ経由でライブ経路の実装が可能かもしれませんが、それは別途ビルド時に評価する事柄であり、この項目ではコミットしません。

`fake.py` には `select_option` と同じ形以上のものが必要です。`FakeDriver.select_option` は `sel` が一意に解決できるかしか確認せず、選択肢そのものは検証しません。そのため、この項目が必要とする「値が存在しない」という振る舞いには `select_option` 側に対応するテストがありません（`tests/test_select_option.py` はパース・ディスパッチ・fake への記録だけを扱い、存在しない option のケースを持ちません）。値が存在しない場合の検出は、この項目の中心にある振る舞いです。`adjust(toPickerWheelValue:)` 自身では検出できないために Swift ランナー側で値の読み戻しという回避策が必要になる（後述）のと同じ理由で、実機なしの高速なユニットテストの経路も必要とします。年月ホイールの兄弟コンポーネント（年と月それぞれのホイール）は、それ自体の識別子を持ちません。上記の「複数コンポーネントのホイールへのアドレス指定」で `within` / `traits` / `index` だけで両者を区別しているのは、まさにこの理由からです。そのため識別子をキーにしたシードでは両者を区別できません。そこで `fake.py` に、フィクスチャの `screen` リストに含まれる特定の `Element` オブジェクトの `id()` をキーとするシードされた `picker_wheel_options: dict[int, list[str]]` を追加します。`handleSystemAlert` のために `system_alert_buttons` がすでにメインの `screen` とは別にシードしているのと同じ形ですが、識別子ではなくオブジェクトの同一性でキーにすることで、識別子を持たない兄弟要素どうしもそれぞれ自分の選択肢を保持できます。`FakeDriver.set_picker_value` は、まず `sel` を一意に解決します（一致がゼロなら `ElementNotFound`、複数なら `AmbiguousSelector` という、ほかのすべてのアクションと同じ規律です）。そのうえで `value` をその要素のシードされた選択肢と照合し、一致すれば actuation を記録し、一致しなければ見つからなかった値を名指しして `ElementNotFound` を送出します。解決できた要素にシードのエントリがまったく無い場合は、値が存在しないのではなくフィクスチャ側の誤りなので、`ElementNotFound` へ落とさず別の明確なエラーを送出します。そうしないと、キーが古くなった場合（scrollable モードで `query()` が返すコピーや、`screen` を作り直す `react` コールバックが原因になり得ます）に、値が存在しない場合のテストが誤った理由で通ってしまいます。これにより、複数コンポーネントの場合も含め、ハンドラのディスパッチ、preflight、値が存在しない場合の経路のすべてを、Simulator なしでテスト可能に保ちます。

**ケーパビリティと preflight。** `Capability`（`bajutsu/drivers/base.py`）に `PICKER_WHEEL` を追加し、常駐ランナーの `XcuitestDriver.CAPABILITIES` と `FakeDriver.CAPABILITIES` の frozenset にこれを加えます（ほかのバックエンドには加えません）。さらに `capability_preflight.py` の `_REQUIREMENTS` に、`HANDLE_SYSTEM_ALERT` のエントリと同じ形で1件追加します。これにより、`PICKER_WHEEL` を広告しないバックエンドで `setPickerValue` を使うシナリオは、デバイス側の処理が始まる前の preflight の時点で、シナリオ内のステップの位置を示しながら失敗します。

**Swift ランナー。** `Router.swift`（`BajutsuKit/Sources/BajutsuRunner/`）に `("POST", "/setPickerValue")` という新しいルートと `handleSetPickerValue` 関数を追加します。`handleTap` と同じ方法でリクエストのハンドルを `SnapshotStore` から解決し、新しい `ElementProviding.setPickerValue(backingElement:value:)` を呼びます。`XcuitestElementProvider`（`BajutsuKit/Runner/Sources/`）は、`liveElement(for:)` で実体を解決してから `el.adjust(toPickerWheelValue: value)` を呼ぶ実装にします。`tap(backingElement:taps:duration:)` がすでに使っているのと同じ、実体要素の解決方法です。

**ホイールに存在しない値の検出。** `adjust(toPickerWheelValue:)` は例外を送出せず、戻り値も返しません。指定した文字列がホイールの表示値にならなかった場合、XCTest はそれを送出された失敗ではなく、ソフトな `XCTIssue` として記録するだけです。`RunnerUITest` の `continueAfterFailure = true`（`BajutsuKit/Runner/Sources/RunnerUITest.swift`）は、まさにこの種のソフトな issue を、常駐ランナーを終了させないまま許容するために存在します。つまり、呼び出し側は、値が反映されなかったことを例外から検知できません。そこで provider は、呼び出し後にホイールの値を読み戻し、要求した文字列と比較する必要があります。このとき一度だけ読むのではなく、上限を決めた小さな回数までサンプリングします。減速がまだ収まっていないホイールを「存在しない値」と誤って報告しないためです。これは `actuateUntilStateChanges`（`BajutsuKit/Sources/BajutsuRunner/GestureRetry.swift`）が `el.value` に現れる着地を観測する際にすでに採用している、回数上限つきサンプリングの規律と同じです。一致しなかった場合には、既存の `.notFound` / `.notHittable`（`ElementProviding.swift`）と並ぶ新しいケースを返します。`.notFound` を再利用しないのは、その既存のメッセージ（セレクタを名指しする「actuatable な要素が見つからない」という文面）が、解決済みで生きている要素の値がたまたま一致しなかっただけの場合に、誤った説明になってしまうためです。`Router` はそのケースを専用のレスポンスステータスへ対応づけます。`_actuate`（`bajutsu/drivers/xcuitest.py`）のステータス分岐は、現状 `_OK` / `_STALE` / `_NOT_FOUND` / `_NOT_HITTABLE` の4通りに固定されており、それ以外のステータスはすべて `XcuitestChannelError`（`SelectorError` ではなく基盤側のエラー）を送出する catch-all に落ちます。そのため `set_picker_value` は、この4つと並ぶ独自の分岐をそこに追加し、見つからなかった値を名指しして `base.ElementNotFound` を送出する必要があります。これは、存在しない値に対する `select_option` 自身の前例に倣うものです。`playwright.py` の `select_option` は、存在しない `<select>` の option を `SelectorError` の一種である `ElementNotFound` として再送出しており、その分岐さえ加われば run loop の既存のセレクタ失敗処理でそのまま扱えます。

**`datePicker` の分類の隙間は影響を受けません。** `docs/selectors.md` は、`UIDatePicker` 自体のコンテナ要素が `typeName(_:)` の switch に `.datePicker` の case を持たないために `other` へ分類される、という既知のトレードオフをすでに記述しています。`setPickerValue` はこの分類に依存しません。親のコンテナではなく、ホイール型の `UIDatePicker` が子として公開する個々の `pickerWheel` コンポーネント要素をアドレス指定するためです。したがって、このステップは今日すでに、素の `UIPickerView` のホイールを操作するのとまったく同じように `UIDatePicker` のホイールを操作でき、親要素の分類の隙間をこのステップのために閉じる必要はありません。`docs/selectors.md` には、親要素が `other` に落ちても `setPickerValue` 経由なら `datePicker` の値を設定できる、という一文を追記します。

**codegen。** `bajutsu/codegen/xcuitest.py` の `_emit_step` に、`step.set_picker_value` に対して `element.adjust(toPickerWheelValue: "...")` を出力するケースを、既存の `handle_system_alert` のケースと同じ形で追加します。`adb` と `playwright` の emitter に新しいコードは不要です。認識されないステップは、`selectOption` が今日この2つの emitter ですでに落ちているのと同じ `// TODO: unsupported step` のスタブへ自然に落ちるからです。

**ドキュメント。** `docs/scenarios.md` のステップ文法の一覧表に1行を追加し、`### drag` と同じ体裁で `### setPickerValue` の節を追加します（上記の YAML 例と、*動機* で述べた「座標ドラッグでは駄目な理由」を含みます）。`docs/ja/scenarios.md` にも日本語版を追加します。`docs/drivers.md` のケーパビリティ表には `pickerWheel` の行を追加します（xcuitest のみ対応）。`docs/ja/drivers.md` にも同じ行を追加します。`docs/selectors.md` と `docs/ja/selectors.md` には、上記の `datePicker` に関する一文を追記します。

**テスト。** `tests/test_select_option.py` のパース・ディスパッチ・fake への記録という形に、上記の `picker_wheel_options` を組み合わせて、次の4つのユニットテストを追加します。

- fake のホイールがシードで持つ選択肢を `value` に指定してディスパッチすると、`driver.set_picker_value` に届き actuation が記録されること
- fake のホイールがシードで持たない選択肢を `value` に指定してディスパッチすると、`ElementNotFound` で失敗すること
- `Capability.PICKER_WHEEL` を持たないバックエンド（`adb` または `playwright`）で `setPickerValue` を使うシナリオを実行すると、actuation を試みる前に preflight の時点で失敗すること
- `tests/driver_conformance.py` の `test_select_option_capability_matches_behavior` にならった `PICKER_WHEEL` のケースを追加すること。トークンを広告するバックエンドは `UnsupportedAction` を送出してはならず、広告しないバックエンドは黙って no-op にせず必ず送出しなければならないことを検証します

## 検討した代替案

- **複数コンポーネントのピッカー全体を1回で指定する `component` / `values` 構造
  （`value: { component: 0, value: "2016年" }` や `values: ["2016年", "4月"]`）。** 採用しませんでした。
  `Selector` はすでに `within` / `traits` / `index` によって1つのコンポーネントをアドレス指定でき、
  これはほかのすべてのセレクタベースのステップが使っている仕組みと同じです。`setPickerValue`
  専用の第二の指定方法を新たに設けることは、既存のフィールドにない指定力を何も加えないまま、
  同じ仕組みを重複させるだけになります。
- **`scroll` の範囲付きループにならい、ホイールの値が一致した時点で止まる座標ドラッグのステップ。**
  採用しませんでした。ピッカーホイールの行の高さやスクロールの物理特性は問い合わせ可能な状態では
  なく、目的の値へ到達するのにドラッグをどこまで進めればよいかを縛る手がかりがありません。これは
  `swipe` / `drag` がまさにこの用途のために抱えている非決定性そのものであり（*動機*）、だからこそ
  この用途には使えません。`adjust(toPickerWheelValue:)` が決定的なのは、座標ではなく XCTest が
  すでに解決済みの要素に対して作用するからです。
- **新しいステップ名を作らず、`tap` に `value` 修飾子を足して再利用する。** 採用しませんでした。
  `tap` はどのバックエンドでもどの要素種別でも、解決済みの要素を起動するという同じ意味を持ちます。
  解決された要素がたまたまピッカーホイールかどうかでその意味を分岐させると、1つのステップの契約が
  バックエンドと要素種別に依存することになります。`select_option` の前例は、これとは逆に、
  別の種類のコントロールには別のステップ名を用意するというものです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] スキーマ: `SetPickerValue` アクションモデル、`Step.set_picker_value` フィールド
- [x] 実行時: `gestures.py` のハンドラ、`Driver.set_picker_value`（xcuitest / adb / playwright /
      webview / xcuitest_live / fake）、`Capability.PICKER_WHEEL` と preflight のエントリ
- [x] Swift ランナー: `/setPickerValue` ルート、`ElementProviding.setPickerValue`、
      値の読み戻しで存在しない選択肢を検出する `XcuitestElementProvider` の実装、対応する
      `_actuate` のステータス分岐（`bajutsu/drivers/xcuitest.py`）
- [x] codegen: xcuitest emitter のケース
- [x] ドキュメント: `docs/scenarios.md` / `docs/drivers.md` / `docs/selectors.md`（両言語）
- [x] テスト: 値が見つかる場合、値が存在しない場合（`ElementNotFound`）、ケーパビリティ不足による
      preflight 失敗、複数コンポーネント（年・月）の場合、`PICKER_WHEEL` の `driver_conformance.py` ケース

## 参考

- [DESIGN.md §5](../../DESIGN.md) — `Driver` 抽象、`Element` / `Selector` の形、そしてこのステップが
  留まるべきセレクタ解決の決定性契約です。
- [BE-0191（`selectOption`、Unit 5）](../BE-0191-pluggable-theme-system-serve-ui/BE-0191-pluggable-theme-system-serve-ui-ja.md)
  — プラットフォーム固有アクションの最も近い前例です。1つの DSL アクション、1つの `Driver`
  プロトコルメソッド、対応しないバックエンドでの `UnsupportedAction` 送出という形です。
- [BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md) — この項目の *詳細設計* が、
  スキーマ、ハンドラレジストリ、`Driver` プロトコル、`fake.py` へ新しいステップを配線する形として
  踏襲した前例であり、バックエンドごとの actuation の細部を実装時のトリアージへ委ねるという前例でも
  あります。
- [`docs/selectors.md`](../../docs/selectors.md) — この項目のアドレス指定モデルが、閉じる必要のないまま
  回避する `datePicker` → `other` の `typeName` 分類のトレードオフを記述しています。
- `bajutsu/drivers/xcuitest.py`（`XcuitestDriver.tap`、`_resolve_handle`、`_actuate`） —
  `set_picker_value` が踏襲するハンドルベースの actuation の流れです。
- `BajutsuKit/Sources/BajutsuRunner/Router.swift`（`handleTap`）と
  `BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`
  （`tap(backingElement:taps:duration:)`） — `handleSetPickerValue` /
  `setPickerValue(backingElement:value:)` が踏襲する Swift 側の流れです。
