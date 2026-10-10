[English](BE-XXXX-runner-resident-alert-watcher.md) · **日本語**

# BE-XXXX — システムアラートの検知を runner 常駐のウォッチャーへ移す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-runner-resident-alert-watcher-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform support |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の反応型システムアラートガードは、シナリオを止めるプロンプトを片付けます。通知の許可要求や、
iOS の「パスワードを保存」シートが対象です。現在、そのプロンプトを見張っているのは Python のオーケス
トレータです。待機のたびに、一定の間隔で SpringBoard（iOS のシステムシェル）へ問い合わせます。さらに
50 ms ごとにアクセシビリティツリーを調べ、その両方のまわりにラッチと注記の状態機械を持っています。

本提案は、この見張りを XCUITest の runner へ移します。runner は Simulator との接続を保持している、
常駐の Swift プロセスです。runner の中のウォッチャースレッドがアラートを検知し、シナリオ自身の方針が
名指しするボタンを押して、その結果を記録します。オーケストレータは見張る代わりにその記録を読みます。
アラートのゲートは検知器から読み取り役に縮み、XCUITest 専用の検知コードは Python 側からなくなります。

## 動機

ガードの検知ロジックは、待機ループのなかで最大の塊に育っています。`_AlertGuardGate`
（`bajutsu/common/orchestrator/waits/_alert_guard_gate.py`）は 801 行あります。重なる問いに答える 3 つ
の検知器が、そこで絡み合っています。

- **ネイティブの SpringBoard プローブ**：`poll_interval`（既定は 1 秒）ごとに間引かれます。
- **ツリー内の消去**：アプリ自身が出すアラート向けです。50 ms ごとのポーリングで走りますが、直前の
  プローブが SpringBoard のアラートなしと答えたポーリングに限ります（BE-0399）。
- **崩れたツリーのプロキシ**：毎ポーリング標本を取り、画面が塞がれている理由をあとから説明します。

3 つの検知器はそれぞれラッチと注記を持ち、他の検知器が作る競合に合わせて調整されてきました。BE-0418 の
リトライのような変更は、3 つすべてに照らして考えなければなりません。

この構造は、検知がどこで走るかから来ています。XCUITest には「アラートが出た」ことを知らせるコール
バックがありません。唯一のフックは BE-0399 が runner に入れた割り込み監視で、これは XCUITest がアプリに
対する操作を評価したときに発火します。操作と操作のあいだ、runner はアラートを見られません。アプリ自身の
プロセスに出るアラートは、そもそも操作に割り込みません。BE-0399 がそのルールを監視の方針から外した
のはこのためです。したがってオーケストレータは、Hypertext Transfer Protocol（HTTP）の往復をはさんで外から問い合わせるしかありません。
runner のメインスレッドが 1 本であることが、問い合わせの頻度を低く保つ制約にもなりました（BE-0315）。
プロセスの境界は、1 つの判断を 2 つの言語に分けてもいます。SpringBoard のボタンは runner が渡された
方針で押し、アプリ側のボタンは Python が同じ方針を解決し直してタップします。

ウォッチャーを runner へ移せば、この分裂はなくなります。runner は時計と直列の操作キューと両方のアラート面を
持っているので、1 回の走査で、あらゆる種類のプロンプトに 1 つの方針で答えられます。オーケストレータは
アラートの問い合わせをやめ、すでに読んでいる地点で runner の記録を読みます。シナリオの書き方は変わらず、
新しい言語モデル呼び出しが実行に入ることもありません（主要原則 1）。

変更が実現したかどうかは、3 つの観察で判断できます。1 つ目は、ガードが有効な待機（`handleSystemAlert` step を除く）が
`/systemAlert/query` も `/interruptionPolicy/drain` も送らないことです。runner のリクエストログで確かめられます。2 つ目は、
ゲートのファイルからネイティブプローブとツリー内消去（絞った場合はネイティブプローブだけ）が
なくなることです。3 つ目は、通知要求と
「パスワードを保存」を一緒に答えるショーケースのシナリオが、BE-0399 が実測した 4 つの iOS バージョン
（18.6、26.3、26.4、26.5）で通り続けることです。

