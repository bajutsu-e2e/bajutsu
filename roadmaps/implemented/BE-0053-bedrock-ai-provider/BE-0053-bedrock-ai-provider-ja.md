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

Bajutsu の Tier-1 AI 経路（`record`、`triage`、`--dismiss-alerts`、`crawl`）が、直接の Anthropic
API に加えて **Amazon Bedrock** 経由でも Claude を呼べるようにします。Bedrock 経路では、モデル
呼び出しの認証が `ANTHROPIC_API_KEY` ではなく **AWS の認証情報（IAM）** になります。`anthropic`
SDK にはすでに `AnthropicBedrock` クライアントがあり、その `.messages.create()` インターフェースは
デフォルトの `Anthropic` クライアントと同一です。したがって呼び出し箇所のプロンプトコードは変わらず、
必要なのは小さな **プロバイダの継ぎ目**（クライアントファクトリ 1 つと設定）と **プロバイダ別の
モデル ID** だけです。デフォルトは Anthropic のままです。本項目は
[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) が掲げる「プロバイダは
差し替え可能」という保証を、1 つのプロバイダに限って具体化する最初の実装です。厳密に Tier-1 側に
留まり、`run` と CI ゲートはモデルを呼ばず影響を受けません（[DESIGN §2 / §3.1](../../../DESIGN.md)）。

## 動機

すでに AWS に標準化しているチームは、Claude の利用を Anthropic のコンシューマ API ではなく自社の
AWS アカウント経由にしたい、という要望を持ちます。別途 `ANTHROPIC_API_KEY` を発行やローテーション
する必要がなく、認証はすでに運用している IAM ロール／SSO で行え、利用料は Bedrock を通じて AWS の
請求にまとまり、推論は選んだ AWS リージョン内、かつ既存の AWS 契約の下に保たれます。こうしたチームに
とって API キー要件は摩擦であり、調達やデータ所在の面で明確な導入障壁になることもあります。

設計を左右するため、よくある誤解を先に正します。「Bedrock は API キー不要」は「**認証が不要**」を
意味しません。Bedrock は Anthropic のキーを **AWS の認証情報** に置き換えます。認証情報には環境変数、
共有プロファイル、インスタンス／ロールの認証情報（EC2/ECS/Lambda の IAM ロールならシークレットを
一切保存せずに済みます）があり、AWS 認証情報を管理しない環境向けには Bedrock の **bearer token**
（`AWS_BEARER_TOKEN_BEDROCK`）という選択肢もあります。つまり本機能は「Anthropic のキーの代わりに
AWS で認証する」のであって、「認証情報なしで使える」ではありません。

現状はコード変更なしには不可能です。5 つの AI 入口はすべて `_ensure_client()` の中で
`anthropic.Anthropic()` を遅延生成し、環境から `ANTHROPIC_API_KEY` を読みます。さらに各モジュールが
`claude-opus-4-8` というモデル定数をハードコードしています。Bedrock へ向ける継ぎ目も、Bedrock 形式の
モデル ID を渡す手段もありません。

[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) との関係: あちらは広い
信頼／ポジショニングの保証（プロバイダの差し替え可能性 **＋** AI 入力への秘匿化 **＋** キーがなければ
AI なしのフェイルクローズ）です。本項目は 1 つの具体的なプロバイダと、それに必要な最小の継ぎ目だけを
提供します。BE-0047 の秘匿化とフェイルクローズの保証はあちらのスコープのままです。両者は補完関係に
あり、本項目は BE-0047 の第 1 の保証を裏づける具体プロバイダです。

## 詳細設計

提案粒度です。以下はすべて、すでに存在する継ぎ目の上に築きます。

### クライアントファクトリ 1 つ（継ぎ目）

