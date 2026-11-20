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

完了した run が記録しているのは、各ステップが何を行うよう「指示された」かだけです。ドライバが画面に対して実際に何をしたかは、記録されていません。この項目は、欠けているもう半分を追加します。ステップが行うすべての actuation を、ステップの結果に記録します。記録するのは、タップが注入した座標、スワイプが動いた2つの端点、ジェスチャを運んだ経路です。記録した内容は `manifest.json` に書き出し、レポートと `bajutsu trace` のタイムラインに表示します。この記録は証跡専用であり、アサーションが読むことは一度もないので、決定的な判定には影響しません。

## 動機

ステップのレポート項目は、その操作種別と所要時間を示すだけで終わっています。`StepOutcome`（`bajutsu/orchestrator/types.py:118`）が持つのは `index`、`action`、`ok`、`reason`、`duration_s`、`started_at`、アサーション結果、成果物、そしてガードが閉じたシステムアラートです。`tap` が「どこ」に着地したのか、`swipe` がどれだけ動いたのかを示すフィールドは、1つもありません。`action` が持つのは、解決先ではなく操作種別の名前（`"tap"` という文字列）だけです。そのため、「このタップはどのピクセルに触れたのか」という問いへの答えは、run が唯一の真実として扱うファイルのどこにもありません。

この欠落は、すでに取得済みのデータに対する表示が足りないという話ではありません。具体的な値が存在するのはドライバの内部だけで、しかも値を解決してから注入するまでのわずかな時間に限られます。4つのバックエンドのうち、その値をついでに記録しているものは1つだけです。Android のドライバは `logger.debug` を2行出力します。1行目は解決した frame（`bajutsu/drivers/adb.py:865`）、2行目はこれから注入する座標（`bajutsu/drivers/adb.py:924`）です。2行目のコメント自身が、この2行で「実際にどこをタップしたか」のすべてになると述べています。しかしどちらも、run の前にログレベルを上げていなければ出力されず、run のディレクトリには届きません。iOS（XCUITest）、web（Playwright）、fake のバックエンドには、同等の出力すらありません。これらのバックエンドでは、run の最中でも事後でも、どのログレベルでも座標を手に入れられません。

この欠落のために、ある種類の失敗の診断がとても高くつきます。Android では、解決の元にした要素ツリーが1つ前の画面を記述していたために、ジェスチャが対象を外す事象が繰り返し観測されています。実測済みの問題です。`long_press` が対象を10ピクセル外した事例と、73ピクセル動いたスワイプが1.2秒以上「変化なし」と読まれた事例があります。どちらも原因は、ジェスチャより前に公開されたツリーでした（[BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier-ja.md)、[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)）。

この種の失敗を診断するには、タッチがどこへ行ったかと、要素が実際にどこにあったかを比べる必要があります。ところが今の run ディレクトリは、要素ツリー（`elements.json`）とスクリーンショットは保持しても、タッチは保持しません。この比較を支えられないため、調査はデバッグログを有効にしてシナリオを再実行し、フレーキーが再現することを期待する形になります。

ジェスチャを運んだ経路も同じくらい重要ですが、同じように記録されていません。Android は `tap` を2通りの方法で行います。

- 常駐 UI Automator サーバの `/act` エンドポイントが、注入の直前に端末側で要素の bounds を読む。
- そのエンドポイントが使えないときに、ホストがタッチの1往復前に座標を計算する（[BE-0339](../BE-0339-adb-device-side-actuation/BE-0339-adb-device-side-actuation-ja.md)）。

ズレが起こりうるのは、後者の方法だけです。前者を通ったうえで外したのなら、原因はまったく別です。しかし `manifest.json` は、どちらを通ったかを記録していません。[`DESIGN.md`](../../DESIGN.md) の §10 の出荷基準チェックリストには、生の座標へ縮退したステップを manifest に degradation として明示するという項目が、未チェックのまま置かれています（`DESIGN.md:543`）。この項目を所有するロードマップ項目はありません。

