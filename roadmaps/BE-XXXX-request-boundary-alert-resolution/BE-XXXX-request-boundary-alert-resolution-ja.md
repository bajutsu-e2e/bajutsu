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
答えた結果の記録が載ります。これで Python のアラートゲートは、801 行の検知器から、数十行の読み取り役と、残す折りたたみツリーの代理指標に縮みます。

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
そのため、予約の状態、方針の世代、記録を消費せずに覗くための仕組みは要りません。あとの `/tap` で確かめ直さなければならない、SpringBoard のアラートの有無を使い回すフラグも、drain の順序の変更も要りません。

シナリオは変わらず、言語モデルの呼び出しが実行に入ることもありません（基本原則 1）。

変更が届いたことは、あとから次の 3 点で確かめられます。

- `handleSystemAlert` ステップの外では、ガードが効いている待機が `/systemAlert/query` を送らず、各ポーリングのツリーの読み取りにはすべて `resolveAlerts=true` が付きます。TipKit のチップが出ている
ときに `dismiss_blocking_tip` がハンドルを得るために読み直す `/elements` は、フラグのないままです。runner のリクエストログで、その両方を確認
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
   ます。スナップショットが割り込み監視を呼ぶなら、Unit 2 の SpringBoard の確認は不要になり、ツリー内のルールを当てる処理だけが残ります。その場合はフラグのない `/elements` も SpringBoard のアラートに答えるので、Unit 2 に入る前に次の 3 点を見直します。証跡のツリーのダンプが実際の画面を残すという Unit 4 の前提、ステップ自身のアラートに触れないために `resolveAlerts=inTree` に頼る Unit 3 の前提、そして手順 1 だけが生む 2 つの出力です。2 つの出力とは、`alert_block_note` のもとになる *unidentified* の記録と、折りたたみツリーの代理指標が待機を早めに打ち切るための `springboardChecked` です。
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
   プせず、確認のたびに *unidentified*（未識別）を記録します。ネイティブのルールの表示は、一致した
   ルールの識別ラベルをキーにするので、1 回の表示は *answered* として 1 回だけ記録します。押し直すのは、あとで述べる間合いの控えに従うときだけです。のちの確認で
   そのアラートが見つからなくなったときに、キーは次の表示に備えます。SpringBoard の *answered* は即座に記録します。runner がボタンを押したからです。押し終えた時点でアラートはたいてい消えています。押しが届かなかったときは、間合いの控えに従って押し直します。
2. **アプリのスナップショットを取ります**：フラグのない `/elements` と同じ処理です。
3. **ツリー内のルールを当てます**：渡されたツリー内のルールは、識別ラベルが、スナップショットのボタンのうち identifier を持たずラベルを持つもの（現在 `bajutsu/common/drivers/elements.py` の `tree_buttons` が作る集合）にちょうど 1 回ずつ現れ、除外ラベルが 1 つも現れないときに一致します。ハンドラはルールを受け取った順（Python は `tree_dedup_rules` の順に送ります）に試すので、入れ子の形では広いほうの兄弟が先に来ます。一
   致すれば同じハンドラの中で SpringBoard を確認し直し、SpringBoard のアラートが出ていないときに限ってタップします。出ていれば、ツリー内のアラートはあとのリクエストに任せます。そのあとスナップショットを取り直します。ツリー内のタップは、タップの時点で *answered* を記録します。runner がこのボタンを押した、という意味です。
4. **応答を返します**：応答には、ツリーと、このリクエストで drain した記録が載ります。`/tap` もすで
   に同じ形で、drain した記録を応答に載せています（BE-0407 の Unit 6、`APIHandler.swift`）。応答に
   は、何も起きなかったときも空の記録の欄を必ず載せます。応答には `springboardChecked` も載せます。この応答自身の手順 1 が SpringBoard を調べてアラートを見つけなかったときに、`true` になります。`resolveAlerts` を付けた読み取りの応答に記録の欄がなければ、ドライバは `XcuitestChannelError` を送出します。古い runner のビルドが黙って普通
   のツリーを返すことはありません。

手順 3 の確認し直しは、今のゲートが守っている順序を保ちます。SpringBoard のアラートが出ている最中に
XCUITest でタップすると、割り込み監視がそのアラートへ先に答えてしまうからです。

**間合い**：ネイティブの表示とツリー内の表示は、同じ間合いの控えを使います。控えは
`InterruptionPolicyStore` の中のルールごとの小さな控えで、次の 3 つの値を持ちます。

- 再タップまでの遅延です。
- 表示 1 回あたりのタップ回数の上限です。
- 諦めるまでの上限です。

