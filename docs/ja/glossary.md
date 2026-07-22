[English](../glossary.md) · **日本語**

# 用語集

> ほかのページ全体を貫くドメインの語を、一語ずつ引くためのリファレンスです。[concepts](concepts.md)
> が Bajutsu を「なぜ」この形にしたかを説明するのに対し、このページは語が「何を意味するか」を答え、
> 混同しやすい語のかたまりについては「似た響きの二つの語のどちらがどちらか」を示します。正とするのは
> 実装（`bajutsu/`）であって、あるページがたまたま使っている言い回しではありません。各項目には、その
> 語を定義しているページやモジュールへのポインタを添えます。
>
> 英語版用語集との対応が一対一でない語もあります。とくに「証跡」は、[`DESIGN.md`](../../DESIGN.md)
> では evidence と trace の両方を指す場面で緩く使われてきました。このページでは、その対応がずれる箇所
> をそのつど明示します。

関連: [concepts](concepts.md)（設計の理由） · [scenarios](scenarios.md) · [drivers](drivers.md) · [cli](cli.md)

---

## シナリオのオーサリング

**scenario（シナリオ）**：名前を付けた一件のテストケースです。`name`、任意の `preconditions`、順序付きの
`steps` のリスト、機械チェック可能なアサーションを並べた `expect` のリスト（さらにシナリオ単位の
`capturePolicy`・`network`・`mocks` など）からなります。Bajutsu が唯一永続化する成果物であり、プルリク
エストでレビューされる平文の YAML です。定義は `bajutsu/scenario/models/scenario.py` の `Scenario`、
書き方の案内は [scenarios](scenarios.md) です。

> **scenario・scenario file・test の区別**：**scenario file（シナリオファイル）** はシナリオのリスト
> を持ち、そのリストの各エントリ自体が **scenario（シナリオ）** です。「test（テスト）」ではありませ
> ん。「test」は Bajutsu の用語ではなく、ドキュメントで「test」と書いてあれば、それは一つのシナリオ
> を指しています。（`ScenarioFile` がリストを包み、`load_scenarios()` は素のリストと
> `{ description, scenarios }` のマッピングのどちらも受け付けます。）

**goal（ゴール）**：人間が `bajutsu record` に渡す自然言語の目的です。AI はこれを目指してアプリを探索
し、シナリオを書き起こします。シナリオのフィールドではなくオーサリングの入力であり、完成したシナリオ
には由来（`from`）としてのみ残ります。[recording](recording.md) を参照してください。

**step（ステップ）**：ちょうど一つのアクション（tap / type / swipe / wait など）に、任意の修飾子
（`capture` / `extract` / `name` / `from`）を添えたものです。「アクションはちょうど一つ」という規則は
スキーマが強制します。例外は **制御フローのステップ**（`if` / `forEach`）で、こちらは capture や
extract の修飾子を持ちません。定義は `bajutsu/scenario/models/steps.py` の `Step` です。

**precondition（前提条件）**：run の前に適用する、シナリオ単位の環境準備です。`erase`、`reinstall`、
`launchArgs`、`launchEnv`、`deeplink`、`locale`、`setup` があります。実行時の関心事（`mocks` /
`network`）とも、config 側の `defaults` / `targets` とも別物です。定義は
`bajutsu/scenario/models/scenario.py` の `Preconditions` です。

