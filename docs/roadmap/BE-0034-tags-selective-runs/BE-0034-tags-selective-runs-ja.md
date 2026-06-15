[English](BE-0034-tags-selective-runs.md) · **日本語**

# BE-0034 — タグ / ラベル + 選択実行

* 提案: [BE-0034](BE-0034-tags-selective-runs-ja.md)
* 状態: **実装済み**
* トラック: [可決済み](../README-ja.md#可決済み)
* トピック: 競合調査（MagicPod / Autify）由来の候補
* 由来: MagicPod

## はじめに

シナリオの `tags` を `--tag`/`--exclude` でサブセット実行します（include/exclude、exclude 優先・`select_scenarios`）。CI の段階的な実行に利用できます。

## 動機

TBD。

## 詳細設計

実装は `bajutsu/scenario.py`（`select_scenarios`） を参照。

## 検討した代替案

TBD。

## 参考

`bajutsu/scenario.py`（`select_scenarios`）、[cli.md](../../ja/cli.md)
