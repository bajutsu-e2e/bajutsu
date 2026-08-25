[English](BE-0392-scenario-before-after-hooks.md) · **日本語**

# BE-0392 — シナリオの steps から独立した before/after ライフサイクルフック

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0392](BE-0392-scenario-before-after-hooks-ja.md) |
| 提案者 | [@akira-matsuda](https://github.com/akira-matsuda) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0392") |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md)、[BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)、[BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md) |
<!-- /BE-METADATA -->

## はじめに

新しいトップレベルのシナリオフィールド `before` と `after` を追加します。前処理・後処理を、シナリオ
本体の `steps` 列に混ぜ込むのではなく、ランナーが別扱いで追跡できるようにする宣言です。`before` は
最初に実行する手順の順序付きリストで、レポート上も独立した区画を持ち、失敗すればシナリオを `steps` の
手前で打ち切ります。`after` は、結果（`always` / `success` / `error`）と、そのときに実行する
手順を組にしたルールのリストです。テスト用に借用したレコードは毎回必ず解放し、成功したときだけ自分が
作成したデータを削除し、失敗したときだけ追加の診断情報を集めます。この3つの関心事は、今は同じシナリオ
ファイルの中にありながら、切り分ける手段がありません。`before` と `after` はどちらも既存の決定論的な
step・アサーション DSL（ドメイン固有言語）をそのまま使うため、シナリオ本体の手順と同じだけ機械的に
検証可能です。

## 動機

Bajutsu にはすでに「前」の仕組みがありますが、独立してはいません。`preconditions.setup` は再利用可能な
前提シナリオファイルを指定するフィールドで、`apply_setups`
（[`bajutsu/scenario/expand.py:153`](../../bajutsu/scenario/expand.py)）が、実行が始まる前にその
前提シナリオの手順をシナリオ本体の `steps` の先頭へそのまま連結します。連結された手順は、以降シナリオ
本体の手順と見分けが付きません。レポートは同じ通し番号の列として並べ、前提シナリオ側の手順が失敗しても
ただの手順失敗としか表示されず、それが `setup` 由来だという印は残りません。前処理を、レポートを見る
レビュアーが一目でシナリオ本体と区別できる独立した区画として扱いたくても、今はその手段がありません。

一方「後」の側にあるギャップは、部分的な不足ではなく仕組みそのものの不在です。Bajutsu には「後」の
フックが一切ありません。後片付けを置ける場所は今のところ `steps` の末尾しかなく、その置き方は
後片付けが本来必要な場面でこそ機能しません。ステップループは最初の失敗で打ち切られるため
（[run-loop](../../docs/run-loop.md) の手順9）、末尾の後片付け手順が実行されるのは手前の手順が
すべて成功したときだけです。つまり後片付けがもっとも要らない実行でしか動きません。テストユーザーを登録した
シナリオが3手順後に壊れたボタンに当たって失敗すれば、そのユーザーは残ったままになります。削除する
はずだった手順に、実行が一度も到達しないからです。シナリオファイルの外に置く回避策——CI 側のスクリプトや
別スケジュールの後片付けジョブ——も解決になりません。その実行自身の `vars.*` バインディングが見えないため、
同じ実行の中で `http` 手順の `saveBody` が捕まえた特定のレコードを狙って処理できませんし、失敗したときだけ
追加の診断情報を集めたいシナリオと、成功したときだけテストデータを削除したいシナリオを区別する手段も
ありません。

ランナー自身は、「シナリオ本体の手順の後で、かつ実行結果を踏まえて動く」段階をすでに別の場所で扱っています。
この項目は、その形を一般化するのであって、新しく考案するのではありません。シナリオレベルの `expect`
ブロックは `steps` が終わったあとに厳密に実行され、手順がすべて成功したシナリオでも、その結果を失敗へ
反転させることがあります（`run_scenario`、
[`bajutsu/orchestrator/loop.py:478`](../../bajutsu/orchestrator/loop.py)）。`capturePolicy` の
`Trigger.result: Literal["error"]`
（[`bajutsu/scenario/models/evidence.py:22`](../../bajutsu/scenario/models/evidence.py)）は、手順が
失敗したときだけ特定のルールを発火させる仕組みです。判定の対象は実行を通してとらえた1手順の結果であり、
シナリオ全体の判定ではありませんが、同じ `error` という結果の語に紐付いています。
不足しているのは、この2つと対称な段階、すなわち固定のアサーション判定でも証跡の収集でもなく任意の手順列を
アクションに取り、しかもシナリオの実行後だけでなく実行前にも置ける段階です。

この仕組みが実装されれば、著者は今日との違いを2つ、具体的に確認できるようになります。1つ目は、シナリオの
レポートに、通し番号の付いた `steps` とは別の「Before」区画と「After」区画が現れることです。今日の
`setup` 前提シナリオは、それ自体としては見えず本体の手順列に埋もれています。2つ目は、手順が途中で失敗した
シナリオでも、宣言した `error` / `always` の後片付けが実行されることです。今日であれば、ループがすでに
打ち切られたあとのため、同じ末尾の手順は一度も実行されません。

## 詳細設計

### `before` フィールドと `after` フィールド

```yaml
# scenario.yaml
scenario:
  before:
    # このシード用エンドポイントは、新規ユーザーの id そのものをレスポンス本文として返す
    - http: { method: POST, url: "${vars.apiBase}/users", saveBody: userId }
  steps:
    - tap: { id: login.button }
    - type: { id: login.username, text: "${vars.userId}" }
    # ...
  after:
    - on: always
      steps:
        - tap: { id: session.logout }
    - on: success
      steps:
        - http: { method: DELETE, url: "${vars.apiBase}/users/${vars.userId}" }
    - on: error
      steps:
        - http: { method: POST, url: "${vars.diagnostics}/report", body: { userId: "${vars.userId}" } }
```

この例の `before` 手順は、既存の `http` が持つ `saveBody` をそのまま使っています。`saveBody` は
レスポンス本文全体をテキストとして `${vars.<name>}` に保存する仕組みで、シード用エンドポイントが
本文として id 以外を返さないよう作られているからこそ、それだけで id を捕まえられます。より大きな
JSON レスポンスから1つのフィールドだけを取り出す仕組みは別のギャップであり、この項目が解決を
提案するものではありません。

`before` は素直な `list[Step]` で、分岐のない順序付きの前処理です。分岐する対象となる結果が、まだ
存在しないからです。`after` は `AfterRule` のリストで、各エントリは結果を表す `on`
（`always` / `success` / `error`）と、そのとき実行する `steps` の組です。同じ `on` の値を持つ
エントリを複数書いてもかまいません。宣言順に合成され、`capturePolicy` がすでに同じトリガーへ複数の
ルールを発火させられるのと同じ形です。`on` の2つの結果語、`success` と `error` は、`capturePolicy` の
`Trigger.result: Literal["error"]` がすでに「失敗した」という結果に充てていた語を拡張したものです。
`capturePolicy` では1手順の失敗、ここではシナリオ全体の判定という違いはありますが、同じ概念に別の語を
新しく充てているわけではありません。`always` は、結果に関わらない後片付けのために唯一新しく加えた語です。

`before` と `after` はどちらも target-config レベルにも存在します（`TargetConfig.before` /
`TargetConfig.after`）。シナリオ側の `before`/`after` がその上に積み重なる、アプリ全体のデフォルト値です。
これは `interrupts` がすでに確立した config 優先・シナリオ追加の階層と同じです
（[`bajutsu/config/schema.py:403`](../../bajutsu/config/schema.py)）。`before` は config・シナリオの
順で合成します。アプリ全体の前処理が先に走り、そのあとにこのシナリオ固有の追加が続くという、`interrupts`
と同じ順序です。`after` は逆に、シナリオ・config の順で合成します。シナリオ固有の後片付け(たとえば自分が
作成したレコードの削除)が、アプリ全体の後片付け(たとえばログアウト)より先に実行されます。これは、内側のリソースの解放が外側のリソースより先に行われる順序を踏襲したものです。ほとんどの
フィクスチャベースのテストフレームワークが、すでに setup/teardown の組に「後に確保したものを先に
解放する」という同じ順序を与えています(pytest のフィクスチャの後始末を、この項目が依存する仕組みでは
なく先例として引用しています)。

### ランナーへの組み込み

`run_scenario`（[`bajutsu/orchestrator/loop.py:478`](../../bajutsu/orchestrator/loop.py)）に、既存の
`steps`/`expect` の前後を挟む2つの段階を追加します。どちらの段階も、`if` や `forEach`、
`_InterruptGuard` の復旧手順がすでに共有している再帰的な `_ExecSteps` クロージャ
（[`bajutsu/orchestrator/loop.py:635`](../../bajutsu/orchestrator/loop.py)）を使って動くため、
`before`/`after` の手順は他のどの手順とも同じ能力を持ち、それらの既存の利用箇所と同じ形で実行の
`live_bindings`（`vars.*`）を共有します。

1. **`_run_steps` が動く前に**、有効な `before` リストを実行します。ここで失敗すると
   `failure = "before: " + 理由` が設定され、`steps` と `expect` はどちらも実行されずに終わります。
   `before` はシナリオの中の1手順ではなく、シナリオを実行するための前提条件です。これは、対象が満たせない
   `preconditions` の値がある場合に、ランナーがそもそもシナリオを開始しないのと同じ扱いです。起動段階で
   `simctl.DeviceError` が送出され(`launch_driver`、
   [`bajutsu/runner/launch.py:27`](../../bajutsu/runner/launch.py))、`run_scenario` が呼ばれる前に
   失敗します。`apply_setups` は `setup` 前提シナリオを展開時に `steps` の先頭へ連結するため
   （[`bajutsu/scenario/expand.py:176`](../../bajutsu/scenario/expand.py)）、`_run_steps` より前に
   走る `before` 段階は、その前提シナリオよりも前に走ることになります。つまり `before` は、前提
   シナリオとシナリオ本体の両方がその後で使う状態を用意する場所であり、`before` の手順が前提シナリオの
   到達する画面に依存してはいけません。`TargetConfig.before` も同じ規則に従います。アプリ全体の
   `TargetConfig.setup` 前提シナリオを置き換えるのではなく、その前に走ります
   （[`bajutsu/config/schema.py:356`](../../bajutsu/config/schema.py)）。独立したレポートの区画を
   持つのは `before` だけなので、2つは別の仕組みのまま残ります。
2. **`steps` と `expect` は変更なく実行されます。** この項目は、両者の結果判定そのものには手を加えません。
   `before` の失敗は、後述する手順3にとって `error` という結果として扱います。`steps`/`expect` が
   失敗した場合と同じ扱いです。`before` が途中まで作った状態があるなら、その後片付けも必要だからです。
3. **判定が確定したあと**——`steps`/`expect` が最後まで実行された場合も、`before` が失敗して両者を
   飛ばした場合も、実行がキャンセルされた場合(`RunCancelled`。`run_scenario` がすでに
   `failure = CANCELLED_FAILURE` を設定する箇所で捕捉します。
   [`bajutsu/orchestrator/loop.py:607`](../../bajutsu/orchestrator/loop.py))も——有効な `after`
   リストを宣言順に実行し、`on` が `always` にもその判定にも一致しないエントリを飛ばします。`on` で
   グループにまとめるのではなく交互に並べたまま実行するため、前述のシナリオ・config の順序が1つの
   `on` グループの内側にとどまらず、段階全体にわたって保たれます(キャンセルされた実行は `error` として
   扱います。`docs/run-loop.md` がキャンセルされた実行を通常の失敗と同列に位置づけているのと同じ
   扱いです)。`after` エントリ自身が失敗した場合、
   それまで実行が成功していたときに限り `failure` を更新します
   (`failure = "after: " + 理由`。すでに成功していた `steps` の結果を `expect` が反転させられるのと
   同じ形です)。上記のいずれかの理由ですでに失敗していた場合は、`after` エントリの失敗は既存の
   `failure` 文字列を置き換えるのではなく、そこに追記します。読み手がまず目にする理由が、後片付けの
   副作用ではなく元の原因のままであるようにするためです。
4. `after` 段階は、`cancelled` をそのまま読むわけにはいきません。`CancelSource` はラッチされた
   `threading.Event.is_set` であり（[`bajutsu/cancellation.py:147`](../../bajutsu/cancellation.py)）、
   猶予時間内の2回目の `SIGTERM` は意図的に何もしないためです。つまり、`after` を `error` として
   ディスパッチするまさにその経路で、`after` の内側の最初のキャンセル判定がふたたび `RunCancelled` を
   送出し、後片付けの手順が1つも実行されないことになります。代わりに、この段階は時間で区切ります。
   キャンセルされた実行では、`after` は `cancelled` を読むのをやめ、`grace_seconds()`
   （[`bajutsu/cancellation.py:76`](../../bajutsu/cancellation.py)）から切り出した独自の期限のもとで
   実行し、その期限を過ぎた時点で残りのエントリを放棄します。こうすれば、`serve` が無条件の強制終了
   までに待つ猶予時間を終了処理がはみ出すことなく、後片付けに時間を区切った実行機会を与えられます。
   キャンセルされていない実行では、`after` は他のどの手順とも同じように各境界で `cancelled` を読みます。
   いずれの場合も、この段階は `steps`/`expect` から抜けるすべての経路——`RunCancelled` がすでにたどる
   経路を含めて——で到達するため、`after` は無条件に終了処理を行う既存の
   `finally: artifacts = sink.finish_scenario_intervals(...)` より前に実行されます。

### レポート

`RunResult`（[`bajutsu/orchestrator/types.py:171`](../../bajutsu/orchestrator/types.py)）に、
`before_outcomes: list[StepOutcome]` と `after_outcomes: list[StepOutcome]` を追加します。どちらも
`steps` や `expect_results` のどちらかに畳み込むのではなく、その隣に置きます。`expect_results` が
すでに受けているのと同じ切り分けです。レポートの Steps タブは、シナリオ本体の通し番号付き手順とは別に
「Before」区画と「After」区画を描画します。レビュアーが前処理・後処理をシナリオ本体と一目で区別できる
ようになり、この項目の *動機* が挙げた「今日の `setup` 前提シナリオにはそうした印が残らない」という
ギャップを埋めます。

### codegen

`interrupts`（BE-0314）はネイティブに対応する構文を持たず、どこでもコメントへのフォールバックに
頼っています。それに対して `before` と `after` の `always` エントリは、各 codegen 対象がすでに
備えている構文へそのまま対応付けられます。Playwright の `test.beforeEach`/`afterEach`、XCTest の
`setUpWithError`/`tearDownWithError`、Espresso/JUnit の `@Before`/`@After` です。`success`/`error`
による分岐には、それぞれのフレームワーク自身がテスト結果を後始末フックの中から読む手段が要ります
(Playwright の `testInfo.status`、XCTest の `testRun?.hasSucceeded`、JUnit の `TestWatcher`)。
対応する codegen 対象ではその分岐を出力し、まだ対応していない対象では、
[BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) と BE-0314が
他の箇所ですでに使っているラベル付き `// TODO` コメントへ回します。

### 作業分解(MECE)

1. **スキーマ。** `Scenario`
   （[`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)）と
   `TargetConfig`（[`bajutsu/config/schema.py`](../../bajutsu/config/schema.py)）の両方に
   `before: list[Step]` と `after: list[AfterRule]`（`AfterRule = { on: Literal["always", "success",
   "error"], steps: list[Step] }`）を追加します。どちらもデフォルト値を空リストとし、未設定のフィールドが
   dump から消えるようにします。`interrupts` と同じ扱いです。
2. **合成のヘルパー。** `before` は config・シナリオの順、`after` はシナリオ・config の順で合成します。
   `interrupts` の config/シナリオ合成がすでに解決している場所と同じ地点で1回だけ解決します。
3. **ランナーへの組み込み。** 前述の before 段階のゲート、after 段階の結果に応じたディスパッチ、
   `failure` 文字列の合成を `run_scenario` に実装します。
4. **レポート。** `RunResult` への `before_outcomes` / `after_outcomes` の追加、レポートレンダラーの
   独立した「Before」/「After」区画、そして `RunResult.steps` を直接読んでいるために before/after
   だけの失敗を取りこぼす2つの出力——JUnit の `<failure>` 本文を作る `_details`
   （[`bajutsu/report/manifest.py`](../../bajutsu/report/manifest.py)）と、CTRF の手順リスト
   （[`bajutsu/report/ctrf.py`](../../bajutsu/report/ctrf.py)）。`manifest.json` へは `asdict` が
   新しいフィールドをそのまま運ぶため、対応は不要です。
5. **codegen。** `always` エントリに対する `beforeEach`/`afterEach`(または各バックエンドの対応する
   構文)の出力、対応可能なバックエンドでの `success`/`error` に応じた出力、それ以外での TODO への
   フォールバック。
6. **ドキュメントとフィクスチャ。** [`docs/scenarios.md`](../../docs/scenarios.md) とその日本語訳に
   `before`/`after` を記載し、`preconditions.setup`(`steps` 内へ連結する前提シナリオ)と
   `capturePolicy`(同じ `error` という結果に紐付く証跡収集)を並べた比較表で、それぞれをどう使い分ける
   かを説明します。`before` でレコードを作成し、`after` の `success` ルールでそれを削除する showcase
   フィクスチャを追加します。
7. **テスト。** 両レベルでのスキーマのパース・検証、両方向の合成順序、結果判定の組み合わせ
   (`before` の失敗が `steps`/`expect` を飛ばし `after` を `error` としてディスパッチすること、
   `always` が結果に関わらず実行されること、`error` ルール自身の失敗が元の `failure` を置き換えず
   追記されること、他が成功しているときの `success` ルールの失敗がそのまま唯一の `failure` になること、
   キャンセルされた実行が `after` を `error` としてディスパッチしたうえで後片付けのエントリを実際に
   実行し(その経路ではラッチされた `cancelled` を読まない)、段階自身の期限を過ぎた時点で残りを
   放棄すること)、両段階への・からの `vars.*` の共有、新しい `RunResult` フィールド。他が成功して
   いるときの `success` ルールの失敗は、HTML のレポートだけでなく JUnit の失敗テキストと CTRF の
   レコードにも、そのルールの名前が現れなければなりません。

### prime directive との整合性

- **AI は判定しません。** `before`/`after` の手順は既存の決定論的な step DSL であり、`after` ルールが
  分岐に使う結果(`success`/`error`)はシナリオ自身がすでに機械的に確定させた判定であって、モデル呼び出し
  ではありません。この項目は新しい AI の経路を一切追加しません。
- **決定論を優先します。** 固定の `sleep` はありません。`before`/`after` の手順も、他のすべての手順が
  すでに使っている条件待機のプリミティブをそのまま使い、結果に応じたディスパッチも、すでに計算済みの
  `failure` 値に対する単純な比較です。
- **アプリ非依存です。** `before`/`after` は config・シナリオ側のデータであり、ランナーとレポートが
  獲得するのはアプリ固有のコードではなく、1つの汎用的な仕組みです。

## 検討した代替案

- **`setup` と対称な `preconditions.teardown` フィールドを追加し、`setup` と同じように `steps` へ
  連結する案。** 却下しました。連結する形は、この項目が解決しようとしている問題をそのまま残します。
  連結した後片付け手順は、手前の手順が失敗してループが打ち切られたあとには実行できませんし、レポート上も
  独立した段階ではなくただの手順として現れます。`steps` への連結をやめること自体が、この項目の要点です。
- **`capturePolicy` の `Trigger` に `result: "success"` を追加し、ルールのアクションを証跡の収集だけ
  でなく任意の手順にも広げる案。** 却下しました。`Trigger` は `action` や `event` にも一致し、
  ステップループ全体を通して機会をとらえて判定する仕組みに紐付いています。シナリオ全体でただ1つ確定する
  最終的な判定とは結び付いていません。これを流用すると、著者が `before`/`after` のつもりで書いたルールが
  実行の途中で意図せず発火しかねず、発火するタイミングと理由がどちらもまったく異なる2つの機能を1つに
  混ぜてしまいます。
- **`{on, steps}` のリストではなく、`always` / `onSuccess` / `onFailure` という3つの固定キーにする案。**
  リストの形を採りました。このスキーマがすでに持つ結果分岐のフィールド——`capturePolicy`、
  `interrupts`、`systemAlertHandling.rules`——は、どれも固定形のオブジェクトではなく、自分自身の条件を
  持つエントリのリストです。リストなら合成できます(別の関心事のために2つ目の `error` エントリを追加
  できます)が、3つの固定キーではそれができません。
- **`after` フックに、通常の手順とは別のリトライやタイムアウトの方針を持たせる案。** 却下ではなく
  見送りです。あとから追加することを妨げるものはこの設計にはありません。この項目の *動機* が挙げている
  ギャップは、手前の手順が失敗したあとでは後片付けがそもそも実行できないことであり、実行できるように
  なったあとの耐障害性ではありません。前者のギャップを埋めるために、後者まで同時に解決する必要は
  ありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] Unit 1 — `Scenario` と `TargetConfig` への `before: list[Step]` / `after: list[AfterRule]`
      スキーマ。
- [x] Unit 2 — `before` の config・シナリオ順の合成、`after` のシナリオ・config 順の合成。
- [x] Unit 3 — `run_scenario` へのランナー組み込み(before 段階のゲート、after 段階の結果に応じた
      ディスパッチ、`failure` 文字列の合成)。
- [x] Unit 4 — `RunResult` への `before_outcomes` / `after_outcomes`、レポートレンダラーの区画、
      JUnit の `_details` 本文と CTRF の手順リスト。
- [x] Unit 5 — codegen の対応付け(対応可能なバックエンドでの `beforeEach`/`afterEach` と結果に応じた
      出力、それ以外での TODO フォールバック)。
- [x] Unit 6 — ドキュメント(scenarios.md と日本語訳)の比較表、showcase フィクスチャ。
- [x] Unit 7 — テスト(スキーマ、両方向の合成順序、結果判定の組み合わせ、`vars.*` の共有、レポート
      フィールド)。

実装したもののうち 2 点は、上の詳細設計と異なります。どちらも実装中に判断しました。

- **`RunResult` には 3 つ目の新フィールド `after_verdict` を持たせました。** レポートは After の
  区画を、各結果とそれを宣言したルールを対応付けて描画します。しかし run がどのルールを実行したかは、
  後片付けのステップ自身の理由が `failure` に折り込まれたあとでは復元できません。段階が振り分けに
  使った判定を記録することが、この対応付けを正確にします。
- **`// TODO` フォールバックを必要とした codegen ターゲットはありませんでした。** すべての
  ターゲットがこの段階をネイティブに表現できます。Playwright と UI Automator は
  `beforeEach`/`afterEach` の組ではなく、テスト本体を `try` / `catch` / `finally` で包みます。
  フレームワークのフックは describe ブロックまたはクラス単位で登録するのに対し、`before`/`after` は
  シナリオ単位であり、かつ両ターゲットのアサーションは例外を投げるため `catch` が判定をそのまま
  観測できるからです。XCUITest は `addTeardownBlock` を 1 つだけ登録し、`testRun?.hasSucceeded` を
  読みます。`XCTAssert` は例外を投げずに失敗を記録するためです。

## 参考

- [`bajutsu/scenario/expand.py:153`](../../bajutsu/scenario/expand.py) — `apply_setups`。この項目が
  補う既存の「前」だけの仕組みで、その手順は独立した段階としてではなく `steps` へ直接連結されます。
- [`bajutsu/runner/launch.py:27`](../../bajutsu/runner/launch.py) — `launch_driver`。満たせない
  `preconditions` の値が、`run_scenario` が呼ばれるより前にすでにシナリオを失敗させている先例で、
  `before` 自身のゲート判定はこれを踏襲します。
- [`bajutsu/orchestrator/loop.py:478`](../../bajutsu/orchestrator/loop.py) — `run_scenario`。新しい
  before/after の段階がまさに組み込まれる場所です。
- [`bajutsu/orchestrator/loop.py:635`](../../bajutsu/orchestrator/loop.py) — `_ExecSteps`。
  `if`/`forEach`/`interrupts` がすでに共有している再帰的な手順実行クロージャで、`before`/`after` は
  これを複製せずそのまま再利用します。
- [`bajutsu/cancellation.py:147`](../../bajutsu/cancellation.py) — ラッチされた
  `threading.Event.is_set` としての `CancelSource` と、その上にある `grace_seconds()` の見積もり
  (最悪 60 秒の driver 呼び出しと 30 秒の終了処理)。時間の余裕がない後処理の段階を許さないこの2つが、
  `after` 段階をキャンセルのラッチではなく独自の期限で区切る理由です。
- [`bajutsu/scenario/models/evidence.py:22`](../../bajutsu/scenario/models/evidence.py) —
  `Trigger.result: Literal["error"]`。既存の `capturePolicy` の結果語で、この項目の `after` ルールは
  これを置き換えるのではなく `success` を加えて拡張します。
- [`bajutsu/orchestrator/types.py:171`](../../bajutsu/orchestrator/types.py) — `RunResult`。
  `expect_results` がすでに `steps` の中ではなくその隣に置かれている先例で、`before_outcomes`/
  `after_outcomes` はこれに倣います。
- [BE-0030 — パラメータ化された共有ステップ](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md) —
  既存の `setup` 前提シナリオと `use` によるコンポーネント再利用の仕組み。この項目の `before` フィールドは
  これを置き換えるのではなく補います。
- [BE-0033 — シナリオ変数と軽量な制御フロー](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md) —
  `before`/`after` の手順が踏襲する `vars.*` 共有の先例。
- [BE-0314 — 出現タイミングの読めない割り込み画面を決定論的に処理する](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md) —
  もっとも近い先例の形です。config・シナリオを合成した、決定論的な条件で発火する手順のリストであり、この
  項目の codegen 作業が、ネイティブに対応付けられない1点のために再利用する TODO フォールバックの
  慣習でもあります。
