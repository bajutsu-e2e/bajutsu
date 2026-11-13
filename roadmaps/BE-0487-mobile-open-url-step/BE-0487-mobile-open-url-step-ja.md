[English](BE-0487-mobile-open-url-step.md) · **日本語**

# BE-0487 — iOS と Android でシナリオの途中に URL を開く openUrl ステップ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0487](BE-0487-mobile-open-url-step-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0487") |
| トピック | シナリオ作成機能 |
<!-- /BE-METADATA -->

## はじめに

この項目では、モバイルのシナリオの任意の位置で URL を OS に渡す `openUrl` ステップを追加します。
書き方は `- openUrl: "myapp://orders/42"` のとおりで、iOS と Android の両方で動きます。URL を受け取るのは、
その時点でシナリオが操作しているアプリです。通常はテスト対象のアプリで、`app:` ブロックの中ではそのブロックが
指定したアプリになります。ステップは URL を渡したあと、受け取るアプリが前面に来るまで待ちます。OS が URL を
受け付けなかった場合と、アプリが前面に来なかった場合は、理由を添えてステップを失敗にします。

あわせて、起動時の deeplink が Android で抱える2つの不具合を直します。新しいステップが同じコマンドを使うためです。
現在の `subject.launch.deeplink` は、URL を扱える Activity が無くても黙って成功します。また、`&` を含む URL は
端末側のシェルで分断されます。

## 動機

現在のモバイルのシナリオは、起動時に一度だけ `subject.launch.deeplink` で deeplink を開けます。この deeplink は
どのステップよりも先に開くので、サインイン済みのユーザーを前提とする画面には届きません。URL が開く時点では、
シナリオがまだサインインしていないからです。環境層には `openurl` の呼び出し（`simctl openurl` と
`adb shell am start`）がありますが、ステップからは呼べません。

この項目で求めるステップは、web バックエンドにはすでにあります。
[BE-0464](../BE-0464-web-navigate-step/BE-0464-web-navigate-step-ja.md) が追加した `navigate` は、シナリオの途中で
ブラウザに URL を読み込みます。BE-0464 は、iOS の deeplink と Android の intent を意図して対象外にしました。
そのうえで、そうしたステップが決めるべき3つの論点を挙げています。URL のスキームをどのアプリが解決するか、iOS の
確認ダイアログ、呼び出しを載せる受け口の3つです。この項目は、モバイルのバックエンドについて3つの論点を決めます。

この欠落は、シナリオをプラットフォーム間で移植する妨げにもなっています。iOS では、サインインしたあとに標準の
ブラウザを前面に出し、アドレス欄に URL を入力して deeplink を開くシナリオを書けます。Android には同じ手順で
置き換えられる手段がないので、同じシナリオを移植できません。どちらのプラットフォームでも、回避策は
別のアプリを経由した UI 操作になります。その操作のタップ1つ1つが、テスト対象の画面とは無関係な理由でシナリオが
失敗しうる箇所になります。

Android の起動時 deeplink には、別の不具合もあります。`adb shell am start` は、intent を解決できないと
`Error: Activity not started, unable to resolve Intent` を出力したうえで、終了コード 0 を返します。adb のラッパーは
終了コードしか見ないので、deeplink が開かなかった実行も、違う画面のまま先へ進みます。最初に失敗したステップは、
本当の原因とは別の箇所を指すことになります。

この項目の効果は、次の2点で確かめられます。サインインしたあと `openUrl` で deeplink を開き、開いた画面でタップと
アサーションを行うシナリオが、何度流しても同じように通ります。どのアプリも扱えない URL を渡したシナリオは、後続の
無関係なセレクタではなく、`openUrl` のステップか起動の時点で失敗します。理由には、`am start` やランナーの返した
内容が載ります。

## 詳細設計

### 書き方

