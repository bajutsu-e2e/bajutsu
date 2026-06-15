[English](BE-0022-update-structured-fixes.md) · **日本語**

# BE-0022 — `update`（最小差分提案＝構造化 fix の適用）

* 提案: [BE-0022](BE-0022-update-structured-fixes-ja.md)
* 状態: **実装済み**
* トラック: [可決済み](../README-ja.md#可決済み)
* トピック: 自己修復トリアージ（M4）

## はじめに

壊れたシナリオを全体再記録せず最小差分で更新します。triage が構造化 fix（`renameId`/`addIndex`/`raiseTimeout`）を提案し、`--apply`（dry-run diff）/`--write` で source に適用、`--rerun` で再実行検証を行います。実機で rename・addIndex の閉ループは実証済みです。

## 動機

TBD。

## 詳細設計

実装は `bajutsu triage --apply` を参照。

## 検討した代替案

TBD。

## 参考

[DESIGN §6.5](../../../DESIGN.md)、`bajutsu triage --apply`
