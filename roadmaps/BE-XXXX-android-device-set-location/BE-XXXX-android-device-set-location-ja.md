[English](BE-XXXX-android-device-set-location.md) · **日本語**

# BE-XXXX — Android 実機で BajutsuAndroid 経由の位置情報設定を可能にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-android-device-set-location-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control-ja.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities-ja.md), [BE-0475](../BE-0475-ios-runner-set-location/BE-0475-ios-runner-set-location-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`setLocation` ステップは、Android のエミュレータでは動き、Android の実機では失敗します。adb バックエンドは
`adb emu geo fix` で位置を設定しますが、このコマンドはエミュレータのコンソールのものです。物理的な端末には
そのコンソールがありません。

そこで、実機向けに 2 本目の経路を設けることを提案します。テスト対象のアプリが組み込むテスト支援ライブラリ
BajutsuAndroid が、アプリの内側から模擬の位置プロバイダを登録します。bajutsu は先に adb でアプリへ
模擬位置情報の権限を与え、続いてブロードキャストで座標を送ります。クリップボードのステップがアプリへ
届くのと同じ方法です。エミュレータは `emu geo fix` のまま残し、どちらの経路を使うかは、端末自身に
エミュレータかどうかを問い合わせて決めます。

## 動機

adb バックエンドは、どの Android ターゲットにも `deviceControl.setLocation` の capability を宣言しています。
そのため capability の事前検査（preflight）は、実機でも `setLocation` ステップを通します。ところが
`adb emu` はエミュレータのコンソールにしか届かないので、ステップは実行時に失敗します。位置に依存する
フロー（店舗検索、配達の追跡、地域による制限）を試験するチームは、そのシナリオを手元の端末でも
デバイスクラウドでも実行できません。エミュレータ専用の写しをもう 1 つ保つか、実機ではそのフローを
飛ばすしかありません。

エミュレータでの結果も誤解を招きます。エミュレータでは通り、実機では落ちるので、未対応なだけのシナリオが
実機で不安定に見えます。

アプリ内の経路は、bajutsu がすでに持つ仕組みでこの差を埋めます。Android 10 以降、シェルのプロセスは
クリップボードを読み書きできないため、[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md)
はクリップボードの操作を BajutsuAndroid へ移しました。bajutsu は、アプリのプロセスで動くレシーバへ順序付きの
`am broadcast` を送ります。Android の模擬位置プロバイダも同じ形をしています。プラットフォームの API
`LocationManager.addTestProvider` は模擬位置情報の権限を持つアプリから使え、その権限は adb の `appops` で
与えられます。したがって、クリップボードのレシーバの隣に位置情報のレシーバを置けば、新しい通信路は要りません。

この項目を実装すると、成果を実機で確かめられます。Android の実機で動くシナリオが位置を設定し、アプリが
表示する座標を検証して通ります。同じ端末で次に動くシナリオは、端末の実際の位置から始まります。
`docs/platform-parity.md` の振る舞いの表では、「どちらのバックエンドも、まだ実機では位置を設定
できません」という記述の対象から Android が外れます。

## 詳細設計

### 経路の選び方

Android のデバイス制御は、リースの最初の `setLocation` で端末ごとに経路を選びます。bajutsu は
`adb shell getprop` で `ro.boot.qemu` と `ro.kernel.qemu` の 2 つのシステムプロパティを読みます。どちらかが
`1` であればエミュレータです。新しいエミュレータのイメージは前者を、古いイメージは後者を設定します。
ただし、コンソールの経路にはローカルのコンソールが要ります。adb がそのコンソールに届くのは、
シリアルが `emulator-<port>` 形式のときに限られます。そこでデバイス制御は、プロパティがエミュレータを
示し、かつシリアルが `emulator-<port>` 形式のときに `emu geo fix` を使います。それ以外のターゲットは
すべてアプリ内の経路を使います。
デバイス制御は答えをリースのあいだ保持するので、以降のステップで adb の呼び出しは増えません。

| ターゲット | 経路 | BajutsuAndroid が必要か |
|---|---|---|
| ローカルのエミュレータ（`emulator-<port>`） | `adb emu geo fix <経度> <緯度>`（変更なし） | 不要 |
| 実機、または `adb connect` でつないだエミュレータ | `appops` で権限を与えてから位置情報のブロードキャスト | 必要 |