これらの定数は、`waits/_alert_guard_gate.py` と `waits/_functions.py` から
`bajutsu/common/orchestrator/types/alert_guard_config.py` へ移し、オーケストレータが runner へ渡します。
諦めるまでの上限は、引き続き Python 側で `poll_interval` から導きます。ツリー内のタップは、今の間合い
を保ちます。ネイティブの表示も、再タップまでの遅延を過ぎてなお次の確認で見つかれば押し直します。押し
直しは、表示 1 回あたりのタップ回数の上限で止めます。

ツリー内のルールの控えは、タップした時点のツリーの署名（ラベルと identifier の組）も、今の
`tree_signature` と同じく持ちます。再タップまでの遅延を過ぎても同じラベルが一致し、画面が変わって
いれば、それはシートが覆っていたアプリ側のボタンです。ハンドラはその表示のあいだタップを控え、
*gave up* も記録しません。

**記録**：*unidentified* と *gave up* は新しい種類の記録です。runner の記録の置き場所、drain の応答、
`DrainedInterruptions` には、それぞれこの 2 種類のための欄を加えます。この欄は `unmatched` とは分け
ます。`unmatched` は引き続き割り込み監視の辞退を表し、ステップを失敗させる唯一の種類です。

- *answered* の記録はルールの種類（`native` か `inTree`）を持ち、`DrainedInterruptions` もそれを
  保ちます。そのため、読む側は SpringBoard での押下とツリー内での押下を区別できます。
- 同じ表示への押し直しは、ネイティブでもツリー内でも、新しい *answered* を作りません。
- 諦めたときは、*answered* の隣に *gave up* を記録し、何も取り消しません。この記録は、ルールの識別
  ラベルと、ルールが選んだボタンを持ちます。`uncleared_prompt_note` はそのボタンを挙げ、押しても
  消えなかったことを作者に伝えます。
- そのため、1 回の表示に残る記録は、*answered* が最大 1 件、*gave up* が最大 1 件です。

**方針の受け渡し**：`InterruptionPolicyStore.setPolicy` は、受け渡しのたびに保留中の記録を消します。
シナリオの途中では、Python が受け渡しの前に drain します。シナリオ開始時の受け渡し
（`bajutsu/common/runner/pipeline.py`）は、前のシナリオの記録を意図して捨てます。

- シナリオ開始時の受け渡しは `scenarioStart` の印を持ち、表示ごとのキーと間合いの控えも消します。
  そのため、前のシナリオで答えたり控えたりしたことが、再起動後の同じプロンプトを妨げることはありま
  せん。
- シナリオの途中の受け渡し（`_reserve_declared_alert` とその復元）は、キーと控えを残します。その
  受け渡しをまたぐ表示を、早すぎるタイミングで押し直すことも、2 回記録することもありません。
- キーは、そのアラートが消えたときに次の表示に備えます。

通信形式には、ルールごとに省略可能な `exclude` の一覧と、ルールの種類（`ResolvedAlertRule` 由来の
`native` か `inTree`）を加えます。方針全体にも、`pollIntervalSeconds` と、先に挙げた間合いの 3 つの値と、`scenarioStart` の印を加えます。`ResolvedAlertRule` は `native` と `in_tree` を独立した 2 つのフラグとして持ちます。今は両方を立てるプロンプトはありません。両方を立てたルールが来たときは、片方の種類だけで渡すのではなく、除外の組を持つネイティブに届くルールと同じく `push_interruption_policy` が `ValueError` を送出します。
`push_interruption_policy`（`bajutsu/common/orchestrator/types/_functions.py`）は、ツリー内のルールを渡す前に落とすのをやめ、`AlertGuardConfig.tree_dedup_rules` の順に送ります。runner は受け取った順を保つので、`_widest_first` は Python 側にだけ残ります。割り込み監視と手順 1 の SpringBoard の確認
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

