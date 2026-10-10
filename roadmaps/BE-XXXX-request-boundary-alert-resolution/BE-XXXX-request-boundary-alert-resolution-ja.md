[English](BE-XXXX-request-boundary-alert-resolution.md) · **日本語**

# BE-XXXX — システムアラートを Python から見張らず、runner のリクエストの境目で片付ける

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-request-boundary-alert-resolution-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform support |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の反応型システムアラートガードは、[シナリオ](../../docs/ja/glossary.md#シナリオのオーサリング)
を止めるプロンプトを片付けます。通知の許可要求や、iOS の「パスワードを保存」シートが対象です。現在、
そのプロンプトを見張っているのは Python のオーケストレータで、デバイスの外から見張っています。待機の
たびに、一定の間隔で SpringBoard（iOS のシステムシェル）へ問い合わせます。さらに 50 ms ごとにアクセ
シビリティツリーを調べ、その両方のまわりにラッチと注記の状態機械を持っています。

本提案は、リクエストが画面を必要とした時点で、XCUITest の runner の中でアラートを片付けます。runner は、
Simulator との接続を保持している常駐の Swift プロセスです。オーケストレータが要素ツリーを求めると、
runner はまず方針が名指しするアラートに答え、そのあとでスナップショットを取ります。応答には、ツリーと
答えた結果の記録が載ります。これで Python のアラートゲートは、801 行の検知器から 50 行ほどの読み取り役
に縮みます。

## 動機

ガードの検知ロジックは、待機ループのなかで最大の塊に育っています。`_AlertGuardGate`
（`bajutsu/common/orchestrator/waits/_alert_guard_gate.py`）は 801 行あります。重なる問いに答える 3 つ
の検知器が、そこで絡み合っています。

- **ネイティブの SpringBoard プローブ**：`poll_interval`（デフォルトは 1 秒）ごとに間引かれます。
- **ツリー内の消去**：アプリ自身が出すアラート向けです。50 ms ごとのポーリングで走りますが、直前の
  プローブが SpringBoard のアラートなしと答えたポーリングに限ります（BE-0399）。
- **崩れたツリーのプロキシ**：毎ポーリング標本を取り、画面が塞がれている理由をあとから説明します。

3 つの検知器はそれぞれラッチと注記を持ち、他の検知器が作る競合に合わせて調整されてきました。BE-0418 の
リトライのような変更は、3 つすべてに照らして考えなければなりません。

この構造は、検知がどこで走るかから来ています。XCUITest には「アラートが出た」ことを知らせるコール
バックがありません。唯一の入口は、BE-0399 が runner に入れた割り込み監視
（`BajutsuKit/Runner/Sources/RunnerUITest.swift`）です。XCUITest が割り込み監視を呼ぶのは、操作を評価
するときと、アプリに対する一部の問い合わせのときです。アプリ自身のプロセスに出るアラートは、割り込み
監視に届きません。そのためオーケストレータは、Hypertext Transfer Protocol（HTTP）の往復をはさんで外
から問い合わせています。runner のメインスレッドは 1 本で、この制約も問い合わせの頻度を低く保たせました
（BE-0315）。プロセスの境界は、1 つの判断を 2 つの言語に分けてもいます。SpringBoard のボタン
は runner が渡された方針で押し、アプリ側のボタンは Python が同じ方針を解決し直してタップします。

アラートが実行にとって意味を持つのは、次のリクエストが画面を見るときです。独立した別の時計で動くウォ
ッチャーは、まだ誰も見ていないアラートへ答えることになります。そこで本提案は、オーケストレータが画面
を読むその場所でアラートを片付けます。runner はリクエストの中で処理を行い、その順序はオーケストレー
タがすでに送っているリクエストの順序に従います。2 つのリクエストのあいだに記録が生まれることはありま
せん。記録はどれもリクエストの中で生まれ、応答に載らなかった分はステップ終了時の drain が集めます。
そのため、予約の状態、方針の世代、記録を消費せずに覗くための仕組みは要りません。SpringBoard のアラー
トの有無を伝えるフラグも、drain の順序の変更も要りません。

シナリオは変わらず、言語モデルの呼び出しが実行に入ることもありません（基本原則 1）。

変更が届いたことは、あとから次の 3 点で確かめられます。

- `handleSystemAlert` ステップの外では、ガードが効いている待機が `/systemAlert/query` を送らず、送る
  `/elements` にはすべて `resolveAlerts=true` が付きます。runner のリクエストログで、その両方を確認
  できます。
- ゲートのファイルから、ネイティブのプローブとツリー内の消去がなくなっています。
- 通知の許可要求と「パスワードを保存」シートに続けて答える showcase のシナリオが、iOS 18.6、26.3、
  26.4、26.5 で成功し続けます。この 4 つは BE-0399 が実測したバージョンです。

## 詳細設計

作業は順に進めます。まず測り、次に runner 側を作り、そのあとで Python 側を作ります。旧来の経路は実機
での検証のあとで削除し、文書の更新もそれと同じ変更で行います。runner の変更はすべて、XCUITest の
[backend](../../docs/ja/glossary.md#driver-backend-actuator-platform) が通知する capability トークンの
内側に置きます。adb と Playwright の backend は今のゲートを使い続けるので、基本原則 3 は保たれます。

### Unit 1：作る前に測る（関門）

Unit 1 はプロダクトのコードを足しません。使い捨ての runner のビルドで、iOS 18.6、26.3、26.4、26.5 の
それぞれについて次の 4 点を測ります。

1. **`app.snapshot()` が割り込み監視を呼ぶかどうか**：`GET /elements` は毎回 `app.snapshot()` を呼び
   ます。スナップショットが割り込み監視を呼ぶなら、Unit 2 の SpringBoard の確認は不要になり、ツリー内
   のルールを当てる処理だけが残ります。
2. **SpringBoard の確認にかかる時間**：`springboard.alerts.firstMatch.exists` を 1 回呼ぶ時間を測り
   ます。50 パーセンタイル値（p50）と 95 パーセンタイル値（p95）を記録します。この値で確認の間引き間隔を決めます。
3. **ボタンを押すときの `/elements` の所要時間**：BE-0399 の活動ログでは、1 回の押下に約 1.6 秒かかって
   います。観測した最長の `/elements` を、15 秒の読み取りの時間枠
   （`bajutsu/common/drivers/xcuitest/_functions.py`）と比べます。
4. **ステップあたりのコスト**：バージョンごとに同じホストで、showcase のスイートを基準の runner で 10 回、
   候補で 10 回走らせます。候補のステップ所要時間の中央値が基準の 5％以内で、p95 が 10％以内であれば
   合格とします。runner を落とした実行が 1 回でもあれば不合格です。

4 点目のしきい値は、レビューのための出発点です。測定値とあわせて本項目に記録します。

### Unit 2：アラートを片付ける `/elements`

`GET /elements` に、クエリパラメータ `resolveAlerts` を加えます。値は `false`（デフォル
ト）、`true`、`inTree` のいずれかです。フラグのない読み取りは、純粋な読み取りのままです。`true` のと
き、ハンドラは runner の直列の操作キュー `APIHandler.operations` の上で、次の 4 つの手順を踏みます。

1. **SpringBoard を確認します**：`springboard.alerts` の確認は、`pollIntervalSeconds` に 1 回までに
   間引きます。この間隔は、オーケストレータが `systemAlertHandling.pollInterval`（デフォルトは 1
   秒）から渡します。アラートが出ていて `InterruptionPolicy.label(for:)` がボタンを選べば、ハンドラ
   はそれをタップして *answered*（応答済み）を記録します。どのルールもボタンを選ばなければ、何もタッ
   プせず *unidentified*（未識別）を記録します。同じ表示に 2 回タップすることも、2 回記録することも
   ありません。表示のキーは、一致したルールの識別ラベルです（*unidentified* のときはボタンの組で
   す）。のちの確認でアラートが見つからなくなったときに限り、次の表示に備えます。
2. **アプリのスナップショットを取ります**：フラグのない `/elements` と同じ処理です。
3. **ツリー内のルールを当てます**：渡されたツリー内のルールは、識別ラベルがスナップショットのボタン
   にちょうど 1 回ずつ現れ、除外ラベルが 1 つも現れないときに一致します。ハンドラはルールを
   `AlertGuardConfig.tree_dedup_rules` の順に試すので、入れ子の形では広いほうの兄弟が先に来ます。一
   致すれば同じハンドラの中で SpringBoard を確認し直し、SpringBoard のアラートが出ていないときに限っ
   てタップして記録します。出ていれば、ツリー内のアラートはあとのリクエストに任せます。そのあとスナ
   ップショットを取り直します。
4. **応答を返します**：応答には、ツリーと、このリクエストで drain した記録が載ります。`/tap` もすで
   に同じ形で、drain した記録を応答に載せています（BE-0407 の Unit 6、`APIHandler.swift`）。応答に
   は、何も起きなかったときも空の記録の欄を必ず載せます。`resolveAlerts` を付けた読み取りの応答にこ
   の欄がなければ、ドライバは `XcuitestChannelError` を送出します。古い runner のビルドが黙って普通
   のツリーを返すことはありません。

手順 3 の確認し直しは、今のゲートが守っている順序を保ちます。SpringBoard のアラートが出ている最中に
XCUITest でタップすると、割り込み監視がそのアラートへ先に答えてしまうからです。

ツリー内のタップは、今の間合いを保ちます。間合いを決める値は次の 3 つで、`InterruptionPolicyStore`
の中のルールごとの小さな控えが持ちます。

- 再タップまでの遅延です。
- 表示 1 回あたりのタップ回数の上限です。
- 諦めるまでの上限です。

これらの定数は、`waits/_alert_guard_gate.py` と `waits/_functions.py` から移します。移し先は
`bajutsu/common/orchestrator/types/alert_guard_config.py` です。runner へは、オーケストレータが
渡します。
諦めるまでの上限は、引き続き Python 側で `poll_interval` から導きます。諦めたときは、ルールの識別ラベル
を添えて *gave up*（断念）を記録します。

通信形式には、ルールごとに省略可能な `exclude` の一覧と、ルールの種類（`ResolvedAlertRule` 由来の
`native` か `inTree`）を加えます。
`push_interruption_policy`（`bajutsu/common/orchestrator/types/_functions.py`）は、ツリー内のル
ールを渡す前に落とすのをやめます。割り込み監視と手順 1 の SpringBoard の確認
は、`InterruptionPolicy.label(for:)` で `native` のルールだけを照合します。手順 3 のツリー内の照合は
`inTree` のルールだけを使い、`exclude` を守ります。除外の組を持つルールを拒む処理は、ネイティブに届
くルールに対しては残します。そうしたルールは割り込み監視の部分一致に届き、そこで除外が捨てられてしま
うからです。前面に出る通知バナーは、BE-0416 のスワイプの経路のままです。

`resolveAlerts` を付けた読み取りはボタンを押すことがあるので、冪等ではありません。BE-0207 では、配信
後に応答が時間切れになった読み取りを、ドライバが送り直せます。`resolveAlerts` を付けた読み取りは、ド
ライバが書き込みと同じに扱います。配信されなかったリクエストは送り直し、配信後の時間切れは送り直さず
に失敗として報告します。変更先は
`_is_retry_eligible`（`bajutsu/common/drivers/xcuitest/_functions.py`）です。この関数は今、`GET` を
すべて冪等とみなしています。BE-0287 のクラッシュ復旧での再送も同じ関数で判定するので、1 か所の変更で
両方に効きます。

### Unit 3：`POST /systemAlert/resolve` エンドポイント

`POST /systemAlert/resolve` は、表示中の SpringBoard のアラートに、渡された方針を当てます。応答には、
その結果の記録が載ります。`handleSystemAlert` ステップは、自分のもの以外のアラートへ答えるときにこの
エンドポイントを使います。

ステップは自分の `/systemAlert/query` のポーリングを続け、このポーリングは何も片付けません。ステップ
の `/elements` のポーリングは、`resolveAlerts=inTree` で送ります。このモードは Unit 2 の手順 2 から
4 を行い、手順 1 の SpringBoard の確認を省きます。そのため、「パスワードを保存」シートのようなアプリ
側のアラートは、ステップの待機中も片付きます（BE-0406）。手順 3 で SpringBoard を確認し直すとステッ
プ自身のアラートが見つかるので、ツリー内のタップは控えます。今の `probe_native` の `"reserved"` と同
じ扱いです。見えたアラートがステップ自身のセレクタと一致するかどうかは、Python が判断します。判断に
は既存の `selector_names_button`（`bajutsu/common/orchestrator/types/_functions.py`）を使います。一
致しないときは、Python が `/systemAlert/resolve` を呼びます。こうしてステップが自分のアラートを確保
する仕組みは、runner に状態を持たせない、リクエストごとの選択になります。セレクタの照合は Python に
残るので、Swift へ移植する必要はありません。

リクエストには、ステップが直前の `/systemAlert/query` で見たボタンのラベルを載せます。runner が答え
るのは、いま出ているアラートのボタンがそのラベルの組とちょうど一致するときだけです。一致しなければ
`changed` を返して何も押しません。Python が判断したアラートの代わりにステップ自身のアラートが出てい
ても、方針で答えてしまうことはありません。アラートが出ていなければ `absent` を返します。どのルールに
も当たらないアラートを見つけたときは、`unidentified` を返して何も押しません。`handleSystemAlert` ス
テップは、今までどおり待ち続けます。

### Unit 4：Python 側の薄い経路

新しい capability トークン `RESOLVE_ALERTS` は、Unit 2 と Unit 3 を実装した backend を表します。この
backend では、待機ループとセレクタの解決が、`handleSystemAlert` ステップの外で `resolveAlerts=true`
を付けて `/elements` を呼びます。[証跡](../../docs/ja/glossary.md#証跡-capturepolicy-trace-triage)の
スクリーンショットとツリーのダンプはフラグを立てないので、証跡には実際の画面が残ります。

ゲートは 50 行ほどに縮み、仕事は 2 つになります。1 つは、各応答の記録をステップの状態に取り込むこと
です。もう 1 つは、*unidentified* の記録から、画面が塞がれていることを伝える注記を組み立てることです。

記録の持ち主は 1 つに限ります。持ち主は、`loop/_step_runner.py` にあるステップ終了時の
drain（`_drain_step_interruptions`）です。記録をステップの `AlertEvent` に変えるのは、引き続きこの
drain だけです。この drain がステップを名指しで失敗させるのは、辞退の記録があるときです。今の扱い
（BE-0406）のままです。*unidentified* の記録は、画面が塞がれていることを伝える注記に使うだけで、ステ
ップは止めません。そのため、あとの `handleSystemAlert` ステップでそのプロンプトに答えるシナリオは、
これまでどおり動きます。ドライバは各応答の記録を、既存の `_drain_carry` に入れます。アラートを片付け
る `/elements` は runner の記録をすべて drain するので、その応答は `/tap` の応答と同じく持ち越しを最
新とします。それ以外の呼び出しは、引き続き `is_current` を落とします。フラグのない `/elements`（Unit
1 でスナップショットが割り込み監視を呼ぶとわかれば、これも監視を呼びます）、ジェスチャ、文字入力がそ
うです。割り込み監視は XCUITest のどの操作の最中にも記録を作り、これらの応答は記録を載せないからで
す。この規則のもとで、`drain_interruptions()` の、持ち越しだけで済ませる近道は有効なままです。*gave
up* の記録は、その drain の結果から、対応する応答済みのイベントを取り下げます。注記の文面は Python
が `uncleared_prompt_note` で組み立てます。この関数は
`bajutsu/common/orchestrator/types/_functions.py` にあり、文言の置き場所は 1 つのままです。

### Unit 5：範囲を狭めたリトライ

ステップあたり 1 回のリトライは、1 つの場合だけを扱うようになります。アラートを片付けた `/elements`
と `/tap` のあいだに、アプリ側のアラートが出た場合です。そのあいだに出た SpringBoard のアラートには
リトライが要りません。タップの最中に割り込み監視が答えるからです。

リトライが発動するのは、`not-found` か `not-hittable` で確定的に拒まれ、かつステップの記録に応答済みが
あるときです。セレクタを解決し直し、操作を 1 回だけ出し直します。結果のわからない書き込みのあとには
リトライしません。2 回目の配信で操作が二重になりうるからです（BE-0207）。

capability のもとでは、ほかの 2 つの経路を動かしません。`AlertGuardConfig.__call__` のステップ終了時の
リトライ（BE-0418）を止めます。`expect` の経路が独自に呼ぶ `__call__`
（`bajutsu/common/orchestrator/loop/_functions.py`）も止めます。その結果、1 つのステップのリトライは多く
て 1 回になります。

### Unit 6：予防

予防は反応型の経路に入る頻度を下げますが、その経路をなくすことはできません。Unit 6 では次の 3 つを
扱います。

- **起動引数**：アプリがテスト中に自分のプロンプトを出さないようにする正式な方法として、
  `targets.<name>` の起動引数（`launchArgs` と `launchEnv`）を文書に書きます。
- **パスワードの自動入力**：Simulator の「パスワードを自動入力」の設定を切る、オプトインの
  `simulator.autofillPasswords: off` を試作します。4 つの iOS バージョンのすべてで設定が効いた場合に
  限って出荷します。
- **通知と App Tracking Transparency（ATT）**：どちらも反応型のままにします。BE-0276 によれば、
  通知の認可は Transparency, Consent, and Control（TCC）のサービスではありません。`simctl privacy`
  に ATT のサービスもないと考えていますが、これは試作で確かめます。

### Unit 7：Python 側の旧来の検知経路を削除する

実機での検証が済んだら、Unit 2 から Unit 5 が置き換えたものを Python 側から削除します。対象は
`probe_native` と `_observe_native` です。ラッチとタップの許可条件を持つ `_dismiss_from_tree` と、
`waits/_functions.py` のアラートの受け渡しも対象です。旧来の経路を戻すフラグは設けません。2 つ目の切り
替えは、1 つの振る舞いに 2 つ目の語彙を持ち込むからです。

capability のない backend は、今のゲートを使い続けます。明示的な `handleSystemAlert` ステップは、
`/systemAlert/query` と `/systemAlert/tap` を使い続けます（BE-0316）。

### Unit 8：検証

- **Swift**：`BajutsuKit/Tests/BajutsuRunnerTests/` の `FakeElementProvider` で、Simulator なしに
  `resolveAlerts` のハンドラを動かします。テストは次の点を扱います。
  - ハンドラの手順の順序と、SpringBoard の確認の間引きを確かめます。
  - 除外、広いものを先に試す照合、表示 1 回につき 1 件の *unidentified* の記録を確かめます。
  - 間合いの控えと *gave up* の記録を確かめます。
  - `POST /systemAlert/resolve` を、`changed` と `absent` の応答も含めて確かめます。
- **Python**：偽の actuator が capability を実装します。テストは次の点を扱います。
  - 薄いゲートと、報告の持ち主を 1 つに限る規則を確かめます。*unidentified* の記録が注記に使われ、ス
    テップを止めないことも確かめます。
  - `handleSystemAlert` ステップのツリー内だけを片付けるポーリングと、`/systemAlert/resolve` の呼び
    出しを確かめます。
  - 範囲を狭めたリトライが、結果のわからない書き込みのあとに発動しないことを確かめます。
  - capability のもとで `AlertGuardConfig.__call__` を呼ばないことを確かめます。
- **実機**：Unit 7 を適用した状態で、3 つのシナリオが 4 つの iOS バージョンで成功しま
  す。`demos/showcase/scenarios/permission.yaml`、BE-0399 の 2 つのプロンプトのシナリ
  オ、`demos/showcase/scenarios/save_password_interrupts_step.yaml` です。

### Unit 9：文書

`DESIGN.md`、`docs/architecture.md`、その `docs/ja/` の対訳は、ガードを Python の検知器として説明して
います。検知器を削除する変更で、これらの文書も更新します（BE-0113）。

## 検討した代替案

| 案 | 内容 | 採らなかった理由 |
|---|---|---|
| 独自の時計で動く、runner 常駐のウォッチャースレッド | 本提案の前の版です。runner のスレッドが一定の間隔でアラートを探し、答えます。 | 独立した時計は、リクエストとリクエストのあいだに記録を作ります。そのため、予約のエンドポイントと方針の世代が必要になりました。セレクタの照合も、正規表現の解釈が一致するかを確かめる検査つきで Swift へ移植する必要がありました。記録を覗く仕組み、アラートの有無のフラグと `/tap` での確認し直し、drain の順序の変更も必要になりました。Unit 1 で、リクエストが届いていないあいだにアラートを片付けなければならないとわかった場合に限り、再検討します。 |
| runner がフラグを立て、Python が判断する | runner が見えているアラートをすべての応答で知らせ、タップは Python が行います。 | タップの往復とゲートの状態機械が残るので、本項目が狙う複雑さも残ります。 |
| 予防だけに頼る | プロンプトが出ないように Simulator とアプリを設定します。 | 予防は通知、ATT、バナーに届きません。プロンプトそのものを試すシナリオにも使えません。Unit 6 では補完として残します。 |
| 明示的なステップだけにし、名指しで失敗させる | プロンプトごとに `handleSystemAlert` ステップを要求し、予期しないプロンプトはステップを名指しで失敗させます。 | もっとも決定的な案です。ただ、プロンプトとバナーは非同期に届くので、タイミングの負担がシナリオの作者に移ります。あとでオプトインの厳格モードとして加える余地はあります。 |
| オーケストレータへのサーバープッシュ | runner が Server-Sent Events（SSE）か WebSocket でアラートのイベントを流します。 | runner が自分でアラートに答えるので、2 本目の経路からオーケストレータが得る新しい情報はありません。 |
| アラートの文面から Swift でボタンを決める | runner が、渡された方針なしにボタンを決めます。 | BE-0399 が退けた案です。候補、プロンプトごとのルール、ロケールの表は Python にあり、方針は Python に残します。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] Unit 1：スナップショットの挙動、SpringBoard の確認のコスト、押下の所要時間、ステップのコストを測る（関門）
- [ ] Unit 2：`resolveAlerts` でアラートを片付ける `/elements`
- [ ] Unit 3：`handleSystemAlert` ステップのための `POST /systemAlert/resolve`
- [ ] Unit 4：`RESOLVE_ALERTS` capability の内側にある Python 側の薄い経路
- [ ] Unit 5：範囲を狭めた、ステップあたり 1 回のリトライ
- [ ] Unit 6：予防（起動引数とパスワードの自動入力の試作）
- [ ] Unit 7：Python 側の旧来の検知経路の削除
- [ ] Unit 8：Swift、Python、実機での検証
- [ ] Unit 9：両言語の文書

## 参考

- [BE-0207：XCUITest ランナーチャネルを一過性のタイムアウトに強くする](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)
  は、配信済みの書き込みを送り直さない規則です。
- [BE-0276：シナリオ単位で宣言する権限状態](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)
  は、通知の認可が `simctl privacy` の外にある理由です。
- [BE-0287：多点タッチ操作下での XCUITest runner チャネルの耐障害性](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)
  は、再送の可否を決める判定関数を共有するクラッシュ復旧です。
- [BE-0315：ネイティブなシステムアラート処理](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)
  は、ネイティブな SpringBoard プローブと、メインスレッドが 1 本であることによる負荷の議論です。
- [BE-0316：iOS の権限プロンプトを明示的に操作するステップ](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)
  は、`handleSystemAlert` ステップとその明示的なエンドポイントです。
- [BE-0399：シナリオの方針で割り込みアラートに答える](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy-ja.md)
  は、本項目が再利用する割り込み監視、渡される方針、drain です。
- [BE-0406：プロンプトだけでシステムアラートを宣言する](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts-ja.md)
  は、ステップを名指しで失敗させる扱いと、アプリ側のルールが割り込み監視に届かない理由です。
- [BE-0407：証拠読み取りの重複排除とドライバ内部の調整による高速化](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)
  は、`/tap` が応答に載せる記録です（Unit 6）。
- [BE-0416：実行を妨げる iOS の通知バナーをスワイプで消す](../BE-0416-ios-notification-banner-swipe-dismiss/BE-0416-ios-notification-banner-swipe-dismiss-ja.md)
  は、本項目がそのまま残すバナーの経路です。
- [BE-0418：ステップ終了時のアラートガードで複数件のアラートを解消する](../BE-0418-ios-alert-guard-one-shot-retry/BE-0418-ios-alert-guard-one-shot-retry-ja.md)
  は、本項目が capability のもとで止めるステップ終了時のリトライです。
- [`BajutsuKit/Runner/Sources/RunnerUITest.swift`](../../BajutsuKit/Runner/Sources/RunnerUITest.swift)
  は、割り込み監視です。
- [`BajutsuKit/Sources/BajutsuRunner/APIHandler.swift`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift)
  は、直列の `operations` キューと、`/tap` の応答に記録を載せる処理です。
- [`BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift`](../../BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift)
  は、渡される方針、その照合の規律、記録の置き場所です。
- [`bajutsu/common/orchestrator/waits/_alert_guard_gate.py`](../../bajutsu/common/orchestrator/waits/_alert_guard_gate.py)
  は、本項目が薄くするゲートです。
- [`bajutsu/common/orchestrator/loop/_step_runner.py`](../../bajutsu/common/orchestrator/loop/_step_runner.py)
  は、唯一の報告者として残るステップ終了時の drain です。