```yaml
steps:
  - use: signIn
  - openUrl: "showcase://tabs/settings"          # テスト対象のアプリが URL を受け取る
  - assert: [{ exists: { id: settings.title } }]
  - openUrl: "showcase://items/${vars.itemId}"   # ${...} は他のフィールドと同じく展開される
  - app:
      bundleId: ${vars.browserId}                # ブラウザが URL を受け取る
      steps:
        - openUrl: "https://example.com/promo"
        - tap: { label: "Open in app" }
```

`openUrl` は通常のアクションです。1つのステップは1つのアクションを持ち、共通の修飾子（`name`、`capture`、
`target`）も他のアクションと同じように使えます。YAML の値は単なる文字列です。`url` フィールドを1つ持つ
`OpenUrl` モデルが、`before` バリデーターでこの文字列を受け取ります。これは `Navigate` と同じ手法です
（`bajutsu/common/scenario/models/actions/navigate.py`）。バリデーターはマッピングを受け付けないので、
`{url: ...}` が文書化されない公開の形になることはありません。

ステップが受け付けるのは、スキームを持つ絶対 URL です。スキームの種類は問いませんが、例外が3つあります。
規則は次の表のとおりです。

| 値 | 結果 |
|---|---|
| `myapp://path` や `https://host/path` | 書いたとおりに OS へ渡します |
| `/settings` のようにスキームを持たない値 | 拒否します。モバイルには解決の基準になる `baseUrl` が無いためです |
| `javascript:`、`data:`、`file:` の URL | 拒否します。どれもアプリ内の行き先を指さないためです |

読み込み時の検査は、`${...}` を含まない値を対象にします。`${...}` を含む値は、展開したあとにハンドラーがもう一度
検査します。規則は1つの共通関数にまとめ、2つの検査はどちらもその関数を呼びます。`navigate` の
`navigate_url_problem` と同じ構成です。

### URL を受け取るアプリ

ステップは、その時点でドライバーが操作しているアプリに URL を渡します。2つのモバイルドライバーは、`app:` ブロックの
ために、操作中のアプリをすでに管理しています。

- XCUITest のランナーはアプリのスタックを持ち、`/app/enter` で積み、`/app/leave` で降ろします。
- adb ドライバーは `_app_stack` を持ちます。`_front_package` は、スタックの先頭か、無ければテスト対象のアプリを
  返します（`bajutsu/common/drivers/adb/adb_driver.py:666`）。

`app:` ブロックの外では、URL はテスト対象のアプリに届きます。ブラウザを指定した `app:` ブロックの中では、
URL はそのブラウザに届きます。ブラウザで URL を開きたい場合は、ステップを `app:` ブロックで包みます。同じブロックの中で、
そのままブラウザの UI も操作できます。`openUrl` に宛先のフィールドを足すと、受け取るアプリの指定方法が2通りに
なります。しかも、そのアプリを操作するには結局 `app:` ブロックが要ります。

URL を1つのアプリに向けることで、ステップは決定的になります。宛先を絞らない Android の `VIEW` intent は、同じ URL
を扱うアプリが2つ入っていると、アプリの選択ダイアログを出します。結果が、端末に入っているアプリ次第で変わって
しまいます。

### URL が開いたかの判定

ステップは、次の2つの条件が揃ったときに成功します。

1. OS が、受け取るアプリへの URL を受け付けます。
2. 受け取るアプリが、同じバックエンドの `app:` ブロックと同じ上限の中で前面に来ます。上限は、Android では
   adb ドライバーの `_APP_FRONT_TIMEOUT`（30秒）、iOS ではランナーの前面の待ち（0.5秒おきに20回、約10秒）です。

ステップは、画面の中身を待ちません。通信のあとで画面を開く deeplink のように、表示が遅れる場合は、作者が後続の
`assert` か `wait` で待ちます。ランナーの条件待ちがこの場合をすでに扱えます。ステップから中身の待ちを外しておくと、
アプリごとの知識もステップに入りません（基本原則 3）。

### iOS の経路