`POST /systemAlert/resolve` は、表示中の SpringBoard のアラートに、渡された方針を当てます。`/tap` と同じく runner の記録を drain して応答に載せ、ドライバはその応答を `_drain_carry` に入れます。そのため、その答えを消費した drain、つまりステップの待機中は `_policy_answered_alert` が、答えを 1 回だけ報告します。`handleSystemAlert` ステップは、自分のもの以外のアラートへ答えるときにこの
エンドポイントを使います。`_policy_answered_alert` はその分岐に残り、`resolve_system_alert` のあとに動きます。そのため、ステップ自身のアラートをポーリングの合間に監視が答えた場合も、ステップは先へ進めます（BE-0406 の Unit 2b）。ただし、セレクタとの照合に数えるのはネイティブの応答
だけです。ツリー内の *answered* の記録は drain を通して種類を保つので、ステップと同じラベルを持つ
アプリ側のボタンは無関係なアラートとして報告され、ステップを通すことはありません。
ステップは自分の `/systemAlert/query` のポーリングを続け、このポーリングは何も片付けません。それと並んで行うツリーの読み取りは、`query_resolving(inTree)` で行います。このモードは Unit 2 の手順 2 から
4 を行い、手順 1 の SpringBoard の確認を省きます。そのため、「パスワードを保存」シートのようなアプリ
側のアラートは、ステップの待機中も片付きます（BE-0406）。手順 3 で SpringBoard を確認し直すとステッ
プ自身のアラートが見つかるので、ツリー内のタップは控えます。今の `probe_native` の `"reserved"` と同
じ扱いです。見えたアラートがステップ自身のセレクタと一致するかどうかは、Python が判断します。判断に
は既存の `selector_names_button`（`bajutsu/common/orchestrator/types/_functions.py`）を使います。一致しないときは、Python が `resolve_system_alert` を呼び、`/systemAlert/resolve` を送ります。こうしてステップが自分のアラートを確保
する仕組みは、リクエストごとの選択になり、今の方針の受け渡しを除けば runner に状態を持たせません。`handleSystemAlert` ステップが `_reserve_declared_alert`（`loop/_step_runner.py`、BE-0406 の Unit 2b）を通して行う方針の受け渡しと、ステップ後の復元は、`RESOLVE_ALERTS` のもとでも変わりません。セレクタの照合は Python に
残るので、Swift へ移植する必要はありません。

リクエストには、ステップが直前の `/systemAlert/query` で見たボタンのラベルを載せます。runner が答え
るのは、いま出ているアラートのボタンがそのラベルの組とちょうど一致するときだけです。一致しなければ
`changed` を返して何も押しません。Python が判断したアラートの代わりにステップ自身のアラートが出てい
ても、方針で答えてしまうことはありません。アラートが出ていなければ `absent` を返します。どのルールに
も当たらないアラートを見つけたときは、`unidentified` を返して何も押しません。`handleSystemAlert` ス
テップは、今までどおり待ち続けます。

### Unit 4：Python 側の薄い経路

待機ループは、`bajutsu/common/drivers/base/` に `InterruptionPolicyTarget` と並べて置く狭いドライバのプロトコル `AlertResolvingTarget` を通して、新しい動きを使います。その `query_resolving(mode)` は `true` か `inTree` を受け取り、要素とその応答の記録を一緒に返します。`resolve_system_alert(labels)` は、ステップが直前に見たラベルの組を載せて Unit 3 の `POST /systemAlert/resolve` を送り、`answered`、`changed`、`absent`、`unidentified` のいずれかを返します。`RESOLVE_ALERTS` のトークンは、このプロトコルを実装したバックエンドの印です。ドライバはどちらの応答の記録も `_drain_carry` に入れます。そのためゲートは戻り値の記録を読むだけで何も消費せず、各記録の報告は、それを消費した drain が行います。`Driver.query` と他のバックエンドは変わらず、証跡の取得は引き続き `Driver.query` を使います。

