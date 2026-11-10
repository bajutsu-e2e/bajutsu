[English](BE-XXXX-android-device-control-parity.md) · **日本語**

# BE-XXXX — simctl にしかないデバイス操作を Android でも使えるようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-android-device-control-parity-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control-ja.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities-ja.md), [BE-0035](../BE-0035-device-control-primitives/BE-0035-device-control-primitives-ja.md), [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md) |
<!-- /BE-METADATA -->

## はじめに

iOS では、Bajutsu は `simctl` を通じて Simulator のデバイスの状態を操作します。Android
バックエンドが adb で操作できるのは、その一部にとどまります。この項目は、adb だけで埋められる4つの差を
埋めます。どれも、テスト対象アプリの中に協調するコードを必要としません。

- `background` ステップは、`KEYCODE_HOME` のキーイベントでアプリをホーム画面へ送ります。
- `overrideStatusBar` と `clearStatusBar` の両ステップは、System UI のデモモードを操作します。
- 新しい設定 `runsOn.android.locale` は、`cmd locale set-app-locales` でアプリ単位の言語を
  適用します。このコマンドは Android のアプリケーションプログラミングインタフェース（API）
  レベル 33 から使えます。
- エビデンスの種類 `appTrace` は、アプリ自身のログ行を `logcat` からタグで絞り込んで読みます。

adb ドライバは、`background`、ステータスバーの2つのステップ、`appTrace` を既存のケイパビリティ
トークンで宣言します。プリフライトは、iOS と同じく Android でもこれらを通します。ロケールは
トークンを持たない設定の項目で、プリフライトが API レベルと突き合わせて検査します。これらのステップを使うシナリオは、両方のプラットフォームでそのまま
動きます。現状では、同じシナリオは Android でデバイスの作業より前に失敗します。

## 動機

Bajutsu は、1つのシナリオ言語を複数のプラットフォームで共有します。一度書いたシナリオは、iOS
Simulator でも Android エミュレータでも動くべきです。アプリごとの違いは設定に置きます（基本原則
の3）。デバイス操作のステップは、現状この約束を破っています。adb バックエンドが宣言するデバイス操作のトークンは、
`setLocation`、`clipboard`、`foreground` の3つだけです。宣言は
`bajutsu/common/drivers/adb/adb_driver.py:1947` にあります。
Android の `DeviceControl` は、残りの操作で `UnsupportedAction` を送出します。送出箇所は
`bajutsu/common/platform_lifecycle/device_control.py:117-133` です。そのためプリフライトは、
Android で `background` や `overrideStatusBar` を使うシナリオを拒否します。

この拒否は、[BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control-ja.md)
の主張に基づいています。その主張は、これらの操作には「エミュレータ上に忠実な対応物がない」という
ものです。2つの操作については、この主張はもう成り立ちません。`KEYCODE_HOME` はアプリを終了させずに
バックグラウンドへ送ります。これは iOS のホームボタンとまったく同じ意味です。System UI のデモモードは、
スクリーンショットのために時計、バッテリー、電波のアイコンを固定します。これは `simctl status_bar`
の目的そのものです。その後、Android には `foreground` も加わりました
（[BE-0447](../BE-0447-install-app-step/BE-0447-install-app-step-ja.md)）。いまや対のうち欠けているのは
`background` だけです。

ステップの一覧の外にも、差が2つあります。1つ目はロケールです。iOS は `runsOn.ios.locale` を起動時に
適用します。Android の設定には、ロケールの項目がそもそもありません
（`bajutsu/common/config/schema/platform/android.py`）。多言語対応した Android アプリは、
エミュレータのイメージが持つ言語でしか動きません。2つ目は、エビデンスの種類 `appTrace` です。
`appTrace` は、アプリが出力する「`<name>` started」と「`<name>` finished」のログ行を、時間区間に
変換します。adb ドライバは、この種類に対して区間を返しません（`AdbDriver.driver_interval`）。
アプリが印を出力していても、Android のレポートにはアプリ自身の所要時間が載りません。

シナリオの作者は、2つのどちらかの方法でこれらの差を回避しています。1つは、ひとつの操作の流れを
iOS 用のシナリオと Android 用の複製に分ける方法です。もう1つは、共有シナリオからこれらのステップを
外す方法です。前者は複製を保守する手間を生み、後者は iOS 側のテストを弱めます。この項目の効果は、
出荷後に直接確かめられます。showcase の共有シナリオは、`background`、`overrideStatusBar`、
デフォルト以外のロケールを使えるようになります。それでも、Android エミュレータのレーンでそのまま動きます。
アプリが印を出力していれば、Android のレポートにも `appTrace` の節が載ります。

