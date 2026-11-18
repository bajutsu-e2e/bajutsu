[English](BE-XXXX-android-push-notification.md) · **日本語**

# BE-XXXX — Android でアプリにプッシュ通知を配信する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-android-push-notification-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control-ja.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities-ja.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md), [BE-0481](../BE-0481-android-device-set-location/BE-0481-android-device-set-location-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`push` ステップは、iOS ではテスト対象のアプリに通知を配信できますが、Android では失敗します。iOS
Simulator では、bajutsu が Apple Push Notification service（APNs）形式のペイロードを `simctl push`
で送ります。Android にはアプリへ通知を渡すコマンドがありません。そのため adb バックエンドは
`deviceControl.push` の capability を宣言していません。

本項目では、テスト対象のアプリが組み込むテスト支援ライブラリ BajutsuAndroid を通してペイロードを
届けることを提案します。bajutsu は ordered ブロードキャストでペイロードを送ります。クリップボードと
位置情報のステップがアプリに届く方法と同じです。アプリ内に新しく置くプッシュ用のレシーバは、アプリが
登録したハンドラへペイロードを渡します。ハンドラを登録していないアプリには、代わりにローカル通知を
出します。シナリオは APNs 形式のペイロードをそのまま使うので、1つの `push` ステップが両方の
プラットフォームで動きます。

## 動機

現状では、Android で通知を起点とする操作の流れをテストできません。capability の事前検査が、adb
バックエンドでの `push` ステップを端末の操作より前に弾くからです。このため showcase は `push.yaml` を
iOS 専用にしています。同じアプリを両方のプラットフォームで出すチームは、通知のシナリオを iOS 専用の
コピーに分けて持つことになります。通知の流れのうち Android 側は、テストされないまま残ります。

Android で真っ先に思いつく手段は、決定的な実行では使えません。実際の Firebase Cloud Messaging（FCM）
による配信には、ネットワーク接続とサーバーの認証情報が要ります。配信の遅れも実行の制御の外にあります。
実行がそのどちらかに依存するわけにはいきません。

配信の部分は、BajutsuAndroid がすでに解決しています。[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md)
以降、bajutsu は ordered な `am broadcast` でアプリのプロセス内のコードに届きます。そこで動く
レシーバが結果コードを設定するので、bajutsu は「処理された」と「レシーバがない」を区別して、明確に
失敗できます。[BE-0481](../BE-0481-android-device-set-location/BE-0481-android-device-set-location-ja.md)
は同じ経路を位置情報に使いました。プッシュ用のレシーバにも新しい経路は要りません。

残る問題は、レシーバがペイロードをどう扱うかです。アプリはプッシュを2か所で処理します。

- **バックグラウンド**では、システムが通知を表示し、利用者がそれをタップするとインテントでアプリが開きます。
- **フォアグラウンド**では、アプリ自身のメッセージング処理がメッセージを受け取ります。たとえば
  `FirebaseMessagingService.onMessageReceived` です。

どちらか一方に固定すると、もう一方がテストされずに残ります。ハンドラの仕組みがあれば、アプリは
ペイロードを自分のメッセージング処理へ回せます。ハンドラを登録しないアプリには、デフォルトのローカル通知が
対応します。BajutsuAndroid は Firebase に依存しないままです。

本項目が完了すると、違いは Android エミュレータの上で確かめられます。`push.yaml` の基本の
シナリオが adb バックエンドで動き、成功します。新しい Android 用シナリオは、タイトル付きのペイロードをプッシュし、
showcase アプリが表示するタイトルを検証します。`docs/platform-parity.md` の `deviceControl.push` の
行は、Android を対応済みとして示します。

## 詳細設計

### ペイロードの対応づけ

シナリオは iOS と同じ APNs 形式のペイロードを使います。bajutsu はホスト側の Python でペイロードを
3つのフィールドに対応づけます。こうすれば、端末なしで動く高速なテストが対応づけを検証できます。

| APNs のペイロード | Android のフィールド | 補足 |
|---|---|---|
| 文字列の `aps.alert` | `body` | `title` は設定しません |
| `aps.alert.title` | `title` | |
| `aps.alert.body` | `body` | |
| `aps` 以外のすべての最上位キー | `data` の項目 | 文字列の値はそのまま渡し、それ以外の値は JSON の文字列にして渡します |
| `aps.badge`、`aps.sound`、`aps.alert.subtitle`、その他の `aps` のキー | なし | Android に対応するものがないため無視します |

`data` は文字列から文字列への対応表で、FCM のメッセージが持つ data と同じ形です。したがって、
`data` から FCM のメッセージを組み立て直すアプリは、型を変換せずに済みます。

