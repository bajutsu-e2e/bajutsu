[English](BE-0039-self-healing-propose-optin.md) · **日本語**

# BE-0039 — 自己修復は「提案＋opt-in 適用」に限定

* 提案: [BE-0039](BE-0039-self-healing-propose-optin-ja.md)
* 状態: **実装済み**
* トラック: [可決済み](../README-ja.md#可決済み)
* トピック: 競合調査（MagicPod / Autify）由来の候補
* 由来: 両社

## はじめに

両社は実行中に自動補正。Bajutsu は自己修復トリアージの **最小差分を提案 → 人間が diff レビュー → `--write` で明示適用**に留める（実行中の暗黙補正はしない＝「テストを甘くする」防止・[DESIGN §11](../../../DESIGN.md)）。

## 動機

TBD。

## 詳細設計

実装済み（コード参照は *参考* を参照）。

## 検討した代替案

TBD。

## 参考

[自己修復トリアージ（M4）](../README-ja.md#自己修復トリアージm4)