## 詳細設計

4つの追加は、同じ形をとります。どれも、純粋関数としての adb コマンドビルダー、`adb.Env` の
メソッド、そして iOS 側にすでにある配線を加えます。ステップのシナリオスキーマは変わりません。
`run` の経路にモデルの呼び出しを持ち込むものもありません。

### Home キーによる background

adb の `DeviceControl.home()` は、`input keyevent KEYCODE_HOME`（キーコード 3）を送ります。新しい定数
`KEYCODE_HOME` は、`bajutsu/common/backend_cli/adb/_shared.py` の `KEYCODE_BACK` の隣に置きます。
あわせて、adb ドライバの `CAPABILITIES` に `Capability.DC_BACKGROUND` を加えます。

Home キーはアプリのプロセスを生かしたままにします。これは iOS の意味（「終了させずにバックグラウンド
へ送る」）と一致します。その結果として、既存の2つの振る舞いは新しいコードなしで成り立ちます。

- アプリ終了の検知は、アプリのプロセスと終了記録を読みます。バックグラウンドのアプリはプロセスを
  保つので、検知は終了を報告しません。
- 後続の `foreground` は、アプリを前面へ戻します。その間にシステムがプロセスを回収していた場合、
  `foreground` はアプリを起動し直します。`installApp` の後と同じ振る舞いです。

`input keyevent` は、画面の遷移が終わる前に戻ります。そのためステップは、テスト対象アプリが前面の
アクティビティでなくなるまで条件で待ち、そうならなければ失敗を明示します。この待機は、`foreground`
がすでに行っている待機の逆にあたります（[BE-0447](../BE-0447-install-app-step/BE-0447-install-app-step-ja.md)）。
待機がなければ、遅いエミュレータでは `background` 直後の `visual` の検査がアプリを写しうるからです。

`background` の後、デバイスにはランチャーが表示されます。テスト対象アプリの要素を指すセレクタは、
何も見つけません。iOS で SpringBoard が前面にあるときと同じです。

### デモモードによる overrideStatusBar と clearStatusBar

Android の System UI は、スクリーンショットのためにデモモードをブロードキャストのインタフェース
として提供しています。root は要りません。制御側は、まず `settings put global sysui_demo_allowed 1`
でデモモードを許可します。続けて `am broadcast -a com.android.systemui.demo -e command enter`
を送ります。その後、ステップが指定した項目ごとにコマンドを1つずつ送ります。ステップの項目は
`simctl status_bar` に倣っており、対応は次の表のとおりです。

| ステップの項目 | iOS での範囲 | デモモードのコマンド | 対応の規則 |
|---|---|---|---|
| `time` | 自由な文字列 | `clock -e hhmm HHMM` | `H:MM` か `HH:MM` だけを受け付けます。コロンを除き、時を2桁にそろえます |
| `batteryLevel` | 0〜100 | `battery -e level N` | そのまま渡します |
| `batteryState` | `charging`、`charged`、`discharging` | `battery -e plugged true\|false` | `charging` と `charged` は `true` に、`discharging` は `false` に対応させます |
| `cellularBars` | 0〜4 | `network -e mobile show -e level N` | そのまま渡します |
| `wifiBars` | 0〜3 | `network -e wifi show -e level N` | `round(N × 4 / 3)` で変換します。0→0、1→1、2→3、3→4 となり、iOS で最大の電波は Android でも最大のままです |

デモモードに入るときは、`notifications -e visible false` も送ります。iOS のステータスバーには
通知アイコンが出ません。アイコンの列を隠せば、実行ごとのスクリーンショットが一致します。
`clearStatusBar` は `-e command exit` を送り、実際のステータスバーに戻します。

デモモードと `simctl status_bar` は、指定しなかった項目の扱いも異なります。デモモードに入ると、すべてのアイコンが
固定されます。そのため、ステップが指定しなかった項目は、実際の値ではなくデモモード固有の値になります。
結果はやはり決定的であり、ステップの目的は満たします。シナリオのリファレンスに、この違いを記載します。

Android が受け付ける `time` は、iOS より狭い範囲です。Android のターゲットでは、`time` が `H:MM`
でも `HH:MM` でもない `overrideStatusBar` を、プリフライトが失敗させ、そのステップを示します。この検査は
デバイスの作業より前に走ります。同じ入力に対して、adb の制御側も最後の砦として `UnsupportedAction`
を送出します。