bajutsu は、JSON にして 4,096 バイトを超えるペイロードを、どのバックエンドでも送信の前に拒否します。
APNs も同じ上限を課すので、実際のアプリが受け取れるペイロードはすべて通ります。
iOS の `simctl push` による経路は現在この上限を確かめていないので、確認は `push` ステップの共通の検証に置きます。
上限があれば、ブロードキャストの `adb shell` の引数もシェルの長さの制限に十分収まります。

### BajutsuAndroid に加えるプッシュ用のレシーバ

`PushReceiver` は、`BajutsuAndroid/src/main/java/dev/bajutsu/android/` に置きます。
`ClipboardReceiver` や `LocationReceiver` と同じ場所です。このレシーバは新しいアクション
`dev.bajutsu.PUSH` を受け取ります。ブロードキャストは次の3つの extra を運びます。シェルの引数を
クォートせずに済むよう、どれも base64 で符号化します。

- `title`：タイトルの文字列です。設定しない場合と空の場合は付けません。
- `body`：本文の文字列です。設定しない場合と空の場合は付けません。
- `data`：`data` の対応表を JSON オブジェクトにしたものです。

レシーバは extra を復号し、新しい値の型 `BajutsuPush(title, body, data)` にします。その後、次の
どちらかの処理をします。

| アプリの設定 | レシーバの処理 |
|---|---|
| `Bajutsu.startPush(context, handler)` でハンドラを登録した | `handler.onPush(push)` を呼び、ほかには何もしません |
| ハンドラなしの `Bajutsu.startPush(context)` | ローカル通知を出します |

ハンドラは `fun interface BajutsuPushHandler` です。レシーバは `onReceive` の中、つまりメイン
スレッドでハンドラを呼びます。そのためハンドラは、ほかのブロードキャストレシーバと同じく、すぐに
処理を返す必要があります。FCM を使うアプリは、`BajutsuPush` から `RemoteMessage` を組み立てて、
自分の `onMessageReceived` を呼べます。このコードはアプリのテスト用ビルドに置くので、BajutsuAndroid
が Firebase に依存することはありません。

デフォルトの処理では、`bajutsu` という名前のチャンネルに通知を出します。チャンネルは初めて使うときに
作ります。通知のアイコンにはアプリのランチャーアイコンを使います。通知のコンテントインテントは
アプリの起動インテントで、`data` の各項目を文字列の extra として持ちます。このため、通知をタップすると
アプリのインテント処理が動きます。

レシーバは次の4つのいずれかの結果を返します。新しいコード `4` と `5` は位置情報のレシーバが使う `2` と `3` に続く番号なので、既存のコードの意味は変わりません。

| 結果 | 意味 | bajutsu のエラーが指す先 |
|---|---|---|
| `RESULT_OK`（`1`） | ハンドラが処理を返した、または通知を出した | なし |
| `RESULT_NO_NOTIFICATION_PERMISSION`（`4`） | デフォルトの処理で、アプリの通知が有効になっていない | `permissions: { notifications: grant }` |
| `RESULT_PUSH_FAILED`（`5`） | ハンドラが例外を投げた場合です。結果データには、クリップボードの `get` と同じく base64 で符号化した例外のメッセージが入ります | ハンドラのメッセージ |
| 未設定（`0`） | ブロードキャストを処理したレシーバがない | `BajutsuAndroid/README.md` と、アプリが起動している必要があること |

権限の確認には `NotificationManagerCompat.areNotificationsEnabled()` を使います。この呼び出しは、
API 33 以降の実行時権限 `POST_NOTIFICATIONS` を反映します。それより前の API レベルで利用者が通知を
切った場合も反映します。bajutsu が自分で権限を与えることはありません。権限の状態は、シナリオが
`permissions` の事前条件で決めます。adb バックエンドは、この事前条件をすでに `POST_NOTIFICATIONS` に
対応づけています。ハンドラを使う処理では、この確認を省きます。ハンドラがペイロードをどう扱うかは、
アプリが決めることだからです。

ほかの2つのレシーバと同じく、プッシュ用のレシーバも `Application.onCreate` で実行時に登録します。
そのためブロードキャストが届くのは、アプリのプロセスが動いているあいだに限られます。iOS は起動して
いないアプリにもプッシュを配信できますが、本項目は Android でその場合を扱いません。「レシーバがない」
ときのメッセージがこの条件を示すので、アプリを起動する前にプッシュするシナリオは、原因のわかる形で
失敗します。

