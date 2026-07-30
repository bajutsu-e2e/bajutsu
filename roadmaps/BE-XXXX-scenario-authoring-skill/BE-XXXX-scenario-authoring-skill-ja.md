[English](BE-XXXX-scenario-authoring-skill.md) · **日本語**

# BE-XXXX — ソースコードからシナリオを起草し自己検証まで行う Claude Code スキルを配布する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-scenario-authoring-skill-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | オーサリング体験 |
<!-- /BE-METADATA -->

## はじめに

本項目では、Claude Code 向けのスキルを配布します。Claude Code は、Anthropic のコーディングエージェント
Command Line Interface（CLI）です。このスキルは、`bajutsu` の配布物に同梱するパッケージとして提供します。

スキルは、対象自身のソースコードを読み取ります。読み取った内容から、`bajutsu.config.yaml`
のエントリを起草します。最初のシナリオファイルも起草します。起草した内容は、`run` がすでに備えている
デバイス不要のツール群で検証します。開発者は、Simulator やブラウザ、エミュレータを起動する前に、この
検証を終えられます。

パッケージには、スキル専用の短い手順書を同梱します。手順書のそばには、文法と用語をすでに定義している
5つの文書を一字一句そのまま複製して置きます。文書のうち3つは scenarios.md、dsl-grammar.md、
configuration.md です。残る2つは selectors.md と glossary.md です。手順書は、これらの文書に書かれて
いるルールを書き直しません。手順書は、参照先を示すだけの役割にとどまります。

新設する `bajutsu skill export` コマンドは、パッケージをコピーします。コピー元は、インストール済みの
`bajutsu` です。コピー先は、対象プロジェクトの `.claude/skills/` です。エクスポートしたコピーは、常に
そのプロジェクトが依存している `bajutsu` のバージョンと一致します。

## 動機

