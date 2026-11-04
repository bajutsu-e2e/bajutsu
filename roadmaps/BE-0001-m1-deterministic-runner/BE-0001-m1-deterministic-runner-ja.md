[English](BE-0001-m1-deterministic-runner.md) · **日本語**

# BE-0001 — 決定的ランナー（M1）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0001](BE-0001-m1-deterministic-runner-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0001") |
| 実装 PR | PR 単位の履歴より前（初期インポートにスカッシュ済みで、単一 PR なし） |
| トピック | 検証とカバレッジ |
<!-- /BE-METADATA -->

## はじめに

マイルストーン 1（M1）では、決定的な [Tier 2](../../docs/ja/glossary.md#2-つの層) ランナーを提供します。環境（simctl）、ドライバ、シナリオ、アサーション、軽量な証跡、manifest、アプリ別 config を、`run` と `doctor` の背後に配線します。

## 動機

テストの合否は再現可能で、AI に依存しないものでなければなりません。M1 は、他のすべてのコンポーネントが土台とする決定的な基盤を確立します。基盤は 3 つで構成します。安定したセレクタ解決、条件ベースの待機（固定 sleep なし）、run ごとのクリーンな環境です。

## 詳細設計

決定性コアは、次の 5 つで構成します。

- ドライバ抽象とセレクタ解決（一致が 0 個、1 個、2 個以上の場合の扱い）
- simctl の環境レイヤ
- 厳密な検証を備えたシナリオスキーマ
- 機械チェック可能なアサーション評価
- manifest レポータ

インメモリの fake driver で、コアの動作を確認します。idb backend 経由で実機でも検証済みです。`cross_backend.yaml` シナリオは id を優先して実機をパスし、対象アプリは config だけで切り替えられます。

## 検討した代替案

AI を実行ループに入れる方式より決定性を優先した背景は、[`DESIGN.md`](../../DESIGN.md) に記載しています。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[DESIGN §2 / §3](../../DESIGN.md)、[architecture.md](../../docs/ja/architecture.md)、`bajutsu/orchestrator.py`、`bajutsu/drivers/base.py`