## 詳細設計

作業は次の順に進めます。ウォッチャーのコストを測る、SpringBoard 向けウォッチャーを作る、オーケストレータ
の読み取り役を作る、アラートに出会った step の扱いを決める、アプリ側アラートを移す、旧経路を削除する、
検証して文書化する。アプリ側の走査が Unit 1 の関門を通らなければ、Unit 5 を外し、残りの Unit で SpringBoard 側だけを出荷します。
その場合、Unit 6 が削除するのはネイティブプローブだけです。
どの Unit も既存の継ぎ目の裏にある XCUITest 固有の挙動なので、他のバックエンドの挙動は変わらず、
主要原則 3 は保たれます。

### Unit 1：作る前に走査のコストを測る

runner 内で走査するコストは未知です。SpringBoard への問い合わせ（`springboard.alerts.firstMatch.exists`）
は、単独なら安価です。`app.alerts` に入らないアプリ側アラートの走査にはアプリツリーのスナップショット
が要り、はるかに重くなります。使い捨ての runner ビルドが、既定の 1 秒間隔で両方を走査しながら、
上の 4 つの iOS バージョンでショーケースのスイートを走らせます。step の所要時間を基準と比べ、runner の
クラッシュを数えます。Unit 2 から 8 への関門は、step の所要時間が実行ごとのばらつきを超えて変わらない
ことと、クラッシュがゼロであることです。SpringBoard が通ってアプリ側の走査が通らなければ、Unit 5 を
外して SpringBoard 側に絞ります。SpringBoard の走査が通らなければ、本項目は Rejected にします。絞った場合、薄いゲートはアプリ側のアラートに対して `_dismiss_from_tree` を呼び続けるので、「パスワードを保存」シートは引き続き扱われます。
この調査では、待機自身の `/elements` 通信のあいだに、監視がすでにアラートへ答えているかどうかも記録
します。その答えで、`/elements` の通信中にも走査が要るかどうかが決まるからです。プロダクトコードは足しません。

### Unit 2：SpringBoard アラート向けの runner 常駐ウォッチャー

ウォッチャーは `BajutsuKit/Sources/BajutsuRunner/` に、`InterruptionPolicy.swift` と並べて置く新しい型
です。`governs` が true の `POST /interruptionPolicy` が届くと始まり、後続の push が別の値を伝えると
止まります。ガードを無効にしたシナリオが、動いているウォッチャーを引き継ぐことはありません。

- **間隔**：push は `pollIntervalSeconds` を運びます。値は `systemAlertHandling.pollInterval`
  （既定 1 秒、`AlertGuardConfig.poll_interval`）です。シナリオのキーの意味は変わらず、兄弟キーも増え
  ません。
- **直列化**：走査は `APIHandler` の直列な `operations` キューで走るので、他の操作と重なることは
  ありません。ハンドラは操作のたびに実行中フラグを立て、フラグが立っているときに来た走査は次の間隔まで
  見送ります。予定している Hummingbird のリスナーでも、同じキューを保つので、この設計は成り立ちます。
- **応答**：アラートを見つけたら、ボタンのラベルを読み、`InterruptionPolicy.label(for:)` で 1 つ選び
  （識別ラベルがそれぞれちょうど 1 回ずつ現れること）、押します。ラベルは Python が解決して push します。
  照合の規律は Swift の `InterruptionPolicy.label(for:)` が保ち、これは Python の `matching_alert_rule` に
  対応するので、方針の置き場所は 1 か所のままです。
