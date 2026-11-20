[English](BE-0498-android-push-notification.md) · **日本語**

# BE-0498 — Android でアプリにプッシュ通知を配信する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0498](BE-0498-android-push-notification-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0498") |
| 実装 PR | [#2492](https://github.com/bajutsu-e2e/bajutsu/pull/2492)（単位 1〜6） |
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

アプリは `startPush` を呼ぶときに、この2つの経路のうち1つを選びます。決めるのは Bajutsu ではなく、
アプリ自身の組み込み方です。ハンドラを登録したアプリは、フォアグラウンドの処理が動きます。登録しない
アプリには、バックグラウンドの経路に当たるローカル通知が出ます。BajutsuAndroid は Firebase に依存しない
ままです。

showcase はハンドラを登録するので、showcase のスイートが動かすのはハンドラの経路です。デフォルトの経路は
CI では確かめません。作業単位 4 のエミュレータでの確認が受け持ちます。

本項目が完了すると、違いは Android エミュレータの上で確かめられます。`push.yaml` の基本の
シナリオが adb バックエンドで動き、成功します。新しい Android 用シナリオは、タイトル付きのペイロードをプッシュし、
showcase アプリが表示するタイトルを検証します。parity の表の `deviceControl.push` の行は、Android を対応済みとして示します。
この表は `docs/platform-parity.md` と `docs/ja/platform-parity.md` にあります。

## 詳細設計

### ペイロードの対応づけ

シナリオは iOS と同じ APNs 形式のペイロードを使います。bajutsu はホスト側の Python でペイロードを
3つのフィールドに対応づけます。こうすれば、端末なしで動く高速なテストが対応づけを検証できます。

| APNs のペイロード | Android のフィールド | 補足 |
|---|---|---|
| 文字列の `aps.alert` | `body` | `title` は設定しません |
| `aps.alert.title` | `title` | |
| `aps.alert.body` | `body` | |
| `aps.alert` がない、または `title` も `body` もない alert | `title` と `body` は未設定 | 下のデータのみのペイロードの規則を参照 |
| `aps` 以外のすべての最上位キー | `data` の項目 | 文字列の値はそのまま渡し、それ以外の値は JSON の文字列にして渡します |
| `aps.badge`、`aps.sound`、`aps.alert.subtitle`、その他の `aps` のキー | なし | Android に対応するものがないため無視します |

`data` は文字列から文字列への対応表で、FCM のメッセージが持つ data と同じ形です。したがって、
`data` から FCM のメッセージを組み立て直すアプリは、型を変換せずに済みます。

`bajutsu/common/scenario/models/actions/push.py` の `Push` モデルが、次の3つの規則を検証します。
どのバックエンドでも、シナリオの読み込み時に、端末の操作より前に、書かれたままのペイロードを測って失敗します。

- ペイロードに `aps` キーがなければなりません。`simctl push` と同じ条件です。`aps` の値はオブジェクトでなければなりません。
  `aps.alert` がある場合は、文字列かオブジェクトです。`aps.alert` がオブジェクトのとき、`title` と `body` は、
  ある場合に文字列です。型の規則はどのバックエンドでも同じなので、`{aps: "x"}`、`{aps: {alert: 5}}`、
  `{aps: {alert: {title: {a: 1}}}}` は読み込み時に失敗します。
- ペイロードは 4,096 バイト以下でなければなりません。測り方は、デフォルトの引数で作った
  `json.dumps(payload)` の長さです。iOS の経路は、同じ文字列を `simctl push` に渡すファイルへ書き出します。
  この文字列は基本ラテン文字の範囲外の文字をエスケープするので、文字数とバイト数は一致します。
- ペイロードの値はすべて `json.dumps` でシリアライズできなければなりません。
  シナリオのローダーはタイムスタンプを解決します。
  そのため、引用符のない `at: 2024-01-01` は `datetime.date` として読み込まれます。
  `json.dumps` はこれに `TypeError` を送出します。
  Pydantic が包むのは、検証関数の `ValueError` と `AssertionError` です。`TypeError` は包みません。
  そのためトレースバックが外へ出てしまいます。
  共有する長さの関数がこれを捕まえ、`ValueError` を送出します。
  この例外は問題のあるキーのパスを示し、値は示しません。
  引用符のない日付は、読み込み時に明確な検証エラーで失敗します。

`simctl push` は、`aps` キーがない場合と、ペイロードが大きすぎる場合を、すでに自分で拒否します。ただし、端末の操作が始まってからで、
メッセージも `simctl` 独自のものです。モデルの検証なら、より早い段階で原因を示して失敗できます。対応づけの側には、どちらの規則の写しも置きません。
この長さは、iOS の経路が `simctl push` に渡すエスケープ後の文字列で数えるので、`simctl` が検査する長さと一致します。
基本ラテン文字の範囲外の文字を含む場合は、エンコード後のバイト数で数える APNs の上限より厳しくなります。
上限があれば、ブロードキャストの
`adb shell` の引数もシェルの長さの制限に十分収まります。

`${vars.*}` や `${secrets.*}` のトークンは、読み込み後にペイロードを大きくします。
実行時には置換後のステップを検証し直します。
そのため、`bajutsu/common/orchestrator/substitution.py` の `_interp_step` は `eval` の先例（BE-0472）に従います。
`eval` では、置換後ではなく書かれたままの `eval` を `Step.model_validate` に渡します。
`ValidationError` は、置換後の文字列（秘密を含みうる）を `input_value=` に含めるからです。

`_interp_step` は `push` でも同じ扱いをします。`model_validate` の前に、書かれたままの `push` に戻します。
モデルの検証は、置換後の文字列を見ません。そのあとで、`extractBody` と同じく `model_copy` で置換後のペイロードを付け直します。
ここでは何も送出しません。ここで送出すると、エラーがステップの外へ出てしまいます。
ステップランナーがここで捕まえるのは `UncoveredSystemAlertLocale` です。
`run_scenario` が変換するのは `ControlChannelError`、`UnsupportedAction`、`RunCancelled` だけです。
それ以外のエラー（`ValidationError` や `DeviceError` など）は `run_all` を中断し、すべてのシナリオの結果を捨てます。

代わりに `push` のハンドラ `_do_push` が、`eval` のハンドラと同じく再検査します。
検証と共有する関数でペイロードの長さを測ります。
上限を超えたら `base.SelectorError` を送出します。
ステップランナーは `SelectorError` を、失敗したステップとして報告します。エラーには、測った長さと上限を書きます。
たとえば「push payload is 4,180 bytes; the limit is 4,096」です。
ペイロードの文字列と値は引用しません。
置換が書き換えるのは値で、キーではありません。`aps` キーは増減しないので、`aps` があることの規則に再検査は要りません。
型の規則にも再検査は要りません。実行時の束縛はすべて文字列であり（`_interp_step` は `Mapping[str, str]` を取ります）、
`${vars.*}` や `${secrets.*}` のトークンは文字列になるからです。文字列でない `${params.*}` や `${row.*}` の値は読み込み時に置換され、
展開後のステップに対して `Push` の検証が再び走ります（`expand.py`）。対応づけは、`aps` がオブジェクトであることと、
3つのフィールドが検証済みの型であることを前提にできます。
置換後に長さを測るので、`adb shell` の引数にも上限がかかります。

`content-available` のサイレントプッシュのように、有効でも alert を持たないペイロードがあります。
この場合、対応づけの結果に `title` と `body` はありません。ハンドラの経路は、`BajutsuPush` を
そのまま受け取ります。`title` と `body` は null で、`data` は埋まっています。デフォルトの経路は、表示するものがないので
通知を出さず、`RESULT_OK` を返します。通知の権限は確認する前に返します。

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
| ハンドラなしの `Bajutsu.startPush(context)` | ローカル通知を出します。ただし `title` と `body` がどちらも未設定なら、何も出しません |

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
| `RESULT_PUSH_FAILED`（`5`） | ハンドラが例外を投げた場合です。結果データには、クリップボードの `get` と同じく base64 で符号化した例外のメッセージが入ります。bajutsu はペイロードの文字列を自分では加えず、ハンドラのメッセージをそのまま渡します。メッセージはアプリの作者が管理でき、もっとも役に立つ診断情報だからです。ハンドラが受け取るのは*置換後*のペイロードです。そのため bajutsu は、安全なメッセージと `${secrets.*}` の文字列を含むメッセージを区別できません。作業単位 1 の README に、ハンドラの例外メッセージにペイロードを含めてはならないと書きます。bajutsu はメッセージを伏せ字にしません | ハンドラのメッセージ |
| 未設定（`0`） | ブロードキャストを処理したレシーバがない | `BajutsuAndroid/README.md` と、アプリが起動している必要があること |

デフォルトの処理では、順序が大切です。`title` と `body` のどちらもないペイロードは、権限を確認せずに
`RESULT_OK` を返します。表示するものがあるペイロードは権限を確認し、`4` を返せるのはこのペイロード
だけです。確認には `NotificationManagerCompat.areNotificationsEnabled()` を使います。この呼び出しは、
API 33 以降の実行時権限 `POST_NOTIFICATIONS` を反映します。それより前の API レベルで利用者が通知を
切った場合も反映します。
デフォルトの経路を使うアプリは、デバッグ用かテスト用のマニフェストに次の宣言を置きます。
`<uses-permission android:name="android.permission.POST_NOTIFICATIONS"/>` です。
宣言がないと、API 33 以降で `areNotificationsEnabled()` は false のままで、`pm grant` も失敗します。
ライブラリのマニフェストは何も宣言しません。リリースビルドにマージされてしまうからです。
showcase のアプリはすでにこの権限を宣言しています。作業単位 1 の README の更新と、結果 `4` の
エラーメッセージが、この宣言の必要を伝えます。bajutsu が自分で権限を与えることはありません。権限の状態は、シナリオが
`permissions` の事前条件で決めます。adb バックエンドは、この事前条件をすでに `POST_NOTIFICATIONS` に
対応づけています。ハンドラを使う処理では、この確認を省きます。ハンドラがペイロードをどう扱うかは、
アプリが決めることだからです。

ほかの2つのレシーバと同じく、プッシュ用のレシーバも `Application.onCreate` で実行時に登録します。
そのためブロードキャストが届くのは、アプリのプロセスが動いているあいだに限られます。iOS は起動して
いないアプリにもプッシュを配信できますが、本項目は Android でその場合を扱いません。「レシーバがない」
ときのメッセージがこの条件を示すので、アプリを起動する前にプッシュするシナリオは、原因のわかる形で
失敗します。

`background` ステップの後の配信は、CI のエミュレータイメージである API 34 で確認が要ります。Android は、
キャッシュされたプロセスにある、コンテキストで登録したレシーバへのブロードキャストを遅らせることが
あります。`background` ステップの後に送る ordered な `am broadcast` は、ブロックされるか、遅れて届く
可能性があります。作業単位 4 の確認でこの場合を試し、結果を進捗に記録します。結果が決めるのは
ドキュメントの書き方です。

- ブロードキャストがすぐ届くなら、ドキュメントの作業単位がこの場合を対応済みとして書きます。
- そうでなければ、ドキュメントの作業単位が、この場合を未対応として書きます。

ホスト側は、どちらの結果でもブロードキャストに上限を設けます。次の節で説明します。

`Bajutsu.startPush` は `Bajutsu.startClipboard` にならいます。アプリは、テスト用またはデバッグ用の
ビルドに限って、`Application.onCreate` からこの関数を呼びます。レシーバは権限なしで export される
ので、リリースビルドで登録するわけにはいきません。2回目の呼び出しは何もしません。

### ホスト側のコマンドと環境

この節は `bajutsu/common/backend_cli/adb/` を扱います。

- `_functions.py` に、`Bajutsu` の値を写した `PUSH_ACTION` と、新しい2つの結果コードを加えます。
- `_functions.py` に `push_broadcast_cmd(serial, package, title, body, data)` を加えます。
  `_clipboard_broadcast_cmd` にならった関数です。
- `_functions.py` に `map_push_payload(payload)` を加えます。上の対応表を適用します。サイズの上限は
  `Push` モデルが持ち、ここには置きません。
- `_functions.py` に `parse_push_result(out)` を加えます。上の結果の表に従い、原因を示す
  `DeviceError` を投げます。メッセージはブロードキャストの argv を引用しません。argv には base64 の
  ペイロードが入るためです。パーサは結果コードと結果データだけから原因を組み立てます。
- `env.py` の `Env` に `push(package, payload)` を加えます。対応づけ、ブロードキャスト、結果の解釈を
  順に行います。
- ブロードキャストは常にタイムアウトつきで実行します。現状の `real_run` はタイムアウトを設定せず、
  `RunFn` は `(list[str]) -> str` です。`_functions.py` に `PUSH_BROADCAST_TIMEOUT_S = 10` と、
  それを `subprocess.run` に渡す実行関数を加えます。この関数は `subprocess.TimeoutExpired`、`subprocess.CalledProcessError`（デバイスがオフラインである、
  見つからないなどの adb の非ゼロ終了）、`OSError` を `DeviceError` に変えます。メッセージには終了コードと
  stderr だけを入れ、argv は入れません。この関数は `DeviceError` を自分で組み立て、コマンドを引用する
  `device_error()` は通しません。`from None` で送出するので、トレースバックの連鎖にも argv は残りません。
- `env.py` のデバイス制御のコメントブロック（256〜262 行目）は、`push` と `clearKeychain` の両方を配線しないと書いています。
  ブロックの書き出しは「Device control: the Android peer」です。
  コメントは、`push` を BajutsuAndroid の push レシーバが配信すると書きます。
  配線しないのは `clearKeychain` だけです。
- `env.py` の `Env.__init__` に、省略できる `push_run: RunFn | None` 引数を加えます。
  `Env.push` は決まった実行関数を呼び、`subprocess.run` は直接呼びません。
  呼び出し側が `push_run` を渡さないとき、`Env` は `run` から実行関数を決めます。
  `run` が `real_run` なら上限つきの実行関数を使い、それ以外なら `run` そのものを使います。
  そのため、`run` を差し替えた呼び出し側では、プッシュのブロードキャストも同じ偽の関数を通ります。
  `bajutsu/` と `tests/` にある `adb.Env(serial, run=…)` の呼び出し箇所は、どれも変わりません。
  `push_run` を渡し忘れて本物のデバイスに届く箇所もありません。
  タイムアウトのエラーは、上限つきの実行関数の下で `subprocess.run` を差し替えるユニットテストが確かめます。
- クリップボード（BE-0233）と位置情報（BE-0481）のブロードキャストは、上限のない `real_run` のままです。
  エラーの形も `device_error()` のままです。上限が要るのはプッシュだけです。プッシュのレシーバはアプリのハンドラを呼びます。
  ハンドラは任意のアプリのコードを実行するので、途中で止まるおそれがあります。
  ほかの 2 つのレシーバは、すぐに応答するライブラリのコードだけを実行します。
  3 つとも 1 つの上限つきの実行関数に載せれば、エラーの形は 1 つになります。
  ただし、すでに出荷した 2 つのステップの挙動が変わるので、この項目の範囲外とします。
  作業単位 4 の確認が、その判断の材料になります。API 34 で `background` の後にプッシュのブロードキャストが
  止まるなら、ほかの 2 つも同じように止まりえます。
- `bajutsu/common/scenario/models/actions/push.py` の `Push` に、検証を加えます。`aps` キーがあってその値がオブジェクトであること、`aps.alert` が文字列かオブジェクトであること、
  `title` と `body` がある場合に文字列であること、ペイロードが 4,096 バイト以下であることを求めます。測り方は上で述べたとおりです。長さを測る処理は
  1つの関数にまとめ、検証と `_do_push` が共有します。この関数は `json.dumps` の `TypeError` を捕まえます。
  そして、問題のあるキーのパスを示す `ValueError` を送出します。値は示しません。
- `bajutsu/common/scenario/models/actions/push.py` の `Push` の docstring を書き換えます。iOS の
  `simctl push` と Android の BajutsuAndroid レシーバの両方の経路を書きます。
- `bajutsu/common/orchestrator/substitution.py` の `_interp_step` を、`eval` と同じ形に変えます。
  書かれたままの `push` を検証し、そのあとで置換後のペイロードを付け直します。何も送出しません。
- `bajutsu/common/orchestrator/actions/handlers/device.py` の `_do_push` が、置換後のペイロードの長さを共有の関数で測ります。
  上限を超えたら `base.SelectorError` を送出します。メッセージには長さと上限だけを書きます。
- `aps` のない既存のフィクスチャや例は、同じ作業単位で直します。該当するのは1つです。
  `tests/test_codegen_uiautomator.py` の `test_device_control_and_helper_steps_are_labeled_todo` にある
  ペイロード `{}` を、`{ aps: { alert: hi } }` に変えます。ほかのプッシュのペイロードは、
  `tests/`、`demos/`、`docs/`、`bajutsu/` のどこでも、すでに `aps` を持っています。
- UI Automator のジェネレータ `bajutsu/codegen/uiautomator.py` は、`push` に対する理由つきの
  `// TODO` を残します。ただし、その理由は誤りになります。コメントは「adb のデバイス制御がない」と
  書いています。これを `// TODO: push — not generated; run it with bajutsu run` に変えます。同じ
  テストの接頭辞だけの確認を、新しい文字列全体の確認に強めます。

### ホスト側のデバイス制御と capability

Android のデバイス制御 `android_device_control` は、`bajutsu/common/platform_lifecycle/device_control.py`
にあります。この関数の `push` は、現在の `UnsupportedAction` に代えて `Env.push(package, payload)` を呼びます。
関数は今と同じく既存の `env_run` から `Env` を作り、`push_run` は渡しません。上で述べたデフォルトが働きます。
`real_run` の下ではプッシュに上限つきの実行関数が使われ、偽の `env_run` の下ではプッシュもその偽の関数に届きます。
`tests/test_android_device_control.py` はすでに `env_run` を差し替えているので、プッシュのケースに新しい引数は要りません。

`AndroidEnvironment` は、`controller` でこの制御を作ります。自身の `adb_run` を `env_run` として渡します。
偽の `adb_run` で環境を作るテストがあります。`tests/test_adb_lifecycle.py` と `tests/test_android_mock_location.py` です。
そこでのプッシュも、その偽の関数に届きます。`android_device_control` と `AndroidEnvironment` のどちらにも、引数は加えません。
このクラスは `platform_lifecycle/environments/android/android_environment.py` にあります。

`android_device_control` の `push` は、エラーの変換も担います。
`e.push(package, payload)` を囲んで `adb.DeviceError` を捕まえます。
そして `base.SelectorError(str(exc)) from None` を送出します。
対象は結果 `0`、`4`、`5` です。タイムアウトと adb の非ゼロ終了も対象です。
メッセージは原因です。

`_do_push` から `run_all` までの間に、`DeviceError` を捕まえる箇所はありません。
`DeviceError` は `RuntimeError` の子クラスなので、実行が中断します。
`loop/_functions.py` のステップのループは `SelectorError` を捕まえます。
そのため、どの結果もステップを原因つきで失敗させ、実行は続きます。

変換を `_do_push` には置きません。orchestrator はバックエンドに依存しません
（`orchestrator/types/device_control.py`）。import するのは `backend_cli` でも `devices.errors` でもありません。
基底の `DeviceError` をそこで捕まえると、iOS が変わります。
`simctl` によるプッシュのエラーがすべて、ステップの失敗になります。`simctl.DeviceTimeout` も含まれます。
BE-0363 は、このエラーをあえて伝播させます。
そこで `_do_push` に捕捉は加えず、iOS の挙動は今のままです。
`_do_push` の長さの検査は、すべてのバックエンドに対して `base.SelectorError` を送出し続けます。

`bajutsu/common/drivers/adb/adb_driver.py` の `AdbDriver.CAPABILITIES` に `deviceControl.push` を
加えます。これで capability の事前検査が、adb バックエンドでの `push` ステップを通します。
`clearKeychain` は未対応のままです。

### 生成される UI Automator のコード

本項目は、UI Automator のコードに `push` を生成しません。ジェネレータは理由つきの `// TODO` を
残し、文言だけを上のように直します。APNs から Android への対応づけは、ホスト側の Python にあります。
生成コードで使うには、その複製を持たせなければならず、2つの複製はずれていきます。
クリップボードのコード生成にならって、別の項目でこの経路を加えられます。

### showcase

Android の2つの双子アプリ `demos/showcase/android/compose` と `demos/showcase/android/views` は、
どちらもハンドラを付けて `Bajutsu.startPush` を呼びます。ハンドラはプッシュされたタイトルをアプリの
モデルに保存します。画面は、id が `perm.push.received` の要素にそのタイトルを表示します。Views の双子アプリ
では、この要素の id は `perm_push_received` です。Android のリソース id にはドットを使えないからです。
`perm.` の接頭辞により、この要素は `perm.location.reading` と同じ Permissions の名前空間に入ります。

要素は、Permissions タブに置きます。BE-0481 が `perm.location.reading` に使った画面です。このタブは、
どの視覚ベースラインやゴールデンにも含まれません。`visual_android.yaml` は `stable.png` を比べ、
`golden.yaml` は `goldens_android/lists.json` を確かめます。どちらの対象も Stable タブなので、
要素は Stable に置きません。要素はプッシュより前から存在し、値は空です。シナリオは、その値が
変わるのを待ちます。守る約束は id です。

- `demos/showcase/SPEC.md` と `SPEC.ja.md` は、Permissions タブである 5.4 節に `perm.push.received` を載せます。
  項目は `perm.location.reading` の隣に置きます。最後にプッシュされたタイトルを示す、
  Android のみの表示です。プッシュが届くまで空です。

- フォアグラウンドのバナーのシナリオを `push.yaml` から、iOS 専用の新しい
  `demos/showcase/scenarios/push_banner.yaml` に移します。このシナリオは `handleSystemAlert` と、
  SwiftUI 専用の起動環境を必要とします。Android のレーンはタグで絞り込まず、ファイルを丸ごと実行します。
- `push.yaml` には基本のシナリオだけを残し、iOS 専用の注記を外します。`device.yaml` と同じく、
  セレクタに Views の形を加えて `[stable.row.1, stable_row_1]` にします。
- `.github/workflows/ios-e2e.yml` の `actuation` ジョブは、`push.yaml` に加えて `push_banner.yaml` も
  列挙します。`demos/showcase/Makefile` の `systemalert` 除外のコメントは、新しいファイルを指すようにします。
- 新しい `demos/showcase/scenarios/push_android.yaml` は `{ aps: { alert: { title, body } } }` を
  プッシュし、タイトルが表示されるのを次の `wait` で待ちます。
  `wait: { for: { id: [perm.push.received, perm_push_received], value: <title> } }`
  タブへの移動は `location_android.yaml` と同じです。`{ label: "Permissions", traits: [button] }` を
  タップし、`[perm.requestNotif, perm_requestNotif]` を待ち、表示までスクロールします。
  検証は待ってから行い、`location_android.yaml` と同じ順です。ほかの端末上で動く `*_android.yaml` のシナリオと同じく
  `android` タグを付けるので、iOS の一括実行のレーンはこのファイルを除外します。
- `demos/showcase/android/Makefile` は、`E2E_SCENARIOS` と `E2E_VIEWS_SCENARIOS` の両方に `push` と
  `push_android` を加えます。
- 古くなるコメントは、1か所ずつ次のように直します。
  - `demos/showcase/scenarios/device.yaml`（11〜15 行目）は、push に Android の対応物がないと書いています。
    コメントには「has no faithful」とあります。16 行目は実行コマンドなので、そのまま残します。
  - `demos/showcase/android/Makefile`（86 行目、「half lives in push.yaml」）は、push の部分を iOS 専用と呼んでいます。
  - `.github/workflows/ios-e2e.yml`（621〜624 行目）は、`actuation` ジョブのシナリオ数を数えています。
    コメントは「push.yaml holds two since BE-0416」と書いています。
    この数は、`push.yaml` が2つのシナリオを持つ前提です。
    バナーのシナリオはファイルを移すだけなので、シナリオの実行回数は23回のままです。ファイル数は11から12になるので、
    「eleven files」は「twelve files」になり、「push.yaml holds two」の節は削除します。
  - `.github/workflows/ios-e2e.yml`（680〜681 行目、`push.yaml (BE-0281)`）は、`push.yaml` を iOS 専用と呼んでいます。
    作業単位 5 でこの項目の文言を直し、実行ステップの上にあるファイルごとのコメント一覧（670〜700 行目）に
    `push_banner.yaml (BE-0416)` の項目を加えます。
  - `.github/workflows/ios-e2e.yml`（612〜614 行目）は、BE-0322 の `actuation` ジョブを起動するシナリオを列挙しています。
    この一覧に `push_banner` を加えます。
  - `demos/showcase/ios/scenarios-noax/push.yaml`（1〜4 行目）と
    `demos/showcase/ios/scenarios-noax/device.yaml`（5 行目）は、`push` に Android の対応物がないと書いています。
  - `demos/showcase/ios/swiftui/Sources/ShowcaseApp.swift`（18〜20 行目）にコメントがあります。
    デリゲートを全モードで有効にすると、`push.yaml` の「最初のシナリオ」もバナーを出す、という内容です。
    これは基本のシナリオです。分割後の `push.yaml` はシナリオを 1 つしか持ちません。
    そのためコメントは、`push.yaml` の「シナリオ」と書きます。
  - `demos/showcase/scenarios/push.yaml`（24 行目）の BE-0416 のコメントブロックは、
    バナーのシナリオと一緒に移ります。移る先は `push_banner.yaml` です。
    ブロックには、「push.yaml 自身の上の最初のシナリオ」がバナーを出さない、と書いてあります。
    `push_banner.yaml` には、その上にシナリオがありません。
    そのためコメントは、`push.yaml` のシナリオを指す書き方に直します。
  - `tests/test_android_device_control.py`（406〜407 行目）には BE-0416 のコメントがあります。
    このコメントは、`push.yaml` が 2 つ目のシナリオを持つと書いています。
    分割後のファイルはシナリオを 1 つしか持たないので、この文を消します。
    テストは名前での検索をそのまま使います。

  `-noax` の双子 `scenarios-noax/push.yaml` は基本のシナリオだけを持ち、バナーの双子はありません。
  `-noax` のレーンは `--exclude android` だけを付けてディレクトリ全体を実行し、ほかの絞り込みはありません。
  分割が必要な箇所がないので、この項目は `scenarios-noax/push_banner.yaml` を追加しません。直すのはコメントだけです。

`tests/test_showcase_fixtures.py` のフィクスチャのテストに、2 件を加えます。
テストの名前は `test_dedicated_lane_scenarios_carry_their_exclusion_tag` です。加える内容は次のとおりです。
`("push_banner.yaml", "systemalert")` と `("push_android.yaml", "android")` です。
新しいファイルが、失敗するしかない一括実行のレーンに戻るのを防ぎます。

showcase はハンドラを登録するので、showcase のシナリオが動かすのはハンドラを使う処理です。デフォルトの
処理は、代わりに作業単位 4 で端末の上で確かめます。確認には、showcase の双子アプリをローカルでビルドして
使います。このビルドは `startPush(this)` を呼びますが、ハンドラを登録しません。コミットはしません。
エミュレータで通知を出し、通知の権限を外したときの失敗も確かめます。

API 34 で `background` ステップの後にプッシュする場合は、作業単位 3 が入った後で、bajutsu から動かします。
作業単位 4 は、3つの結果をこの項目の進捗ログに記録します。

### ドキュメント

ドキュメントの変更は、古い記述を誤りにするコードと同じ作業単位で行います。
そうしないと、2 つの PR の間で、ページが説明対象のコードと食い違います。
`CLAUDE.md` の同じ変更で更新する規則（BE-0113）は、これを防ぐためにあります。

作業単位 2 はペイロードの規則を加えるので、ペイロードの形を述べるページを更新します。

- `docs/dsl-grammar.md`（261 行目）と `docs/ja/dsl-grammar.md`（259 行目）は、`push` の文法を示しています。
  そこではペイロードが `map(string,any)` で、制約はありません。
  どちらのページにも、読み込み時の規則を書きます。ペイロードには `aps` のオブジェクトが必要です。
  `alert` は文字列か、`title` と `body` が文字列のオブジェクトです。ペイロードは 4,096 バイト以下です。
- `### setLocation / push (device control)` の節にも、同じ規則を書きます。
  場所は `docs/scenarios.md` の 1621〜1630 行目と `docs/ja/scenarios.md` の 1150〜1158 行目です。
  長さは、デフォルトの `json.dumps` の文字列で測ります。
  置換後のペイロードが上限を超えたステップは、実行時に失敗します。

作業単位 3 は Android で `push` を動かします。そうでないと書いているページは、すべて同じ作業単位で直します。

- capability の表とその下の注記を直します。注記は、Android エミュレータが `push` と `clearKeychain`
  以外のすべての操作を実現すると書いています。場所は `docs/drivers.md` の 106〜108 行目と、
  `docs/ja/drivers.md` の 99〜100 行目です。これを `clearKeychain` だけを挙げる形に直します。
  2 つの parity のページでは、`deviceControl.push` の行の Android を ✅ にします。詳しくは作業分解に書きます。
- `docs/drivers.md` の adb の節にある「adb が公開する capability」の一覧（120〜131 行目）に、
  `deviceControl.push` を加えます。届け方は BajutsuAndroid を通じたものです。
  一覧の直後の文（133 行目）は、`push` と `clearKeychain` を非公開としています。
  この文は、`clearKeychain` だけを挙げる形に直します。`docs/ja/drivers.md`（122 行目）も同じように直します。
- adb の節にある**デバイス制御**の項目に、BajutsuAndroid 経由の `push` を加えます。
  対象は `docs/drivers.md` の 900〜901 行目と、`docs/ja/drivers.md` の 700 行目です。
  どちらも「一族の残りは未対応のままです」という趣旨の文で終わっています。
  未対応として残る操作は `clearKeychain` だけになります。
- `docs/scenarios.md` と `docs/ja/scenarios.md` にある `push` の記述 2 か所を直します。
  - ステップの表の行は、`docs/scenarios.md` の 1049 行目と `docs/ja/scenarios.md` の 753 行目にあります。
    ここには、iOS では `simctl push`、Android ではアプリ内の BajutsuAndroid の受信側で配信すると書きます。
  - `### setLocation / push (device control)` の節に、Android への対応づけを加えます。権限の規則も加えます。
    iOS では 2 つのステップとも `simctl` で Simulator を操作します。
    Android では、`setLocation` がエミュレータのコンソールかアプリ内の受信側（BE-0481）を使います。
    `push` は BajutsuAndroid の push 受信側を使います。
- `docs/codegen.md`（712 行目付近）と `docs/ja/codegen.md`（693 行目付近）は、「`push` には adb の
  経路そのものがない」と書いています。`push` は `run` で動くと改めます。生成される UI Automator の
  コードは、`bajutsu run` に任せます。
- `docs/architecture.md`（441 行目付近）と `docs/ja/architecture.md`（430 行目付近）は、
  `push` にはアプリ内のレシーバが必要だと書いています。どちらも、BajutsuAndroid を通す経路の説明に改めます。
- `docs/platform-parity.md` と `docs/ja/platform-parity.md`（17 行目付近）は、capability のギャップの例として
  `deviceControl.push` を挙げています。これを `handleNotificationBanner` に差し替えます。この
  capability は、表でギャップのまま残るからです。
- `docs/drivers.md`（1081 行目付近）と `docs/ja/drivers.md`（838 行目付近）の Flutter の注記から、
  `push` を iOS 専用とする記述を外します。代わりに、Flutter の Android レーンが `push` を引き続き除外する理由を書きます。
  Flutter アプリが BajutsuAndroid を組み込まないからです。

作業単位 5 は showcase のレーンを変えるので、レーンを説明するページを更新します。

- `docs/ci.md` の `android-e2e.yml` の行（英語と日本語）に、smoke ジョブが `push.yaml` と
  `push_android.yaml` を実行するようになったと書きます。push の部分を iOS 専用とする記述は外します。
- `docs/ci.md` の `ios-e2e.yml` の行（英語と日本語）は、`push.yaml` と並べて `push_banner.yaml` を挙げます。
- `demos/showcase/Makefile` のコメント（553〜560 行目付近）から、`push` を iOS 専用とする記述を外します。
  Flutter の注記と同じく、Flutter の Android レーンが `push` を引き続き除外する理由を書きます。

作業単位 6 は、作業単位 4 の結果を書きます。API 34 で `background` ステップの後にプッシュする場合が、
対応済みか未対応のどちらかです。

- `docs/drivers.md` と `docs/ja/drivers.md` の**デバイス制御**の項目に、結果を書きます。
- `docs/platform-parity.md` と `docs/ja/platform-parity.md` の「振る舞いの差」の表に、`push` の行を加えます。
  - iOS：`simctl push` が APNs のペイロードを届けます。
  - Android：アプリ内のレシーバがペイロードを対応づけ、アプリのハンドラを呼ぶか、ローカル通知を出します。
  - 注記：アプリが起動中で、BajutsuAndroid を組み込んでいる必要があります。
    デフォルトの経路では `notifications` の権限が要ります。作業単位 4 の結果も書きます。

`BajutsuAndroid/README.md` と `README.ja.md` は、この節ではなく作業単位 1 の担当です。作業単位 1 が
`Bajutsu.startPush`、ハンドラ、結果コード、`POST_NOTIFICATIONS` の宣言を書きます。

### 作業分解

| # | 作業単位 | ファイル | 完了条件 |
|---|---|---|---|
| 1 | プッシュ用のレシーバ、ハンドラ、`startPush` | `BajutsuAndroid/`（`README.md` と `README.ja.md` を含む） | ライブラリがビルドでき、2つの README が API と `POST_NOTIFICATIONS` の宣言を説明し、ハンドラの例外メッセージにペイロードを含めてはならないと書いている |
| 2 | ペイロードの対応づけ、コマンドの生成、結果の解釈、上限つきの実行経路、`Env.push`、`Push` モデルの検証、`aps` のないフィクスチャ、UI Automator の `push` のプレースホルダにある正しい理由 | `bajutsu/common/backend_cli/adb/`、`bajutsu/common/scenario/models/actions/push.py`、`bajutsu/common/orchestrator/substitution.py`、`bajutsu/common/orchestrator/actions/handlers/device.py`、`bajutsu/codegen/uiautomator.py`、`tests/test_codegen_uiautomator.py`、`tests/` の新しいユニットテスト、`docs/dsl-grammar.md` と `docs/ja/dsl-grammar.md`、`docs/scenarios.md` と `docs/ja/scenarios.md` のペイロードの規則の記述 | 2つの表のすべての行、`aps`、型、サイズの規則（`{aps: "x"}`、`{aps: {alert: 5}}`、`{aps: {alert: {title: {a: 1}}}}` をそれぞれ検証のテストが拒否します。引用符のない日付を含むペイロードが読み込み時に `ValidationError` で失敗することも確かめます）、タイムアウトと非ゼロ終了のエラーをユニットテストが確かめ、偽の `run` で作り `push_run` を渡さない `Env` がプッシュのブロードキャストをその偽の関数に通すことを確かめ、`${vars.*}` と `${secrets.*}` を展開すると 4,096 バイトを超えるペイロードを試すテストがあり、そのテストは中断された実行ではなく失敗したステップを確かめ、bajutsu が書くエラーの文言にペイロードの文字列は含まれず、結果 `0`、`4`、`5`、タイムアウト、adb の非ゼロ終了がそれぞれ、`adb.DeviceError` になり、bajutsu が書くメッセージの文言にペイロードの文字列が含まれないこと（結果 `5` はハンドラのメッセージをそのまま渡します）をユニットテストが確かめ、プレースホルダのテストが接頭辞だけでなく新しい文言を確認し、既存のテストも通る |
| 3 | デバイス制御、capability、capability の表 | `device_control.py`、`android_environment.py`、`adb_driver.py`、`bajutsu/common/drivers/base/capability.py`、`bajutsu/common/capability/capability_preflight.py`、`docs/platform-parity.md` と `docs/ja/platform-parity.md`（振る舞いの差の表の行を除く）、`docs/drivers.md`、`docs/ja/drivers.md`、`docs/scenarios.md` と `docs/ja/scenarios.md` のステップの表の行と Android の記述、`docs/codegen.md`、`docs/ja/codegen.md`、`docs/architecture.md`、`docs/ja/architecture.md`、`tests/test_android_device_control.py`、`tests/test_capability_preflight.py` | 事前検査が adb での `push` を通し、結果 `0`、`4`、`5`、タイムアウト、adb の非ゼロ終了がそれぞれ、`android_device_control` を通って、中断された実行ではなく失敗したステップになり、bajutsu が書くメッセージの文言にペイロードの文字列は含まれず（結果 `5` はハンドラのメッセージをそのまま渡します）、`tests/test_platform_parity.py` とユニットテストが通る |
| 4 | エミュレータでデフォルトの処理とバックグラウンドからの配信を確かめる | コミットするものはなし（進捗ログ） | 通知が出ること、権限がないときの失敗、API 34 での `background` ステップ後のプッシュの3つをすべて確認した |
| 5 | showcase のハンドラ、表示、シナリオ、古くなるコメント | `demos/showcase/`（`SPEC.md` と `SPEC.ja.md` を含む）、`.github/workflows/ios-e2e.yml`、`tests/test_showcase_fixtures.py`、`tests/test_android_device_control.py`（コメント）、`docs/ci.md` と `docs/ja/ci.md` | `push.yaml` と `push_android.yaml` が Android エミュレータで成功し、`push_banner.yaml` が iOS で成功し、showcase の2つの仕様ファイルに `perm.push.received` があり、フィクスチャのテストが新しい2つのタグを確認する |
| 6 | 作業単位 4 の結果をドキュメントに書く | `docs/drivers.md` と `docs/ja/drivers.md` の**デバイス制御**の項目、`docs/platform-parity.md` と `docs/ja/platform-parity.md` の振る舞いの差の表 | `make check` が通る |

作業単位 4 は、`startPush(this)` を呼ぶがハンドラを登録しない showcase の双子アプリを、使い捨てでビルドします。
これがデフォルトの経路に届く唯一の方法で、ビルドはローカルに留めます。`background` ステップの後の
プッシュは bajutsu から動かすので、capability が要ります。このため作業単位 4 は作業単位 3 の後に置きます。

作業単位 3 では、`tests/test_android_device_control.py` にある、従来の拒否を固定したテストを反転します。

- `test_push_scenario_is_rejected_by_preflight_on_adb` は、事前検査が `push.yaml` を通すことを確かめます。
  名前も `test_push_scenario_is_admitted_by_preflight_on_adb` に変えます。
- `test_adb_advertises_the_device_control_subset_it_can_honor` は、`DC_PUSH` が `caps` に含まれることを確かめます。
- `test_preflight_rejects_unsupported_steps_on_adb` の `push` のパラメータは、
  `test_preflight_admits_supported_steps_on_adb` に移します。
- `UnsupportedAction` を期待していた `push` のケースは、`Env.push` による配信を期待します。
- `push.yaml` を iOS 専用とするコメントも、これに合わせて直します。
- このテストファイルのモジュールの docstring（7〜8 行目）は、`push` は未対応のままだと書いています。Android ではアプリ内の
  レシーバを通して配信すると書き換えます。

- `android_device_control` の docstring（`device_control.py`、90〜93 行目）は、`push` が例外を送出すると書いています。
- `CAPABILITIES` のコメント（`adb_driver.py`、2543〜2544 行目）は、`push` を通知しないと書いています。
  該当の文には「all but `push`」とあります。
- `AndroidEnvironment` クラスの docstring（`android_environment.py`、111〜114 行目）があります。
  対応するサブセットを `push` なしで挙げ、
  「the rest of the family stays unsupported」で終わっています。
  BajutsuAndroid の push レシーバを通して配信する `push` を加えます。
  未対応の操作は `clearKeychain` だけになります。
- `controller` のコメント（`android_environment.py`、591〜596 行目）は、`push` が例外を送出すると書いています。
  コメントの書き出しは「The subset Android can honor」です。
- `bajutsu/common/drivers/base/capability.py`（94〜96 行目）と
  `bajutsu/common/capability/capability_preflight.py`（43〜45 行目）のコメントは、`setLocation` と
  クリップボードだけに対応する backend の例として、Android エミュレータを名指ししています。
  名指しをやめ、一族の一部に対応する backend として一般的に書き直します。
- `tests/test_capability_preflight.py` は、`_SUBSET` を Android エミュレータと説明しています（46〜48 行目）。
  エミュレータが対応できない操作の例にも `push` を使っています（270 行目と 289 行目）。
  `_SUBSET` は合成した一部対応の backend だと書き直します。
  `_SUBSET` は固定値なので、テストのデータは変えません。

capability の表は作業単位 3 の担当です。表が `AdbDriver.CAPABILITIES` と一致するまで、
`tests/test_platform_parity.py` は失敗します。`docs/platform-parity.md` と `docs/ja/platform-parity.md` では、
`deviceControl.push` の行の Android を ✅ にします。注記は隣の行と同じ形にします。
Android ではアプリに BajutsuAndroid ライブラリが必要であることを書き、振る舞いの差の表へ誘導します。
この表は、`parity-table` と `settings-table` の各マーカー以降の行を読むテストの対象外です。
そのため、振る舞いの差の表に加える行は、作業単位 6 の担当です。

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

- [x] 作業単位 1：BajutsuAndroid の `PushReceiver`、`BajutsuPushHandler`、`Bajutsu.startPush`、README（英語と日本語）
- [x] 作業単位 2：adb のコマンド経路と `Push` モデルの検証、フィクスチャ、ペイロードの規則のドキュメント
- [x] 作業単位 3：Android の `push` とその capability、反転したテスト、`push` を iOS 専用とするドキュメント
- [x] 作業単位 4：エミュレータで、デフォルトの通知の処理、権限がないときの失敗、API 34 での `background` ステップ後のプッシュを確かめる
- [x] 作業単位 5：showcase のハンドラ、表示、シナリオと、関係するドキュメントやコメント
- [x] 作業単位 6：ドライバと parity のドキュメントに書くバックグラウンド配信の結果（英語と日本語）

ログ：

- 2026-11-20：作業単位 1〜6 をまとめて実装しました（[#2492](https://github.com/bajutsu-e2e/bajutsu/pull/2492)）。作業単位 4 は、API 34 のエミュレータ（Pixel 8 の
  イメージ）で `bajutsu run` を通して実施しました。使ったのは、`startPush(this)` がハンドラを登録しない
  showcase のローカルビルドです。確かめたのは次の 3 点です。
  - ハンドラのない経路は、`bajutsu` チャネルに通知を出しました。通知には送ったタイトルと本文、アプリを
    開く content intent が入っていました。
  - `permissions: { notifications: revoke }` のもとでは、レシーバは `4` を返しました。ステップは
    `permissions` の事前条件を示すメッセージで失敗し、実行は続きました。
  - `background` ステップの直後のプッシュも、25 秒後のプッシュも遅れずに届きました。プッシュの
    ステップはフォアグラウンドでの 2.2 秒に対して 2.6 秒でした。プロセスは直前のアプリの状態
    （メモリ不足時の調整値 700）にあり、凍結されていませんでした。そのため作業単位 6 では、この場合を
    対応済みとしました。

  詳細設計との違いは 2 つあります。
  - showcase のハンドラは、タイトルをアプリのモデルではなく、プロセス全体の `PushInbox` に保存します。
    ハンドラは `Application.onCreate` で登録するため、その時点では Activity もモデルもまだありません。
  - Android のレーンは、`push.yaml` と `push_android.yaml` を両方の双子でローカルに実行しました。
    `push_banner.yaml` は中身を変えずに移したので、iOS での確認は `ios-e2e.yml` の `actuation` ジョブが
    行います。

## 参考

- Issue [#2397](https://github.com/bajutsu-e2e/bajutsu/issues/2397)：本項目が埋める、Android で未対応の部分です。
- [`docs/ja/platform-parity.md`](../../docs/ja/platform-parity.md)（英語版は `docs/platform-parity.md`）：iOS と Android の capability の違いです。
- [`BajutsuAndroid/src/main/java/dev/bajutsu/android/ClipboardReceiver.kt`](../../BajutsuAndroid/src/main/java/dev/bajutsu/android/ClipboardReceiver.kt)：本項目がならうレシーバの形です。
- [`demos/showcase/scenarios/push.yaml`](../../demos/showcase/scenarios/push.yaml)：本項目が両プラットフォームで動くようにするシナリオです。