同じプリフライト検査と最後の砦は、表の範囲外の値もすべて扱います。対象は、0〜100 を外れる
`batteryLevel`、0〜4 を外れる `cellularBars`、0〜3 を外れる `wifiBars`、表にない `batteryState` です。
iOS では、`simctl status_bar` がこうした値を拒否してステップを失敗させます。一方 `am broadcast` は、
付加情報の中身にかかわらず成功を返し、System UI は表示できない値を無視します。検査がなければ、
ステップは成功したまま、ステータスバーは固定されません。この対応を実装したうえで、adb ドライバは
`Capability.DC_STATUS_BAR` を加えます。

iOS の上書きと同じく、デモモードは `clearStatusBar` を実行するまでデバイスに残ります。Android だけに
後片付けを加えることはせず、両方のプラットフォームでこの振る舞いを保ちます。

### アプリ単位のロケールによる runsOn.android.locale

`AndroidDestination` に `locale: str | None` を加えます。iOS と同じく、シナリオは
`preconditions.device.android.locale` でこの値を置き換えられます。値の書式と検証は
`runsOn.ios.locale`（`ja_JP`）と同じです。Android の環境は、値を Best Current Practice 47
（BCP 47）形式の言語タグ（`ja-JP`）に変換します。

環境は、起動のたびに `cmd locale set-app-locales <package> --user current --locales <tag>` を
実行します。続けて `cmd locale get-app-locales` で値を読み戻します。値が一致しなければ、パッケージと
ロケールを示す `DeviceError` で起動を失敗させます。この設定はアプリの再起動を越えて残り、繰り返しても
シェルの呼び出し1回で済みます。そのため環境は、デバイスがすでに設定を持つかを追跡せず、起動のたびに
適用します。

iOS と違い、Android で `locale` を指定しなければ言語を設定しません。iOS は組み込みの `en_US` に
フォールバックします。Android にデフォルト値を設けると、既存のすべての Android の実行が変わり、すべての
デバイスに API レベル 33 以上を求めることになります。そのため、未指定ならデバイス自身の言語を
保ちます。現状と同じ振る舞いです。

この約束を守るには、リセットが1つ要ります。アプリ単位の設定はアプリの再起動を越えて残り、プールは
エミュレータを複数のシナリオで使い回します。リセットがなければ、`ja_JP` を指定したシナリオの次に
走るシナリオは、ロケールを指定していなくても日本語のまま動きます。`reinstall: overwrite` の経路は
アンインストールも `pm clear` も省くので、そこでは確実に漏れます。そこで API レベル 33 以上では、
ロケール未指定のときも起動前に `cmd locale set-app-locales <package> --user current --locales ""`
を実行し、アプリ単位の設定を消します。API レベル 33 未満ではアプリ単位のロケールを設定できず、
前の実行が残した設定もないので、環境は何も発行しません。この判断のため、環境は `apiLevel` の値に
かかわらず、起動のたびに `_read_api_level` でレベルを読みます。

レベルを読めないときの扱いは、実行が Android のロケールを指定しているかで分かれます。指定している
実行は、後述の第2段階と同じく `DeviceError` で起動を失敗させます。指定していない実行は、リセットを
省いて警告を記録します。これにより、`_read_api_level` が現在持つ寛容さを保ちます。現在は、レベルを
読めなくても終了の検知が弱まるだけで、シナリオは中断しません
（[BE-0424](../BE-0424-app-crash-diagnostics/BE-0424-app-crash-diagnostics-ja.md)）。リセットを
ロケールを指定した実行だけに絞ることはできません。エミュレータは1回の実行より長く生きるので、
前に起動した Bajutsu が、ロケールを指定しない実行にアプリ単位のロケールを残しうるからです。

アプリ単位のロケールが届くのは、アプリだけです。実行時の権限を求めるダイアログのようなシステムの
ダイアログは、エミュレータ自身の言語のままです。iOS はさらに進んで、Simulator のシステム言語も
固定します（[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)）。
Android で同じことをするには、root とランタイムの再起動が要ります。その選択肢は「検討した代替案」の
節で比べます。設定のリファレンスには、この違いを記載します。システムのダイアログのボタンには `id`
のセレクタを勧めることも、あわせて記載します。ダイアログの文言はイメージの言語に従うからです。

`cmd locale` は API レベル 33 から使えます。プリフライトはプールがデバイスを確保する前に走るので、
デバイスのレベルを読めません。そこで検査を2段階で行います。

1. **設定から、デバイスの作業の前に検査します。** `runsOn.android.apiLevel` が 33 未満のレベルを
   許す場合、プリフライトは `locale` を指定したターゲットを失敗させます。メッセージは両方の項目を
   示し、`apiLevel` を 33 以上に絞るよう促します。この検査は、`bajutsu/common/config/preconditions.py`
   にある既存の `seedPhotos` の検査の隣に置きます。