ランナーに `POST /app/openUrl` のルートを1つ追加します。本文は `{url}` です。このルートは、スタックの先頭のアプリに
対して `XCUIApplication.open(_:)` を呼び、アプリの状態が `runningForeground` になるまで待ちます。待ちが上限に達したら、
現在の `/app/enter` と同じ `not-foreground` を返します。

シナリオの途中では、受け取るアプリはたいてい前面にあるので、前面の待ちだけでは OS が URL を受け付けたことを示せません。
そこでルートは、`XCUIApplication.open(_:)` をランナーの `ObjCExceptionCatcher` の中で呼びます。ランナーは要素の操作でも
すでに同じことをしています（`BajutsuKit/Sources/BajutsuRunner/APIHandler.swift`）。呼び出しが例外か XCTest の失敗を
起こしたら、その内容を理由とする新しい `not-opened` の返答にします。ドライバーは `not-opened` をステップの失敗に
対応づけます。単位 5 は、受け取るアプリが宣言していないスキームに対して `open(_:)` が何をするかを、Simulator で
確かめた時点で完了とします。その URL で `open(_:)` が何も起こさない場合は、その制約をドキュメントに書きます。iOS では
そうした URL でもステップは通り、後続のアサーションが違う画面を検出します。

`XCUIApplication.open(_:)` は iOS 16.4 から使えます。
ランナーのデプロイメントターゲットは iOS 15.0 です（`BajutsuKit/Runner/project.yml`）。そのため、呼び出しを
`#available(iOS 16.4, *)` で囲み、それより前の OS では `unsupported` を返します。`unsupported` は `ActuationReply` の新しい状態で、
`BajutsuKit/Sources/BajutsuRunner/openapi.yaml` の列挙に加えます。XCUITest ドライバーはこれを `UnsupportedAction` に
対応づけます。対応づけが無いと、ドライバーの既定の扱いで `XcuitestChannelError` になり、ランナーの障害に見えて
しまいます。preflight は端末の iOS のバージョンを知らないので、iOS 16.3 以前ではステップの時点で失敗します。
端末を借りたあとの失敗です。

ランナーのルートは、Simulator と実機の両方で動きます。`simctl openurl` は Simulator にしか届かず、受け取るアプリも
指定できないので、このステップでは使いません。iOS の起動時 deeplink は現在の経路のままで、この項目では変えません。

ステップのあとに、OS の確認ダイアログが出ることがあります。たとえば、ブラウザのページが別のアプリへ URL を渡す
ときです。その時点でステップはすでに終わっています。ステップが URL を渡したブラウザは、もう前面にあるからです。
このダイアログはシステムアラートなので、`interrupts` の `systemAlert` エントリーか、後続の `handleSystemAlert`
ステップで閉じられます。ステップ自身の前面の待ちはドライバーとランナーの中で動き、そこではアラートのガードが
動きません。そのため、受け取るアプリ自身が前面に来るのを妨げるダイアログがあると、ステップは `not-foreground` で
失敗します。
ステップが自分でダイアログを閉じることはしません。「開く」を自動でタップすると、実際のユーザーには見えるはずの
確認を隠してしまうからです。

### Android の経路

adb ドライバーは `am start -W --activity-single-top -a android.intent.action.VIEW -d <url> <package>` を
実行します。`<package>` には `_front_package` を渡します。ドライバーが操作するアプリを持たず `_front_package` が `None` の
ときは、`am start` を実行する前に、名前の付いたエラーでステップを失敗にします。`<package>` を省くと、*検討した代替案*
で退けた、宛先を絞らない intent になってしまうからです。ドライバーは、パッケージが無いときに失敗する方へ倒す扱いを、
ほかの箇所でもすでにとっています（`bajutsu/common/backends.py`）。`-W` を付けると、`am start` は起動を待ってから状態を
出力します。