- **報告**：応答した、辞退した、払いのけたという記録は、`POST /interruptionPolicy/drain` がすでに返して
  いる保管先に入ります。どのルールも名指ししないアラートで、ウォッチャーが画面に残したものには、*unidentified* という記録の
  種類を足します。これは *declined* とは別です。*declined* は、XCUITest の既定のハンドラがアラートを
  片付けたあとに、監視が記録するものです。ウォッチャーは、unidentified のアラートを 1 回の表示につき
  1 件だけ記録します。キーはボタンの組で、走査がそのアラートをもう見つけなくなると再び有効になります。
  そのため、出しっぱなしのプロンプトが足す記録は、間隔ごとではなく 1 件です。オーケストレータは、
  BE-0406 が辞退に対して行うのと同じく、step を名前つきで失敗させます。
- **予約**：`handleSystemAlert` step は自分でアラートをタップします。そのためウォッチャーは、そのアラート
  だけは触らず、ほかのアラートには答え続けなければなりません。現在のゲートが `reserved=sel`
  （`waits/_functions.py`）で行っているのと同じです。オーケストレータは step の前に、別のエンドポイント
  `POST /alertWatcher/reservation` で step のセレクタを push します。step のあとには、空の本体で予約を
  解きます。このエンドポイントは記録の保管先も push 済みの方針も変えません。`POST /interruptionPolicy`
  は `setPolicy` が保留中の記録を消すからです。プロンプト形式の step の冒頭で `_reserve_declared_alert`
  が行うような `POST /interruptionPolicy` の push は、予約に触れません。ウォッチャーは、セレクタが
  ボタンの 1 つを名指しするアラートを飛ばします。Swift は、現在 `selector_names_button` が使う
  `base.matches` の部分集合を移します。対象は `label`、`labelMatches`、`value`、`traits` です。`id`
  は現在と同じく何も予約しません。これは `probe_native` の `reserved` 引数をウォッチャーへ引き継ぎます。

この移植は、1 つの照合規則を 2 つの言語に分けます。BE-0399 はボタンの方針についてこれを避けました。
予約がこの代償を受け入れるのは、ウォッチャーを止めると step のほかのアラートに答える者がいなくなる
からです。移植を Python に揃えておくために、2 つの歯止めを置きます。

- 両方のテストスイートが走らせる、セレクタとボタン一覧の共有フィクスチャ。
- シナリオの読み込み時の検査。Python の `re` と Foundation の `NSRegularExpression` で読みが違う
  `labelMatches` のパターンを拒否します。判定できないときも拒否します。

BE-0399 の監視は残します。2 回の走査のあいだに操作へ割り込んだアラートには、同じ方針で引き続き答え、
ウォッチャーと監視は記録の保管先を共有します。

### Unit 3：オーケストレータはプローブせずに記録を読む

新しい capability トークン `ALERT_WATCHER` が、Unit 2 のウォッチャーを走らせるバックエンドの印になり
ます。ドライバがこれを宣言すると、`bajutsu/common/orchestrator/waits/` は完全版の代わりに、
`_AlertGuardGate` と並ぶ薄いゲートを組み立てます。runner は保留中の記録を `/elements` の応答ごとに畳み込み、スナップショットと不可分に drain
します。これは `/tap` の先例にならったもので、`/tap` の応答はすでに drain したラベルを運んでいます
（BE-0407 Unit 6）。XCUITest ドライバは、畳み込まれた記録を、`/tap` の drain 結果を
すでに保持している持ち越し（`_drain_carry`）に加えます。`drain_interruptions()` は今の規則を保ちます。
持ち越しが最新ならそれだけを返し、そうでなければ `POST /interruptionPolicy/drain` の結果と合わせて返します。`POST /interruptionPolicy/drain` は、一回きりの地点（step 終了時と
シナリオ終了時）と、Unit 4 の失敗時の drain のために残ります。

薄いゲートは、ポーリングのたびに、そのポーリングの `/elements` 応答に畳み込まれた記録を読みます。
読み取りに余分な往復は要りません。記録は消費せず、報告用の event も出しません。読んだ記録は 2 つの用途に
使います。1 つは、unidentified と辞退の記録から作る、塞がれた画面の注記です。もう 1 つは、Unit 4 の
step 単位の応答済みアラートのリストです。
`probe_native` は呼ばず、タップもせず、画面についてのラッチも持ちません。例外は Unit 1 で絞った場合で、そのときは `_dismiss_from_tree` を呼び続けます。

