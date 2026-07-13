[English](BE-0188-configurable-ai-output-language.md) · **日本語**

# BE-0188 — AI 出力言語を record と crawl で設定可能にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0188](BE-0188-configurable-ai-output-language-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0188") |
| 実装 PR | [#772](https://github.com/bajutsu-e2e/bajutsu/pull/772) |
| トピック | AI プロバイダ設定 |
<!-- /BE-METADATA -->

## はじめに

AI が関与する記述の経路である `record` と `crawl` は、モデルが書いた自由文のプローズを出力します。
`record` がシナリオに書き込む `from:` の由来（provenance）文字列と、`crawl` が探索しながら流す推論の
ナレーションがそれです。現状、このプローズの言語はユーザーが選べません。`record` では言語が創発的で、
自然言語のゴールがたまたま書かれていた言語に追従します。そのため日本語のゴールからは日本語の由来が、
英語のゴールからは英語の由来が出力され、入力とは独立に出力言語を固定する手段がありません。`crawl` では、
入力によらずモデルのプロ―ズが英語で出てきます。この提案は、明示的で設定可能な **AI 出力言語** を追加し、
両方の経路から一貫した、選んだ言語のプロ―ズが得られるようにします。設定は config に一度書けばよく、
実行ごとの上書きもできます。

この設定が制御するのは **AI が自分の生成プロ―ズを書く言語だけ** です。モデルを一切呼ばない決定論的な
`run` / CI ゲートには入り込まないので、合否の判定には触れません。prime directive 1 と整合する、Tier-1 の
記述・調査向けの設定です。

## 動機

- **偶然ではなく一貫性を。** `record` では、出力言語がゴールの言語の副作用であって、選んだ結果では
  ありません。ゴールを日本語で書きつつ由来は英語にしたいチーム（あるいはその逆）には、動かせるつまみが
  ありません。由来文字列（`from:`）は、各ステップがなぜ存在するのかを人間が読む形で残す恒久的な記録です
  （[BE-0044](../BE-0044-scenario-provenance/BE-0044-scenario-provenance-ja.md)）。その言語は、ゴールごとの
  偶然ではなくプロジェクトの選択であるべきです。
- **`crawl` は英語に固定されている。** 日本語で作業するチームは、モデルがその能力を持っているにもかかわらず、
  `crawl` から日本語の推論ナレーションを一切得られません。
- **つまみの置き場所は明白。** `record` と `crawl` はすでに `AiSettings` / `AiConfig`
  （`provider` / `model` / `base_url` / `key_env` / `effort`）を共有していて、これはターゲット単位でも
  グローバルでも解決され、すべての AI 呼び出し箇所にすでに配線済みです。出力言語のフィールドは `effort` の
  隣に配線なしで収まります。serve の Web UI はすでに `effort` をプルダウンで描画しているので、新しい設定は
  その兄弟となる `<select>` です。
- **既存の `locale` とは別物。** `locale` フィールド（`Preconditions.locale`、Simulator 起動時に
  `-AppleLocale` / `-AppleLanguages` で適用）が設定するのは **デバイス／アプリの UI 言語** で、これは
  直交する層です。両者を混同すると footgun になります（たとえば日本語ローカライズしたアプリを英語で
  ナレーションしながらテストする、あるいはその逆）。そのため、これは意図的に別の設定とします。

## 詳細設計

作業は次の 4 単位で MECE に分解できます。

### 1. config フィールド `ai.language`

`AiConfig` / `AiSettings`（`bajutsu/config.py`）に `language` フィールドを、`effort` と並列で追加します。

- **値**：enum とし、`ja` | `en` | `auto` のいずれか。`auto` は現状の `record` の挙動（モデルがゴールの
  言語に追従する）を保ち、これを **デフォルト** とするので、既存のプロジェクトは変わりません。`crawl` では
  `auto` は現状の英語デフォルトに解決されます（追従すべきゴールがないため）。
- グローバルとターゲット単位（`targets.<name>.ai.language`）の両方で解決でき、既存の `AiSettings` の解決を
  再利用するので、優先順位は `effort` と一致します。

### 2. プロンプトへの配線

解決した言語を AI 呼び出し箇所へ配線し、モデルが自由文の出力を制約するようにします。

- **`record`** — 記述側のシステムプロンプト（`bajutsu/claude_agent.py`）と補強エージェント
  （`bajutsu/claude_enrich_agent.py`）に、出力言語の指示を 1 つ追記します。「自由文の出力（推論、意図、
  由来）はすべて `<language>` で書く」という形の指示です。値が `auto` のときは何も追記しません（現状の
  挙動）。これは既存の `--alert-instruction` フラグと同じ仕組みで、解決した文字列をシステムプロンプトに
  折り込みます。
- **`crawl`** — crawl のガイド／タブのシステムプロンプト（`bajutsu/crawl_guide.py`、
  `bajutsu/crawl_tabs.py`）に同じ指示を追記し、モデルの生成プロ―ズ（`Proposal.thought`、流れる推論）が
  選んだ言語で出るようにします。

### 3. CLI フラグ `--language`

`record` と `crawl` の両方（`bajutsu/cli/commands/record.py`、`bajutsu/cli/commands/crawl.py`）に
`--language {ja,en,auto}` を追加し、その実行に限って解決済みの config 値を上書きします。`--effort` が
`ai.effort` を上書きするのと同じ形です。指定がなければ config 値（デフォルト `auto`）が適用されます。

### 4. serve Web UI のプルダウン

serve の AI 設定パネルに **出力言語** の `<select>` を、既存の推論 effort のプルダウン
（`bajutsu/templates/serve.html.j2` / `serve.js` の `#ai-effort`、`data-testid="settings.effort"`）の
隣に追加します。同じ `ai.language` 値を読み書きし、`data-testid` は `settings.language` として、
web バックエンドのドッグフード用テストが操作できるようにします。

### スコープの境界（ローカライズしないもの）

この設定が制御するのは **モデルが生成したプロ―ズだけ** です。恒久化される `crawl` レポート
（`screenmap.json` / `screenmap.html`）は LLM のプロ―ズではありません。その文言は、アプリ画面の UI から
そのまま写したもの（`TabTarget.label`）か、ハードコードされた英語の f-string
（`bajutsu/crawl_flows.py`、`bajutsu/crawl_repro.py`）です。これらのハードコードされたレポート文字列の
ローカライズは別の関心事であり（Bajutsu 自身のレポート表示の i18n であって、「AI が何語で書くか」ではない
ため）、ここでは明示的に **スコープ外** とします。区別を記録に残すため、将来の項目候補として言及するに
とどめます。ここから 1 つの帰結が生じます。`crawl` の `Proposal.thought` は現状ログ／ストリーム出力のみで
レポートには書き込まれないため、`crawl` で目に見える効果は流れる推論に現れます。コミットされる成果物に
設定が現れる経路は、`record` の恒久的な `from:` 由来のほうです。

## 検討した代替案

- **自由文の言語文字列（`--alert-instruction` 方式）。** 任意の言語名（「日本語」「français」）を受け付け
  ます。柔軟ですが、検証しにくく、プルダウンとして描画しにくく、チーム内で値がばらつくのを招きます。
  小さな enum を選び、これを退けました。具体的な必要が出てくれば enum は拡張できます。
- **`auto` をなくし、常に明示指定を必須にする。** `record` の出力言語は完全に決定論的になりますが、由来が
  今日は創発的である既存のすべてのプロジェクトにとって破壊的変更であり、「ゴールにそのまま追従する」挙動を
  望むユーザーからその挙動を奪います。代わりに `auto` をデフォルトとして残しました。
- **既存の `locale` フィールドを再利用する。** 退けました。`locale` はデバイス／アプリの UI 言語で、直交
  する層です。過負荷にすると「日本語アプリを英語 AI ナレーションで」という状態が表現できなくなり、2 つの
  別々の関心事を混同します。
- **ハードコードされた `crawl` レポート文字列を同じ変更でローカライズする。** スコープ外です（上のスコープ
  境界を参照）。それはレポート表示の i18n であって、モデルの出力言語を制約する問題とは別物であり、折り込むと
  この項目の MECE な境界がぼやけます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `ai.language` config フィールド（`AiConfig` / `AiSettings`）、enum `ja` | `en` | `auto`、デフォルト `auto`
- [x] `record`（記述＋補強）と `crawl`（ガイド＋タブ）のプロンプトへの配線
- [x] `record` と `crawl` の `--language` CLI フラグ
- [x] serve の AI 設定パネルの出力言語プルダウン

ログ:

- 4 単位すべてを実装しました。`AiConfig` / `AiSettings` の `ai.language` と、`anthropic_client` の
  `resolve_language` / `language_instruction`（設定が先で `BAJUTSU_AI_LANGUAGE` 環境変数がフォールバック、
  未知の値は `auto`）。出力言語の指示を `record` の記述／補強と `crawl` のガイド／タブの各エージェントの
  静的システムプロンプトへ折り込み（`auto` では空文字なのでプロンプトキャッシュを保ちます）。`record` /
  `crawl` に config を上書きする `--language {ja,en,auto}` フラグ。serve の AI 設定パネルに `/api/provider`
  を通じて配線した「出力言語」プルダウン。各面にユニットテストと日英のドキュメントを追加しました。この設定は
  決定論的な `run` / CI の判定には一切触れません。（[#772](https://github.com/bajutsu-e2e/bajutsu/pull/772)）

## 参考

- [BE-0044 — シナリオの由来（`from:`）](../BE-0044-scenario-provenance/BE-0044-scenario-provenance-ja.md)
  — この設定が制御する `record` の恒久的なプロ―ズ。
- [BE-0104 — ベンダー中立な AI バックエンドインターフェース](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)
  と [BE-0103 — 開発タスクごとにモデルと推論 effort を最適化する](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering-ja.md)
  — このフィールドが隣に並ぶ `AiSettings` のつまみ（`effort`）。
- `bajutsu/config.py`（`AiConfig` / `AiSettings`）、`bajutsu/claude_agent.py`、
  `bajutsu/crawl_guide.py`、`bajutsu/cli/commands/{record,crawl}.py`、
  `bajutsu/templates/serve.html.j2` — 4 つの作業単位が触れる箇所。
