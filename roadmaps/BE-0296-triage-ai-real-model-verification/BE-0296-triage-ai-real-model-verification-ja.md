[English](BE-0296-triage-ai-real-model-verification.md) · **日本語**

# BE-0296 — triage --ai 診断経路の実モデル検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0296](BE-0296-triage-ai-real-model-verification-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0296") |
| 実装 PR | [#1364](https://github.com/bajutsu-e2e/bajutsu/pull/1364) |
| トピック | 自己修復トリアージ |
<!-- /BE-METADATA -->

## はじめに

`triage --ai` の自己修復経路（`agents/claude_triage.py`）は、失敗した run の
[証跡](../../docs/ja/glossary.md#証跡-capturepolicy-trace-triage)を Claude へ送ります。証跡には失敗時の
スクリーンショットが含まれます。経路はレスポンスを、構造化された診断と、該当する場合は提案される修正
（`renameId` / `addIndex` / `raiseTimeout`）へパースします。

このパース処理に触れるテストは、すべて `FakeBackend(FakeBlock(...))` で駆動されています。CLI レベルの
テストはさらに進み、triage のエージェントクラス自体を手組みの `_FakeAgent` に置き換えています。
`_FakeAgent` は固定の `Triage` オブジェクトを返すだけなので、AI backend は一度も動きません。本項目は、
この診断パース処理自体に対する実モデル検証を追加します。

## 動機

`tests/test_claude_triage.py` の fake は、診断レスポンスはこう見えるはずだと作者が想定した形に、
きっちり整形されています。`tests/test_triage.py` の `_stub_ai_cli` は、CLI の `--ai` テストを支えます。
さらに踏み込み、`ClaudeCrossRunTriageAgent` を `_FakeAgent` に差し替えます。
認証情報が欠けたときの経路（`_require_ai_credential`）も、実行されずにスタブへ置き換わっています。

実モデルが実際の失敗スクリーンショットと実際の run の証跡を見たとき、次の2点をテストスイートは
どこも確認していません。1つは、診断 JSON が `Triage` スキーマへ実際にパースできること。もう1つは、
提案される修正のカテゴリ列挙が、実モデルに提示された選択肢と一致することです。

triage は設計上あくまで advisory（助言）です。`--apply`/`--write` は、シナリオを書き換える前に必ず
差分をプレビューし、人間がその結果をレビューします（`DESIGN.md` のロードマップにおける M4）。
prime directive 1 により、triage は `run` ゲートから完全に切り離されています。
この advisory という位置づけこそが、ギャップを放置せず埋めるべき理由です。パーサが実モデルの修正提案を静かに落とす、あるいは
カテゴリを誤ってマッピングすれば、人間がレビューできる型付けされた提案を出すという、この AI 機能の
価値そのものが損なわれます。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **実際の診断レスポンスをフィクスチャとして捕捉する**：本物のショーケース run を実際に失敗させて
  `triage --ai` を実行し、実際のレスポンスペイロードを保存します。既存の fake-backend テストもパース
  できるフィクスチャとして追加します。
- **API キーで gate したライブ smoke テスト**：`pytest.mark.skipif` で gate したテストを追加します。
  このテストは、実際の失敗した run に対して実際の認証情報で `triage --ai` をエンドツーエンドで実行
  します。結果が妥当な `Triage` オブジェクトへパースでき、修正が提案される場合はその修正が整形式で
  あることを検証します。
- **認証情報が欠落したときの経路を、代役のエージェントクラスではなく実際に検証する**：
  `_stub_ai_cli` は、エージェントクラスの差し替えに加えて `_require_ai_credential` 自体も
  monkeypatch します。そのため `--ai` の実際の認証情報チェックは、このテストでは一度も実行されません。
  実際のギャップ検出コード経路を駆動する専用のテストを、別途維持します。
- **triage の advisory という位置づけは変えない**：本項目が検証するのは、AI の出力がパースできる
  ことです。`--apply`/`--write` の「差分プレビュー、人間レビュー」という流れは変えません。
  決定的な判定経路にモデル呼び出しを加えることもしません。

## 検討した代替案

- **診断スキーマが文書化されていることを根拠に、fake-backend のテストを信頼する**：
  スキーマが文書化されていても、スクリーンショットに対する実モデルの自由形式な推論が、
  そのスキーマに準拠する出力を常に生む保証にはなりません。
  実モデル検証が捕まえるべきリスクは、まさにこれです。
- **triage は advisory でどのみち人間がレビューするので、実検証を省く**：人間が差分をレビューするには、
  その差分が存在し、整形式であることが前提です。静かに落とされた提案や不正な形の提案は、
  レビュー工程にすら届きません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 実際の `triage --ai` 診断レスポンスを回帰用フィクスチャとして捕捉します。
- [x] 実際に失敗した run に対して `triage --ai` をエンドツーエンドで実行する、API キーで gate した
  ライブ smoke テストを追加します。
- [x] 代役のエージェントクラスではなく、実際の認証情報欠落チェックを検証する専用テストを追加します。
- [x] triage の advisory という位置づけが変わっていないことを確認します（検証するのは出力の
  パースのみ）。

## 参考

- [BE-0104 — ベンダー中立な AI バックエンドインターフェース](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/agents/claude_triage.py`、`bajutsu/triage.py`、`tests/conftest.py`
  （`FakeBackend` / `FakeBlock`）、`tests/test_claude_triage.py`、`tests/test_triage.py`
  （`_stub_ai_cli`、`_FakeAgent`）