[新しいターゲットのオンボーディング](../../docs/ja/configuration.md#新しいターゲットのオンボーディング)
は、現在5段階の手作業です。まず、対象の識別子規約を適用します。次に、`targets.<name>` の config
エントリを1つ追加します。必要なら、`setup:` のプレリュードを切り出します。`bajutsu doctor` で検証します。
最後に、新しい識別子を参照するシナリオを配置します。

シナリオ文法の参照資料を一度も開いたことのない開発者は、5つの文書を同時に頭に置かなければなりません。
[`configuration.md`](../../docs/ja/configuration.md) は、config の形を扱います。
[`scenarios.md`](../../docs/ja/scenarios.md) と [`dsl-grammar.md`](../../docs/ja/dsl-grammar.md) は、
ステップ構文を扱います。[`selectors.md`](../../docs/ja/selectors.md) は、どのセレクタ種別が安定して
いるかを扱います。[`glossary.md`](../../docs/ja/glossary.md) は、他の4つが前提とする用語を扱います。
この開発者は、対象のソースコードも読まなければなりません。動かす価値のある識別子を、すべて
見つけ出すためです。

[`record`](../BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md) は、この負担をすでに
減らしています。ただし、稼働中の対象を操作できる開発者に対してだけです。Claude は、稼働中の対象を
探索します。そして、画面で観測した内容からシナリオを書きます。

この「ライブなツリーに接地する」という性質は、コーディングエージェントには不要です。Claude Code は、
対象プロジェクト自身のリポジトリの中に、すでに存在しています。そのため、`accessibilityIdentifier` や
`data-testid`、`resource-id` の値を、ソースコードから直接読み取れます。Simulator やブラウザ、
エミュレータを起動する必要がありません。

配布する手順書には、2つのルールを含めます。この2つのルールが、ソースコードへのアクセスを安全な
オーサリングへと変えます。ルールがなければ、ソースコードへのアクセスだけが、別の発想に近づいてしまいます。
その発想とは、「AI が合否を決める」というものです。本ロードマップは、この発想をすでに退けています
（[ロードマップ → 取り込まない](../README-ja.md#取り込まない既に充足--スコープ外)）。

第一に、手順書は、ドラフトする各ステップを識別子に接地させることを要求します。その識別子は、スキルが
対象のソースコードの中で実際に見つけたものでなければなりません。ゴールが必要とする要素に、まだ
識別子が付いていない場合があります。その場合、スキルは不足そのものを報告します。不安定なセレクタを
発明して、不足を覆い隠すことはありません。

第二に、手順書は、完成したドラフトを4つの読み取り専用ツールに通すことを要求します。`run` は、この4つを
すでに備えています。4つとは、`lint`、`audit`、`trace --explain`、`doctor --scenario --environment-only`
です。この4つのツールは、いずれもモデルを呼びません。いずれも、合否を決めません。スキルの判定は、
「文法として整っている」までにとどまります。「合格している」までは、進みません。

唯一の権威ある確認は、今までと変わりません。開発者自身が、稼働中の対象に対して `bajutsu doctor
--target <name>` を実行します。あるいは、ひとつめの `bajutsu run` を実行します。オンボーディングは、
もともとこの手順を求めています。プライムディレクティブ1は、この境界をどこにも動かしていないために、
保たれます。

## 詳細設計

### 1. パッケージの構成が文法の唯一の正本を守る

パッケージのソースは、本リポジトリの `bajutsu/scenario_author_skill/` に置きます。ここには、2種類の
ファイルを持たせます。

- `SKILL.md` — 手順書。以下の作業手順、バックエンドごとの識別子の見つけ方、自己検証のループの記述先。
  意図的な短さであり、参照文書のルールの書き直しではなく、参照文書への道筋づけという役割にとどまる設計。
- `references/` — 次の5つの文書の一字一句そのままの複製。
  - `docs/scenarios.md`
  - `docs/dsl-grammar.md`
  - `docs/configuration.md`
  - `docs/selectors.md`
  - `docs/glossary.md`

既存のバイリンガル文書チェックと同じ仕組みのテストを、新たに用意します。このテストは、`references/`
以下のいずれかのファイルが `docs/` の対応ファイルから1バイトでも乖離すると、`make check` を失敗させます。
文法の唯一の正本は、`docs/` に置いたままとします。同梱するコピーは、同期されたミラーにとどめます。
保守担当者が単独で書き換えて、正本からずれることはありません。

シナリオの JavaScript Object Notation（JSON）Schema は、そもそも複製しません。手順書は、対象プロジェクト
にすでにインストールされている `bajutsu schema` を、スキル自身に実行させます。正本の形は、常にインストール
済みのバージョンそのものです。古びるコピーを、持ちません。

### 2. `bajutsu skill export` が対象プロジェクトへパッケージを展開する

`bajutsu skill export [--dest .claude/skills/bajutsu-scenario-author] [--force]` は、新しいコマンド
です。Claude 不要です。インストール済みの `bajutsu` から、このパッケージを対象プロジェクトへコピーします。

パッケージは、通常のパッケージデータとして同梱します（[`pyproject.toml`](../../pyproject.toml)）。
force-include の仕組みは不要です。その仕組みは、`bajutsu/_xcuitest_runner/**` という別件のためだけに
存在します。このディレクトリは `.gitignore` の対象であり、生成物を置く場所です。force-include が
なければ、hatchling はこのディレクトリを取りこぼします。

展開先がすでに存在する場合、コマンドは終了コード非ゼロで終わります。ただし、例外が1つあります。以前の
エクスポートが生成した展開先です。`--force` を付けると、この確認を上書きします。`bajutsu` をアップグレード
した後、素朴に再実行するだけで済みます。パッケージは、新しくインストールされたバージョンへ常に更新
されます。

### 3. 手順書は推測ではなく必ずソースコードにドラフトを接地させる

手順書は、スキルを次の手順に沿って進めます。

1. **バックエンドの特定** — 対象プロジェクト自身の形からの判断。ブラウザフレームワークを含む
   `package.json` は web の目印。`.xcodeproj` は iOS の目印。Gradle モジュールは Android の目印。
2. **既存の安定した識別子のソースコード内での発見** — バックエンドごとの目印の読み取り。web では
   `data-testid` 属性の探索。iOS では `accessibilityIdentifier` の設定の探索。Android では
   `resource-id` / `testTag` の探索。対応関係の正本は、[`selectors.md`](../../docs/ja/selectors.md#解決セマンティクス)
   の `id` の行。対象のソースツリーの直接読み取りによる発見であり、Claude Code のセッションがすでに
   持っているこのリポジトリへの読み取りアクセスの利用。
3. **`targets.<name>` とシナリオのステップのドラフト作成** — 手順2で見つけた識別子だけの使用。
   [`selectors.md`](../../docs/ja/selectors.md) の安定度のはしごへの準拠。id ベースのセレクタは、座標を
   使うジェスチャーやインデックスに常に優先。識別子のない要素がゴールに必要な場合、不足の報告。報告には、
   対象の名前空間規約に沿った名前を付与
   （[`configuration.md`](../../docs/ja/configuration.md#識別子の命名規約)）。不安定なセレクタへの後退は
   なし。後退はプライムディレクティブ2、決定論を第一に置く原則への違反。
4. **自己検証ループの実行** — 作業単位4で定義したループの、ドラフトを準備完了と報告する前の実行。

### 4. デバイス不要の自己検証ループがドラフトと開発者のあいだに立つ

手順書は、4つのツールをスキルがドラフトを準備完了と報告する前に要求します。この順序で要求します。

第一に、`bajutsu lint` は、ドラフトが文法として妥当かを確認します。

第二に、`bajutsu audit` は、ドラフトの安定度の判定を確認します。この判定に、Fragile なセレクタを含んでは
いけません。含む場合、スキルはその手順を自ら書き直します。他人の問題として報告することはありません。

第三に、`bajutsu trace --explain` は、広すぎる capture policy のルールを検出します。最初の実行がコストを
払う前に検出します。

第四に、スキルは `bajutsu doctor --target <name> --scenario <file> --environment-only` を実行します。
これは capability プリフライトです。選択したバックエンドがドラフトの使う構文をすべてサポートすることを
確認します。

この4つのツールは、いずれも読み取り専用です。この4つは、すでに `bajutsu` の Claude 不要の経路です
（[`ai-boundary.md`](../../docs/ja/ai-boundary.md)）。手順書への組み込みは、新しいモデル呼び出しをどこ
にも増やしません。

この4つすべてに通ってはじめて、スキルの報告はドラフトの準備完了を告げます。その前には、告げません。
報告は、開発者が次に行うべき手順も名指しします。それは、唯一の権威ある確認です。稼働中の対象に対する
`bajutsu doctor --target <name>`、またはひとつめの `bajutsu run` です。

### 5. ドキュメント

- [`docs/getting-started/ios.md`](../../docs/ja/getting-started/ios.md) と
  [`web.md`](../../docs/ja/getting-started/web.md) の既存の「AI でオーサリングする」節への小節追加。
  追加する小節は、対象をまだ稼働させていない開発者向けに、`record` に代わるソースコード接地の
  選択肢として `bajutsu skill export` を紹介するもの。Android 向けの getting-started ページは未作成で
  あり、それまでの間、Android バックエンドの導入手順は [`docs/configuration.md`](../../docs/ja/configuration.md)
  に記載。
- [`docs/cli.md`](../../docs/ja/cli.md) への、`lint` / `schema` の隣での新しい `skill export` コマンド
  の記載。
- [`docs/ai-boundary.md`](../../docs/ja/ai-boundary.md) への、`skill export` の新しい行の追加。行の
  置き場所は Claude 不要の列。このコマンド自体は、ファイルコピーのみの存在。あわせて添える注記の
  内容は、エクスポートしたスキルの行うドラフト作成が、外部エージェントである Claude Code の内部での
  実行だという点。この実行の中身は、`bajutsu` の既存の Claude 不要のコマンドの呼び出しのみ。ドラフト
  作成そのものは `bajutsu` のコードパスではなく、行を持たない位置づけ。

## 検討した代替案

* **専用に凝縮した新しいチートシートの執筆**（既存文書の同梱ではなく）。却下。文法の説明を手作業で別に
  持てば、`scenarios.md` や `dsl-grammar.md` のどちらかが変わった最初の瞬間に、対応する編集がなされない
  まま乖離するおそれ。手順書は、重複ではなく同梱した文書への道筋づけにとどめることで、「手早く参照できる」
  という同じ目的を達成。
* **`bajutsu` リポジトリの Git submodule としてのバンドル配布**。却下。submodule は、わずか数個の
  ファイルのために本モノレポの全履歴を持ち込む代物。加えて、`--init --recursive` の付け忘れや detached
  `HEAD` といった submodule 特有の落とし穴は、普段 submodule を使っていない contributor にとってよく
  知られたつまずき。新鮮さの基準を、対象プロジェクトが自身の依存管理ですでに扱っている `bajutsu` の
  バージョンではなく Git の ref に結び付けてしまう点も問題。
* **手作業のコピー&ペースト手順の文書化**（新しい CLI コマンドの見送り）。より小さな v1 として検討。
  パッケージのインストール先を getting-started に示し、開発者への手作業でのコピーを委ねる案。却下。手で
  コピーしたバンドルは、`bajutsu` のアップグレード後にやり直し忘れやすく、インストール済みのバージョンが
  実際に受け付けるスキーマからの、気づかれないままの乖離という代物。`bajutsu skill export` は、それを
  避けるための小さな1つのコマンドで済む話。
* **`record` や `crawl` へのソースコード読み取りの追加**（別スキルを設けるのではなく）。却下。`record`
  と `crawl` は、稼働中の対象を操作するために作られた `bajutsu` の Python コードパス。任意のソース
  ツリーを読む力をそこに教え込めば、「ツールが対象を操作する」と「コーディングエージェントがソース
  コードからドラフトする」という Tier 1 の境界の曖昧化を招くおそれ。加えて、`bajutsu` 自身に、Claude
  Code をスキルとして呼び出せばもともと無償で得られるソースコード読み取りのエージェント的振る舞いの
  埋め込みも必要。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] **パッケージの構成** — `bajutsu/scenario_author_skill/` への `SKILL.md` と `references/` の配置。
  `docs/` との乖離チェックの `make check` への組み込み。
- [ ] **`bajutsu skill export`** — 新しい CLI コマンド、wheel パッケージング、`--dest` / `--force`。
- [ ] **手順書** — バックエンドの特定、バックエンドごとの識別子の見つけ方、ソースコード接地のドラフト
  作成、識別子不足の報告。
- [ ] **自己検証ループ** — `lint` → `audit` → `trace --explain` → `doctor --scenario
  --environment-only` の手順書への組み込み。Fragile 時の書き直しルール付き。
- [ ] **ドキュメント** — getting-started の小節、`cli.md`、`ai-boundary.md`。

## 参考

- [`docs/configuration.md`](../../docs/ja/configuration.md) — ターゲットのオンボーディングと識別子の
  命名規約。
- [`docs/scenarios.md`](../../docs/ja/scenarios.md) と [`docs/dsl-grammar.md`](../../docs/ja/dsl-grammar.md)
  — このスキルがドラフトの拠り所とする文法。
- [`docs/selectors.md`](../../docs/ja/selectors.md) — 安定度のはしご。
- [`docs/glossary.md`](../../docs/ja/glossary.md) — 他の4つの同梱文書が前提とする用語。
- [`docs/ai-boundary.md`](../../docs/ja/ai-boundary.md) — Claude 不要と Claude を使う経路の分離。
- [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md) — `record`。この
  スキルが補完する、稼働中の対象を操作する [Tier 1](../../docs/ja/glossary.md#2-つの層)
  のオーサリング経路。
- [ロードマップ → 取り込まない](../README-ja.md#取り込まない既に充足--スコープ外) — 本提案が距離を
  置く、退けられた「AI が合否を決める」という発想。
