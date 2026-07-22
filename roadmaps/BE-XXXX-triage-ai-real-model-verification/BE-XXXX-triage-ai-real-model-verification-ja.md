[English](BE-XXXX-triage-ai-real-model-verification.md) · **日本語**

# BE-XXXX — triage --ai 診断経路の実モデル検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-triage-ai-real-model-verification-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | 自己修復トリアージ |
<!-- /BE-METADATA -->

## はじめに

`triage --ai` の自己修復経路（`agents/claude_triage.py`）は、失敗した run の
[証跡](../../docs/ja/glossary.md#証跡-capturepolicy-trace-triage)（失敗時の
スクリーンショットを含む）を Claude へ送り、そのレスポンスを構造化された診断と、該当する場合は
提案される修正（`renameId` / `addIndex` / `raiseTimeout`）へパースします。このパース処理に触れる
テストはすべて `FakeBackend(FakeBlock(...))` で駆動されています。CLI レベルのテストはさらに一歩
進んで、triage のエージェントクラス自体を、固定の `Triage` オブジェクトを返すだけの手組みの
`_FakeAgent` に置き換え、AI backend を完全に迂回しています。本項目は、この診断パース処理自体に
対する実モデル検証を追加します。

## 動機

`tests/test_claude_triage.py` の fake は、作者が「診断レスポンスはこう見えるはずだ」と想定する
形にきっちり整形されています。`tests/test_triage.py` の `_stub_ai_cli`（CLI の `--ai` テストを
支える）はさらに踏み込み、`ClaudeCrossRunTriageAgent` を `_FakeAgent` に差し替えるため、認証情報
欠落時の経路（`_require_ai_credential`）すら実行されず、スタブに置き換えられています。実モデルが
実際の失敗スクリーンショットと実際の run の証跡を見て、`Triage` スキーマへ実際にパースできる
診断 JSON を生成すること、あるいは提案される修正のカテゴリ列挙が実モデルに提示された選択肢と
一致することを、テストスイートのどこも確認していません。

triage は設計上あくまで advisory（助言）であり、`--apply`/`--write` は常に差分をプレビューして
から人間がその結果をレビューします（`DESIGN.md` のロードマップにおける M4。prime directive 1に
より、これは `run` ゲートから完全に切り離されています）。この advisory という位置づけこそが、
このギャップを放置せず埋めるべき理由です。パーサが実モデルの修正提案を静かに落とす、あるいは
カテゴリを誤ってマッピングすれば、人間がレビューできる形のまさしく型付けされた提案を出すという、
この AI 機能の価値そのものが損なわれます。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **実際の診断レスポンスをフィクスチャとして捕捉する**：本物のショーケース run を実際に失敗させて
  `triage --ai` を実行し、実際のレスポンスペイロードを保存し、既存の fake-backend テストもパース
  できるフィクスチャとして追加します。
- **API キーで gate したライブ smoke テスト**：実際の失敗した run に対して実際の認証情報で
  `triage --ai` をエンドツーエンドで実行し、結果が妥当な `Triage` オブジェクトへパースでき、
  （修正が提案される場合は）その修正が整形式であることを検証する `pytest.mark.skipif` gated の
  テストを追加します。
- **認証情報が欠落したときの経路を、代役のエージェントクラスではなく実際に検証する**：`_stub_ai_cli` は
  エージェントクラスの差し替えとは別に `_require_ai_credential` 自体も monkeypatch するため、
  `--ai` の実際の認証情報チェックはそのテストでは一度も実行されません。実際のギャップ検出コード
  経路を駆動する専用のテストを別途維持します。
- **triage の advisory という位置づけは変えない**：本項目が検証するのは AI の出力がまさしく
  パースできることだけであり、`--apply`/`--write` の「差分プレビュー→人間レビュー」という流れも、
  決定的な判定経路へのモデル呼び出しの追加も一切行いません。

## 検討した代替案

- **診断スキーマが文書化されていることを根拠に、fake-backend のテストを信頼する**：スキーマが
  文書化されていても、スクリーンショットに対する実モデルの自由形式な推論が、常にそのスキーマへ
  準拠する出力を生む保証にはなりません。これはまさに実モデル検証が捕まえるべきリスクです。
- **triage は advisory でどのみち人間がレビューするので、実検証を省く**：人間が差分をレビュー
  するには、その差分がそもそも存在し、整形式であることが前提です。静かに落とされた、あるいは
  不正な形の提案は、そのレビュー工程にすら届きません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 実際の `triage --ai` 診断レスポンスを回帰用フィクスチャとして捕捉します。
- [ ] 実際に失敗した run に対して `triage --ai` をエンドツーエンドで実行する、API キーで gate した
  ライブ smoke テストを追加します。
- [ ] 代役のエージェントクラスではなく、実際の認証情報欠落チェックを検証する専用テストを追加します。
- [ ] triage の advisory という位置づけが変わっていないことを確認します（検証するのは出力の
  パースのみ）。

## 参考

- [BE-0104 — ベンダー中立な AI バックエンドインターフェース](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/agents/claude_triage.py`、`bajutsu/triage.py`、`tests/conftest.py`
  （`FakeBackend` / `FakeBlock`）、`tests/test_claude_triage.py`、`tests/test_triage.py`
  （`_stub_ai_cli`、`_FakeAgent`）
