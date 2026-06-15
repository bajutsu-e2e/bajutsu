[English](BE-0033-scenario-variables-control-flow.md) · **日本語**

# BE-0033 — シナリオ変数 + 軽い制御フロー

* 提案: [BE-0033](BE-0033-scenario-variables-control-flow-ja.md)
* 状態: **可決・実装中**
* トラック: [可決済み](../README-ja.md#可決済み)
* トピック: 競合調査（MagicPod / Autify）由来の候補
* 由来: MagicPod

## はじめに

`${...}` 補間プリミティブ（`interp.py`、params/row/secrets を共通処理）は実装済み。残りは **UI 値の capture → 後続で再利用（`vars.*`）** と、決定性を崩さない範囲の条件分岐 / ループ。

## 動機

TBD。

## 詳細設計

実装は `bajutsu/interp.py` を参照。

## 検討した代替案

TBD。

## 参考

`bajutsu/interp.py`、[scenarios.md](../../ja/scenarios.md)
