[English](BE-0345-actuation-record.md) · **日本語**

# BE-0345 — ステップが実際に行った操作の座標とジェスチャの形状を記録するようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0345](BE-0345-actuation-record-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0345") |
| 実装 PR | [#1511](https://github.com/bajutsu-e2e/bajutsu/pull/1511) |
| トピック | 検証とカバレッジ |
<!-- /BE-METADATA -->

## はじめに

完了した run が記録しているのは、各ステップが何を行うよう「指示された」かだけであり、ドライバが画面
に対して実際に何をしたかは記録されていません。この項目は、その欠けているもう半分を追加します。
ステップが行うすべての actuation を、ステップの結果に記録します。タップが注入した座標、スワイプが
動いた 2 つの端点、そのジェスチャを運んだ経路です。記録した内容は `manifest.json` に書き出し、
レポートと `bajutsu trace` のタイムラインに表示します。この記録は証跡専用であり、アサーションが読む
ことは一度もないので、決定的な判定には影響しません。

## 動機

ステップのレポート項目は、その操作種別と所要時間を示すだけで終わっています。
`StepOutcome`（`bajutsu/orchestrator/types.py:118`）が持つのは `index`、`action`、`ok`、`reason`、
`duration_s`、`started_at`、アサーション結果、成果物、そしてガードが閉じたシステムアラートです。
`tap` が「どこ」に着地したのか、`swipe` がどれだけ動いたのかを示すフィールドは 1 つもありません。
`action` が持つのは操作種別の名前（解決先ではなく `"tap"` という文字列）だけです。つまり「このタップは
どのピクセルに触れたのか」という問いへの答えは、run が唯一の真実として扱うファイルのどこにもありません。

この欠落は、すでに取得済みのデータに対する表示が足りないという話ではありません。具体的な値が存在する
のはドライバの内部だけで、しかも値を解決してから注入するまでのわずかな時間に限られます。4 つの
バックエンドのうち、その値をついでに記録しているものが 1 つあるだけです。Android のドライバは
`logger.debug` を 2 行出力します。解決した frame（`bajutsu/drivers/adb.py:865`）と、これから注入する
座標（`bajutsu/drivers/adb.py:924`）です。後者のコメント自身が、この 2 行で「実際にどこをタップしたか」
のすべてになると述べています。しかしどちらも、run の前に誰かがログレベルを上げていなければ出力されず、
run のディレクトリには届きません。iOS（XCUITest）、web（Playwright）、fake のバックエンドには同等の
出力すらないので、これらのバックエンドでは、run の最中でも事後でも、どのログレベルでも座標は手に
入りません。

この欠落があるために、ある種類の失敗の診断がとても高くつきます。解決の元にした要素ツリーが 1 つ前の
画面を記述していたために、ジェスチャが対象を外す事象は、Android で繰り返し観測されている実測済みの
問題です。`long_press` が対象を 10 ピクセル外し、73 ピクセル動いたスワイプが 1.2 秒以上「変化なし」と
読まれた事例があり、どちらも原因はジェスチャより前に公開されたツリーでした
（[BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier-ja.md)、
[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)）。
この種の失敗を診断するには、タッチがどこへ行ったかと、要素が実際にどこにあったかを比べる必要があります。
ところが今の run ディレクトリは、要素ツリー（`elements.json`）とスクリーンショットは保持しても
タッチは保持しないので、この比較を支えられません。そのため調査は、デバッグログを有効にしてシナリオを
再実行し、フレーキーが再現することを期待する形になります。

ジェスチャを運んだ経路も同じくらい重要で、同じように記録されていません。Android は `tap` を 2 通りの
方法で行います。常駐 UI Automator サーバの `/act` エンドポイントが、注入の直前に端末側で要素の bounds を
読む方法と、そのエンドポイントが使えないときにホストがタッチの 1 往復前に座標を計算する方法です
（[BE-0339](../BE-0339-adb-device-side-actuation/BE-0339-adb-device-side-actuation-ja.md)）。ズレが
起こりうるのは後者の方法だけです。前者を通ったうえで外したのなら原因はまったく別なのに、
`manifest.json` はどちらを通ったかを記録していません。[`DESIGN.md`](../../DESIGN.md) の §10 の出荷基準
チェックリストには、生の座標へ縮退したステップを manifest に degradation として明示するという項目が
未チェックのまま置かれていますが（`DESIGN.md:543`）、この項目を所有するロードマップ項目はありません。

[証跡](../../docs/ja/glossary.md#証跡-capturepolicy-trace-triage)サブシステム自身も、すでにこの記録を
約束しています。`actionLog` は
[`docs/ja/evidence.md`](../../docs/ja/evidence.md) と `DESIGN.md` の §9 で宣言されている証跡の種別であり、
出荷されている既定の取得ポリシーにも入っています（`bajutsu/config/schema.py:258` の
`capture: [screenshot.after, elements, actionLog]`）。`DESIGN.md` の §9 の表は、その取得元を
「オーケストレータ内部（操作・引数・結果・所要時間）」と書き、常時記録だと明記しています。ところが
このトークンに対してコードが行うことは何もありません。`capture()` は「actionLog は manifest に内在する」
というコメントを添えて読み飛ばし（`bajutsu/evidence/core.py:179`）、その manifest に内在しているのは
操作種別と所要時間だけです。約束のうち引数の部分は、はじめから実装されていません。この項目はそれを
実装し、あわせて 2 つの文書を実際の記録内容に合わせて改訂します。

## 詳細設計

設計全体を決める事実が 1 つあります。具体的な座標を知りうるのはドライバの内部だけであり、ドライバの
内部でさえ知りえない場合もあるということです。
[actuator](../../docs/ja/glossary.md#driver-backend-actuator-platform) ごとに、ジェスチャの解決と伝達の
仕方が違います。Android のドライバはピクセル座標を計算して注入するか、要素の identity を端末に渡して
座標の選択を端末に委ねます。iOS のドライバはスナップショット済みの要素への handle を XCUITest に渡し、
点を選ぶのは XCUITest です。web のドライバは CSS ピクセルの座標をクリックします。WebView のブリッジは
Document Object Model（DOM）の内部でセレクタによって操作します。ドライバより上のどの層も、こうした
違いを見ていません。したがって記録を生み出す場所はドライバであり、記録の第 1 の規則も同じ事実から
導かれます。**記録が持つのは、実際にプラットフォームへ渡された座標だけであり、あとから再構成した座標は
決して持ちません。** frame 中心を計算して送るドライバはその中心を記録し、`swipe` のハンドラから 2 つの
端点を受け取ったドライバはその端点を記録します。プラットフォームへ渡ったのがその値だからです。handle
経由の iOS のタップは、解決した要素とその frame を記録し、タッチ点は「未設定」のままにします。何かに
座標を渡していないからです。点を選ぶのは handle の向こう側の XCUITest であって、ここで frame の中心を
書けばもっともらしい推測を測定値として差し出すことになります。要素の identity を送って座標の選択を
端末に委ねる Android の端末側の経路も同じです。

第 2 の規則は、記録が端末側の処理を増やさないことです。記録が持つ値はどれも、actuator が自分のために
すでに手にしていた値です（解決した frame、送った点、渡した duration）。そのため端末の
read、query、往復はどれも増えません。`tests/orchestrator/test_read_count.py` が固定している read 数の
不変条件（素の `tap` はループ側の read を 1 回も出さない）はそのまま保たれ、後述の作業単位でその保持を
テストします。メモリについては別の理由で上限を設けます。actuator は crawl、`record` の再生、ドライバの
適合性スイートからも呼ばれ、いずれも drain しないので、蓄積器は自分で上限を持ちます。

第 3 の規則は、秘匿情報の境界です。ドライバが手にする値のうち、解決済みの `${secrets.*}` を持ちうる
ものが 3 つあります。`_interp_step`（`bajutsu/orchestrator/substitution.py:14-29`）はステップ全体を
置換するので、著者が書いたどの文字列にもなりえます。`type` ステップが入力したテキスト、`selectOption`
ステップが選ぶ `option`、そして要素のアクセシビリティ label です。だからこそ
`Redactor.redact_elements`（`bajutsu/evidence/redaction.py:150-169`）は、`elements.json` を書き出す前に
`label` と `value` を洗います。`manifest_dict`（`bajutsu/report/manifest.py`）は redactor を通さないので、
記録はそもそもこの 3 つのどれも持ってはいけません。`type` と `selectOption` については、それが起きた
事実だけを記録し、文字列については何も記録しません。文字数さえ記録しません。`Redactor` は秘匿情報を
固定長のプレースホルダに置き換えており（`redaction.py:23`）、パスワードの長さがどの成果物にも出ない
ようにしているからです。加えてドライバは redactor とバインディングのどちらも持たないので、どの文字列が秘匿
情報だったかを知る手立てがありません。`target` は常に解決した `Element["identifier"]` にします。label は使わず、バックエンド
独自のより豊かなアドレス値も使いません。最後の一文は仮定の話ではありません。Android は端末側の経路を
`NodeIdentity` で指定します。dump からそのまま取った `resource-id`、`content-desc`、`text`、`class` の
4 要素タプルです（`bajutsu/drivers/adb.py:81-82`）。このうち `content-desc` と `text` は redactor が
洗っている当のフィールドであり、`type` ステップが入力した欄では `text` が入力された文字列そのものです。
identity を `target` として記録すれば、第 3 の規則が禁じているものをそのまま漏らすことになるので、
Android の記録も他のバックエンドと同じ正規化済みの識別子を持ちます。識別子を持たない要素も frame と
座標で位置は特定でき、frame と座標にはこの危険がないので、そのまま記録します。

### 記録の形

`Actuation` は、新しいモジュール `bajutsu/drivers/actuation.py` に置く frozen な dataclass で、
ドライバが行った 1 つのプリミティブを表します。

| フィールド | 意味 |
|---|---|
| `gesture` | ドライバのプリミティブ。`tap`、`doubleTap`、`longPress`、`swipe`、`scroll`、`pinch`、`rotate`、`typeText`、`deleteText`、`selectAll`、`copy`、`selectOption`、`systemAlert`、`back` など |
| `via` | ジェスチャが対象へ届いた方法。`coordinate`（ドライバが点を計算して送った）、`handle`（XCUITest がスナップショットの handle を操作した）、`identity`（Android の端末が要素を解決して点を選んだ）、`bridge`（WebView のブリッジを要素 id で呼んだ）、`focused`（フォーカスを持つ入力欄に対する文字系のプリミティブ。要素を指定しない）、`key`（キーイベント）、`history`（ブラウザの履歴） |
| `unit` | 数値が属する座標系。`point`（iOS）、`pixel`（Android）、`cssPixel`（ブラウザのページ、および WebView 自身の空間） |
| `points` | ジェスチャが触れた接触点を順に並べたもの。タップは 1 点、ドラッグは始点と終点の 2 点、ドライバが座標を選んでいないときは空 |
| `frame` | ドライバが要素を解決した場合、その要素の frame `(x, y, w, h)` |
| `target` | 常に解決した `Element["identifier"]` だけを入れ、他は入れません。自由文が載りようがない形にするためです。識別子を持たない要素では未設定になります（前述の第 3 の規則） |
| `accepted` | その試行をプラットフォームが受け入れたかどうか。答えを返す 2 つの経路（XCUITest の handle 操作と Android の端末側エンドポイント。どちらも拒否と再試行がありえます）で設定されます。`None` はドライバが個別の答えを得られなかったことを意味し、そのときステップが動いたかを語るのはステップ自身の `ok` と `reason` です |
| `duration_s` / `scale` / `radians` | ジェスチャの位置以外のパラメータ。それを持つジェスチャでのみ設定されます。文字列のパラメータ（`type` のテキスト、`selectOption` の option）は、どんな形であれ意図的に持ちません。前述の第 3 の規則によります |

`gesture` と `via` の型は、意図して `Literal` ではなく `str` にします。レポートの描画側は、これらの
記録を、より古いバージョンやより新しいバージョンのツールが書いた `manifest.json` から復元します
（[BE-0068](../BE-0068-regenerable-reports/BE-0068-regenerable-reports-ja.md) が、その前後互換性を
ローダの契約にしています）。そのため `Literal` にすると、ディスク上のファイルが保証できない約束を
型のレベルで主張してしまいます。代わりに、語彙はモジュール直下のタプルと docstring で示し、描画側は
語彙に載っていない値を不透明な文字列として扱います。

同じモジュールの `ActuationLog` は、ドライバが持つ蓄積器です。`record()` が追記し、`drain()` が前回の
drain 以降のすべてを返して自身を空にします。実体は上限 512 の `deque` なので、drain しない利用側
（crawl、`record` の再生、適合性スイート）では、ジェスチャ 1 つごとに 1 件をセッション全体にわたって
溜め込むのではなく、直近の記録を残します。上限は、1 回の drain で扱う 1 ステップの最悪値より十分大きく
取ります。その最悪値は `tap` の数件ではありません。`scroll` ステップは `maxScrolls` 回までジェスチャを
費やし（既定 15、著者が上限なしで設定可能。`bajutsu/scenario/models/actions.py:332`）、Android では素の
`tap` でも、対象を画面に入れる `_scroll_into_view` がさらに 3 回スワイプしえます（`adb.py:398`）。
捨てたことも黙って済ませません。ステップの最初のほうのジェスチャは、まさに「スクロールが対象に届かなかった」
を診断するために必要な部分だからです。`drain()` が記録とあわせて件数を返し、ループがそれを
`StepOutcome.dropped_actuations` に載せ、レポートとタイムラインが切り詰められた記録を切り詰めとして
表示します。ログ行では足りません。ログ行は run の前にレベルを上げていなければ存在せず、run の
ディレクトリにも届かないと、この項目自身の代替案の節が述べているとおりです。`ActuationReporter` は、
オーケストレータが蓄積器を読むための狭い `runtime_checkable` なプロトコルで、メソッドは
`drain_actuations()` の 1 つだけです。このプロトコルを実装しないバックエンドは何も記録せず、run の
挙動は変わりません。`bajutsu/drivers/base.py` の `ViewportProvider`、`ReadLagProvider`、
`SettledReadProvider` がすでに同じ形で機能しています。モジュールを `bajutsu/drivers/base.py` への追記
ではなく新規にするのは、`base.py` 自身の docstring がそのファイルをすべてのバックエンドが依存する
凍結された要と呼んでいるからです。新しいモジュールは、[`pyproject.toml`](../../pyproject.toml) で
`bajutsu.drivers.base` を挙げている 2 つの import-linter 契約の両方に加えます。決定的なコアが周辺から
独立していることの契約と、移植可能な内側の契約です。これにより、記録の型が `serve`、`triage`、
エージェント、オーケストレータ、runner、レポートへの依存を持つことはできなくなります。

### 作業分解（MECE）

1. **記録、蓄積器、プロトコル。** `bajutsu/drivers/actuation.py` を追加し、`Actuation`、
   `ActuationLog`（上限 512 の `deque`。捨てた件数を数えて警告する）、`ActuationReporter`、および前述の 2 つの
   語彙タプルを置きます。`pyproject.toml` で `bajutsu.drivers.base` を挙げている 2 つの契約、つまり
   周辺からの独立の契約と移植可能な内側の契約の両方の `source_modules` にモジュールを加えます。片方
   だけではもう片方が守る import が無防備に残ります。この作業単位では挙動は変わりません。`Actuation`
   を構築するコードはまだありません。

2. **各バックエンドが、実際に行ったことを記録する。** すべてのドライバが `ActuationLog` と
   `drain_actuations()` を持ち、行ったプリミティブごとに `Actuation` を 1 つ記録します。各記録は
   「試行」であり、通信の結果がわかる前に書きます。そのため操作に失敗したステップ（`stale` の再試行を
   使い切った、チャネルがエラーになった、常駐エンドポイントが処理を断った）でも、何を試したかは残ります。
   合否を運ぶのはステップ自身の `ok` と `reason` であり、作業単位 3 の drain は合否によらず走ります。
   したがって Android の縮退は 1 回の `tap` に対して 2 件を記録します。断られた `identity` の試行と、
   続いて行った `coordinate` の注入を、その順で残します。読む人が必要とするのはこの並びです。ただし試行を
   記録するのは半分にすぎません。答えを返す 2 つの経路、つまり XCUITest の handle 操作と Android の
   `/act` エンドポイント（どちらも拒否と再試行がありえます）では、答えが届いた時点でドライバが記録に
   それを刻みます（`ActuationLog.settle`）。これがないと、stale で再試行したタップは同一内容の記録を
   3 件残し、どれを端末が受け入れたのかを示すものが何もなくなります。レポートは 1 回のタップを 3 回として
   描いてしまいます。個別の答えを返さない経路は、成功を主張せず `accepted` を未設定のままにします。
   Android の「要求は出たが応答が失われた」場合もこの読み方になります。記録先は
   具体的な値がすでに置かれている場所なので、二重に解決するドライバは 1 つもありません。
   - **XCUITest**（`bajutsu/drivers/xcuitest.py`）は、記録を 1 か所、`_actuate` で構築します。handle
     経由のジェスチャはすべてこの関数を通ります。ただし現在の `_actuate` が受け取るのは `path`、
     `body`、`sel` だけで（`xcuitest.py:550`）、要素はスコープにありません。そこで解決し直せば
     `/elements` の往復が 1 回増え、第 2 の規則を破ります。そのため `_resolve_handle` はすでに手元に
     ある `(handle, element)` の組を返すようにし、5 つの呼び出し側（`tap`、`double_tap`、`long_press`、
     `pinch`、`rotate`。`xcuitest.py:585-606`）がその要素と自分の `gesture` 名を `_actuate` へ渡します。
     名前を渡すのは意図的で、`_actuate` がリクエスト本体から推測する形は採りません。
     `body["taps"] == 2` から `doubleTap` を導けば、通信の形に関する知識が記録側にも重複し、その形が
     変わった日に黙って誤ったラベルを付けるようになるからです。`via` は `handle` で、`points` は空です。
     `stale` 応答は handle を再解決するので
     （[BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve-ja.md)）、
     `_actuate` は自身の再解決で要素を上書きし、記録が示すのは「最後の試行」が解決した要素、つまり実際に
     操作された要素になります。`_actuate` を通らず自前の通信を持つプリミティブが 8 つあるので、それぞれ
     が送信する場所で記録します。`tap_point`（`xcuitest.py:594-598`）、`swipe`（`:608-611`）、
     `scroll`（`:625-632`）は `via: coordinate` としてその点を記録し、文字系のプリミティブ
     （`type_text`、`delete_text`、`select_all`、`copy_selection`。`xcuitest.py:681-699`）は
     `via: focused` として記録します。要素を指定せず、フォーカスを持つ入力欄に作用するからです。
     `handle_system_alert`（`xcuitest.py:639`）も同様に自前で `/systemAlert/tap` を送るので、
     `gesture: systemAlert` として記録します。その `target` は通常は未設定です。SpringBoard の
     アラートボタンは見えている label で指定され、識別子を持たないことが多く（`xcuitest.py:665-675`）、
     label の記録は第 3 の規則が禁じているからです。したがって作業単位 3 のガードのタップの載り先を
     実際に成り立たせるのは、中断した先のステップに記録が存在すること、そしてそのステップがすでに持って
     いる `alerts[].label` のボタン名とあわせて読めることです。構造上、記録を自分では生まないプリミティブ
     が iOS に 2 つあるので、明示しておきます。`back` は `tap` を通して OS の戻るボタンを叩くので
     （`xcuitest.py:676-679`）、iOS の `back` ステップは `BackButton` に対する `gesture: tap` /
     `via: handle` として記録され、iOS の記録が `gesture: back` を持つことはありません。`select_option`
     は `UnsupportedAction` を投げるので、何も記録しません。`unit` は全体を通じて `point` です。
   - **Android**（`bajutsu/drivers/adb.py`）は 2 つの経路を区別して記録します。`_device_act` は
     `via: identity` として、解決した要素の正規化済み識別子を `target` に置きます。端末へ送る
     `NodeIdentity` のタプルは置きません。第 3 の規則が述べた秘匿情報の理由によります。あわせて解決した
     frame を置き、点は置きません。座標を選んだのは端末だからです。座標へ縮退する経路は
     `via: coordinate` として注入した点を記録し、
     内部の解決ヘルパ（`_center`、`_center_with_screen`、`_resolve_frame_and_screen`）が解決済みの
     要素も返すようにして、`target` と `frame` を一緒に得ます。root 権限で `sendevent` を使う
     double-tap は、その点をスケールして得る生のタッチデバイスの範囲ではなく、ツリー座標系の点を
     記録します。生の範囲は注入方法の副産物であり、それを記録すると同じ要素への 2 回の double-tap が
     別々の座標に見えてしまいます。`unit` は `pixel` で、`back` は `via: key` として座標なしで記録します。
     `KEYCODE_BACK` イベントの実態と一致する形です。文字系のプリミティブは iOS と同じく `via: focused`
     として記録します。
   - **Playwright**（`bajutsu/drivers/playwright.py`）は、マウスとタッチのプリミティブを
     `cssPixel` の `via: coordinate` として、文字系のプリミティブを `via: focused` として、`back` を
     `via: history` として記録します。`select_option` を実装している唯一のバックエンドでもあるので
     （`playwright.py:697`。例外を投げるのではなく、要素を解決して計算した中心で値を設定する本物の
     操作です）、`gesture: selectOption` を座標として記録します。option の文字列は第 3 の規則に従って
     いっさい残しません。
   - **WebView のコンテキストドライバ**（`bajutsu/webview.py`）は、独自の経路ではなく
     `via: coordinate` として記録します。`tap`（`webview.py:128-134`）と `double_tap`（`:141-143`）は
     要素の frame 中心を自分で計算してブリッジに点を渡し、`tap_point`（`:138-139`）は呼び出し側の点を
     そのまま渡します。いずれの場合も WebView へ座標が渡っており、第 1 の規則が求めるのはその値です。
     点が属するのは端末の画面では
     なく WebView 自身の座標系なので、`unit` は `cssPixel` で、記録は端末ではなく WebView に対して
     読みます。`tap` は対象を表示範囲に入れるため先に `scroll_to` を要素 id で呼ぶので、これも
     `via: bridge` の 1 件として記録します。タップの点を計算した対象の内容を動かす操作であり、まさに
     この記録が可視化しようとしている失敗の型そのものなので、落とすと読む人が探しているものが消えます。
     `type_text` は `via: focused` として記録します。この最初のスライスでは他のプリミティブ
     はすべて `UnsupportedAction` を投げるので、何も記録しません。
   - **`FakeDriver`**（`bajutsu/drivers/fake.py`）は、記録のために計算した frame 中心を
     `via: coordinate` として記録します。第 1 の規則に照らしても正当です。点を選ぶ主体は fake 自身で
     あり、fake は端末がメモリである座標系のバックエンドだからです。中心を取るのは `query()` の空間、
     つまり fake のスクロール可能ビューポートのモデルが適用するスクロールオフセットで平行移動した
     あとの空間です（`fake.py:80-91`）。一意性の確認が解決に使う平行移動前の `self.screen` の空間では
     ありません。こうすることで、記録された点は、オーケストレータが `swipe` に渡す点と同じ意味を
     持ちます。`unit` は `point` にします。任意ですが固定した選択です。fake に与える frame は実在する
     端末のどの空間にも属さず、空のままにすると記録を統一して読めない唯一のバックエンドになってしまう
     からです。fake は操作系の表面をすべて実装しており、作業単位 6 の `tests/orchestrator/` のケースが
     fake の上で走るので、その全体を覆う必要があります。`back` は `via: key`（`fake.py:138`）、文字系の
     プリミティブと `select_option` は実バックエンドと同じ形（`fake.py:149-164`。`select_option` は例外を
     投げずに成功します）、`handle_system_alert` は `gesture: systemAlert`（`fake.py:166`）として記録
     します。スタブではなく実在する座標を記録することが、端末なしの高速なゲートで作業単位 6 が
     正確な形状を検証できる根拠になります。

3. **ステップループが蓄積器を結果へ移す。** `StepOutcome` に `actuations: list[Actuation]` を追加します。
   `_handle_action`（`bajutsu/orchestrator/loop.py`）は、ステップ本体が終わった直後、`outcome.ok` と
   `outcome.reason` を代入し終えた位置で、`active_driver` から 1 回 drain します。試行ごとではなく
   1 回にするのは意図的です。反応型のシステムアラートガードがアラートを閉じて本体を再試行した
   場合、どちらの試行の actuation も端末に実際に起きた出来事であり、drain したリストは起きた順に
   両方を保持します。同じ理由で、ガード自身がアラートを閉じるために行ったタップは、中断した先の
   ステップに載ります。そのステップを読む人が探す場所がそこだからです。drain の対象は
   `self.cfg.driver` ではなく `active_driver` です。`web` ブロックの内側のステップが操作するのは
   WebView のドライバであり、そのようなステップの最中にネイティブのドライバが操作されることはないので、
   取り残される記録はありません。載せる先のステップがない actuation が 1 つだけあります。ガードは
   シナリオ末尾の `expect` の再チェックのためにも発火し、そのときはステップループの外にいます
   （`loop.py:482`）。だからこそ `RunResult` は、どのステップにも属さない `expect_alerts` を別に
   持っています。その隣に `expect_actuations` を追加し、同じ位置で drain するので、ガードのタップは
   蓄積されたまま黙って捨てられるのではなく記録されます。どちらの経路でもシナリオ間に漏れることは
   ありません。シナリオごとに環境が新しいドライバを組み立てるので、蓄積器はリースとともに消えます。

4. **manifest が記録を運び、ローダが読み戻す。** `manifest_dict` は `StepOutcome` を `asdict` で
   直列化するので、記録は `manifest.json` に届きます。`bajutsu/report/manifest.py` に必要な変更は、
   `SCHEMA_VERSION` を 5 に上げ、この項目を指すコメントを添えることだけです。これにより、古い run は
   新しい表示が失敗するのではなく単に欠けた状態で描画されます（BE-0068 の契約）。バージョンを主張して
   いる箇所が 2 つあり、あわせて動かさないとゲートが赤くなります。`tests/report/test_load.py:69` は
   `data["schemaVersion"] == 4` を検証し、`docs/reporting.md` は散文で 2 回この数値を固定しています
   （作業単位 7）。
   `bajutsu/report/load.py` は `_step` に 1 行、`_result` に 1 行（作業単位 3 の `expect_actuations` の
   ため）を加えて入れ子の記録を復元し、あわせて小さな変換を加えます。
   JSON にタプルはないので、`points` と `frame` はリストとして届き、型が宣言しているタプルへ戻します。
   この変換がなければ既存の `test_round_trip_through_manifest_is_lossless` が失敗します。そのテストは
   まさにこれを検出するために存在しています。

5. **2 つの表示面が記録を見せる。** HTML レポートは、actuation を行ったステップの下に 1 行を追加します。
   既存の `alertrow` と同じ形（`bajutsu/templates/report.html.j2`、`bajutsu/report/rows.py`、
   `bajutsu/templates/report.css`）で、ジェスチャ、経路、解決した target、形状を示します。座標のある
   タップなら `tap → (128.0, 460.5) pt on settings.reindex`、ドライバが座標を選んでいない場合は
   `tap → settings.reindex [124, 448, 96, 44] via handle` のような表示です。作業単位 3 のシナリオ単位の
   `expect_actuations` も、`alertrow` の形が持つシナリオ単位の対応物と同じ扱いで表示します。`rows.py` は
   ステップの `alerts` を `rows.py:164` で、expect ブロックの `expect_alerts` を `rows.py:392` で組み立てて
   いるので、新しいフィールドは後者の隣に描画します。書き出したのにどこにも出ない状態にはしません。
   `bajutsu trace` のタイムラインは、ステップごとの行に同じ要約を追加します（`bajutsu/trace.py` の
   `_step_event`）。タイムラインは manifest の dict を直接読むので、ローダ側の変更は要りません。expect
   段階の記録はレポートだけに出します。タイムラインには記録を載せる expect の行がないからです。`serve` の web UI は
   意図してスコープ外にします。`serve` は独自の run 表示を描画しており、そこへ広げるのは別の表示面
   への別の変更です。

6. **決定的なテスト。** 作業単位 2 によって fake が実在する座標を記録するので、`tests/orchestrator/`
   に置く `FakeDriver` ベースのテストは正確な形状を検証できます。`tap` は解決した要素の frame 中心と
   その識別子を記録すること、方向指定の `swipe` は `_scroll_gesture` が計算する端点と一致する 2 点を
   記録すること、`long_press` は duration を記録すること、`pinch` は scale と解決した frame を記録する
   ことを確認します。これらのケースは fake の素の（スクロールしない）モードを使い、記録された点と
   与えた frame が同じ空間に属する状況で検証します。さらに 1 つのケースでスクロール可能ビューポートの
   モードをスクロール後に走らせ、記録された点が「平行移動後」の中心であることを確認して、作業単位 2 が
   選んだ空間を固定します。どのステップに載るかは独立した性質としてテストします。各ステップの結果はその
   ステップが行った actuation だけを持つこと、操作しない `assert` ステップは 1 つも持たないこと、
   アラートガードのもとで本体が 2 回走ったステップは両方の試行を順に持つことです。実バックエンドごとの
   ドライバのユニットテストで経路を確認します。Android のドライバは、常駐エンドポイントがジェスチャを処理
   したときに `identity`、処理を断ったときに `coordinate` を記録すること、XCUITest のドライバは
   `points` が空の `handle` を記録することです。秘匿情報の境界には独立したケースを置きます。ここでの
   漏洩は無言で起こるからです。label は持つが識別子を持たない要素を操作したステップが `target` を
   まったく記録しないこと、`type` ステップの記録がそのテキストに由来するフィールドを何も持たないこと
   （テキストも、その長さも持ちません）、そして Android の
   `identity` の記録が、端末へ送った `NodeIdentity` タプルのどの要素でもなく正規化済みの識別子を持つ
   ことを確認します。最後のケースは、`content-desc` と `text` が `resource-id` と異なる dump を与えて
   検証するので、タプルへ戻る退行は通りません。
   `tests/orchestrator/test_read_count.py` のケースは
   drain が read を出さないことを示し、そのモジュールが守るために存在する不変条件を保ちます。
   往復のテストは、actuation を含む manifest から等しい記録が復元されることを示し、`bajutsu trace` の
   テストは、タイムラインの行が manifest の dict から描画されることを示します。

7. **文書。** [`docs/evidence.md`](../../docs/evidence.md) と日本語版の
   [`docs/ja/evidence.md`](../../docs/ja/evidence.md) では、`actionLog` の行を「オーケストレータ内部
   （操作と所要時間）」から、実際に記録される内容を示す記述へ変え、秘匿情報の境界（文字数だけで、入力
   された文字そのものと label は記録しない）と、ドライバは自分が選んだ座標だけを記録するという規則を
   加えます。[`DESIGN.md`](../../DESIGN.md) の §9 の証跡種別の表にある `actionLog` の行も同じように
   改訂します。§10 の出荷基準チェックリストにある座標への縮退の明示の項目（`DESIGN.md:543`）には、
   この項目が閉じた半分だけを、過不足なく書き足します。この項目が指す stability ladder の段は Android の
   ものです。§5（`DESIGN.md:191`）は、常駐エンドポイントが使えないか identity を特定できないために座標へ
   落ちた `tap` / `longPress` / `doubleTap` にその縮退を限定しており、記録の `identity` と `coordinate` の
   対がそれをステップごとに示します。トークンだけを見て縮退と読むことはできません。`coordinate` は
   Playwright、WebView のドライバ、fake、iOS の `swipe` / `scroll` / `tap_point` では通常の、縮退していない
   経路でもあるからです。書き足す文もそのことを述べ、`coordinate` の記録がすべて縮退であるかのようには
   書きません。`index` セレクタへの縮退（actuation ではなくセレクタ解決の問題）は未解決のまま残るので、
   項目は残った半分を明記したうえで未チェックのままにします。`actionLog` を瞬時系の証跡種別として
   挙げている [`docs/architecture.md`](../../docs/architecture.md) と日本語版の
   [`docs/ja/architecture.md`](../../docs/ja/architecture.md) も確認し、必要に応じて合わせます
   （BE-0113）。この項目が偽にしてしまう記述を散文で持つページがさらに 4 つあるので、それぞれ日本語版と
   あわせて直します。[`docs/reporting.md`](../../docs/reporting.md) と
   [`docs/ja/reporting.md`](../../docs/ja/reporting.md) は `steps[].duration_s` を「`actionLog` 相当の
   情報」と書き（`reporting.md:69`）、schema のバージョンを散文で「現在は `4` です」と固定しています
   （`reporting.md:80`）。しかも固定は 2 か所あり、もう 1 つは「このブロックが出るようになった時点で
   `schemaVersion` は `4` です」という下限ではなく厳密な言い方なので（`reporting.md:89-90`、
   `docs/ja/reporting.md:71`）、5 になると偽になります。[`docs/run-loop.md`](../../docs/run-loop.md) と
   [`docs/ja/run-loop.md`](../../docs/ja/run-loop.md) は同じ `actionLog` 相当という記述を `StepOutcome`
   のフィールド一覧の中で繰り返しており（`run-loop.md:111`）、その一覧には `actuations` を、`RunResult`
   の一覧には `expect_actuations` を加えます。

### 機械的に検証できる結果

端末とモデルのどちらも使わない決定的なテストスイートで検証します。`FakeDriver` で走るシナリオの
`StepOutcome` が、`points` が解決した要素の frame 中心に等しい `Actuation` を持つこと（テスト側は
その中心を、与えた frame から独立に計算します）。2 ステップのシナリオが、各ステップの actuation を
それぞれの結果に保つこと。label と識別子の両方を与えた要素で、ステップが記録する `target` が識別子で
あって label ではないこと。manifest の往復が等しい記録を復元すること。read 数のテストが固定している
0 が 0 のまま保たれること。判定するのは `make check` であり、この記録が判定に入ることは一度もありません。
アサーション、wait、extract のどれも読みません（第 1 原則）。

## 検討した代替案

**各 `Driver` の actuator が記録を返す形にする。** `tap(sel) -> Actuation` に変えれば、記録はオプト
インのプロトコルではなく型レベルの保証になり、バックエンドが記録を作り忘れることはできなくなります。
これは得られるものに対して波及範囲が大きすぎるので採りません。`Driver` の操作系メソッドは 10 個あまり
あり、そのすべてが action ハンドラ、crawl、`record` の再生、アラートガード、適合性スイートから
呼ばれています。呼び出し側は、欲しくない戻り値を受けて捨てる変更を全箇所で被ることになります。
オプトインのプロトコルなら、この項目の中で同じ網羅（作業単位 2 で出荷済みの全バックエンドが実装する）
に到達でき、しかもその churn がありません。ドライバの表面が直近 4 つの任意機能を吸収してきた形にも
一致します。

**座標を運用ログに出す。** Android のドライバの `logger.debug` 2 行がすでにこれを行っており、その
不十分さが動機そのものです。ログ行は証跡ではありません。run の前にレベルを上げていなければ存在せず、
run のディレクトリとともに移動せず、比較対象であるスクリーンショットと要素ツリーの隣に描画できません。
構造化された運用ログ（[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging-ja.md)）も、
決定的な `run` の経路から意図して外した `serve` モードの関心事だと明示しており、同じ線を引いています。
証跡はテスト対象の軌跡であり、運用ログはツール自身の軌跡です。

**ステップごとに `actionLog.json` を書く。** manifest の代わりに、`elements.json` のようにステップ
ごとのファイルにする案です。記録は小さく構造化されていて、置き場所がすでにあるので採りません。
manifest は run の唯一の真実であり、`asdict` が記録を無償で運び、`DESIGN.md` の §9 は `actionLog` を
ファイルではなく manifest に内在するものとして規定しています。別ファイルにすると、ステップごとの
書き込みが 1 回増え、すべての利用側で解決すべきパスが増えます。そしてステップの真実が置かれる場所は
2 つになります。

**ドライバより上で、要素ツリーから座標を再構成する。** オーケストレータが自分でセレクタを解決し、
計算した frame 中心を記録する案です。結果が推測を測定値として差し出すものになるので採りません。記録
されるのはオーケストレータが使ったはずの座標です。iOS では XCUITest が触れた点と一致せず、Android
では常駐エンドポイントがジェスチャを処理したときに端末が選んだ点と一致しません。どちらの場合も、古い
ツリーが原因で実際のジェスチャが対象を外した点とも一致しないので、記録が可視化しようとしている失敗
そのものを取り違えます。加えて、ステップごとに端末の read を 1 回増やし、作業単位 6 が固定する不変
条件を壊します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 作業単位 1 — `bajutsu/drivers/actuation.py` に `Actuation` / `ActuationLog` /
      `ActuationReporter` を置き、`pyproject.toml` で `bajutsu.drivers.base` を挙げている
      **両方**の import 契約に加える
- [x] 作業単位 2 — 各バックエンドが、値がすでに置かれている場所で、行ったプリミティブを記録する
- [x] 作業単位 3 — ステップループが蓄積器を `StepOutcome.actuations` へ、シナリオ単位の `expect` の
      ガードが `RunResult.expect_actuations` へ移す
- [x] 作業単位 4 — `manifest.json` が記録を運び、レポートのローダが読み戻す
- [x] 作業単位 5 — HTML レポート（ステップと expect ブロック）と `bajutsu trace` のタイムラインが
      記録を見せる
- [x] 作業単位 6 — 形状、載り先、経路、秘匿情報、read 数、往復の決定的なテスト
- [x] 作業単位 7 — `docs/evidence.md`、`DESIGN.md`、`docs/architecture.md`、`docs/reporting.md`、
      `docs/run-loop.md`（いずれも日本語版とあわせて）が記録内容を記述する

## 参考

- [`docs/ja/evidence.md`](../../docs/ja/evidence.md) — この項目が `actionLog` の種別を完成させる
  対象の証跡サブシステム
- [BE-0341](../BE-0341-pre-action-evidence-capture/BE-0341-pre-action-evidence-capture-ja.md) —
  ステップごとのベースラインのスクリーンショットと要素ツリーをステップの動作より前へ動かし、
  ステップが対象とした画面をレポートが示すようにした兄弟項目。この項目は、そのあとステップが
  その画面に対して何をしたかを追加します
- [BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier-ja.md)、
  [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)
  — この記録が診断を支える、実測された古いツリーによるジェスチャ
- [BE-0339](../BE-0339-adb-device-side-actuation/BE-0339-adb-device-side-actuation-ja.md) — 記録が
  ホストの座標への縮退と区別する、Android の端末側 actuation の経路
- [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve-ja.md)
  — 記録が最後の試行を名指す根拠となる、XCUITest の stale handle の再解決
- [BE-0068](../BE-0068-regenerable-reports/BE-0068-regenerable-reports-ja.md) — schema の版上げと
  `Literal` ではなく `str` を選ぶ判断を形づくる、版管理された描画モデルの互換性の契約
- [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary-ja.md) — run
  ディレクトリへの書き込みをすべて 1 つの秘匿処理付きシンクへ通す提案。第 3 の規則が根拠にしている
  「manifest が秘匿処理を通らない」という穴を閉じるものです。第 3 の規則はどちらでも成り立ちます。
  秘匿情報を含む文字列をそもそも持たない記録は境界に守ってもらう必要がなく、境界があることは、
  その文字列を持ち始める理由にもなりません
- [BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging-ja.md) — この項目が証跡側に
  留まる根拠となる、証跡とツール自身のログの線を引いた運用ログの契約
