[English](BE-XXXX-ant-cli-oauth-provider.md) · **日本語**

# BE-XXXX — Claude Code CLI バックエンドを `ant` CLI の OAuth プロバイダに置き換える

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ant-cli-oauth-provider-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | AI provider configuration |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の Tier 1 の AI 呼び出し口は `record`、`crawl`、`triage --ai`、`--dismiss-alerts`、
`enrich` の五つですが、Claude への到達経路は今のところ構造の異なる二系統に分かれています。この
うち四つと、`record` の既定エージェントは SDK 経由の `bajutsu.ai` レジストリ（BE-0104）を通り
ます。ここに登録済みのプロバイダは `anthropic`（BE-0047）と `bedrock`（BE-0053）の二つだけで、
どちらも `ANTHROPIC_API_KEY` または AWS の資格情報で認証します。`record` と `crawl` は、これとは
別に `--agent claude-code`（`bajutsu/claude_code_agent.py` と `bajutsu/crawl_guide.py` の
`ClaudeCodeActionProposer`、いずれも `bajutsu/agents.py` 経由）を選べます。これは Claude Code
CLI（`claude -p`）を呼び出すもう一つの経路で、API 利用料の代わりに Claude Pro / Max の
サブスクリプションに課金できます。この二重構造は `bajutsu/ai_availability.py` 自身の docstring が
「既存の仕組みを薄く一般化したものであり、新しい subsystem ではない」と述べており、解消すべき
歪みとしてすでに認識されています。しかもサブスクリプション課金の恩恵は見た目より狭く、届くのは
`record` と `crawl` だけで（`triage --ai` / `--dismiss-alerts` / `enrich` には届きません）、
しかもそこでもテキストのみです（スクリーンショットを送らない）。そのため、画面の解釈に画像を使う
`crawl` はこの経路では機能が落ちます。`claude -p` の構造化出力モードは生の Messages API とは
別物だからです。

この提案は、この迂回路をまるごと一つの新しい AI プロバイダ `ant` に置き換えます。`ant` は公式の
[Anthropic CLI](https://github.com/anthropics/anthropic-cli) で、`ant auth login` はブラウザ
経由の OAuth（SSO）フローを Claude Console に対して行います。これを Bedrock のときと同じ形で
既存の `bajutsu.ai` の仕組みに登録します。`ant messages create` は Anthropic Python SDK が使う
のと同じ Messages API を薄くラップしただけのコマンドで（system プロンプト、`tool_choice` の
強制、画像コンテンツブロック、モデルの一覧まで同一です)、既存の変換層（`bajutsu/ai/anthropic.py`
の `AnthropicBackend`）には一切手を入れる必要がありません。変更が要るのはクライアント構築
（`bajutsu/anthropic_client.py`）だけで、そこに三つ目の認証経路が加わります。`ai.provider: ant`
がすべての AI 呼び出し口を均等にカバーするようになった時点で、`bajutsu/claude_code_agent.py` と
`--agent claude-code` は削除します。サブスクリプション・SSO 課金というもともとの狙いは、
`record` 専用の特別なエージェントではなく、すべての AI 機能が共有する一つの経路で達成されます。

## 動機

- `ANTHROPIC_API_KEY` を発行・ローテーションする代わりに、すでに持っている Pro / Max /
  Console のシートに Claude の利用料を寄せたいチームは、現状では `record` と `crawl` でしか、
  しかもテキストのみでしかそれができません。そのため `crawl` は、画面の解釈に頼っている画像を
  この経路では失います。`triage --ai`（画像を使った原因調査）、`--dismiss-alerts`（画像そのものが
  判定の入力です）、`enrich` には、サブスクリプション・SSO の選択肢がそもそもありません。これは
  好みの問題ではなく実際の機能差です。
- 「Claude に到達する」経路が構造的に異なる二本立てになっている状態（`bajutsu.ai` レジストリと
  CLI エージェントの二重構造）は、`bajutsu.ai_availability` 自身の docstring がすでに整理すべき
  歪みとして名指ししているとおりです。新しい AI 機能を追加するたびに、`crawl` / `triage` /
  `triage --ai` / `--dismiss-alerts` / `enrich` が現状そうであるように「サブスクリプション課金に
  対応するかどうか」を個別に判断する
  羽目になります。
- `ant` はこの差を素直に埋められます。確認した限り `--system`、`--tool` / `--tool-choice`、
  `--message` 内の画像コンテンツブロック、SDK と同じモデル一覧まで Messages API をそのまま
  なぞっているため、組み込みに並行した変換層も画像対応の劣化も、システムプロンプトやツール
  スキーマの二重管理も要りません。これは BE-0053 が Bedrock に対して行った変更と同じ形を、
  ホスティング先ではなく認証手段という別の軸に適用するものです。
