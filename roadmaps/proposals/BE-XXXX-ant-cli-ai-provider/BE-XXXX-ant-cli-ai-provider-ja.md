[English](BE-XXXX-ant-cli-ai-provider.md) · **日本語**

# BE-XXXX — OAuth 認証の AI プロバイダとしての ant CLI

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ant-cli-ai-provider-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | AI provider configuration |
| 関連 | [BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md), [BE-0053](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider-ja.md), [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`AiConfig.provider` に、Anthropic 公式の CLI である `ant` を使う 3 つ目の値を追加します。これにより
ローカルで開発する利用者は、bajutsu のすべての AI 経路（`record`、`triage --ai`、`run
--dismiss-alerts`、`crawl --explore`、MCP の enrich）を、長期間有効な `ANTHROPIC_API_KEY` を `.env`
に貼り付ける代わりに、`ant auth login` によるブラウザ経由の OAuth 認証で使えるようになります。これは
認証情報の取り扱いを楽にするための変更であり、コストやモデルの系統を変えるものではありません。`ant
auth login` が発行するトークンも、静的な API キーと同じ Console の API ワークスペースに対して
従量課金されるためです。得られる利点は、平文の秘密情報を gitignore 対象のファイルへ置く代わりに、
スコープが絞られ取り消し可能な、ブラウザ発行の認証情報（`ant auth status` / `ant auth logout` で
管理できます）を使えることです。

## 動機

[BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) に
よって、AI を使うすべての経路は `anthropic_client.make_client()` という単一のファクトリを通してモデルに
到達し、`AiConfig.provider`（`anthropic` | `bedrock`）で選べるようになっています。しかし既存の 2 つの
プロバイダは、どちらも静的な秘密情報を前提にしています。`ai.keyEnv` で名指しされた環境変数の API キー、
または Bedrock 向けの AWS IAM 認証情報です。ローカルで bajutsu を動かす開発者にとってこれは、
Anthropic Console で API キーを作り、`.env` に貼り付け、後から手動でローテーションや失効をする必要が
あることを意味します。OAuth ベースのツールがまさに避けようとしている、平文の長期有効な秘密情報その
ものです。

bajutsu にはすでに、OAuth ベースの AI 経路が 1 つあります。`record --agent claude-code`
（[`claude_code_agent.py`](../../../../bajutsu/claude_code_agent.py)）は Claude Code CLI
（`claude -p`）にシェルアウトし、Claude Pro/Max のサブスクリプショントークン
（`CLAUDE_CODE_OAUTH_TOKEN`）で認証します。この経路は本項目の対象から意図的に外します。
サブスクリプション課金であり、`record` に限定され、しかもテキストのみだからです（スクリーンショットは
送らず、アクセシビリティツリーからエージェントが推論します）。本項目が狙うのはこれとは別の、補完的な
隙間です。**API** 側に留まりつつ（従量課金のまま、Messages API の全機能、すなわち画像入力・ツール
使用を含め、残り 5 つの AI 経路がすでに送っているのと同じリクエスト形状を保ちつつ）OAuth 認証できる
経路です。現在の `anthropic` プロバイダが持つ能力を一切手放さずに、認証情報の取り扱いだけを楽にします。

Anthropic の `ant` CLI は、まさにこの置き換えのために作られています。`ant auth login` はブラウザ
経由の OAuth フローを開き、発行されたトークンを 1 つの Console ワークスペースに紐づけて
`$ANTHROPIC_CONFIG_DIR` の下に保存します。そして `ant messages create` は Messages API の全機能を
話します。マルチモーダルなコンテンツブロック（画像や PDF を `@file` で埋め込めます)、ツール使用の
ターンのための繰り返し可能な `--tool` フラグ、機械可読な解析のための `--format json` /
`--transform` です。これは、既存の 6 つの AI 呼び出し箇所が今日使っている能力をすべて覆っています。

## 詳細設計

提案レベルの粒度です。作業はすべて既存の単一ファクトリのシーム（`bajutsu/anthropic_client.py`）の
内側に収まります。どの呼び出し側（`claude_agent.py`、`claude_triage.py`、`alerts.py`、
`claude_enrich_agent.py`、`crawl_guide.py`、`crawl_tabs.py`）も変更しません。これは
[BE-0053](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider-ja.md) が
呼び出し側に手を入れずに Bedrock を追加したのと同じやり方です。作業は次の 4 つの単位に沿って
MECE に分割します。

