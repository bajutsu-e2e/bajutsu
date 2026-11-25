[English](BE-0053-bedrock-ai-provider.md) · **日本語**

# BE-0053 — 差し替え可能な AI プロバイダとしての Amazon Bedrock

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0053](BE-0053-bedrock-ai-provider-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0053") |
| 実装 PR | [#109](https://github.com/bajutsu-e2e/bajutsu/pull/109) |
| トピック | AI プロバイダ設定 |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の Tier-1 AI 経路（`record`、`triage`、`--dismiss-alerts`、`crawl`）が、直接の Anthropic API に加えて **Amazon Bedrock** 経由でも Claude を呼べるようにします。Bedrock 経路では、モデル呼び出しの認証が `ANTHROPIC_API_KEY` ではなく **AWS の認証情報（IAM）** になります。

`anthropic` SDK にはすでに `AnthropicBedrock` クライアントがあります。その `.messages.create()` インターフェースはデフォルトの `Anthropic` クライアントと同一なので、呼び出し箇所のプロンプトコードは変わりません。必要なのは、小さな**プロバイダの継ぎ目**（クライアントファクトリ 1 つと設定）と、**プロバイダ別のモデル ID** です。デフォルトは Anthropic のままです。

本項目は、[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) が掲げる「プロバイダは差し替え可能」という保証を、1 つのプロバイダに限って具体化する最初の実装です。Tier-1 側にとどまります。`run` と CI ゲートはモデルを呼ばないので、影響を受けません（[DESIGN §2 / §3.1](../../DESIGN.md)）。

## 動機

すでに AWS に標準化しているチームは、Claude の利用を Anthropic のコンシューマ API ではなく、自社の AWS アカウント経由にしたいと考えます。得られるものは 4 つです。

- 別途 `ANTHROPIC_API_KEY` を発行したりローテーションしたりする必要がない。
- 認証を、すでに運用している IAM ロールや SSO で行える。
- 利用料が、Bedrock を通じて AWS の請求にまとまる。
- 推論が、選んだ AWS リージョン内で、既存の AWS 契約の下に保たれる。

こうしたチームにとって、API キーの要件は摩擦です。調達やデータ所在の面で、明確な導入障壁になることもあります。

よくある誤解が設計を左右するので、先に正します。「Bedrock は API キー不要」は、「**認証が不要**」を意味しません。Bedrock は、Anthropic のキーを **AWS の認証情報**に置き換えます。認証情報は、環境変数、共有プロファイル、インスタンスまたはロールの認証情報から得ます。EC2、ECS、Lambda の IAM ロールなら、シークレットを一切保存せずに済みます。AWS の認証情報を管理しない環境は、Bedrock の **bearer token**（`AWS_BEARER_TOKEN_BEDROCK`）を任意で使えます。本機能は「Anthropic のキーの代わりに AWS で認証する」ものであり、「認証情報なしで使える」ものではありません。

現状は、コードを変更しなければ実現できません。5 つの AI 入口はすべて、`_ensure_client()` の中で `anthropic.Anthropic()` を遅延生成し、環境から `ANTHROPIC_API_KEY` を読みます。各モジュールは `claude-opus-4-8` というモデル定数もハードコードしています。Bedrock へ向ける継ぎ目はなく、Bedrock 形式のモデル ID を渡す手段もありません。

[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) は、広い信頼とポジショニングの保証です。プロバイダの差し替え可能性、AI 入力への秘匿化、キーがなければ AI を使わないフェイルクローズを合わせて含みます。本項目が提供するのは、1 つの具体的なプロバイダと、そのプロバイダに必要な最小の継ぎ目です。BE-0047 の秘匿化とフェイルクローズの保証は、BE-0047 のスコープのままです。両者は補完関係にあります。本項目は、BE-0047 の第 1 の保証を裏づける具体的なプロバイダです。

## 詳細設計

本節は、提案の粒度で書いています。以下はすべて、すでに存在する継ぎ目の上に築きます。

### クライアントファクトリ 1 つ（継ぎ目）

5 つの AI クラスは、同じ作り方でクライアントを作っています。いずれも `_ensure_client()` の中で `import anthropic; self._client = anthropic.Anthropic()` を呼びます。該当するのは、`bajutsu/claude_agent.py`、`bajutsu/alerts.py`、`bajutsu/claude_triage.py`、`bajutsu/crawl_guide.py`、`bajutsu/crawl_tabs.py` です。これとは別に、`bajutsu/agents.py` には、record と crawl のエージェント*種別*用の `make_agent` ファクトリがすでにあります。

クライアントファクトリを 1 つ導入します（例：`make_anthropic_client(config)`）。設定に応じて、`anthropic.Anthropic()`（現行の `anthropic` プロバイダ）か `anthropic.AnthropicBedrock(aws_region=…)`（`bedrock` プロバイダ）を返します。5 箇所の `_ensure_client()` は、すべてこのファクトリを通します。

2 つのクライアントは同じ `.messages.create()` を公開します。Bedrock はプロンプトキャッシュ（`cache_control: ephemeral`）と base64 画像入力に対応するので、リクエスト本体は変わりません。各クラスは、テスト用の注入可能な `client` コンストラクタ引数をすでに受け取ります。この引数は、テストの継ぎ目としてそのまま残します。

### 設定：app 非依存のプロバイダ選択

`bajutsu/config.py` の `defaults` と `apps.<name>` の下に、AI プロバイダのブロックを追加します。他の設定と同様に、`defaults < app` で解決します（[DESIGN §8](../../DESIGN.md)）。ブロックに入れるのは次の 3 つです。

- プロバイダの選択。`anthropic`（デフォルト）または `bedrock`。
- `bedrock` の場合は、`aws_region`（なければ `AWS_REGION` にフォールバック）と、任意の認証およびエンドポイント設定。
- プロバイダ別のモデル ID（後述）。

Anthropic がデフォルトのままなので、既存ユーザーに変化はありません。app 非依存の原則にも沿います。プロバイダの差は設定に寄せ、ツール、ドライバ、実行系は変えません。このブロックは、BE-0047 が求める設定の形式化（「エンドポイント、モデル、キーの取得元を明示的かつ差し替え可能に」）を、1 つのプロバイダについて実現したものです。

> **用語：** この軸は**AI プロバイダ**と呼び、「backend」とは呼びません。Bajutsu では `backend:` がすでに、UI の*actuator*（idb、将来の XCUITest、Web）を指します（[DESIGN §5 / §8](../../DESIGN.md)、[BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md) を参照）。LLM プロバイダは直交する別の軸です。`backend` に相乗りさせると、デバイス操作とモデルのルーティングを混同します。

### モデル ID をプロバイダ別にする

5 つのモジュールにハードコードされた `claude-opus-4-8` 定数は、プロバイダ別に上書きできるようにする必要があります。Bedrock は、プレフィックス付きの ID を要求するためです。形式は 2 つあります。

- グローバルエンドポイント形式（`global.anthropic.…`）。動的にルーティングし、追加料金はありません。
- リージョンのクロスリージョン推論（CRIS）形式（`us.anthropic.…`）。リージョンを保証し、約 10% のプレミアムがかかります。

各 AI クラスは、注入可能な `model` 引数をすでに取ります。この引数をプロバイダ設定から渡します。Bedrock の実行では Bedrock 形式の ID を、Anthropic の実行では素の ID を使います。

### 認証

`AnthropicBedrock` は、標準のプロバイダチェーンで AWS の認証情報を解決します。解決元は、`AWS_ACCESS_KEY_ID` と `AWS_SECRET_ACCESS_KEY`（一時認証なら `AWS_SESSION_TOKEN` も）、共有の `~/.aws/credentials` プロファイル、インスタンスまたはロールの認証情報です。

リージョンは、`aws_region` 引数か `AWS_REGION` から取得します。SDK はリージョンを `~/.aws/config` から読み**ません**。既定は `us-east-1` です。Bedrock の bearer token（`AWS_BEARER_TOKEN_BEDROCK`）も使えます。この経路に `ANTHROPIC_API_KEY` は不要です。

この設計は、BE-0047 のフェイルクローズ方針と噛み合います。設定したプロバイダの認証情報が解決できなければならず、別のプロバイダへ黙ってフォールバックすることはありません。

### 依存関係

Bedrock に対応するには、`anthropic[bedrock]` エクストラが必要です。SigV4 署名のために boto3 と botocore を導入し、`uv.lock` の更新を伴います。Bedrock を使わないユーザーに boto3 を強制しないよう、本体のハードな依存ではなく、**オンデマンドで入れる任意の依存グループ**を推奨します。`make serve`（[scripts/serve.sh](../../scripts/serve.sh)）が idb バックエンドの依存をオンデマンドで入れるのと、同じ方式です。正確なパッケージング（任意のエクストラか、遅延インストールか）は **TBD** です。

### 機能サポート：現行の AI 経路に劣化なし

Bajutsu が使い、Bedrock が対応している機能は次のとおりです。Messages API、プロンプトキャッシュ（`cache_control`）、base64 画像入力（`alerts` と `crawl_tabs` が使うビジョン）、tool use、structured outputs です。

Bedrock が対応しておらず、Bajutsu の AI 経路が現状**使っていない**機能は次のとおりです。Files API と URL 画像ソース、サーバーサイドツール（コード実行と web 検索）、Batches、サーバーサイドの `fallbacks` パラメータです。したがって、既存の AI 経路は機能の劣化なく移行できます。

`bajutsu/usage.py` のトークン集計は、Bedrock のレスポンスから同じ usage の形を読みます。利用量レポートはそのまま動きます。コスト自体は AWS の請求に載ります。

別経路の `claude-code` エージェント（`bajutsu/claude_code_agent.py`）は、本項目の**対象外**です。このエージェントは `claude` CLI を起動し、`ANTHROPIC_API_KEY` を取り除きます。この経路には、独自の Bedrock 機構があります。

### 未確定事項：Opus 4.8 の正確な Bedrock モデル ID

デフォルトモデルは `claude-opus-4-8` です。Bedrock の公開モデル表は、Opus 4.6（`global.anthropic.claude-opus-4-6-v1`）までです。Opus 4.8 と 4.7 は `bedrock-runtime` で到達できますが、ARN 形式の ID を持たないと注記されています。

実装時に、対象リージョンで有効化されたモデルアクセスに対して、次の 2 点を確認する必要があります。1 つは、Opus 4.8 の**正確な** Bedrock モデル ID の文字列です。もう 1 つは、新しい "Claude in Amazon Bedrock" の Messages エンドポイントが Opus 4.8 を提供するかどうかです。モデル ID は設定値なので、答えはコードの変更ではなく、設定の問題です。

### doctor（任意）

`doctor` に、決定的なチェックを足す余地があります。検査するのは次の 2 点です。1 つ目は、設定したプロバイダの認証情報が解決できることです。AWS の認証情報が存在してリージョンが設定されているか、Anthropic プロバイダなら `ANTHROPIC_API_KEY` が設定されているかを見ます。2 つ目は、設定したモデル ID がそのプロバイダにとって妥当な形式であることです。既存の実行可能ゲート（[DESIGN §7.2](../../DESIGN.md)）に倣う形です。本項目に含めるかどうかは **TBD** です。

## 検討した代替案

- **[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) に統合し、新しい項目を作らない。** 不採用としました。BE-0047 は、プロバイダの差し替え可能性、秘匿化、フェイルクローズからなる広い保証です。Bedrock は、単独で出荷できる具体的なプロバイダ統合で、AWS 固有の設計面（認証チェーン、モデル ID のプレフィックス、依存、リージョン）を持ちます。分けておけば、BE-0047 を傘として残しながら、本項目を独立して着地させられます。
- **既存の `backend` 軸を流用する。** 不採用としました。`backend` は UI の actuator（idb、XCUITest、Web）で、モデルプロバイダは直交する軸です。相乗りさせると、デバイス操作とモデルのルーティングを混同します。独立した「AI プロバイダ」の設定キーを置けば、2 つの軸をきれいに保てます。
- **boto3 や `InvokeModel` で、Bedrock を直接呼ぶ。** 不採用としました。`AnthropicBedrock` は、5 箇所がすでに使っている `.messages.create()` インターフェース（キャッシュ、ビジョン、ツール）をそのまま保ちます。変更はファクトリ越しのクライアント差し替えで済み、各プロンプト経路の書き直しになりません。
- **利便性のために、ホストされた既定の AWS 認証情報を同梱する。** 不採用としました。BE-0047 と同じ精神です。認証情報はユーザーのものにとどめます。認証情報がなければ明確なエラーにし、他者のアカウントへ黙ってフォールバックすることはしません。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

`bajutsu/agents.py`、`bajutsu/claude_agent.py`、`bajutsu/alerts.py`、`bajutsu/claude_triage.py`、`bajutsu/crawl_guide.py`、`bajutsu/crawl_tabs.py`、`bajutsu/config.py`、`bajutsu/usage.py`、`bajutsu/claude_code_agent.py`、[DESIGN §2 / §3.1 / §5 / §8](../../DESIGN.md)、[BE-0047 — AI データ主権](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)、[BE-0042 — プラットフォーム対応バックエンドレジストリ](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)、Anthropic ドキュメント「Claude on Amazon Bedrock」（`AnthropicBedrock`、`anthropic.` 接頭辞付きモデル ID）。
