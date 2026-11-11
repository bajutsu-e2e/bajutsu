[English](../platform-parity.md) · **日本語**

# iOS と Android のドライバの機能差

Bajutsu は、プラットフォームごとに別の[バックエンド](glossary.md#driver-backend-actuator-platform)で動きます。
iOS は `xcuitest` で、Android は `adb` で動きます。2つのバックエンドが対応する機能には違いがあります。
このページは、2つのバックエンドの差をすべて挙げます。差はそれぞれ、今後埋める予定の不足か、
そのプラットフォームに不要な機能のどちらかです。ページには、どちらにあたるかも記録します。
このページは、不足を埋める作業の一覧として使います。

1つ目の表は、テストで正しさを保ちます。テストは、表の印を各ドライバが宣言する能力と突き合わせます。
そのため、このページを書き換えずに不足が増えたり埋まったりすることはありません。

## 3つの表が扱う範囲

**能力**（capability）は、バックエンドが `Driver.capabilities()` で宣言する名前付きの機能です。
たとえば `multiTouch` や `deviceControl.push` があります。実行前の事前チェック（preflight）は、
デバイスを確保する前に走ります。ここで止まるのは、ゲート対象の構文を、その能力を持たないバックエンドで使うシナリオです。
すべての能力がゲート対象ではありません。`conditionWait`、`network`、`semanticTap`、`webView`、区間の証跡の能力は、
コアが別の手段で満たすので、意図的にゲートしていません。
能力の一覧とそれぞれの意味は、[ドライバ](drivers.md#能力capability)に載っています。

3つの表は、差の種類で分かれています。

- **能力の対応表**：すべての能力を並べ、バックエンドごとに印を1つ付けます。この表はテストで検証します。
- **起動時の設定**：アプリの起動前に実行が整える設定を並べます。この表もテストで検証します。
- **振る舞いの差**：どの能力の名前にも表れない差を並べます。両方のバックエンドで宣言済みの機能でも、
  片方だけ振る舞いの異なることがあります。この表は人が手で保守します。

iOS の列は iOS シミュレータを、Android の列は Android エミュレータを指します。
iOS の実機では、`simctl` が提供する能力がなくなります。この絞り込みは
[iOS を実機とデバイスクラウドで動かす](ios-device-cloud.md)で説明しています。

## 印の読み方

能力の対応表の各セルには、次の4つの印のどれか1つだけを書きます。詳しい説明は備考の列に書きます。

| 印 | 意味 |
|---|---|
| ✅ | バックエンドがその能力を宣言しています。 |
| gap | まだ宣言していませんが、プラットフォームとしては対応できます。備考に実現の方法を書きます。 |
| n/a | そのバックエンドには不要か、プラットフォームとして提供できません。備考に理由を書きます。 |
| per-run | 静的には宣言していませんが、アプリ側の準備があれば実行ごとに付与されます。 |

## 能力の対応表

<!-- parity-table -->
| 能力 | iOS | Android | 備考 |
|---|---|---|---|
| `query` | ✅ | ✅ | |
| `elements` | ✅ | ✅ | |
| `screenshot` | ✅ | ✅ | |
| `semanticTap` | ✅ | gap | adb は、ホスト側の問い合わせで特定した要素の枠の中心をタップします。デバイス上のステップ実行器（[BE-0410](../../roadmaps/BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)）が実現の道筋です。 |
| `conditionWait` | ✅ | gap | adb は、Bajutsu を動かすマシンから要素ツリーを繰り返し読んで待ちます。BE-0410 で待ちをデバイス側へ移します。 |
| `network` | n/a | n/a | この能力は、プロセス内でのネイティブな通信観測を指し、web バックエンドだけが持ちます。モバイルの2つのバックエンドは、アプリ側のコレクタで `request` アサーションに答えます。 |
| `video` | ✅ | ✅ | |
| `deviceLog` | ✅ | ✅ | |
| `appTrace` | ✅ | ✅ | iOS はアプリの `os_log` を取り込みます。Android は `logcat` からアプリ自身のログ行をタグで絞って読みます（[BE-0478](../../roadmaps/BE-0478-android-device-control-parity/BE-0478-android-device-control-parity-ja.md)）。 |
| `multiTouch` | ✅ | ✅ | Android は常駐 UI Automator サーバーで 2 本の指を注入します。サーバーがないときは、root 化した端末が必要です。 |
| `textSelection` | ✅ | ✅ | Android 12 以降が必要です。振る舞いの差の表を参照してください。 |
| `webView` | per-run | gap | iOS では、アプリが BajutsuKit の WebView ブリッジを組み込んでいるときに付与されます。Android にはブリッジがありません。Chrome の WebView デバッグ用ポートが実現の候補です。 |
| `selectOption` | n/a | n/a | web ページの `<select>` 要素を設定する能力で、web バックエンドだけが持ちます。 |
| `urlNavigation` | n/a | n/a | シナリオの途中でブラウザに web アドレスを開かせる能力です。モバイルの2つのバックエンドは、ディープリンクを起動時の前提条件として開きます。 |
| `elementAttribute` | n/a | n/a | web ページの要素の属性を読む能力で、web バックエンドだけが持ちます。 |
| `handleSystemAlert` | ✅ | n/a | Android のシステムダイアログは取得した要素ツリーに現れるので、通常の `tap` で操作できます。 |
| `resolveAlerts` | ✅ | n/a | `handleSystemAlert` と同じ理由です。 |
| `pickerWheel` | ✅ | gap | Android で近いコントロールは `NumberPicker` と、スピナー表示の `DatePicker` です。値を直接設定する手段はまだありません。 |
| `appContext` | ✅ | gap | UI Automator はどのアプリのウィンドウも読めます。`am start` で別のパッケージを前面に出せます。 |
| `selectPhotos` | ✅ | gap | Android の写真選択画面はシステム UI で、タップで操作できます。位置を指定して写真を選ぶステップはまだありません。iOS では、Apple Silicon のシミュレータに限り実行時にこの能力を外します。 |
| `handleTipkitTip` | ✅ | n/a | TipKit は iOS のフレームワークです。 |
| `handleNotificationBanner` | ✅ | gap | Android のヘッドアップ通知は、SystemUI のノードとして要素ツリーに現れます。 |
| `deviceGroup` | ✅ | ✅ | |
| `deviceControl.setLocation` | ✅ | ✅ | Android はエミュレータのコンソール経由でしか設定できません。振る舞いの差の表を参照してください。 |
| `deviceControl.clipboard` | ✅ | ✅ | Android ではアプリに BajutsuAndroid ライブラリが必要です。振る舞いの差の表を参照してください。 |
| `deviceControl.push` | ✅ | gap | クリップボードと同じく、BajutsuAndroid ライブラリがアプリの中から通知を出せば実現できます。 |
| `deviceControl.clearKeychain` | ✅ | n/a | Android には iOS の Keychain にあたる仕組みがありません。アプリの Android Keystore の鍵は、クリーンな再インストールでデータと一緒に消えます。 |
| `deviceControl.background` | ✅ | ✅ | Android はホームキー（`KEYCODE_HOME`）を送ります（BE-0478）。アプリのプロセスは生きたままです。その後、アプリが前面のアクティビティでなくなるまで待ちます。 |
| `deviceControl.foreground` | ✅ | ✅ | |
| `deviceControl.statusBar` | ✅ | ✅ | Android は SystemUI のデモモードを使います（BE-0478）。ステップが指定しない項目はデモモードの固定値になります。`time` は `H:MM` か `HH:MM` の形だけを受け付けます。 |
| `deviceControl.permissions.location` | ✅ | ✅ | |
| `deviceControl.permissions.camera` | ✅ | ✅ | |
| `deviceControl.permissions.microphone` | ✅ | ✅ | |
| `deviceControl.permissions.contacts` | ✅ | ✅ | |
| `deviceControl.permissions.photos` | ✅ | ✅ | |
| `deviceControl.permissions.calendar` | ✅ | ✅ | |
| `deviceControl.permissions.notifications` | n/a | ✅ | iOS は通知の許可を、`simctl privacy` が編集するプライバシーのデータベースの外で管理しているので、このコマンドでは操作できません。代わりに `handleSystemAlert` ステップで許可ダイアログに答えます。 |

## 起動時の設定

**起動時の設定**は、アプリが起動する前にデバイスへ届く設定です。たとえば、クリーンな再インストールや、写真ライブラリへの画像の事前投入があります。
プラットフォームのルートが適用できる設定は、対応表 `settings_support.py` で管理しています。
ルートが適用できない設定を必要とする実行は、デバイスでの作業より前の段階で失敗します。
ターゲットは、理由を添えて設定を `runsOn.ignoredSettings` に挙げれば、その設定なしで実行を進められます。

権限の設定は能力の対応表で扱うので、この表には含めません。✅ は、ルートがその設定を適用することを表します。

<!-- settings-table -->
| 設定 | iOS | Android | 備考 |
|---|---|---|---|
| `erase` | ✅ | ✅ | Android にはデバイス全体を初期化する手段がありません。`erase` はアプリをアンインストールし、`pm clear` でデータを消します。 |
| `install` | ✅ | ✅ | |
| `seedPhotos` | ✅ | ✅ | Android では、共有ストレージにある Bajutsu 専用のディレクトリを空にしてから画像を置き、再スキャンします（[BE-0479](../../roadmaps/BE-0479-android-seed-photos/BE-0479-android-seed-photos-ja.md)）。 |
| `locale` | ✅ | ✅ | Android は `runsOn.android.locale` をアプリごとの言語設定として適用します。Android 13（API レベル 33）以降が対象です（BE-0478）。システムダイアログはエミュレーターの言語のままです。 |
| `launchEnv` | ✅ | ✅ | Android では、変数を intent の extra としてアプリに渡します。 |
| `launchArgs` | ✅ | n/a | `am start` は起動引数を受け取りません。アプリへ値を渡すには `launchEnv` を使います。 |
| `deeplink` | ✅ | ✅ | |

## 振る舞いの差

次の差は、ここまでの2つの表には表れません。どれも、両方のバックエンドが宣言している機能の内側にあるか、
2つの表が扱う範囲の外にあります。

| 振る舞い | iOS | Android | 現時点での影響 |
|---|---|---|---|
| 入力欄の文字 | 要素の `value` に文字が入ります。 | 文字は `label` に入り、`value` には `content-desc` が入ります。 | 入力欄に対する `value` アサーションでは文字を読めません。`clear` ステップは先に `value` を読み、空なら何もしません。 |
| `select` と `copy` | XCUITest がネイティブに選択とコピーをします。 | adb は `input keycombination` で Ctrl+A と Ctrl+C を送ります。Android 12 以降が必要です。 | Android の end-to-end（E2E）レーンが、Compose と Views の両方でこの2つを実行します。コピーした文字列はクリップボードから読み戻して確かめます。 |
| `setLocation` | シミュレータ上で `simctl` が設定します。 | エミュレータのコンソールが設定します。 | どちらのバックエンドも、まだ実機では位置を設定できません。iOS 側は [BE-0475](../../roadmaps/BE-0475-ios-runner-set-location/BE-0475-ios-runner-set-location-ja.md) が扱います。 |
| クリップボードのステップ | `simctl` がペーストボードを読み書きします。 | アプリ内の受信部が処理します。アプリが BajutsuAndroid をリンクしている必要があります。Android 10 以降は、フォーカスを持つアプリしかクリップボードを読めません。 | BajutsuAndroid のないアプリでは、クリップボードのステップはすべて失敗します。アプリがバックグラウンドにあるときの読み取りも失敗します。 |

## テストによる表の検証

`tests/test_platform_parity.py` は、`parity-table` の目印より後の行を読みます。
読む対象は、このページと英語版のページです。`make check` のたびに、次の4点を確かめます。

- 行は、能力の名前を過不足なくすべて挙げています。
- セルが ✅ になるのは、バックエンドの `CAPABILITIES` 定数がその能力を含むときだけです。
- ✅ 以外の印には、必ず備考が付いています。
- 英語版と日本語版で、印が一致しています。

バックエンドが新しい能力を宣言するように変わると、セルを ✅ に直すまでテストは失敗します。
逆向きも同じです。能力の名前を新しく足したときは、行を足すまでテストは通りません。

`settings-table` の目印より後の行も、同じ方法で検証します。照合先は、設定の対応表にある `ios-simulator` と `android` のルートです。
ルートが設定を適用するか、すでに満たしているときに、セルは ✅ になります。
設定の名前を新しく足したときは、行を足す必要があります。ルートが新しく設定を適用するようになったときは、セルを ✅ に直す必要があります。

同じテストで、[ドライバ](drivers.md#能力capability)のページにある4つのバックエンドの能力表も検証します。
この表にも、すべての能力の行が必要です。✅ の印は、`xcuitest`、`adb`、`playwright`、`fake` の宣言と一致しなければなりません。
