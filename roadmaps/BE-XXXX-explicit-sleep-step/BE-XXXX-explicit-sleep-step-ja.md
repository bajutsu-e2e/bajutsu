[English](BE-XXXX-explicit-sleep-step.md) · **日本語**

# BE-XXXX — 条件で観測できない待機のための明示的な `sleep` step

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-explicit-sleep-step-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Scenario authoring features |
<!-- /BE-METADATA -->

## はじめに

本項目は `sleep` step を追加します。書き方は `sleep: { seconds: 2, reason: "サーバーが再試行を 2 秒間制限する" }` です。この step は、指定した秒数だけシナリオを止め、それ以外は何もしません。「待機は条件待ちのみ」という原則（[Prime directive 2](../../CLAUDE.md#prime-directives-do-not-violate)）に対する、唯一の公認された例外です。例外が埋もれないように、スキーマで `reason` を必須にして `seconds` に上限を設けます。report は固定の待機をすべて表示し、決定性の監査（[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)）は 1 件ずつ finding として挙げます。

対象は、画面、アクセシビリティツリー、ネットワークログのどの条件でも観測できない待機です。現状では、そのような待機をシナリオに書く手段がありません。そのため作者は、無関係な条件で `wait` をでっち上げるか、flaky なままシナリオを放置しています。

## 動機

シナリオの待機は条件待ちです。`wait` step は、セレクタの出現、消失、または画面の安定まで `query()` を poll し、`timeout` を必須とします（[`docs/ja/scenarios.md`](../../docs/ja/scenarios.md)、[BE-0118](../BE-0118-wait-for-contract-unification/BE-0118-wait-for-contract-unification.md)）。run loop の wait が固定時間だけ待つことはありません。これは正しい既定であり、本項目も維持します。

一方で、観測できる条件を持たない待機が存在します。サーバーが再試行を一定時間だけ絞り、終わるまで何も表示しない場合があります。アニメーションがツリーの変化の止まる時点より長く続き、`until: settled` が早く戻る場合もあります。デモ動画を録るシナリオでは、画面をしばらく見せ続ける必要があり、「読むのに十分な長さ」を表す要素はありません。どの場合も、作者は欠けた step を回避しています。回避策は、無関係な要素への `wait` か、デバイス時間を浪費する再試行ループです。前者は本当の理由を「たまたま十分遅く成立する条件」に隠します。どちらも決定的に見えますが、作者が必要とした遅延がどこにも書かれていないため、決定的ではありません。

観測できる変化は次の 3 点です。

- 止める必要のあるシナリオは、長さと理由を 1 行で書きます。reason のない `sleep` や上限を超える `sleep` は、loader が拒否します。
- 固定の待機はすべて理由つきで `report.html` に現れ、`bajutsu audit` が `fixed-sleep` finding として報告します。レビュアーは、スイートの固定待機を数えて個別に検討できます。
- 観測できない待機が `wait` を借りなくなるため、スイートの `wait` は「timeout 付きの条件」という意味を保てます。

## 詳細設計

### step の定義

`sleep` は `wait` と並ぶ新しい action です。モデルを `bajutsu/common/scenario/models/actions/sleep.py` に置き、`Step`（[`step.py`](../../bajutsu/common/scenario/models/steps/step.py)）にフィールドを足します。1 step 1 action の規則と `STEP_ACTIONS` は、二重の登録なしにこの action を拾います。

| フィールド | 型 | 意味 |
|---|---|---|
| `seconds` | 数値、必須 | 止める長さ。0 より大きく、30 以下です。厳密に検証し、真偽値や引用符付きの文字列は変換せずに拒否します。 |
| `reason` | 文字列、必須 | 条件でこの待機を表せない理由。前後の空白を除いて空でないことが条件です。 |

30 秒の上限は、モデルのモジュール内の定数 `MAX_SLEEP_SECONDS` に置きます。これより長い固定待機は、ほぼ確実に条件の書き漏れです。そのため loader は `wait` を案内するメッセージで拒否します。sleep に条件はないので、モデルは `timeout` もセレクタも受け取りません。

### 実行

run loop は、driver のハンドラではなく `wait` と並べて `sleep` を実行します。この step はデバイスに触れないためです。loop は注入された `Clock.sleep`（[`clock.py`](../../bajutsu/common/orchestrator/types/clock.py)）を 0.25 秒ずつ区切って呼びます。fake clock を使うテストは待たずに時刻を進められ、キャンセルされた run は 1 区切り以内に待機を抜けます。待機が終わると step は成功します。`record` や `enrich` の再生でも待機を守ります。`sleep` が表す遅延は、再生時にも同じく存在するためです。`query()` も入力もなく、`BAJUTSU_MIN_WAIT_TIMEOUT` も適用しません。この下限は条件待ちの上限を引き上げるもので、固定待機に上限という概念がないからです。fake driver を含むすべての backend で使え、capability token は要りません。

待機は何にも作用しないので、`wait` と同じく、作用する step の前に行う割り込みの確認を省きます。step 末尾のシステムアラートガードは失敗した step でだけ動き、`sleep` はキャンセル以外で失敗しません。待機中に出たプロンプトは、ツリーを query する次の step が処理します。

### 例外を見える状態に保つ

例外を数えられるように、3 つの面を用意します。

- **report。** step の進捗ラベルと `report.html` の行は `sleep 2s — <reason>` と表示します。行は step 自身の定義から組み立て、定義はすでに `seconds` を持ちます。そのため `StepOutcome` にフィールドを足さなくても、ツールは 1 回の run の固定時間を合計できます。
- **決定性の監査。** [`audit/_functions.py`](../../bajutsu/analysis/audit/_functions.py) に、`loose-wait` の隣へ `fixed-sleep` finding を足します。`sleep` 1 つにつき 1 件で、reason を添えます。audit はシナリオのモデル全体をたどって `sleep` を探すので、`before`、`after`、`interrupts`、`web` や `app` のブロック内の待機も列挙します。固定待機の合計が 10 秒を超えるシナリオは、loose wait と同じ moderate 階層に入ります。
- **AI による執筆。** `record` と `crawl` はモデルに `sleep` を提示しません。手書きで足すのは作者に限り、モデルが flaky な step の回避策として固定待機を選ぶことはありません。記録エージェントのツール一覧は `STEP_ACTIONS` から導出せず手書きしているため、もともと `sleep` を含みません。その状態をテストで固定します。モデルが待機を足しうる経路はもう 1 つ、`triage --ai` の修正案です。そのため、検証を緩める変更を警告する仕組みは、`sleep` を足す修正案にも警告を出します。

### codegen

3 つのエミッタが待機を翻訳するので、生成テストはシナリオと一致します。

| エミッタ | 出力 |
|---|---|
| `xcuitest` | `Thread.sleep(forTimeInterval: <秒>)`、reason はコメント |
| `uiautomator` | `Thread.sleep(<ミリ秒>)`、reason はコメント |
| `playwright` | Playwright の page が持つ固定待機の呼び出し、reason はコメント |

Playwright エミッタは TypeScript を出力するので、呼び出しは `await page.waitForTimeout(<ミリ秒>)` です。どのエミッタも reason を残すので、生成テストもシナリオと同じ程度にレビューできます。

### serve の Author エディタ

Web UI の Author エディタ（[`serve.author.mjs`](../../bajutsu/templates/serve.author.mjs)）は、シナリオの YAML をテキストとして編集します。編集のたびに、シナリオの loader を動かす `/api/lint` で検証し、`/api/audit` で評価します。そのため上限、非空の規則、`fixed-sleep` の finding は、専用のコードなしでエディタに届きます。エディタの step 一覧は、`sleep` を `sleep 2s — <reason>` と表示します。

### Prime directive との整合

- **AI は判定者ではない。** 固定待機に判定はありません。シナリオの合否は従来どおりアサーションが決め、モデルの呼び出しは含みません。
- **決定性が最優先。** 待機の長さは決まっており、記録されます。意図して名前を付けた例外であり、上限、必須の reason、report の行、audit の finding が、例外が無自覚な習慣になることを防ぎます。原則の文言は [`CLAUDE.md`](../../CLAUDE.md)、`README.md`、`DESIGN.md`、原則を述べ直す `docs/` のページで、例外を明記する形に改めます。文書とスキーマが食い違うことを避けるためです。
- **アプリ非依存。** セレクタもターゲット固有の値も取らないので、特定のアプリの事情はツールに入りません。

### 作業分解（MECE）

| # | 作業 | 触るファイル | 完了条件 | 前提 |
|---|---|---|---|---|
| 1 | `Sleep` モデル、`Step` のフィールド、スキーマ規則（正の値、上限、非空の reason） | `models/actions/sleep.py`、`models/steps/step.py` | 有効な `sleep` を含むシナリオが読み込める。0、負、上限超過、reason 欠落は拒否される。`bajutsu schema` に載る | なし |
| 2 | キャンセルに備えて区切った `Clock` 経由の run loop 実行と、`record` の再生 | `orchestrator/loop/_functions.py`、`orchestrator/loop/_step_runner.py`、`record/loop.py` | fake clock のテストで時刻が `seconds` だけ進み、キャンセルされた run は途中で抜ける | 1 |
| 3 | report の行と進捗ラベル | `common/report`、`orchestrator/actions/_registry.py` | report が `sleep Ns — reason` を表示し、run の合計が導ける | 2 |
| 4 | `fixed-sleep` の audit finding、10 秒の階層規則、triage の警告 | `analysis/audit/_functions.py`、`triage/heuristic/_functions.py` | どこに置いた `sleep` にも finding が出ること、階層の変化、`sleep` を足す修正案への警告をテストで示す | 1 |
| 5 | `record` と `crawl` の AI 経路に `sleep` を出さない | `tests/test_sleep_step.py` | モデルのツール一覧に `sleep` がないことをテストで示す | 1 |
| 6 | 3 つの codegen エミッタとカバレッジ項目 | `bajutsu/codegen/*.py` | 各エミッタに reason コメント付きの golden 出力テストがある | 1 |
| 7 | serve Author エディタの step ラベル | `templates/serve.author.mjs` | `make lint-js` が通り、`/api/lint` が reason のない下書きを拒否する | 1 |
| 8 | 両言語のドキュメントと、改めた原則の文言 | `docs/scenarios.md`、`docs/concepts.md`、`docs/cli.md`、`docs/index.md`、`docs/vision.md`、`docs/run-loop.md`、`docs/dsl-grammar.md`、`docs/codegen.md`、`DESIGN.md`、`README.md`、`CLAUDE.md`、`CONTRIBUTING.md`、PR と Issue のテンプレート、原則を述べ直すスキル、日本語の対応ファイル | `make check` が通り、例外を欠いた原則の記述が残らない | 1〜7 |

### 実装時に決めたこと

- アラートガードに特別な扱いは要りません。step 末尾のガードは失敗した step でだけ動き、作用の前の割り込みの確認は `wait` と同じく `sleep` でも省きます。
- audit は、固定の待機の合計が 10 秒を超えたシナリオを `Moderate` と評価します。短い `sleep` が 1 つだけなら finding として挙げるだけで評価は変えません。正当な待機によって評価が下がり、本当のセレクタの問題が埋もれることを避けるためです。
- step の定義がすでに `seconds` を持つので、`StepOutcome` にフィールドを足しません。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| `wait` に `until: { duration: N }` を足す | `wait` に固定時間の形を加える。 | `wait` は「`timeout` で区切られた条件」を意味し、audit、report、codegen のすべてがその意味に依存しています。固定時間の形は、固定時間だけ待つことのない唯一の step を、固定時間だけ待つ step に変えてしまいます。 |
| reason なしの `sleep: 2` | 数値だけを書く。 | レビューで、正当な待機と flaky な step の回避策を区別できません。audit も示す説明を持てません。1 行の追加が、例外の代価です。 |
| config で opt-in したときだけ許可 | プロジェクトが `allowFixedSleep: true` を設定しない限り `sleep` を拒否する。 | 歯止めとしては実効的ですが、reason、上限、audit の finding をすでに備えた step に設定をもう 1 つ足すことになります。audit で濫用が見えた段階で、後から採用できます。 |
| 痕跡なしで許可 | reason も finding もない単なる待機。 | 例外が見えなくなり、曲げた原則が誰にも気付かれないまま崩れていきます。 |
| step を作らず `until: request` と `settled` で済ませる | すべての待機を条件で表す。 | ネットワークと settle の条件は多くの待機をすでに表せますが、本項目はそれを置き換えません。サーバーの再試行制限や、意図した画面の見せ止めにはそのような条件がなく、表せない待機が残ります。 |

## 進捗

> 作業の進行に合わせてこの節を更新します。チェックリストは「詳細設計」の MECE な作業分解
> （作業単位ごとに 1 項目）に対応し、ログには変更内容と日付を古い順に PR へのリンクつきで記録します。

- [x] `Sleep` モデルとスキーマ規則
- [x] `Clock` 経由の run loop 実行
- [x] report の行と進捗ラベル
- [x] `fixed-sleep` の audit finding
- [x] `record` と `crawl` の AI 経路からの除外
- [x] 3 つの codegen エミッタ
- [x] serve Author エディタの step ラベル
- [x] ドキュメントと日本語ミラー

ログ：

- 提案と実装を 1 つの変更にまとめました。モデル、run loop での実行、report の行、audit の finding、codegen、Author エディタのラベル、テスト、ドキュメントを含みます。

## 参考

- [BE-0118](../BE-0118-wait-for-contract-unification/BE-0118-wait-for-contract-unification.md)：本項目が唯一の例外を設ける、条件待ちの契約。
- [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)：固定の待機をそれぞれ報告する決定性の監査。
