[English](BE-0004-m4-self-healing-triage.md) · **日本語**

# BE-0004 — 自己修復トリアージ（M4）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0004](BE-0004-m4-self-healing-triage-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0004") |
| 実装 PR | PR 単位の履歴より前（初期インポートにスカッシュ済みで、単一 PR なし） |
| トピック | 自己修復トリアージ |
<!-- /BE-METADATA -->

## はじめに

マイルストーン 4（M4）では、自己修復[トリアージ](../../docs/ja/glossary.md#証跡-capturepolicy-trace-triage)を提供します。`bajutsu triage` が、失敗した run のコンテキストを組み立てて診断し、原因と修正案を返します。診断は**助言にとどまり、合否を判定する立場には決して立ちません**。

## 動機

回帰の保守にはコストがかかります。M4 は、AI に失敗を調査させて最小限の修正を提案させることで、そのコストを下げます。決定性の境界は保たれます。修正が適用されるのは、人間が diff をレビューして opt-in したときだけです。

## 詳細設計

診断は、同じ `TriageAgent` プロトコルの背後にある 2 つのエージェントのいずれかで実行します。

- ルールベースの `HeuristicTriageAgent`
- `triage --ai`（失敗時のスクリーンショットも読む Claude）

エージェントは、構造化された fix（`renameId`、`addIndex`、`raiseTimeout`）を提案できます。fix には 3 つのフラグが働きます。`--apply` が dry-run の diff として表示し、`--write` が source に適用し、`--rerun` が再検証します。実機で end-to-end の検証済みです。

## 検討した代替案

競合ツールは、実行中に自動補正します。Bajutsu は「テストを甘くする」ことを避けるため、実行中の暗黙の補正を意図的に拒否しています（[DESIGN §11](../../DESIGN.md)）。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[DESIGN §3.1 / §12](../../DESIGN.md)、[reporting.md](../../docs/ja/reporting.md)、`bajutsu/triage.py`、[自己修復トリアージ](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html)（ロードマップダッシュボード）
