[English](../../roadmap/BE-0001-m1-deterministic-runner.md) · **日本語**

# BE-0001 — 決定的ランナー（M1）

* 提案: [BE-0001](BE-0001-m1-deterministic-runner.md)
* 状態: **実装済み**
* トラック: [可決済み](README.md#可決済み)
* トピック: マイルストーン（M1–M4）

## はじめに

決定的な Tier 2 ランナー: 環境（simctl）+ ドライバ + シナリオ + アサーション + 軽量証跡 + manifest + アプリ別 config を `run` / `doctor` の背後に配線。

## 動機

テストの合否は再現可能で AI 非依存でなければならない。M1 は他のすべてが乗る決定的な背骨を確立する: 安定したセレクタ解決、条件ベースの待機（固定 sleep なし）、run ごとのクリーンな環境。

## 詳細設計

決定性コアは、ドライバ抽象 + セレクタ解決（0/1/2+ 一致の扱い）、simctl 環境レイヤ、厳密検証付きシナリオスキーマ、機械チェック可能なアサーション評価、manifest レポータ —— インメモリの fake driver で動作確認し、idb backend 経由で実機検証済み（`cross_backend.yaml` が id ファーストで実機をパスし、対象アプリは config だけで切替可能）。

## 検討した代替案

AI を実行ループに入れる方式ではなく決定性ファーストを選んだ背景は [`DESIGN.md`](../../../DESIGN.md) を参照。

## 参考

[DESIGN §2 / §3](../../../DESIGN.md)、[architecture.md](../architecture.md)、`bajutsu/orchestrator.py`、`bajutsu/drivers/base.py`
