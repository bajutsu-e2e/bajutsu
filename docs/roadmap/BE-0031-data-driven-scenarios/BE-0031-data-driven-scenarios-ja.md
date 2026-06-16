[English](BE-0031-data-driven-scenarios.md) · **日本語**

# BE-0031 — データ駆動シナリオ

* 提案: [BE-0031](BE-0031-data-driven-scenarios-ja.md)
* 状態: **実装済み**
* 実装 PR: [#6](https://github.com/bajutsu-e2e/bajutsu/pull/6)
* トラック: [可決済み](../README-ja.md#可決済み)
* トピック: 競合調査（MagicPod / Autify）由来の候補
* 由来: MagicPod

## はじめに

`data`（inline）/ `dataFile`（CSV）で 1 つのシナリオを複数行にわたって反復します。`${row.*}` を各行で置換します（`expand_data`）。多言語テストや境界値テストに利用できます。

## 動機

TBD。

## 詳細設計

実装は `bajutsu/scenario.py`（`expand_data`） を参照。

## 検討した代替案

TBD。

## 参考

`bajutsu/scenario.py`（`expand_data`）、[scenarios.md](../../ja/scenarios.md)