2. **デバイスから、最初のステップの前に検査します。** `apiLevel` が未指定の場合、環境は起動時に
   `ro.build.version.sdk` を読みます。`_read_api_level` がすでに読んでいる値です。レベルが 33 未満
   なら、ロケールとレベルを示す `DeviceError` で起動を失敗させます。

### タグで絞り込んだ logcat による appTrace

iOS の `appTrace` は、アプリの `os_log` のサブシステムをストリームで受け取ります。`parse_app_trace`
が「`<name>` started」と「`<name>` finished」を組にして区間にします。Android 版は、1つのタグに
絞り込んだ `logcat` をストリームで受け取ります。

```
adb -s <serial> logcat -v year,usec,zone -T "<MM-DD hh:mm:ss.mmm>" -s <tag>:V
```

タグは `--log-subsystem` の値で、指定がなければ Android のパッケージ名です。`_log_subsystem_default`
は、iOS にバンドル ID を返すのと同じく、Android のターゲットにはパッケージ名を返します。絞り込みは
プロセス ID ではなくタグに基づきます。そのため、`relaunch` を挟んでもストリームは途切れません。

`-T` の境界は、ストリームの開始時に読んだデバイスの時計の時刻です。読み取りには
`adb.launch_marker_cmd` が返す `logcat -t` 形式の表記を使い、`logcat_crash_dump_cmd` も同じ方法で
この表記を渡しています。どの行がストリームより前かは、デバイス側が決めます。ホスト側で切り捨てると、
デバイスの時刻とホストの時刻を比べることになり、時計のずれで本物の行を落とすか、古い `started` を
残してしまいます。

新しい `parse_logcat_app_trace` は、各行の `year,usec,zone` 形式のタイムスタンプとメッセージを
読みます。そのうえで既存の `_BEGIN` と `_END` のパターンを再利用するので、両プラットフォームが同じ印を
認識します。アプリは `android.util.Log` で印を出力します。たとえば `Log.i(tag, "checkout started")`
のように書きます。

シンクは、区間の種類を `driver_interval(kind, path)` でドライバ側のレーンに渡します。この継ぎ目は、
`appTrace` に必要なサブシステムも、解析結果の JavaScript Object Notation（JSON）ファイルのパスも
運びません。この項目は、2つをキーワード引数として継ぎ目に加えます。同じ継ぎ目を実装する Playwright と XCUITest live のドライバは、それらを無視します。adb ドライバは `appTrace` に対して新しい区間を返し、`Capability.APP_TRACE` を加えます。

### 作業分解（MECE）

1. **`background`**：定数 `KEYCODE_HOME`、adb のビルダー、`adb.Env.home()` を加えます。制御側を
   配線し、`DC_BACKGROUND` を宣言します。高速ゲートのテストでは、次の点を確かめます。
   - コマンドの形を確かめます。
   - adb のプリフライトが `background` を通すことを確かめます。
   - アプリがバックグラウンドにある間、終了の検知が反応しないことを確かめます。
   - アプリが前面から離れないとき、ステップが失敗することを確かめます。
2. **ステータスバー**：デモモードのビルダーと、項目の対応を純粋関数として加えます。制御側を配線し、
   Android 用の値のプリフライト検査を加え、`DC_STATUS_BAR` を宣言します。高速ゲートのテストでは、
   次の点を確かめます。
   - 対応表の各行を確かめます。
   - プリフライトが `time` の各形式と範囲外の各値を失敗させることを確かめます。
   - `clearStatusBar` が `exit` コマンドを送ることを確かめます。
3. **ロケール**：`runsOn.android.locale` と `preconditions.device.android.locale` の項目を加え、
   実効設定へ解決します。設定と読み戻しを行う起動経路と、2段階の API レベル検査を加えます。高速
   ゲートのテストでは、次の点を確かめます。
   - BCP 47 形式への変換を確かめます。
   - 読み戻しの不一致で起動が失敗することを確かめます。
   - API レベル検査が両方の段階で失敗することを確かめます。
   - ロケール未指定のとき、API レベル 33 以上ではリセットだけを発行し、33 未満では何も発行しない
     ことを確かめます。
   - レベルを読めないとき、ロケールを指定した実行は失敗し、指定していない実行は警告だけを記録する
     ことを確かめます。
