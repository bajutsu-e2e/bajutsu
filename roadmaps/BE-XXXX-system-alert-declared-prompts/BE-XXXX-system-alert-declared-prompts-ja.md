[English](BE-XXXX-system-alert-declared-prompts.md) · **日本語**

# BE-XXXX — システムアラートの宣言をプロンプト名だけにし、handleSystemAlert の待機中にも答える

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-system-alert-declared-prompts-ja.md) |
| 提案者 | [@akiramatsuda](https://github.com/akiramatsuda) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
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

組み込みのデフォルト値が 1 つだけ生き残ります。上の主張に条件を付けるものなので、先に述べておきます。
XCUITest は、自分の操作に割り込んだアラートを、その操作を合成する前に解決します。そして答えるのを辞退した側は、アラートをそのまま放置しません。XCUITest 自身のデフォルトハンドラが引き取り、その**デフォルト**
ボタンをタップします。これは許可する側です。この 1 箇所に限っては、答えるか答えないかの選択が存在しません。
そのため拒否寄りのラベル一覧は、runner の割り込み監視へ push する方針として残します。ガード自身が
動く場所では、シナリオが宣言したものだけに動きます。

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

本提案が届いたかどうかは、あとから読む人にも判別できます。「パスワードを保存」のアラートが画面を占めて
いるところへ、通知プロンプトのための `handleSystemAlert` ステップを置く showcase のシナリオを走らせて
ください。そのシナリオは今日、上に引いたタイムアウトで失敗します。変更後は、割り込みを覆う `wait`
ステップを 1 つも置かずに通ります。移行した 2 つのデモが、2 つめの観測可能な差です。どちらも保存のプロンプトに、
`labels: ["Not Now"]` ではなく `savePassword` のルールで答えます。`save_password_browser.yaml` は、
迂回策として持っている `gone` の待機も落とします。

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

ステップが poll する間隔は、待機機構の `_POLL` ではなくガード自身の `pollInterval` にします。1 回の poll
は 3 つの読み取りでプロセス境界を越えます。1 番目の SpringBoard への問い合わせ、`gate.observe` が消費する
ツリーの問い合わせ、そしてゲート自身の SpringBoard の probe です。SpringBoard への問い合わせを `_POLL` ごとに行うと、runner の単一の
メインスレッドにかかる負荷がおよそ倍になると、BE-0315 がすでに記録しています。今日のドライバ側の
ループは 0.2 秒で poll しているので、ガードの標準である 1 秒のほうが遅い側です。従来の間隔が必要な
シナリオは、`pollInterval` を下げます。

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

### 単位 2 — アラートをプロンプトで宣言し、ラベルを照合していた経路を付け替える

`labels` を [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py) の
`SystemAlertHandling` から削除します。`run` からは `--alert-labels` オプションを削除します。
`_flag_alert_policy` に残すのは、`--alert-vision-instruction` と `--alert-poll-interval` だけです。
このオプションは [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) にあります。代わりのフラグは設けません。
ルールはプロンプトと選択の組であり、それを読みやすく運べるフラグはないと BE-0401 がすでに記録して
います。プロンプトごとの宣言は、シナリオファイルとターゲット設定の宣言のままにします。
`AlertGuardConfig` は `labels` フィールドを失います。BE-0401 が確立した層合成の表は、行を 1 つ失います。
`rules` は内側の層から順に連結されるリストのキーのままです。`visionInstruction` と `pollInterval` は、
値を与えるもっとも内側の層が勝つスカラーのままです。有効と無効の真偽値も変わりません。

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
この 3 つが `pick_alert_label` の唯一の呼び出し元なので、この関数は `labels` とともに削除します。
Swift 側は、割り込み監視のために順序付き候補の照合を自前で持ち続けます。それを今も必要とする面は、
そこだけです。

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
これらのルールを push しなければ、監視が見るルールに除外の集合は 1 つも載らず、`BajutsuKit` は変更
不要のままです。これは偶然ではなく、構成として成り立たせます。除外の集合を持ち、なおかつツリー内だけの
ものではないルールを、push は拒否します。将来 SpringBoard に届く形が除外を必要としたとき、除外を黙って
落としたまま監視へ届くのではなく、はっきり失敗します。落ちた先にあるのは、1 つ表を足したあとの部分集合の
衝突だからです。

`DEFAULT_DISMISSIVE_LABELS` は、`set_interruption_policy` の `candidates` に限って生き残ります。理由は、
この場所では辞退が中立の行為ではないことです。XCUITest は、自分の操作に割り込んだアラートを、その操作を
合成する前に解決します。辞退した側は、アラートを XCUITest 自身のデフォルトハンドラへ返します。そのハンドラは、アラートの
**デフォルト**ボタンをタップします。シナリオが一度も言及していない権限を、
報告へ `AlertEvent` を残さないまま許可します。この沈黙こそ BE-0399 が終わらせたものです。
`save_password_browser.yaml` の `expect` も、名指しで検査しています。したがって空の候補一覧が意味するのは「何も答えない」ではありません。「XCUITest に許可させる」です。そこで拒否寄りのデフォルト値を、どのルールも同定しない
アラートに対する答えとして残します。ガード自身の 2 つの面、すなわちネイティブの probe とツリー内消去は、
宣言されたプロンプトだけに答えます。割り込み監視がすべてに答えるのは、第 3 の選択肢を持たないからです。

`labels` を読む場所は、前の段落が片付けた `push_interruption_policy` の候補のフォールバックを除くと、
あと 2 つです。どちらも照合の場所ではありません。
[`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) の `_vision_instruction` は、ラベルを
与えたもっとも内側の層から、ビジョンのフォールバックへの指示を組み立てます。
`_warn_target_rules_reach` は `layer.labels or layer.vision_instruction` を読み、シナリオが自分で答えて
いるかどうかを判定します。`_vision_instruction` からはラベルの引数を落とし、判定は
`layer.rules or layer.vision_instruction` へ付け替えます。これでビジョンのフォールバック自身の挙動は
変わりません。ルールだけのシナリオでは locator がもっとも破壊の少ないデフォルトのままになると、その関数の
docstring がすでに記録しているからです。ルールのタップラベルは構成上ほかのプロンプトの
答えなので、フォールバックを誘導してはならない、という理由です。`labels` を削ると、すべてのシナリオが
ルールだけのシナリオになります。その関数がすでに通っている経路です。

`_resolve_rules` は、覆っていない言語に対して `UncoveredSystemAlertLocale` を送出し、その救済として
`labels` を案内しています。このメッセージを、残る 2 つの救済を名指しする形に書き直します。言語を
[`bajutsu/scenario/system_alerts.py`](../../bajutsu/scenario/system_alerts.py) に加えるか、表が覆う
ロケールを固定するかです。

Swift 側は `InterruptionPolicy.candidates` を残します。割り込み監視が、まさにそれを必要とする消費者
だからです。`Driver.set_interruption_policy` も引数を 2 つとも残します。この単位で `BajutsuKit` は
変わりません。

`labels` を書いたままのシナリオは読み込みに失敗し、`rules` を名指しするエラーになります。BE-0401 は
`enabled` と 2 つの非推奨の綴りを同じやり方で削除し、後方互換を保たないことを明言しました。本提案は
別名を持ち回るのではなく、その先例に従います。

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
ラベルが成り立つところなら成り立ちます。しかも外れたときの結果は、誤ったタップではなく不一致です。

### 単位 4 — デモを移行し、回帰用のシナリオを追加する

「パスワードを保存」の 2 つのデモは、保存のプロンプトに `labels: ["Not Now"]` ではなくルールで答えます。
`save_password_browser.yaml` はすでに `rules: [{ prompt: notifications, choice: deny }]` を宣言して
います。`expect` は、そのルールに依存しています。そのため `savePassword` のエントリは、その一覧を置き換えるのではなく
加わります。`save_password_native.yaml` は今日ルールを持たないので、1 件だけの一覧を得ます。
`save_password_browser.yaml` はさらに、`wait: { until: { gone: { label: "Not Now" … } } }` を落とします。
この待機は、ガードを走らせるステップを与えるためだけに存在します。`save_password_native.yaml` は
`wait: { for: { id: signin.value, value: "signedIn" } }` を残します。この待機はアラートの窓ではなく、
非同期なサインインに対するシナリオ自身の同期であり、`expect` は 1 度しか評価されないので、落とすと
シナリオが送信と競走することになります。

本提案が出発点とした事例のために、3 つめのシナリオを追加します。「パスワードを保存」のアラートが画面を
占めている位置に、通知プロンプトのための `handleSystemAlert` ステップを、割り込みを覆う `wait` ステップ
なしで置きます。補完し合う 2 つと並べて、`ios-e2e` のレーンの `systemalert` タグのシナリオに加えます。

### 単位 5 — ドキュメント

`docs/scenarios.md` とその `docs/ja/` の写しから、`labels` のキーと層合成の行を落とします。
`savePassword` と、それがルール専用であることを加えます。そして `handleSystemAlert` ステップが待機中に
宣言済みの割り込みへ答えるようになったことを記録します。`docs/architecture.md` とその写しには、待機がドライバから orchestrator へ移った
ことを記録します。各層の待機がどこにあるかを説明しているページだからです。

### 今回の範囲外

本提案は、人工知能（AI）によるビジョンのフォールバックのコード経路に手を加えません。ただし入力を 1 つ
だけ狭めます。`labels` がなくなると、`_vision_instruction` が組み立てるラベル由来のヒントを、どのシナリオも
供給できなくなります。そのためすべてのシナリオが、その関数のすでに扱うルールだけの場合になります。
locator はもっとも破壊の少ないデフォルトに置かれたままです。誘導する手段として残るのは
`visionInstruction` だけです。`run` からそのフォールバックそのものを削除するのは
[BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback-ja.md)
の主題です。削除はすでに決まっており、別の変更として作業が進んでいます。

2 つの提案は、どのルールも名指ししないアラートに対して `probe_native` が返す `"unhandled"` で接します。
本提案は、その返り値に到達するものを絞ります。ガード自身の解決から `labels` の照合と組み込みの
デフォルト値を取り除くからです。BE-0402 は、その返り値のあとに起きることを取り除きます。どちらの項目も、
相手の順序に依存しません。先に着地したほうも、それ自体で成り立ちます。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| ドライバに poll のコールバックを渡す | `handle_system_alert` に `on_poll` 引数を足し、ドライバの既存のループから orchestrator のガードを毎回呼び戻す | ループは居場所を変えずに済む。ただし変更が `Driver` インタフェースに乗るので、すべてのバックエンド、fake ドライバ、適合性スイートが一緒に動く。待機は操作ではなく統制であり、他のあらゆる条件待機はすでに orchestrator が持っている |
| アプリ所有のアラートを SpringBoard の問い合わせから返す | `querySystemAlertButtons` を拡張し、`springboard.alerts` と並べて `app.alerts` も読む | `handleSystemAlert` ステップが `savePassword` を直接名指しできるようになり、単位 3 がそれを禁じる必要もなくなる。ただし BE-0399 が実測して依拠している境界が消える。反応的なガードも、ツリー内タップを許可する `probed_absent` の信号を失う。2 つのプロンプトの順序を正しく決めているのは、その信号である |
| 付け替えではなくラベルの照合を削除する | `labels` を削り、ツリー内消去も一緒に落として、ガードはネイティブの SpringBoard 経路だけで動くようにする | 「ボタンではなくプロンプトを宣言する」のもっとも素直な読み方であり、そして誤りである。ツリー内消去はアプリ所有のアラートを片付けられる唯一の経路なので、削ると `savePassword` は事実上宣言できなくなり、動機としたタイムアウトも直らない |
| `labels` と一緒に `DEFAULT_DISMISSIVE_LABELS` も落とす | 空の候補一覧を割り込みの monitor へ push し、宣言されていないものには一切答えない | もっとも厳格に読めて、もっとも安全でない。空の方針は monitor を辞退させ、XCUITest のデフォルトハンドラがそのアラートを許可し、報告には何も残らない。BE-0399 が終わらせた沈黙である。この場所に「何も答えない」という選択肢は存在しない |
| 形をアラートのボタン集合そのもので照合する | 除外ラベルではなく、形のラベルがアラートのボタン集合と等しいことを要求して、26.5 のアプリ内の保存シートをクレジットカードのアップデートのシートから切り分ける | もっとも素直な切り分けに読めて、必要な場所では計算できない。`savePassword` のルールを照合するのはツリー内の経路だけであり、この経路が見るのは 1 つのアラートのボタンではなく poll のツリー全体にある識別子のないラベル付きボタンである。ほかにそうしたボタンを画面のどこかに持つアプリでは、集合の一致が必ず外れる |
| 迂回策を文書化する | 割り込みがありうる箇所では `handleSystemAlert` の前に `wait` ステップを置く、と `docs/scenarios.md` に記録する | 実装の費用はかからず、本提案を見送る場合の選択肢としては残る。ただし OS のタイミングを作者が予測することを要求し、そのタイミングが不明なときは書けない。書けない状況は、showcase のシナリオのコメントがすでに記述している |
| ステップ側に宣言を足す | `handleSystemAlert` に、待機中に片付ける割り込みを名指しする専用のフィールドを与える | 作者の意図が、それを必要とするステップの真上に残る。ただし 1 つのステップ種別のために `systemAlertHandling` を二重化し、独自のスキーマ、ロケール解決、両言語の文書、codegen への対応が要る |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 単位 1 — `handleSystemAlert` の待機を orchestrator へ移す。ステップにつき 1 つのゲートを
      ガードの `pollInterval` で駆動する。画面を占めていたアラートを名指しする理由で失敗させる。
- [ ] 単位 2 — `labels` と `--alert-labels` を削除し、ネイティブの probe と 2 つのツリー内消去を
      `rules` へ付け替える。`DEFAULT_DISMISSIVE_LABELS` は割り込み監視に限って残す。`labels` を
      書いたままのシナリオは拒否する。
- [ ] 単位 3 — プロンプトの言語エントリを、除外ラベルの集合を持ちうる形のリストに対応づける。3 つの形と
      プロンプトごとの面の記録とともに `savePassword` を加え、`handleSystemAlert` ステップでは拒否する。
- [ ] 単位 4 — 「パスワードを保存」の 2 つのデモを `rules` へ移し、ブラウザ側のデモから迂回策の待機を
      落とし、回帰用のシナリオを `ios-e2e` のレーンに加える。
- [ ] 単位 5 — `docs/scenarios.md`、`docs/architecture.md`、および両方の `docs/ja/` の写しを更新する。

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
  — 本提案が残すビジョンのフォールバックを削除する提案。
