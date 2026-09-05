[English](BE-0406-system-alert-declared-prompts.md) · **日本語**

# BE-0406 — システムアラートの宣言をプロンプト名だけにし、handleSystemAlert の待機中にも答える

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0406](BE-0406-system-alert-declared-prompts-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0406") |
| 実装 PR | [#1871](https://github.com/bajutsu-e2e/bajutsu/pull/1871)（単位 1）、[#1894](https://github.com/bajutsu-e2e/bajutsu/pull/1894)（単位 2a、3）、[#1903](https://github.com/bajutsu-e2e/bajutsu/pull/1903)（単位 2b、単位 5）、[#1908](https://github.com/bajutsu-e2e/bajutsu/pull/1908)（単位 4） |
| トピック | Platform support |
| 関連 | [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md), [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md), [BE-0369](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice-ja.md), [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md), [BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy-ja.md), [BE-0401](../BE-0401-system-alert-handling-dsl-consolidation/BE-0401-system-alert-handling-dsl-consolidation-ja.md), [BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Bajutsu は、アプリ自身のアクセシビリティツリーから見えない OS のプロンプトに、2 つの経路で答えています。
1 つは `handleSystemAlert` ステップです。[シナリオ](../../docs/ja/glossary.md#シナリオのオーサリング)が
プロンプトを予期する 1 箇所に置かれ、名指ししたボタンをタップします。もう 1 つは `systemAlertHandling`
が設定する反応的なガードです。実行中のどこであれプロンプトが割り込んだら、シナリオの方針が名指しした
ボタンをタップします。

本提案は、2 つの変更を加えます。1 つめは、`handleSystemAlert` ステップにガードを与えることです。
ステップの待機を XCUITest ドライバから orchestrator へ移します。宣言済みのアラートがステップへ割り込んだ
とき、ステップが失敗したあとではなく、まだ待っているうちに答えられます。2 つめは、シナリオがボタンを
名指しするのをやめ、プロンプトを名指しするようにすることです。`systemAlertHandling` は候補ボタンラベルの
順序付きリストである `labels` を落とし、それに対して照合していた経路をすべて、シナリオが書く `rules` へ
付け替えます。ツリー内消去もその 1 つです。アプリ自身のプロセスに出るアラートを片付けられる経路は、
これしかありません。動機とした事例を宣言可能にするには、iOS の「パスワードを保存」のアラートを
プロンプト表に加える必要があります。この表で SpringBoard が所有しないプロンプトは、これが最初になります。

1 つの面は、同じやり方で付け替えられません。理由を先に述べておきます。XCUITest は、自分の操作に
割り込んだアラートを、その操作を合成する前に解決し、その解決には常に何かが答えます。辞退は、どのルールも
同定しないアラートに対する唯一の安全な答えです。割り込みを主張しながら片付けずにいると、次のインタラク
ションのたびに監視が呼び直され、BE-0399 はこれが runner を丸ごと死なせるまでループすることを実測して
います。辞退した監視は、アラートを XCUITest 自身のデフォルトハンドラへ渡します。そのハンドラは、
アラートの**デフォルト**ボタンをタップして片付けます。これは、その監視が存在する前に起きていたことと
同じです。本提案は、このタップが起きること自体は止められません。変えるのは、それが黙って起きるかどうか
です。割り込み監視は、辞退したアラートのボタンを今後は報告します。それを見た step または `expect` は、
シナリオに代わって誰かが答えたかのようにランを続けるのではなく、そのボタンを名指しして失敗します。

後方互換は意図的に保ちません。`labels` を書いたままのシナリオは読み込みに失敗し、代わりに `rules` を
使うよう名指しするエラーになります。これは
[BE-0401](../BE-0401-system-alert-handling-dsl-consolidation/BE-0401-system-alert-handling-dsl-consolidation-ja.md)
が削除したキーに与えたのと同じ扱いです。

## 動機

`handleSystemAlert` ステップは、待機中に iOS の「パスワードを保存」のアラートが画面に出ているとタイム
アウトします。サインインしたあとに通知の認可を要求するアプリで実測しました。ステップは目的のプロンプトを
一度も見ないまま `timeout` を使い切り、`no system alert appeared within <timeout>s` で失敗します。実際に
出ていたアラートは実行結果のどこにも現れません。そのためこの失敗は、割り込みではなく、許可プロンプトが
現れなかった事象として読めてしまいます。

原因は、ステップの待機と、割り込みを片付けられるガードが、呼び出しの反対側にいることです。
`XcuitestDriver.handle_system_alert` は、`springboard.alerts` を自分の期限まで poll します。実装は
[`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) にあります。一方の反応的なガードは
[`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) の `_AlertGuardGate` です。これは
`wait` ステップが 1 poll ずつ駆動する orchestrator 側のオブジェクトです。ガードを背後で走らせるスレッドは
ありません。`_run_step_body` が `alert_guard` を渡すのは、`kind == "wait"` のときだけです。他のステップ
種別は無視すると、その docstring 自身が記録しています。実装は
[`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py) にあります。つまりドライバのループが
回っているあいだ、ガードは割り込めません。ステップ終端のガードはどのステップ種別でも発火し、
`handleSystemAlert` も例外ではありません。ただしそれは、ステップが `timeout` を使い切って失敗したあとです。

「パスワードを保存」のアラートは、そもそもステップが poll している問い合わせから見えません。
[BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy-ja.md)
が、どのプロンプトがどこに出るかを実測しています。通知の認可要求は、別プロセスの SpringBoard のアラート
です。これに対し「パスワードを保存」のアラートは、iOS が**アプリ自身のプロセス**に出します。届く先は
アプリ自身のツリーで、識別子のないラベル付きボタンとして現れます。Web フォームに対しては `app.alerts`、
アプリ自身の入力欄に対しては `app.sheets` のシートです。runner の `querySystemAlertButtons` が読むのは
`springboard.alerts.buttons` だけです。そのため「パスワードを保存」のアラートは、アラートが存在しないのと
同じに見えます。ステップの poll は、そのアラートが画面を占めているあいだ空のボタン一覧を受け取り続けます。
しかもアラートはアプリに対してモーダルなので、ステップが待っている許可要求そのものが発生しません。

シナリオの作者は今日、`wait` ステップを置いてこの穴を迂回しています。ガードが配線されている唯一のステップ
種別だからです。[`save_password_browser.yaml`](../../demos/showcase/scenarios/save_password_browser.yaml)
は、ほかの目的を持たない `wait: { until: { gone: { label: "Not Now" … } } }` を置いています。OS の
タイミングがどちらに転んでも成り立つ待機の形について、コメントが説明しています。この迂回策は、iOS が
いつ割り込むかを作者が予測できることを要求します。割り込みのタイミングが本当に不明なときには、そもそも
書けません。

問題のもう半分は、シナリオが何を宣言できるかにあります。`systemAlertHandling` は 2 つのキーを取ります。
プロンプトと選択を名指しする `rules` と、どのルールも同定しなかったものに対して参照される候補ボタン
ラベルの順序付きリスト `labels` です。ラベルが名指しするのはボタンであって、プロンプトではありません。
そのため `labels` は、シナリオが一度も説明していないアラートのボタンを、ガードがタップすることを許します。
BE-0399 はこの危険を直接記録しています。「Cancel」と「Close」は、実在の画面が正当に表示しうる普通の
アプリの語彙です。そこで BE-0399 は、シナリオ自身が `labels` を宣言していることをツリー内消去の条件にし、
組み込みのデフォルト値からはその消去を外しました。この条件は、シナリオの `labels` を作者の意図の代理と
して扱っています。しかし代理でしかありません。宣言が述べているのは、受け入れるボタンの文字列であって、
どのアラートを予期しているかではないからです。

プロンプトを名指しする形へ移すのは、整理のためだけではありません。宣言がもっとも働く場所は、ツリー内
消去です。ここを担うのは `AlertGuardConfig.dismiss_from_tree_once` と `_AlertGuardGate._dismiss_from_tree` の
2 つです。どちらも poll 自身のツリーにある識別子のないラベル付きボタンを見ます。照合に使うのは
`pick_alert_label(self.labels, …)` です。この 2 つは、`springboard.alerts` から見えないアラートを片付けられる、このコードベースで唯一の
機構です。これを `rules` へ付け替えることが、シナリオに「どのアラートをガードに片付けてほしいのか」を
言わせます。どの語ならタップされてよいか、ではありません。

答えの経路はもう 1 つあり、そちらは未対応のアラートをそのまま放っておけません。今日はそれを
失敗にもしません。許可してしまいます。XCUITest は、割り込んだ相手のインタラクションを合成する前にそのアラートを解決し、
runner の割り込み監視は、その解決に一方かもう一方で答えます。ルールに一致してタップするか、辞退して
XCUITest 自身のデフォルトハンドラへ渡し、そのハンドラがそのアラートのデフォルトボタンをタップするかです。
後者は、本書の冒頭で述べたのと同じサイレントな許可を、`rules` が名指ししないあらゆるアラートについて
起こします。`bajutsu/orchestrator/types.py` の `DEFAULT_DISMISSIVE_LABELS` は、今日 push している
フォールバック候補です。その許可へアラートが届く頻度を狭めます。組み込みの語のどれかを提供するアラートに、
控えめに答えるからです。しかし穴は閉じません。その語のどれも提供しないアラートは
今日と同じように辞退されます。その辞退がなぜ避けられず、かつ BE-0399 以来はっきり安全とされているのかは、
単位 2 でたどります。

本提案が届いたかどうかは、あとから読む人にも判別できます。「パスワードを保存」のアラートが画面を占めて
いるところへ、通知プロンプトのための `handleSystemAlert` ステップを置く showcase のシナリオを走らせて
ください。そのシナリオは今日、上に引いたタイムアウトで失敗します。変更後は、割り込みを覆う `wait`
ステップを 1 つも置かずに通ります。移行した 2 つのデモが、2 つめの観測可能な差です。どちらも保存のプロンプトに、
`labels: ["Not Now"]` ではなく `savePassword` のルールで答えます。`save_password_browser.yaml` は、
迂回策として持っている `gone` の待機も落とします。3 つめの差は、どの `rules` エントリも名指ししない
アラートが割り込んだところで、ふつうの `tap` を走らせるシナリオです。今日はその step が通るか、アラート
自身の答えがたまたま引き起こした無関係な理由で失敗します。本提案のあとは、アラートのボタンを実行結果に
残したうえで、名指しで失敗します。

## 詳細設計

### 単位 1 — ステップの待機を orchestrator へ移す

`Driver.handle_system_alert` は待つのをやめます。シグネチャはそのままです。呼び出し側は timeout に 0 を
渡し、すでに出ているとわかっているアラートへの問い合わせとタップとして使います。これは
`AlertGuardConfig.probe_native` が `_NATIVE_TAP_TIMEOUT` を通じて今も行っていることです。XCUITest の実装は
`bajutsu/drivers/xcuitest.py:1105-1114` のポーリングループを落とします。そして `/systemAlert/query` の
1 回の読み取りに対してセレクタを解決します。Swift 側の変更は不要です。他のバックエンドにも影響しません。
`HANDLE_SYSTEM_ALERT` を広告しないバックエンドでは `capability_preflight` がこのステップをすでに拒否
します。ドライバは実行中の最後の砦として `UnsupportedAction` を送出し続けます。

待機そのものは、ステップのハンドラへ移ります。
[`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py)
の `_do_handle_system_alert` が、ガードを引数として受け取ります。ハンドラは `_AlertGuardGate` を
ステップにつき 1 つ作り、ステップ自身の期限まで poll します。この形は `_wait` がすでに使っているものと
同じです。`_wait` もゲートを 1 度だけ作り、各 poll で `gate.observe(elements)` を呼びます。各 poll は、
次の順序で 2 つのことを行います。

1. `driver.system_alert_labels()` を読む。ステップ自身のセレクタがそのラベル群に対して解決したら、
   `driver.handle_system_alert(sel, 0)` でタップして復帰する。
2. 解決しないなら、その poll のツリーを `gate.observe` へ渡し、poll を続ける。

1 番目のタップは、`probe_native` がすでに扱っている時間差の競走を同じように抱えます。扱い方も同じに
します。`ElementNotFound` は、問い合わせとタップのあいだにアラートが自分で閉じたことを意味します。
`AmbiguousSelector` は、アラートがまだ出ていて、セレクタのラベルを 2 つ提示していることを意味します。
どちらもステップの評決ではありません。ハンドラは期限まで poll を続けるので、無害な競走が費やすのは
ステップではなく 1 回の poll です。

外側のループは `_POLL`（0.05 秒）で poll します。`_wait` 自身の間隔と同じです。ただしこの 1 tick は
安いタイムスタンプの比較であり、毎回クロスプロセスの読み取りを行うわけではありません。tick が引き起こし
うる 2 つの読み取りは、それぞれ独立した頻度を保ちます。今日すでに払っているコストから変わりません。
ステップ自身の `system_alert_labels()` の読み取りは、内部で `_SYSTEM_ALERT_POLL_SECONDS`（0.2 秒）に
絞られます。この単位が移す前に、ドライバ自身の poll ループがすでに払っていたのと同じ間隔であり、ステップが
自分の目的のプロンプトに答える速さは、今日より遅くなりません。`gate.observe()` のクロスプロセスな
SpringBoard の probe は、既存の別の絞り込みを保ちます。`_observe_native` はすでに自分自身を絞っています。`observe()` がどれだけ頻繁に呼ばれるかとは無関係に、
`guard.poll_interval` へです（`_last_native`。本提案では変えません）。これは、ガードが覆うすべての
`wait` ステップに対して BE-0315 がすでに受け入れて
いる負荷と同じです。この 2 つを 1 つの共有した間隔に結びつけること、本単位の初期の草稿がまさにそうして
いましたが、それが誤りでした。`save_password_browser.yaml` は `pollInterval: 5` を意図的に広く取っています。2 つの重なった
プロンプトを probe をまたいで出したままにするための設定です。これをステップ自身の応答性にまで
押しつけることになります。単位 4 の回帰用シナリオの `handleSystemAlert` ステップに残るのは、自分の
目的を見つけるための、5 秒の隙間の中の `tap` サイズの窓だけです。2 つの頻度を切り離すことが、その緊張を取り除き
ます。ガードの SpringBoard の probe のために広い `pollInterval` を必要とするシナリオも、ステップが自分の
プロンプトに気づく速さでは、何も払わずに済みます。

`_dismiss_from_tree` をゲートから切り出さずに、ゲートごと再利用します。poll をまたぐ状態が成り立つのは、
そのためです。再タップの間隔、拒否されたときの断念、ラベルごとのタップ上限は、いずれもゲートの
フィールドとして poll のあいだ持ち越されます。切り出した関数には、それを置く場所がありません。poll ごとに
ゲートを作り直せば、毎回すべてが初期化されてしまいます。ゲートの再利用は、BE-0399 が確立した順序も、
書き直すことなくそのまま与えます。`_observe_native` は SpringBoard を先に probe し、ルールが同定した
アラートにそこで答えます。ツリー内タップを発行するのは、自分自身の probe が SpringBoard のアラートなしを
報告した poll だけです。

ステップ自身のセレクタをゲートより先に照合するのは、両者が同じプロンプトを取り合わないようにするため
です。シナリオは、ステップが答えるために置かれたプロンプトに対する反応的なルールを、しかも逆の選択で
持ち得ます。`rules: [{ prompt: notifications, choice: deny }]` と、`choice: grant` を名指しするステップが
同居する場合です。先にアラートを読んだ側が、そのまま決めてしまいます。上の 1 番目は、各 poll の最初の
読み取りをステップに与えることで、通常の場合を解決します。残るのは、1 つの poll のなかで、ステップの
読み取りとゲート自身の probe のあいだにプロンプトが現れる場合です。これはゲート側で閉じます。ハンドラは
ステップのセレクタをゲートへ渡し、`probe_native` は、そのセレクタが解決するアラートを辞退して、ステップの
次の poll に残します。ゲートが答えるのは、ステップが待っていないものだけになります。

`_run_step_body` は、`kind == "wait"` と並べて `kind == "handle_system_alert"` でも `alert_guard` を渡し
ます。docstring は両方を名指しするよう更新します。ステップ終端のガードは変えません。待機中に割り込みへ
答えるようになったステップは、単にそこへ到達する頻度が下がります。

期限を過ぎたときの失敗は、ステップが何を見たかを名指しします。メッセージは今日の `no system alert appeared
within <timeout>s` を置き換え、3 つの場合を区別する理由になります。アラートが 1 つも現れなかった場合、
アラートは現れたがセレクタがそのボタンに一致しなかった場合、そして宣言されていないアラートが待機のあいだ
画面を占めていた場合です。3 つめは画面に出ていたボタンを添えます。実行結果を読む人は、シナリオに欠けて
いたルールを補えます。

### 単位 2a — アラートをプロンプトで宣言し、ラベルを照合していた経路を付け替える

`labels` を [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py) の
`SystemAlertHandling` から削除します。`run` からは `--alert-labels` オプションを削除します。
`_flag_alert_policy` に残すのは `--alert-poll-interval` だけです。
`--alert-vision-instruction` は、BE-0402 がそのフラグの誘導していたフォールバックごと、すでに
`run` から退けています。このオプションは
[`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) にあります。`rules` の代わりの
フラグは設けません。ルールはプロンプトと選択の組であり、それを読みやすく運べるフラグはないと
BE-0401 がすでに記録しています。プロンプトごとの宣言は、シナリオファイルとターゲット設定の宣言の
ままにします。`AlertGuardConfig` は `labels` フィールドを失います。BE-0401 が確立した層合成の表は、
行を 1 つ失います。`rules` は内側の層から順に連結されるリストのキーのままです。`pollInterval` は、
値を与えるもっとも内側の層が勝つ唯一のスカラーになります。有効と無効の真偽値も変わりません。

`labels` に対して照合していた呼び出し元は 3 つあり、いずれも削除ではなく付け替えます。

| 呼び出し元 | 今日 | 変更後 |
|---|---|---|
| `AlertGuardConfig.probe_native` | `match_alert_rule(self.rules, …)` のあと `pick_alert_label(self.labels or DEFAULT_DISMISSIVE_LABELS, …)` | `match_alert_rule(self.rules, …)` だけ。どのルールも同定しなければ、今日と同じく `"unhandled"` |
| `AlertGuardConfig.dismiss_from_tree_once` | `self.labels` が空でないことで武装（`if not self.labels: return None`）し、`pick_alert_label(self.labels, …)` で照合 | ツリー内で働けるルールを 1 つ以上持つことで武装し、そのルールに対して `match_alert_rule(…)` で照合 |
| `_AlertGuardGate._dismiss_from_tree` | `if self.guard.labels and probed_absent` で武装 | ツリー内で働けるルールを 1 つ以上持つことと `probed_absent` で武装し、そのルールに対して `match_alert_rule(…)` で照合 |

荷重を担うのは、ツリー内の 2 行です。この 2 つは poll 自身のツリーにある識別子のないラベル付きボタンに
対して照合し、`springboard.alerts` から見えないアラートを片付けられる唯一の機構です。「パスワードを
保存」のアラートも、そこに含まれます。付け替えずに照合ごと削除すると、
`rules: [{ prompt: savePassword, choice: deny }]` に対して働ける経路が 1 つも残りません。この 2 つの
経路のほかの部分は、いずれも変えません。タップ前の一意性の事前確認、再タップの間隔、拒否されたときの
断念、ラベルごとのタップ上限は、そのままです。ツリー内タップは自分自身の probe が SpringBoard のアラート
なしを報告した poll でしか発行しないという BE-0399 の規則も、そのままです。変わるのは、ツリーへ問う内容だけです。どの
宣言済みのアラートが出ているか、であって、どの許容語がボタンに載っているか、ではありません。
この 3 つが `pick_alert_label` の唯一の Python 側の呼び出し元なので、この関数は `labels` とともに
削除します。

上の表の「ツリー内で働ける」は新しい概念ではなく、単位 3 が導入するプロンプトごとの面の記録を、反対側
から読んだものです。どのルールでもツリー内の経路を武装させると、作者が求めた範囲を越えます。
`notifications` だけを宣言したシナリオでも、SpringBoard にしか現れないプロンプトのためにツリー内の照合が
武装します。そして、識別子のない「Allow」と「Don't Allow」をたまたま表示しているアプリの画面が
タップされます。武装させるのは、プロンプトがツリー内で働けると記録されたルールだけです。これが、範囲の拡大を
防ぎます。

同じ記録が、`push_interruption_policy` の送る内容も決めます。ツリー内でしか出ないプロンプトのルールは、
push するルールの一覧から落とします。割り込み監視は、XCUITest の操作に割り込んでくる別プロセスの
アラートのためにあり、アプリ自身のプロセスに出るアラートはそこへ届かないからです。落とすのは整理のため
だけではありません。`InterruptionPolicy.label(for:)` はルールを部分集合で照合します。`savePassword` のアプリ内の形を
push すると、単位 3 が Python 側で閉じた衝突を、Swift 側で開き直すことになります。
これらのルールを push しなければ、監視が見るルールに除外の集合は 1 つも載らず、後 2 段落の変更はどちらも
それを勘定に入れる必要がありません。これは偶然ではなく、構成として成り立たせます。除外の集合を持ち、
なおかつツリー内だけのものではないルールを、push は拒否します。将来 SpringBoard に届く形が除外を必要と
したとき、除外を黙って落としたまま監視へ届くのではなく、はっきり失敗します。落ちた先にあるのは、1 つ表を
足したあとの部分集合の衝突だからです。

`labels` を読む場所は、いま削除した `push_interruption_policy` の候補のフォールバックを除くと、
あと 1 つです。ここも照合の場所ではありません。
[`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) の `_warn_target_rules_reach` です。
`any(layer.labels for layer in inner_layers)` を読み、シナリオが自分で答えているかどうかを判定します。
判定は `any(layer.rules for layer in inner_layers)` へ付け替えます。これと並べて付け替えるビジョンの
フォールバック向けの指示は、もうありません。`run` がそのフォールバックごと `_vision_instruction` を削除したのが BE-0402 です。
シナリオの `labels` がそこを誘導していたのは、その変更が着地するまでのことでした。

`_resolve_rules` は、覆っていない言語に対して `UncoveredSystemAlertLocale` を送出し、その救済として
`labels` を案内しています。このメッセージを、残る 2 つの救済を名指しする形に書き直します。言語を
[`bajutsu/scenario/system_alerts.py`](../../bajutsu/scenario/system_alerts.py) に加えるか、表が覆う
ロケールを固定するかです。

`labels` を書いたままのシナリオは読み込みに失敗し、`rules` を名指しするエラーになります。BE-0401 は
`enabled` と 2 つの非推奨の綴りを同じやり方で削除し、後方互換を保たないことを明言しました。本提案は
別名を持ち回るのではなく、その先例に従います。

### 単位 2b — 割り込み監視が辞退したアラートを報告する

`DEFAULT_DISMISSIVE_LABELS` は残さず削除します。それが監視へ渡していた Swift 側の照合も一緒に落とします。
[`BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift`](../../BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift)
の `InterruptionPolicy.candidates` を削除します。`label(for:)` は `rules` だけで照合します。
`openapi.yaml` の `InterruptionPolicyRequest` も、対応するフィールドを落とします。この面での辞退は、
「まだ安全とは言えない」ではなく「安全」です。すでに文書化されています。`docs/architecture.md:708-709`
は、記録しています。方針がボタンを名指ししないプロンプトは、「XCUITest 自身のデフォルトハンドラへ委ねる、
変わらず（BE-0399）」です。`docs/scenarios.md:168-169` も、「この監視が存在する前と同じく XCUITest へ
委ねる」と記録しています。`RunnerUITest.swift` 自身のコメントも、辞退を「唯一の安全なフォールバック…
この監視が存在する前に起きていたことであり、実際にそれを片付ける」と呼びます。常駐 runner を丸ごと
落としうるループが起きるのは、割り込みを**主張**しながら片付けない監視からです。アラートを引き渡す
監視からは起きません。`rules` が名指ししないアラートに対して、監視が答えを推測する必要はどこにも
ありません。組み込みの候補が果たしていたのは、その記録されない許可へアラートが届く頻度を狭めることだけ
でした。閉じることではありませんでした。

推測に代わるのは報告です。ただし「ガードが実際に方針を持つ」ことと「push するルール一覧が空でない」
ことは同じ事実ではなく、今日の `isEmpty` の確認は両者を混同しています。`InterruptionPolicy` は
`rules` と並んで `governs: Bool` フィールドを得ます（`candidates` の代わりです）。
`Driver.set_interruption_policy` と `InterruptionPolicyRequest` も、`candidates` の引数・フィールドを
`governs` に置き換えます。`push_interruption_policy` はこれを `guard is not None` に設定します。
`systemAlertHandling` が有効なシナリオならどれも真であり、そのシナリオのルールがツリー内専用のフィルタを
くぐり抜けたかどうかとは無関係です。これがないと、唯一のルールが `savePassword` であるシナリオは困ります。単位 4 が
`save_password_native.yaml` にするのが、まさにこれです。そのシナリオは、ツリー内専用フィルタが
落としたあと、空のルール一覧を push します。`isEmpty` の確認が「このシナリオは何も宣言していない」と
読めば、どうなるでしょうか。そのシナリオがほかに一切ルールを持たない、ほかのすべてのアラートについて、
単位 2b が閉じるはずの許可をサイレントに再び開いてしまいます。実際に宣言はあったのに、この面が働ける
形には絞り込まれなかった。それは、宣言が何もないのとは違います。`governs` が真になるのはちょうど前者の場合だけで、
偽になるのは `guard is None`（ガードが存在しない、または `systemAlertHandling: false`）のときだけです。
これは今日のサイレントで記録されない許可をそのまま残すべき、唯一の場合です。何も宣言しないことは、
その作者自身の選択だからです。

割り込み監視は、辞退する前に、答えられなかったものを記録するようになります。`InterruptionPolicyStore`
は drain 対象を 2 つ持ちます。1 つはタップ済みのラベルのため、今もあるものです。もう 1 つは、方針が
`governs` である poll で `label(for:)` が `nil` を返した、各アラートのボタンラベルを持つ、新しいもの
です。監視はそこへ追記してから、今までどおり `false` を返します。そのあとに続くタップは変わりません。
XCUITest 自身のタップで、そのアラートがデフォルトと呼ぶボタンです。その時点で変えられるものが何もない
からです。近い辞退が 2 つあり、どちらも記録しません。意図的にそうします。`RunnerUITest.swift` の
`guard policy.governs else { return false }` は `label(for:)` を呼ぶより先に走り、そこで打ち切ります。
ガードが存在しない場合の辞退は、今日と同じサイレントな許可のままです。何も宣言しないのはそのシナリオの
作者自身の選択であり、本提案が拾おうとしている見落としではないからです。もう 1 つは、本提案のほかの
場所で `probe_native` がすでに無害として扱っている競走の再来です。ラベル自体は一致したのに、タップの
前にそのボタンが消えた場合（`guard button.exists else { return false }`）です。これも記録せずに辞退
します。そのアラート、未宣言だったのではなく、ボタンが自分自身の消去と競走に負けただけです。
`POST /interruptionPolicy/drain` の応答は、既存の `labels` と並んで `unmatched` フィールドを得ます。
辞退して記録された各アラートにつき 1 つのボタン一覧で、古い順です。
[`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) の `Driver.drain_interruptions` は、戻り値の
型を `list[str]` から小さな組に変えます。組の中身は 2 つです。タップ済みのラベル（変わらず）。そして、
辞退したものが持っていたボタン一覧です。`bajutsu/orchestrator/types.py` の orchestrator 側
`drain_interruptions` も、後半の扱いを変えます。`AlertEvent` ではなく `UndeclaredInterruption` の記録
にします。シナリオに代わって何も答えていないからです。片付けとして報告するものがありません。

[`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py) の `run_scenario` は、この
2 つめの一覧を、すでに 1 つめを drain している場所すべてで読みます。retry を数えれば 2 箇所ではなく
3 箇所です。1 つは step の終端です（`outcome.alerts.extend(drain_interruptions(active_driver))`）。
ここは、step 自身の本体から `outcome.ok` / `outcome.reason` が決まったあとに実行されます。未対応の
割り込みは、それらを上書きします。step は無条件で失敗し、そのアラートが提示したボタンを名指しします。
それまで通っていた step でも同じです。名指しされていないプロンプトが黙って解決されているあいだ通って
いた step は、まさに本提案が信頼をやめさせようとしている「成功したように見える」状態そのものです。
それはほかの 2 面と同じく、この面にも当てはまります。`expect` のあいだも同じ上書きが働きます
（`expect_alerts.extend(drain_interruptions(driver))`）。アサーションの結果を参照する前に `failure`
をセットするのも、理由も同じです。この drain は、この段階自身の alert-guard retry より先に 1 度だけ
走ります。今日は、その retry のあとを何も drain しません。retry のコメント自身が「このフェーズをほかに
drain するものはない」とすでに記録しているとおりです。これまではその代償が `AlertEvent` を 1 つ
逃すだけでしたが、今後は失敗を丸ごと見落とす代償になります。そこで retry には、そのコメントが「ない」と
書いていた 2 度目の drain を加えます。ここで得た `UndeclaredInterruption` も、`failure` を決める前の
同じ判定へ合流させます。一致した割り込みの扱いは、どちらの呼び出しでも変わりません。それらはこれまで
どおり `AlertEvent` になり、それ自体では何も失敗させません。

### 単位 3 — 「パスワードを保存」をラベル表に加える

[`bajutsu/scenario/system_alerts.py`](../../bajutsu/scenario/system_alerts.py) の `SystemAlertPrompt` と
`_LABELS` に `savePassword` を加えます。このプロンプトは、表がいま置いている前提を 3 つ壊します。
どれを閉じるかも、この単位の一部です。

**SpringBoard の所有ではありません。** 表はボタンの**ラベル**の出所であり、そのラベルをタップする経路は、
SpringBoard への問い合わせがアラートを見られるかどうかで別に選ばれます。したがって参照の側は、どの
プロセスがアラートを所有するかに依存しません。表に載っていることから SpringBoard のアラートだと推論
されないよう、docstring にこの区別を記録します。

**`handleSystemAlert` ステップでは名指しできません。** ステップは `driver.system_alert_labels()` に対して
セレクタを解決します。この問い合わせが読むのは `springboard.alerts` だけです。`prompt: savePassword` を
名指しするステップは、期限まで空のボタン一覧を poll することになります。本提案が取り除こうとしている失敗
そのものです。そのため `savePassword` は `systemAlertHandling` のルールとしてのみ宣言できます。
`HandleSystemAlert` は、解決しようのないステップを受け入れるのではなく、ルールの形を名指しする
メッセージとともに、これをパース時に拒否します。宣言の面が分かれるのはこのプロンプトが最初です。そこで
表は、プロンプトがどの面に届くかの記録をプロンプトごとに持ちます。`handleSystemAlert` ステップ、ガードの
ネイティブな SpringBoard の probe、ガードのツリー内消去の 3 つです。この記録は 3 つの仕事をします。
ステップでの `savePassword` を拒否すること、どのルールがツリー内の経路を武装させるかを決めること、
そしてツリー内でしか出ないルールを割り込み監視へ push する方針から外すことです。あとの 2 つは単位 2 が
述べたとおりです。

**ボタンの組を 3 通り描画します。** 以下のラベルは、`WebUI.framework/<lang>.lproj/Localizable.strings`
から転記しました。このファイルは Simulator ランタイムの cryptex、
`System/Cryptexes/OS/System/Library/PrivateFrameworks/` の下にあります。BE-0320 が求めるとおり新しい
ランタイムで表を検査し直す人にとって、この置き場所には 2 つ意味があります。1 つは、表にすでにある 3 つのプロンプトが、ランタイムルートの
`System/Library/PrivateFrameworks` にある、cryptex の外のフレームワークに由来することです。もう 1 つは、`.strings` が
いずれも Apple binary property list であり、中身が平文検索ではなく `plutil` に対して開くことです。

| アラート | iOS | 英語 | 日本語 |
|---|---|---|---|
| Web フォームに対して出るもの | 18.6 と 26.5 | Save Password / Never for This Website / Not Now | パスワードを保存 / このWebサイトでは保存しない / 今はしない |
| アプリ自身の入力欄に対して出るもの | 18.6 | Save Password / Not Now | パスワードを保存 / 今はしない |
| アプリ自身の入力欄に対して出るもの | 26.5 | Save / Not Now | 保存 / 今はしない |

動くのは、受け入れる側のボタンです。iOS 26.5 には 18.6 にないキーがあります。
`"Save Password (save login information sheet in app)"` で、値は「Save」です。そのため同じ受け入れの
意図が、Web フォームでは「Save Password」、アプリ自身の入力欄では「Save」と表示されます。拒否する側の
ボタンは 3 通りとも「Not Now」で、日本語はいずれも「今はしない」です。

そこで `_LABELS` は、プロンプトの言語エントリを 1 組の `{grant, deny}` ではなく、形の**リスト**に
対応づけます。1 つの形が持つのは 3 つです。アラートを同定するラベル。各選択がタップするラベル。そして、存在すれば
その形を除外するラベルの集合です。最後の 1 つは省略できます。`notifications`、`tracking`、`paste` は、
除外を持たない形を 1 つだけ宣言します。解決する値に変化はありません。`savePassword` は、上の表の順に
3 つを宣言します。

`_resolve_rules` は形ごとに 1 つの `ResolvedAlertRule` を出し、`match_alert_rule` は除外の確認を持ち
ます。ルールが一致するのは、同定用のラベルがすべてちょうど 1 回ずつ存在し、なおかつ除外ラベルが 1 つも
存在しないときです。前半は今日と同じです。Web フォームの形に除外は要りません。3 つのボタンを同時に出す
アラートは、ほかにないからです。18.6 のアプリ内の形は「Save Password」と「Not Now」で同定し、この組は
Web フォームのアラートも満たします。ただし無害です。どちらの形も、同じ選択に対して同じラベルをタップ
するからです。

除外が要るのは、26.5 のアプリ内の形です。ラベルは「Save」と「Not Now」です。同じ `WebUI.framework` が、クレジットカードのアップデートのシートに
「Save」「Never for This Card」「Not Now」を与えています。組だけ
では、そのシートにも答えてしまいます。そこでこの形は「Never for This Card」を除外します。2 つを見分ける
唯一のラベルです。

形のラベルをアラートのボタン集合そのものと一致させるのではなく除外の集合を使うのは、照合の現場が実際に
計算できるのがそれだからです。`savePassword` のルールが届く先はツリー内の経路だけであり、この経路が見る
のは 1 つのアラートのボタンではなく、poll のツリー全体にある識別子のないラベル付きボタンです。
`_dismiss_from_tree` は候補一覧をそのように組み立てますし、`shows_app_ui` の docstring は、アプリ全体が
識別子をまったく持たないこともありうると記録しています。この経路に「アラートのボタン集合」という比較対象
は存在しません。除外は、同定用のラベルがすでに問われているのと同じ平坦な一覧に対する問いなので、同定用の
ラベルが成り立つところなら成り立ちます。Web フォームの形と 18.6 のアプリ内の形については、そうです。
同定用の組（「Save Password」「Never for This Website」）が具体的で、画面のどこかで偶然一致することは
考えにくいからです。

26.5 の組は、それより弱いケースです。あとから読む人まかせにせず、はっきり述べておきます。「Save」と
「Not Now」は、BE-0399 が「Cancel」や「Close」と同じ範疇に挙げた、普通のアプリの語彙です。ほかのすべての
ツリー内ルールと共有するこの平坦なツリーでの照合は、除外が閉じる一方向だけでなく、両方向の危険を抱えて
います。識別子のない「Save」と「Not Now」をたまたま表示していて、保存シートは出ていないアプリの画面は、
この組を満たしてタップされます。除外の集合はここでは何の役にも立ちません。クレジットカードのシートを
除外するだけだからです。`match_alert_rule` は、同定用のラベルがそれぞれちょうど 1 回ずつ存在することを要求します
（`bajutsu/orchestrator/types.py:342`）。同じツリーのどこかに識別子のない「Save」がもう 1 つあれば、
シートの背後であれ、シートが覆う画面であれ、数は 2 になります。この形は一致しなくなります。
これは不一致です。まさに本提案が直そうとしているタイムアウトを、その iOS バージョンで再び持ち込みます。
どちらの危険も本提案が新しく持ち込んだものではありません。単一ラベルでの照合は、画面が繰り返しうる
どんな語についても、すでに同じ両方向の危険を抱えています（本提案より前から本番で使われている
`labels: ["Not Now"]`）。2 つのラベルの組は、それを広げるのではなく狭めます。新しいのは、これほど
普通の語の組を、保存するか拒否するかという、作者には見通せない決定のために頼ることです。本提案は `systemAlertHandling` の
内側にこれへの逃げ道を持ちません。`savePassword` はルールとしてのみ宣言されます。`handleSystemAlert`
はそれを拒否します。この経路がツリー内経路だけである以上、SpringBoard のプロンプトにあるような第 2 の
宣言手段には頼れません。`_dismiss_from_tree` のどちらの呼び出しも識別子のないボタンに絞り込むので、残された
1 つの手立てはアプリ自身のアクセシビリティ表記です。衝突する「Save」ボタンに識別子を持たせれば、この
機構が調べる平坦な一覧に一致しなくなりますが、それはテスト対象のアプリが自分のものでないときには、作者の
制御が及ばない変更という代償を伴います。それを踏まえたうえで、これは解決した危険としてではなく、境界が
わかっている、受け入れた危険として扱います。すでに出荷されている単一ラベルでの照合より悪くはなく、その組が
見分けられる場合にはそれより狭く、iOS の 1 バージョンの 1 つの形に閉じています。

### 単位 4 — デモを移行し、回帰用のシナリオを追加する

「パスワードを保存」の 2 つのデモは、保存のプロンプトに `labels: ["Not Now"]` ではなくルールで答えます。
`save_password_browser.yaml` はすでに `rules: [{ prompt: notifications, choice: deny }]` を宣言して
います。`expect` は、そのルールに依存しています。そのため `savePassword` のエントリは、その一覧を置き換えるのではなく
加わります。`save_password_native.yaml` は今日ルールを持たないので、1 件だけの一覧を得ます。
`save_password_browser.yaml` はさらに、`wait: { until: { gone: { label: "Not Now" … } } }` を落とします。
この待機は、ガードを走らせるステップを与えるためだけに存在します。`save_password_native.yaml` は
`wait: { for: { id: signin.value, value: "signedIn" } }` を残します。この待機は、非同期なサインインを
同期させると同時に、ツリー内消去が走る窓でもあります。そのシナリオ自身のコメントは実測しています。iOS 18.6 では
この条件より前に「パスワードを保存」のアラートが着地し、26.3・26.4・26.5 では後に着地します。
`gone` の待機がなぜ代わりに使えないのかも説明しています。単位 1 が着地したあとも、このシナリオで
唯一残るガード付きの step はこれです。`handleSystemAlert` はこのシナリオに一度も出てこないからです。
`expect` は 1 度しか評価されないので、この待機を落とすと、送信との競走に加えて、アラートを片付ける窓
まで失うことになります。

本提案が出発点とした事例のために、3 つめのシナリオを追加します。「パスワードを保存」のアラートが画面を
占めている位置に、通知プロンプトのための `handleSystemAlert` ステップを、割り込みを覆う `wait` ステップ
なしで置きます。補完し合う 2 つと並べて、`ios-e2e` のレーンの `systemalert` タグのシナリオに加えます。

### 単位 5 — ドキュメント

`docs/scenarios.md` とその `docs/ja/` の写しから、`labels` のキーと層合成の行を落とします。
`savePassword` と、それがルール専用であることを加えます。そして `handleSystemAlert` ステップが待機中に
宣言済みの割り込みへ答えるようになったことを記録します。両ページの割り込み監視についての説明も
書き直します。「方針がボタンを名指ししないプロンプトは XCUITest 自身のデフォルトハンドラへ委ねる」は、
それだけでは終わりません。委ねること自体はこれまでどおり起きますが、それに遭遇した step または
`expect` は、アラートが提示したボタンを名指しして失敗するようになります。`docs/architecture.md` と
その写しには、待機がドライバから orchestrator へ移ったことも記録します。各層の待機がどこにあるかを
説明しているページだからです。

### 今回の範囲外

本提案は、人工知能（AI）によるビジョンのフォールバックのコード経路に手を加えません。`run` に
その経路がもうないからです。
[BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback-ja.md)
が [#1843](https://github.com/bajutsu-e2e/bajutsu/pull/1843) でそれを削除し、すでにマージ済みです。
`AlertGuardConfig` を通るすべての経路は、今では決定的です。`run` は `visionInstruction` をまだ渡す層を
読むのではなく、はっきり拒否します（`_reject_vision_instruction`）。そのため `labels` がスキーマから
消えても、そのフォールバックが今も汲んでいる入力は 1 つも失われません。

BE-0402 は `probe_native` が返す `"unhandled"` にも用途を与えていて、本提案はそれを重ねて作るのでは
なく積み上げます。`blocked_note` です。塞がれた step または `wait` 自身の失敗理由に、見えたボタンを
添え、「element not found」という素っ気ない読み方にしません。単位 2b は、同じ発想を BE-0402 が届かな
かった面に適用したものです。**割り込む**アラート、すなわち XCUITest が割り込んだインタラクションを
合成する前に解決してしまうアラートです。単に step を**塞ぐ**だけのアラートとは違います。何にも塞がれた
ままのアラートは、その step 自身の条件待機のタイムアウトを通じてすでに失敗し、`blocked_note` がそれを
止めたものを名指しします。一方、割り込むアラートは XCUITest 自身の解決を完了させ、インタラクションは
まるで何も起きなかったかのように進みます。失敗はなく、注記も一切ありませんでした。単位 2b が閉じる
のは、この隙間です。2 つの面を同じ基準にそろえます。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| ドライバに poll のコールバックを渡す | `handle_system_alert` に `on_poll` 引数を足し、ドライバの既存のループから orchestrator のガードを毎回呼び戻す | ループは居場所を変えずに済む。ただし変更が `Driver` インタフェースに乗るので、すべてのバックエンド、fake ドライバ、適合性スイートが一緒に動く。待機は操作ではなく統制であり、他のあらゆる条件待機はすでに orchestrator が持っている |
| アプリ所有のアラートを SpringBoard の問い合わせから返す | `querySystemAlertButtons` を拡張し、`springboard.alerts` と並べて `app.alerts` も読む | `handleSystemAlert` ステップが `savePassword` を直接名指しできるようになり、単位 3 がそれを禁じる必要もなくなる。ただし BE-0399 が実測して依拠している境界が消える。反応的なガードも、ツリー内タップを許可する `probed_absent` の信号を失う。2 つのプロンプトの順序を正しく決めているのは、その信号である |
| 付け替えではなくラベルの照合を削除する | `labels` を削り、ツリー内消去も一緒に落として、ガードはネイティブの SpringBoard 経路だけで動くようにする | 「ボタンではなくプロンプトを宣言する」のもっとも素直な読み方であり、そして誤りである。ツリー内消去はアプリ所有のアラートを片付けられる唯一の経路なので、削ると `savePassword` は事実上宣言できなくなり、動機としたタイムアウトも直らない |
| `DEFAULT_DISMISSIVE_LABELS` を控えめな推測として残す | 割り込み監視を、組み込みの語のどれかを提供するアラートに答えさせたままにし、一致しないものすべてを報告して失敗させることはしない | 本提案の最初の草稿はまさにこれを行っていた。辞退そのものが安全でないという誤った理解に基づいてのことである。実際には安全である。BE-0399、`docs/architecture.md`、`docs/scenarios.md`、そして `RunnerUITest.swift` 自身のコメントが、そろって辞退を安全なフォールバックとして記録している。辞退が安全だとわかった以上、宣言されていないアラートに答えを推測させることは、native probe とツリー内消去から取り除いたのと同じサイレントな自動判断を、どちらのやり方でも避けられなかった 1 つの面にだけ残すことになる |
| 未対応の割り込みを失敗の理由に添えるだけにし、単独では失敗させない | アラートのボタンを、すでに失敗していた step の理由には報告するが、通っていた step を単独で覆しはしない | 下流の何かを壊さなかっただけの、意図しない許可を代償なしのままにする。これはまさに本提案全体が、シナリオに持たせないよう主張している「成功したように見える」状態である。どのルールも名指ししていないアラートが割り込んだこと自体が、その後の step の検査がどうであれ、シナリオの前提が誤っていた証拠である |
| 形をアラートのボタン集合そのもので照合する | 除外ラベルではなく、形のラベルがアラートのボタン集合と等しいことを要求して、26.5 のアプリ内の保存シートをクレジットカードのアップデートのシートから切り分ける | もっとも素直な切り分けに読めて、必要な場所では計算できない。`savePassword` のルールを照合するのはツリー内の経路だけであり、この経路が見るのは 1 つのアラートのボタンではなく poll のツリー全体にある識別子のないラベル付きボタンである。ほかにそうしたボタンを画面のどこかに持つアプリでは、集合の一致が必ず外れる |
| 迂回策を文書化する | 割り込みがありうる箇所では `handleSystemAlert` の前に `wait` ステップを置く、と `docs/scenarios.md` に記録する | 実装の費用はかからず、本提案を見送る場合の選択肢としては残る。ただし OS のタイミングを作者が予測することを要求し、そのタイミングが不明なときは書けない。書けない状況は、showcase のシナリオのコメントがすでに記述している |
| ステップ側に宣言を足す | `handleSystemAlert` に、待機中に片付ける割り込みを名指しする専用のフィールドを与える | 作者の意図が、それを必要とするステップの真上に残る。ただし 1 つのステップ種別のために `systemAlertHandling` を二重化し、独自のスキーマ、ロケール解決、両言語の文書、codegen への対応が要る |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 単位 1 — `handleSystemAlert` の待機を orchestrator へ移す。ステップにつき 1 つのゲートを
      `_POLL` で駆動し、ステップ自身の読み取りはガードの `pollInterval` とは独立に
      `_SYSTEM_ALERT_POLL_SECONDS` へ絞る。画面を占めていたアラートを名指しする理由で失敗させる。
- [x] 単位 2a — `labels` と `--alert-labels` を削除する。ネイティブの probe と 2 つのツリー内
      消去を `rules` だけへ付け替える。`labels` を書いたままのシナリオは拒否する。
- [x] 単位 2b — `DEFAULT_DISMISSIVE_LABELS` と、それが監視へ渡していた Swift 側の照合を削除する。
      `InterruptionPolicy` / `set_interruption_policy` / `InterruptionPolicyRequest` に `governs`
      を加え、ルールがすべてツリー内専用のシナリオでも方針が governs であり続けるようにする。
      割り込み監視が辞退したアラートを記録する。ただし governs しない方針自身の辞退と、無害な競走に
      負けた一致済みボタンは除く。drain エンドポイントと `Driver.drain_interruptions` プロトコルを
      それぞれ拡張して運ぶ。`expect` の alert-guard retry に今は欠けている 2 度目の drain を加える。
      遭遇した step または `expect` を失敗させる。
- [x] 単位 3 — プロンプトの言語エントリを、除外ラベルの集合を持ちうる形のリストに対応づける。3 つの形と
      プロンプトごとの面の記録とともに `savePassword` を加え、`handleSystemAlert` ステップでは拒否する。
- [x] 単位 4 — 「パスワードを保存」の 2 つのデモを `rules` へ移し、ブラウザ側のデモから迂回策の
      待機を落とす。回帰用のシナリオは `make -C demos/showcase e2e-savepassword` のターゲットに
      加える。単位の設計文が名指す `ios-e2e` のレーンには加えない。「パスワードを保存」のデモは
      どちらも CI のレーンでは走らないからです。
- [x] 単位 5 — `docs/scenarios.md`、`docs/architecture.md`、両方の `docs/ja/` の写しを更新する。
      割り込み監視の辞退がいまや結果を伴うことも含める。

ログ：

- [#1871](https://github.com/bajutsu-e2e/bajutsu/pull/1871) — 単位 1。`Driver.handle_system_alert` が待機をやめました。XCUITest の実装は
  `/systemAlert/query` の 1 回の読み取りに対してステップのセレクタを解決します。`timeout` は
  シグネチャに残し（呼び出し側はすべて 0 を渡します）、他のバックエンドには手を触れていません。
  待機は `bajutsu/common/orchestrator/waits.py` の `wait_for_system_alert` になりました。
  ステップ自身の期限まで `_POLL` でポーリングし、ステップの対象は 0.2 秒の間隔で読み、
  各ポーリングのツリーを 1 つの `_AlertGuardGate` に渡します。`_run_step_body` は
  `kind == "handle_system_alert"` のときに、`wait` と並んでシナリオのガードを与えて実行します。
  アクションハンドラの登録はガードなしのまま残し、`record` のリプレイが使います。
  `probe_native` は `"reserved"` という答えを持つようになり、待機中のステップのセレクタが
  名指しするアラートをガードが辞退します。これが、同じプロンプトに 2 者が反対の答えを出すことを
  止めます。タイムアウトは、アラートが何も出なかった場合と、出たもののセレクタがボタンに
  一致しなかった場合とを区別し、何も片付けられなかったプロンプトについてはガード自身の注記も
  運びます。`docs/scenarios.md` と `docs/architecture.md`、および両方の `docs/ja/` の写しに
  待機の移動を記録しました。単位 5 の残りは後続の単位を待ちます。本文中のパスは、この提案が
  書かれた当時の `bajutsu/…` ではなく、再編後の `bajutsu/common/…` です。もう1つ、単位の本文
  からの逸脱があります。ステップ終端のアラートガードは、失敗した `handleSystemAlert` ステップに
  対して「変わらない」のではなく、丸ごとスキップするようにしました。`wait_for_system_alert` は、
  ステップ自身のセレクタで予約した同じガードを、ステップの期限いっぱいすでに走らせています。予約
  なしでもう一度 probe しても、それ以上のカバレッジは増えません。むしろガードの緩いフォールバック
  方針が、ステップ自身のアラートをタップしてしまう恐れがありました。これでは、そのプロンプトを
  ステップの代わりに決めてしまいます。しかも画面が片付いたあとの再試行が生む一般的な理由で、この
  単位が用意した具体的な失敗理由を上書きしてしまいます。
- [#1894](https://github.com/bajutsu-e2e/bajutsu/pull/1894) — 単位 2a と単位 3 をまとめて実装
  しました。2a がツリー内経路に付け替える照合は、単位 3 が面の記録とツリー内で扱える唯一の
  プロンプトを用意するまで、武装できる rule を 1 つも持ちません。分けて出せば、その間だけ
  「パスワードを保存」のデモが自分のアラートを消せなくなります。`labels`、`--alert-labels`、
  `alertLabels`、`pick_alert_label` を削除しました。`probe_native` は `rules` だけを照合し、
  2 つのツリー内消去は新しい `AlertGuardConfig.tree_rules` で武装します。`_LABELS` はプロンプトと
  言語を、除外ラベルの集合を持ちうる形のリストへ対応づけ、`_SURFACES` は 3 つの応答面のどれに届く
  かをプロンプトごとに記録します。`savePassword` のラベルは iOS 18.6 と 26.5 の両ランタイムの
  `WebUI.framework` から転記しました。`handleSystemAlert` は `step` の面を持たないプロンプトを
  拒否し、`push_interruption_policy` はその面が決して出会えない rule を落とし、subset 照合が黙って
  捨ててしまう除外集合を持つ rule は明示的に失敗させます。デモ 4 本を `rules` へ移しましたが、
  これは単位 4 の前倒しではなく、`labels` の削除がそのまま強制した結果です。単位の本文からの逸脱が
  3 つあります。`_warn_target_rules_reach` は `inner_layers` 引数を失いました。フラグが rule を
  運べなくなり、ターゲットより内側の層はシナリオだけになったからです。`push_interruption_policy`
  は組み込みの dismiss ラベル群を無条件に push するようになり、`labels` でそれを絞っていた
  シナリオがランナーへ送る範囲は、単位 2b がこの一覧を消すまで広がります。そして著者の依頼により、
  この項目の設計にはない変更として、ガードが 1 回限りの dismiss を行う 2 か所で、再試行の前に
  現れた画面が動かなくなるまで待つようにしました。上限付きでベストエフォートの条件待ちなので、
  アニメーション途中のツリーを相手にステップが失敗することはなくなります。
- [#1903](https://github.com/bajutsu-e2e/bajutsu/pull/1903) — 単位 2b と、単位 5 の残りです。
  `bajutsu/common/orchestrator/types.py` から
  `DEFAULT_DISMISSIVE_LABELS` を削除しました。`push_interruption_policy` は、削除した候補一覧の
  代わりに `governs` を送ります。ガードが有効なシナリオでは、ツリー内専用の drop で rule が
  1 つも残らなくても true になります。`Driver.set_interruption_policy` は `candidates` の代わりに
  `governs` を受け取ります。`Driver.drain_interruptions` は `DrainedInterruptions(tapped, declined)`
  の組を返すようになりました。orchestrator 側のラッパーは `declined` を、`AlertEvent` ではなく
  `UndeclaredInterruption` の記録へ変えます。
  `declined` が空でなくなった drain 箇所は、どこも無条件に失敗するようになりました。ステップの
  drain、`expect` フェーズ自身の drain（ガードの probe が何かを片付けたかどうかに関わらず対象にする
  よう、レビューで見つかった漏れを塞ぎました）、再試行のあとの 2 度目の drain（コメントがすでに
  欠落として指摘していたものです）、そして未対応の locale で `handleSystemAlert` に遭遇した際に
  `_handle_action` が取る早期リターンの 4 か所です。最後のものは、actuation の同じ取りこぼしを
  すでに自分のコメントが指摘していました。この失敗はどれも、ステップや `expect` がすでに持っている
  理由へ `undeclared_interruption_note` を追記するのであって、置き換えるのではありません。
  ドレインした記録もすべて名指しし、最初の 1 件だけにはしません。
  Swift 側では、`InterruptionPolicy` から `candidates`/`isEmpty` を削り `governs` を加えました。
  `InterruptionPolicyStore` は、監視が辞退する前に追記する 2 本目のドレインリストを持ちます。
  ただし、アラートのボタン一覧のラベルがすべて空文字列のときは追記しません。これは、数行下で
  `button.exists` がすでにガードしている、アラートが自分で閉じてしまう競走と同じ徴候です。
  `openapi.yaml`、生成される `APIHandler`、レガシーの `Router` は、いずれも新しい
  `governs`/`unmatched` の配線形状を運びます。レガシー経路もいまは、`governs` が欠けているか
  型違いのときに `false` へ既定せず、そのまま拒否します。
  3 巡目は、`if`/`forEach`/`web` の各ステップハンドラを、drain を取りこぼす 3 件目のパターンとして
  見つけました。どれもネストしたステップへ入る前にドライバへ問い合わせており、`_handle_action`
  自身の早期リターンと同じ形です。そこで 4 か所とも、1 つの `_drain_step_interruptions` ヘルパーを
  共有するようにしました。同じ巡では、空ラベルによる競走のガードも見直しました。`buttons.count`
  が 0 でなくても、個々の `.label` はすでに空文字列に解決していることがあるからです。一覧そのものが
  空かどうかではなく、中身をすべて点検するようにしました。
  さらに、diff を読み返すだけでは見つからなかった設計上の欠落も見つかりました。`handleSystemAlert`
  ステップ自身が使うプロンプトは、シナリオの `systemAlertHandling.rules` に含まれるとは限りません。
  宣言しない選択を認めるために、ステップという形があるからです。そのため、先行する操作の割り込みが
  同じプロンプトと先に出会い、一致する規則がなく、このステップが答えようとしていたアラートのせいで
  無関係なステップが失敗することがありえました。`_reserve_declared_alert` はこれを閉じます。
  `prompt`/`choice` 形のステップが待っている間だけ、シナリオ自身の規則に加えてそのステップの対象を
  1 つ追加で送ります。ステップが答え終わる、失敗する、あるいは待機自体が例外で終わるいずれの場合も
  元へ戻します。これは、この項目自身が動機として挙げているシナリオそのものです。ただし `sel` 形の
  ステップには予約すべき識別ラベルの集合がないため、今日の振る舞いのままです。
  セルフレビュー（BE-0347 の二役の作法、3 巡。レビュー自身の上限です）が、ここまでの内容をすべて
  見つけて直しました。`swift build` と `swift test --filter BajutsuRunnerTests`（172 件。
  `InterruptionPolicy.label(for:)` への直接のテストを新たに含みます）が通り、Python 側のテスト一式
  （7,177 件）もすべて通ります。
  `docs/architecture.md` と `docs/scenarios.md`（両方の `docs/ja/` の写しを含む）からは、
  割り込み監視の説明にあった組み込みの dismiss 候補一覧が消えました。方針が有効な状態での辞退は、
  それに遭遇したステップまたは `expect` を失敗させると記録し、予約の仕組みも記録しています。
  単位 5 に残っていたのはこの作業だけだったので、単位 5 も完了します。
- [#1908](https://github.com/bajutsu-e2e/bajutsu/pull/1908) — 最後の単位 4 です。
  `save_password_browser.yaml` から `wait: { until: { gone: { label: "Not Now" } } }` を削除しました。
  反応的なガードにステップを1つ与えるためだけに存在していた迂回策です。続く
  `wait: { for: { id: Close } }` が同じ窓を与えます。アラートがブラウザの上にモーダルで出ている間は
  この条件が偽のままだからです。この項目の動機となった回帰シナリオ
  `save_password_interrupts_step.yaml` も追加しました。割り込みを覆う `wait` ステップを置かずに
  通知プロンプト向けの `handleSystemAlert` ステップを置き、パスワード保存アラートが画面を単独で
  占有している状況を作ります。単位の原文からの逸脱が1つあります。新シナリオが加わる先を
  `ios-e2e` の CI レーンではなく `make -C demos/showcase e2e-savepassword` にしたことです。
  「パスワードを保存」の 2 つのデモはそもそもそのレーンに乗ったことがなく、レーンは明示的な
  パスでシナリオを選ぶうえブラウザのフィクスチャも用意しないため、原文のレーンに関する記述は
  この単位が着地する時点で古びていました。この `make` ターゲットは、いまや 2 本の
  「パスワードを保存」シナリオを順に走らせ、それぞれ端末を消去してからにします。加えて、
  新シナリオの追加が明らかにした `tests/test_showcase_fixtures.py` の除外タグ保証の抜けも
  埋めました。`browser.yaml`、`tabs.yaml`、2 本の「パスワードを保存」デモがもともと漏れていました。
  自己レビューした差分（BE-0347 の二役の手順）が、ステップ前のページ読み込みと入力に対して
  タイミングの余裕が狭すぎる点と、古びたコメント 3 箇所を見つけて直しました。その後、実機の
  iOS Simulator で再検証し、消去し直した端末に対して 2 本の「パスワードを保存」シナリオが
  どちらも通ることを確認しました。`make check`（7,214 件のテスト）も通り、この項目を
  完了させます。

## 参考

- [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)
  — 反応的なガードの待機中の介入と、iOS の「パスワードを保存」のプロンプトが待機を止める最初の記録。
- [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)
  — ガードが優先する決定的なネイティブ経路と、`system_alert_labels` による在否の問い合わせ。
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) —
  `handleSystemAlert` ステップと、本提案が引き取る「許可プロンプトだけでなくすべてのアラートを覆う」の見送り。
- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)
  — プロンプトと選択のラベル表と、その値を推測せず転記するという基準。
- [BE-0369](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice-ja.md)
  — その表に前回加えられたプロンプトと、本提案が従う先例。
- [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md) —
  唯一の宣言手段になる、プロンプトごとの宣言である `rules`。
- [BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy-ja.md)
  — どのプロンプトがどこに出るか、その間の順序、そしてツリー内消去に付けられた条件。
- [BE-0401](../BE-0401-system-alert-handling-dsl-consolidation/BE-0401-system-alert-handling-dsl-consolidation-ja.md)
  — `labels` を導入した経路ごとのキーの整理と、別名を置かずにキーを削除する先例。
- [BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback-ja.md)
  — `run` の AI ビジョンのフォールバックを削除し、`blocked_note` を加えた、すでにマージ済みの項目。
  塞ぐ面の先例であり、単位 2b はその先例に割り込む面をそろえる。