4. **`appTrace`**：タグで絞り込む `logcat` のビルダーと `parse_logcat_app_trace` を加えます。
   `driver_interval` の継ぎ目を拡張し、Android のデフォルトのサブシステムを定め、`APP_TRACE` を宣言します。
   高速ゲートのテストは、記録した `logcat` の標本を区間に解析します。Playwright と XCUITest live のドライバが新しい引数を無視することと、ストリームがデバイスの時計の
   境界から始まることも確かめます。
5. **ドキュメント**：次の各ページを、`docs/ja/` の対訳とあわせて更新します。
   - `docs/scenarios.md`：新しいステップの Android 対応と、デモモードとの違いを記載します。
   - `docs/configuration.md`：`runsOn.android.locale` と、システムのダイアログについての注記を記載します。
   - `docs/evidence.md`：Android の `appTrace` を記載します。
   - `docs/architecture.md`：adb バックエンドのケイパビリティを並べた箇所をすべて更新します。
6. **実機での確認**：Android エミュレータのレーンに showcase のシナリオを加えます。このシナリオは、
   デフォルト以外のロケールのもとで `background`、`foreground`、`overrideStatusBar`、`clearStatusBar`
   を実行します。`visual` の検査で、ステータスバーが固定されたことを確かめます。

## 検討した代替案

- **ランチャーの `HOME` インテントを `am start` で送ってバックグラウンドにする案**：採りません。
  インテントは、アクティビティマネージャ独自の起動規則を通って同じランチャーに届きます。キーイベントは
  利用者がボタンを押したときと同じ経路をたどるので、ステップの意味が iOS のホームボタンと一致します。
- **System UI のリソースを書き換える案、または root 専用のオーバーレイを使う案**：採りません。どちらも
  root か独自のイメージを必要とします。デモモードは System UI に同梱されて
  おり、どちらも必要としません。
- **`setprop persist.sys.locale` とランタイムの再起動でシステム全体のロケールを変える案**：今回は
  採りません。再起動には root が要り、変更のたびに数十秒かかります。Play ストアを含むエミュレータの
  イメージでは失敗します。その代わり、システムのダイアログの文言を予測できるようになります。作者が
  Android のシステムのダイアログに文言のセレクタを必要とするなら、この利点は別の項目に値します。
- **Android でもロケール未指定のときに `en_US` をデフォルトにする案**：採りません。デフォルト値は既存のすべての
  Android の実行を変えます。すべてのデバイスに API レベル 33 以上を求めることにもなります。
- **API レベルをデバイスの起動時だけに検査する案**：採りません。API の範囲を書いた設定でも、矛盾に
  気づくのはプールがデバイスを確保して起動した後です。先に設定を読めば、その場合は数ミリ秒で
  失敗させられます。
- **`appTrace` をプロセス ID（`logcat --pid`）で絞り込む案**：採りません。プロセス ID は `relaunch`
  のたびに変わるので、シナリオの途中でストリームが途絶えます。タグなら再起動を越えて残ります。
- **`push` と `clearKeychain` もこの項目で扱う案**：範囲の都合で採りません。どちらも、クリップボード
  と同じくアプリのプロセス内に受け手を必要とします。Android のアプリ内ライブラリを拡張する別の項目で
  扱います。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `KEYCODE_HOME` による `background` と、`DC_BACKGROUND` の宣言
- [ ] デモモードによる `overrideStatusBar` と `clearStatusBar`、`DC_STATUS_BAR` の宣言
- [ ] アプリ単位のロケールによる `runsOn.android.locale` と、2段階の API レベル検査
- [ ] タグで絞り込んだ `logcat` による `appTrace` と、`APP_TRACE` の宣言
- [ ] `docs/` と `docs/ja/` のドキュメント
- [ ] Android エミュレータのレーンでの実機確認

## 参考

- [BE-0211 Android のデバイス制御](../BE-0211-android-device-control/BE-0211-android-device-control-ja.md)：
  これらの操作を Android で非対応と最初に定めた項目です。
- [BE-0212 deviceControl ケイパビリティの操作単位への分割](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities-ja.md)：
  この項目が宣言するトークンを定めた項目です。
- [BE-0035 デバイス制御のステップ](../BE-0035-device-control-primitives/BE-0035-device-control-primitives-ja.md)：
  この項目が移植する iOS のステップを定めた項目です。
- [BE-0320 iOS のシステムアラートのロケール決定性](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)：
  Android が合わせない、iOS のシステム言語の固定を定めた項目です。
- Android Open Source Project、[System UI demo mode](https://android.googlesource.com/platform/frameworks/base/+/master/packages/SystemUI/docs/demo_mode.md)
- Android Developers、[Per-app language preferences](https://developer.android.com/guide/topics/resources/app-languages)