`--activity-single-top` は、起動時 deeplink と同じ URL で画面へ戻れるようにするために付けます。このオプションが
無いと、Android は新しい intent を、アプリのタスクを開始した intent と比べます。両者が一致すると、Android は
タスクを前面に出すだけで、何も届けません。オプションを付けると、Android は先に、受け取る Activity がタスクの
先頭にいるかを確かめます。先頭にいれば、比較の前に、その Activity が `onNewIntent` で URL を受け取ります。
1つの Activity の中で画面を切り替えるアプリは、そのためどの URL も受け取れます。別の Activity が先頭にある
複数 Activity のアプリでは、比較が行われます。URL がタスクを開始した intent と一致すれば、ステップは下の表の
とおり失敗します。アプリ側は、`onNewIntent` で
deeplink を処理する必要があります。deeplink を受け付けるアプリの多くは、すでに処理しています。

終了コードは intent の拒否を表しません。また、現在の `real_run` は標準出力しか返しません。そこでドライバーは、
終了コードにかかわらず標準出力と標準エラー出力をあわせて読みます。そのうえで、2種類の障害を分けます。
`Error` の行のような `am start` 自身の出力は、その行を理由にステップを失敗にします。`error: device offline` や
`error: device not found` のような adb の転送の障害は、現在と同じく `DeviceError` にします。端末を失ったのは
基盤の障害であって、URL の拒否ではないからです。iOS の経路も、ランナーの障害とテストの結果を同じように分けて
います。

出力ごとの扱いは次の表のとおりです。複数の行が当てはまるときは、`Status: ok` よりも、intent を拒否する行を
優先します。`-W` は、警告のあとにも状態の行を出力するからです。

| `am start` の出力 | 結果 |
|---|---|
| 拒否の行を含まない `Status: ok` | 受け付けたものとします |
| `Warning: Activity not started, intent has been delivered to currently running top-most instance.` | 受け付けたものとします。起動中の Activity が `onNewIntent` で intent を受け取っているためです |
| `Error` で始まる行 | 拒否されたものとし、その行を理由にします |
| `Warning: Activity not started, its current task has been brought to the front` | 拒否されたものとし、その行を理由にします。Android は URL を届けずにタスクを前面に出しただけだからです |
| `java.lang.SecurityException` のように例外を名指す行 | 拒否されたものとし、その行を理由にします |
| `Status: timeout` のようなその他の出力 | 拒否されたものとし、出力の全体を理由にします |

受け付けたと判断するには、`Status: ok` か配信の警告という、出力の側の積極的な合図が要ります。前面の待ちは、その合図の
代わりになりません。シナリオの途中では、受け取るアプリがたいてい resumed の Activity をすでに持っているので、待ちは
最初の確認で終わってしまうからです。intent が受け付けられたら、ドライバーは `_front_package` が resumed の Activity を持つまで待ちます。この待ちには、
`app:` ブロックのためにすでに `dumpsys` を読んでいる `_bring_forward` のポーリングを使います。

`adb shell` は引数を1行のコマンドにまとめ、端末側のシェルがその行を解釈します。そこでドライバーは、入力した文字列に
対する `text_script` と同じく、URL を `shlex.quote` で囲みます。囲まないと、クエリ文字列の `&` でコマンドが
途切れます。細工した URL で、端末上のコマンドを実行されるおそれもあります。

### 起動時 deeplink の修正

`deeplink_cmd`（`bajutsu/common/backend_cli/adb/_functions.py:1004`）と、環境の `open_url` にも、同じ変更を
入れます。`-W` を付けること、URL を引用符で囲むこと、上の表に沿って出力を検査することです。これで、拒否された起動時
deeplink は、`Error` の行を理由に起動の時点で失敗します。この検査には `-W` が欠かせません。`-W` が無いと、
`am start` は `Error` の行を標準エラー出力に書くことがあり、`real_run` がそれを捨ててしまうからです。開かない deeplink を抱えたまま通っていたシナリオは、今後は起動の時点で
失敗します。この変化は意図したもので、変更履歴にもそう書きます。