5 つの AI クラスはどれも同じ作り方をしています。`_ensure_client()` の中で
`import anthropic; self._client = anthropic.Anthropic()` を呼ぶ箇所が、
`bajutsu/claude_agent.py`、`bajutsu/alerts.py`、`bajutsu/claude_triage.py`、
`bajutsu/crawl_guide.py`、`bajutsu/crawl_tabs.py` の 5 つです。`bajutsu/agents.py` にはすでに
record／crawl のエージェント *種別* 用の `make_agent` ファクトリがあります。これとは別に、設定に応じて
`anthropic.Anthropic()`（現行の `anthropic` プロバイダ）か `anthropic.AnthropicBedrock(aws_region=…)`
（`bedrock` プロバイダ）を返すクライアントファクトリ（例: `make_anthropic_client(config)`）を 1 つ
導入し、5 箇所の `_ensure_client()` をそこへ通します。両クライアントは同じ `.messages.create()` を
公開するため、Bedrock も対応するプロンプトキャッシュ（`cache_control: ephemeral`）と base64 画像入力を
含めて、リクエスト本体は変わりません。各クラスがすでに受け取る注入用 `client` コンストラクタ引数
（テスト用）はテストの継ぎ目としてそのまま残します。

### 設定：app 非依存のプロバイダ選択

`bajutsu/config.py` の `defaults` ／ `apps.<name>` の下に AI プロバイダのブロックを追加し、他の設定と
同様に `defaults < app` で解決します（[DESIGN §8](../../../DESIGN.md)）:

- プロバイダ選択：`anthropic`（デフォルト）または `bedrock`;
- `bedrock` の場合: `aws_region`（無ければ `AWS_REGION` にフォールバック）、および任意の認証／
  エンドポイント設定;
- プロバイダ別のモデル ID（後述）。

これにより Anthropic がデフォルトのままになり（既存ユーザーに変化なし）、app 非依存の原則にも沿います。
プロバイダ差は設定に寄せ、ツール、ドライバ、実行系は不変です。これは BE-0047 が求める設定の
形式化（「エンドポイント、モデル、キーの取得元を明示的かつ差し替え可能に」）を、1 プロバイダ分だけ
実現したものです。

> **用語:** この軸は **AI プロバイダ** と呼び、「backend」とは呼びません。Bajutsu で `backend:` は
> すでに UI の *actuator*（idb／将来の XCUITest／Web。[DESIGN §5 / §8](../../../DESIGN.md)、
> [BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md) を参照）
> を指します。LLM プロバイダは直交する別軸であり、`backend` に相乗りさせるとデバイス操作とモデル
> ルーティングを混同します。

### モデル ID をプロバイダ別にする

5 つのモジュールにハードコードされた `claude-opus-4-8` 定数は、プロバイダ別に上書き可能にする必要が
あります。Bedrock はプレフィックス付き ID を要求するためです: グローバルエンドポイント形式
（`global.anthropic.…`、動的ルーティング、追加料金なし）か、リージョン CRIS 形式
（`us.anthropic.…`、リージョン保証、約 10% のプレミアム）です。各 AI クラスはすでに注入用 `model`
引数を取るので、これをプロバイダ設定から渡し、Bedrock 実行では Bedrock 形式 ID を、Anthropic 実行
では素の ID を使うようにします。

### 認証

`AnthropicBedrock` は標準のプロバイダチェーンで AWS 認証情報を解決します。解決元は
`AWS_ACCESS_KEY_ID` ／ `AWS_SECRET_ACCESS_KEY`（一時認証なら `AWS_SESSION_TOKEN`）、共有
`~/.aws/credentials` プロファイル、またはインスタンス／ロールの認証情報です。リージョンは
`aws_region` 引数か `AWS_REGION` から取得します（SDK はリージョンについては `~/.aws/config` を
読みません。既定は `us-east-1`）。Bedrock の bearer token（`AWS_BEARER_TOKEN_BEDROCK`）も使えます。
この経路に `ANTHROPIC_API_KEY` は不要です。これは BE-0047 のフェイルクローズ方針とも噛み合い、
設定したプロバイダの認証情報が解決できることを要求し、別プロバイダへ黙ってフォールバックしません。

### 依存関係

Bedrock 対応には `anthropic[bedrock]` エクストラ（SigV4 署名のため boto3／botocore を導入）が必要で、
`uv.lock` の更新を伴います。Bedrock を使わないユーザーに boto3 を強制しないよう、ハードな本体依存
ではなく **オンデマンドで入れる任意の依存グループ** を推奨します。これは `make serve`
（[scripts/serve.sh](../../../scripts/serve.sh)）が idb バックエンドの依存をオンデマンドで入れるのと
同じ流儀です。正確なパッケージング（任意エクストラか遅延インストールか）は **TBD** です。

### 機能サポート：現行 AI 経路に劣化なし

