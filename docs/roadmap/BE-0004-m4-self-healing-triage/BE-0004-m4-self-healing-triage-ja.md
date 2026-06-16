[English](BE-0004-m4-self-healing-triage.md) · **日本語**

# BE-0004 — 自己修復トリアージ（M4）

* 提案: [BE-0004](BE-0004-m4-self-healing-triage-ja.md)
* 状態: **実装済み**
* 実装 PR: PR 単位の履歴より前（初期インポートにスカッシュ済み・単一 PR なし）
* トラック: [可決済み](../README-ja.md#可決済み)
* トピック: マイルストーン（M1–M4）

## はじめに

自己修復トリアージ: `bajutsu triage` が失敗 run のコンテキストを組み立てて診断します（原因 + 修正案）。**助言のみで、合否の判定者にはなりません**。

## 動機

回帰の保守はコストがかかります。M4 は AI に失敗を調査させ最小限の修正を提案させることでそのコストを下げます。決定性の境界は維持されます: 修正は人間が diff をレビューして opt-in したときだけ適用されます。

## 詳細設計

診断は同じ `TriageAgent` プロトコル背後の 2 エージェントのいずれかで実行します: ルールベースの `HeuristicTriageAgent`、または `triage --ai`（失敗スクショも読む Claude）。エージェントは構造化 fix（`renameId` / `addIndex` / `raiseTimeout`）を提案でき、`--apply` が dry-run diff を表示し、`--write` が source に適用し、`--rerun` が再検証します。実機で end-to-end 検証済みです。

## 検討した代替案

競合ツールは実行中に自動補正します。Bajutsu は「テストを甘くする」のを避けるため、実行中の暗黙補正を意図的に拒否しています（[DESIGN §11](../../../DESIGN.md)）。

## 参考

[DESIGN §3.1 / §12](../../../DESIGN.md)、[reporting.md](../../ja/reporting.md)、`bajutsu/triage.py`、[自己修復トリアージ（M4）](../README-ja.md#自己修復トリアージm4)