どちらの判定も、単独では足りません。シリアルは慣例でエミュレータを表すだけなので、プロパティで
エミュレータであることを確かめます。`adb connect` でつないだエミュレータ（クラウドのエミュレータなど）は
プロパティを報告しますが、ホスト名とポートをシリアルとして持ちます。adb はそのコンソールに届かないため、
このエミュレータは実機と同じくアプリ内の経路を使います。

### BajutsuAndroid に加える位置情報のレシーバ

`BajutsuAndroid/src/main/java/dev/bajutsu/android/` に、`ClipboardReceiver` と並べて `LocationReceiver` を
加えます。レシーバは新しいアクション `dev.bajutsu.LOCATION` を待ち受け、`op` エクストラで 2 つの操作を
扱います。

- **`set`**：`lat` と `lon` を文字列として読みます。シェルの引数に引用符が要らないようにするためです。
  レシーバは `gps` と `network` の各プロバイダについてテストプロバイダを追加して有効にします。
  API 31 以降では `ProviderProperties` を渡します。ライブラリの最小 SDK は 26 なので、API 31 より前では、
  各プロパティを個別の引数で受け取る従来のオーバーロードを使います。
  各プロバイダには、精度、時刻、起動からの経過時間をそろえた `Location` を渡します。これらの項目を欠いた `Location` は
  `IllegalArgumentException` を投げます。
- **`clear`**：2 つのテストプロバイダを取り除きます。存在しないプロバイダは、取り除き済みとして扱います。

レシーバは成功すると、クリップボードのレシーバと同じく `Bajutsu.RESULT_OK` を設定します。2 種類の失敗には
専用の結果コードを割り当て、bajutsu が原因を名指しできるようにします。

| 結果 | 意味 | bajutsu のエラーが指す先 |
|---|---|---|
| `RESULT_OK` | 位置を設定した、または解除した | なし |
| `RESULT_NO_MOCK_PERMISSION` | `addTestProvider` が `SecurityException` を投げた | `appops` による権限付与 |
| 未設定（0） | ブロードキャストを処理したレシーバがない | `BajutsuAndroid/README.md` |

`RESULT_NO_MOCK_PERMISSION` の値は `2` です。`Bajutsu` がこの値を宣言し、`_functions.py` は同じ値の
定数を持ちます。`CLIPBOARD_RESULT_OK` が `RESULT_OK` と同じ値を持つのと同じ形です。
`Bajutsu.startLocation` は、クリップボードとは別に自分の登録済みフラグを持ちます。

アプリは、テスト用またはデバッグ用のビルドに限り、`Application.onCreate` から新しい
`Bajutsu.startLocation(context)` を呼んでレシーバを有効にします。この関数は `Bajutsu.startClipboard` に
倣います。レシーバは外部に公開されるため、リリースビルドで登録してはいけません。同じ理由で、ライブラリの
マニフェストは空のままにします。作業単位 1 で、`appops` にはマニフェストの
`ACCESS_MOCK_LOCATION` 権限が必要だとわかる場合があります。そのときは、デバッグ用のマニフェストに
だけ権限を宣言するよう README で案内します。

模擬するのは `gps` と `network` の 2 つのプロバイダで、Google Play 開発者サービスの fused プロバイダには
手を付けません。この 2 つに絞れば、BajutsuAndroid に Play 開発者サービスへの依存が加わりません。fused
プロバイダを使うアプリに模擬の位置が届くかどうかは、端末と Play 開発者サービスの版によって変わります。
作業単位 1 で実機での振る舞いを記録し、README に書きます。

### ホスト側のコマンドと環境

対象は `bajutsu/common/backend_cli/adb/` です。

`_functions.py` には、クリップボード用のものと並べて 2 つのコマンド生成関数を加えます。エミュレータの判定には既存の `get_prop_cmd` を使います。

- `mock_location_appop_cmd(serial, package, mode)` は `appops` で模擬位置情報の権限を切り替えます。
  実行するのは `appops set <package> android:mock_location <mode>` で、モードは `allow` か `default` です。
- `location_broadcast_cmd(serial, package, op, lat, lon)` は順序付きのブロードキャストを送ります。
  `_clipboard_broadcast_cmd` に倣います。

