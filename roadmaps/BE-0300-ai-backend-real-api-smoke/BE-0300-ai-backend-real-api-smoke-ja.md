[English](BE-0300-ai-backend-real-api-smoke.md) · **日本語**

# BE-0300 — AI バックエンドアダプタ向けの実 API 契約スモークレーン

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0300](BE-0300-ai-backend-real-api-smoke-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0300") |
| 実装 PR | [#1348](https://github.com/bajutsu-e2e/bajutsu/pull/1348) |
| トピック | AI プロバイダ設定 |
<!-- /BE-METADATA -->

## はじめに

ベンダー中立な AI バックエンド（[BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)）には、
3つのアダプタがあります。Anthropic API への直接アダプタ、Amazon Bedrock アダプタ、`ant` CLI アダプタ
（[BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md)）です。これらに触れる
テストはすべて、手書きの代役（`tests/conftest.py` の `FakeAnthropic` / `FakeBlock`）を経由します。実際の
サービスを通るテストは1つもありません。

テストスイートにも CI にも、Bajutsu 自身のアダプタコードを通して Anthropic API、Bedrock、`ant` CLI への
実際の呼び出しを完了させるものはありません。本項目は、その呼び出しを行うスモークレーンを1つ追加します。
このレーンは opt-in で、API キーによりゲートされ、ゲート対象外です。実際のレスポンスをベンダー中立な
リクエスト/レスポンス契約へアダプタが正しく変換できることを証明し、モデルを `run` の判定には近づけません。

## 動機

これらの fake は、作者が「実サービスはこう返すはずだ」と信じている内容に合わせてあります。
`FakeBlock` は `.type = "tool_use"` を無条件に設定し、手組みのメッセージオブジェクトが
`client.messages.create(...)` の実際の返り値の代役を務めます。この思い込みを実 API と突き合わせるものは、
何もありません。

実際のレスポンスは、fake が構造上表現できない形で異なり得ます。

- `tool_use` の途中で `stop_reason` が `"max_tokens"` になって届く。
- API が `tool_choice` を黙って無視する。
- サービス側が `cache_control: ephemeral` のブロックを拒否する。
- Bedrock や `ant` のアダプタが、直接 API 経路の想定とは異なる形のレスポンスを返す。

`test_make_client_bedrock` は、このギャップを端的に示しています。偽の AWS 認証情報で
`isinstance(client, AnthropicBedrock)` を検証するだけで、`.messages.create` を一度も呼び出しません。

本項目は prime directive 1 を緩めるものではありません。`run` / CI の判定はモデル呼び出しから無縁であり
続けるべきで、本項目はその経路に一切触れません。`ai/anthropic.py`、`agents/anthropic_client.py`、
`ai/registry.py` はいずれも periphery で、AI extra の背後にあります。決定的コアは、これらを import
しません。

欠けているのは、periphery 自身が包むベンダーとの契約に対するカバレッジです。しかも、もっとも安価な水準で
足ります。実呼び出しが確かめるのはトランスポートとスキーマであり、意味論ではないからです。わずかな
トークン数で済む最小限のプロンプトで、配線を証明できます。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **API キーでゲートした最小限のライブ呼び出しテスト。契約のみを検証する**：`AnthropicBackend` に対して、
  些細なプロンプトと強制的な `tool_choice` を渡すテストを追加します。
  - `ANTHROPIC_API_KEY`（または Bedrock / `ant` 相当の認証情報）に加えて、専用の opt-in フラグ
    （例：`BAJUTSU_LIVE_AI_SMOKE=1`）でも `pytest.mark.skipif` によりゲートします。
  - キーの有無だけを条件にするのは、安全なゲートになりません。contributor のセッションは、`record`
    （`CLAUDE.md` が前提とする環境）や `triage --ai`（`bajutsu/cli/commands/triage.py`）のために、すでに
    キーを export しています。通常の `make check` で、課金される実際の呼び出しが走ってしまいます。
  - テストが確認するのは、アダプタが正規化した `MessageResponse` / `ToolUseBlock` の形が空でなく返り、
    パースできることだけです。モデルが何を選んで話したかは問いません。配線の契約検証にとどまり、モデルの
    品質判定にはなりません。
- **アダプタごとに1つ、opt-in かつゲート対象外の CI レーン**：直接 API、Bedrock、`ant` のそれぞれに
  ワークフロージョブを用意します。ジョブはリポジトリの secrets から実際の認証情報を受け取り、ライブ
  呼び出しテストを実行します。
  - トリガーは `workflow_dispatch` のみとし、`pull_request` では実行しません。フォークからの実行で認証情報が
    漏れてしまうためです。
  - これは、`.github/workflows/devicefarm.yml` にすでに明記されているハードな境界と同じです。
  - アダプタごとのレーン設計は、まずゲート対象外のシグナルとして着地させる
    [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
    の前例に従います。
- **カバーしきれないものは明示する**：ライブの Bedrock ロールのように、現実的に CI で用意できない認証情報も
  あります。レーンを用意できない箇所は、本項目の「進捗」ログに明示します。ギャップをカバー済みと
  見せないためです。

## 検討した代替案

- **実際の呼び出しを一度録画した VCR 形式のカセットを使う**：繰り返し実行する分には安価で決定的です。
  しかし、一度録画したカセットは、現行の手書き fake と同じように陳腐化します。アダプタが実 API を二度と
  観測しなくなるからです。現実を観測し続けられるのは、定期的なライブスモークレーンだけです。小さなもので
  も同じです。
- **Anthropic 自身の SDK テストスイートに配線の契約検証を任せる**：SDK のテストがカバーするのは SDK 自体
  です。Bajutsu 側のアダプタコードが実際のレスポンスを自前の `MessageResponse` / `ToolUseBlock` 型へ正しく
  変換できているかは、何も語りません。これこそが、本項目の対象とするギャップです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 直接 Anthropic API アダプタ向けに、API キーでゲートした最小限のライブ呼び出しテストを追加する。
- [x] Bedrock アダプタにも同様のテストを追加する。困難な場合はその理由を明示する。
- [x] `ant` CLI アダプタにも同様のテストを追加する。困難な場合はその理由を明示する。
- [x] 直接 Anthropic API アダプタ向けに opt-in かつゲート対象外の CI レーンを組み込む。Bedrock と `ant` の CI レーンは、ライブな AWS ロールや OAuth CLI のセッションを CI シークレットとして用意できないため、ログに記したとおり見送っています。

**ログ**

- `tests/test_ai_backend_live_smoke.py` は、`AnthropicBackend` を通して些細な強制ツールのプロンプトで
  実プロバイダを呼び出します。検証するのは、返り値がベンダー中立な `MessageResponse` として空でなく
  パースでき、`ToolUseBlock` を持つことだけです。トランスポートとスキーマの検証であって、モデルの品質検証
  ではありません。
  - アダプタごとに1つずつ（`api-key` / `bedrock` / `ant`）テストを置きます。
  - 各プロバイダに認証情報が無いときは、`bajutsu.ai.credential_gap` によりスキップします。
  - 同じファイルにある決定的な `FakeAnthropic` の自己検証テストが、配線契約のアサーションの健全さを保ちます。
    ライブ実行は空振りせず、実際に検証を行います。
- opt-in のゲートには、設計が例として挙げた `BAJUTSU_LIVE_AI_SMOKE` 環境変数ではなく、**`live` の
  pytest マーカー**を用います。このマーカーは既定で高速スイートから除外されます（`addopts` の
  `not live`）。contributor が `ANTHROPIC_API_KEY` を export していても、`make check` は構造として密閉の
  ままです。
  - 兄弟項目の BE-0295（[#1344](https://github.com/bajutsu-e2e/bajutsu/pull/1344)）が先にマージされました。
    BE-0295 は自身の実モデルスモークのために、同じ `live` マーカーと `addopts` 行を追加しています。
    そのため `main` には、すでにこれらが入っています。
  - 本項目は、そのマーカーを定義し直さず再利用します。実行は `-m live` で opt-in します。
- `.github/workflows/ai-smoke.yml` は、**直接 Anthropic API** のレーンだけを配線します。
  - 起動は `workflow_dispatch` のみで、push や PR では動きません。フォークからの実行が認証情報を目にする
    ことはありません。`devicefarm.yml` と同じ境界です。
  - `ai-smoke` の Environment にスコープした `ANTHROPIC_API_KEY` の secret を使い、ゲート対象外です。
  - secret が未設定ならテストはスキップし、ジョブは緑の no-op になります。運用者が接続するまで、レーンは
    休止します。
- **Bedrock と `ant` のレーンは意図的に CI へ配線していません。** ライブの Bedrock 呼び出しには AWS ロール
  （とプロバイダ接頭辞つきの `BAJUTSU_BEDROCK_MODEL`）が必要です。`ant` にはサインイン済みの OAuth CLI
  シートが必要です。いずれも現実的にはリポジトリの secret にできません。それらの `-m live` テストは、
  認証情報があるときにローカルや手動でなら実行できます。CI レーンだけを、そうした認証情報が用意できる
  まで見送ります。

## 参考

- [BE-0104 — ベンダー中立な AI バックエンドインターフェース](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)
- [BE-0163 — Claude Code CLI バックエンドを `ant` CLI の OAuth プロバイダに置き換える](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/ai/anthropic.py`、`bajutsu/agents/anthropic_client.py`、`bajutsu/ai/registry.py`、
  `tests/conftest.py`（`FakeAnthropic` / `FakeBlock`）、`tests/test_ai_anthropic_adapter.py`、
  `tests/test_anthropic_client.py`、`tests/test_ai_backend.py`
