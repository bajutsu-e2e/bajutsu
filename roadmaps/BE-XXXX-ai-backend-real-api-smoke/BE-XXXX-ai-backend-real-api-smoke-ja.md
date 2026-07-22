[English](BE-XXXX-ai-backend-real-api-smoke.md) · **日本語**

# BE-XXXX — AI バックエンド アダプタ向けの実 API 契約 smoke レーン

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ai-backend-real-api-smoke-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | AI プロバイダ設定 |
<!-- /BE-METADATA -->

## はじめに

ベンダー中立な AI バックエンド
（[BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)）に触れる
テスト、つまり Anthropic API への直接アダプタ、Amazon Bedrock アダプタ、`ant` CLI アダプタ
（[BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md)）に触れるテストは
すべて、手書きの代役（`tests/conftest.py` の `FakeAnthropic` / `FakeBlock`）を経由するだけで、実際の
サービスを一度も通りません。テストスイートと CI のどちらにも、Bajutsu 自身のアダプタコードを通して
Anthropic API / Bedrock / `ant` CLI に実際の呼び出しを完了させるものは1つもありません。本項目は、
opt-in で API キーによりゲートされた、ゲート対象外の smoke レーンを1つ追加し、実際のレスポンスをベンダー中立な
リクエスト/レスポンス契約へアダプタが正しく変換できることを、モデルを `run` の判定に一切近づける
ことなく証明します。

## 動機

これらの fake は、作者が「実サービスはこう返すはずだ」と信じている内容とは内部的に整合しています。
`FakeBlock` は `.type = "tool_use"` を無条件に設定し、手組みのメッセージオブジェクトが
`client.messages.create(...)` の実際の返り値の代役を務めます。しかしこの思い込みを実 API と
突き合わせるものは何もありません。実際のレスポンスは、fake が構造上どうしても表現できない形で
異なり得ます。例えば `tool_use` の途中で `stop_reason` が `"max_tokens"` になって届く場合や、
`tool_choice` を API が黙って無視する場合です。ほかにも、`cache_control: ephemeral` のブロックを
サービス側が拒否する場合や、Bedrock/`ant` アダプタが直接 API 経路の想定とは異なる形のレスポンスを
返す場合があります。
`test_make_client_bedrock` はこのギャップを端的に示しています。偽の AWS 認証情報で
`isinstance(client, AnthropicBedrock)` を検証するだけで、`.messages.create` を一度も呼び出しません。

本項目は prime directive 1 を緩めろという提案ではありません。`run` / CI の判定はモデル呼び出しから
無縁であり続けるべきで、本項目はその経路に一切触れません。`ai/anthropic.py`、
`agents/anthropic_client.py`、`ai/registry.py` はいずれも periphery で、AI extra の背後にあり、
決定的コアはこれらを import しません。欠けているのは、periphery 自身が包むベンダーとの契約に対する
カバレッジであり、しかももっとも安価な水準で足ります。実呼び出しはトランスポートとスキーマの検証で
あって意味論的な検証ではないため、わずかなトークン数で済む最小限のプロンプトで配線を証明できます。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **API キーでゲートした最小限のライブ呼び出しテスト。契約のみを検証する**：`AnthropicBackend` に
  対して些細なプロンプトと強制的な `tool_choice` を渡すテストを追加します。`ANTHROPIC_API_KEY`
  （または Bedrock / `ant` 相当の認証情報）に加えて、専用の opt-in フラグ（例：
  `BAJUTSU_LIVE_AI_SMOKE=1`）でも `pytest.mark.skipif` によりゲートします。キーの有無だけを
  条件にするのは安全な gate にはなりません。`record`/`triage` のためにすでにキーを export している
  contributor のセッション（`CLAUDE.md` が前提とする環境）では、通常の `make check` で実際に
  課金される呼び出しが走ってしまうためです。このテストは、アダプタが正規化した
  `MessageResponse` / `ToolUseBlock` の形が空でなく返り、
  パースできることだけを確認します。モデルが何を選んで話したかや品質は問わず、あくまで配線の契約検証に
  とどめます。
- **アダプタごとに1つ、opt-in かつゲート対象外の CI レーン**：直接 API / Bedrock / `ant` それぞれに
  対して、リポジトリの secrets から実際の認証情報を渡してライブ呼び出しテストを実行するワークフロー
  ジョブを用意します。
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  と同じ、まずゲート対象外のシグナルとして着地させる前例に従います。
- **カバーしきれないものは明示する**：ライブの Bedrock ロールのように、現実的に CI で用意できない
  認証情報もあります。レーンを用意できない箇所は、カバー済みであるかのように扱わず、本項目の
  「進捗」ログに明示します。

## 検討した代替案

- **実際の呼び出しを一度録画した VCR 形式のカセットを使う**：繰り返し実行する分には安価で決定的
  ですが、一度録画したカセットは、現行の手書き fake とまったく同じやり方で陳腐化します。つまり
  アダプタが実 API を二度と観測しなくなります。定期的なライブ smoke レーンだけが、現実を観測し
  続ける設計です。
- **Anthropic 自身の SDK テストスイートに配線の契約検証を任せる**：SDK 自身のテストは SDK 自体を
  カバーするものであり、Bajutsu 側のアダプタコードが実際のレスポンスを自前の `MessageResponse` /
  `ToolUseBlock` 型へ正しく変換できているかどうかについては何も語りません。これこそが本項目の
  対象とするギャップです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 直接 Anthropic API アダプタ向けに、API キーでゲートした最小限のライブ呼び出しテストを追加する。
- [ ] Bedrock アダプタにも同様のテストを追加する。困難な場合はその理由を明示する。
- [ ] `ant` CLI アダプタにも同様のテストを追加する。困難な場合はその理由を明示する。
- [ ] アダプタごとに opt-in かつゲート対象外の CI レーンを組み込む。

## 参考

- [BE-0104 — ベンダー中立な AI バックエンドインターフェース](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)
- [BE-0163 — Claude Code CLI バックエンドを `ant` CLI の OAuth プロバイダに置き換える](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/ai/anthropic.py`、`bajutsu/agents/anthropic_client.py`、`bajutsu/ai/registry.py`、
  `tests/conftest.py`（`FakeAnthropic` / `FakeBlock`）、`tests/test_ai_anthropic_adapter.py`、
  `tests/test_anthropic_client.py`、`tests/test_ai_backend.py`
