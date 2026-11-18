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

本提案は、アプリ自身のアクセシビリティツリーから見えない OS のプロンプトへ Bajutsu が答える方法を、2 つ変えます。

1 つめは、`handleSystemAlert` ステップにガードを与えることです。ステップの待機を XCUITest ドライバから orchestrator へ移します。宣言済みのアラートがステップに割り込んだとき、ステップが失敗したあとではなく、まだ待っているうちに答えられます。

2 つめは、シナリオがボタンではなくプロンプトを名指しするようにすることです。`systemAlertHandling` は、候補ボタンラベルの順序付きリストである `labels` を落とします。`labels` に対して照合していた経路は、すべてシナリオが書く `rules` へ付け替えます。ツリー内消去もその 1 つです。アプリ自身のプロセスに出るアラートを片付けられる経路は、これしかありません。動機とした事例を宣言可能にするには、iOS の「パスワードを保存」のアラートをプロンプト表に加えます。この表で SpringBoard が所有しないプロンプトは、これが最初になります。

今日、このようなプロンプトには 2 つの経路で答えています。1 つは `handleSystemAlert` ステップです。[シナリオ](../../docs/ja/glossary.md#シナリオのオーサリング)がプロンプトを予期する 1 箇所にステップを置き、ステップが名指ししたボタンをタップします。もう 1 つは `systemAlertHandling` が設定する反応的なガードです。実行中のどこであれプロンプトが割り込んだら、シナリオの方針が名指ししたボタンをタップします。

1 つの面は、同じやり方で付け替えられません。理由を先に述べます。XCUITest は、自分の操作に割り込んだアラートを、その操作を合成する前に解決します。その解決には常に何かが答えます。辞退した監視は、アラートを XCUITest 自身のデフォルトハンドラへ渡します。ハンドラはアラートの**デフォルト**ボタンをタップして片付けます。これは、その監視が存在する前に起きていたことと同じです。

どのルールも同定しないアラートに対して、辞退が唯一の安全な答えです。割り込みを主張しながら片付けずにいると、次のインタラクションのたびに監視が呼び直されます。BE-0399 は、これが runner を落とすまでループすることを実測しています。

本提案は、このタップが起きること自体を止められません。変えるのは、タップが黙って起きるかどうかです。割り込み監視は、辞退したアラートのボタンを報告するようになります。そのアラートを見た step または `expect` は、シナリオに代わって誰かが答えたかのようにランを続けません。ボタンを名指しして失敗します。

後方互換は意図的に保ちません。`labels` を書いたままのシナリオは読み込みに失敗し、代わりに `rules` を使うよう名指しするエラーになります。これは、[BE-0401](../BE-0401-system-alert-handling-dsl-consolidation/BE-0401-system-alert-handling-dsl-consolidation-ja.md) が自身の削除したキーに与えたのと同じ扱いです。

## 動機

`handleSystemAlert` ステップは、待機中に iOS の「パスワードを保存」のアラートが画面に出ているとタイムアウトします。サインインしたあとに通知の認可を要求するアプリで実測しました。ステップは目的のプロンプトを一度も見ないまま `timeout` を使い切り、`no system alert appeared within <timeout>s` で失敗します。実際に出ていたアラートは、実行結果のどこにも現れません。そのためこの失敗は、割り込みではなく、許可プロンプトが現れなかった事象に見えます。

原因は、ステップの待機と、割り込みを片付けられるガードが、呼び出しの反対側にいることです。`XcuitestDriver.handle_system_alert` は、`springboard.alerts` を自分の期限まで poll します。実装は [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) にあります。反応的なガードは、[`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) の `_AlertGuardGate` です。これは `wait` ステップが 1 poll ずつ駆動する orchestrator 側のオブジェクトです。

ガードを背後で走らせるスレッドはありません。`_run_step_body` が `alert_guard` を渡すのは、`kind == "wait"` のときだけです。他のステップ種別は無視すると、その docstring 自身が記録しています。実装は [`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py) にあります。つまり、ドライバのループが回っているあいだ、ガードは割り込めません。ステップ終端のガードは、どのステップ種別でも発火します。`handleSystemAlert` も例外ではありません。ただし発火するのは、ステップが `timeout` を使い切って失敗したあとです。

「パスワードを保存」のアラートは、ステップが poll する問い合わせから見えません。[BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy-ja.md) は、どのプロンプトがどこに出るかを実測しています。通知の認可要求は、別プロセスの SpringBoard のアラートです。これに対し、iOS は「パスワードを保存」のアラートを**アプリ自身のプロセス**に出します。アラートはアプリ自身のツリーに、識別子のないラベル付きボタンとして現れます。Web フォームに対しては `app.alerts`、アプリ自身の入力欄に対しては `app.sheets` のシートです。

runner の `querySystemAlertButtons` が読むのは、`springboard.alerts.buttons` だけです。そのため「パスワードを保存」のアラートは、アラートが存在しないのと同じに見えます。ステップの poll は、そのアラートが画面を占めているあいだ、空のボタン一覧を受け取り続けます。しかもアラートはアプリに対してモーダルなので、ステップが待っている許可要求そのものが発生しません。

シナリオの作者は今日、`wait` ステップを置いてこの穴を迂回しています。ガードが配線されている唯一のステップ種別だからです。[`save_password_browser.yaml`](../../demos/showcase/scenarios/save_password_browser.yaml) は、ほかの目的を持たない `wait: { until: { gone: { label: "Not Now" … } } }` を置いています。OS のタイミングがどちらに転んでも成り立つ待機の形を、コメントが説明しています。この迂回策は、iOS がいつ割り込むかを作者が予測できることを要求します。割り込みのタイミングが本当に不明なときには、そもそも書けません。

問題のもう半分は、シナリオが何を宣言できるかにあります。`systemAlertHandling` は 2 つのキーを取ります。プロンプトと選択を名指しする `rules` と、どのルールも同定しなかったものに対して参照される候補ボタンラベルの順序付きリスト `labels` です。ラベルが名指しするのは、プロンプトではなくボタンです。そのため `labels` は、シナリオが一度も説明していないアラートのボタンを、ガードがタップすることを許します。

BE-0399 は、この危険を直接記録しています。「Cancel」と「Close」は、実在の画面が正当に表示しうる普通のアプリの語彙です。そこで BE-0399 は、シナリオ自身が `labels` を宣言していることをツリー内消去の条件にし、組み込みのデフォルト値からはその消去を外しました。この条件は、シナリオの `labels` を作者の意図の代理として扱っています。しかし代理でしかありません。宣言が述べているのは、受け入れるボタンの文字列であって、どのアラートを予期しているかではないからです。

プロンプトを名指しする形へ移すのは、整理のためだけではありません。宣言がもっとも働く場所は、ツリー内消去です。`AlertGuardConfig.dismiss_from_tree_once` は、`pick_alert_label(self.labels, …)` で照合します。`_AlertGuardGate._dismiss_from_tree` も同じです。照合の対象は、poll 自身のツリーにある識別子のないラベル付きボタンです。この 2 つは、`springboard.alerts` から見えないアラートを片付けられる、このコードベースで唯一の機構です。この 2 つを `rules` へ付け替えると、シナリオは「どのアラートをガードに片付けてほしいのか」を言えるようになります。「どの語ならタップされてよいか」を言う必要はなくなります。

答えの経路がもう 1 つあります。こちらは、未対応のアラートをそのまま放っておけません。しかも今日は、失敗にもせず、許可してしまいます。XCUITest は、割り込んだ相手のインタラクションを合成する前に、そのアラートを解決します。runner の割り込み監視は、その解決に 2 通りのどちらかで答えます。1 つは、ルールに一致してタップすることです。もう 1 つは、辞退して XCUITest 自身のデフォルトハンドラへ渡すことです。ハンドラは、そのアラートがデフォルトと呼ぶボタンをタップします。後者は、本書の冒頭で述べたのと同じサイレントな許可を、`rules` が名指ししないあらゆるアラートについて起こします。

`bajutsu/orchestrator/types.py` の `DEFAULT_DISMISSIVE_LABELS` は、今日、監視のフォールバック候補として push されています。組み込みの語のどれかを提供するアラートには控えめに答えるので、アラートがその許可へ届く頻度は狭まります。しかし穴は閉じません。その語のどれも提供しないアラートは、今日と同じように辞退されます。その辞退がなぜ避けられず、BE-0399 以来はっきり安全とされているのかは、単位 2 でたどります。

本提案が届いたかどうかは、あとから読む人にも 3 つの観測可能な差で判別できます。

1 つめは、「パスワードを保存」のアラートが画面を占めているところへ、通知プロンプトのための `handleSystemAlert` ステップを置く showcase のシナリオです。今日は、上に引いたタイムアウトで失敗します。変更後は、割り込みを覆う `wait` ステップを 1 つも置かずに通ります。

2 つめは、移行した 2 つのデモです。どちらも保存のプロンプトに、`labels: ["Not Now"]` ではなく `savePassword` のルールで答えます。`save_password_browser.yaml` は、迂回策として持っている `gone` の待機も落とします。

3 つめは、どの `rules` エントリも名指ししないアラートが割り込んだところで、ふつうの `tap` を走らせるシナリオです。今日は、その step が通るか、アラート自身の答えがたまたま引き起こした無関係な理由で失敗します。本提案のあとは、アラートのボタンを実行結果に残したうえで、名指しで失敗します。

## 詳細設計

### 単位 1 — ステップの待機を orchestrator へ移す

`Driver.handle_system_alert` は待つのをやめます。シグネチャはそのままです。呼び出し側は timeout に 0 を渡し、すでに出ているとわかっているアラートに対して、問い合わせとタップを行います。`AlertGuardConfig.probe_native` が `_NATIVE_TAP_TIMEOUT` を通じて今も行っていることです。

XCUITest の実装は、`bajutsu/drivers/xcuitest.py:1105-1114` のポーリングループを落とします。そして `/systemAlert/query` の 1 回の読み取りに対して、セレクタを解決します。Swift 側の変更は不要で、他のバックエンドにも影響しません。`HANDLE_SYSTEM_ALERT` を広告しないバックエンドでは、`capability_preflight` がこのステップをすでに拒否します。ドライバは、実行中の最後の砦として `UnsupportedAction` を送出し続けます。

待機そのものは、ステップのハンドラへ移ります。ハンドラは [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py) の `_do_handle_system_alert` で、ガードを引数として受け取ります。ハンドラは `_AlertGuardGate` をステップにつき 1 つ作り、ステップ自身の期限まで poll します。この形は `_wait` がすでに使っているものと同じです。`_wait` もゲートを 1 度だけ作り、各 poll で `gate.observe(elements)` を呼びます。各 poll は、次の順序で 2 つのことを行います。

1. `driver.system_alert_labels()` を読む。ステップ自身のセレクタがそのラベル群に対して解決したら、`driver.handle_system_alert(sel, 0)` でタップして復帰する。
2. 解決しないなら、その poll のツリーを `gate.observe` へ渡し、poll を続ける。

1 番目のタップは、`probe_native` がすでに扱っている時間差の競走を、同じように抱えます。扱い方も同じです。`ElementNotFound` は、問い合わせとタップのあいだにアラートが自分で閉じたことを意味します。`AmbiguousSelector` は、アラートがまだ出ていて、セレクタのラベルを 2 つ提示していることを意味します。どちらもステップの評決ではありません。ハンドラは期限まで poll を続けるので、無害な競走が費やすのはステップではなく 1 回の poll です。

外側のループは `_POLL`（0.05 秒）で poll します。`_wait` 自身の間隔と同じです。この 1 tick は安いタイムスタンプの比較であり、毎回クロスプロセスの読み取りを行うわけではありません。tick が引き起こしうる 2 つの読み取りは、それぞれ独立した頻度を保ちます。今日すでに払っているコストから変わりません。

ステップ自身の `system_alert_labels()` の読み取りは、内部で `_SYSTEM_ALERT_POLL_SECONDS`（0.2 秒）に絞られます。この単位が移す前に、ドライバ自身の poll ループが払っていたのと同じ間隔です。ステップが自分の目的のプロンプトに答える速さは、今日より遅くなりません。

`gate.observe()` のクロスプロセスな SpringBoard の probe は、既存の別の絞り込みを保ちます。`_observe_native` は、自分自身を `guard.poll_interval` へ絞っています（`_last_native`。本提案では変えません）。`observe()` がどれだけ頻繁に呼ばれるかとは無関係です。これは、ガードが覆うすべての `wait` ステップに対して BE-0315 がすでに受け入れている負荷と同じです。

この 2 つを 1 つの共有した間隔に結びつけることは、本単位の初期の草稿がまさにそうしていましたが、誤りでした。`save_password_browser.yaml` は `pollInterval: 5` を意図的に広く取っています。2 つの重なったプロンプトを probe をまたいで出したままにするための設定です。結びつければ、この設定がステップ自身の応答性にまで及びます。

その場合、単位 4 の回帰用シナリオの `handleSystemAlert` ステップが自分の目的を見つけられる窓は、`tap` サイズしかありません。その窓は 5 秒の隙間の中にあります。2 つの頻度を切り離せば、この緊張は消えます。ガードの SpringBoard の probe のために広い `pollInterval` を必要とするシナリオも、ステップが自分のプロンプトに気づく速さでは、何も払わずに済みます。

`_dismiss_from_tree` をゲートから切り出さずに、ゲートごと再利用します。この選択が、poll をまたぐ状態を成り立たせます。再タップの間隔、拒否されたときの断念、ラベルごとのタップ上限は、いずれもゲートのフィールドとして poll のあいだ持ち越されます。切り出した関数には、それを置く場所がありません。poll ごとにゲートを作り直せば、毎回すべてが初期化されてしまいます。

ゲートの再利用は、BE-0399 が確立した順序も、書き直すことなくそのまま与えます。`_observe_native` は SpringBoard を先に probe し、ルールが同定したアラートにそこで答えます。ツリー内タップを発行するのは、自分自身の probe が SpringBoard のアラートなしを報告した poll だけです。

ステップ自身のセレクタをゲートより先に照合するのは、両者が同じプロンプトを取り合わないようにするためです。シナリオは、ステップが答えるために置かれたプロンプトに対する反応的なルールを、逆の選択で持ち得ます。たとえば `rules: [{ prompt: notifications, choice: deny }]` と、`choice: grant` を名指しするステップが同居する場合です。先にアラートを読んだ側が、そのまま決めてしまいます。

上の 1 番目は、各 poll の最初の読み取りをステップに与えることで、通常の場合を解決します。残るのは、1 つの poll のなかで、ステップの読み取りとゲート自身の probe のあいだにプロンプトが現れる場合です。これはゲート側で閉じます。ハンドラはステップのセレクタをゲートへ渡し、`probe_native` は、そのセレクタが解決するアラートを辞退して、ステップの次の poll に残します。ゲートが答えるのは、ステップが待っていないものだけです。

`_run_step_body` は、`kind == "wait"` と並べて `kind == "handle_system_alert"` でも `alert_guard` を渡します。docstring は両方を名指しするよう更新します。ステップ終端のガードは変えません。待機中に割り込みへ答えるようになったステップは、単にそこへ到達する頻度が下がります。

期限を過ぎたときの失敗は、ステップが何を見たかを名指しします。メッセージは今日の `no system alert appeared within <timeout>s` を置き換え、3 つの場合を区別する理由になります。アラートが 1 つも現れなかった場合。アラートは現れたが、セレクタがそのボタンに一致しなかった場合。宣言されていないアラートが、待機のあいだ画面を占めていた場合です。3 つめは画面に出ていたボタンを添えます。実行結果を読む人は、シナリオに欠けていたルールを補えます。

### 単位 2a — アラートをプロンプトで宣言し、ラベルを照合していた経路を付け替える

`labels` を [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py) の `SystemAlertHandling` から削除します。`run` からは `--alert-labels` オプションを削除します。`_flag_alert_policy` に残すのは `--alert-poll-interval` だけです。`--alert-vision-instruction` は、BE-0402 がそのフラグの誘導していたフォールバックごと、すでに `run` から退けています。このオプションは [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) にあります。

`rules` の代わりのフラグは設けません。ルールはプロンプトと選択の組であり、それを読みやすく運べるフラグはないと BE-0401 がすでに記録しています。プロンプトごとの宣言は、シナリオファイルとターゲット設定の宣言のままにします。

`AlertGuardConfig` は `labels` フィールドを失います。BE-0401 が確立した層合成の表は、行を 1 つ失います。`rules` は、内側の層から順に連結されるリストのキーのままです。`pollInterval` は、値を与えるもっとも内側の層が勝つ唯一のスカラーのままです。有効と無効の真偽値も変わりません。

`labels` に対して照合していた呼び出し元は 3 つあり、いずれも削除ではなく付け替えます。

| 呼び出し元 | 今日 | 変更後 |
|---|---|---|
| `AlertGuardConfig.probe_native` | `match_alert_rule(self.rules, …)` のあと `pick_alert_label(self.labels or DEFAULT_DISMISSIVE_LABELS, …)` | `match_alert_rule(self.rules, …)` だけ。どのルールも同定しなければ、今日と同じく `"unhandled"` |
| `AlertGuardConfig.dismiss_from_tree_once` | `self.labels` が空でないことで武装（`if not self.labels: return None`）し、`pick_alert_label(self.labels, …)` で照合 | ツリー内で働けるルールを 1 つ以上持つことで武装し、そのルールに対して `match_alert_rule(…)` で照合 |
| `_AlertGuardGate._dismiss_from_tree` | `if self.guard.labels and probed_absent` で武装 | ツリー内で働けるルールを 1 つ以上持つことと `probed_absent` で武装し、そのルールに対して `match_alert_rule(…)` で照合 |

荷重を担うのは、ツリー内の 2 行です。この 2 つは、poll 自身のツリーにある識別子のないラベル付きボタンに対して照合します。`springboard.alerts` から見えないアラートを片付けられる唯一の機構で、「パスワードを保存」のアラートもそこに含まれます。付け替えずに照合ごと削除すると、`rules: [{ prompt: savePassword, choice: deny }]` に対して働ける経路が 1 つも残りません。

この 2 つの経路のほかの部分は、いずれも変えません。タップ前の一意性の事前確認、再タップの間隔、拒否されたときの断念、ラベルごとのタップ上限は、そのままです。ツリー内タップは、自分自身の probe が SpringBoard のアラートなしを報告した poll でしか発行しません。この BE-0399 の規則も、そのままです。

変わるのは、ツリーへ問う内容だけです。問うのは、どの宣言済みのアラートが出ているかであり、どの許容語がボタンに載っているかではありません。この 3 つの呼び出し元は `pick_alert_label` の唯一の Python 側の呼び出し元なので、この関数は `labels` とともに削除します。

上の表の「ツリー内で働ける」は新しい概念ではなく、単位 3 が導入するプロンプトごとの面の記録を、反対側から読んだものです。どのルールでもツリー内の経路を武装させると、作者が求めた範囲を越えます。`notifications` だけを宣言したシナリオでも、SpringBoard にしか現れないプロンプトのために、ツリー内の照合が武装します。そして、識別子のない「Allow」と「Don't Allow」をたまたま表示しているアプリの画面が、タップされます。武装させるのは、プロンプトがツリー内で働けると記録されたルールだけです。これが、この範囲の拡大を防ぎます。

同じ記録が、`push_interruption_policy` の送る内容も決めます。ツリー内でしか出ないプロンプトのルールは、push するルールの一覧から落とします。割り込み監視は、XCUITest の操作に割り込んでくる別プロセスのアラートのためにあります。アプリ自身のプロセスに出るアラートは、そこへ届きません。

落とすのは整理のためだけではありません。`InterruptionPolicy.label(for:)` は、ルールを部分集合で照合します。`savePassword` のアプリ内の形を push すると、単位 3 が Python 側で閉じた衝突を、Swift 側で開き直すことになります。これらのルールを push しなければ、監視が見るルールに除外の集合は 1 つも載りません。次の単位 2b の 2 つの変更は、どちらも除外を勘定に入れる必要がありません。

これは偶然ではなく、構成として成り立たせます。除外の集合を持ち、なおかつツリー内だけのものではないルールを、push は拒否します。将来 SpringBoard に届く形が除外を必要としたとき、はっきり失敗します。拒否しなければ、除外が黙って落ちたまま監視へ届きます。それは、1 つ表を足したあとの部分集合の衝突です。

`labels` を読む場所は、いま削除した `push_interruption_policy` の候補のフォールバックを除くと、あと 1 つです。ここも照合の場所ではありません。[`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) の `_warn_target_rules_reach` は、シナリオが自分で答えているかどうかを判定します。そのために `any(layer.labels for layer in inner_layers)` を読みます。判定は `any(layer.rules for layer in inner_layers)` へ付け替えます。

これと並べて付け替えるビジョンのフォールバック向けの指示は、もうありません。BE-0402 が、`run` のフォールバックごと `_vision_instruction` を削除しました。シナリオの `labels` がそこを誘導していたのは、その変更が着地するまでのことです。

`_resolve_rules` は、覆っていない言語に対して `UncoveredSystemAlertLocale` を送出し、その救済として `labels` を案内しています。このメッセージを、残る 2 つの救済を名指しする形に書き直します。言語を [`bajutsu/scenario/system_alerts.py`](../../bajutsu/scenario/system_alerts.py) に加えるか、表が覆うロケールを固定するかです。

`labels` を書いたままのシナリオは、読み込みに失敗し、`rules` を名指しするエラーになります。BE-0401 は `enabled` と 2 つの非推奨の綴りを同じやり方で削除し、後方互換を保たないことを明言しました。本提案は別名を持ち回るのではなく、その先例に従います。

### 単位 2b — 割り込み監視が辞退したアラートを報告する

`DEFAULT_DISMISSIVE_LABELS` は残さず削除します。それが監視へ渡していた Swift 側の照合も、一緒に落とします。[`BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift`](../../BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift) の `InterruptionPolicy.candidates` を削除します。`label(for:)` は `rules` だけで照合します。`openapi.yaml` の `InterruptionPolicyRequest` も、対応するフィールドを落とします。

この面での辞退は、「まだ安全とは言えない」ではなく「安全」であり、すでに文書化されています。

`docs/architecture.md:708-709` は、方針がボタンを名指ししないプロンプトを記録しています。記録は「XCUITest 自身のデフォルトハンドラへ委ねる、変わらず（BE-0399）」です。`docs/scenarios.md:168-169` の記録は、「この監視が存在する前と同じく XCUITest へ委ねる」です。`RunnerUITest.swift` 自身のコメントも、辞退を「唯一の安全なフォールバック…この監視が存在する前に起きていたことであり、実際にそれを片付ける」と呼びます。

常駐 runner を丸ごと落としうるループが起きるのは、割り込みを**主張**しながら片付けない監視からです。アラートを引き渡す監視からは起きません。`rules` が名指ししないアラートに対して、監視が答えを推測する必要はどこにもありません。組み込みの候補が果たしていたのは、その記録されない許可へアラートが届く頻度を狭めることだけでした。許可を閉じることはありませんでした。

推測に代わるのは報告です。ただし、「ガードが実際に方針を持つ」ことと「push するルール一覧が空でない」ことは同じ事実ではありません。今日の `isEmpty` の確認は、両者を混同しています。

`InterruptionPolicy` は、`rules` と並んで `governs: Bool` フィールドを得ます（`candidates` の代わりです）。`Driver.set_interruption_policy` と `InterruptionPolicyRequest` も、`candidates` の引数とフィールドを `governs` に置き換えます。`push_interruption_policy` は、これを `guard is not None` に設定します。`systemAlertHandling` が有効なシナリオならどれも真になります。そのシナリオのルールが、ツリー内専用のフィルタをくぐり抜けたかどうかとは無関係です。

`governs` がないと、唯一のルールが `savePassword` であるシナリオで問題が起きます。単位 4 が `save_password_native.yaml` にするのが、まさにこのシナリオです。ツリー内専用フィルタがルールを落とすと、空のルール一覧を push します。`isEmpty` の確認が「このシナリオは何も宣言していない」と読めば、どうなるでしょうか。そのシナリオが一切ルールを持たない、ほかのすべてのアラートについて、単位 2b が閉じるはずの許可をサイレントに再び開いてしまいます。実際に宣言はあったのに、この面が働ける形には絞り込まれなかった場合は、宣言が何もない場合とは違います。

`governs` が偽になるのは、`guard is None`（ガードが存在しない、または `systemAlertHandling: false`）のときだけです。これは、今日のサイレントで記録されない許可をそのまま残すべき、唯一の場合です。何も宣言しないことは、その作者自身の選択だからです。

割り込み監視は、辞退する前に、答えられなかったものを記録するようになります。`InterruptionPolicyStore` は drain 対象を 2 つ持ちます。1 つは、タップ済みのラベルのために今もあるものです。もう 1 つは新しいもので、方針が `governs` である poll で `label(for:)` が `nil` を返した、各アラートのボタンラベルを持ちます。

監視はそこへ追記してから、今までどおり `false` を返します。そのあとに続くタップは変わりません。XCUITest 自身のタップで、そのアラートがデフォルトと呼ぶボタンです。その時点で変えられるものが何もないからです。

近い辞退が 2 つあり、どちらも意図的に記録しません。

1 つめは、`RunnerUITest.swift` の `guard policy.governs else { return false }` です。`label(for:)` を呼ぶより先に走り、そこで打ち切ります。ガードが存在しない場合の辞退は、今日と同じサイレントな許可のままです。何も宣言しないのはそのシナリオの作者自身の選択であり、本提案が拾おうとしている見落としではないからです。

2 つめは、競走の再来です。本提案のほかの場所では、`probe_native` がすでにこの競走を無害として扱っています。ラベル自体は一致したのに、タップの前にそのボタンが消えた場合（`guard button.exists else { return false }`）です。これも記録せずに辞退します。そのアラートは未宣言だったのではありません。ボタンが自分自身の消去との競走に負けただけです。

`POST /interruptionPolicy/drain` の応答は、既存の `labels` と並んで `unmatched` フィールドを得ます。辞退して記録された各アラートにつき 1 つのボタン一覧を、古い順に持ちます。

[`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) の `Driver.drain_interruptions` は、戻り値の型を `list[str]` から小さな組に変えます。組の中身は 2 つです。タップ済みのラベル（変わらず）と、辞退したものが持っていたボタン一覧です。`bajutsu/orchestrator/types.py` の orchestrator 側 `drain_interruptions` も、後半の扱いを変えます。`AlertEvent` ではなく `UndeclaredInterruption` の記録にします。シナリオに代わって何も答えていないので、片付けとして報告するものがないからです。

[`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py) の `run_scenario` は、この 2 つめの一覧を、すでに 1 つめを drain している場所すべてで読みます。retry を数えれば、2 箇所ではなく 3 箇所です。

1 つめは、step の終端です。`outcome.alerts.extend(drain_interruptions(active_driver))` は、step 自身の本体から `outcome.ok` と `outcome.reason` が決まったあとに実行されます。未対応の割り込みは、それらを上書きします。step は無条件で失敗し、そのアラートが提示したボタンを名指しします。それまで通っていた step でも同じです。

名指しされていないプロンプトが黙って解決されているあいだ通っていた step は、「成功したように見える」状態そのものです。本提案は、その状態への信頼をやめさせるためにあります。ほかの 2 面と同じく、この面にも当てはまります。

2 つめは、`expect` のあいだです。`expect_alerts.extend(drain_interruptions(driver))` にも、同じ上書きが働きます。アサーションの結果を参照する前に `failure` をセットするのも、理由も同じです。

3 つめは、`expect` 段階の alert-guard retry です。最初の drain は、この段階自身の alert-guard retry より先に 1 度だけ走ります。今日は、その retry のあとを何も drain しません。retry のコメント自身が「このフェーズをほかに drain するものはない」とすでに記録しているとおりです。

これまでの代償は、`AlertEvent` を 1 つ逃すだけでした。今後は、失敗を丸ごと見落とす代償になります。そこで retry には、そのコメントが「ない」と書いていた 2 度目の drain を加えます。ここで得た `UndeclaredInterruption` も、`failure` を決める前の同じ判定へ合流させます。

一致した割り込みの扱いは、どちらの呼び出しでも変わりません。一致した割り込みはこれまでどおり `AlertEvent` になり、それ自体では何も失敗させません。

### 単位 3 — 「パスワードを保存」をラベル表に加える

[`bajutsu/scenario/system_alerts.py`](../../bajutsu/scenario/system_alerts.py) の `SystemAlertPrompt` と `_LABELS` に、`savePassword` を加えます。このプロンプトは、表がいま置いている前提を 3 つ壊します。3 つを閉じることも、この単位の一部です。

**SpringBoard の所有ではありません。** 表はボタンの**ラベル**の出所です。そのラベルをタップする経路は、SpringBoard への問い合わせがアラートを見られるかどうかで、別に選ばれます。したがって参照の側は、どのプロセスがアラートを所有するかに依存しません。表に載っていることから SpringBoard のアラートだと推論されないよう、docstring にこの区別を記録します。

**`handleSystemAlert` ステップでは名指しできません。** ステップは `driver.system_alert_labels()` に対してセレクタを解決します。この問い合わせが読むのは `springboard.alerts` だけです。`prompt: savePassword` を名指しするステップは、期限まで空のボタン一覧を poll することになります。本提案が取り除こうとしている失敗そのものです。

そのため `savePassword` は、`systemAlertHandling` のルールとしてのみ宣言できます。`HandleSystemAlert` は、解決しようのないステップを受け入れず、ルールの形を名指しするメッセージとともに、これをパース時に拒否します。

宣言の面が分かれるのは、このプロンプトが最初です。そこで表は、プロンプトがどの面に届くかの記録を、プロンプトごとに持ちます。面は 3 つあります。`handleSystemAlert` ステップ、ガードのネイティブな SpringBoard の probe、ガードのツリー内消去です。この記録は、3 つの仕事をします。ステップでの `savePassword` を拒否すること。どのルールがツリー内の経路を武装させるかを決めること。ツリー内でしか出ないルールを、割り込み監視へ push する方針から外すことです。あとの 2 つは、単位 2 が述べたとおりです。

**ボタンの組を 3 通り描画します。** 以下のラベルは、`WebUI.framework/<lang>.lproj/Localizable.strings` から転記しました。このファイルは、Simulator ランタイムの cryptex の `System/Cryptexes/OS/System/Library/PrivateFrameworks/` の下にあります。BE-0320 が求めるとおり、新しいランタイムで表を検査し直す人にとって、この置き場所には 2 つ意味があります。

1 つは、表にすでにある 3 つのプロンプトが、ランタイムルートの `System/Library/PrivateFrameworks` にある、cryptex の外のフレームワークに由来することです。もう 1 つは、`.strings` がいずれも Apple binary property list であり、中身が平文検索ではなく `plutil` に対して開くことです。

| アラート | iOS | 英語 | 日本語 |
|---|---|---|---|
| Web フォームに対して出るもの | 18.6 と 26.5 | Save Password / Never for This Website / Not Now | パスワードを保存 / このWebサイトでは保存しない / 今はしない |
| アプリ自身の入力欄に対して出るもの | 18.6 | Save Password / Not Now | パスワードを保存 / 今はしない |
| アプリ自身の入力欄に対して出るもの | 26.5 | Save / Not Now | 保存 / 今はしない |

動くのは、受け入れる側のボタンです。iOS 26.5 には 18.6 にないキーがあります。`"Save Password (save login information sheet in app)"` で、値は「Save」です。そのため、同じ受け入れの意図が、Web フォームでは「Save Password」、アプリ自身の入力欄では「Save」と表示されます。拒否する側のボタンは 3 通りとも「Not Now」で、日本語はいずれも「今はしない」です。

そこで `_LABELS` は、プロンプトの言語エントリを 1 組の `{grant, deny}` ではなく、形の**リスト**に対応づけます。1 つの形が持つのは次の 3 つです。アラートを同定するラベル。各選択がタップするラベル。その形を除外するラベルの集合です。最後の 1 つは省略できます。`notifications`、`tracking`、`paste` は、除外を持たない形を 1 つだけ宣言します。解決する値に変化はありません。`savePassword` は、上の表の順に 3 つを宣言します。

`_resolve_rules` は形ごとに 1 つの `ResolvedAlertRule` を出し、`match_alert_rule` は除外の確認を持ちます。ルールが一致するのは、同定用のラベルがすべてちょうど 1 回ずつ存在し、なおかつ除外ラベルが 1 つも存在しないときです。前半は今日と同じです。

Web フォームの形に除外は要りません。3 つのボタンを同時に出すアラートは、ほかにないからです。18.6 のアプリ内の形は「Save Password」と「Not Now」で同定します。この組は Web フォームのアラートも満たしますが、無害です。どちらの形も、同じ選択に対して同じラベルをタップするからです。

除外が要るのは、26.5 のアプリ内の形です。ラベルは「Save」と「Not Now」です。同じ `WebUI.framework` が、クレジットカードのアップデートのシートに「Save」「Never for This Card」「Not Now」を与えています。組だけでは、そのシートにも答えてしまいます。そこでこの形は「Never for This Card」を除外します。2 つを見分ける唯一のラベルです。

形のラベルをアラートのボタン集合そのものと一致させるのではなく、除外の集合を使うのは、照合の現場が実際に計算できるのがそれだからです。`savePassword` のルールが届く先はツリー内の経路だけです。この経路が見るのは、1 つのアラートのボタンではなく、poll のツリー全体にある識別子のないラベル付きボタンです。

`_dismiss_from_tree` は候補一覧をそのように組み立てます。`shows_app_ui` の docstring は、アプリ全体が識別子をまったく持たないこともありうると記録しています。この経路に、「アラートのボタン集合」という比較対象は存在しません。

除外は、同定用のラベルがすでに問われているのと同じ平坦な一覧に対する問いです。そのため、同定用のラベルが成り立つところなら成り立ちます。Web フォームの形と 18.6 のアプリ内の形が、これに当たります。同定用の組（「Save Password」「Never for This Website」）が具体的で、画面のどこかで偶然一致することは考えにくいからです。

26.5 の組は、それより弱いケースです。あとから読む人まかせにせず、はっきり述べておきます。「Save」と「Not Now」は、BE-0399 が「Cancel」や「Close」と同じ範疇に挙げた、普通のアプリの語彙です。ほかのすべてのツリー内ルールと共有するこの平坦なツリーでの照合は、両方向の危険を抱えています。除外が閉じるのは、そのうち一方向だけです。

1 つめの危険は、誤った一致です。識別子のない「Save」と「Not Now」をたまたま表示していて、保存シートは出ていないアプリの画面が、この組を満たしてタップされます。除外の集合は、クレジットカードのシートを除外するだけなので、ここでは何の役にも立ちません。

2 つめの危険は、誤った不一致です。`match_alert_rule` は、同定用のラベルがそれぞれちょうど 1 回ずつ存在することを要求します（`bajutsu/orchestrator/types.py:342`）。同じツリーのどこかに識別子のない「Save」がもう 1 つあれば、シートの背後であれ、シートが覆う画面であれ、数は 2 になります。この形は一致しなくなります。まさに本提案が直そうとしているタイムアウトを、その iOS バージョンで再び持ち込みます。

どちらの危険も、本提案が新しく持ち込んだものではありません。単一ラベルでの照合は、画面が繰り返しうるどんな語についても、すでに同じ両方向の危険を抱えています。その照合（`labels: ["Not Now"]`）は、本提案より前から本番で使われていました。2 つのラベルの組は、その危険を広げるのではなく狭めます。

新しいのは、これほど普通の語の組を、保存するか拒否するかという、作者には見通せない決定のために頼ることです。本提案は `systemAlertHandling` の内側に、これへの逃げ道を持ちません。`savePassword` はルールとしてのみ宣言されます。`handleSystemAlert` はそれを拒否します。この経路がツリー内経路だけである以上、SpringBoard のプロンプトにあるような第 2 の宣言手段には頼れません。

`_dismiss_from_tree` のどちらの呼び出しも識別子のないボタンに絞り込むので、残された手立てはアプリ自身のアクセシビリティ表記の 1 つです。衝突する「Save」ボタンに識別子を持たせれば、この機構が調べる平坦な一覧に一致しなくなります。ただし、テスト対象のアプリが作者のものでないときには、作者の制御が及ばない変更という代償を伴います。

それを踏まえたうえで、これは解決した危険としてではなく、境界がわかっている、受け入れた危険として扱います。すでに出荷されている単一ラベルでの照合より悪くはありません。その組が見分けられる場合には、それより狭くなります。危険は、iOS の 1 バージョンの 1 つの形に閉じています。

### 単位 4 — デモを移行し、回帰用のシナリオを追加する

「パスワードを保存」の 2 つのデモは、保存のプロンプトに `labels: ["Not Now"]` ではなくルールで答えます。

`save_password_browser.yaml` はすでに `rules: [{ prompt: notifications, choice: deny }]` を宣言しています。`expect` は、そのルールに依存しています。そのため `savePassword` のエントリは、その一覧を置き換えるのではなく加わります。このデモはさらに、`wait: { until: { gone: { label: "Not Now" … } } }` を落とします。この待機は、ガードを走らせるステップを与えるためだけに存在します。

`save_password_native.yaml` は今日ルールを持たないので、1 件だけの一覧を得ます。このデモは `wait: { for: { id: signin.value, value: "signedIn" } }` を残します。

この待機には、2 つの役割があります。非同期なサインインを同期させることと、ツリー内消去が走る窓であることです。そのシナリオ自身のコメントは実測しています。iOS 18.6 では、この条件より前に「パスワードを保存」のアラートが着地します。26.3、26.4、26.5 では後に着地します。コメントは、`gone` の待機がなぜ代わりに使えないのかも説明しています。

単位 1 が着地したあとも、このシナリオで唯一残るガード付きの step は、この待機です。`handleSystemAlert` はこのシナリオに一度も出てこないからです。`expect` は 1 度しか評価されません。この待機を落とすと、送信との競走に加えて、アラートを片付ける窓まで失うことになります。

本提案が出発点とした事例のために、3 つめのシナリオを追加します。「パスワードを保存」のアラートが画面を占めている位置に、通知プロンプトのための `handleSystemAlert` ステップを置きます。割り込みを覆う `wait` ステップは置きません。補完し合う 2 つと並べて、`ios-e2e` のレーンの `systemalert` タグのシナリオに加えます。

### 単位 5 — ドキュメント

`docs/scenarios.md` とその `docs/ja/` の写しは、3 点が変わります。`labels` のキーと層合成の行を落とします。`savePassword` と、それがルール専用であることを加えます。`handleSystemAlert` ステップが待機中に宣言済みの割り込みへ答えるようになったことを記録します。

両ページの割り込み監視についての説明も書き直します。「方針がボタンを名指ししないプロンプトは XCUITest 自身のデフォルトハンドラへ委ねる」は、それだけでは説明が終わりません。委ねること自体はこれまでどおり起きます。ただし、それに遭遇した step または `expect` は、アラートが提示したボタンを名指しして失敗するようになります。

`docs/architecture.md` とその写しには、待機がドライバから orchestrator へ移ったことも記録します。各層の待機がどこにあるかを説明しているページだからです。

### 今回の範囲外

本提案は、人工知能（AI）によるビジョンのフォールバックのコード経路に手を加えません。`run` にその経路がもうないからです。[BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback-ja.md) が [#1843](https://github.com/bajutsu-e2e/bajutsu/pull/1843) でそれを削除し、すでにマージ済みです。

`AlertGuardConfig` を通るすべての経路は、今では決定的です。`run` は、`visionInstruction` をまだ渡す層を、読むのではなく、はっきり拒否します（`_reject_vision_instruction`）。そのため `labels` がスキーマから消えても、そのフォールバックが今も汲んでいる入力は 1 つも失われません。

BE-0402 は `probe_native` が返す `"unhandled"` にも用途を与えました。本提案は、それを重ねて作るのではなく、積み上げます。その用途が `blocked_note` です。塞がれた step または `wait` 自身の失敗理由に、見えたボタンを添えます。「element not found」という素っ気ない読み方にはしません。

単位 2b は、同じ発想を BE-0402 が届かなかった面に適用したものです。その面とは、**割り込む**アラートです。XCUITest が、割り込んだインタラクションを合成する前に解決してしまうアラートを指します。単に step を**塞ぐ**だけのアラートとは違います。

何にも片付けられないまま step を塞ぐアラートは、その step 自身の条件待機のタイムアウトを通じて、すでに失敗します。`blocked_note` が、止めたものを名指しします。一方、割り込むアラートは XCUITest 自身の解決を完了させ、インタラクションはまるで何も起きなかったかのように進みます。失敗はなく、注記も一切ありませんでした。単位 2b が閉じるのは、この隙間です。2 つの面を同じ基準にそろえます。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| ドライバに poll のコールバックを渡す | `handle_system_alert` に `on_poll` 引数を足し、ドライバの既存のループから orchestrator のガードを毎回呼び戻す | ループは居場所を変えずに済みます。ただし変更が `Driver` インタフェースに乗るので、すべてのバックエンド、fake ドライバ、適合性スイートが一緒に動きます。待機は操作ではなく統制であり、他のあらゆる条件待機はすでに orchestrator が持っています |
| アプリ所有のアラートを SpringBoard の問い合わせから返す | `querySystemAlertButtons` を拡張し、`springboard.alerts` と並べて `app.alerts` も読む | `handleSystemAlert` ステップが `savePassword` を直接名指しできるようになり、単位 3 がそれを禁じる必要もなくなります。ただし、BE-0399 が実測して依拠している境界が消えます。反応的なガードも、ツリー内タップを許可する `probed_absent` の信号を失います。2 つのプロンプトの順序を正しく決めているのは、その信号です |
| 付け替えではなくラベルの照合を削除する | `labels` を削り、ツリー内消去も一緒に落として、ガードはネイティブの SpringBoard 経路だけで動くようにする | 「ボタンではなくプロンプトを宣言する」のもっとも素直な読み方ですが、誤りです。ツリー内消去はアプリ所有のアラートを片付けられる唯一の経路なので、削ると `savePassword` は事実上宣言できなくなり、動機としたタイムアウトも直りません |
| `DEFAULT_DISMISSIVE_LABELS` を控えめな推測として残す | 割り込み監視を、組み込みの語のどれかを提供するアラートに答えさせたままにし、一致しないものすべてを報告して失敗させることはしない | 本提案の最初の草稿はまさにこれを行っていました。辞退そのものが安全でないという誤った理解に基づいていました。実際には安全です。BE-0399、`docs/architecture.md`、`docs/scenarios.md`、そして `RunnerUITest.swift` 自身のコメントが、そろって辞退を安全なフォールバックとして記録しています。辞退が安全だとわかった以上、宣言されていないアラートに答えを推測させることは、native probe とツリー内消去から取り除いたのと同じサイレントな自動判断を、1 つの面にだけ残すことになります。その面は、どちらのやり方でも避けられませんでした |
| 未対応の割り込みを失敗の理由に添えるだけにし、単独では失敗させない | アラートのボタンを、すでに失敗していた step の理由には報告するが、通っていた step を単独で覆しはしない | 下流の何かを壊さなかっただけの、意図しない許可を、代償なしのままにします。これはまさに、本提案全体がシナリオに持たせないよう主張している「成功したように見える」状態です。どのルールも名指ししていないアラートが割り込んだこと自体が、その後の step の検査がどうであれ、シナリオの前提が誤っていた証拠です |
| 形をアラートのボタン集合そのもので照合する | 除外ラベルではなく、形のラベルがアラートのボタン集合と等しいことを要求して、26.5 のアプリ内の保存シートをクレジットカードのアップデートのシートから切り分ける | もっとも素直な切り分けに読めますが、必要な場所では計算できません。`savePassword` のルールを照合するのはツリー内の経路だけです。この経路が見るのは、1 つのアラートのボタンではなく、poll のツリー全体にある識別子のないラベル付きボタンです。ほかにそうしたボタンを画面のどこかに持つアプリでは、集合の一致が必ず外れます |
| 迂回策を文書化する | 割り込みがありうる箇所では `handleSystemAlert` の前に `wait` ステップを置く、と `docs/scenarios.md` に記録する | 実装の費用はかからず、本提案を見送る場合の選択肢としては残ります。ただし、OS のタイミングを作者が予測することを要求し、そのタイミングが不明なときは書けません。書けない状況は、showcase のシナリオのコメントがすでに記述しています |
| ステップ側に宣言を足す | `handleSystemAlert` に、待機中に片付ける割り込みを名指しする専用のフィールドを与える | 作者の意図が、それを必要とするステップの真上に残ります。ただし、1 つのステップ種別のために `systemAlertHandling` を二重化し、独自のスキーマ、ロケール解決、両言語の文書、codegen への対応が要ります |

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

[#1871](https://github.com/bajutsu-e2e/bajutsu/pull/1871) は、単位 1 を実装しました。
`Driver.handle_system_alert` が待機をやめました。XCUITest の実装は、`/systemAlert/query` の 1 回の読み取りに対してステップのセレクタを解決します。`timeout` はシグネチャに残し（呼び出し側はすべて 0 を渡します）、他のバックエンドには手を触れていません。
待機は `bajutsu/common/orchestrator/waits.py` の `wait_for_system_alert` になりました。ステップ自身の期限まで `_POLL` でポーリングします。ステップの対象は 0.2 秒の間隔で読みます。各ポーリングのツリーは、1 つの `_AlertGuardGate` に渡します。
`_run_step_body` は、`kind == "handle_system_alert"` のときに、`wait` と並んでシナリオのガードを与えて実行します。アクションハンドラの登録はガードなしのまま残し、`record` のリプレイが使います。
`probe_native` は `"reserved"` という答えを持つようになりました。待機中のステップのセレクタが名指しするアラートを、ガードが辞退します。これが、同じプロンプトに 2 者が反対の答えを出すことを止めます。
タイムアウトは、アラートが何も出なかった場合と、出たもののセレクタがボタンに一致しなかった場合とを区別します。何も片付けられなかったプロンプトについては、ガード自身の注記も運びます。
`docs/scenarios.md` と `docs/architecture.md`、および両方の `docs/ja/` の写しに、待機の移動を記録しました。単位 5 の残りは、後続の単位を待ちます。
本文中のパスは、この提案が書かれた当時の `bajutsu/…` ではなく、再編後の `bajutsu/common/…` です。
単位の本文からの逸脱が 1 つあります。ステップ終端のアラートガードは、失敗した `handleSystemAlert` ステップに対して「変わらない」のではなく、丸ごとスキップするようにしました。`wait_for_system_alert` は、ステップ自身のセレクタで予約した同じガードを、ステップの期限いっぱいすでに走らせています。予約なしでもう一度 probe しても、カバレッジは増えません。むしろ、ガードの緩いフォールバック方針が、ステップ自身のアラートをタップしてしまう恐れがありました。それではプロンプトをステップの代わりに決めてしまいます。しかも画面が片付いたあとの再試行が生む一般的な理由で、この単位が用意した具体的な失敗理由を上書きしてしまいます。

[#1894](https://github.com/bajutsu-e2e/bajutsu/pull/1894) は、単位 2a と単位 3 をまとめて実装しました。2a がツリー内経路に付け替える照合は、単位 3 が面の記録とツリー内で扱える唯一のプロンプトを用意するまで、武装できる rule を 1 つも持ちません。分けて出せば、その間だけ「パスワードを保存」のデモが自分のアラートを消せなくなります。
`labels`、`--alert-labels`、`alertLabels`、`pick_alert_label` を削除しました。`probe_native` は `rules` だけを照合し、2 つのツリー内消去は新しい `AlertGuardConfig.tree_rules` で武装します。
`_LABELS` は、プロンプトと言語を、除外ラベルの集合を持ちうる形のリストへ対応づけます。`_SURFACES` は、3 つの応答面のどれに届くかをプロンプトごとに記録します。`savePassword` のラベルは、iOS 18.6 と 26.5 の両ランタイムの `WebUI.framework` から転記しました。
`handleSystemAlert` は、`step` の面を持たないプロンプトを拒否します。`push_interruption_policy` は、その面が決して出会えない rule を落とします。subset 照合が黙って捨ててしまう除外集合を持つ rule は、明示的に失敗させます。
デモ 4 本を `rules` へ移しました。これは単位 4 の前倒しではなく、`labels` の削除がそのまま強制した結果です。
単位の本文からの逸脱が 3 つあります。1 つめは、`_warn_target_rules_reach` が `inner_layers` 引数を失ったことです。フラグが rule を運べなくなり、ターゲットより内側の層はシナリオだけになったからです。2 つめは、`push_interruption_policy` が組み込みの dismiss ラベル群を無条件に push するようになったことです。`labels` でそれを絞っていたシナリオがランナーへ送る範囲は、単位 2b がこの一覧を消すまで広がります。3 つめは、著者の依頼による、この項目の設計にはない変更です。ガードが 1 回限りの dismiss を行う 2 か所で、再試行の前に現れた画面が動かなくなるまで待つようにしました。上限付きでベストエフォートの条件待ちなので、アニメーション途中のツリーを相手にステップが失敗することはなくなります。

[#1903](https://github.com/bajutsu-e2e/bajutsu/pull/1903) は、単位 2b と、単位 5 の残りを実装しました。
**Python 側。** `bajutsu/common/orchestrator/types.py` から `DEFAULT_DISMISSIVE_LABELS` を削除しました。`push_interruption_policy` は、削除した候補一覧の代わりに `governs` を送ります。ガードが有効なシナリオでは、ツリー内専用の drop で rule が 1 つも残らなくても true になります。`Driver.set_interruption_policy` は、`candidates` の代わりに `governs` を受け取ります。`Driver.drain_interruptions` は `DrainedInterruptions(tapped, declined)` の組を返すようになりました。orchestrator 側のラッパーは、`declined` を `AlertEvent` ではなく `UndeclaredInterruption` の記録へ変えます。
**drain 箇所。** `declined` が空でなくなった drain 箇所は、どこも無条件に失敗するようになりました。対象は 4 か所です。ステップの drain。`expect` フェーズ自身の drain。再試行のあとの 2 度目の drain。未対応の locale で `handleSystemAlert` に遭遇した際に `_handle_action` が取る早期リターンです。`expect` フェーズ自身の drain は、ガードの probe が何かを片付けたかどうかに関わらず対象にするよう、レビューで見つかった漏れを塞ぎました。再試行のあとの 2 度目の drain は、コメントがすでに欠落として指摘していたものです。早期リターンについては、actuation の同じ取りこぼしを、すでに自分のコメントが指摘していました。
この失敗はどれも、ステップや `expect` がすでに持っている理由へ `undeclared_interruption_note` を追記するのであって、置き換えるのではありません。ドレインした記録もすべて名指しし、最初の 1 件だけにはしません。
**Swift 側。** `InterruptionPolicy` から `candidates` と `isEmpty` を削り、`governs` を加えました。`InterruptionPolicyStore` は、監視が辞退する前に追記する 2 本目のドレインリストを持ちます。ただし、アラートのボタン一覧のラベルがすべて空文字列のときは追記しません。これは、数行下で `button.exists` がすでにガードしている、アラートが自分で閉じてしまう競走と同じ徴候です。`openapi.yaml`、生成される `APIHandler`、レガシーの `Router` は、いずれも新しい `governs` と `unmatched` の配線形状を運びます。レガシー経路もいまは、`governs` が欠けているか型違いのときに `false` へ既定せず、そのまま拒否します。
**3 巡目のレビュー。** `if`、`forEach`、`web` の各ステップハンドラが、drain を取りこぼす 3 件目のパターンだと見つかりました。どれもネストしたステップへ入る前にドライバへ問い合わせており、`_handle_action` 自身の早期リターンと同じ形です。そこで 4 か所とも、1 つの `_drain_step_interruptions` ヘルパーを共有するようにしました。同じ巡では、空ラベルによる競走のガードも見直しました。`buttons.count` が 0 でなくても、個々の `.label` はすでに空文字列に解決していることがあるからです。一覧そのものが空かどうかではなく、中身をすべて点検するようにしました。
**diff を読み返すだけでは見つからなかった設計上の欠落。** `handleSystemAlert` ステップ自身が使うプロンプトは、シナリオの `systemAlertHandling.rules` に含まれるとは限りません。宣言しない選択を認めるために、ステップという形があるからです。そのため、先行する操作の割り込みが同じプロンプトと先に出会い、一致する規則がなく、このステップが答えようとしていたアラートのせいで無関係なステップが失敗することがありえました。`_reserve_declared_alert` がこれを閉じます。`prompt` と `choice` 形のステップが待っている間だけ、シナリオ自身の規則に加えて、そのステップの対象を 1 つ追加で送ります。ステップが答え終わる、失敗する、あるいは待機自体が例外で終わるいずれの場合も、元へ戻します。これは、この項目自身が動機として挙げているシナリオそのものです。ただし `sel` 形のステップには予約すべき識別ラベルの集合がないため、今日の振る舞いのままです。
**検証。** セルフレビュー（BE-0347 の二役の作法、3 巡。レビュー自身の上限です）が、ここまでの内容をすべて見つけて直しました。`swift build` と `swift test --filter BajutsuRunnerTests`（172 件。`InterruptionPolicy.label(for:)` への直接のテストを新たに含みます）が通ります。Python 側のテスト一式（7,177 件）もすべて通ります。
**ドキュメント。** `docs/architecture.md` と `docs/scenarios.md`（両方の `docs/ja/` の写しを含む）から、割り込み監視の説明にあった組み込みの dismiss 候補一覧が消えました。方針が有効な状態での辞退は、それに遭遇したステップまたは `expect` を失敗させると記録しています。予約の仕組みも記録しています。単位 5 に残っていたのはこの作業だけだったので、単位 5 も完了します。

[#1908](https://github.com/bajutsu-e2e/bajutsu/pull/1908) は、最後の単位 4 を実装しました。
`save_password_browser.yaml` から `wait: { until: { gone: { label: "Not Now" } } }` を削除しました。反応的なガードにステップを 1 つ与えるためだけに存在していた迂回策です。続く `wait: { for: { id: Close } }` が同じ窓を与えます。アラートがブラウザの上にモーダルで出ている間は、この条件が偽のままだからです。
この項目の動機となった回帰シナリオ `save_password_interrupts_step.yaml` も追加しました。割り込みを覆う `wait` ステップを置かずに、通知プロンプト向けの `handleSystemAlert` ステップを置きます。パスワード保存アラートが、画面を単独で占有している状況を作ります。
単位の原文からの逸脱が 1 つあります。新シナリオが加わる先を、`ios-e2e` の CI レーンではなく、`make -C demos/showcase e2e-savepassword` にしたことです。「パスワードを保存」の 2 つのデモは、そもそもそのレーンに乗ったことがありません。レーンは明示的なパスでシナリオを選び、ブラウザのフィクスチャも用意しません。原文のレーンに関する記述は、この単位が着地する時点で古びていました。この `make` ターゲットは、いまや 2 本の「パスワードを保存」シナリオを、それぞれ端末を消去したうえで順に走らせます。
加えて、新シナリオの追加が明らかにした `tests/test_showcase_fixtures.py` の除外タグ保証の抜けも埋めました。`browser.yaml`、`tabs.yaml`、2 本の「パスワードを保存」デモが、もともと漏れていました。
自己レビュー（BE-0347 の二役の手順）では、ステップ前のページ読み込みと入力に対してタイミングの余裕が狭すぎる点と、古びたコメント 3 箇所を見つけて直しました。その後、実機の iOS Simulator で再検証し、消去し直した端末に対して 2 本の「パスワードを保存」シナリオがどちらも通ることを確認しました。`make check`（7,214 件のテスト）も通り、この項目を完了させます。

## 参考

- [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)：反応的なガードの待機中の介入と、iOS の「パスワードを保存」のプロンプトが待機を止める最初の記録。
- [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)：ガードが優先する決定的なネイティブ経路と、`system_alert_labels` による在否の問い合わせ。
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)：`handleSystemAlert` ステップと、本提案が引き取る「許可プロンプトだけでなくすべてのアラートを覆う」の見送り。
- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)：プロンプトと選択のラベル表と、その値を推測せず転記するという基準。
- [BE-0369](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice-ja.md)：その表に前回加えられたプロンプトと、本提案が従う先例。
- [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md)：唯一の宣言手段になる、プロンプトごとの宣言である `rules`。
- [BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy-ja.md)：どのプロンプトがどこに出るか、その間の順序、そしてツリー内消去に付けられた条件。
- [BE-0401](../BE-0401-system-alert-handling-dsl-consolidation/BE-0401-system-alert-handling-dsl-consolidation-ja.md)：`labels` を導入した経路ごとのキーの整理と、別名を置かずにキーを削除する先例。
- [BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback-ja.md)：`run` の AI ビジョンのフォールバックを削除し、`blocked_note` を加えた、すでにマージ済みの項目。塞ぐ面の先例であり、単位 2b はその先例に割り込む面をそろえます。