**expect / assertion（アサーション）**：**assertion（アサーション）** は機械チェック可能な検査ひとつ
（`exists` / `value` / `label` / `count` / `enabled` / `disabled` / `selected` / `request` /
`event` / `visual` などのいずれか）です。**`expect`** は、そのアサーションを並べたシナリオ単位のリスト
です。このリストが合否の唯一の源であり、判定に LLM（大規模言語モデル）は一切関与しません
（[concepts の 1](concepts.md#1-ai-は著者と調査役であり判定者ではない) を参照）。アサーションはステップ
上の `assert:` としても、待機の `until` 条件としても現れます。定義は
`bajutsu/scenario/models/assertions.py` の `Assertion` です。

**selector / identifier（セレクタと識別子）**：**selector（セレクタ）** は「UI 要素をどう指すか」で、
AND で結合されるフィールドの集合（`id`、`idMatches`、`label`、`labelMatches`、`traits`、`value`、
`within`、`index`）です。**identifier（識別子）** は要素の安定した `id` で、セレクタの主フィールドであ
り、まず優先すべきものです。つまりセレクタは問い合わせオブジェクト全体、識別子はその中の一フィールド
です。解決は決定的で、0 件でも 2 件以上でも、推測せずに失敗します。[selectors](selectors.md) を参照
してください。定義は `bajutsu/scenario/models/selector.py` の `Selector` です。

**component（コンポーネント）**：再利用できる、パラメータ付きのステップ列です。ステップから `use:` と
`with:` で呼び出します。シナリオ DSL（ドメイン固有言語）のマクロであり、コンパイル時に展開されて消えるため、決定性には影響
しません。UI の「コンポーネント」ではありません。定義は `bajutsu/scenario/models/scenario.py` の
`Component`、展開は `bajutsu/scenario/expand.py` です。

**from（由来、provenance）**：ある構文が、どの自然言語の言い回しから記録されたかを表します。表示のため
のオーサリングメタデータであり、`run` は読まないので合否には影響しません。ステップ、アサーション、
キャプチャルール、シナリオに付きます（YAML のキーは `from`、モデルのフィールドは `from_`）。

## 2 つの層

**Tier 1**：AI のライブ操作です。探索とオーサリング（`record`、`crawl`、`triage`、`serve`）を担いま
す。柔軟ですが非決定的で、著者として書き調査役として調べますが、合否は決めません。

**Tier 2**：CI（継続的インテグレーション）をゲートする決定的なランナー（`bajutsu run`）です。この経路に AI はなく、合否は `expect`
のアサーションだけから決まります。Tier 2 が唯一の合否の権限です。

この分離はプロジェクトの最上位の制約です。[concepts の 1〜2](concepts.md#1-ai-は著者と調査役であり判定者ではない)
を参照してください。

## driver backend actuator platform

この四語は、中核のなかで唯一プラットフォームに依存する継ぎ目を指します。中核そのものはプラットフォームに依存しません。混同
しやすいので、関係をここで一箇所にまとめます。正とするのは `bajutsu/backends.py`（`PLATFORMS`、
`IMPLEMENTED`）であって、どのページの散文でもありません。

| 語 | 意味 |
|---|---|
| **driver** | 抽象的な `Driver` インターフェース（`bajutsu/drivers/base.py` の `Protocol`）です。唯一のプラットフォーム依存の継ぎ目で、どの actuator もこれを実装します。 |
| **backend** | `--backend` と config の `backend:` が受け付けるユーザー向けのトークンです。platform の別名（`ios`）か、actuator の名前そのもの（`xcuitest`）のどちらかです。「backend」は入力トークンの総称で、解決されて actuator になります。 |
| **actuator** | 実際に操作（tap / type / swipe / query）を担う具体的なエンジンで、driver が実装するものです。backend のトークンは一つの actuator に解決され、run の開始時に確定して以後は固定されます。 |
| **platform** | 対象の種類を表す粗いトークン（`ios` / `android` / `web` / `fake`）で、安定度順（最も安定するものが先）の actuator のリストに展開されます。 |

`backend:` のリストは安定度順で書きます。選択は各トークンを actuator に展開し、既知でこのマシンで利用で
きる最初の一つを選びます。現在コードに組み込まれている platform から actuator への対応は次のとおりです
（四つの actuator はすべて `IMPLEMENTED` に含まれます）。

| platform | actuator（安定度順） | 利用可否の条件 |
|---|---|---|
| `ios` | `xcuitest` | `xcodebuild` が必要 |
| `android` | `adb` | `adb` 実行ファイルが必要 |
| `web` | `playwright` | `playwright` の Python パッケージが必要 |
| `fake` | `fake` | 常に利用可能（インメモリ。テスト用） |

> **`adb` は予定ではなく実装済みです。** Android の actuator（`adb`）は組み込み済みで、現在の
> `IMPLEMENTED` に含まれ、エミュレータ上で end-to-end に検証されています（[architecture →
> 実装状況](architecture.md#実装状況)、[vision → reach](vision.md#1-reachより多くのプラットフォームと面)）。
> iOS では `xcuitest` が唯一の actuator です（`--backend ios` はこれに解決されます）。かつての
> `idb` バックエンドは BE-0290 で廃止されました。

インターフェースと actuator ごとの capability の違いは [drivers](drivers.md) を参照してください。

## target app device

この三語は、初めて読む人には同じに見えますが、別々のものを指します。この区別こそ、BE-0057 が config 上
の概念を `app` から `target` へ改めた理由です。

| 語 | 意味 |
|---|---|
| **target（ターゲット）** | `targets.<name>` の下にある、テスト対象のアプリを記述する config エントリ一つです。プラットフォームごとの識別子（iOS の `bundleId`、web の `baseUrl`、Android の `package`）に加え、`backend`・`device`・`appPath` などを持ちます。config の単位です。定義は `bajutsu/config/schema.py` の `TargetConfig` です。 |
| **app（アプリ）** | テスト対象のアプリケーションそのものです。target が指し示し、device にインストールされるソフトウェアです。 |
| **device（デバイス）** | target を駆動する具体的な実行時インスタンスです。Simulator、emulator、ブラウザコンテキストで、`device`（たとえば `iPhone 15`）で名付け、実行時には `udid` で指します。 |

つまり、config の **target** が、ソフトウェアである **app** を指し、それを実行時インスタンスである
**device** の上で駆動します。

## 証跡 capturePolicy trace triage

**evidence（証跡）**：run の最中に取得する成果物で、どのプロバイダが生成したかのタグが付きます。二つの
形があります。**instant（瞬間）** はスクリーンショットや要素階層で、ステップごとに取得します。
**interval（区間）** は動画、デバイスログ、アプリトレースで、シナリオをまたいで取得します。
[evidence](evidence.md) を参照してください。定義は `bajutsu/evidence/core.py` にあります。

**capturePolicy・CaptureRule・「ルール」**：同じ概念の三つの呼び名を、ここで整理します。

- **`capturePolicy`** はシナリオの YAML フィールドで、リストです。
- **`CaptureRule`** はそのリストの各要素の型です（`bajutsu/scenario/models/evidence.py`）。
- 散文の **「ルール」** は一つの `CaptureRule` を指します。`on` のトリガーが発火するたびに、その
  `capture` の成果物を取得します。ルールは繰り返し発火し、それが、2 度目以降の run で AI なしに同じ証跡
  を再現させるしくみです。

ステップ上のインライン `capture:` とは別物です。こちらはそのステップでの一度きりのキャプチャです。

**trace と triage**：綴りは一文字違いですが、種類は正反対です。

- **`trace`** は、CLI（コマンドラインインターフェース）の動詞で、**完了した** run を読み取り専用のテキストのタイムライン（ステップ、
  ネットワーク、アプリトレース）として表示します。`--explain` を付けると、シナリオの `capturePolicy` が
  どう発火するかを事前にプレビューします。決定的で観測用であり、AI も合否判定もありません。エンジンは
  `bajutsu/trace.py` です。
- **`triage`**（CLI の動詞）は、**失敗した run を AI が診断し**、最小限の修正を提案します。Tier 1 の助言
  的な作業であり、CI をゲートすることはありません。エンジンは `bajutsu/triage.py` です。

> `trace` は多義的です。動詞のほかに、**`appTrace`** という区間の証跡の種類（os_signpost / os_log の
> 区間）があります。どちらの意味も観測用で、合否は決めません。

## CLI の動詞

ほかのページが前提にするコマンドです。ここにあるのは語彙であって完全なリファレンスではありません。すべ
てのコマンドとオプションは [cli](cli.md) にあります。

| 動詞 | 層 | 何をするか |
|---|---|---|
| `record` | 1 | AI が `goal` を目指して探索し、シナリオを書き起こします。 |
| `crawl` | 1 | AI がアプリを幅優先で探索し、画面のマップを描きます。 |
| `run` | 2 | 決定的な実行です。合否の唯一の権限です。 |
| `trace` | — | 完了した run を読み取り専用のタイムラインとして表示します（AI なし）。 |
| `triage` | 1 | AI が失敗した run を診断し、修正を提案します（助言的）。 |
| `codegen` | — | シナリオをネイティブの XCUITest / Playwright へ構造的にマッピングします。 |
| `doctor` | — | Bajutsu が前提とする規約に、target がどれだけ従っているかを採点します。 |
| `serve` | 1 | ローカルの Web UI（記録、再実行、crawl、統計）を起動します。Tier 1 で、CI 用ではありません。 |