`Bajutsu.startPush` は `Bajutsu.startClipboard` にならいます。アプリは、テスト用またはデバッグ用の
ビルドに限って、`Application.onCreate` からこの関数を呼びます。レシーバは権限なしで export される
ので、リリースビルドで登録するわけにはいきません。2回目の呼び出しは何もしません。

### ホスト側のコマンドと環境

この節は `bajutsu/common/backend_cli/adb/` を扱います。

- `_functions.py` に、`Bajutsu` の値を写した `PUSH_ACTION` と、新しい2つの結果コードを加えます。
- `_functions.py` に `push_broadcast_cmd(serial, package, title, body, data)` を加えます。
  `_clipboard_broadcast_cmd` にならった関数です。
- `_functions.py` に `map_push_payload(payload)` を加えます。上の対応表とサイズの上限を適用します。
- `_functions.py` に `parse_push_result(out)` を加えます。上の結果の表に従い、原因を示す
  `DeviceError` を投げます。
- `env.py` の `Env` に `push(package, payload)` を加えます。対応づけ、ブロードキャスト、結果の解釈を
  順に行います。

### ホスト側のデバイス制御と capability

Android のデバイス制御 `android_device_control` は、`bajutsu/common/platform_lifecycle/device_control.py`
にあります。この関数の `push` は、現在の `UnsupportedAction` に代えて `Env.push(package, payload)` を呼びます。
`bajutsu/common/drivers/adb/adb_driver.py` の `AdbDriver.CAPABILITIES` に `deviceControl.push` を
加えます。これで capability の事前検査が、adb バックエンドでの `push` ステップを通します。
`clearKeychain` は未対応のままです。

### showcase

Android の2つの双子アプリ `demos/showcase/android/compose` と `demos/showcase/android/views` は、
どちらもハンドラを付けて `Bajutsu.startPush` を呼びます。ハンドラはプッシュされたタイトルをアプリの
モデルに保存します。画面は、id が `push.received` の要素にそのタイトルを表示します。Views の双子アプリ
では、この要素の id は `push_received` です。Android のリソース id にはドットを使えないからです。
要素を置く場所は、それぞれの双子アプリが既存のレイアウトに合わせて決めます。守るべき約束は id だけです。

- フォアグラウンドのバナーのシナリオを `push.yaml` から、iOS 専用の新しい
  `demos/showcase/scenarios/push_banner.yaml` に移します。このシナリオは `handleSystemAlert` と、
  SwiftUI 専用の起動環境を必要とします。Android のレーンはタグで絞り込まず、ファイルを丸ごと実行します。
- `push.yaml` には基本のシナリオだけを残し、iOS 専用の注記を外します。`device.yaml` と同じく、
  セレクタに Views の形を加えて `[stable.row.1, stable_row_1]` にします。
- `.github/workflows/ios-e2e.yml` の `actuation` ジョブは、`push.yaml` に加えて `push_banner.yaml` も
  列挙します。`demos/showcase/Makefile` の `systemalert` 除外のコメントは、新しいファイルを指すようにします。
- 新しい `demos/showcase/scenarios/push_android.yaml` は `{ aps: { alert: { title, body } } }` を
  プッシュし、`[push.received, push_received]` の値を検証します。ほかの `*_android.yaml` と同じく
  `tags: [android]` を付けるので、iOS の一括実行のレーンはこのファイルを除外します。
- `demos/showcase/android/Makefile` は、`E2E_SCENARIOS` と `E2E_VIEWS_SCENARIOS` の両方に `push` と
  `push_android` を加えます。

showcase はハンドラを登録するので、showcase のシナリオが動かすのはハンドラを使う処理です。デフォルトの
処理は、代わりに作業単位 1 で端末の上で確かめます。この確認では、エミュレータで通知を出し、通知の
権限を外したときの失敗も確かめます。作業単位 1 は、両方の結果をこの項目の進捗ログに記録します。

### ドキュメント

- `docs/platform-parity.md` と `docs/ja/` の対応ページで、`deviceControl.push` の行を Android 対応に
  変えます。
- `docs/drivers.md`（英語と日本語）の capability の表とデバイス制御の注記で、`deviceControl.push` に
  Android を加えます。両方の文書が `AdbDriver.CAPABILITIES` と一致するまで、
  `tests/test_platform_parity.py` は失敗します。
- `docs/scenarios.md`（英語と日本語）の `push` の説明に、Android での対応づけと権限の規則を書きます。
- `docs/drivers.md` の Flutter の注記と、`docs/ci.md` の `android-e2e.yml` の行（英語と日本語）から、
  `push` を iOS 専用とする記述を外します。代わりに、Flutter の Android レーンが `push` を引き続き除外する
  理由を書きます。Flutter アプリが BajutsuAndroid を組み込まないからです。
