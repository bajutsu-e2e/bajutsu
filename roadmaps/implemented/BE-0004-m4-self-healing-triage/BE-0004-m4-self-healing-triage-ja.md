[English](BE-0004-m4-self-healing-triage.md) · **日本語**

# BE-0004 — 自己修復トリアージ（M4）

* 提案: [BE-0004](BE-0004-m4-self-healing-triage-ja.md)
* Author: [@0x0c](https://github.com/0x0c)
* 状態: **実装済み**
* 実装 PR: PR 単位の履歴より前（初期インポートにスカッシュ済み・単一 PR なし）
* トラック: [可決済み](../../README-ja.md#可決済み)
* トピック: マイルストーン（M1–M4）

## はじめに

自己修復トリアージです。`bajutsu triage` が失敗した run のコンテキストを組み立てて診断します（原因と修正案）。**助言を返すだけで、合否を判定する立場には決して立ちません**。

## 動機

回帰の保守にはコストがかかります。M4 は、AI に失敗を調査させて最小限の修正を提案させることで、そのコストを下げます。このとき決定性の境界は保たれます。修正が適用されるのは、人間が diff をレビューして opt-in したときだけです。

## 詳細設計

診断は、同じ `TriageAgent` プロトコルの背後にある 2 つのエージェントのいずれかで実行します。ルールベースの `HeuristicTriageAgent` と、`triage --ai`（失敗時のスクリーンショットも読む Claude）です。エージェントは構造化された fix（`renameId`、`addIndex`、`raiseTimeout`）を提案でき、`--apply` がそれを dry-run の diff として表示し、`--write` が source に適用し、`--rerun` が再検証します。実機で end-to-end の検証済みです。

## 検討した代替案

競合ツールは実行中に自動補正します。Bajutsu は「テストを甘くする」ことを避けるため、実行中の暗黙の補正を意図的に拒否しています（[DESIGN §11](../../../DESIGN.md)）。

## 参考

[DESIGN §3.1 / §12](../../../DESIGN.md)、[reporting.md](../../../docs/ja/reporting.md)、`bajutsu/triage.py`、[自己修復トリアージ（M4）](../../README-ja.md#自己修復トリアージm4)
