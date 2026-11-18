[English](BE-0163-ant-cli-oauth-provider.md) · **日本語**

# BE-0163 — Claude Code CLI バックエンドを `ant` CLI の OAuth プロバイダに置き換える

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0163](BE-0163-ant-cli-oauth-provider-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0163") |
| 実装 PR | [#689](https://github.com/bajutsu-e2e/bajutsu/pull/689) |
| トピック | AI プロバイダ設定 |
| 関連 | [BE-0125](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction-ja.md), [BE-0175](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login-ja.md), [BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の Tier 1 の AI 呼び出し口は、`record`、`crawl`、`triage --ai`、`--dismiss-alerts`、
`enrich` の5つです。Claude への到達経路は今のところ、構造の異なる二系統に分かれています。

このうち4つと `record` の既定エージェントは、SDK 経由の `bajutsu.ai` レジストリ（BE-0104）を
通ります。登録済みのプロバイダは `anthropic`（BE-0047）と `bedrock`（BE-0053）の2つです。どちらも
`ANTHROPIC_API_KEY` または AWS の資格情報で認証します。

`record` と `crawl` は、これとは別の経路として `--agent claude-code` を選べます。
このエージェントの実体は、`bajutsu/claude_code_agent.py` と、
`bajutsu/crawl_guide.py` の `ClaudeCodeActionProposer` です。
どちらも `bajutsu/agents.py` 経由で呼ばれます。
Claude Code CLI（`claude -p`）を呼び出すもう1つの経路です。
API 利用料の代わりに、Claude Pro / Max のサブスクリプションに課金できます。

この二重構造を、`bajutsu/ai_availability.py` の docstring は歪みとして認識しています。
docstring は、この経路を「既存の仕組みを薄く一般化したものであり、新しい subsystem ではない」と
述べています。

サブスクリプション課金の恩恵は、見た目より狭くなっています。届くのは `record` と `crawl` だけで、
`triage --ai`、`--dismiss-alerts`、`enrich` には届きません。届く経路もテキストのみで、スクリーン
ショットを送りません。そのため、画面の解釈に画像を使う `crawl` は、この経路では機能が落ちます。
`claude -p` の構造化出力モードが、生の Messages API とは別物だからです。

この提案は、この迂回路をまるごと1つの新しい AI プロバイダ `ant` に置き換えます。`ant` は公式の
[Anthropic CLI](https://github.com/anthropics/anthropic-cli) で、`ant auth login` はブラウザ経由の
OAuth（SSO）フローを Claude Console に対して行います。このプロバイダを、Bedrock のときと同じ形で
既存の `bajutsu.ai` の仕組みに登録します。

`ant messages create` は、Anthropic Python SDK が使う Messages API を薄くラップしたコマンドです。
system プロンプト、`tool_choice` の強制、画像コンテンツブロック、モデルの一覧まで同一です。そのため、
既存の変換層（`bajutsu/ai/anthropic.py` の `AnthropicBackend`）には、一切手を入れません。
変更が要るのはクライアント構築（`bajutsu/anthropic_client.py`）だけで、3つ目の認証経路が加わります。

`ai.provider: ant` が全 AI 呼び出し口を均等にカバーしたら、
`bajutsu/claude_code_agent.py` と `--agent claude-code` を削除します。

サブスクリプション・SSO 課金というもともとの狙いは、`record` 専用の特別なエージェントではなく、
すべての AI 機能が共有する1つの経路で達成されます。

## 動機

- `ANTHROPIC_API_KEY` を発行・ローテーションせず、持っている Pro / Max / Console のシートに
  Claude の利用料を寄せたいチームがあります。現状では `record` と `crawl` でしかできません。
  しかもテキストのみです。そのため `crawl` は、画面の解釈に頼っている画像を失います。
- `triage --ai`（画像を使った原因調査）、`--dismiss-alerts`（画像そのものが判定の入力です）、
  `enrich` には、サブスクリプション・SSO の選択肢がそもそもありません。これは好みの問題ではなく、
  実際の機能差です。
- 「Claude に到達する」経路が、`bajutsu.ai` レジストリと CLI エージェントの二本立てになっています。
  この状態は、`bajutsu.ai_availability` の docstring が整理すべき歪みとして名指ししているとおり、
  安定した設計ではありません。新しい AI 機能を追加するたびに、`triage --ai`、`--dismiss-alerts`、
  `enrich` が現状そうであるように、「サブスクリプション課金に対応するかどうか」を個別に判断する
  必要があります。
- `ant` は、Messages API を薄くそのままラップしているため、この差を素直に埋められます。確認した
  限り、`--system`、`--tool` / `--tool-choice`、`--message` 内の画像コンテンツブロック、SDK と
  同じモデル一覧を備えています。組み込みに、並行した変換層も画像対応の劣化も、システムプロンプトや
  ツールスキーマの二重管理も要りません。
- この変更は、BE-0053 が Bedrock に対して行った変更と同じ形です。ホスティング先ではなく、認証手段
  という別の軸に適用します。
- `ant` が使えるようになった時点で `claude_code_agent.py` を削除すると、次の3つが消えます。
  モジュール1つ、`--agent` の種類1つ、そして BE-0125 と同期を取り続ける必要があった denylist
  （`_DISALLOWED_TOOLS`）1つです。
  そのエージェント専用だった `ai_availability.py` の分岐も消えます。
- `--agent claude-code` の利点だった「API キー不要でシート課金できる」という性質は残ります。1つの
  経路だけでなく、すべての AI 機能の性質になります。

## 詳細設計

1. **`anthropic_client.py` に3つ目のプロバイダを追加する。** `PROVIDERS` に `"ant"` を加え、
   `provider()` が認識するようにします。`make_client()` には新しい分岐を追加します。
   `ai.key_env` / `ANTHROPIC_API_KEY` を読む代わりに、`ant` バイナリを呼んでベアラートークンを
   取得します（例えば `ant auth print-credentials --access-token`。`--profile` /
   `ANTHROPIC_PROFILE` を尊重します）。そのうえで `anthropic.Anthropic(auth_token=token,
   base_url=ai.base_url or None)` を構築します。SDK の引数には、`api_key`（`x-api-key` ヘッダー）
   ではなく `auth_token`（`Authorization: Bearer` ヘッダー）を使います。このコードベースは、
   `claude setup-token` / `CLAUDE_CODE_OAUTH_TOKEN` によるサブスクリプション認証で、すでに同じ引数を
   使っています（`claude_code_agent.py` の docstring を参照）。実績のある仕組みであり、新しい仕組み
   ではありません。
2. **`credential_gap()` / `resolve_model()` を拡張する。** `credential_gap()` に `"ant"` の分岐を
   追加します。`ant` バイナリが存在して有効な資格情報を持っているときは `None` を返し、そうでない
   ときはギャップトークンを返します。バイナリ不在なら `"ant-cli-missing"`、存在するが未ログインなら
   `"ant-cli-unauthenticated"` などです。これは、`ai_availability.py` にある既存の
   `CLAUDE_CODE_MISSING` のパターンを踏襲したもので、後述のとおり本提案で撤去します。
   `resolve_model()` は変更不要です。`ant` のモデル一覧は、Bedrock のようなプレフィックス付きでは
   なく、素の Anthropic の id とそのまま一致します。
3. **`ai/registry.py` に登録する。** `"ant"` を、同じ `Adapter` に登録します。
   `anthropic` / `bedrock` が共有している、factory と credential_gap の組です。
   `AnthropicBackend` は、構築済みの SDK クライアントさえ持てばプロバイダに依存しません。
   新しいアダプタクラスは要りません。
4. **プロファイル選択に新しい設定項目は要りません。** `ant` 自身がすでに `--profile` /
   `ANTHROPIC_PROFILE` から名前付きプロファイルを解決します。bajutsu 側で `ai.profile` のような
   新しいフィールドを作らず、この環境変数をそのまま流用します。
5. **`claude-code` エージェントを削除する。** 削除の内容は次のとおりです。
   - `bajutsu/claude_code_agent.py` を削除します。
   - `agents.AGENT_KINDS` と `make_agent` の分岐から `"claude-code"` を外します。
   - `bajutsu/crawl_guide.py` の `ClaudeCodeActionProposer` と、`make_guide` 内の `claude-code`
     分岐を撤去します。
   - `record` / `crawl` が `bajutsu/cli/commands/record.py` / `crawl.py` で公開している `--agent`
     オプションを撤去します。両コマンドは、他の AI 経路と同じくプロバイダレジストリ経由で
     解決するようになります。
   - `ai_availability.py` の CLI 専用分岐（`CLAUDE_CODE_MISSING`、`agent_kind` 引数）を撤去します。
     すべての経路が1つの `ai.provider` で解決するようになるためです。
   - `serve` の AI プロバイダ選択 UI（`bajutsu/templates/serve.html.j2` / `serve.js`、
     `bajutsu/serve/operations/config.py`）を、エージェント種別の切り替えではなく
     `anthropic` / `bedrock` / `ant` を選ぶ形に更新します。
   - `bajutsu/capabilities.py` と `doctor` のうち、旧エージェント種別を表示している箇所も更新します。
6. **ドキュメントとテストを更新する。** `docs/configuration.md`（および `docs/ja/`）に、Bedrock と
   並べて新しいプロバイダを記載します。`docs/recording.md`（および `docs/ja/`）からは、Claude Code
   CLI の節を落とします。`claude_code_agent.py` / `agents.py` / `ai_availability.py` の既存テストは、
   新しいプロバイダ分岐のテストに置き換えます。`tests/test_anthropic_client.py`、
   `tests/test_ai_availability.py`、`tests/test_ai_backend.py` にはすでに Bedrock 相当のひな形が
   あり、それを拡張します。
7. **新しい依存は増えません。** `ant` は、ユーザー自身が導入する外部バイナリです（Homebrew、
   リリースバイナリ、`go install`）。bajutsu は `subprocess` 経由で呼び出します。`ai_availability.py`
   が今も `claude` CLI を `shutil.which` で確認しているのと同じやり方です。bajutsu 自身が `ant` を
   同梱・インストールすることはありません。

実装時に詰める **未決事項**:

- 非対話的に新鮮なベアラートークンを取得・確認する、正確な `ant` のサブコマンドと引数。また、
  その呼び出しを `make_client()` の呼び出しごと（実行のたび）に行うか、キャッシュするかの判断。
- `ant auth status` の人間向け出力が、`credential_gap()` のためにパースできるほど安定していると
  見なせるか、それとも機械可読な代替手段があるかの確認。

## 検討した代替案

- **既存の `"anthropic"` プロバイダの下でのフォールバックとして透過的に扱う案。**
  `ANTHROPIC_API_KEY` をまず試し、なければ `ant` の OAuth 資格情報を黙って試す形です。却下しました。
  明示的なプロバイダ名にしておけば、`doctor`、`ai_availability`、`serve` は、どの対象がどの認証経路を
  前提にしているかを曖昧さなく言えます。Bedrock も、`anthropic` のフォールバックではなく独立した名前の
  プロバイダになっています。
- **`--agent claude-code` を残したまま `ant` プロバイダを追加するだけにする案**（削除しない）。
  却下しました。1つの仕組みですべての AI 経路が、サブスクリプション・SSO 課金の資格情報に届くように
  なりました。そのため、`record` 専用でテキストのみの経路をもう一本残すことは、`ai_availability.py` がすでに
  指摘している表面の重複そのものです。撤去は後回しにできる片付けではなく、この提案の眼目です。
- **`ant messages create` を呼び出しごとに CLI プロキシとして使う案。** `claude_code_agent.py` が
  `claude -p` に対して行っているのと同じやり方です。却下しました。
  `ant auth print-credentials --access-token` は、ベアラートークンを返します。
  既存の Anthropic Python SDK は、このトークンをそのまま受け取れます。
  AI 呼び出し口は、SDK の利用（プロンプトキャッシュ、既存の `AnthropicBackend` の変換、エラー処理）を
  そのまま続けられます。呼び出しごとのサブプロセスも、`ai/anthropic.py` と並行するもう1つの
  リクエスト・レスポンス変換も抱える必要がありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `anthropic_client.py` に `ant` プロバイダを登録する（`PROVIDERS` / `make_client` /
      `credential_gap` / `resolve_model`）
- [x] `ai/registry.py` に `ant` を登録する
- [x] `bajutsu/claude_code_agent.py` と `claude-code` エージェント種別を削除する
      （`agents.py`、`crawl_guide.py` の `ClaudeCodeActionProposer`、`ai_availability.py`、
      `record` / `crawl` の `--agent` オプション）
- [x] `serve` の AI プロバイダ選択 UI を更新する
- [x] ドキュメントを更新する（`docs/configuration.md`、`docs/recording.md`、日本語版）
- [x] 新しいプロバイダ分岐のテストを追加し、`claude_code_agent` のテストを置き換える

ログ:

- [#689](https://github.com/bajutsu-e2e/bajutsu/pull/689)：`ant` プロバイダを一通り実装しました。`anthropic_client.py` に三つ目の認証経路
  （`ant auth print-credentials` が返すトークンを `auth_token` に渡す）を追加し、`anthropic` /
  `bedrock` と共有するアダプタに登録しました。`claude-code` エージェント種別と `--agent` オプション、
  denylist、`ai_availability` の CLI 専用分岐は撤去し、`serve` の設定画面のセレクタは
  `api-key` / `bedrock` / `ant` を選ぶ形にしました。ドキュメント（英日）とテストも更新し、
  `make check` は緑です。
- 追随: 直接 API のプロバイダ名を `anthropic` から `api-key` に改名しました（Bedrock も `ant` も
  Anthropic なので、名前で認証手段を表すためです）。旧称 `anthropic` は `normalize_provider` を通じて
  今も `api-key` として解決されるため、既存の config はそのまま動きます。

## 参考

- `bajutsu/anthropic_client.py`、`bajutsu/ai/registry.py`、`bajutsu/ai/anthropic.py`、
  `bajutsu/ai_availability.py`、`bajutsu/agents.py`、`bajutsu/claude_code_agent.py`
- [BE-0053 — Amazon Bedrock as a pluggable AI provider](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider-ja.md)：この提案がなぞる前例です（新しい認証経路を同じやり方でレジストリに登録します）
- [BE-0104 — Vendor-neutral AI backend interface](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)：この提案が三つ目のプロバイダを加えるレジストリです
- [BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)：この提案のプロバイダも守るべき、プロバイダ差し替え可能性とフェイルクローズドの保証です
- [BE-0125 — Restrict the claude-code authoring agent tools](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction-ja.md)：この提案が、対象のエージェントごと撤去する denylist です
- Anthropic CLI のドキュメント: [CLI quickstart](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/quickstart)、
  [CLI authentication options](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/authentication)、
  [`ant messages create` reference](https://platform.claude.com/docs/en/api/cli/messages/create)