- `ant` が使えるようになった時点で `claude_code_agent.py` を削除すれば、モジュール一つ、
  `--agent` の種類一つ、BE-0125 と同期を取り続ける必要があった denylist
  （`_DISALLOWED_TOOLS`）一つ、そしてそのためだけに存在する `ai_availability.py` の分岐が
  まとめて消えます。それでいて `--agent claude-code` の利点だった「API キー不要でシート課金
  できる」という性質は、一つの経路だけでなくすべての AI 機能の性質になります。

## 詳細設計

1. **`anthropic_client.py` に三つ目のプロバイダを追加する。** `PROVIDERS` に `"ant"` を
   加え、`provider()` がそれを認識するようにします。`make_client()` には新しい分岐を追加し、
   `ai.key_env` / `ANTHROPIC_API_KEY` を読む代わりに `ant` バイナリを呼んで（例えば `ant auth
   print-credentials --access-token`、`--profile` / `ANTHROPIC_PROFILE` を尊重）ベアラー
   トークンを取得し、`anthropic.Anthropic(auth_token=token, base_url=ai.base_url or None)` を
   構築します。`api_key`（`x-api-key` ヘッダー）ではなく `auth_token`（`Authorization:
   Bearer` ヘッダー）を使う点は、このコードベースが `claude setup-token` /
   `CLAUDE_CODE_OAUTH_TOKEN` によるサブスクリプション認証ですでに使っている SDK の引数と
   同じであり（`claude_code_agent.py` の docstring を参照）、実績のある仕組みです。
2. **`credential_gap()` / `resolve_model()` を拡張する。** `credential_gap()` に `"ant"`
   の分岐を追加し、`ant` バイナリが存在して有効な資格情報を持っているときは `None` を、
   そうでないときはギャップトークン（バイナリ不在なら `"ant-cli-missing"`、存在するが未
   ログインなら `"ant-cli-unauthenticated"` など）を返します。これは `ai_availability.py`
   にある既存の `CLAUDE_CODE_MISSING` のパターン（後述のとおり本提案で撤去します）を踏襲した
   ものです。`resolve_model()` は変更不要です。`ant` のモデル一覧は Bedrock のようなプレフィ
   ックス付きではなく、素の Anthropic の id とそのまま一致します。
3. **`ai/registry.py` に登録する。** `"ant"` を、`anthropic` / `bedrock` がすでに共有して
   いるのと同じ `Adapter`（factory と credential_gap）に登録します。`AnthropicBackend` は
   構築済みの SDK クライアントさえ持てばプロバイダに依存しないため、新しいアダプタクラスは
   要りません。
4. **プロファイル選択に新しい設定項目は要りません。** `ant` 自身がすでに `--profile` /
   `ANTHROPIC_PROFILE` から名前付きプロファイルを解決するため、bajutsu 側で `ai.profile` の
   ような新しいフィールドを作らず、この環境変数をそのまま流用します。
5. **`claude-code` エージェントを削除する。** `bajutsu/claude_code_agent.py` を削除し、
   `agents.AGENT_KINDS` と `make_agent` の分岐から `"claude-code"` を外します。あわせて
   `bajutsu/crawl_guide.py` の `ClaudeCodeActionProposer` と `make_guide` 内の `claude-code`
   分岐（および `record` / `crawl` が公開している `--agent` オプション、
   `bajutsu/cli/commands/record.py` / `crawl.py`）も撤去し、両コマンドが他の AI 経路と同じく
   プロバイダレジストリ経由で解決するようにします。`ai_availability.py` の CLI 専用分岐
   （`CLAUDE_CODE_MISSING`、`agent_kind` 引数）も、
   すべての経路が一つの `ai.provider` で解決するようになるため撤去します。`serve` の AI
   プロバイダ選択 UI（`bajutsu/templates/serve.html.j2` / `serve.js`、
   `bajutsu/serve/operations/config.py`）は、エージェント種別の切り替えではなく
   `anthropic` / `bedrock` / `ant` を選ぶ形に更新します。`bajutsu/capabilities.py` と
   `doctor` のうち旧エージェント種別を表示している箇所も合わせて更新します。