`parse_location_result(out)` は結果コードを読みます。成功でなければ、上の表の原因を添えて
`DeviceError` を投げます。`env.py` の
`Env` には次の 3 つのメソッドを加えます。

- `has_emulator_console()`
- `set_mock_location(package, lat, lon)`
- `clear_mock_location(package)`

`set_mock_location` は権限を与えてから `set` をブロードキャストします。権限の付与は何度実行しても
結果が変わらないので、呼び出しのたびに実行します。

### ホスト側のデバイス制御とライフサイクル

対象は `bajutsu/common/platform_lifecycle/` です。

`device_control.py` の `android_device_control` は、`Env.has_emulator_console()` の結果で `set_location` の経路を
分けます。ローカルのエミュレータでは、`emu geo fix` を実行する `Env.set_location` を使い続けます。それ以外の
ターゲットでは `Env.set_mock_location` を呼び、そのリースで模擬の位置を設定したことをデバイス制御が記録します。

この記録は環境が持ちます。`controller()` は `foreground` や `background` と同じく
`android_device_control` にコールバックを渡します。コールバックはパッケージと座標を、パッケージを
キーとして環境へ保存します。デバイス制御は `appops` で権限を与える前に記録を書きます。そのため、権限の付与に
続くブロードキャストが失敗した場合も、`teardown` で権限を元に戻せます。`teardown` と再起動の経路
（`relauncher` と `crawl_reset`）は、環境からこの記録を読みます。

デバイスグループのメンバーのリースは、`teardown` ではなく `end_member` で終わります。そのため
`end_member` も、強制停止の前に自分のパッケージについて同じ解除と権限の取り消しを行います。
`teardown` は、記録に残っているパッケージを解除します。

`android_environment.py` にある Android 環境の `teardown` は、アプリを強制停止する前に模擬の位置を
解除します。順序には意味があります。クリップボードと位置情報のレシーバは実行時に登録されるので、
ブロードキャストがレシーバに届くのはアプリのプロセスが生きているあいだに限られます。リースのあいだに
模擬の位置を設定したターゲットについて、`teardown` は次の手順を踏みます。

1. `clear` をブロードキャストし、テストプロバイダを取り除きます。
2. `appops set <package> android:mock_location default` を実行し、権限を外します。
3. 現在の `teardown` と同じく、アプリを強制停止します。

手順 1 と手順 2 の失敗は警告として記録し、実行は失敗させません。シナリオの判定はその時点で確定しており、
手順 2 だけでもアプリはその後に位置を模擬できなくなります。Android では `teardown` がリースを返すたびに
動くので、同じ端末で次に動くシナリオは実際の位置から始まります。

模擬の位置は、アプリの再起動を越えて残らない可能性があります。`relaunch` ステップはプロバイダを登録した
プロセスを終了させ、Android がそのプロセスとともにプロバイダを取り除く可能性があります。実機での振る舞いは
作業単位 1 で確定させます。残らない場合は、環境がアプリを再起動したあとにデバイス制御が最後の位置を
送り直します。こうすれば、エミュレータで保たれるのと同じ位置をシナリオが見ます。

### capability と事前検査

`deviceControl.setLocation` は adb バックエンドの静的な capability の集合に残します。
`capabilities_for_run` に Android 向けの分岐は加えません。BajutsuAndroid を組み込んでいないアプリでは、上の表の「レシーバがない」
エラーでステップが実行時に失敗します。クリップボードのステップも現在同じ振る舞いをします
（[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md)）。実行前に集合を
絞るには capability の計算の中で adb を呼ぶ必要があり、その計算は現在、設定だけで決まっています。

### showcase のシナリオ

Android の showcase アプリは Compose 版と Views 版の両方で `Bajutsu.startLocation` を呼び、小さな位置表示を加えます。`LocationManager` から得た最新の緯度と経度を
表示するテキストビューです。シナリオは位置を設定し、表示された座標を検証します。同じシナリオが
エミュレータのレーンと実機の両方で動くので、1 つのファイルで 2 つの経路を確かめられます。showcase は
既存の権限の前提条件で位置情報の権限を与えるため、システムのダイアログが実行を妨げることはありません。

### 作業分解（MECE）

