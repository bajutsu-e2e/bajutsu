[English](BE-0032-secret-variables.md) · **日本語**

# BE-0032 — シークレット変数

* 提案: [BE-0032](BE-0032-secret-variables-ja.md)
* 状態: **実装済み**
* 実装 PR: [#6](https://github.com/bajutsu-e2e/bajutsu/pull/6)
* トラック: [可決済み](../README-ja.md#可決済み)
* トピック: 競合調査（MagicPod / Autify）由来の候補
* 由来: MagicPod

## はじめに

`${secrets.X}` を環境変数から解決して入力に使用し、その実値を証跡で自動的にマスクします（既存の `redact` を入力値まで拡張）。config の `secrets:` で宣言します。

## 動機

TBD。

## 詳細設計

実装は `bajutsu/interp.py`・`bajutsu/redaction.py` を参照。

## 検討した代替案

TBD。

## 参考

`bajutsu/interp.py`・`bajutsu/redaction.py`、[evidence.md](../../ja/evidence.md)
