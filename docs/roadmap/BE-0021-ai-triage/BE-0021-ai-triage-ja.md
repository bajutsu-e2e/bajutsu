[English](BE-0021-ai-triage.md) · **日本語**

# BE-0021 — AI triage（原因要約・修正提案）

* 提案: [BE-0021](BE-0021-ai-triage-ja.md)
* 状態: **実装済み**
* トラック: [可決済み](../README-ja.md#可決済み)
* トピック: 自己修復トリアージ（M4）

## はじめに

失敗証跡を AI が読み、原因要約・修正提案を出す（人間レビュー前提）。`bajutsu triage`（ルールベース）＋ `--ai`（Claude・失敗スクショ込み）。決定的な `trace` コマンドはその下の層。

## 動機

TBD。

## 詳細設計

実装は `bajutsu/triage.py`・`bajutsu/claude_triage.py` を参照。

## 検討した代替案

TBD。

## 参考

[DESIGN §3.1 / §12](../../../DESIGN.md)、`bajutsu/triage.py`・`bajutsu/claude_triage.py`
