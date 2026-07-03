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
| トピック | マイルストーン（M1–M4） |
<!-- /BE-METADATA -->

## はじめに

決定的な Tier 2 ランナーです。環境（simctl）、ドライバ、シナリオ、アサーション、軽量な証跡、manifest、アプリ別 config を `run` / `doctor` の背後に配線します。

## 動機

テストの合否は再現可能で、AI に依存しないものでなければなりません。M1 は、他のすべてのコンポーネントが土台とする決定的な基盤を確立します。すなわち、安定したセレクタ解決、条件ベースの待機（固定 sleep なし）、run ごとのクリーンな環境です。

## 詳細設計

決定性コアは、ドライバ抽象とセレクタ解決（0 / 1 / 2 個以上の一致の扱い）、simctl の環境レイヤ、厳密な検証を備えたシナリオスキーマ、機械チェック可能なアサーション評価、manifest レポータで構成します。インメモリの fake driver で動作を確認し、idb backend 経由で実機でも検証済みです（`cross_backend.yaml` シナリオが id を優先して実機をパスし、対象アプリは config だけで切り替えられます）。

## 検討した代替案

AI を実行ループに入れる方式ではなく決定性を優先した背景は、[`DESIGN.md`](../../../DESIGN.md) を参照してください。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[DESIGN §2 / §3](../../../DESIGN.md)、[architecture.md](../../../docs/ja/architecture.md)、`bajutsu/orchestrator.py`、`bajutsu/drivers/base.py`
