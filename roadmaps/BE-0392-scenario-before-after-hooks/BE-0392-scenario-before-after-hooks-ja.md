[English](BE-0392-scenario-before-after-hooks.md) · **日本語**

# BE-0392 — シナリオの steps から独立した before/after ライフサイクルフック

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0392](BE-0392-scenario-before-after-hooks-ja.md) |
| 提案者 | [@akira-matsuda](https://github.com/akira-matsuda) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0392") |
| 実装 PR | [#1762](https://github.com/bajutsu-e2e/bajutsu/pull/1762) |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md)、[BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)、[BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md) |
<!-- /BE-METADATA -->

## はじめに

新しいトップレベルのシナリオフィールド `before` と `after` を追加します。前処理と後処理を、シナリオ
本体の `steps` 列に混ぜ込まず、ランナーが別扱いで追跡できるようにする宣言です。

`before` は、最初に実行する手順の順序付きリストです。レポート上も独立した区画を持ち、失敗すると
シナリオを `steps` の手前で打ち切ります。`after` は、結果（`always` / `success` / `error`）と、
そのときに実行する手順を組にしたルールのリストです。

`after` を使うと、テスト用に借用したレコードを毎回必ず解放できます。成功したときだけ自分が作成した
データを削除し、失敗したときだけ追加の診断情報を集めることもできます。この3つの関心事は、今は同じ
シナリオファイルの中にありながら、切り分ける手段がありません。

`before` と `after` はどちらも、既存の決定論的な step・アサーション DSL（ドメイン固有言語）を
そのまま使います。そのため、シナリオ本体の手順と同じだけ機械的に検証できます。

## 動機

Bajutsu にはすでに「前」の仕組みがありますが、独立してはいません。`preconditions.setup` は、再利用できる
前提シナリオファイルを指定するフィールドです。`apply_setups`
（[`bajutsu/scenario/expand.py:153`](../../bajutsu/scenario/expand.py)）が、実行の開始前に、前提シナリオの
手順をシナリオ本体の `steps` の先頭へそのまま連結します。

連結された手順は、以降シナリオ本体の手順と見分けが付きません。レポートは同じ通し番号の列として並べます。
前提シナリオ側の手順が失敗しても、ただの手順失敗としか表示されず、`setup` 由来だという印は残りません。
前処理を、レポートを見るレビュアーが一目でシナリオ本体と区別できる独立した区画にしたくても、今は
その手段がありません。

一方「後」の側にあるギャップは、部分的な不足ではなく仕組みそのものの不在です。Bajutsu には「後」の
フックが一切ありません。後片付けを置ける場所は今のところ `steps` の末尾しかなく、その置き方は、後片付けが
本来必要な場面でこそ機能しません。ステップループは最初の失敗で打ち切られます
（[run-loop](../../docs/run-loop.md) の手順9）。そのため、末尾の後片付け手順が実行されるのは、手前の手順が
すべて成功したときだけです。つまり後片付けがもっとも要らない実行でしか動きません。

テストユーザーを登録したシナリオが、3手順後に壊れたボタンに当たって失敗したとします。そのユーザーは
残ったままになります。削除するはずだった手順に、実行が一度も到達しないからです。

CI 側のスクリプトや別スケジュールの後片付けジョブのように、シナリオファイルの外に置く回避策も
解決になりません。

回避策には、その実行自身の `vars.*` バインディングが見えません。同じ実行の中で `http` 手順の
`saveBody` が捕まえた特定のレコードを狙って処理できません。失敗したときだけ追加の診断情報を集めたい
シナリオと、成功したときだけテストデータを削除したいシナリオを区別する手段もありません。

ランナー自身は、「シナリオ本体の手順の後で、かつ実行結果を踏まえて動く」段階を、すでに別の場所で
扱っています。この項目は、その形を新しく考案するのではなく一般化します。

シナリオレベルの `expect` ブロックは、`steps` が終わったあとに厳密に実行されます。手順がすべて成功した
シナリオでも、その結果を失敗へ反転させることがあります（`run_scenario`、
[`bajutsu/orchestrator/loop.py:478`](../../bajutsu/orchestrator/loop.py)）。

`capturePolicy` の `Trigger.result: Literal["error"]`
（[`bajutsu/scenario/models/evidence.py:22`](../../bajutsu/scenario/models/evidence.py)）は、手順が
失敗したときに特定のルールを発火させる仕組みです。判定の対象は、実行を通してとらえた1手順の結果で
あり、シナリオ全体の判定ではありません。ただし、同じ `error` という結果の語に紐付いています。

不足しているのは、この2つと対称な段階です。アクションは、固定のアサーション判定でも証跡の収集でもなく、
任意の手順列を取ります。しかも、シナリオの実行後だけでなく実行前にも置けます。

この仕組みが実装されれば、著者は今日との違いを2つ、具体的に確認できます。1つ目は、シナリオの
レポートに、通し番号の付いた `steps` とは別の「Before」区画と「After」区画が現れることです。今日の
`setup` 前提シナリオは、それ自体としては見えず、本体の手順列に埋もれています。2つ目は、手順が途中で
失敗したシナリオでも、宣言した `error` / `always` の後片付けが実行されることです。今日は、ループが
すでに打ち切られたあとのため、同じ末尾の手順は一度も実行されません。

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

この例の `before` 手順は、既存の `http` が持つ `saveBody` をそのまま使っています。`saveBody` は、
レスポンス本文全体をテキストとして `${vars.<name>}` に保存する仕組みです。シード用エンドポイントが
本文として id 以外を返さないよう作られているので、それだけで id を捕まえられます。より大きな JSON
レスポンスから1つのフィールドだけを取り出す仕組みは別のギャップであり、この項目が解決を提案する
ものではありません。

`before` は素直な `list[Step]` で、分岐のない順序付きの前処理です。分岐する対象となる結果が、まだ
存在しないからです。

`after` は `AfterRule` のリストです。各エントリは、結果を表す `on`（`always` / `success` / `error`）と、
そのとき実行する `steps` の組です。同じ `on` の値を持つエントリを複数書いてもかまいません。それらは宣言順に
合成されます。`capturePolicy` がすでに同じトリガーへ複数のルールを発火させられるのと同じ形です。

`on` の2つの結果語、`success` と `error` は、`capturePolicy` の `Trigger.result: Literal["error"]` が
すでに「失敗した」という結果に充てていた語を拡張したものです。`capturePolicy` では1手順の失敗、
ここではシナリオ全体の判定という違いはあります。しかし、同じ概念に別の語を新しく充てているわけでは
ありません。`always` は、結果に関わらない後片付けのために唯一新しく加えた語です。

`before` と `after` はどちらも target-config レベルにも存在します（`TargetConfig.before` /
`TargetConfig.after`）。シナリオ側の `before`/`after` がその上に積み重なる、アプリ全体のデフォルト値です。
`interrupts` がすでに確立した、config を先に置きシナリオを追加する階層と同じです
（[`bajutsu/config/schema.py:403`](../../bajutsu/config/schema.py)）。

`before` は config・シナリオの順で合成します。アプリ全体の前処理が先に走り、そのあとにこのシナリオ
固有の追加が続きます。`interrupts` と同じ順序です。`after` は逆に、シナリオ・config の順で合成します。
シナリオ固有の後片付け（たとえば自分が作成したレコードの削除）が、アプリ全体の後片付け（たとえば
ログアウト）より先に実行されます。

`after` の順序は、内側のリソースの解放が、それを包む外側のリソースより先に行われる順序を踏襲しています。
ほとんどのフィクスチャベースのテストフレームワークが、setup/teardown の組に「後に確保したものを先に
解放する」という同じ順序を与えています。pytest のフィクスチャの後始末は、この項目が依存する仕組みでは
なく先例として引用しています。

### ランナーへの組み込み

`run_scenario`（[`bajutsu/orchestrator/loop.py:478`](../../bajutsu/orchestrator/loop.py)）に、既存の
`steps`/`expect` の前後を挟む2つの段階を追加します。どちらの段階も、`if` や `forEach`、
`_InterruptGuard` の復旧手順がすでに共有している再帰的な `_ExecSteps` クロージャ
（[`bajutsu/orchestrator/loop.py:635`](../../bajutsu/orchestrator/loop.py)）を使って動きます。そのため
`before`/`after` の手順は、他のどの手順とも同じ能力を持ちます。それらの既存の利用箇所と同じ形で、
実行の `live_bindings`（`vars.*`）も共有します。

1. **`_run_steps` が動く前に**、有効な `before` リストを実行します。ここで失敗すると
   `failure = "before: " + 理由` が設定され、`steps` と `expect` はどちらも実行されずに終わります。

   `before` は、シナリオの中の1手順ではなく、シナリオを実行するための前提条件です。ランナーは、対象が
   満たせない `preconditions` の値がある場合も、そもそもシナリオを開始しません。起動段階で
   `simctl.DeviceError` が送出され（`launch_driver`、
   [`bajutsu/runner/launch.py:27`](../../bajutsu/runner/launch.py)）、`run_scenario` が呼ばれる前に
   失敗します。`before` は、それと同じ扱いです。

   `apply_setups` は、`setup` 前提シナリオを展開時に `steps` の先頭へ連結します
   （[`bajutsu/scenario/expand.py:176`](../../bajutsu/scenario/expand.py)）。そのため、`_run_steps` より
   前に走る `before` 段階は、その前提シナリオよりも前に走ります。つまり `before` は、前提シナリオと
   シナリオ本体の両方がその後で使う状態を用意する場所です。`before` の手順が、前提シナリオの到達する
   画面に依存してはいけません。

   `TargetConfig.before` も同じ規則に従います。アプリ全体の `TargetConfig.setup` 前提シナリオを
   置き換えず、その前に走ります（[`bajutsu/config/schema.py:356`](../../bajutsu/config/schema.py)）。
   独立したレポートの区画を持つのは `before` だけなので、2つは別の仕組みのまま残ります。
2. **`steps` と `expect` は変更なく実行されます。** この項目は、両者の結果判定そのものには手を加えません。
   `before` の失敗は、後述する手順3にとって `error` という結果として扱います。`steps`/`expect` が
   失敗した場合と同じ扱いです。`before` が途中まで作った状態があるなら、その後片付けも必要だからです。
3. **判定が確定したあと**に、有効な `after` リストを宣言順に実行します。判定が確定するのは、
   `steps`/`expect` が最後まで実行された場合、`before` が失敗して両者を飛ばした場合、実行がキャンセル
   された場合のいずれかです。キャンセルは `RunCancelled` で表し、`run_scenario` がすでに
   `failure = CANCELLED_FAILURE` を設定する箇所で捕捉します
   （[`bajutsu/orchestrator/loop.py:607`](../../bajutsu/orchestrator/loop.py)）。

   この段階は、`on` が `always` にもその判定にも一致しないエントリを飛ばします。`on` でグループに
   まとめず交互に並べたまま実行するので、前述のシナリオ・config の順序は、1つの `on` グループの内側に
   とどまらず、段階全体にわたって保たれます。キャンセルされた実行は `error` として扱います。
   `docs/run-loop.md` が、キャンセルされた実行を通常の失敗と同列に位置づけているのと同じ扱いです。

   `after` エントリ自身が失敗した場合は、それまで実行が成功していたときに限り `failure` を更新します
   （`failure = "after: " + 理由`）。すでに成功していた `steps` の結果を `expect` が反転させられるのと
   同じ形です。上記のいずれかの理由ですでに失敗していた場合、`after` エントリの失敗は既存の `failure`
   文字列を置き換えず、そこに追記します。読み手がまず目にする理由が、後片付けの副作用ではなく元の原因の
   ままであるようにするためです。
4. **キャンセルされた実行では、`after` 段階は独自の期限のもとで動きます。** `after` 段階は、`cancelled` を
   そのまま読むわけにはいきません。`CancelSource` はラッチされた `threading.Event.is_set` であり
   （[`bajutsu/cancellation.py:147`](../../bajutsu/cancellation.py)）、猶予時間内の2回目の `SIGTERM` は
   意図的に何もしないためです。`after` を `error` としてディスパッチするまさにその経路で、`after` の
   内側の最初のキャンセル判定がふたたび `RunCancelled` を送出し、後片付けの手順が1つも実行されません。

   代わりに、この段階は時間で区切ります。キャンセルされた実行では、`after` は `cancelled` を読むのを
   やめ、`grace_seconds()`（[`bajutsu/cancellation.py:76`](../../bajutsu/cancellation.py)）から切り出した
   独自の期限のもとで実行します。その期限を過ぎた時点で、残りのエントリを放棄します。こうすれば、
   `serve` が無条件の強制終了までに待つ猶予時間を終了処理がはみ出すことなく、後片付けに時間を区切った
   実行機会を与えられます。

   キャンセルされていない実行では、`after` は他のどの手順とも同じように各境界で `cancelled` を読みます。
   いずれの場合も、この段階は `steps`/`expect` から抜けるすべての経路で到達します。`RunCancelled` が
   すでにたどる経路も含みます。したがって `after` は、無条件に終了処理を行う既存の
   `finally: artifacts = sink.finish_scenario_intervals(...)` より前に実行されます。

### レポート

`RunResult`（[`bajutsu/orchestrator/types.py:171`](../../bajutsu/orchestrator/types.py)）に、
`before_outcomes: list[StepOutcome]` と `after_outcomes: list[StepOutcome]` を追加します。どちらも
`steps` や `expect_results` に畳み込まず、その隣に置きます。`expect_results` がすでに受けているのと
同じ切り分けです。

レポートの Steps タブは、シナリオ本体の通し番号付き手順とは別に、「Before」区画と「After」区画を
描画します。レビュアーは、前処理と後処理をシナリオ本体と一目で区別できます。この項目の *動機* が挙げた
「今日の `setup` 前提シナリオにはそうした印が残らない」というギャップを、これで埋めます。

### codegen

`interrupts`（BE-0314）はネイティブに対応する構文を持たず、どこでもコメントへのフォールバックに
頼っています。それに対して、`before` と `after` の `always` エントリは、各 codegen 対象がすでに備えている
構文へそのまま対応付けられます。Playwright の `test.beforeEach` / `afterEach`、XCTest の
`setUpWithError` / `tearDownWithError`、Espresso/JUnit の `@Before` / `@After` です。

`success`/`error` による分岐には、それぞれのフレームワーク自身がテスト結果を後始末フックの中から読む
手段が要ります。Playwright の `testInfo.status`、XCTest の `testRun?.hasSucceeded`、JUnit の
`TestWatcher` です。対応する codegen 対象ではその分岐を出力します。まだ対応していない対象では、
[BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) と BE-0314 が
他の箇所ですでに使っているラベル付き `// TODO` コメントへ回します。

### 作業分解(MECE)

1. **スキーマ。** `Scenario`
   （[`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)）と
   `TargetConfig`（[`bajutsu/config/schema.py`](../../bajutsu/config/schema.py)）の両方に
   `before: list[Step]` と `after: list[AfterRule]`（`AfterRule = { on: Literal["always", "success",
   "error"], steps: list[Step] }`）を追加します。どちらもデフォルト値を空リストとし、未設定のフィールドが
   dump から消えるようにします。`interrupts` と同じ扱いです。
2. **合成のヘルパー。** `before` は config・シナリオの順、`after` はシナリオ・config の順で合成します。
   `interrupts` の config/シナリオ合成がすでに解決している場所と同じ地点で、1回だけ解決します。
3. **ランナーへの組み込み。** 前述の before 段階のゲート、after 段階の結果に応じたディスパッチ、
   `failure` 文字列の合成を `run_scenario` に実装します。
4. **レポート。** `RunResult` への `before_outcomes` / `after_outcomes` の追加と、レポートレンダラーの
   独立した「Before」/「After」区画です。あわせて、`RunResult.steps` を直接読むために、before/after
   だけの失敗を取りこぼす2つの出力も対象にします。JUnit の `<failure>` 本文を作る `_details`
   （[`bajutsu/report/manifest.py`](../../bajutsu/report/manifest.py)）と、CTRF の手順リスト
   （[`bajutsu/report/ctrf.py`](../../bajutsu/report/ctrf.py)）です。`manifest.json` へは `asdict` が
   新しいフィールドをそのまま運ぶため、対応は不要です。
5. **codegen。** `always` エントリに対する `beforeEach`/`afterEach`（または各バックエンドの対応する
   構文）の出力、対応可能なバックエンドでの `success`/`error` に応じた出力、それ以外での TODO への
   フォールバックです。
6. **ドキュメントとフィクスチャ。** [`docs/scenarios.md`](../../docs/scenarios.md) とその日本語訳に
   `before`/`after` を記載します。`preconditions.setup`（`steps` 内へ連結する前提シナリオ）と
   `capturePolicy`（同じ `error` という結果に紐付く証跡収集）を並べた比較表で、それぞれの使い分けを
   説明します。`before` でレコードを作成し、`after` の `success` ルールで削除する showcase
   フィクスチャを追加します。
7. **テスト。** 両レベルでのスキーマのパースと検証、両方向の合成順序、両段階への・からの `vars.*` の
   共有、新しい `RunResult` フィールドを確かめます。結果判定は、すべての組み合わせで確かめます。`before` の失敗が `steps`/`expect` を飛ばし、
   `after` を `error` としてディスパッチすること。`always` が結果に関わらず実行されること。`error` ルール
   自身の失敗が元の `failure` を置き換えず、追記されること。他が成功しているときの `success` ルールの失敗が、
   そのまま唯一の `failure` になること。キャンセルされた実行が `after` を `error` としてディスパッチし、
   後片付けのエントリを実際に実行すること。この経路では、ラッチされた `cancelled` を読みません。段階自身の
   期限を過ぎた時点で、残りは放棄します。

   他が成功しているときの `success` ルールの失敗は、HTML のレポートだけでなく、JUnit の失敗テキストと
   CTRF のレコードにも、そのルールの名前が現れなければなりません。

### prime directive との整合性

- **AI は判定しません。** `before`/`after` の手順は既存の決定論的な step DSL です。`after` ルールが
  分岐に使う結果（`success`/`error`）は、シナリオ自身がすでに機械的に確定させた判定であり、モデル呼び出し
  ではありません。この項目は新しい AI の経路を一切追加しません。
- **決定論を優先します。** 固定の `sleep` はありません。`before`/`after` の手順も、他のすべての手順が
  すでに使っている条件待機のプリミティブをそのまま使います。結果に応じたディスパッチは、すでに計算済みの
  `failure` 値に対する単純な比較です。
- **アプリ非依存です。** `before`/`after` は config・シナリオ側のデータです。ランナーとレポートが
  獲得するのは、アプリ固有のコードではなく、1つの汎用的な仕組みです。

## 検討した代替案

- **`setup` と対称な `preconditions.teardown` フィールドを追加し、`setup` と同じように `steps` へ
  連結する案。** 却下しました。連結する形は、この項目が解決しようとしている問題をそのまま残します。
  連結した後片付け手順は、手前の手順が失敗してループが打ち切られたあとには実行できません。レポート上も
  独立した段階ではなく、ただの手順として現れます。`steps` への連結をやめること自体が、この項目の要点です。
- **`capturePolicy` の `Trigger` に `result: "success"` を追加し、ルールのアクションを証跡の収集だけ
  でなく任意の手順にも広げる案。** 却下しました。`Trigger` は `action` や `event` にも一致します。
  ステップループ全体を通して機会をとらえて判定する仕組みに紐付いており、シナリオ全体でただ1つ確定する
  最終的な判定とは結び付いていません。`Trigger` を流用すると、著者が `before`/`after` のつもりで書いた
  ルールが、実行の途中で意図せず発火しかねません。発火するタイミングと理由がどちらもまったく異なる
  2つの機能を、1つに混ぜてしまいます。
- **`{on, steps}` のリストではなく、`always` / `onSuccess` / `onFailure` という3つの固定キーにする案。**
  リストの形を採りました。このスキーマがすでに持つ結果分岐のフィールド（`capturePolicy`、
  `interrupts`、`systemAlertHandling.rules`）は、どれも固定形のオブジェクトではなく、自分自身の条件を
  持つエントリのリストです。リストなら合成できます。別の関心事のために、2つ目の `error` エントリを
  追加できます。3つの固定キーではそれができません。
- **`after` フックに、通常の手順とは別のリトライやタイムアウトの方針を持たせる案。** 却下ではなく
  見送りです。あとから追加することを妨げるものは、この設計にありません。この項目の *動機* が挙げている
  ギャップは、手前の手順が失敗したあとでは後片付けがそもそも実行できないことです。実行できるように
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

- [`bajutsu/scenario/expand.py:153`](../../bajutsu/scenario/expand.py)：`apply_setups`。この項目が
  補う既存の「前」だけの仕組みで、その手順は独立した段階としてではなく `steps` へ直接連結されます。
- [`bajutsu/runner/launch.py:27`](../../bajutsu/runner/launch.py)：`launch_driver`。満たせない
  `preconditions` の値が、`run_scenario` が呼ばれるより前にすでにシナリオを失敗させている先例で、
  `before` 自身のゲート判定はこれを踏襲します。
- [`bajutsu/orchestrator/loop.py:478`](../../bajutsu/orchestrator/loop.py)：`run_scenario`。新しい
  before/after の段階がまさに組み込まれる場所です。
- [`bajutsu/orchestrator/loop.py:635`](../../bajutsu/orchestrator/loop.py)：`_ExecSteps`。
  `if`/`forEach`/`interrupts` がすでに共有している再帰的な手順実行クロージャで、`before`/`after` は
  これを複製せずそのまま再利用します。
- [`bajutsu/cancellation.py:147`](../../bajutsu/cancellation.py)：ラッチされた
  `threading.Event.is_set` としての `CancelSource` と、その上にある `grace_seconds()` の見積もり
  (最悪 60 秒の driver 呼び出しと 30 秒の終了処理)。時間の余裕がない後処理の段階を許さないこの2つが、
  `after` 段階をキャンセルのラッチではなく独自の期限で区切る理由です。
- [`bajutsu/scenario/models/evidence.py:22`](../../bajutsu/scenario/models/evidence.py)：
  `Trigger.result: Literal["error"]`。既存の `capturePolicy` の結果語で、この項目の `after` ルールは
  これを置き換えるのではなく `success` を加えて拡張します。
- [`bajutsu/orchestrator/types.py:171`](../../bajutsu/orchestrator/types.py)：`RunResult`。
  `expect_results` がすでに `steps` の中ではなくその隣に置かれている先例で、`before_outcomes`/
  `after_outcomes` はこれに倣います。
- [BE-0030 — パラメータ化された共有ステップ](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md)：
  既存の `setup` 前提シナリオと `use` によるコンポーネント再利用の仕組み。この項目の `before` フィールドは
  これを置き換えるのではなく補います。
- [BE-0033 — シナリオ変数と軽量な制御フロー](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)：
  `before`/`after` の手順が踏襲する `vars.*` 共有の先例。
- [BE-0314 — 出現タイミングの読めない割り込み画面を決定論的に処理する](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md)：
  もっとも近い先例の形です。config・シナリオを合成した、決定論的な条件で発火する手順のリストであり、この
  項目の codegen 作業が、ネイティブに対応付けられない1点のために再利用する TODO フォールバックの
  慣習でもあります。
