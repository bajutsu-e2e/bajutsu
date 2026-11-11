[English](BE-0480-android-picker-wheel-set-value.md) · **日本語**

# BE-0480 — setPickerValue で Android の NumberPicker の wheel を指定の値に合わせる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0480](BE-0480-android-picker-wheel-set-value-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0480") |
| 実装 PR | [#2412](https://github.com/bajutsu-e2e/bajutsu/pull/2412)（単位 1〜6） |
| トピック | シナリオ記述機能 |
<!-- /BE-METADATA -->

## はじめに

`setPickerValue` ステップは、wheel 型のピッカーを指定した値の行まで動かします。現在このステップが動くのは
iOS だけです。[BE-0356](../BE-0356-xcuitest-picker-wheel-adjust/BE-0356-xcuitest-picker-wheel-adjust-ja.md)
が XCUITest の `adjust(toPickerWheelValue:)` の上に実装し、adb backend はこのステップに対して
`UnsupportedAction` を送出します。本項目は、同じステップを Android でも使えるようにします。対象は
Android の wheel 部品である `android.widget.NumberPicker` と、それを組み合わせた spinner モードの
`DatePicker` と `TimePicker` です。シナリオの書き方は変わりません。

```yaml
- setPickerValue:
    sel: { within: { id: form.birthdate }, traits: [pickerWheel], index: 2 }
    value: "2016"
```

adb backend の常駐 UI Automator サーバーに、`/setPickerValue` エンドポイントを追加します。このエンドポイントは、
解決済みの wheel をアクセシビリティのスクロール操作で1行ずつ動かします。1行動かすたびに wheel の値を読み直し、
指定の行で止めます。座標を一切やり取りしないため、Android でも iOS と同じく決定的に動きます。

## 動機

wheel 型のピッカーは、Android のフォームでありふれた部品です。`NumberPicker` は個数や区分を選ばせる部品で、
spinner モードの `DatePicker` と `TimePicker` は各項目を2つか3つの `NumberPicker` に分けて並べます。
こうしたフォームを埋めるシナリオは、アプリがその値をどう扱ったかを検証する前に、まず wheel を設定する必要があります。

しかし、Android のシナリオで今使えるステップには、wheel を指定の値に合わせるものがありません。
`docs/platform-parity.md` のパリティ表は、Android の `pickerWheel` を未対応（gap）としています。
`setPickerValue` を使うシナリオは、adb backend ではデバイス操作の前の preflight で拒否されます。
ほかのステップで代用できない理由は、BE-0356 が iOS について述べたものと同じです。`swipe`、`drag`、`scroll`
は座標で指定するドラッグです。wheel を目的の値の方向へ回せますが、その値で止まる保証はありません。
隣の行への `tap` は wheel を1行だけ動かすので、行の数だけジェスチャーと待機を繰り返すことになります。
しかもその `tap` は、常駐サーバーの `/act` エンドポイントが置き換えるために作られた座標経由の操作です。
プライムディレクティブ2は、おおよその位置に着地する操作を認めません。このため Android のシナリオ作成者には、
wheel の項目を決定的に埋める手段がありません。

この不足は、共通のシナリオも分断します。iOS のセレクター `{ within, traits: [pickerWheel], index }`
は Android では何にも一致しません。そのため両プラットフォームで共通のフォームのシナリオは、wheel
のステップを外すか、シナリオを2つに分けるしかありません。adb backend は `NumberPicker` のノードにクラス名から
`numberPicker` の trait を付けるだけで、`pickerWheel` は付けません。

この変更が完了したかどうかは、次の3点で確かめられます。

- `demos/showcase/scenarios/picker_wheel.yaml` の2つのシナリオが、iOS でも解決できるセレクターで、
  `android-e2e.yml` の smoke ジョブで adb backend を通ります。
- `setPickerValue` を使うシナリオが、adb で preflight に拒否されなくなります。
- `docs/platform-parity.md` の `pickerWheel` の行で、Android の欄が ✅ になります。

## 詳細設計

**対象範囲。** このステップの対象は、`android.widget.NumberPicker` と、それを使って作られた部品です。具体的には
spinner モードの `DatePicker` と `TimePicker`、および `AndroidView` で `NumberPicker` を載せた Jetpack
Compose の画面です。Compose 自身が描画する wheel は対象外とします。Compose には標準の wheel がなく、
アプリごとに自作した wheel はアクセシビリティ上の構造がそれぞれ異なります。それらに対応するにはアプリごとの規約が必要になり、
プライムディレクティブ3がそうした規約をドライバーに持ち込むことを認めません。シナリオのスキーマ、`Step` モデル、
実行時のハンドラー、BE-0356 が追加した `FakeDriver` は変更しません。本項目が変更するのは、adb backend、
常駐サーバー、Android の showcase アプリ、それらを説明する文書です。

**trait の対応づけ。** `bajutsu/common/drivers/adb/_functions.py` の `_traits` は、ノードの最初の trait
をウィジェットのクラスから作ります。このため `NumberPicker` のノードは、現在 `numberPicker` を持っています。
`_traits` に規則を1つ加え、クラスが `android.widget.NumberPicker` のノードには `pickerWheel` も付けます。
この token は `bajutsu/common/drivers/base/trait.py` の新しい `Trait.PICKER_WHEEL` として定義します。
共通の名前の定義が1か所になり、各 backend の対応づけも conformance suite による検証も、そこを参照できます。
`Trait.SECURE_TEXT_FIELD` が先例です（BE-0331）。
クラス由来の `numberPicker` は残すので、既存のセレクターはそのまま解決します。共通の token により、
シナリオは両プラットフォームで同じセレクターを使って wheel を指定できます。spinner モードの `DatePicker`
は、子の `NumberPicker` を含むコンテナとしてダンプに現れます。`within` は枠の包含関係で範囲を絞り、
`traits` と `index` がその中から wheel を1つ選びます。これは BE-0356 が iOS ですでに使っている指定方法です。
wheel の並び順は iOS と同じくロケールに従い、`en_US` では月、日、年の順です。

**常駐サーバーのエンドポイント。**
`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt`
の常駐サーバーに、`/act` と `/type` に並べて `/setPickerValue` のルートを追加します。リクエストは `/act`
と同じ識別フィールド、`index`、`count`、`since`、待機予算を持ち、それに `value` が加わります。
ハンドラーの前半は `respondAct` と同じです。すべてのフィールドをデフォルト値で補わずに検証し、アイドルと
postdate の印を待ってから、一致する要素の枠を読みます。自分の数えた件数がホストの件数と違えば、既存の
stale ステータスを返します。そこから先は、座標ではなくアクセシビリティのノードを操作します。

1. 画面上の枠が `matches[index]` と等しく、クラスが `android.widget.NumberPicker` である
   `AccessibilityNodeInfo` を探します。該当するノードがほかの部品であれば、新しい
   *not-a-picker-wheel* ステータスを返します。
2. wheel の現在の値を、子の入力ノード `android:id/numberpicker_input` のテキストから読みます。この子ノードが、
   wheel が選択中として表示している行です。
3. 走査を始める前に、ハンドラーは向きを決めます。現在の値と要求された値が、空白を除いてどちらも整数として
   読めるなら、要求された値の側へ先に走査します。要求された値が大きければ順方向、小さければ逆方向です。
   そうでなければ順方向から始めます。年の wheel は、最短の走査で目的の行に着きます。
   時間や分を選ぶ折り返しの wheel では遠回りになることがありますが、それも行数の上限の範囲に収まります。
   値が要求された値と異なる間は、wheel のノードに `ACTION_SCROLL_FORWARD` を実行し
   （逆方向の走査では `ACTION_SCROLL_BACKWARD`）、アイドルを待ってから値を読み直します。`NumberPicker`
   のアクセシビリティのプロバイダーは、各操作を1行分の移動に対応づけています。利用者が wheel を
   1行だけはじいたときと同じ変化です。
4. 最初の走査は、一致しないまま端で終わることがあります。折り返さない wheel では最後か最初の行に達し、
   操作が `false` を返すか、確認のための読み直しでも値が変わらないままになります。
   値が変わらなかった1回の読み取りは、テキストがまだ更新されていない行の可能性があるため、それだけでは走査を終えません。この場合ハンドラーは、反対の向きに
   走査します。並びが単調でない数値の行でも、この反対向きの走査で目的の行に着けます。折り返す wheel では、
   走査を始めた値に戻ってきます。そこで走査を終えます。どちらも一致しなければ、wheel にその行は
   ありません。このときハンドラーは、新しい *picker-value-absent* ステータスを返します。
5. 2つの予算で走査を区切ります。値が変わり続ける wheel がリクエストをいつまでも占有しないためです。
   固定の行数の上限 `MAX_PICKER_ROWS = 400` が、両方向を合わせて進める行の総数を制限します。
   上限に達したときは新しい *picker-walk-exhausted* ステータスを返し、応答には上限の値を記します。1行ごとにも予算
   `PICKER_ROW_BUDGET_MS = 300` があります。アクセシビリティのスクロール1回と、`NumberPicker`
   の1行分のアニメーションに対するアイドル待ちの合計です。この値は初期値です。最初の実機での実行で測定し、調整します。
   ハンドラーは、1行目から数えて `MAX_PICKER_ROWS × PICKER_ROW_BUDGET_MS` の期限でも走査を打ち切ります。
   そのときも、期限を記した *picker-walk-exhausted* を返します。このため、遅い行があっても、応答がホストの
   タイムアウトを過ぎることはありません。

一致したときは、`/act` と同じ `respondLanded` で `200` を返します。ホストは追加の読み取りなしに、
操作後のツリーと操作時刻の印を受け取れます。ハンドラーは入力ノードに文字を書き込みません。
`ACTION_SET_TEXT` を使わない理由は、「検討した代替案」の節で述べます。

**ホスト側のドライバー。** `bajutsu/common/backend_cli/adb_resident/_functions.py` に、`act(...)`
と並べて `set_picker_value(...)` のクライアントを追加します。このクライアントは `act(...)` と同じ識別フィールドを送り、
応答のステータスを結果に対応づけます。`bajutsu/common/drivers/adb/adb_driver.py` の
`AdbDriver.set_picker_value` は、現在の `UnsupportedAction` を送出するだけの実装を置き換えます。
このメソッドは、`_device_act` が `tap` で行うのと同じ方法で `sel` を解決します。安定したツリー上で
`resolve_unique` を使う `_resolve` を呼び、一致がなければ `_scroll_into_view` を使います。それでも一致しなければ
`ElementNotFound` を、2件以上一致すれば `AmbiguousSelector` を送出します。そのうえで要素の識別情報と序数を送り、
応答のステータスごとに次のように扱います。

| 応答のステータス | `AdbDriver.set_picker_value` の動作 |
|---|---|
| `200` | 操作を記録し、返されたツリーを次の読み取りの起点にします。tap に対する `_device_act` と同じ扱いです。 |
| stale | 再解決して再試行します。`/act` の stale 応答に対して `_device_act` がすでに使っている、回数に上限のある再試行と同じです。試行回数を使い切った場合と、解決した要素に送れる識別情報の記録がない場合と、序数を数えた読み取りより後まで要素が残っていた場合は、フォールバックできる座標経路がないため、セレクターを示して `ElementNotFound` を送出します。 |
| not-a-picker-wheel | セレクターを示し、要素が `NumberPicker` ではないことを述べて `ElementNotFound` を送出します。 |
| picker-value-absent | 現れなかった値を示して `ElementNotFound` を送出します。BE-0356 も iOS で同じ場合をこう扱います。 |
| picker-walk-exhausted | 値が現れる前に、走査が行数の上限か期限に達したことを述べて `ElementNotFound` を送出します。存在しない値の場合とは別のメッセージにするので、調整が必要な予算が「行がない」と読まれることはありません。 |
| 古いサーバーからの `404` | 配備されている常駐サーバーが `/setPickerValue` より古く、再ビルドが必要であることを述べて `UnsupportedAction` を送出します。`/act` がない場合に `AdbActUnsupported` がしているのと同じく、リースの間は `/setPickerValue` の不在だけを専用のフラグで記憶します。`/act` の経路はそのまま tap に使い続けます。 |
| 送信後に応答が失われた | サーバーが wheel を動かした可能性はあるが、行を確認できなかったことを述べて `ElementNotFound` を送出します。tap の `_device_act` は、この場合に着地したものとして続行します。このステップは続行できません。走査の途中で止まった wheel は、誤った値を示すからです。 |

`set_picker_value` のクライアントは `ACTION_TIMEOUT_S` を流用しません。タイムアウトは
`MAX_PICKER_ROWS × PICKER_ROW_BUDGET_MS` に `ACTION_TIMEOUT_S` を足した値で、初期値では約130秒です。
この定数は `bajutsu/common/backend_cli/adb_resident/budgets.py` の `ACTION_TIMEOUT_S` の隣に定義します。
サーバーは、ホストのタイムアウトが切れるより前に、行数の上限か期限で *picker-walk-exhausted* を返します。
そのためホストでタイムアウトが起きたときは、値がないのではなく、通信路が壊れたことを意味します。

このステップには座標による代替経路を設けません。常駐サーバーとの通信路が使えないときは、wheel
をドラッグせずに `UnsupportedAction` を送出します。座標によるドラッグは指定の行に止まる保証がなく、
その危うさを避けることがこのステップの存在理由だからです。

**capability と preflight。** `AdbDriver.CAPABILITIES` に `Capability.PICKER_WHEEL` を加えます。
`bajutsu/common/capability/capability_preflight.py` の該当する要件は、判定の条件をそのまま保ちます。
説明文だけを「iOS XCUITest only」から両方の backend を挙げる表現に改めます。
`bajutsu/common/drivers/base/driver.py` の `Driver.set_picker_value` のコメントと、
`capability_preflight.py` のモジュール docstring にある `setPickerValue` の項目からも、wheel が iOS 専用であり、XCUITest だけが指定の行に止められるという記述を外します。web backend、
`WebContextDriver`、`XcuitestLiveDriver` は引き続き `UnsupportedAction` を送出し、preflight もそれらの上では
このステップを拒否し続けます。

**コード生成。** `bajutsu/codegen/uiautomator.py` の UI Automator 向けの生成器は、`setPickerValue`
に対して、セレクターと値を記した `// TODO` を出力します。UI Automator の `UiObject2` はアクセシビリティの操作を
公開していません。忠実に変換するには、エンドポイントと同じ手順で wheel を走査する補助関数が必要です。
その補助関数は別の作業とし、本項目には含めません。

**showcase。** Android の2つの showcase アプリに、iOS アプリと同じく起動時の環境変数 `SHOWCASE_PICKERS`
で切り替わるピッカー画面を追加します。`demos/showcase/android/views/` の Views 版は、ネイティブの
`NumberPicker` と spinner モードの `DatePicker` を使います。`demos/showcase/android/compose/` の Compose 版は、
同じ2つの部品を `AndroidView` で載せます。これにより、「対象範囲」で挙げた Compose からの経路も確かめられます。
Compose 版の画面は、iOS の画面と同じ識別子 `form.school`、`form.school.value`、`form.birthdate`、
`form.birthdate.value` を使います。Android のリソース名にはドットを含められないため、Views 版の画面は
`form_school` のようにアンダースコアの形を使います。各アプリは選ばれた値を `.value` 要素の `content-desc`
に書き写し、adb backend はそれを `value` として読みます。`picker_wheel.yaml` の各 `id` には、両方の形を並べます（`id: [form.school, form_school]`）。
BE-0221 以降、Views のレーンで動かすシナリオはすべてこの書き方です。`swiftui` タグは引き続き UIKit と
Flutter の iOS のレーンを除外します。`picker_wheel` は、`demos/showcase/android/Makefile` の
`E2E_SCENARIOS` と `E2E_VIEWS_SCENARIOS` に加えます。この2つは、`android-e2e.yml` の smoke ジョブが実行する
シナリオの一覧です。ファイル冒頭のコメントからも、iOS 専用という記述を外します。SwiftUI 版専用という記述は、引き続き `swiftui` タグを除外する iOS のレーンに範囲を絞って残します。iOS のシナリオの月の値 `"May"` は、iOS の wheel が `en_US` で表示する行の名前です。
同じロケールで Android の spinner が表示する行の名前は、最初の実機での実行で確かめます。名前が異なる場合は、
共通のシナリオをやめ、Android のレーンに専用のシナリオファイルを用意します。

**ドキュメント。** 次の英語のページを、それぞれ `docs/ja/` の対訳と合わせて更新します。

- `docs/scenarios.md`：`setPickerValue` の行と節から「iOS (XCUITest) only」を外し、Android
  での仕組みを説明します。Compose が描画する wheel が対象外であることも、同じ節に記します。Android には wheel に相当するものがないと述べている末尾の段落は、書き直します。
- `docs/drivers.md`：capability 表の `pickerWheel` の行に `adb` を加えて「(iOS only)」を外します。wheel をネイティブの iOS 部品と呼んでいる preflight の段落も、両方の backend を挙げる表現に改めます。`PICKER_WHEEL` を XCUITest 専用の capability に含めている `APP_CONTEXT` の段落からも、`PICKER_WHEEL` を外します。
- `docs/platform-parity.md`：`pickerWheel` の行で Android の欄を ✅ にし、Compose での制限を注記します。
- `docs/selectors.md`：adb の trait 対応表に、`NumberPicker` のノードが `numberPicker` と `pickerWheel`
  の両方を持つことを記します。
- `docs/architecture.md`：`setPickerValue` の項目に、XCUITest の仕組みと並べて常駐サーバーのエンドポイントを記します。オンデバイスのカバレッジの行には、`ios-e2e.yml` と並べて `android-e2e.yml` の smoke ジョブを挙げます。`PICKER_WHEEL` を宣言するのは XCUITest の backend と `FakeDriver` だけだと述べている行に、adb backend を加えます。

**テスト。** 決定的なゲートは、エミュレーターなしでホスト側を検証します。

- `_traits` のテストで、`NumberPicker` のノードが `numberPicker` と `pickerWheel` の両方を持つこと、
  ほかのクラスには `pickerWheel` が付かないことを確かめます。conformance のケースで、wheel を扱う
  すべての backend が wheel を `Trait.PICKER_WHEEL` に対応づけることを固定します。
- `AdbDriver.set_picker_value` のテストでは、常駐サーバーとの通信路を差し替えて、上の応答表の各行を
  確かめます。対象は次の場合です。
  - 一致した場合と、stale の後の再試行が成功した場合
  - stale の再試行を使い切った場合
  - 要素が wheel でない場合と、値が存在しない場合
  - 走査が打ち切られた場合
  - 古いサーバーが `404` を返した場合
  - 応答が失われた場合と、通信路が使えない場合
- preflight のテストで、`setPickerValue` を使うシナリオが adb では preflight を通り、web backend
  では引き続き拒否されることを確かめます。
- `tests/test_adb_resident_budgets.py` に、サーバーのソースから `MAX_PICKER_ROWS` と `PICKER_ROW_BUDGET_MS`
  を読むケースを加えます。`WAIT_BUDGET_MS`、走査の期限、`ACT_PUBLISH_BUDGET_MS`、`ACT_TREE_BUDGET_MS`
  の合計が、1秒の余裕を残して `set_picker_value` のタイムアウトに収まることを確かめます。片方の端だけで
  1行の予算を調整すると、ゲートが失敗します。
- `tests/driver_conformance.py` の `PICKER_WHEEL` のケースは、adb のドライバーがこの token を宣言した時点で、
  adb のドライバーも検証の対象に含めます。

エンドポイントから showcase の画面までを通す実機での経路は、`android-e2e.yml` で実行します。
このジョブは `make check` には含まれません。

## 検討した代替案

| 案 | 内容 | 採らない理由 |
|---|---|---|
| wheel の入力欄に値を書き込む | `android:id/numberpicker_input` に `ACTION_SET_TEXT` で値を設定し、フォーカスを外して wheel に値を確定させます。距離に関係なく1回のリクエストで済みます。 | 値が確定するかどうかは、wheel の入力フィルターと、フォーカスが適切な時点で外れるかどうかに左右されます。`descendantFocusability` で子へのフォーカスを止めている wheel は、そもそも編集できません。結果が、アプリがこの部品をどう設定したかに依存してしまいます。 |
| ホストから隣の行を tap する | 選択中の行の上か下の行に既存の `tap` を送り、値が一致するまで1行ずつ繰り返します。サーバーの変更は要りません。 | 1行ごとにジェスチャーの往復とアニメーションの待機がかかり、もっとも遅い案です。どの tap も座標経由で着地するので、常駐サーバーの `/act` が置き換えるために作られた経路に戻ってしまいます。 |
| 向きの手がかりを使わず、常に両方向へ走査する | 値に関係なく、端まで順方向に走査してから逆方向に走査します。サーバーの論理がもっとも単純です。 | spinner の `DatePicker` の年の wheel は、デフォルトで約200行あります。前の年を設定すると、最後の年まで順方向に進んでから戻ることになり、1分を超えることがあります。向きの手がかりにより、折り返さない数値の wheel は最短の向きに走査します。2方向の走査は、文字列の行のためのフォールバックとして残します。 |
| Compose が描画する wheel も対象にする | Compose の wheel が従うべきアクセシビリティの規約を定め、その規約に従う wheel を操作します。 | Compose には標準の wheel がないので、規約は Bajutsu が独自に作り、アプリに採用してもらうものになります。それは独自の得失を持つ別の設計で、`NumberPicker` の不足を埋めるより大きな作業です。 |
| Android 用に別のステップ名を設ける | `setNumberPickerValue` を追加し、`setPickerValue` は iOS 専用のままにします。 | 「wheel を名前の付いた行まで動かす」という意図は、両プラットフォームで同じです。名前を分けても表現力は増えず、共通のシナリオが分断されるだけです。BE-0356 が「異なる部品には異なるステップ名を」と判断したのも、同じ考え方によります。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] trait の対応づけ：`NumberPicker` のノードに `pickerWheel` も付けて `Trait.PICKER_WHEEL` として定義し、ユニットテストと conformance のテストを加える
- [x] 常駐サーバー：stale、not-a-picker-wheel、picker-value-absent、picker-walk-exhausted の各ステータスと端での確認の読み直しを持つ `/setPickerValue` エンドポイント、数値の向きの手がかり、行数の上限
- [x] ホスト側のドライバー：専用のタイムアウトを持つ通信路のクライアント、`AdbDriver.set_picker_value`、`PICKER_WHEEL` capability、preflight の説明文、`Driver.set_picker_value` のコメントと preflight の docstring、それらのユニットテスト、サーバーの走査の定数と照合する予算のテスト
- [x] コード生成：UI Automator 向けの生成器が出力する `// TODO`
- [x] showcase：Views 版と Compose 版のピッカー画面と、`android-e2e.yml` の smoke ジョブへの2つのシナリオの追加（Android の Makefile のシナリオ一覧に `picker_wheel` を加える）
- [x] ドキュメント：`scenarios`、`drivers`、`platform-parity`、`selectors`、`architecture` の英日両方