`RESOLVE_ALERTS` を通知する backend では、ガードが効いているとき、待機ループとセレクタの解決が、`handleSystemAlert` ステップの外で `query_resolving(true)`（`GET /elements?resolveAlerts=true` を送ります）を呼びます。[証跡](../../docs/ja/glossary.md#証跡-capturepolicy-trace-triage)の
スクリーンショットとツリーのダンプはフラグを立てないので、証跡には実際の画面が残ります。ガードを切ったシナリオは引き続き `Driver.query` を使うので、片付ける読み取りは送りません。

ゲートは、数十行と、残す折りたたみツリーの代理指標に縮みます。代理指標のほかに、ゲートの仕事は 2 つです。1 つは、`query_resolving` が返す記録を読み、ステップの状態に取り込むことです。もう 1 つは、画面が塞がれていることを伝える注記を組み立てることです。*unidentified* の記録からは、どのルールも識別しなかったボタンを挙げる `alert_block_note` で組み立てます。*gave up* の記録からは、ルールが選んだのに片付けられなかったボタンを挙げる `uncleared_prompt_note` で組み立てます。

ゲートは、今の折りたたみツリーの代理指標も `springboardChecked` の上に残します。このフラグは、同じリクエストの中で確かめた事実であり、使い回す許可ではありません。ツリー内のルールを宣言したシナリオでツリーが `frozenScreenTimeout` のあいだ折りたたまれたままのとき、`springboardChecked` が `true` の応答が来た時点で、`collapsed_tree_note` で行き詰まりの注記を付け、待機を早めに打ち切ります。フラグが `false` の応答では、この注記を付けません。今のネイティブ確認の「不在」も、同じ扱いです。iOS 26.5 で「パスワードを保存」シートが表示の途中で残る場合が、その例です。

各記録は、それを消費した drain が 1 回だけ報告します。今の役割を保つ 3 つの drain を除けば、ステップ終了時の drain（`loop/_step_runner.py` の `_drain_step_interruptions`）が唯一の報告元です。3 つとは、`handleSystemAlert` の待機の中の `_policy_answered_alert`（`waits/_functions.py`）、`expect` の段階の drain（`loop/_functions.py`）、`_reserve_declared_alert` が push の前に行う drain（`loop/_step_runner.py`）です。ステップ終了時の drain がステップを名指しで失敗させるのは、辞退の記録があるときです。今の扱い
（BE-0406）のままです。*unidentified* の記録は、画面が塞がれていることを伝える注記に使うだけで、ステ
ップは止めません。そのため、あとの `handleSystemAlert` ステップでそのプロンプトに答えるシナリオは、
これまでどおり動きます。ドライバは各応答の記録を、既存の `_drain_carry` に入れます。アラートを片付け
る `/elements` は runner の記録をすべて drain するので、その応答は `/tap` の応答と同じく持ち越しを最
新とします。それ以外の呼び出しは、引き続き `is_current` を落とします。フラグのない `/elements`（Unit
1 でスナップショットが割り込み監視を呼ぶとわかれば、これも監視を呼びます）、ジェスチャ、文字入力がそ
うです。割り込み監視は XCUITest のどの操作の最中にも記録を作り、これらの応答は記録を載せないからで
す。この規則のもとで、`drain_interruptions()` の、持ち越しだけで済ませる近道は有効なままです。注記の文面は Python が既存の `alert_block_note` と `uncleared_prompt_note` で組み立てます。どちらも `bajutsu/common/orchestrator/types/_functions.py` にあり、文言の置き場所は 1 つのままです。

### Unit 5：範囲を狭めたリトライ

ステップあたり 1 回のアラートによるリトライは、1 つの場合だけを扱うようになります。アラートを片付けた `/elements`
と `/tap` のあいだに、アプリ側のアラートが出た場合です。このあいだには、`Driver.tap` が要素のハンドルを得るために送る
フラグのない `/elements` も含まれます。そのあいだに出た SpringBoard のアラートには
リトライが要りません。タップの最中に割り込み監視が答えるからです。

runner に `not-found` か `not-hittable` で確定的に拒まれたとき、または `Driver.tap` が `/tap` を
送る前の自分のハンドルの読み取りで `ElementNotFound` を送出したときは、アラートを片付ける `query_resolving(true)` でセレクタを解決し直します。その応答の記録に応答済みがあるときに限り、操作を 1 回だけ出し直します。結果のわからない書き込みのあとには
リトライしません。2 回目の配信で操作が二重になりうるからです（BE-0207）。

capability のもとでは、ほかの 2 つの経路を動かしません。`AlertGuardConfig.__call__` のステップ終了時の
リトライ（BE-0418）を止めます。`expect` の経路が独自に呼ぶ `__call__`
（`bajutsu/common/orchestrator/loop/_functions.py`）も止めます。その結果、1 つのステップのアラートによるリトライは多く
て 1 回になります。`_dismiss_blocking_tip` のあとの TipKit のリトライ（`loop/_step_runner.py`）は変わりません。

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

実機での検証が済んだら、Unit 2 から Unit 5 が置き換えたものを Python 側から削除します。折りたたみツリーの代理指標は残すので、削除するのはネイティブ確認とツリー内の消去だけです。対象は `probe_native` と `_observe_native`、そして `AlertGuardConfig.__call__` の各ラウンドのうちネイティブの半分です。`HANDLE_SYSTEM_ALERT` を持たない backend では `probe_native` がすぐに空の "absent" を返し、その空の読み取りが、同じラウンドで `dismiss_from_tree_once` がタップしてよい根拠になっています。そのため、ツリーの半分は残し、`__call__` を使い続ける backend では条件なしに動かします。ラッチとタップの許可条件を持つ `_dismiss_from_tree` と、
`waits/_functions.py` のアラートの受け渡しも対象です。旧来の経路を戻すフラグは設けません。2 つ目の切り
替えは、1 つの振る舞いに 2 つ目の語彙を持ち込むからです。

capability のない backend は、今のゲートを使い続けます。明示的な `handleSystemAlert` ステップは、
`/systemAlert/query` と `/systemAlert/tap` を使い続けます（BE-0316）。

### Unit 8：検証

- **Swift**：`BajutsuKit/Tests/BajutsuRunnerTests/` の `FakeElementProvider` で、Simulator なしに
  `resolveAlerts` のハンドラを動かします。テストは次の点を扱います。
  - ハンドラの手順の順序と、SpringBoard の確認の間引きを確かめます。
  - 除外、広いものを先に試す照合、確認のたびの *unidentified* の記録、ネイティブの表示を 1 回だけタップして記録することを確かめます。
  - 間合いの控えと *gave up* の記録を確かめます。
  - 再タップまでの遅延を過ぎても残るネイティブの表示が、上限まで押し直され、そのあと *gave up* として記録されることを確かめます。
  - `POST /systemAlert/resolve` を、`answered`、`changed`、`absent`、`unidentified` の応答とラベルの組による確認も含めて確かめます。
  - 応答の `springboardChecked` が事実どおりになることを確かめます。
  - シナリオの途中の受け渡しでは表示ごとのキーと間合いの控えが残り、`scenarioStart` の受け渡しでは消えることを確かめます。
  - ツリー内の *answered* がタップの時点で記録され、あとの *gave up* がその隣に、取り消しなしで記録されることを確かめます。
  - 画面の署名が変わったときに再タップを控えることを確かめます。
- **Python**：偽の actuator が capability を実装します。テストは次の点を扱います。
  - 薄いゲートと、記録を消費した drain が 1 回だけ報告する規則を確かめます。*unidentified* の記録が注記に使われ、ス
    テップを止めないことも確かめます。
  - `handleSystemAlert` ステップのツリー内だけを片付けるポーリングと、`/systemAlert/resolve` の呼び
    出しを確かめます。
  - 範囲を狭めたリトライが、結果のわからない書き込みのあとに発動しないことを確かめます。
  - capability のもとで、ステップ終了時のリトライからも `expect` の経路からも `AlertGuardConfig.__call__` を呼ばないことを確かめます。
  - 配信済みの `resolveAlerts` の読み取りを、BE-0207 のリトライでも BE-0287 の復旧での再送でも、`_is_retry_eligible` が送り直させないことを確かめます。
  - 記録の欄がない `resolveAlerts` の応答で `XcuitestChannelError` が送出されることを確かめます。
  - 折りたたみツリーの代理指標が、`springboardChecked` が `true` の応答でだけ待機を早めに打ち切ることを確かめます。
  - `query_resolving` が各応答の記録を返し、持ち越しにも入れることを確かめます。
  - ステップのセレクタと同じラベルのツリー内の応答が、`handleSystemAlert` ステップを通さないことを確かめます。
  - capability のもとで、ステップ自身のアラートをポーリングの合間に監視が答えた場合も、`handleSystemAlert` ステップが先へ進むことを確かめます。
  - capability のない backend で、`probe_native` を削除したあとも、ステップ終了時の `__call__` がツリー内のプロンプトを片付けることを確かめます。
- **実機**：Unit 7 を適用した状態で、3 つのシナリオが 4 つの iOS バージョンで成功しま
  す。`demos/showcase/scenarios/permission.yaml`、BE-0399 の 2 つのプロンプトのシナリ
  オ、`demos/showcase/scenarios/save_password_interrupts_step.yaml` です。

### Unit 9：文書

`DESIGN.md`、`docs/architecture.md`、その `docs/ja/` の対訳は、ガードを Python の検知器として説明して
います。検知器を削除する変更で、これらの文書も更新します（BE-0113）。

## 検討した代替案

| 案 | 内容 | 採らなかった理由 |
|---|---|---|
| 独自の時計で動く、runner 常駐のウォッチャースレッド | 本提案の前の版です。runner のスレッドが一定の間隔でアラートを探し、答えます。 | 独立した時計は、リクエストとリクエストのあいだに記録を作ります。そのため、予約のエンドポイントと方針の世代が必要になりました。セレクタの照合も、正規表現の解釈が一致するかを確かめる検査つきで Swift へ移植する必要がありました。記録を覗く仕組み、アラートの有無のフラグと `/tap` での確認し直し、drain の順序の変更も必要になりました。Unit 8 の実機での検証で、リクエストが届いていないあいだにアラートを片付けなければならないとわかった場合に限り、再検討します。 |
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
  は、ステップの記録を報告する、ステップ終了時の drain です。