記録の持ち主は 1 つです。step 終了時の drain（`bajutsu/common/orchestrator/loop/_step_runner.py` の
`_drain_step_interruptions`）が、記録を step の `AlertEvent` に変える唯一の場所であり続けます。
unidentified や辞退の記録で step を失敗させるのも、今と同じくこの drain です。この規則がないと、
ウォッチャーの応答がレポートに 2 回、ゲートから 1 回と drain から 1 回、載ってしまいます。

step 終了時とシナリオ終了時の一回きりの経路（`AlertGuardConfig.__call__` と `dismiss_from_tree_once`）
も、drain のエンドポイントで記録を読みます。capability を持たないバックエンド（adb、Playwright、および
試験で capability を切った fake ドライバ）は、Unit 6 が XCUITest 専用の分岐を取り除くまで、`_AlertGuardGate` をそのまま使います。

### Unit 4：応答済みのアラートに出会った step

走査が `operations` で走っているあいだに届いた要求は、その後ろで待ちます。走査が占めるのは 1 回の押下
ぶんで、BE-0399 のアクティビティログでは約 1.6 秒です。オーケストレータのソケット窓は、読み取りが
15 秒、書き込みが 30 秒です（`bajutsu/common/drivers/xcuitest/_functions.py`）。ウォッチャーは 1 回の
走査につきボタンを 1 つだけ押すので、待っている要求が吸収する時間は、押下 1 回を超えません。
Unit 1 は観測した最長の待ち時間を記録します。

それでも step がアラートに出会う場面は 2 つあり、それぞれに答えがあります。

- **step の操作が進行中にアラートが出る場合**：XCUITest が割り込みを監視に渡し、そのあと元のイベントを
  合成します。操作は一度だけ完了するので、リトライは要りません。
- **操作が届く前に、アラートが対象を覆っていた場合**：step のセレクタが何も見つけないか、要素が
  タップできず、今は step が失敗します。薄いゲートは、現在の step のあいだに読んだ応答済みの
  アラートを、step 自身のコンテキストの step 単位のリストに保持します。この失敗のとき、オーケストレータは
  もう一度 drain して、最後のポーリングのあとに記録された応答を拾います。drain した記録は、
  `_reserve_declared_alert` の push 前の drain が今そうしているように、step の結果に畳み込みます。
  拾ったものがレポートから落ちることはありません。そのうえで、step 単位のリストと、この drain が返した応答済みの記録を調べます。
  どちらかに応答済みのものがあれば、セレクタを解決し直し、操作をもう一度だけ出します。runner の記録は
  タイムスタンプを持たないままです。窓を決めるのは時計ではなく、step の境界です。

リトライは、結果が不明な書き込みのあとには決して走りません。BE-0207 は、二重操作の恐れがあるため、
配信後に結果が不明な書き込みの再送を禁じています。本項目もこの規則を保ちます。`not-found` や
`not-hittable` の明確な拒否は runner が実行せずに断ったものなので、これに当たりません。リトライは step ごとに 1 回で、応答の記録が
手元にあるときに限ります。関係のない失敗を隠すことはありません。レポートには、応答したアラートとリトライ
の両方を載せます。

### Unit 5：ウォッチャーでアプリ側のアラートを扱う

走査はアプリ側のアラート（「パスワードを保存」シートなど）も探します。Python のツリー内ルール
（`AlertGuardConfig.tree_rules`）を、同じ `POST /interruptionPolicy` の本体で push します。現在は
`push_interruption_policy` が push の前にこれを捨てています。通信形式はルールごとに省略可能な `exclude` リストを持ち、Python は
`AlertGuardConfig.tree_dedup_rules` の順（入れ子の形は広いほうが先）でルールを push します。
ウォッチャーは、識別ラベルがそれぞれアプリのスナップショットのボタンにちょうど 1 回ずつ現れ、
除外ラベルが 1 つもないルールを照合し、名指しされたボタンをタップします。
`push_interruption_policy` が除外の組を拒む制約は、ツリー内ルールに限って外します。

