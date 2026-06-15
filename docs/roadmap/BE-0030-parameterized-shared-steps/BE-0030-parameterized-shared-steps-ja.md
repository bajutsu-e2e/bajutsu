[English](BE-0030-parameterized-shared-steps.md) · **日本語**

# BE-0030 — パラメータ化シェアドステップ

* 提案: [BE-0030](BE-0030-parameterized-shared-steps-ja.md)
* 状態: **実装済み**
* トラック: [可決済み](../README-ja.md#可決済み)
* トピック: 競合調査（MagicPod / Autify）由来の候補
* 由来: MagicPod

## はじめに

`use` ステップで**引数付きの再利用部品（component）**を定義・呼び出し、`${params.*}` を展開（`expand_components`）。`setup` プレリュード（引数なし）と併用可。ログイン等の共通手順を DRY 化。

## 動機

TBD。

## 詳細設計

実装は `bajutsu/scenario.py`（`use`/`expand_components`） を参照。

## 検討した代替案

TBD。

## 参考

`bajutsu/scenario.py`（`use`/`expand_components`）、[scenarios.md](../../ja/scenarios.md)
