[English](../ai-boundary.md) · **日本語**

# Claude を使う機能と使わない機能

> 「Bajutsu のどの部分がモデルに到達し、どの部分が何も設定せずに動くのか」に対する正本です。これは
> ツールの一級の性質であり、テストで保証されています
> （[BE-0101](../../roadmaps/BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config-ja.md)）。
> 同じ線の反対側にある「あなたの AI、あなたのキー、あなたのデータ」という保証
> （[self-hosting](self-hosting.md)、[BE-0047](../../roadmaps/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)）
> の、開発体験の面で対をなすものです。

関連: [cli](cli.md) · [concepts](concepts.md) · [recording](recording.md) · [self-hosting](self-hosting.md)

---

## 肝心の一線

Bajutsu はアーキテクチャに明確な線を引いています。決定論的な `run` と CI ゲートはモデルを一切
呼ばず、Claude に到達するのは Tier 1 の作成と調査の経路だけです。軸は、特定の資格情報があるか
どうかではなく、経路がそもそも Claude を呼ぶかどうかにあります。Claude へは三つの方法で到達できる
ため（Anthropic API、Amazon Bedrock、ブラウザ経由の OAuth（SSO）でサインインする Anthropic CLI
`ant`）、「API キーが要る」という捉え方は、区切りの軸として誤りです。正しい軸は「Claude を使う」であり、
プロバイダが増えても正しいままです。

Claude を使わない側のすべては、設定ゼロで動きます。資格情報も `.env` もログインも、いかなる AI の
ランタイムも要りません。リポジトリをクローンした直後からそのまま動きます。

## 分離

| | コマンド / 経路 | 何をするか |
|---|---|---|
| **Claude 不要**（設定ゼロ） | `run` | シナリオを決定論的に実行する。合否は機械判定のみで、モデルは関与しない |
| | `doctor` | 環境が実行可能かを確認し、現在の画面を採点する |
| | `codegen` | シナリオからネイティブの XCUITest ソースを生成する |
| | `trace` | 保存済みの実行をテキストのタイムラインとして表示する |
| | `lint` / `schema` | 実行せずにシナリオを検証する、または JSON Schema を出力する |
| | `approve` | 実行のスクリーンショットを視覚ベースラインに昇格させる |
| | `audit` / `coverage` | シナリオの決定論性を採点する、または id 名前空間の網羅状況を調べる（助言的） |
| | `report` / `export` | 完了した実行を再描画する、またはアーカイブする |
| | `mcp` / `worker` | run / doctor を MCP ツールとして提供する、またはバックグラウンドのジョブワーカーを動かす |
| | `serve` | ローカルの Web UI。何も設定せずに起動し、Claude のタブは無理なく縮退する |
| | `triage` | ルールベースのエージェントで失敗した実行を診断する（`--ai` なし） |
| **Claude を使う** | `record` | Claude でアプリを操作しながらシナリオを作成する |
| | `crawl` | Claude で自律的にアプリを探索し、画面マップを作る |
| | `triage --ai` | ルールベースの代わりに Claude で失敗した実行を診断する |
| | `run --alert-handling` | アラートガード。ステップを塞いだ OS のプロンプトを Claude が処理する |

分類はコマンド名ではなく経路の粒度です。`triage` は Claude を使わず、`--ai` フラグ一つで Claude の
経路に切り替わります。`run` も Claude を使わず、アラートガード（`--alert-handling`。シナリオごとに
既定で有効）がその Claude 経路です。資格情報が無いとき、このガードは何もしない動作に縮退し、決定論的な
実行を塞ぐことはありません。

この分離は [Tier 1 と Tier 2 の境界](concepts.md)を目に見えるようにしたものです。ここに `run` や CI
ゲートへモデルを持ち込む変更はありません。

## どこで見えるか

分類は一度だけ（`bajutsu/capabilities.py` に）定義し、あらゆる場所がそれを参照します。そのため表示面が
食い違うことはありません。

- **`bajutsu --help`** は、各コマンドを *Claude-free (zero-config)* か *Uses Claude* のいずれかに
  まとめて表示します。
- **`doctor`** は、Claude の準備状況を独立した明らかに任意の節として報告します。AI の準備が無い
  ホストでも決定論的な経路については `Ready` と採点され、Claude は別立ての「not configured (optional)」の
  行として示されます。阻害要因と混同されることはありません。
- **`serve`** は Claude のタブ（`record` と `crawl`）を見せたうえで、Claude に到達できないときは
  インラインの説明とともに無効化し、UI 内のキー入力欄へ誘導します。キーを設定するか、Bedrock を
  構成するか、`ant` CLI にサインインした時点で、タブはすぐに有効へ戻ります。

## Claude の経路をインストールする

この分離は実行時だけでなく、パッケージングの境界でもあります（[BE-0111](../../roadmaps/BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency-ja.md)）。
AI のソフトウェア開発キット（SDK）は任意インストールの extra なので、基本インストールは AI の依存を
一切含みません。

- `pip install bajutsu`：決定論的なオーサリングと実行の経路（`run`、`doctor`、`lint`、`codegen`、
  `trace`、`approve` など、上記の Claude-free 列すべて）です。AI SDK はインストールされず、ここから
  モデルに到達することはありません。
- `pip install bajutsu[ai]`：Claude の経路（`record`、`crawl`、`triage --ai`、`run --alert-handling`）の
  ために Anthropic の SDK を追加します。Amazon Bedrock プロバイダを使う場合は代わりに
  `bajutsu[bedrock]` を指定します。同じ SDK の上に Bedrock 版を重ねる形になります。

コントリビュータは `uv sync --group dev` ですべての extra をまとめて導入するため、ゲートは変わらず
Claude の経路をテストし続けます。AI-free の保証はあくまで**基本**インストールに関するものであり、
テストの網を外すという意味ではありません。

## 使いたいときに Claude へ到達する

次のいずれか一つで「Claude を使う」経路が満たされます（詳細は [self-hosting](self-hosting.md) と
[recording](recording.md) にあります）。

- **Anthropic API**：`ANTHROPIC_API_KEY`（または `ai.keyEnv` が指す環境変数）を設定します。
- **Amazon Bedrock**：標準の AWS 資格情報チェーンに加え、プロバイダ接頭辞つきのモデル id（`ai.model`
  または `$BAJUTSU_BEDROCK_MODEL`）を用意します。
- **Anthropic CLI（`ant`）**：`ai.provider: ant` を設定して `ant auth login` を実行します。キーの
  代わりに Claude の Pro / Max / Console のシートを使います（BE-0163）。

どの手段で認証するかは設定の問題です（[BE-0047](../../roadmaps/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)、
[BE-0053](../../roadmaps/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider-ja.md)、
[BE-0163](../../roadmaps/BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md)
に従います）。どれを選んでも、上記の分類は変わりません。