`_dismiss_from_tree` が今持っているペース配分は、タップと一緒に移ります。ただし数値は動きません。
再タップの遅延、1 回の表示あたりのタップ回数の上限、タップ不能時の見切りの上限は、
`waits/_alert_guard_gate.py` と `waits/_functions.py` から `alert_guard_config.py` へ移し、そこから
push するので、置き場所は 1 か所のままです。見切りの上限は、引き続き Python 側で `poll_interval` から
導きます。縮小した形では、定数は `_dismiss_from_tree` が使う場所に残ります。ウォッチャーは見切りを、
ルールの識別ラベルを名指しする *gave up* 記録として報告します。オーケストレータはその記録から
`uncleared_prompt_note` の文面を組みます。取り下げは step 終了時の drain の結果に適用し、
`_withdraw_tree_event` と同じ扱いです。同じ表示に対する後の *gave up* 記録が否定する応答済みの event を、
step 終了時の drain が落とします。文面は
Python に残ります。

ブラウザを統合したツリー（BE-0396）の重複ペアは、`queryElements` の中ですでに 1 つにまとまります。
ウォッチャーが見るボタンは 1 つなので、`resolve_unique` の意味は保たれます。曖昧な一致は、引き続き辞退
して報告します。最初に一致したものを黙ってタップすることはありません（主要原則 2）。

### Unit 6：旧来の XCUITest 検知経路を削除する

Unit 2 から 5 が実機で通ったあと、Python 側はウォッチャーが置き換えたものをすべて失います。対象は
`probe_native`、`_observe_native` のプローブと許可のロジック、ラッチを含む `_dismiss_from_tree`、
崩れたツリーのプロキシのうち XCUITest 固有の分岐です。これらを元に戻すフラグは作りません。2 つ目の
スイッチは、同じ挙動の第二の語彙になるからです。明示的な `handleSystemAlert` step は、
`/systemAlert/query` と `/systemAlert/tap` を引き続き使います（BE-0316）。崩れたツリーのプロキシは、
capability を持たないバックエンドのために残ります。絞った場合、Unit 6 が削除するのはネイティブプローブだけで、`_dismiss_from_tree` は残します。

### Unit 7：検証

- **Swift**：`BajutsuKit/Tests/BajutsuRunnerTests/` の `FakeElementProvider` で、ウォッチャーの間隔、
  処理中の見送り、予約の照合（共有フィクスチャを使います）、除外と広い順の照合、「ちょうど 1 回」の
  照合、1 回の表示につき 1 件の unidentified 記録、`/elements` 応答に畳み込まれた記録、見切りの上限を、
  Simulator なしで動かします。
- **Python**：fake アクチュエータが capability を実装します。試験するのは次の 5 つです。
  - 記録の持ち主が 1 つであること。薄いゲートは畳み込み記録を読むだけで報告せず、step 終了時の drain が各記録を 1 回だけ報告すること。
  - unidentified の記録で step を名前つきで失敗させること。
  - `handleSystemAlert` の前後で予約を push して解くことと、読み込み時の `labelMatches` の検査。
  - step 単位の event リストをもとに、配信前の失敗のあとで step ごとに 1 回だけリトライすること。
    結果が不明な書き込みのあとには走りません。
  - *gave up* 記録から `uncleared_prompt_note` の文面を組み、step 終了時の drain の結果でその `AlertEvent` を取り下げること。
- **実機**：`demos/showcase/scenarios/permission.yaml` と BE-0399 の 2 プロンプトのシナリオが、Unit 6
  の削除を適用した状態で、4 つの iOS バージョンで通ります。