Bajutsu が使い、かつ Bedrock が対応: Messages API、プロンプトキャッシュ（`cache_control`）、base64
画像入力（`alerts` ／ `crawl_tabs` が使うビジョン）、tool use、structured outputs。
Bedrock 非対応だが Bajutsu の AI 経路が現状 **使っていない**: Files API と URL 画像ソース、サーバー
サイドツール（コード実行／web 検索）、Batches、サーバーサイドの `fallbacks` パラメータ。したがって
既存の AI 経路は機能劣化なく移行できます。`bajutsu/usage.py` のトークン集計は Bedrock レスポンスから
同じ usage 形状を読むため、利用量レポートはそのまま動きます（コスト自体は AWS 請求側）。別経路の
`claude-code` エージェント（`bajutsu/claude_code_agent.py`。`claude` CLI を起動し
`ANTHROPIC_API_KEY` を子プロセスから取り除く）は本項目の **対象外** です。あの経路には独自の
Bedrock 機構があります。

### 未確定事項：Opus 4.8 の正確な Bedrock モデル ID

デフォルトモデルは `claude-opus-4-8` です。Bedrock の公開モデル表は Opus 4.6
（`global.anthropic.claude-opus-4-6-v1`）までで、Opus 4.8 / 4.7 は `bedrock-runtime` で到達可能だが
ARN 形式の ID を持たないと注記されています。よって Opus 4.8 の **正確な** Bedrock モデル ID 文字列
（および新しい "Claude in Amazon Bedrock" Messages エンドポイント経由かどうか）は、対象リージョンで
有効化されたモデルアクセスに対して実装時に確認する必要があります。モデル ID は設定値なので、これは
コードではなく設定の問題です。

### doctor（任意）

`doctor` に、設定したプロバイダの認証情報が解決できること（AWS 認証情報の有無／リージョン設定、または
Anthropic プロバイダなら `ANTHROPIC_API_KEY` の有無）と、モデル ID がそのプロバイダにとって妥当な
形式であることを、決定的に検査するチェックを足す余地があります。既存の実行可能ゲート
（[DESIGN §7.2](../../../DESIGN.md)）に倣う形です。本項目に含めるかは **TBD** です。

## 検討した代替案

- **[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) に統合し、新項目を
  作らない。** 不採用。BE-0047 は広い保証（プロバイダの差し替え可能性 + 秘匿化 + フェイルクローズ）
  であり、Bedrock は AWS 固有の設計面（認証チェーン、モデル ID プレフィックス、依存、リージョン）を
  持つ、単独で出荷可能な具体プロバイダ統合です。分けておくことで、BE-0047 を傘として残しつつ本項目を
  独立して着地させられます。
- **既存の `backend` 軸を流用する。** 不採用。`backend` は UI の actuator（idb／XCUITest／Web）です。
  モデルプロバイダは直交します。相乗りさせるとデバイス操作とモデルルーティングを混同します。独立した
  「AI プロバイダ」設定キーが両軸を綺麗に保ちます。
- **boto3 ／ `InvokeModel` で直接 Bedrock を呼ぶ。** 不採用。`AnthropicBedrock` は 5 箇所がすでに
  使う `.messages.create()` インターフェース（キャッシュ、ビジョン、ツール）をそのまま保つので、変更は
  ファクトリ越しのクライアント差し替えで済み、各プロンプト経路の書き直しになりません。
- **利便性のためにホストされた既定の AWS 認証情報を同梱する。** 不採用（BE-0047 と同じ精神）。認証情報
  はユーザーのものに留め、その不在は明確なエラーであって、誰かのアカウントへの静かなフォールバック
  ではありません。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

`bajutsu/agents.py`、`bajutsu/claude_agent.py`、`bajutsu/alerts.py`、`bajutsu/claude_triage.py`、
`bajutsu/crawl_guide.py`、`bajutsu/crawl_tabs.py`、`bajutsu/config.py`、`bajutsu/usage.py`、
`bajutsu/claude_code_agent.py`、[DESIGN §2 / §3.1 / §5 / §8](../../../DESIGN.md)、
[BE-0047 — AI データ主権](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)、
[BE-0042 — プラットフォーム対応バックエンドレジストリ](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)、
Anthropic ドキュメント「Claude on Amazon Bedrock」（`AnthropicBedrock`、`anthropic.` 接頭辞付きモデル ID）。