### 1. 既存の呼び出し側が使う属性の棚卸し

6 つの呼び出し側が `anthropic` SDK のリクエストのどのフィールドを設定しているか（system プロンプト、
テキスト / 画像 / tool_use / tool_result の各コンテンツブロックを持つメッセージ、`tools`、`model`、
`max_tokens`）、そしてレスポンスのどの属性を読んでいるか（`message.content[*].type` / `.text` /
`.id` / `.input`、`message.stop_reason`）を列挙します。これが、新しいアダプタのシムが再現しなければ
ならない正確なサーフェスを決めます。Anthropic SDK の型全体ではなく、この 6 経路が触れている部分
だけです。

### 2. `make_client()` の背後に置く `ant` バックエンドのアダプタ

`make_client()` に `provider: ant-cli` の分岐を追加し、第 1 項で棚卸しした表面を持つ
`.messages.create(...)` を公開する小さなオブジェクトを返すようにします。この呼び出しを
`subprocess` 経由の `ant messages create --format json ...` の実行にシリアライズし、解析した
JSON レスポンスを、第 1 項で特定した属性に一致する軽量なオブジェクトへ整形し直します。エラー
（0 以外の終了コード、`ant` の JSON エラー本体）は、呼び出し側がすでに `anthropic` SDK から
捕捉している例外の型へ対応づけるため、既存のエラー処理には手を入れる必要がありません。

### 3. バイナリに支えられたプロバイダのための fail closed な認証チェック

現在の `credential_gap()` は「名指しされた環境変数が設定されているか」を確認します。`ant-cli` では
値の有無は正しいチェックではないため、これを「`ant` バイナリが `PATH` 上にあり、かつ
`ANTHROPIC_API_KEY`（`ant` もこれを尊重します）が設定されているか、または `ant auth status` が
有効な認証情報を報告しているか」という条件に拡張します。どちらも欠けている場合は、`ant auth login`
を指す明確なメッセージとともに fail closed にし、静かにフォールバックするのではなく実行可能な
エラーを返すという BE-0047 の規律に合わせます。

### 4. 設定、ドキュメント、テスト

- 設定：`AiSettings` / ターゲットごとの `ai` の上書きで `ai: { provider: ant-cli }` とします。
  新しいフィールドは追加せず、既存の `provider` の列挙のパターンをそのまま踏襲します。
- ドキュメント（英語 / 日本語の両方、`docs/` と `docs/ja/`、`README.md`、`.env.example`）：
  `ant-cli` を**ローカル開発向け**の選択肢として文書化します。外部の `ant` バイナリのインストール
  （Homebrew、リリースバイナリ、`go install`。idb バックエンドにおける `idb_companion` と同じ
  範疇のローカルの前提条件です）と、`ant auth login` のためのブラウザ操作が必要なため、CI や
  ヘッドレスホスト向けではありません（そこでは静的キーを使う `anthropic`、または Bedrock の IAM が
  引き続き適切な選択です）。
- テスト：新しいアダプタに対して `subprocess.run` / `Popen` をモックする単体テストを追加します
  （ネットワークもデバイスも不要で、高速な Linux ゲートで走ります）。シリアライズされた `ant` の
  呼び出しと、解析後のレスポンス形状の両方をアサートします。既存の Bedrock アダプタのテストと
  同じ前例に従います。

### Prime directive との整合

新しいプロバイダは、既存の Tier-1 の AI ファクトリを通してのみ到達します。新しい呼び出し箇所を
増やすことはなく、`run` / CI ゲートには一切触れません。リダクション
（[BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) /
`redaction.py`）は、どの呼び出し側が `.messages.create(...)` を呼ぶより前にもすでに走っているため、
`ant-cli` プロバイダにも自動的に適用されます。新しいリダクションのロジックは必要ありません。
プロバイダの選択は設定（`targets.<name>.ai` / `defaults.ai`）のままであり、ドライバとランナーは
アプリ非依存を保ちます。決定性についても変わるところはありません。

## 検討した代替案