ログ：

- 2026-11-11 — 単位 1〜6 をまとめて着地させました（[#2412](https://github.com/bajutsu-e2e/bajutsu/pull/2412)）。
  API 34 arm64 のエミュレータで、Compose 版と Views 版の両方のショーケースアプリで `picker_wheel` が通りました。
  詳細設計から外れた点が5つあります。
  - `NumberPicker` のノードは `resource-id` を持ちません。アクセシビリティのプロバイダが設定しないためです。
    そこでショーケースの画面では、ホイールを囲むコンテナに識別子を付けました。シナリオは iOS と同じく、
    `within` と `pickerWheel` の組み合わせでホイールを指します。
  - 1 行ごとの待機は、値が変わるまで 20 ms 間隔でホイールを読み直す方式にしました。アイドル待ちでは間に合いません。
    アイドル待ちは 1 行分のアニメーション全体より長い無音の時間を必要とするからです。
    アイドル待ちのままでは、25 行の walk で変化のない読み取りが2回続き、端に着いたと誤判定しました。
  - 最初の実機実行では、1 行あたり 142〜1469 ms（中央値 625 ms、38 行）かかりました。
    そのため 1 行の予算は 300 ms ではなく 1,500 ms から始めます。walk の期限は 600 秒、ホストのタイムアウトは
    613 秒になります。サーバーが先に応答するので、このタイムアウトが発火するのは通信路の障害のときだけです。
  - 常駐サーバーとの通信路の配線として、`ResidentChannel`、`ResidentServer`、`make_driver`、Android の環境に
    `set_picker_value` の呼び出し口を加えました。詳細設計には挙がっていなかった変更です。web バックエンドと
    WebView コンテキストの拒否メッセージも、このステップを iOS 専用とは書かなくなりました。
  - セルフレビューを受けて、walk を設計より堅くしました。行の予算 2 回分で行が変わらないときは、端とみなさず
    `picker-walk-exhausted` で失敗させます。そこで折り返すと、ホイールが直後に離れる行を着地と報告しうるからです。
    着地した行は 400 ms 保たれることを確かめます。設計にあるステータスに2つ加えました。どちらの向きにも
    スクロールできないホイールには `picker-immovable` を返します。端末側でホイールの識別情報の全体に一致する
    ノードが2つあれば、`picker-wheel-ambiguous` を返します。
    ホスト側は `tap` と同じく、ほかの要素に覆われたホイールを先に拒否します。

## 参考

- [BE-0356](../BE-0356-xcuitest-picker-wheel-adjust/BE-0356-xcuitest-picker-wheel-adjust-ja.md)：iOS の
  `setPickerValue` ステップと、そのスキーマ、`PICKER_WHEEL` capability です。本項目はこれらを再利用します。
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)：
  `/setPickerValue` エンドポイントを追加する常駐 UI Automator サーバーです。
- [BE-0476](../BE-0476-android-non-ascii-text-entry/BE-0476-android-non-ascii-text-entry-ja.md)：
  `/type` エンドポイントです。常駐サーバーがアクセシビリティの操作でノードを動かす、先行する例です。
- [`docs/ja/platform-parity.md`](../../docs/ja/platform-parity.md)：本項目が `pickerWheel` の行を埋める
  パリティ表です。
- [`docs/ja/scenarios.md`](../../docs/ja/scenarios.md#setpickervalue)：`setPickerValue` ステップのリファレンスです。
