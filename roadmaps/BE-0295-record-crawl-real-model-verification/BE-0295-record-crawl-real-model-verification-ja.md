[English](BE-0295-record-crawl-real-model-verification.md) · **日本語**

# BE-0295 — record と crawl の propose ループに対する実モデル検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0295](BE-0295-record-crawl-real-model-verification-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0295") |
| 実装 PR | _pending_ |
| トピック | オーサリング体験 |
| 関連 | [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)、[BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`record` の observe → propose → execute ループ(`agents/claude.py` の `ClaudeAgent.next_action`)と、
`crawl` の自律探索(`crawl/guide.py`、`crawl/tabs.py`)は、いずれも実モデルが返す構造化された
tool-use の提案を Bajutsu 自身のアクションスキーマへパースする処理に依存しています。この
パース処理に触れるテストはすべて、`ClaudeAgent` や crawl のエージェントを
`FakeBackend(FakeBlock(...))` で構築しています。これはテスト作者が期待する形にきっちり整形された
レスポンスであり、実モデルが実際に生成したレスポンスではありません。本項目は、このパース処理
自体に対する実モデル検証を追加します。[PR #1233](https://github.com/bajutsu-e2e/bajutsu/pull/1233)
で提案する transport 水準のアダプタ検証とは異なり、それを補完する位置づけです。

## 動機

[PR #1233](https://github.com/bajutsu-e2e/bajutsu/pull/1233) の提案は「アダプタは実サービスとそもそも往復できるのか」という transport の
問いを扱います。本項目が扱うのは別の問いで、「本物の record/crawl プロンプトを与えたとき、実
モデルのレスポンスが propose ループのアクションスキーマへ実際にパースできるのか」という、
`record` と `crawl` が使う具体的なプロンプトとスキーマに関する意味論的な問いです。これは手組みの
`FakeBlock` では構造上どうしても再現できません。`tests/test_crawl_lanes.py` と `tests/test_crawl.py`
は実際の認証情報にもっとも近づいていますが、やっていることは逆です。`ANTHROPIC_API_KEY` を削除して
認証情報が欠落したときのエラーメッセージを検証するだけで、キーを渡して実際の提案経路を検証することは
ありません。`record` や `crawl` を実デバイスに対して実際の API キーで走らせる CI ジョブは
どこにも存在しません。

このギャップは、一般的な AI 呼び出しよりも重い意味を持ちます。`record` の出力は
[シナリオファイル](../../docs/ja/glossary.md#シナリオのオーサリング)そのものであり、いったん書き込まれれば決定的な `run` ゲートが完全に信頼する唯一の成果物だから
です(prime directive 1：AI は著者であって判定者ではない)。実モデルが出す提案をパーサが静かに
落とす、あるいは誤った形にマッピングしてしまえば、その信頼される成果物の質が劣化しますが、
現行のテストスイートはそれに一切気付きません。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **実レスポンスを一度捕捉し、回帰用フィクスチャとして再生する**：ショーケースの画面に対して
  `record` の propose ステップと `crawl` のナビゲーションステップを実モデルに対して実行し、
  生の応答ペイロードを保存し、既存の fake-backend テストもパースできるフィクスチャとして
  追加します。これにより、捕捉した実際の形が一度きりの確認ではなく、恒久的な回帰スイートの
  一部になります。
- **各ループに API キーで gate したライブ smoke テストを追加する**：再生用の
  フィクスチャに加えて、`record` / `crawl` それぞれについて、実際のショーケース画面に対して
  実際の認証情報で propose/navigate ステップを実行し、結果が妥当なアクションへパースできることを
  検証する `pytest.mark.skipif` gated のテストを1つずつ追加します。
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  と同じ、まずゲート対象外のシグナルとして着地させる前例に従います。
- **認証情報が欠落したときのテストはそのまま残す**：`tests/test_crawl_lanes.py` と `tests/test_crawl.py`
  は、キー欠落時のエラー経路をすでに正しくカバーしています。本項目はこれらを置き換えるのではなく、
  キーが存在する場合の経路をその隣に追加します。
- **判定の境界には手を入れない**：どちらの追加も、AI が*著者として*生成した出力がまさしくパース
  できることを検証するだけで、`run` の決定的な判定にモデル呼び出しを近づけるものではありません
  (prime directive 1)。新規テストは、`record`/`crawl` がすでに占めている periphery のテスト範囲を
  出ません。

## 検討した代替案

- **スキーマ自体が文書化されていることを根拠に、fake-backend のテストで十分とする**：スキーマが
  文書化されていても、実モデルの出力が実際にそれへ準拠する保証にはなりません。プロンプトの
  ドリフト、モデルの更新、実際のショーケース画面の要素ツリーに含まれるエッジケースは、いずれも
  fake が想定していないレスポンスを生みえます。
- **ライブ smoke テストだけを追加し、フィクスチャの捕捉は省く**：smoke テスト単体でも、その時点
  で経路が動くことは証明できますが、将来の回帰のための成果物が残りません。捕捉したフィクスチャ
  があれば、ライブ実行の合間でも実際の形の検証を続けられます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 実際の `record` propose ステップのレスポンスを回帰用フィクスチャとして捕捉する。
- [ ] 実際の `crawl` ナビゲーションステップのレスポンスを回帰用フィクスチャとして捕捉する。
- [x] `record` の propose ループ向けに、API キーで gate したライブ smoke テストを追加する。
- [x] `crawl` のナビゲーションループ向けに、API キーで gate したライブ smoke テストを追加する。

**ログ**

- API キーで gate したライブ smoke テスト 2 本を、signal-first の方針（BE-0282 の前例）で先に着地
  させました。`tests/test_real_model_smoke.py` は、コミット済みのショーケース golden 画面に対して
  `ClaudeAgent.next_action` と `ClaudeActionProposer.propose` を実モデルで実行し（Simulator は不要で、
  ライブなのはモデル呼び出しだけです）、実レスポンスが propose ループのアクションスキーマへパース
  できることを検証します。AI の認証情報が未設定のときはスキップするので、ゲートは密閉のままです。
  同じファイルの決定的な `FakeBackend` 自己検証テストがローダーと妥当性アサーションの健全さを保つ
  ため、ライブ実行は実際に検証を行います。フィクスチャ捕捉の 2 単位は、レスポンスを記録するのに実
  認証情報が要るため残します。

## 参考

- [BE-0104 — ベンダー中立な AI バックエンドインターフェース](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- [PR #1233 — AI backend アダプタの実 API 契約 smoke レーン](https://github.com/bajutsu-e2e/bajutsu/pull/1233)
- `bajutsu/agents/claude.py`、`bajutsu/crawl/guide.py`、`bajutsu/crawl/tabs.py`、
  `tests/conftest.py`(`FakeBackend` / `FakeBlock`)、`tests/test_claude_agent.py`、
  `tests/test_crawl_guide.py`、`tests/test_crawl_tabs.py`、`tests/test_crawl_lanes.py`、
  `tests/test_crawl.py`