6. **ドキュメントとテストを更新する。** `docs/configuration.md`（および `docs/ja/`）に
   Bedrock と並べて新しいプロバイダを記載し、`docs/recording.md`（および `docs/ja/`）から
   Claude Code CLI の節を落とします。`claude_code_agent.py` / `agents.py` /
   `ai_availability.py` の既存テストは、新しいプロバイダ分岐のテストに置き換えます。
   `tests/test_anthropic_client.py`、`tests/test_ai_availability.py`、
   `tests/test_ai_backend.py` にはすでに Bedrock 相当のひな形があり、それを拡張します。
7. **新しい依存は増えません。** `ant` はユーザー自身が導入する外部バイナリです
   （Homebrew・リリースバイナリ・`go install`）。呼び出しは `subprocess` 経由で、
   `ai_availability.py` が今も `claude` CLI を `shutil.which` で確認しているのと同じやり方
   です。bajutsu 自身がこれを同梱・インストールすることはありません。

実装時に詰める **未決事項**:

- 非対話的に新鮮なベアラートークンを取得・確認する正確な `ant` のサブコマンドと引数、
  および `make_client()` の呼び出しごと（実行のたびに）行うかキャッシュするかの判断。
- `ant auth status` の人間向け出力を `credential_gap()` のためにパースできるほど安定して
  いると見なせるか、それとも機械可読な代替手段があるかの確認。

## 検討した代替案

- **既存の `"anthropic"` プロバイダの下でのフォールバックとして透過的に扱う案**
  （`ANTHROPIC_API_KEY` をまず試し、なければ `ant` の OAuth 資格情報を黙って試す）。
  却下しました。明示的なプロバイダ名にしておけば、`doctor` / `ai_availability` /
  `serve` はどの対象がどの認証経路を前提にしているかを曖昧さなく言えます。Bedrock が
  `anthropic` のフォールバックではなく独立した名前のプロバイダになっているのと同じ考え
  方です。
- **`--agent claude-code` を残したまま `ant` プロバイダを追加するだけにする案**（削除
  しない）。却下しました。一つの仕組みですべての AI 経路がサブスクリプション・SSO 課金の
  資格情報に届くようになった以上、`record` 専用でテキストのみの経路をもう一本残すことは、
  `ai_availability.py` がすでに指摘している表面の重複そのものです。撤去は後回しにできる
  片付けではなく、この提案の眼目です。
- **`ant messages create` を呼び出しごとに CLI プロキシとして使う案**
  （`claude_code_agent.py` が `claude -p` に対して行っているのと同じやり方）。却下しました。
  `ant auth print-credentials --access-token` は既存の Anthropic Python SDK がそのまま
  受け取れるベアラートークンを返すため、AI 呼び出し口は SDK の利用（プロンプトキャッシュ、
  既存の `AnthropicBackend` の変換、エラー処理）をそのまま続けられます。呼び出しごとの
  サブプロセスや、`ai/anthropic.py` と並行するもう一つのリクエスト・レスポンス変換を
  抱える必要がありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `anthropic_client.py` に `ant` プロバイダを登録する（`PROVIDERS` / `make_client` /
      `credential_gap` / `resolve_model`）
- [ ] `ai/registry.py` に `ant` を登録する
- [ ] `bajutsu/claude_code_agent.py` と `claude-code` エージェント種別を削除する
      （`agents.py`、`crawl_guide.py` の `ClaudeCodeActionProposer`、`ai_availability.py`、
      `record` / `crawl` の `--agent` オプション）
- [ ] `serve` の AI プロバイダ選択 UI を更新する
- [ ] ドキュメントを更新する（`docs/configuration.md`、`docs/recording.md`、日本語版）
- [ ] 新しいプロバイダ分岐のテストを追加し、`claude_code_agent` のテストを置き換える

## 参考

- `bajutsu/anthropic_client.py`、`bajutsu/ai/registry.py`、`bajutsu/ai/anthropic.py`、
  `bajutsu/ai_availability.py`、`bajutsu/agents.py`、`bajutsu/claude_code_agent.py`
- [BE-0053 — Amazon Bedrock as a pluggable AI provider](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider-ja.md)
  ―この提案がなぞる前例です（新しい認証経路を同じやり方でレジストリに登録します）
- [BE-0104 — Vendor-neutral AI backend interface](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)
  ―この提案が三つ目のプロバイダを加えるレジストリです
- [BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)
  ―この提案のプロバイダも守るべき、プロバイダ差し替え可能性とフェイルクローズドの保証です
- [BE-0125 — Restrict the claude-code authoring agent tools](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction-ja.md)
  ―この提案が、対象のエージェントごと撤去する denylist です
- Anthropic CLI のドキュメント: [CLI quickstart](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/quickstart)、
  [CLI authentication options](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/authentication)、
  [`ant messages create` reference](https://platform.claude.com/docs/en/api/cli/messages/create)