### capability と preflight

ステップは、新しい `Driver.open_url(url)` メソッドと、新しい `openUrl` の capability トークンに載せます。XCUITest
ドライバーは、Simulator と実機の両方でこのトークンを提示します。adb ドライバーも提示します。それ以外のドライバーは
`UnsupportedAction` を送出します。トークンを持たないバックエンドでこのステップを使うシナリオは、端末を借りる前に
preflight で拒否されます。

ただし、preflight の汎用の走査は `web:` と `app:` のブロックと `interrupts` の復旧ステップをたどりません。そこで
`openUrl` には、`navigate` の `_navigate_locations` と同じく3つをすべてたどる専用の検出を用意します
（`bajutsu/common/capability/capability_preflight.py`）。iOS と Android では、トークンの検査だけでは `web:` ブロックの
中の `openUrl` を拒否できません。preflight が見るのは、`WebContextDriver` ではなくバックエンドのトークンだからです。
この規則はバックエンドに依存しないので、preflight ではなくシナリオのモデルに置きます。モデルは、`installApp` と同じく、
`web:` ブロックの中の `openUrl` をシナリオの読み込み時に拒否します（`bajutsu/common/scenario/models/scenario/_targets.py`）。
`unsupported()` の中に検査を置くと、この関数が判断する capability とは無関係な理由になります。しかも `requirements()` には
反映されないので、serve が受け付けて配った仕事を、実行時の preflight が拒否することになります。
web バックエンドは `navigate` を使い続け、`openUrl` は提示しません。`navigate` はブラウザの中でページを読み込み、
`openUrl` は OS に URL を渡します。2つに同じステップ名を付けると、シナリオがどちらを意図しているのかが曖昧になります。

`interrupts` の復旧ステップでも、多くの場合は変更なしで `openUrl` を使えます。復旧ステップは中断されたステップの
ドライバーで動き、`web:` ブロックの外ではそれがネイティブのドライバーだからです。`web:` ブロックの中のステップの
最中に発火した復旧は、`WebContextDriver` で動きます（`bajutsu/common/orchestrator/loop/_interrupt_guard.py`）。
そこでの `openUrl` は、実行時に `UnsupportedAction` で失敗します。どのステップの最中に割り込みが発火するかは、
preflight からは分かりません。

### ドキュメント

文法、書き方の手引き、parity 表の3つに、英語と日本語の両方でステップを載せます。

- `docs/dsl-grammar.md` に、`openUrl` の生成規則とスキームの規則を加えます。
- `docs/scenarios.md` に、サインインしてから deeplink を開く例と、ブラウザで開く例の節を加えます。
- `docs/platform-parity.md` に `openUrl` の行を加え、iOS と Android を ✅ にします。注記には、web バックエンドでは `navigate` を使うことを書きます。
- `docs/platform-parity.md` の `urlNavigation` の行の注記から、モバイル向けの `openUrl` を案内します。

### 作業の分割

| 単位 | 範囲 | ファイル |
|---|---|---|
| 1. シナリオのモデル | `OpenUrl` モデル、共通のスキーム規則、読み込み時の検査（スキームと、`web:` の下の `openUrl` の禁止）、`Step.open_url` フィールド | `bajutsu/common/scenario/models/actions/`、`bajutsu/common/scenario/models/steps/step.py`、`bajutsu/common/scenario/models/scenario/_targets.py` |
| 2. ドライバーの受け口と preflight | `Driver.open_url`、`OPEN_URL` capability、preflight の検出、ハンドラー、fake ドライバー | `bajutsu/common/drivers/base/`、`bajutsu/common/capability/capability_preflight.py`、`bajutsu/common/orchestrator/actions/handlers/navigation.py`、`bajutsu/common/drivers/fake/` |
| 3. Android の経路 | adb ドライバーでの `--activity-single-top` 付きの引用符付きコマンド、出力の検査、前面の待ち | `bajutsu/common/backend_cli/adb/`、`bajutsu/common/drivers/adb/adb_driver.py` |
| 4. 起動時 deeplink の修正 | `subject.launch.deeplink` での `-W`、引用符付き URL、出力の検査 | `bajutsu/common/backend_cli/adb/_functions.py`、`bajutsu/common/backend_cli/adb/env.py`、`bajutsu/common/platform_lifecycle/environments/android/` |
| 5. iOS の経路 | ランナーの `/app/openUrl` ルート、`not-opened` と `unsupported` の返答、XCUITest ドライバーからの呼び出し | `BajutsuKit/Sources/BajutsuRunner/`、`bajutsu/common/drivers/xcuitest/` |
| 6. showcase のシナリオ | シナリオの途中で `openUrl` を使ってタブに移り、iOS と Android で流すシナリオ | `demos/showcase/scenarios/` |
| 7. ドキュメント | 文法、手引き、parity 表の行を英語と日本語で | `docs/`、`docs/ja/` |

