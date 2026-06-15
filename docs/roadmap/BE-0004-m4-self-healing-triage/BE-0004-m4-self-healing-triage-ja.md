[English](BE-0004-m4-self-healing-triage.md) · **日本語**

# BE-0004 — 自己修復トリアージ（M4）

* 提案: [BE-0004](BE-0004-m4-self-healing-triage-ja.md)
* 状態: **実装済み**
* トラック: [可決済み](../README-ja.md#可決済み)
* トピック: マイルストーン（M1–M4）

## はじめに

自己修復トリアージ: `bajutsu triage` が失敗 run のコンテキストを組み立てて診断する（原因 + 修正案）—— **助言のみで、合否の判定者には決してならない**。

## 動機

回帰の保守はコストが高い。M4 は AI に失敗を調査させ最小限の修正を提案させることでコストを下げる。一方で決定性の境界は保たれる: 修正は人間が diff をレビューして opt-in したときだけ適用される。

## 詳細設計

診断は同じ `TriageAgent` プロトコル背後の 2 エージェントのいずれかで実行: ルールベースの `HeuristicTriageAgent`、または `triage --ai`（失敗スクショも読む Claude）。エージェントは構造化 fix（`renameId` / `addIndex` / `raiseTimeout`）を提案でき、`--apply` が dry-run diff、`--write` が source に適用、`--rerun` が再検証。実機で end-to-end 検証済み。

## 検討した代替案

競合は実行中に自動補正する; Bajutsu は「テストを甘くする」のを避けるため実行中の暗黙補正を意図的に拒否する（[DESIGN §11](../../../DESIGN.md)）。

## 参考

[DESIGN §3.1 / §12](../../../DESIGN.md)、[reporting.md](../../ja/reporting.md)、`bajutsu/triage.py`、[自己修復トリアージ（M4）](../README-ja.md#自己修復トリアージm4)