- **[BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md) の
  プロバイダレジストリを待ち、`ant-cli` をその最初の後続アダプタとして着地させる**：長期的には
  こちらのほうが見通しがよく（ファクトリの分岐を増やすのではなく、登録されたアダプタになります）、
  不採用にしたのは、BE-0104 自体がまだ着手時期の決まっていない未実装の設計提案だからです。小さく
  自己完結した認証まわりの改善を、より大きなリファクタリングの完了待ちにする価値はないと判断しました。
  本項目は意図的に、現在の `provider: anthropic | bedrock | ant-cli` というファクトリへ直接
  着地させます。将来 BE-0104 がレジストリを出荷したときに、このアダプタをその背後へ移す作業は
  小さく独立した後続作業になります（アダプタの内部、すなわち棚卸しされたリクエスト / レスポンスの
  サーフェスは、おおむねそのまま持ち越せます）。
- **代わりに `record --agent claude-code`（Claude Code CLI の経路）を拡張する**：本項目の目的に
  対しては不採用としました。あの経路はサブスクリプション課金かつテキストのみとして設計されており、
  残り 5 つの AI 経路まで広げるには、コーディングエージェントの print モードを前提にした経路へ
  画像入力を足すことになります。これは Messages API をすでにフルに話せる `ant` を包む本項目より、
  実質的に大きな作業です。認証情報の取り扱いではなくコスト（サブスクリプションの枠）を目的とする
  利用者にとっては、引き続き別の有効な選択肢として残り、本項目の影響を受けません。
- **`ant` の認証情報ストアから OAuth のベアラートークンを取り出し、`anthropic` の Python SDK に
  直接渡す**（例えば `ant auth print-credentials --access-token` 経由）。呼び出しごとにシェルアウト
  する代わりのこの方式は不採用としました。トークンの更新と失効の管理を bajutsu 側で持つ必要があり、
  `ant` がすでに持っているロジックを重複させてしまうためです。呼び出しごとに `ant messages create`
  へシェルアウトするやり方は、Anthropic が Claude Code 自身による `ant` の使い方として文書化して
  いるのと同じパターンであり、トークンのライフサイクルを `ant` バイナリの内側に完全に留めておけます。
- **何もしない（`anthropic` と `bedrock` だけを保つ）**：不採用としました。ローカル開発において、
  ゼロコンフィグで使える選択肢が静的な API キーだけのまま残ってしまいます。これはまさに本項目が
  取り除こうとしている摩擦そのものです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 属性の棚卸し（6 つの AI 呼び出し側が使う `anthropic` SDK のリクエスト / レスポンスの正確なサーフェス）
- [ ] `ant` バックエンドのアダプタ（`make_client()` の `provider: ant-cli` 分岐。subprocess で支える `.messages.create(...)` のシム）
- [ ] fail closed な認証チェック（`ant` が `PATH` 上にあること、かつ `ANTHROPIC_API_KEY` または `ant auth status`）
- [ ] 設定、ドキュメント（英語 / 日本語）、subprocess をモックしたテスト

## 参考

`bajutsu/anthropic_client.py`（`make_client` / `resolve_model` / `credential_gap` / `provider` /
`AiConfig`。本項目が拡張するシーム）、6 つの AI 呼び出し側 `bajutsu/claude_agent.py` ·
`bajutsu/claude_triage.py` · `bajutsu/alerts.py` · `bajutsu/claude_enrich_agent.py` ·
`bajutsu/crawl_guide.py` · `bajutsu/crawl_tabs.py`、`bajutsu/claude_code_agent.py`
（`record` 向けの、既存の補完的な Claude Code CLI / サブスクリプション経路）、
`bajutsu/redaction.py`（このプロバイダがそのまま引き継ぐ保証）、`.env.example`
（現在の `ANTHROPIC_API_KEY` のみのローカル設定を文書化しています）、
[BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)
（プロバイダ非依存でリダクション済み、fail closed な AI 経路）、
[BE-0053](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider-ja.md)
（呼び出し側に手を入れずプロバイダを追加した前例）、
[BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)
（このアダプタが将来移行しうるレジストリ）、そして Anthropic の `ant` CLI のドキュメント
（[Quickstart](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/quickstart)、
[Authentication](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/authentication)、
[Using the CLI](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/using)）。