単位 1 と 2 を先に進めます。単位 3 と 5 は単位 2 に依存し、互いには依存しません。単位 4 は、単位 3 が作る `am start` の出力の検査を使うので、
単位 3 に依存します。単位 3 は、起動時 deeplink と同じ URL をエミュレータ上で `openUrl` で開き直し、その URL が
示す画面に移るシナリオが通った時点で完了とします。単位 6 には単位 3 と 5 が必要です。
単位 5 の Swift のコードは `xcodebuild` の E2E ジョブでしかコンパイルされないので、そのジョブで単位 5 を確かめます。

## 検討した代替案

| 代替案 | 採らなかった理由 |
|---|---|
| `navigate` をモバイルのバックエンドに広げる | `navigate` は `baseUrl` を基準に解決し、`http` と `https` だけを受け付け、ページの `load` イベントを待ちます。モバイルの deeplink はどれにも当てはまらないので、1つの名前が2つの意味を持つことになります。 |
| `simctl openurl` と `adb` を使う `DeviceControl` のメソッドにする | `DeviceControl` は `app:` ブロックが操作しているアプリを知りません。`simctl openurl` は実機にも、指定したアプリにも届きません。 |
| 宛先を絞らず、OS に URL を解決させる | 同じ URL を扱うアプリが2つあると、Android は選択ダイアログを出します。結果が、端末に入っているアプリ次第で変わります。 |
| `openUrl` に `app` フィールドを持たせる | 受け取るアプリの指定方法が2通りになり、そのアプリを操作するにはやはり `app:` ブロックが要ります。 |
| deeplink を付けた `relaunch` にする | 再起動でアプリが終了し、シナリオがサインインしたセッションのようなメモリ上の状態が失われます。 |
| Simulator では `simctl openurl` を使い、実機は未対応のままにする | iOS に挙動の異なる2つの経路ができ、実機ではシナリオがまったく流せません。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] シナリオのモデル
- [ ] ドライバーの受け口と preflight
- [ ] Android の経路
- [ ] 起動時 deeplink の修正
- [ ] iOS の経路
- [ ] showcase のシナリオ
- [ ] ドキュメント

## 参考

- [BE-0464](../BE-0464-web-navigate-step/BE-0464-web-navigate-step-ja.md)：web の `navigate` ステップです。
  モバイルの deeplink は後の項目に回しました。
- [BE-0447](../BE-0447-install-app-step/BE-0447-install-app-step-ja.md)：`foreground` ステップと、`app:` ブロックが
  頼るアプリのスタックです。
- [`docs/ja/platform-parity.md`](../../docs/ja/platform-parity.md)：この項目が行を加える parity 表です。
- [`XCUIApplication.open(_:)`](https://developer.apple.com/documentation/xctest/xcuiapplication/open(_:))：
  ランナーのルートが使う XCTest の呼び出しです。
- [`am` コマンドのリファレンス](https://developer.android.com/tools/adb#am)：`am start` と `-W` オプションです。