- `BajutsuAndroid/README.md`（英語と日本語）に、`Bajutsu.startPush`、ハンドラ、結果コードを書きます。

### 作業分解

| # | 作業単位 | ファイル | 完了条件 |
|---|---|---|---|
| 1 | エミュレータでデフォルトの処理を確かめる | なし（進捗ログ） | 通知が出ることと、権限がないときの失敗の両方を確認した |
| 2 | プッシュ用のレシーバ、ハンドラ、`startPush` | `BajutsuAndroid/` | ライブラリがビルドでき、README が API を説明している |
| 3 | ペイロードの対応づけ、共通のサイズ確認、コマンドの生成、結果の解釈、`Env.push` | `bajutsu/common/backend_cli/adb/` と `push` ステップの検証 | 2つの表のすべての行をユニットテストが確かめる |
| 4 | デバイス制御と capability | `device_control.py`、`adb_driver.py` | 事前検査が adb での `push` を通し、ユニットテストが通る |
| 5 | showcase のハンドラとシナリオ | `demos/showcase/`、`.github/workflows/ios-e2e.yml` | `push.yaml` と `push_android.yaml` が Android エミュレータで成功し、`push_banner.yaml` が iOS で成功する |
| 6 | ドキュメント | `docs/`、`docs/ja/` | `tests/test_platform_parity.py` が通る |

## 検討した代替案

| 代替案 | 採らなかった理由 |
|---|---|
| ローカル通知だけを出し、ハンドラを用意しない | もっとも単純ですが、`onMessageReceived` のようなアプリのフォアグラウンドの処理がテストされずに残ります。ハンドラの仕組みは、小さなインタフェース1つでその処理を扱えるようにします。 |
| ペイロードを `RemoteMessage` としてアプリのメッセージングサービスに渡す | 実際の配信にもっとも近い一方で、BajutsuAndroid が Firebase に依存します。FCM を使わないアプリはこのステップを使えません。FCM を使うアプリは、ハンドラを使えば同じことを自分のテストコードで行えます。 |
| 実際の FCM メッセージを送る | 忠実ですが、ネットワーク接続とサーバーの認証情報が要ります。決定的な実行は、そのどちらにも依存するわけにはいきません。 |
| ステップに Android 専用のキー（`notification`、`data`）を加える | FCM により忠実ですが、シナリオがプラットフォームごとに分かれ、ステップのスキーマも大きくなります。APNs の形を対応づければ、1つのシナリオで両方のプラットフォームを扱えます。 |
| プッシュのたびに adb で `POST_NOTIFICATIONS` を与える | シナリオは短くなりますが、アプリの権限の状態を黙って変えます。権限がない状態を確かめるシナリオが壊れます。`permissions` の事前条件で、すでに明示的に権限を与えられます。 |
| システムの通知シェードに出た通知もタップする | 通知をタップして画面に至る流れ全体を扱えますが、アプリの外にある System UI を操作します。そこでのセレクタの安定性や端末ごとの違いは、別の項目で扱う価値があります。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 作業単位 1：エミュレータでデフォルトの通知の処理と、権限がないときの失敗を確かめる
- [ ] 作業単位 2：BajutsuAndroid の `PushReceiver`、`BajutsuPushHandler`、`Bajutsu.startPush`
- [ ] 作業単位 3：ペイロードの対応づけ、4,096 バイトの共通の確認、コマンドの生成、結果の解釈、`Env.push`
- [ ] 作業単位 4：Android のデバイス制御の `push` と、`AdbDriver.CAPABILITIES` の `deviceControl.push`
- [ ] 作業単位 5：showcase のハンドラ、両プラットフォームで動く `push.yaml`、`push_banner.yaml`、`push_android.yaml`
- [ ] 作業単位 6：プラットフォーム対応表、ドライバ、シナリオ、BajutsuAndroid のドキュメント（英語と日本語）

## 参考

- Issue [#2397](https://github.com/bajutsu-e2e/bajutsu/issues/2397)：本項目が埋める、Android で未対応の部分です。
- [`docs/ja/platform-parity.md`](../../docs/ja/platform-parity.md)：iOS と Android の capability の違いです。
- [`BajutsuAndroid/src/main/java/dev/bajutsu/android/ClipboardReceiver.kt`](../../BajutsuAndroid/src/main/java/dev/bajutsu/android/ClipboardReceiver.kt)：本項目がならうレシーバの形です。
- [`demos/showcase/scenarios/push.yaml`](../../demos/showcase/scenarios/push.yaml)：本項目が両プラットフォームで動くようにするシナリオです。