| 作業単位 | 作業 | 完了条件 |
|---|---|---|
| 1 | 実機でのスパイク：マニフェストの権限の有無による `appops` の挙動、再起動を越えた模擬の存続、権限を外したときの解除、fused プロバイダへの反映 | 結果をこの項目の進捗ログに記録した |
| 2 | BajutsuAndroid の `LocationReceiver`、`Bajutsu.startLocation`、結果コード | `make -C demos/showcase/android build-all` で、showcase のすべてのバリアントがライブラリとともにビルドできる |
| 3 | adb のコマンド生成関数、`parse_location_result`、`Env` のメソッド | 各生成関数の引数列と各結果コードをユニットテストが確かめる |
| 4 | `android_device_control` での経路選択と、Android 環境の `teardown` | 2 つの経路、判定結果の保持、`teardown` と `end_member` の順序と失敗時の許容をユニットテストが確かめる |
| 5 | Compose 版と Views 版の showcase の位置表示と位置情報のシナリオ | エミュレータのレーンでシナリオが通る |
| 6 | 英語と日本語のドキュメント：`docs/platform-parity.md` と `BajutsuAndroid/README.md` | 両ページと日本語版（`docs/ja/platform-parity.md`、`BajutsuAndroid/README.ja.md`）が 2 つの経路を説明している（capability の表の `deviceControl.setLocation` の行と、振る舞いの表の `setLocation` の行を含む） |
| 7 | 実機での検証 | 実機でシナリオが通り、次のシナリオが実際の位置を見る。結果を PR に記録した |

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| 常にアプリ内の経路を使う | iOS の BE-0475 と同じく、どのターゲットでも経路を 1 本にする | BajutsuAndroid を組み込んでいないエミュレータのターゲットは、現在使えている `setLocation` を失う |
| アプリ内の経路を先に試し、`emu geo fix` を予備にする | レシーバが応答しなければ、エミュレータに限り予備の経路へ切り替える | 1 回の呼び出しに失敗の道筋が 2 つでき、どちらの経路を通るかがアプリのビルドに左右される |
| 位置を模擬する補助アプリを別に用意する | bajutsu が各端末にインストールするアプリが模擬を受け持つ | アプリを変えずに済むが、端末ごとのインストール手順と、版を管理する成果物がもう 1 つ増える |
| fused プロバイダも模擬する | `FusedLocationProviderClient.setMockMode` で fused だけを読むアプリにも届く | BajutsuAndroid が Play 開発者サービスに依存し、Play 開発者サービスのない端末で動かない |
| fused プロバイダだけを模擬する | レシーバがもっとも単純になる | `gps` プロバイダを直接読むアプリには届かない |
| 実行前に capability を絞る | `capabilities_for_run` が実機かどうかとレシーバの有無を調べる | 設定だけで決まる計算に adb の呼び出しを持ち込む。クリップボードの前例は実行時に失敗させている |
| 模擬の解除をリースではなく実行の終わりにする | adb の呼び出しが減る | シナリオの結果が、同じ端末で直前に動いたシナリオに左右される |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 作業単位 1：実機でのスパイク（`appops`、再起動、権限の解除、fused への反映）
- [ ] 作業単位 2：BajutsuAndroid の `LocationReceiver` と `Bajutsu.startLocation`
- [ ] 作業単位 3：adb のコマンド生成関数、結果の解析、`Env` のメソッド
- [ ] 作業単位 4：経路の選択とリース終了時の解除
- [ ] 作業単位 5：showcase の位置表示とシナリオ
- [ ] 作業単位 6：英語と日本語のドキュメント
- [ ] 作業単位 7：実機での検証

## 参考

- 差を報告した GitHub issue [#2392](https://github.com/bajutsu-e2e/bajutsu/issues/2392)。
- [`LocationManager.addTestProvider`](https://developer.android.com/reference/android/location/LocationManager#addTestProvider(java.lang.String,%20android.location.provider.ProviderProperties))
  と [`setTestProviderLocation`](https://developer.android.com/reference/android/location/LocationManager#setTestProviderLocation(java.lang.String,%20android.location.Location))。
- [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control-ja.md)：`emu geo fix` で
  エミュレータに `setLocation` を加えた項目。
- [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md)：この項目が倣うアプリ内の
  レシーバを導入した項目。
- [BE-0475](../BE-0475-ios-runner-set-location/BE-0475-ios-runner-set-location-ja.md)：iOS の実機での
  同じ差を扱う項目。