### Unit 8：文書

`docs/architecture.md`、`DESIGN.md`、`docs/ja/` の対応ページは、ガードを Python の検知器として説明して
います。検知器を削除する同じ変更で更新します（BE-0113）。

## 検討した代替案

| 案 | 内容 | 採らなかった理由 |
|---|---|---|
| runner がフラグを立て、Python が判断する | runner が検知し、すべての応答にフラグを載せます。タップは Python が行います。 | タップの往復とゲートの状態機械が残ります。本項目が狙う複雑さが残ることになります。 |
| オーケストレータへのサーバープッシュ | runner が Server-Sent Events か WebSocket でアラートの event を流します。 | runner が自分でアラートに答えるので、オーケストレータが記録を要するのは、すでに読んでいる地点だけです。2 本目のチャネルは、新しい情報なしに輸送路を増やします。 |
| 割り込み監視だけに頼る | ウォッチャーをやめ、監視に答えさせます。 | 監視は XCUITest が操作を評価したときにしか走りません。アプリ側のアラートは操作に割り込まない（BE-0406）ので、答えられないまま残ります。 |
| Python の経路をフラグの裏に残す | ウォッチャーを出荷し、ポーリングへ戻すスイッチを付けます。 | 1 つの挙動の実装が 2 つ保守対象に残り、フラグが第二の語彙になります。 |
| アラートの文面から Swift がボタンを決める | 渡された方針なしに runner がボタンを決めます。 | BE-0399 が却下しました。候補、プロンプトごとのルール、ロケール表は Python にあります。 |
| `handleSystemAlert` の前後でウォッチャーを止める | step が、停止と再開の 2 つのエンドポイントでウォッチャーを止めます。 | step のほかのアラートに答える者がいなくなります。薄いゲートはタップせず、監視は step 自身の `/systemAlert/query` のポーリングでは発火せず、アプリ側のアラートは監視に届きません。そのため「パスワードを保存」シートが、step のタイムアウトいっぱいまで画面を塞ぎかねません。 |
| SpringBoard のアラートだけ | ウォッチャーは `springboard.alerts` を扱い、ツリー内の経路は Python が持ちます。 | アプリ側の走査が Unit 1 の関門を通らなければ、このとおりに縮めます。ただし採ると、最大の検知器がゲートに残ります。 |

## 進捗

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1：4 つの iOS バージョンで runner 内の走査を測る（関門）
- [ ] Unit 2：SpringBoard アラート向けの runner 常駐ウォッチャー
- [ ] Unit 3：薄いゲートと `ALERT_WATCHER` capability で、オーケストレータが記録を読む
- [ ] Unit 4：応答済みのアラートに出会った step（走査の後ろで待つ時間の上限と、配信前の失敗に限るリトライ）
- [ ] Unit 5：ウォッチャーでアプリ側のアラートを扱う
- [ ] Unit 6：旧来の XCUITest 検知経路を削除する
- [ ] Unit 7：Swift、Python、実機での検証
- [ ] Unit 8：両言語の文書

## 参考

- [BE-0315：ネイティブなシステムアラート処理](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)
  は、ネイティブな SpringBoard プローブと、メインスレッドが 1 本であることによる負荷の議論です。
- [BE-0399：シナリオの方針で割り込みアラートに答える](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy-ja.md)
  は、本項目が再利用する監視、push される方針、drain です。
- [BE-0406：プロンプトだけでシステムアラートを宣言する](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts-ja.md)
  は、アプリ側のルールが監視に届かない理由です。
- [BE-0316：権限プロンプト用の明示的な step](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)
  は、明示的な経路を残す `handleSystemAlert` step です。
- [`BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift`](../../BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift)
  は、push される方針と照合の規律です。
- [`bajutsu/common/orchestrator/waits/_alert_guard_gate.py`](../../bajutsu/common/orchestrator/waits/_alert_guard_gate.py)
  は、本項目が薄くするゲートです。
