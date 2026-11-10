[English](BE-0461-request-boundary-alert-resolution.md) · **日本語**

# BE-0461 — システムアラートを Python から見張らず、runner のリクエストの境目で片付ける

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0461](BE-0461-request-boundary-alert-resolution-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0461") |
| 実装 PR | [#2152](https://github.com/bajutsu-e2e/bajutsu/pull/2152)（Unit 1）、[#2155](https://github.com/bajutsu-e2e/bajutsu/pull/2155)（Unit 2）、[#2164](https://github.com/bajutsu-e2e/bajutsu/pull/2164)（Unit 3〜9） |
| トピック | Platform support |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の反応型システムアラートガードは、[シナリオ](../../docs/ja/glossary.md#シナリオのオーサリング)
を止めるプロンプトを片付けます。対象は、通知の許可要求と、iOS の「パスワードを保存」シートです。
現在、そのプロンプトを見張っているのは Python のオーケストレータで、デバイスの外から見張っています。
待機のたびに、一定の間隔で SpringBoard（iOS のシステムシェル）へ問い合わせます。さらに 50 ms ごとに
アクセシビリティツリーを調べます。この 2 つのまわりには、ラッチと注記の状態機械があります。

本提案は、リクエストが画面を必要とした時点で、XCUITest の runner の中でアラートを片付けます。runner は、
Simulator との接続を保持している常駐の Swift プロセスです。オーケストレータが要素ツリーを求めると、
runner はまず方針が名指しするアラートに答え、そのあとでスナップショットを取ります。応答には、ツリーと
答えた結果の記録が載ります。これで Python のアラートゲートは、801 行の検知器から、数十行の読み取り役に
縮みます。折りたたみツリーの代理指標は残します。

## 動機

ガードの検知ロジックは、待機ループのなかで最大の塊に育っています。`_AlertGuardGate`
（`bajutsu/common/orchestrator/waits/_alert_guard_gate.py`）は 801 行あります。そこでは、重なる問いに
答える 3 つの検知器が絡み合っています。

- **ネイティブの SpringBoard プローブ**：`poll_interval`（デフォルトは 1 秒）ごとに間引かれます。
- **ツリー内の消去**：アプリ自身が出すアラート向けです。50 ms ごとのポーリングで走りますが、直前の
  プローブが SpringBoard のアラートなしと答えたポーリングに限ります（BE-0399）。
- **折りたたみツリーの代理指標**：毎ポーリング標本を取り、画面が塞がれている理由をあとから説明します。

3 つの検知器はそれぞれラッチと注記を持ち、他の検知器が作る競合に合わせて調整されてきました。BE-0418 の
リトライのような変更は、3 つすべてに照らして考えなければなりません。

この構造は、検知がどこで走るかから来ています。XCUITest には「アラートが出た」ことを知らせるコール
バックがありません。唯一の入口は、BE-0399 が runner に入れた割り込み監視
（`BajutsuKit/Runner/Sources/RunnerUITest.swift`）です。XCUITest が割り込み監視を呼ぶのは、操作を評価
するときと、アプリに対する一部の問い合わせのときです。アプリ自身のプロセスに出るアラートは、割り込み
監視に届きません。

そのためオーケストレータは、Hypertext Transfer Protocol（HTTP）の往復をはさんで外から問い合わせています。
runner のメインスレッドは 1 本で、この制約も問い合わせの頻度を低く保たせました（BE-0315）。プロセスの
境界は、1 つの判断を 2 つの言語に分けてもいます。SpringBoard のボタンは runner が渡された方針で押し、
アプリ側のボタンは Python が同じ方針を解決し直してタップします。

アラートが実行にとって意味を持つのは、次のリクエストが画面を見るときです。独立した別の時計で動く
ウォッチャーは、まだ誰も見ていないアラートへ答えることになります。そこで本提案は、オーケストレータが
画面を読むその場所でアラートを片付けます。runner はリクエストの中で処理を行い、その順序はオーケストレータ
がすでに送っているリクエストの順序に従います。

2 つのリクエストのあいだに記録が生まれることはありません。記録はどれもリクエストの中で生まれ、応答に
載らなかった分はステップ終了時の drain が集めます。そのため、予約の状態、方針の世代、記録を消費せずに
覗くための仕組みは要りません。あとの `/tap` で確かめ直さなければならない、SpringBoard のアラートの有無を
使い回すフラグも要りません。drain の順序も変えません。

シナリオは変わらず、言語モデルの呼び出しが実行に入ることもありません（基本原則 1）。

変更が届いたことは、あとから次の 3 点で確かめられます。

- `handleSystemAlert` ステップの外では、ガードが効いている待機が `/systemAlert/query` を送らず、各
  ポーリングのツリーの読み取りにはすべて `resolveAlerts=true` が付きます。TipKit のチップが出ているときに
  `dismiss_blocking_tip` がハンドルを得るために読み直す `/elements` は、フラグのないままです。runner の
  リクエストログで、その両方を確認できます。
- ゲートのファイルから、ネイティブのプローブとツリー内の消去がなくなっています。
- 通知の許可要求と「パスワードを保存」シートに続けて答える showcase のシナリオ
  （`demos/showcase/scenarios/save_password_browser.yaml`）が、iOS 18.6、26.3、26.4、26.5 で成功し続け
  ます。この 4 つは BE-0399 が実測したバージョンです。

## 詳細設計

作業は順に進めます。まず測り、次に runner 側を作り、そのあとで Python 側を作ります。旧来の経路は実機
での検証のあとで削除し、文書の更新もそれと同じ変更で行います。runner の変更はすべて、XCUITest の
[backend](../../docs/ja/glossary.md#driver-backend-actuator-platform) が通知する capability トークンの
内側に置きます。adb と Playwright の backend は今のゲートを使い続けるので、基本原則 3 は保たれます。

### Unit 1：作る前に測る（関門）

Unit 1 はプロダクトのコードを足しません。使い捨ての runner のビルドで、iOS 18.6、26.3、26.4、26.5 の
それぞれについて次の 4 点を測ります。

1. **`app.snapshot()` が割り込み監視を呼ぶかどうか**：`GET /elements` は毎回 `app.snapshot()` を呼び
   ます。スナップショットが割り込み監視を呼ぶなら、Unit 2 の SpringBoard の確認は不要になり、ツリー内の
   ルールを当てる処理だけが残ります。その場合はフラグのない `/elements` も SpringBoard のアラートに答える
   ので、Unit 2 に入る前に設計の 3 点を見直します。
   - 証跡のツリーのダンプが実際の画面を残すという、Unit 4 の前提。
   - ステップ自身のアラートに触れないために `resolveAlerts=inTree` に頼る、Unit 3 の前提。
   - 手順 1 だけが生む 2 つの出力。*unidentified* の記録（`alert_block_note` のもとになります）と、
     折りたたみツリーの代理指標が待機を早めに打ち切るための `springboardChecked` です。
2. **SpringBoard の確認にかかる時間**：`springboard.alerts.firstMatch.exists` を 1 回呼ぶ時間を測り
   ます。50 パーセンタイル値（p50）と 95 パーセンタイル値（p95）を記録します。この値で確認の間引き間隔を
   決めます。
3. **ボタンを押すときの `/elements` の所要時間**：BE-0399 の活動ログでは、1 回の押下に約 1.6 秒かかって
   います。観測した最長の `/elements` を、片付ける読み取りに与える 30 秒の操作の時間枠と比べます
   （Unit 2、`bajutsu/common/drivers/xcuitest/_functions.py` の `_ACTUATION_TIMEOUT_SECONDS`）。
4. **ステップあたりのコスト**：バージョンごとに同じホストで、showcase のスイートを基準の runner で 10 回、
   候補で 10 回走らせます。候補のステップ所要時間の中央値が基準の 5％以内で、p95 が 10％以内であれば
   合格とします。runner を落とした実行が 1 回でもあれば不合格です。

4 点目のしきい値は、レビューのための出発点です。測定値とあわせて本項目に記録します。

#### Unit 1 の結果

4 点は、2026-10-11 から 2026-10-12 にかけて、Apple シリコンのホスト 1 台で測りました。runner のビルドは
使い捨てで、リポジトリには入れていません。iOS のバージョンごとに、新しく作った専用の Simulator を
使いました。26.3、26.4、26.5 は iPhone 17 Pro です。18.6 は iPhone 17 Pro が対応していないので、
iPhone 16 Pro です。このビルドでは、`GET /elements` に 2 つのモードを持たせました。

- **probe モード**：スナップショットの前後で SpringBoard を確認し、ボタンは押しません。リクエストの各部分
  で割り込み監視が呼ばれた回数を数えます。アラートが出ている間は、スナップショットを 10 回繰り返し、
  SpringBoard の確認を 20 回計時します。
- **candidate モード**：スナップショットの前に Unit 2 の手順 1 を実行します。手順 1 は、1 秒に 1 回までに
  間引いた SpringBoard の確認と、渡されたネイティブの方針による押下です。candidate モードは証跡の読み取り
  を含むすべての `/elements` で片付けを行い、Python 側のゲートも自前の確認を続けます。したがって、以下で
  測ったコストは Unit 2 が足すコストの上限です。

どちらのモードでも、次の 3 つのシナリオを 4 バージョンで走らせました。

- `permission.yaml`
- `save_password_browser.yaml`
- `save_password_interrupts_step.yaml`

1. **`app.snapshot()` は、4 バージョンのどれでも割り込み監視を呼びません**。SpringBoard のアラートが出て
   いる間に、単発の読み取りを 11 回、繰り返しのスナップショットを 110 回取りました。どのバージョンでも、
   こうした読み取りを 2 回以上取っています。割り込み監視は一度も呼ばれず、どのスナップショットの後も
   アラートは残っていました。アラートが出ている間の SpringBoard の確認も、341 回のうち一度も割り込み監視を
   呼んでいません。

   したがって、これらのシナリオが出す通知の許可の要求については、フラグのない `/elements` は純粋な読み
   取りのままです。Unit 2 の手順 1 は必要です。この事実にかかっていた設計の 3 点は、見直さずに済みます。
   3 点とは、Unit 4 の証跡のツリーのダンプ、Unit 3 の `resolveAlerts=inTree` への依存、手順 1 の 2 つの
   出力です。
2. **SpringBoard の確認は、アラートが出ていないときのほうが高くつきます**。
   `springboard.alerts.firstMatch.exists` は、「ない」と答えるまでに SpringBoard のツリー全体を探します。
   アラートが出ていれば、最初に一致した時点で探索が終わります。「なし」の値は、2 つの出所を合わせたもの
   です。probe モードの実行と、4 点目の candidate モードの実行で間引かれたすべての確認で、バージョンごと
   に約 3,500 回です。

   | iOS | なし p50 | なし p95 | 表示中 p50 | 表示中 p95 |
   |---|---|---|---|---|
   | 18.6 | 68 ms | 156 ms | 40 ms | 68 ms |
   | 26.3 | 41 ms | 120 ms | 30 ms | 48 ms |
   | 26.4 | 40 ms | 109 ms | 31 ms | 57 ms |
   | 26.5 | 36 ms | 95 ms | 38 ms | 66 ms |

   1 秒に 1 回の確認では、「なし」の場合に runner のメインスレッドを p50 で 4〜7％の時間ふさぎます。
   4 点目の結果から、この割合は典型的なステップにはほとんど表れません。対応するステップの差の中央値で
   0〜3 ms、`scroll` で 28〜40 ms です。そこで Unit 2 では、`pollIntervalSeconds` のデフォルトを 1 秒の
   ままにします。
3. **ボタンを押す片付けの `/elements` は、30 秒の時間枠に十分収まります**。押下そのものは 0.7〜2.1 秒
   でした。押下を伴う `/elements` の最長は 3.1 秒で、iOS 26.5 の「Don’t Allow」のときです。

   押下を伴わない読み取りが 1 回、24.7 秒かかりました。iOS 26.5 の `save_password_browser.yaml` の実行中
   です。このうち SpringBoard の確認は 154 ms で、残りの 24.6 秒は SafariViewService の読み取りを含む
   スナップショットでした。フラグのない `/elements` も同じスナップショットを取ります。それでも、30 秒の
   操作の時間枠の余裕は、押下の所要時間から想像するより小さいとわかります。片付けの読み取りは、BE-0207
   のリトライの代わりにこの時間枠を受け取ります。
4. **対応するステップのコストは約 1％にとどまり、候補の 1 回で runner が失われました**。バージョンごと
   に、showcase のスイートを runner ごとに 10 回、基準と候補を交互に走らせました。スイートは
   `make run-swiftui` と同じタグの選び方で、`select_photos.yaml` だけを外しました。iOS 18.6 では、この
   ファイルの写真の投入（`simctl addmedia`）がこのホストでタイムアウトし、実行全体を止めたためです。
   基準で毎回失敗するシナリオは、候補でも同じように失敗しました。候補でだけ失敗の増えたシナリオも
   1 つあり、表の後で説明します。

   | iOS | 中央値（基準 → 候補） | p95（基準 → 候補） | 対応するステップの差の中央値 | runner の喪失 |
   |---|---|---|---|---|
   | 18.6 | 0.346 s → 0.327 s（−5.6％） | 2.937 s → 2.894 s（−1.5％） | −0.4 ms | 0 / 0 |
   | 26.3 | 0.351 s → 0.352 s（+0.4％） | 3.337 s → 3.373 s（+1.1％） | +0.9 ms | 0 / 0 |
   | 26.4 | 0.333 s → 0.305 s（−8.4％） | 3.220 s → 3.204 s（−0.5％） | +1.8 ms | 0 / 0 |
   | 26.5 | 0.243 s → 0.234 s（−3.5％） | 3.009 s → 2.966 s（−1.4％） | +3.3 ms | 0 / 1 |

   全ステップを合わせた中央値は、候補が有利な方向に最大 8％動きました。確認を足してステップが速くなる
   ことはないので、この振れは、各実行がどのステップを完了したかの違いを測っているにすぎず、候補のコスト
   ではありません。このノイズの大きさでは、全体の中央値に対する 5％の基準で実際の変化を見分けられません。
   そこで判定は、対応するステップの差の列と、2％以内の p95 に基づきます。「対応するステップの差」の列は、
   同じ回の同じシナリオの同じステップどうしを組にしたものです。この列では、候補のコストはステップ
   あたり 0〜3 ms で、中央値のステップの約 1％です。一貫したコストが出た操作は `scroll` だけで、
   28〜40 ms でした。scroll は 1 ステップのなかで `/elements` を何度も読むためです。

   runner の喪失は、「runner を落とした実行が 1 回でもあれば不合格」という基準に反します。iOS 26.5 で、
   `swipe` の最中に通知バナーが割り込みました。XCUITest の割り込み処理がバナーのスナップショットに失敗し
   （「Automation type mismatch」）、テストケースが終了しました。この経路は BE-0416 のバナー処理で、基準の
   runner も同じものを持っています。喪失の直前に候補が返した `/elements` は正常でした。40 回ずつの実行で、
   基準は runner を一度も失わず、候補は 1 回失いました。この 1 回が Unit 2 を止めるかどうかは、レビューで
   判断します。私たちは、片付けのコストではなく、計測で表に出た既存のバナーの不具合と読んでいます。

   iOS 26 では、候補でだけ失敗の増えたシナリオが 1 つありました。`codegen_android.yaml` の最初の
   シナリオで、最初のステップが待たずに `{ id: search }` をタップします。基準ではバージョンごとに 10 回中
   1〜2 回、候補では 10 回中 3〜7 回失敗しました。iOS 26 の Search タブは、識別子 `Search` を持つツリーと
   持たないツリーがあります。このため、そのタップは 1 回きりの読み取りが識別子の残っているツリーに当たった
   ときだけ通ります。

   iOS 26 では、候補の SpringBoard の確認がスナップショットを p50 で約 40 ms 遅らせるので、この競争に負け
   やすくなると考えられます。このシナリオは UI Automator のコード生成用のフィクスチャで、iOS での実行は、
   片付けの有無を問わずこの競争に依存しています。それでも Unit 2 は、SpringBoard の確認の分だけスナップ
   ショットの時刻をずらします。最初に描画されたツリーと競争するシナリオは、その影響を受けます。

### Unit 2：アラートを片付ける `/elements`

`GET /elements` に、クエリパラメータ `resolveAlerts` を加えます。値は `false`（デフォルト）、`true`、
`inTree` のいずれかです。フラグのない読み取りは、純粋な読み取りのままです。`true` のとき、ハンドラは
runner の直列の操作キュー `APIHandler.operations` の上で、次の 4 つの手順を踏みます。

1. **SpringBoard を確認します**：`springboard.alerts` の確認は、`pollIntervalSeconds` に 1 回までに
   間引きます。この間隔は、オーケストレータが `systemAlertHandling.pollInterval`（デフォルトは 1 秒）から
   渡します。拒まれた操作や失敗した判定のあとの立て直しの読み取り（Unit 5）は、この間引きを飛ばします。
   `resolveAlerts=true` は付随するフラグ `forceCheck` を受け取り、Unit 5 の立て直しの読み取りはすべて
   これを立てます。
   - アラートが出ていて `InterruptionPolicy.label(for:)` がボタンを選べば、ハンドラはそれをタップして
     *answered*（応答済み）を記録します。
   - どのルールもボタンを選ばなければ、何もタップせず、確認のたびに *unidentified*（未識別）を記録します。
   - ネイティブのルールの表示は、一致したルールの識別ラベルをキーにします。そのため、1 回の表示は
     *answered* として 1 回だけ記録します。押し直すのは、あとで述べる間合いのメモに従うときだけです。
   - のちの確認でその表示が見つからなくなったときに、キーは次の表示に備えます。
   - 押下のあとに、アプリへの操作が完了したときにも、キーは次の表示に備えます。対象は runner が合成する
     操作で、`/tap`、ジェスチャ、文字入力、手順 3 のツリー内のタップです。XCUITest は、アラートに
     妨げられなくなってからこうした操作を合成します。
   - SpringBoard のアラート自身のボタンを押す操作は、キーを次の表示に備えさせません。手順 1、割り込み
     監視、`/systemAlert/resolve`、`/systemAlert/tap` による押下がこれにあたります。XCUITest は、
     アラートが出ているあいだにこの押下を合成するからです。割り込み監視が割り込んだ操作は、完了した
     時点でキーを次の表示に備えさせます。
   - この区別がないと、届かなかった押下が自分のキーを次の表示に備えさせます。同じ表示が再び *answered*
     と記録され、タップ回数の上限に達しなくなります。
   - 拒まれた操作（`not-found`、`not-hittable`、`stale`）は何も合成していないので、キーを次の表示に
     備えさせません。
   - 2 つ目の契機は、ツリー内のキーを次の表示に備えさせることはありません。ツリー内のアラートは合成を
     妨げないからです。割り込み監視が割り込んだ操作は、監視の押下のあとに完了します。割り込まなかった
     操作が完了したなら、アラートはすでに消えていたことになります。
   - 2 つ目の契機がないと、2 回の確認のあいだに再び出るプロンプトが、最初の表示がまだ出ていると読まれて
     しまいます。たとえば、貼り付けのたびに出る貼り付けのプロンプトです。監視はそのプロンプトに
     *answered* を記録せず、その押下は最初の表示の上限に数えられます。
   - SpringBoard の *answered* の記録は、runner がボタンを押した時点ですぐ付きます。アラートは、押し終える
     ころには消えているのがふつうです。届かなかった押下は、メモに従って押し直します。
2. **アプリのスナップショットを取ります**：フラグのない `/elements` と同じ処理です。
3. **ツリー内のルールを当てます**：渡されたツリー内のルールは、次の 2 つが成り立つときに一致します。
   識別ラベルが、スナップショットのボタンのうち identifier を持たずラベルを持つもの（現在
   `bajutsu/common/drivers/elements.py` の `tree_buttons` が作る集合）にちょうど 1 回ずつ現れること。
   除外ラベルが 1 つも現れないこと。ハンドラはルールを受け取った順（Python は `tree_dedup_rules` の順に
   送ります）に試すので、入れ子の形では広いほうの兄弟が先に来ます。
   - 一致すれば、まず間合いのメモを参照します。メモがタップを許すときに限って、同じハンドラの中で
     SpringBoard を確認し直します。
   - この確認し直しは、手順 1 と同じ `pollIntervalSeconds` の間引きに数えます。ただし、同じリクエストの
     手順 1 が SpringBoard へ問い合わせたときは、必ず確認し直します。
   - メモが見送る一致や、辞退する一致、間引きの間隔のうちに当たった一致では、SpringBoard へ問い合わせず、
     ツリー内のアラートをあとのリクエストに任せます。
   - 確認し直して SpringBoard のアラートが出ていなければタップします。出ていれば、ツリー内のアラートを
     あとのリクエストに任せます。
   - タップしたあとは、スナップショットを取り直します。ツリー内のタップは、タップした時点で *answered* を
     記録します。これは、runner がこのボタンを押したという意味です。
4. **応答を返します**：応答には、ツリーと、このリクエストで drain した記録が載ります。`/tap` もすでに
   同じ形で、drain した記録を応答に載せています（BE-0407 の Unit 6、`APIHandler.swift`）。応答には、
   何も起きなかったときも空の記録の欄を必ず載せます。応答には、さらに 2 つの事実を載せます。
   - `springboardChecked`：この応答自身の手順 1 が SpringBoard を調べてアラートを見つけず、手順 3 の
     確認し直しを行った場合はそこでも見つけなかったときに、`true` になります。
   - `withheld`：すでにキーのある表示を、再タップまでの遅延のうちだったために間合いのメモが押さずに
     見送ったときに、`true` になります。先に押したのは、片付ける読み取り、割り込み監視、
     `/systemAlert/resolve` のどれでも構いません。

   `resolveAlerts` を付けた読み取りの応答に記録の欄がなければ、ドライバは `XcuitestChannelError` を
   送出します。古い runner のビルドが黙って普通のツリーを返すことはありません。

手順 3 の確認し直しは、今のゲートが守っている順序を保ちます。SpringBoard のアラートが出ている最中に
XCUITest でタップすると、割り込み監視がそのアラートへ先に答えてしまうからです。

**間合い**：ネイティブの表示とツリー内の表示は、同じ間合いのメモを使います。メモは
`InterruptionPolicyStore` の中の表示ごとの小さなメモで、次の 3 つの値を持ちます。

- 再タップまでの遅延です。
- 表示 1 回あたりのタップ回数の上限です。
- 諦めるまでの上限です。表示されているのにまだ押せない（表示途中の幕に覆われた）ツリー内のボタンを
  試し続け、その表示に *gave up* を記録するまでの時間で、今の `_decline_giveup` が決めている上限です。

これらの定数は、`waits/_alert_guard_gate.py` と `waits/_functions.py` から
`bajutsu/common/orchestrator/types/alert_guard_config.py` へ移し、オーケストレータが runner へ渡します。
諦めるまでの上限は、引き続き Python 側で `poll_interval` から導きます。ツリー内のタップは、今の間合いを
保ちます。

- ネイティブの表示は、再タップまでの遅延を過ぎてなお次の確認で見つかれば押し直します。押し直しは
  表示 1 回あたりのタップ回数の上限で止め、その表示に *gave up* を記録します。
- 上限に達したツリー内の表示も、今の `_TREE_DISMISS_MAX_TAPS` による断念と同じく、*gave up* を
  記録します。
- 割り込み監視も、同じ表示ごとのキーを参照して更新します。ただし、キーで決めるのは記録するかどうかだけ
  です。割り込み監視は、再タップまでの遅延のうちでも、タップ回数の上限を超えたあとでも、ルールが選んだ
  ボタンを必ず押します。割り込み監視が辞退すると、そのアラートは XCUITest の既定の処理に渡り、既定の
  ボタンで答えられてしまうからです（`RunnerUITest.swift`）。
- すでにキーのある表示を監視が押しても、新しい *answered* は作りません。そのため、1 回の表示に
  *answered* は最大 1 件という規則は、どの経路でも成り立ちます。

ツリー内の表示は、ルールではなく、今の `_tree_dismiss_pending` と同じくタップしたラベルをキーにします。
`savePassword` の入れ子の形はどれも "Not Now" をタップするので、ボタンの一部だけが残った消えかけの
シートには狭いほうの兄弟が一致します。ルールをキーにすると、再タップまでの遅延のうちにその兄弟を
もう一度タップし、2 件目の *answered* を記録してしまいます。

ツリー内のルールのメモは、タップした時点のツリーの署名（ラベルと identifier の組）も、今の
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
  ラベルと、ルールの種類と、ルールが選んだボタンを持ちます。`uncleared_prompt_note` はそのボタンを
  挙げ、押しても消えなかったことを作者に伝えます。
- そのため、1 回の表示に残る記録は、*answered* が最大 1 件、*gave up* が最大 1 件です。

**方針の受け渡し**：`InterruptionPolicyStore.setPolicy` は、受け渡しのたびに保留中の記録を消します。
シナリオの途中では、Python が受け渡しの前に drain します。シナリオ開始時の受け渡し
（`bajutsu/common/runner/pipeline.py`）は、前のシナリオの記録を意図して捨てます。

- シナリオ開始時の受け渡しは `scenarioStart` の印を持ち、表示ごとのキーと間合いのメモも消します。
  そのため、前のシナリオで答えたり控えたりしたことが、再起動後の同じプロンプトを妨げることはありません。
- シナリオの途中の受け渡し（`_reserve_declared_alert` とその復元）は、キーとメモを残します。その
  受け渡しをまたぐ表示を、早すぎるタイミングで押し直すことも、2 回記録することもありません。
- キーは、そのアラートが消えたときに次の表示に備えます。ネイティブのキーは、押下のあとに合成した操作が
  完了したときにも備えます（Unit 2 の手順 1）。

**通信形式**：通信形式には、ルールごとに省略可能な `exclude` の一覧と、ルールの種類（`ResolvedAlertRule`
由来の `native` か `inTree`）を加えます。方針全体にも、`pollIntervalSeconds` と、先に挙げた間合いの
3 つの値と、`scenarioStart` の印を加えます。

- `ResolvedAlertRule` は `native` と `in_tree` を独立した 2 つのフラグとして持ちます。今は両方を立てる
  プロンプトはありません。両方を立てたルールが来たときは、片方の種類だけで渡すのではなく、
  `push_interruption_policy` が `ValueError` を送出します。除外の組を持つネイティブに届くルールに
  対しても、すでに同じく送出します。
- `push_interruption_policy`（`bajutsu/common/orchestrator/types/_functions.py`）は、ツリー内のルールを
  渡す前に落とすのをやめ、`AlertGuardConfig.tree_dedup_rules` の順に送ります。runner は受け取った順を
  保つので、`_widest_first` は Python 側にだけ残ります。
- 割り込み監視と手順 1 の SpringBoard の確認は、`InterruptionPolicy.label(for:)` で `native` のルールだけを
  照合します。手順 3 のツリー内の照合は `inTree` のルールだけを使い、`exclude` を守ります。
- 除外の組を持つルールを拒む処理は、ネイティブに届くルールに対しては残します。そうしたルールは割り込み
  監視の部分一致に届き、そこで除外が捨てられてしまうからです。
- 前面に出る通知バナーは、BE-0416 のスワイプの経路のままです。

**送り直しの可否**：`resolveAlerts` を付けた読み取りはボタンを押すことがあるので、冪等ではありません。
BE-0207 では、配信後に応答が時間切れになった読み取りを、ドライバが送り直せます。`resolveAlerts` を付けた
読み取りは、ドライバが書き込みと同じに扱います。配信されなかったリクエストは送り直し、配信後の時間切れは
送り直さずに失敗として報告します。

- 変更先は `_is_retry_eligible`（`bajutsu/common/drivers/xcuitest/_functions.py`）です。この関数は今、
  `GET` をすべて冪等とみなしています。BE-0287 のクラッシュ復旧での再送も同じ関数で判定するので、
  1 か所の変更で両方に効きます。
- 同じ理由で、`_timeout_for` は `resolveAlerts` を付けた読み取りに、書き込みと同じ 30 秒の操作の時間枠
  （`_ACTUATION_TIMEOUT_SECONDS`）を与えます。今は、送り直しに頼れることを前提に `GET` すべてへ 15 秒の
  読み取りの時間枠を与えています。

### Unit 3：`POST /systemAlert/resolve` エンドポイント

`POST /systemAlert/resolve` は、表示中の SpringBoard のアラートに、渡された方針を当てます。
`handleSystemAlert` ステップは、自分のもの以外のアラートへ答えるときにこれを使います。

このエンドポイントは、`/tap` と同じく runner の記録を drain して応答に載せます。ドライバはその応答を
`_drain_carry` に入れるので、その答えを消費した drain が、答えを 1 回だけ報告します。その drain とは、
ステップの待機中の `_policy_answered_alert` です。応答の記録は、Unit 4 の `query_resolving` の記録と
同じく、ゲートにも届きます。

`_policy_answered_alert` はこの分岐に残り、`resolve_system_alert` のあとに走ります。そのため、監視が
ポーリングの合間に答えたステップ自身のアラートは、これまでどおりステップを先へ進ませます（BE-0406 の
Unit 2b）。セレクタの照合が数えるのは、ネイティブの答えだけです。ツリー内の *answered* の記録は、
drain を通ってもルールの種類を保ちます。そのため、ステップのラベルと同じラベルを持つアプリ側のボタンは、
無関係なアラートとして報告され、ステップを通すことはありません。

ステップは自前の `/systemAlert/query` のポーリングを続けますが、これは何も片付けません。ガードが効いて
いる状態では、ボタンをラベルで指定するステップは、`query_resolving(inTree)` を通してツリーの読み取りも
行います。次の `resolve_system_alert` を呼ぶのは、この種類のステップだけです。位置で答えるステップ
（BE-0445）とガードを切ったステップは、ツリーの読み取りも `/systemAlert/resolve` も送りません。今も
ゲートなしで待つからです。

`inTree` のモードは、Unit 2 の手順 2 から 4 を実行し、手順 1 の SpringBoard の確認を飛ばします。そのため、
Save Password のシートのようなアプリ側のアラートは、ステップが待つあいだも片付きます（BE-0406）。
手順 3 の SpringBoard の確認し直しはステップ自身のアラートを見つけ、ツリー内のタップを見送ります。
今の `probe_native` の `"reserved"` という答えと同じ動きです。

ステップが見たアラートがステップのセレクタに一致しないとき、Python は既存の `selector_names_button`
（`bajutsu/common/orchestrator/types/_functions.py`）でそれを判断します。そのうえで Python は
`resolve_system_alert` を呼び、これが `/systemAlert/resolve` を送ります。

- Python がこの呼び出しを行うのは、`systemAlertHandling.pollInterval` に 1 回までです。これは
  `probe_native` が今も保っている頻度です。呼び出しのたびに SpringBoard への問い合わせが起きるので、
  0.2 秒ごとの `/systemAlert/query` のポーリングのたびに呼ぶと、ステップが待っていないアラートが画面を
  占めるあいだ、ステップの問い合わせ頻度が倍になります（BE-0315）。
- 呼び出しを飛ばしたポーリングでも、`_policy_answered_alert` は今までどおり走ります。
- ステップが自分のアラートを確保する扱いは、リクエストごとの選択になります。今の方針の受け渡しのほかに、
  runner が持つ状態は増えません。
- `handleSystemAlert` ステップが `_reserve_declared_alert`（`loop/_step_runner.py`、BE-0406 の Unit 2b）で
  行う方針の受け渡しと、ステップのあとの復元は、`RESOLVE_ALERTS` のもとでも変わりません。
- セレクタの照合は Python に残るので、Swift への移植は要りません。

リクエストは、ステップの直近の `/systemAlert/query` が見たボタンのラベルの組を運びます。runner が答える
のは、いま出ているアラートがちょうどそのラベルの組を持つときだけです。そうでなければ `changed` を返し、
何もタップしません。そのため、Python が判断したアラートの代わりに届いたステップ自身のアラートに、方針が
答えてしまうことはありません。

このエンドポイントは、Unit 2 の表示ごとのキーと間合いのメモを共有します。

- すでに答えた表示は、再タップまでの遅延のあとにだけ、表示 1 回あたりの上限まで押し直し、そのあと
  *gave up* と記録します。押し直しは新しい *answered* を作りません。
- メモが見送った呼び出しは、何もタップせず、5 つ目の応答 `pending` を返します。そのため、ステップが
  見送られた呼び出しを押下と読むことはありません。
- アラートが出ていなければ、`absent` を返します。
- どのルールにも当たらないアラートを見つけたときは、*unidentified* を記録し、`unidentified` を返し、
  何もタップしません。`handleSystemAlert` ステップは、今までどおり待ち続けます。

### Unit 4：Python 側の薄い経路

待機ループは、狭いドライバのプロトコル `AlertResolvingTarget` を通して新しい動作に届きます。このプロトコルは
`bajutsu/common/drivers/base/` にあり、`InterruptionPolicyTarget` の隣に置きます。メソッドは 2 つです。

- `query_resolving(mode, force_check=False)` は `true` か `inTree` を受け取り、要素と、その応答の記録を
  あわせて返します。
- `resolve_system_alert(labels)` は、ステップが直近に見たラベルの組を付けて、Unit 3 の
  `POST /systemAlert/resolve` を送ります。状態（`answered`、`changed`、`absent`、`unidentified`、
  `pending`）と、その応答の記録をあわせて返します。

`RESOLVE_ALERTS` のトークンは、このプロトコルを実装するバックエンドを示します。`Driver.query` と、ほかの
すべてのバックエンドは変わりません。証跡の採取も `Driver.query` を呼び続けます。

ゲートは、`resolve_system_alert` の記録を `query_resolving` の記録と同じ方法で読みます。そのため、
`handleSystemAlert` の待機中に `/systemAlert/resolve` が返した *gave up* や *unidentified* の記録は、
ノート（`uncleared_prompt_note` か `alert_block_note`）としてタイムアウトに届きます。`_with_block_note` が、
今のゲートのノートと同じく、そのノートをステップ自身のタイムアウトの理由に付け足します。理由は、ステップの
セレクタが逃したボタンを名指しし続けます。

ドライバは、2 つの応答の記録を `_drain_carry` にも入れます。ゲートは何も消費せずに返された記録を読み、
記録を消費する drain が、それぞれを 1 回だけ報告します。

`RESOLVE_ALERTS` を宣言するバックエンドでは、ガードが効いている待機ループとセレクタの解決が
`query_resolving(true)` を呼び、`GET /elements?resolveAlerts=true` を送ります。例外は
`handleSystemAlert` ステップです。

- ここでのセレクタの解決とは、アクションのハンドラが、操作の前に対象を解決するために `Driver.query()`
  を通して行う読み取りです。`scroll.py` の読み取りと、`gestures.py` の中で `_do_clear` がフォーカスの
  タップの前に行う値の読み取りと `_directional_endpoints` が該当します。
- ハンドルを使う操作（`tap`、`double_tap`、`long_press`、`pinch`、`rotate`、`set_picker_value`）は、
  ハンドルを発行するフラグなしの `/elements` の中で対象を解決します。自前の片付ける読み取りは送りません。
- ガードが効いているとは、ステップに設定された `systemAlertHandling` のガード（`_LoopConfig.alert_guard`）
  のことで、読み取りをどの本体の実行が行っても同じです。
- TipKit のリトライとアラートのリトライは、今は待機中のゲートなしでステップの本体を再実行します。
  この機能のもとでも片付ける読み取りを送ります。片付ける読み取りは、再実行が次の表示に備えさせるような
  状態を持たないからです。
- この規則は、`screenChanged` を含むガード付きのすべての待機に及ぶので、画面の凍結による停止も働き
  続けます。

[証跡](../../docs/ja/glossary.md#証跡-capturepolicy-trace-triage)のスクリーンショットとツリーの
ダンプはフラグを立てないので、証跡には実際の画面が写ります。ガードを切ったシナリオは `Driver.query` を
呼び続けるので、片付ける読み取りを送りません。

ゲートは、数十行と、残す折りたたまれたツリーの代理判定にまで縮みます。`query_resolving` が返す記録を読み、
ステップの状態に保ちます。ブロックされた画面のノートは、2 種類の記録から作ります。

- *unidentified* の記録からは、どのルールも識別しないボタンを挙げる `alert_block_note` を作ります。
- *gave up* の記録からは、ルールが選んだものの消せなかったボタンを挙げる `uncleared_prompt_note` を
  作ります。

どちらの記録から作ったノートも、折りたたまれたツリーの代理判定が作るラベルのないノートより優先します。
代理判定がそのノートを消すことはなく、今の `_native_unhandled` と `_tree_gave_up` のラッチもこれを保証して
います（BE-0402）。

- *unidentified* のノートは、応答の `springboardChecked` が true になるまで残ります。
  `handleSystemAlert` の待機中は、ステップ自身の `/systemAlert/query` がノートの挙げるボタンをもう読めなく
  なったときにも終わります。
- *gave up* のノートは、挙げているアラートが消えたと示されるまで残ります。今の `_tree_gave_up` のラッチが
  解けるのと同じです。
  - ツリー内の記録では、応答のツリーがアプリを示し、宣言されたツリー内の形のどれも記録の識別ラベルと入れ子に
    ならなくなったときに、アラートが消えたことになります。入れ子とは、一方が他方を含むことです。その形が
    数えられるのは、識別ラベルがすべて `tree_buttons` に現れるときだけです。これは今、
    `_tree_gave_up_shape_matches` が判定しています。そのため、狭いほうの兄弟の形だけで再び出るシートは、
    ノートを保ちます。
  - ネイティブの記録では、*unidentified* のノートを解く条件と同じ条件でアラートが消えたことになります。

ゲートは、今の折りたたまれたツリーの代理判定を、`springboardChecked` の上に保ちます。このフラグは同じ
リクエストの事実であり、保存した許可証ではありません。ツリー内のルールを宣言したシナリオで、ツリーが
`frozenScreenTimeout` のあいだ折りたたまれたままになると、`springboardChecked` が true の折りたたまれた
応答は、`collapsed_tree_note` で行き詰まりのノートを設定し、待機を早めに打ち切ります。フラグが false の
応答は、これを設定しません。今のネイティブの確認の "absent" も、iOS 26.5 の Save Password のシートが
表示の途中で止まった場合に、同じことをします。

`handleSystemAlert` の待機中は、ステップ自身の `/systemAlert/query` がセレクタの挙げるアラートを読んで
いるあいだ、代理判定は動きません。今の `probe_native` の `"reserved"` という答えが代理判定を止めて
いるのと同じです。そのため、自分のアラートが出たままタイムアウトするステップに、ラベルのないノートが
付くことはありません。

各記録は、それを消費する drain が 1 回だけ報告します。ステップ終了時の drain
（`bajutsu/common/orchestrator/loop/_step_runner.py` の `_drain_step_interruptions`）が唯一の報告者です。
ただし、今も役割を保つ 4 つの drain は別です。

- `handleSystemAlert` の待機の `_policy_answered_alert`（`waits/_functions.py`）
- `expect` フェーズの drain（`loop/_functions.py`）
- `_reserve_declared_alert` の、受け渡しの前の drain
- 同じステップの `finally` で、復元の受け渡しの前に行う drain（どちらも `loop/_step_runner.py`）

ステップ終了時の drain は、辞退された記録のあるステップを名指しで失敗させます（BE-0406）。今と同じです。
*unidentified* の記録はブロックされた画面のノートに回り、ステップを動かし続けます。そのため、あとの
`handleSystemAlert` ステップでプロンプトに答えるシナリオは、動き続けます。

ドライバは、各応答の記録を既存の `_drain_carry` に入れます。片付ける `/elements` は runner の記録の置き場所
全体を drain するので、その応答は、`/tap` の応答と同じく、carry を最新の状態にします。ほかの呼び出しは、
`is_current` を消し続けます。割り込み監視は XCUITest のどの操作の最中にも記録し、それらの応答は何も運ばない
からです。対象は、フラグのない `/elements`（Unit 1 がスナップショットでも監視が動くと示した場合は、
これも監視を動かします）、ジェスチャ、文字入力です。この規則のもとでは、`drain_interruptions()` の
carry だけで済ませる近道も有効なままです。

Python は 2 つのノートの文面を既存の `alert_block_note` と `uncleared_prompt_note`
（`bajutsu/common/orchestrator/types/_functions.py`）で組み立てるので、文言の置き場所は 1 か所のままです。

### Unit 5：範囲を狭めたリトライ

操作のあとで 1 ステップに 1 回だけ行うアラートのリトライは、操作の前にどの片付ける読み取りも消さなかった
アプリ側のアラートを対象にします。当てはまるのは次の 2 つの場合です。

- アラートが、直近の片付ける読み取りのあとに現れた場合。その読み取りは、数ステップ前のガード付き待機の
  ポーリングかもしれません。ハンドルを使う操作が、自前の片付ける読み取りを送らないからです（Unit 4）。
- SpringBoard のアラートが出ていたために、片付ける読み取りがそのアラートをあとのリクエストに任せた場合
  （Unit 2 の手順 3）。

この隙間にある SpringBoard のアラートは、たいていリトライを要しません。タップを合成するときに監視が
答えるからです。`not-hittable` の拒否は `tap()` が走る前に返る（`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`）
ので、次の立て直しの読み取りは SpringBoard の確認を強制します。

**操作のあとの立て直し**：立て直しは、ガードが効いている状態で、次の 3 つのいずれかを契機に走ります。

- runner からの確定的な `not-found` か `not-hittable` の拒否
- ハンドルを使う操作（`tap`、`double_tap`、`long_press`、`pinch`、`rotate`、`set_picker_value`）が、
  リクエストを送る前、または古くなったハンドルを再解決するあいだに、自前のフラグなしのハンドル読み取りから
  送出する `ElementNotFound`
- アクションのハンドラが、セレクタ解決の読み取り（Unit 4）から送出する `ElementNotFound`

契機が起きると、ステップは `query_resolving(true, force_check=True)` でセレクタを解決し直します。これは
SpringBoard の間引きを飛ばす、片付ける `/elements` です。そのあとステップは、既存の
`settle_after_alert_dismiss`（`waits/_functions.py`）で画面が落ち着くのを待ちます。今の
`AlertGuardConfig.__call__` のラウンドと同じです。待つのは、次の 3 つのいずれかの場合です。

- 応答の記録が答えを示している。
- 応答が `withheld` を設定している。
- 失敗した本体の実行が行った片付ける読み取り（ガード付き待機のポーリングか、セレクタ解決の読み取り）が、
  *answered* の記録を返した。

最後の場合は、本体自身の読み取りが答えたアラートが、立て直しの読み取りの時点で消えているときを含みます。
そのとき立て直しの読み取りだけでは、答えも `withheld` も見えません。

落ち着いたあと、ステップは下で述べるステップ終了時の位置で、本体をもう一度実行します。本体を再実行する
かどうかにかかわらず、応答の記録がブロックされた画面のノートを供給します。そのため、名前のないアラートの
下で拒まれたタップは、素の `not-hittable` ではなく、そのノート付きで失敗します。結果が不明な書き込みの
あとには、この立て直しを行いません。2 回目の配信が二重の操作になりうるからです（BE-0207）。

**失敗した判定のあとの立て直し**：失敗した判定にも、同じ種類の 1 回きりの立て直しを残します。`assert`
ステップ、`expect` フェーズ、`extract`、`if` は `Driver.query` を通した読み取りを続けるので、判定の
最中に読み取りが画面を変えることはありません。

- ガードが効いている状態で `assert` ステップか `expect` フェーズが失敗すると、ステップは
  `query_resolving(true, force_check=True)` を 1 回送ります。これは SpringBoard の間引きを飛ばします。
- その応答が答えを示すか `withheld` を設定していれば、ステップは `settle_after_alert_dismiss` で待ち、
  判定をもう一度実行します。失敗した `assert` は、下で述べるステップ終了時の位置でステップの本体を
  再実行します。失敗した `expect` は、自分のフェーズで評価し直します。
- 判定を再実行するかどうかにかかわらず、応答の記録が、今は `__call__` が供給するブロックされた画面の
  ノートを供給します。
- `extract` は、今と同じく立て直しを行いません。`extract` はステップ終了時の位置とその drain のあとに
  走る（`loop/_step_runner.py`）ので、そこで立て直すと、記録が次のステップの drain に残ります。状態を
  変えるステップの本体を再実行して `extract` を繰り返すと、操作が二重になります。
- `if` の条件は失敗しないので、立て直しを行いません。今と同じく、アラートが偽にした条件は `else`
  の枝に進みます。

`expect` フェーズは最後のステップ終了時の drain のあとに走るので、立て直しの読み取りと再実行のあとにも
もう一度 drain します。今の 2 回目の drain と同じです。そのため、立て直しの記録は、このシナリオの中で
報告されます。どの方針の受け渡しも消さない `_drain_carry` に残って、次のシナリオの最初の drain を待つこと
はありません。

**走らなくなる経路**：この機能のもとでは、ほかの 2 つの経路も止めたままにします。`AlertGuardConfig.__call__`
のステップ終了時のリトライ（BE-0418）と、`expect` の経路が行う `__call__` 自身の呼び出し
（`bajutsu/common/orchestrator/loop/_functions.py`）です。

- 拒まれた操作と失敗した `assert` の立て直しは、今 `__call__` が占めるステップ終了時の位置で、TipKit の
  リトライのあとに走ります。ステップの本体を 1 回再実行します。
- この立て直しはステップの本体の中では走らないので、TipKit のリトライの 2 回目の本体実行が、2 回目の
  立て直しを使うことはありません。そのため、拒まれた操作に続くか失敗した判定に続くかにかかわらず、
  1 ステップのアラートのリトライは最大 1 回です。
- `_dismiss_blocking_tip`（`loop/_step_runner.py`）のあとの TipKit のリトライは変わりません。
- 位置で答える `handleSystemAlert` ステップ（BE-0445）はゲートなしで待ちます。今、失敗したそのステップが
  何が画面を塞いだかを知るのは、ステップ終了時の位置の `__call__` だけです。ガードが効いている状態では、
  このステップが同じ位置で同じ立て直しを行います。`query_resolving(true, force_check=True)` を 1 回送り、
  その記録がブロックされた画面のノートを供給します。答えか `withheld` があれば、
  `settle_after_alert_dismiss` で待ち、ステップの本体を 1 回再実行します。
- ボタンをラベルで指定する、失敗した `handleSystemAlert` ステップは、今と同じくステップ終了時の立て直しを
  行いません。自分の待機がすでにガードを動かしたからです。

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

実機での検証が済んだら、Unit 2 から Unit 5 が置き換えたものを Python 側から削除します。折りたたみツリーの
代理指標は残すので、削除するのはネイティブ確認とツリー内の消去だけです。

- 対象は `probe_native` と `_observe_native`、そして `AlertGuardConfig.__call__` の各ラウンドの両方の
  半分です。
- `HANDLE_SYSTEM_ALERT` を持たない backend では、`probe_native` が `"incapable"` を返し、ラウンドは
  `dismiss_from_tree_once` に届く前に終わります。そのタップを許すのは本当に空の `"absent"` の読み取り
  だけなので、そうした backend は今もツリーをタップしません。XCUITest が `RESOLVE_ALERTS` に移れば、
  ツリーの半分を動かす backend はなくなるので、これも削除します。
- そのうえで Unit 7 は、`__call__` に何が残るか（ほかの backend がまだ必要とする注記など）を確かめ
  ます。何も残らなければ `__call__` そのものを削除します。
- ラッチとタップの許可条件を持つ `_dismiss_from_tree` と、`waits/_functions.py` のアラートの受け渡しも
  対象です。

旧来の経路を戻すフラグは設けません。2 つ目の切り替えは、1 つの振る舞いに 2 つ目の語彙を持ち込むから
です。

capability のない backend は、今のゲートを使い続けます。明示的な `handleSystemAlert` ステップは、
`/systemAlert/query` と `/systemAlert/tap` を使い続けます（BE-0316）。

### Unit 8：検証

- **Swift**：`BajutsuKit/Tests/BajutsuRunnerTests/` の `FakeElementProvider` で、Simulator なしに
  `resolveAlerts` のハンドラを動かします。テストの対象は次のとおりです。
  - ハンドラの手順の順序と、SpringBoard の確認の間引き。
  - `forceCheck` が間引きを飛ばすことと、`inTree` が手順 1 を飛ばすこと。
  - SpringBoard のアラートが出ているあいだ、手順 3 の確認し直しがツリー内のタップを控えること。
  - 除外ラベルと、受け取った順のままの照合（Swift 側では並べ替えません）。
  - 確認のたびの *unidentified* の記録と、ネイティブの表示を 1 回だけタップして記録すること。
  - 割り込み監視が呼ぶ `InterruptionPolicyStore` の入口。すでにキーのある表示について、再タップまでの
    遅延のうちでも上限を超えたあとでも「押すが記録しない」と答えます。割り込み監視の本体
    （`RunnerUITest.swift`）は、これらのテストがビルドする `BajutsuRunner` のパッケージのターゲットの
    外にあります。そのため、判断はこの入口に置きます。
  - ネイティブのキーの備え直し。押下のあとに操作が完了すると、割り込み監視が割り込んだかどうかにかかわ
    らず、キーが次の表示に備えます。次の確認より前に再び出たプロンプトは、表示のたびに *answered* として
    記録されます。
  - 備え直しが起きない場合。拒まれた操作、SpringBoard のアラート自身のボタンを押す操作（手順 1、割り込み
    監視、`/systemAlert/resolve`）、ツリー内のキーに対するどの操作も、キーを備えさせません。そのため、
    届かなかったネイティブの押下は、上限と *gave up* に達します。
  - 間合いのメモと *gave up* の記録。再タップまでの遅延を過ぎても残るネイティブの表示は、上限まで押し
    直され、そのあと *gave up* として記録されます。
  - `/systemAlert/resolve` が間合いのメモを共有し、上限まで押し直し、見送った呼び出しに `pending` を返す
    こと。
  - `POST /systemAlert/resolve` の `answered`、`changed`、`absent`、`unidentified`、`pending` の応答と、
    ラベルの組による確認。
  - 応答の `springboardChecked` と `withheld` が事実どおりになること。
  - シナリオの途中の受け渡しではキーと間合いのメモが残り、`scenarioStart` の受け渡しでは消えること。
  - ツリー内の *answered* がタップの時点で記録され、あとの *gave up* がその隣に、取り消しなしで記録される
    こと。
  - 画面の署名が変わったときに再タップを控えること。
  - 消えかけの入れ子のツリー内の形に狭いほうの兄弟が一致しても、再タップまでの遅延のうちに押し直さない
    こと。
- **Python**：偽の actuator が capability を実装します。テストの対象は次のとおりです。
  - 薄いゲートと、記録を消費した drain が 1 回だけ報告する規則。*unidentified* の記録は、ステップを止め
    ません。
  - `handleSystemAlert` ステップの、ツリー内だけを片付けるポーリングと、`/systemAlert/resolve` の呼び
    出し。
  - `handleSystemAlert` の待機中の `/systemAlert/resolve` の断念が、時間切れのときに
    `uncleared_prompt_note` として届くこと。
  - *unidentified* や *gave up* の記録から作った注記が、そのあとの折りたたみツリーの代理指標のポーリング
    で消えないこと。
  - `/systemAlert/resolve` から作った *unidentified* の注記が、ステップ自身の `/systemAlert/query` が
    そのボタンを読まなくなったときに消えること。
  - `native` と `in_tree` の両方を立てたルールに、`push_interruption_policy` が `ValueError` を送出する
    こと。
  - 範囲を狭めたリトライが、結果のわからない書き込みのあとに発動しないこと。
  - 手順 3 が後回しにしたツリー内のシートに対象が覆われたままで `not-hittable` になったタップのあとの
    リトライが、立て直しの読み取りを強制して行われること。
  - 答えたばかりでまだ消えきっていないアラートに覆われてタップが拒まれ、強制した立て直しの読み取りを間合い
    のメモが見送ったとき。`withheld` を受けて画面が落ち着くのを待ち、1 回だけやり直します。
  - SpringBoard の確認が間引かれたアラートの下で、セレクタの解決の読み取りが `ElementNotFound` を送出した
    `swipe`。強制した立て直しの読み取りを 1 回送り、応答済みがあれば 1 回だけやり直します。
  - 本体自身の片付ける読み取りがツリー内のアラートに答え、立て直しの読み取りの時点でそのアラートが消えて
    いた場合。拒まれたタップやセレクタの解決の `ElementNotFound` のあとで、画面が落ち着くのを待って
    1 回だけやり直します。
  - 失敗した `assert` や `expect` の判定が、`query_resolving(true, force_check=True)` を 1 回送ること。
    応答済みがあれば `settle_after_alert_dismiss` で待って 1 回だけやり直し、注記をその応答から作ります。
  - 失敗した `extract` が立て直しの読み取りを送らず、ステップの本体もやり直さないこと。
  - 失敗した、位置で答える `handleSystemAlert` ステップ（BE-0445）が、ステップ終了時の位置で
    `query_resolving(true, force_check=True)` を 1 回送り、その応答から注記を作ること。ラベルで名指しする
    ステップは何も送りません。
  - TipKit のリトライとアラートによるリトライのやり直しが、ガード付きの待機で `query_resolving(true)` を
    送ること。
  - 失敗した `expect` の判定の立て直しの記録が、次のシナリオではなく `expect` の段階の drain で報告される
    こと。
  - capability のもとで、ステップ終了時のリトライからも `expect` の経路からも `AlertGuardConfig.__call__`
    を呼ばないこと。
  - `HANDLE_SYSTEM_ALERT` を持たない backend が、`__call__` の両方の半分を削除したあとも、ツリー内の
    プロンプトをタップしないこと。
  - 配信済みの `resolveAlerts` の読み取りを、BE-0207 のリトライでも BE-0287 の復旧での再送でも、
    `_is_retry_eligible` が送り直させないこと。
  - 記録の欄がない `resolveAlerts` の応答で、`XcuitestChannelError` が送出されること。
  - `resolveAlerts` を付けた読み取りに、`_timeout_for` が操作の時間枠を与えること。
  - 折りたたみツリーの代理指標が、`springboardChecked` が `true` の応答でだけ待機を早めに打ち切ること。
  - `query_resolving` が各応答の記録を返し、持ち越しにも入れること。
  - ステップのセレクタと同じラベルのツリー内の応答が、`handleSystemAlert` ステップを通さないこと。
  - capability のもとで、ステップ自身のアラートをポーリングの合間に監視が答えた場合も、
    `handleSystemAlert` ステップが先へ進むこと。
- **実機**：Unit 7 を適用した状態で、3 つの showcase シナリオが 4 つの iOS バージョンで成功します。
  - `demos/showcase/scenarios/permission.yaml`
  - `demos/showcase/scenarios/save_password_browser.yaml`（BE-0399 の 2 つのプロンプトのシナリオ）
  - `demos/showcase/scenarios/save_password_interrupts_step.yaml`

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

- [x] Unit 1：スナップショットの挙動、SpringBoard の確認のコスト、押下の所要時間、ステップのコストを測る（関門）
- [x] Unit 2：`resolveAlerts` でアラートを片付ける `/elements`
- [x] Unit 3：`handleSystemAlert` ステップのための `POST /systemAlert/resolve`
- [x] Unit 4：`RESOLVE_ALERTS` capability の内側にある Python 側の薄い経路
- [x] Unit 5：範囲を狭めた、ステップあたり 1 回のリトライ
- [x] Unit 6：予防（起動引数とパスワードの自動入力の試作）
- [x] Unit 7：Python 側の旧来の検知経路の削除
- [x] Unit 8：Swift、Python、実機での検証
- [x] Unit 9：両言語の文書

ログ：

- [#2152](https://github.com/bajutsu-e2e/bajutsu/pull/2152) — Unit 1 の計測です。使い捨ての runner の
  ビルドで、iOS 18.6、26.3、26.4、26.5 について 4 点を測りました。数値は「Unit 1 の結果」にあります。

  `app.snapshot()` は割り込み監視を一度も呼ばないので、Unit 2 の SpringBoard の確認は残します。1 秒の
  間引き間隔もそのままにします。候補の 1 回で、BE-0416 のバナーの経路により runner が失われました。
  この扱いは、Unit 2 に入る前にレビューで判断します。

  この PR は、BE-0428 で入った不具合（回帰）も直しました。`--exclude` で自己宣言型のシナリオファイルを
  除いたあとも、`bajutsu run` がシナリオディレクトリ全体を拒んでいました。この拒否のため、計測で
  `make run-swiftui` をそのまま使えませんでした。
- [#2155](https://github.com/bajutsu-e2e/bajutsu/pull/2155) — Unit 2 です。着手の前に、Unit 1 で
  runner が失われた件をメンテナーが判断しました。アラートを片付けるコストではなく、BE-0416 のバナーの
  経路にもともとある不具合と見て、Unit 2 を進めることにしました。

  `GET /elements` に `resolveAlerts`（`true` または `inTree`）と `forceCheck` を足しました。片付ける
  読み取りは、渡された `pollIntervalSeconds` につき多くとも 1 回 SpringBoard を確認してから、スナップ
  ショットを取ります。続いて SpringBoard を確認し直し、アラートがなければ、最初に一致したツリー内の
  ルールに答えます。返信は記録をすべて drain し、`springboardChecked` と `withheld` を載せます。

  表示ごとのキーと間合いのメモは `InterruptionPolicyStore` が持ち、割り込み監視、片付ける読み取り、
  操作の完了の 3 つが共有します。ストアは *unidentified* と *gave up* も記録します。
  `push_interruption_policy` は、ツリー内のルールを `tree_dedup_rules` の順に送り、ガードの間合いの値と
  `scenarioStart` の印を添えます。両方の表示面を名乗るルールには `ValueError` を出します。

  ドライバは、BE-0207 の再送、BE-0287 の再発行、タイムアウトの長さについて、片付ける読み取りを書き込みと
  同じに扱います。記録のない返信には `XcuitestChannelError` を出し、記録は `_drain_carry` に最新のものと
  して畳み込みます。片付ける読み取りを送る箇所はまだありません。`query_resolving` と `RESOLVE_ALERTS` の
  トークンは Unit 4 で足します。

  設計の記述と食い違う点が 1 つあります。設計には、除外ラベルを持つ native のルールを
  `push_interruption_policy` がすでに拒んでいるとありますが、実際には拒んでいません。`notifications` は
  除外ラベルごと送られ、runner もその除外を守るため、この変更でも拒否は足していません。間合いの定数は
  `alert_guard_config.py` に移し、Unit 7 まではガードもそこから読みます。

  Unit 4 が片付ける読み取りを送り始めるまでは、native のキーを解除するのは完了した操作だけです。
  割り込み監視が割り込んだ操作がそのあと例外で終わると、キーは残ります。次の操作が完了する前に同じ
  プロンプトがまた出ると、監視はボタンを押しますが、記録はしません。設計はこの隙間を受け入れています。
  Unit 4 の片付ける読み取りによる確認で、この隙間はなくなります。
- [#2164](https://github.com/bajutsu-e2e/bajutsu/pull/2164) — Unit 3 から 9 までで、本項目を完了しました。

  runner に `POST /systemAlert/resolve` を足しました。アラートがステップの読んだラベル付きのボタンを
  そのまま示している間だけ方針どおりに答え、間合いのメモは片付ける読み取りと共有します。Python には
  `Capability.RESOLVE_ALERTS` と `AlertResolvingTarget` を足しました。ガードが効いているときは、待機と
  操作のセレクタの解決の読み取りが片付ける読み取りを通ります。アサーションと証跡は通常の読み取りのまま
  です。

  ゲートは記録を読む部分と折りたたみツリーの代理指標だけに縮みました。ステップ終了時、`expect`、
  シナリオ開始時の立て直しは、それぞれ強制の読み取りを 1 回送ります。Unit 7 では、`probe_native`、
  `dismiss_from_tree_once`、`AlertGuardConfig.__call__`、ゲートのネイティブ照会とツリー内のタップを
  削除しました。

  実機では、4 つの showcase シナリオが、iOS 18.6、26.3、26.4、26.5 のすべてで成功しました。4 つとは、
  `permission.yaml`、`permission_system_alert.yaml`、`save_password_browser.yaml`、
  `save_password_interrupts_step.yaml` です。Unit 7 の前と後の 2 回とも成功しています。

  最初の実機の実行で、Unit 2 の不具合が見つかりました。`LegacyBackedTransport` がパス全体でルートを
  照合していたため、`GET /elements?resolveAlerts=true` が 404 を返していました。Unit 2 のテストはハンドラを
  直接呼んでいたので、この不具合を捉えられませんでした。この変更でルーティングを直し、通信経路を通す
  テストを足しました。設計の記述から外れた点は 5 つです。
  - シナリオ開始時の確認（`_clear_entry_alert`）も `probe_native` を使っていましたが、設計には載って
    いませんでした。いまは強制の読み取りを 1 回送ります。
  - 読み取りの中の押下はドライバの操作ではないため、ステップの操作記録に `systemAlert` の項目は
    残りません。押下はアラートのイベントとしてレポートに届きます。
  - 失敗した `email` ステップは、アラートによるリトライを受けなくなりました。範囲を狭めたリトライの
    対象は、拒まれた操作、見つからなかった対象、失敗した判定、位置で答える `handleSystemAlert`
    ステップだけだからです。
  - `/systemAlert/resolve` はラベル付きのボタンだけを比べます。ドライバ自身の読み取りが報告するのと
    同じ集合です。また、メモが保留した表示だけでなく、押下を打ち切った表示にも `pending` を返します。
  - Unit 6 では、予防の手段として `launchArgs` と `launchEnv` を文書にし、`simctl privacy` に通知と
    ATT のサービスがないことを確かめました。パスワードの自動入力の試作では、実行前にこの設定を切る
    サポートされた方法が見つかりませんでした。26.5 では自動操作でスイッチが切り替わらず、手で切り
    替えても、実行前の手順が書き込める設定ファイルは変わりませんでした。そのため
    `simulator.autofillPasswords` のオプトインは出荷しません。

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