[証跡](../../docs/ja/glossary.md#証跡-capturepolicy-trace-triage)サブシステム自身も、すでにこの記録を約束しています。`actionLog` は、[`docs/ja/evidence.md`](../../docs/ja/evidence.md) と `DESIGN.md` の §9 で宣言されている証跡の種別です。出荷されている既定の取得ポリシーにも入っています（`bajutsu/config/schema.py:258` の `capture: [screenshot.after, elements, actionLog]`）。`DESIGN.md` の §9 の表は、その取得元を「オーケストレータ内部（操作・引数・結果・所要時間）」と書き、常時記録だと明記しています。

ところが、このトークンに対してコードが行うことは何もありません。`capture()` は「actionLog は manifest に内在する」というコメントを添えて読み飛ばします（`bajutsu/evidence/core.py:179`）。その manifest に内在しているのは、操作種別と所要時間だけです。約束のうち引数の部分は、はじめから実装されていません。この項目はそれを実装し、あわせて2つの文書を実際の記録内容に合わせて改訂します。

## 詳細設計

設計全体を決める事実が1つあります。具体的な座標を知りうるのはドライバの内部だけであり、ドライバの内部でさえ知りえない場合もあるということです。[actuator](../../docs/ja/glossary.md#driver-backend-actuator-platform) ごとに、ジェスチャの解決と伝達の仕方が違います。

- Android のドライバは、ピクセル座標を計算して注入するか、要素の identity を端末に渡して座標の選択を端末に委ねます。
- iOS のドライバは、スナップショット済みの要素への handle を XCUITest に渡し、点を選ぶのは XCUITest です。
- web のドライバは、CSS ピクセルの座標をクリックします。
- WebView のブリッジは、Document Object Model（DOM）の内部でセレクタによって操作します。

ドライバより上のどの層も、こうした違いを見ていません。したがって、記録を生み出す場所はドライバです。

記録には3つの規則があります。

**第1の規則：記録が持つのは、実際にプラットフォームへ渡された座標だけであり、あとから再構成した座標は決して持ちません。** frame 中心を計算して送るドライバは、その中心を記録します。`swipe` のハンドラから2つの端点を受け取ったドライバは、その端点を記録します。プラットフォームへ渡ったのが、その値だからです。handle 経由の iOS のタップは、解決した要素とその frame を記録し、タッチ点は「未設定」のままにします。何かに座標を渡していないからです。点を選ぶのは handle の向こう側の XCUITest であり、ここで frame の中心を書けば、もっともらしい推測を測定値として差し出すことになります。要素の identity を送って座標の選択を端末に委ねる、Android の端末側の経路も同じです。

**第2の規則：記録は端末側の処理を増やしません。** 記録が持つ値はどれも、actuator が自分のためにすでに手にしていた値です（解決した frame、送った点、渡した duration）。そのため、端末の read、query、往復はどれも増えません。`tests/orchestrator/test_read_count.py` が固定している read 数の不変条件（素の `tap` はループ側の read を1回も出さない）はそのまま保たれます。後述の作業単位で、その保持をテストします。メモリについては、別の理由で上限を設けます。actuator は crawl、`record` の再生、ドライバの適合性スイートからも呼ばれ、いずれも drain しません。そのため、蓄積器は自分で上限を持ちます。

**第3の規則：秘匿情報の境界。** ドライバが手にする値のうち、解決済みの `${secrets.*}` を持ちうるものが3つあります。`_interp_step`（`bajutsu/orchestrator/substitution.py:14-29`）がステップ全体を置換するので、著者が書いたどの文字列にもなりえます。

- `type` ステップが入力したテキスト。
- `selectOption` ステップが選ぶ `option`。
- 要素のアクセシビリティ label。だからこそ `Redactor.redact_elements`（`bajutsu/evidence/redaction.py:150-169`）は、`elements.json` を書き出す前に `label` と `value` を洗います。

`manifest_dict`（`bajutsu/report/manifest.py`）は redactor を通さないので、記録はそもそもこの3つのどれも持ってはいけません。`type` と `selectOption` については、それが起きた事実だけを記録し、文字列については何も記録しません。文字数さえ記録しません。`Redactor` は秘匿情報を固定長のプレースホルダに置き換えており（`redaction.py:23`）、パスワードの長さがどの成果物にも出ないようにしているからです。加えて、ドライバは redactor とバインディングのどちらも持たないので、どの文字列が秘匿情報だったかを知る手立てがありません。

`target` は常に、解決した `Element["identifier"]` にします。label は使わず、バックエンド独自のより豊かなアドレス値も使いません。この最後の点は、仮定の話ではありません。Android は端末側の経路を `NodeIdentity` で指定します。dump からそのまま取った `resource-id`、`content-desc`、`text`、`class` の4要素タプルです（`bajutsu/drivers/adb.py:81-82`）。このうち `content-desc` と `text` は redactor が洗っている当のフィールドです。`type` ステップが入力した欄では、`text` が入力された文字列そのものです。identity を `target` として記録すれば、第3の規則が禁じているものをそのまま漏らすことになります。そのため Android の記録も、他のバックエンドと同じ正規化済みの識別子を持ちます。識別子を持たない要素も、frame と座標で位置を特定できます。frame と座標にはこの危険がないので、そのまま記録します。

### 記録の形

`Actuation` は、新しいモジュール `bajutsu/drivers/actuation.py` に置く frozen な dataclass で、ドライバが行った1つのプリミティブを表します。

| フィールド | 意味 |
|---|---|
| `gesture` | ドライバのプリミティブ。`tap`、`doubleTap`、`longPress`、`swipe`、`scroll`、`pinch`、`rotate`、`typeText`、`deleteText`、`selectAll`、`copy`、`selectOption`、`systemAlert`、`back` など |
| `via` | ジェスチャが対象へ届いた方法。`coordinate`（ドライバが点を計算して送った）、`handle`（XCUITest がスナップショットの handle を操作した）、`identity`（Android の端末が要素を解決して点を選んだ）、`bridge`（WebView のブリッジを要素 id で呼んだ）、`focused`（フォーカスを持つ入力欄に対する文字系のプリミティブ。要素を指定しない）、`key`（キーイベント）、`history`（ブラウザの履歴） |
| `unit` | 数値が属する座標系。`point`（iOS）、`pixel`（Android）、`cssPixel`（ブラウザのページ、および WebView 自身の空間） |
| `points` | ジェスチャが触れた接触点を順に並べたもの。タップは1点、ドラッグは始点と終点の2点、ドライバが座標を選んでいないときは空 |
| `frame` | ドライバが要素を解決した場合、その要素の frame `(x, y, w, h)` |
| `target` | 常に解決した `Element["identifier"]` だけを入れ、他は入れません。自由文が載りようがない形にするためです。識別子を持たない要素では未設定になります（前述の第3の規則） |
| `accepted` | その試行をプラットフォームが受け入れたかどうか。答えを返す2つの経路（XCUITest の handle 操作と Android の端末側エンドポイント。どちらも拒否と再試行がありえます）で設定されます。`None` はドライバが個別の答えを得られなかったことを意味し、そのときステップが動いたかを語るのはステップ自身の `ok` と `reason` です |
| `duration_s` / `scale` / `radians` | ジェスチャの位置以外のパラメータ。それを持つジェスチャでのみ設定されます。文字列のパラメータ（`type` のテキスト、`selectOption` の option）は、どんな形であれ意図的に持ちません。前述の第3の規則によります |

`gesture` と `via` の型は、意図して `Literal` ではなく `str` にします。レポートの描画側は、これらの記録を、より古いバージョンやより新しいバージョンのツールが書いた `manifest.json` から復元します（[BE-0068](../BE-0068-regenerable-reports/BE-0068-regenerable-reports-ja.md) が、その前後互換性をローダの契約にしています）。`Literal` にすると、ディスク上のファイルが保証できない約束を、型のレベルで主張してしまいます。代わりに、語彙はモジュール直下のタプルと docstring で示し、描画側は語彙に載っていない値を不透明な文字列として扱います。

同じモジュールの `ActuationLog` は、ドライバが持つ蓄積器です。`record()` が追記し、`drain()` が前回の drain 以降のすべてを返して自身を空にします。実体は上限512の `deque` です。そのため、drain しない利用側（crawl、`record` の再生、適合性スイート）は、ジェスチャ1つごとに1件をセッション全体にわたって溜め込まず、直近の記録を残します。

上限は、1回の drain で扱う1ステップの最悪値より、十分大きく取ります。その最悪値は、`tap` の数件ではありません。`scroll` ステップは `maxScrolls` 回までジェスチャを費やします（既定15、著者が上限なしで設定可能。`bajutsu/scenario/models/actions.py:332`）。Android では、素の `tap` でも、対象を画面に入れる `_scroll_into_view` がさらに3回スワイプしえます（`adb.py:398`）。

捨てたことも黙って済ませません。ステップの最初のほうのジェスチャは、まさに「スクロールが対象に届かなかった」を診断するために必要な部分だからです。`drain()` が記録とあわせて件数を返し、ループがそれを `StepOutcome.dropped_actuations` に載せます。レポートとタイムラインは、切り詰められた記録を切り詰めとして表示します。ログ行では足りません。ログ行は run の前にレベルを上げていなければ存在せず、run のディレクトリにも届かないと、この項目自身の代替案の節が述べているとおりです。

`ActuationReporter` は、オーケストレータが蓄積器を読むための狭い `runtime_checkable` なプロトコルです。メソッドは `drain_actuations()` の1つだけです。このプロトコルを実装しないバックエンドは何も記録せず、run の挙動は変わりません。`bajutsu/drivers/base.py` の `ViewportProvider`、`ReadLagProvider`、`SettledReadProvider` が、すでに同じ形で機能しています。

モジュールを `bajutsu/drivers/base.py` への追記ではなく新規にするのは、`base.py` 自身の docstring がそのファイルを、すべてのバックエンドが依存する凍結された要と呼んでいるからです。新しいモジュールは、[`pyproject.toml`](../../pyproject.toml) で `bajutsu.drivers.base` を挙げている2つの import-linter 契約の両方に加えます。1つは決定的なコアが周辺から独立していることの契約で、もう1つは移植可能な内側の契約です。これにより、記録の型が `serve`、`triage`、エージェント、オーケストレータ、runner、レポートへの依存を持つことは、できなくなります。

### 作業分解（MECE）

1. **記録、蓄積器、プロトコル。** `bajutsu/drivers/actuation.py` を追加し、`Actuation`、`ActuationLog`（上限512の `deque`。捨てた件数を数えて警告する）、`ActuationReporter`、および前述の2つの語彙タプルを置きます。`pyproject.toml` で `bajutsu.drivers.base` を挙げている2つの契約、つまり周辺からの独立の契約と移植可能な内側の契約の両方の `source_modules` に、モジュールを加えます。片方だけでは、もう片方が守る import が無防備に残ります。この作業単位では挙動は変わりません。`Actuation` を構築するコードが、まだないからです。

2. **各バックエンドが、実際に行ったことを記録する。** すべてのドライバが `ActuationLog` と `drain_actuations()` を持ち、行ったプリミティブごとに `Actuation` を1つ記録します。

   各記録は「試行」であり、通信の結果がわかる前に書きます。そのため、操作に失敗したステップ（`stale` の再試行を使い切った、チャネルがエラーになった、常駐エンドポイントが処理を断った）でも、何を試したかは残ります。合否を運ぶのはステップ自身の `ok` と `reason` であり、作業単位3の drain は合否によらず走ります。したがって Android の縮退は、1回の `tap` に対して2件を、読む人が必要とする順で記録します。断られた `identity` の試行、続いて行った `coordinate` の注入の順です。

   ただし、試行を記録するのは半分にすぎません。答えを返す2つの経路、つまり XCUITest の handle 操作と Android の `/act` エンドポイントでは、プラットフォームが拒否でき、ドライバが再試行します。答えが届いた時点で、ドライバが記録にそれを刻みます（`ActuationLog.settle`）。これがないと、stale で再試行したタップは同一内容の記録を3件残し、どれを端末が受け入れたのかを示すものが何もなくなります。レポートは1回のタップを3回として描いてしまいます。個別の答えを返さない経路は、成功を主張せず `accepted` を未設定のままにします。Android の「要求は出たが応答が失われた」場合も、この読み方になります。

   記録先は、具体的な値がすでに置かれている場所なので、二重に解決するドライバは1つもありません。
   - **XCUITest**（`bajutsu/drivers/xcuitest.py`）は、記録を1か所、`_actuate` で構築します。handle 経由のジェスチャは、すべてこの関数を通ります。ただし現在の `_actuate` が受け取るのは `path`、`body`、`sel` だけで（`xcuitest.py:550`）、要素はスコープにありません。そこで解決し直せば `/elements` の往復が1回増え、第2の規則を破ります。そのため `_resolve_handle` は、すでに手元にある `(handle, element)` の組を返すようにします。5つの呼び出し側（`tap`、`double_tap`、`long_press`、`pinch`、`rotate`。`xcuitest.py:585-606`）が、その要素と自分の `gesture` 名を `_actuate` へ渡します。名前を渡すのは意図的です。`_actuate` がリクエスト本体から推測する形は採りません。`body["taps"] == 2` から `doubleTap` を導けば、通信の形に関する知識が記録側にも重複します。その形が変わった日に、黙って誤ったラベルを付けるようになるからです。`via` は `handle` で、`points` は空です。

     `stale` 応答は handle を再解決します（[BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve-ja.md)）。そのため `_actuate` は、自身の再解決で要素を上書きします。記録が示すのは、「最後の試行」が解決した要素、つまり実際に操作された要素になります。

     `_actuate` を通らず自前の通信を持つプリミティブが8つあるので、それぞれが送信する場所で記録します。
     - `tap_point`（`xcuitest.py:594-598`）、`swipe`（`:608-611`）、`scroll`（`:625-632`）は、`via: coordinate` としてその点を記録します。
     - 文字系のプリミティブ（`type_text`、`delete_text`、`select_all`、`copy_selection`。`xcuitest.py:681-699`）は、`via: focused` として記録します。要素を指定せず、フォーカスを持つ入力欄に作用するからです。
     - `handle_system_alert`（`xcuitest.py:639`）も自前で `/systemAlert/tap` を送るので、`gesture: systemAlert` として記録します。その `target` は通常は未設定です。SpringBoard のアラートボタンは見えている label で指定され、識別子を持たないことが多く（`xcuitest.py:665-675`）、label の記録は第3の規則が禁じているからです。したがって、作業単位3のガードのタップの載り先を実際に成り立たせるのは、中断した先のステップに記録が存在することです。そのステップがすでに持っている `alerts[].label` のボタン名とあわせて読めます。

     構造上、記録を自分では生まないプリミティブが iOS に2つあるので、明示しておきます。`back` は `tap` を通して OS の戻るボタンを叩きます（`xcuitest.py:676-679`）。そのため iOS の `back` ステップは、`BackButton` に対する `gesture: tap` / `via: handle` として記録され、iOS の記録が `gesture: back` を持つことはありません。`select_option` は `UnsupportedAction` を投げるので、何も記録しません。`unit` は全体を通じて `point` です。
   - **Android**（`bajutsu/drivers/adb.py`）は、2つの経路を区別して記録します。`_device_act` は `via: identity` として、解決した要素の正規化済み識別子を `target` に置きます。あわせて解決した frame を置き、点は置きません。座標を選んだのは端末だからです。端末へ送る `NodeIdentity` のタプルは `target` に置きません。第3の規則が述べた秘匿情報の理由によります。座標へ縮退する経路は、`via: coordinate` として注入した点を記録します。内部の解決ヘルパ（`_center`、`_center_with_screen`、`_resolve_frame_and_screen`）は解決済みの要素も返すので、`target` と `frame` を一緒に得られます。

     root 権限で `sendevent` を使う double-tap は、その点をスケールして得る生のタッチデバイスの範囲ではなく、ツリー座標系の点を記録します。生の範囲は注入方法の副産物です。それを記録すると、同じ要素への2回の double-tap が別々の座標に見えてしまいます。`unit` は `pixel` です。`back` は `via: key` として座標なしで記録します。`KEYCODE_BACK` イベントの実態と一致する形です。文字系のプリミティブは、iOS と同じく `via: focused` として記録します。
   - **Playwright**（`bajutsu/drivers/playwright.py`）は、マウスとタッチのプリミティブを `cssPixel` の `via: coordinate` として、文字系のプリミティブを `via: focused` として、`back` を `via: history` として記録します。`select_option` を実装している唯一のバックエンドでもあります（`playwright.py:697`）。これは例外を投げるのではなく、要素を解決して計算した中心で値を設定する本物の操作です。そのため `gesture: selectOption` を座標として記録します。option の文字列は、第3の規則に従っていっさい残しません。
   - **WebView のコンテキストドライバ**（`bajutsu/webview.py`）は、独自の経路ではなく `via: coordinate` として記録します。`tap`（`webview.py:128-134`）と `double_tap`（`:141-143`）は、要素の frame 中心を自分で計算してブリッジに点を渡します。`tap_point`（`:138-139`）は、呼び出し側の点をそのまま渡します。いずれの場合も WebView へ座標が渡っており、第1の規則が求めるのはその値です。点が属するのは端末の画面ではなく WebView 自身の座標系なので、`unit` は `cssPixel` で、記録は端末ではなく WebView に対して読みます。

     `tap` は、対象を表示範囲に入れるため、先に `scroll_to` を要素 id で呼びます。このスクロールも `via: bridge` の1件として記録します。タップの点を計算した対象の内容を動かす操作であり、まさにこの記録が可視化しようとしている失敗の型そのものです。落とすと、読む人が探しているものが消えます。`type_text` は `via: focused` として記録します。この最初のスライスでは、他のプリミティブはすべて `UnsupportedAction` を投げるので、何も記録しません。
   - **`FakeDriver`**（`bajutsu/drivers/fake.py`）は、記録のために計算した frame 中心を `via: coordinate` として記録します。第1の規則に照らしても正当です。点を選ぶ主体は fake 自身であり、fake は端末がメモリである座標系のバックエンドだからです。中心を取るのは `query()` の空間、つまり fake のスクロール可能ビューポートのモデルが適用するスクロールオフセットで平行移動したあとの空間です（`fake.py:80-91`）。一意性の確認が解決に使う、平行移動前の `self.screen` の空間ではありません。こうすることで、記録された点は、オーケストレータが `swipe` に渡す点と同じ意味を持ちます。

     `unit` は `point` にします。任意ですが固定した選択です。fake に与える frame は実在する端末のどの空間にも属しません。空のままにすると、fake は記録を統一して読めない唯一のバックエンドになってしまいます。

     fake は操作系の表面をすべて実装しており、作業単位6の `tests/orchestrator/` のケースが fake の上で走るので、その全体を覆う必要があります。`back` は `via: key`（`fake.py:138`）、文字系のプリミティブと `select_option` は実バックエンドと同じ形（`fake.py:149-164`。`select_option` は例外を投げずに成功します）、`handle_system_alert` は `gesture: systemAlert`（`fake.py:166`）として記録します。スタブではなく実在する座標を記録することが、端末なしの高速なゲートで、作業単位6が正確な形状を検証できる根拠になります。

3. **ステップループが蓄積器を結果へ移す。** `StepOutcome` に `actuations: list[Actuation]` を追加します。`_handle_action`（`bajutsu/orchestrator/loop.py`）は、ステップ本体が終わった直後、`outcome.ok` と `outcome.reason` を代入し終えた位置で、`active_driver` から1回 drain します。

   試行ごとではなく1回にするのは、意図的です。反応型のシステムアラートガードがアラートを閉じて本体を再試行した場合、どちらの試行の actuation も端末に実際に起きた出来事です。drain したリストは、起きた順に両方を保持します。同じ理由で、ガード自身がアラートを閉じるために行ったタップは、中断した先のステップに載ります。そのステップを読む人が探す場所が、そこだからです。

   drain の対象は `self.cfg.driver` ではなく `active_driver` です。`web` ブロックの内側のステップが操作するのは WebView のドライバであり、そのようなステップの最中にネイティブのドライバが操作されることはありません。そのため、取り残される記録はありません。

   載せる先のステップがない actuation が、1つだけあります。ガードはシナリオ末尾の `expect` の再チェックのためにも発火し、そのときはステップループの外にいます（`loop.py:482`）。だからこそ `RunResult` は、どのステップにも属さない `expect_alerts` を別に持っています。その隣に `expect_actuations` を追加し、同じ位置で drain します。ガードのタップは、蓄積されたまま黙って捨てられるのではなく、記録されます。どちらの経路でも、シナリオ間に漏れることはありません。シナリオごとに環境が新しいドライバを組み立てるので、蓄積器はリースとともに消えます。

4. **manifest が記録を運び、ローダが読み戻す。** `manifest_dict` は `StepOutcome` を `asdict` で直列化するので、記録は `manifest.json` に届きます。`bajutsu/report/manifest.py` に必要な変更は、`SCHEMA_VERSION` を5に上げ、この項目を指すコメントを添えることだけです。これにより、古い run は新しい表示が失敗するのではなく、単に欠けた状態で描画されます（BE-0068 の契約）。

   バージョンを主張している箇所が2つあり、あわせて動かさないとゲートが赤くなります。`tests/report/test_load.py:69` は `data["schemaVersion"] == 4` を検証し、`docs/reporting.md` は散文で2回この数値を固定しています（作業単位7）。

   `bajutsu/report/load.py` は、`_step` に1行、`_result` に1行（作業単位3の `expect_actuations` のため）を加えて、入れ子の記録を復元します。あわせて小さな変換も加えます。JSON にタプルはないので、`points` と `frame` はリストとして届きます。これを、型が宣言しているタプルへ戻します。この変換がなければ、既存の `test_round_trip_through_manifest_is_lossless` が失敗します。そのテストは、まさにこれを検出するために存在しています。

5. **2つの表示面が記録を見せる。** HTML レポートは、actuation を行ったステップの下に1行を追加します。既存の `alertrow` と同じ形（`bajutsu/templates/report.html.j2`、`bajutsu/report/rows.py`、`bajutsu/templates/report.css`）で、ジェスチャ、経路、解決した target、形状を示します。
   - 座標のあるタップなら `tap → (128.0, 460.5) pt on settings.reindex`。
   - ドライバが座標を選んでいない場合は `tap → settings.reindex [124, 448, 96, 44] via handle`。

   作業単位3のシナリオ単位の `expect_actuations` も、`alertrow` の形が持つシナリオ単位の対応物と同じ扱いで表示します。`rows.py` はステップの `alerts` を `rows.py:164` で、expect ブロックの `expect_alerts` を `rows.py:392` で組み立てています。そのため、新しいフィールドは後者の隣に描画します。書き出したのにどこにも出ない状態にはしません。

   `bajutsu trace` のタイムラインは、ステップごとの行に同じ要約を追加します（`bajutsu/trace.py` の `_step_event`）。この関数は manifest の dict を直接読むので、ローダ側の変更は要りません。expect 段階の記録はレポートだけに出します。タイムラインには、記録を載せる expect の行がないからです。`serve` の web UI は、意図してスコープ外にします。`serve` は独自の run 表示を描画しており、そこへ広げるのは、別の表示面への別の変更です。

6. **決定的なテスト。** 作業単位2によって fake が実在する座標を記録するので、`tests/orchestrator/` に置く `FakeDriver` ベースのテストは、正確な形状を検証できます。
   - `tap` は、解決した要素の frame 中心とその識別子を記録する。
   - 方向指定の `swipe` は、`_scroll_gesture` が計算する端点と一致する2点を記録する。
   - `long_press` は、duration を記録する。
   - `pinch` は、scale と解決した frame を記録する。

   これらのケースは fake の素の（スクロールしない）モードを使い、記録された点と与えた frame が同じ空間に属する状況で検証します。さらに1つのケースで、スクロール可能ビューポートのモードをスクロール後に走らせ、記録された点が「平行移動後」の中心であることを確認します。これで、作業単位2が選んだ空間を固定します。

   どのステップに載るかは、独立した性質としてテストします。各ステップの結果は、そのステップが行った actuation だけを持ちます。操作しない `assert` ステップは、1つも持ちません。アラートガードのもとで本体が2回走ったステップは、両方の試行を順に持ちます。

   実バックエンドごとのドライバのユニットテストで、経路を確認します。Android のドライバは、常駐エンドポイントがジェスチャを処理したときに `identity`、処理を断ったときに `coordinate` を記録します。XCUITest のドライバは、`points` が空の `handle` を記録します。

   秘匿情報の境界には、独立したケースを置きます。ここでの漏洩は無言で起こるからです。
   - label は持つが識別子を持たない要素を操作したステップは、`target` をまったく記録しない。
   - `type` ステップの記録は、そのテキストに由来するフィールドを何も持たない（テキストも、その長さも持たない）。
   - Android の `identity` の記録は、端末へ送った `NodeIdentity` タプルのどの要素でもなく、正規化済みの識別子を持つ。このケースは、`content-desc` と `text` が `resource-id` と異なる dump を与えて検証するので、タプルへ戻る退行は通りません。

   `tests/orchestrator/test_read_count.py` のケースは、drain が read を出さないことを示し、そのモジュールが守るために存在する不変条件を保ちます。往復のテストは、actuation を含む manifest から等しい記録が復元されることを示します。`bajutsu trace` のテストは、タイムラインの行が manifest の dict から描画されることを示します。

7. **文書。** [`docs/evidence.md`](../../docs/evidence.md) と日本語版の [`docs/ja/evidence.md`](../../docs/ja/evidence.md) では、`actionLog` の行を「オーケストレータ内部（操作と所要時間）」から、実際に記録される内容を示す記述へ変えます。あわせて、秘匿情報の境界（入力された文字そのものも、その文字数も、label も記録しない）と、ドライバは自分が選んだ座標だけを記録するという規則を加えます。

   [`DESIGN.md`](../../DESIGN.md) の §9 の証跡種別の表にある `actionLog` の行も、同じように改訂します。§10 の出荷基準チェックリストにある座標への縮退の明示の項目（`DESIGN.md:543`）には、この項目が閉じた半分だけを、過不足なく書き足します。

   この項目が指す stability ladder の段は、Android のものです。§5（`DESIGN.md:191`）は、常駐エンドポイントが使えないか identity を特定できないために座標へ落ちた `tap` / `longPress` / `doubleTap` に、その縮退を限定しています。記録の `identity` と `coordinate` の対が、それをステップごとに示します。トークンだけを見て縮退と読むことはできません。`coordinate` は、Playwright、WebView のドライバ、fake、iOS の `swipe` / `scroll` / `tap_point` では、通常の縮退していない経路でもあるからです。書き足す文もそのことを述べ、`coordinate` の記録がすべて縮退であるかのようには書きません。`index` セレクタへの縮退は、actuation ではなくセレクタ解決の問題であり、未解決のまま残ります。そのため、項目は残った半分を明記したうえで、未チェックのままにします。

   `actionLog` を瞬時系の証跡種別として挙げている [`docs/architecture.md`](../../docs/architecture.md) と日本語版の [`docs/ja/architecture.md`](../../docs/ja/architecture.md) も確認し、必要に応じて合わせます（BE-0113）。

   この項目が偽にしてしまう記述を散文で持つページが、さらに4つあります。それぞれ日本語版とあわせて直します。
   - [`docs/reporting.md`](../../docs/reporting.md) と [`docs/ja/reporting.md`](../../docs/ja/reporting.md) は、`steps[].duration_s` を「`actionLog` 相当の情報」と書いています（`reporting.md:69`）。schema のバージョンも、散文で「現在は `4` です」と固定しています（`reporting.md:80`）。固定はもう1か所あり、「このブロックが出るようになった時点で `schemaVersion` は `4` です」と書いています（`reporting.md:89-90`、`docs/ja/reporting.md:71`）。こちらは下限ではなく厳密な言い方なので、5になると偽になります。
   - [`docs/run-loop.md`](../../docs/run-loop.md) と [`docs/ja/run-loop.md`](../../docs/ja/run-loop.md) は、同じ `actionLog` 相当という記述を `StepOutcome` のフィールド一覧の中で繰り返しています（`run-loop.md:111`）。その一覧には `actuations` を、`RunResult` の一覧には `expect_actuations` を加えます。

### 機械的に検証できる結果

端末とモデルのどちらも使わない決定的なテストスイートで、次の5つを検証します。

- `FakeDriver` で走るシナリオの `StepOutcome` が、`points` が解決した要素の frame 中心に等しい `Actuation` を持つこと。テスト側は、その中心を与えた frame から独立に計算します。
- 2ステップのシナリオが、各ステップの actuation をそれぞれの結果に保つこと。
- label と識別子の両方を与えた要素で、ステップが記録する `target` が識別子であって label ではないこと。
- manifest の往復が、等しい記録を復元すること。
- read 数のテストが固定している0が、0のまま保たれること。

判定するのは `make check` であり、この記録が判定に入ることは一度もありません。アサーション、wait、extract のどれも読みません（第1原則）。

## 検討した代替案

**各 `Driver` の actuator が記録を返す形にする。** `tap(sel) -> Actuation` に変えれば、記録はオプトインのプロトコルではなく型レベルの保証になります。バックエンドが記録を作り忘れることは、できなくなります。しかし、これは得られるものに対して波及範囲が大きすぎるので採りません。`Driver` の操作系メソッドは10個あまりあり、そのすべてが action ハンドラ、crawl、`record` の再生、アラートガード、適合性スイートから呼ばれています。呼び出し側は、欲しくない戻り値を受けて捨てる変更を、全箇所で被ります。オプトインのプロトコルなら、この項目の中で同じ網羅に到達できます（作業単位2で出荷済みの全バックエンドが実装します）。その churn もありません。ドライバの表面が直近4つの任意機能を吸収してきた形にも、一致します。

**座標を運用ログに出す。** Android のドライバの `logger.debug` 2行が、すでにこれを行っており、その不十分さが動機そのものです。ログ行は証跡ではありません。run の前にレベルを上げていなければ存在せず、run のディレクトリとともに移動せず、比較対象であるスクリーンショットと要素ツリーの隣に描画できません。構造化された運用ログ（[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging-ja.md)）も、決定的な `run` の経路から意図して外した `serve` モードの関心事だと明示しており、同じ線を引いています。証跡はテスト対象の軌跡であり、運用ログはツール自身の軌跡です。

**ステップごとに `actionLog.json` を書く。** manifest の代わりに、`elements.json` のようにステップごとのファイルにする案です。記録は小さく構造化されていて、置き場所がすでにあるので採りません。manifest は run の唯一の真実であり、`asdict` が記録を無償で運びます。`DESIGN.md` の §9 は、`actionLog` をファイルではなく manifest に内在するものとして規定しています。別ファイルにすると、ステップごとの書き込みが1回増え、すべての利用側で解決すべきパスが増えます。そしてステップの真実が置かれる場所は、2つになります。

**ドライバより上で、要素ツリーから座標を再構成する。** オーケストレータが自分でセレクタを解決し、計算した frame 中心を記録する案です。結果が推測を測定値として差し出すものになるので、採りません。記録されるのは、オーケストレータが使ったはずの座標です。iOS では XCUITest が触れた点と一致せず、Android では常駐エンドポイントがジェスチャを処理したときに端末が選んだ点と一致しません。どちらの場合も、古いツリーが原因で実際のジェスチャが対象を外した点とも一致しません。記録が可視化しようとしている失敗そのものを、取り違えます。加えて、ステップごとに端末の read を1回増やし、作業単位6が固定する不変条件を壊します。

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
