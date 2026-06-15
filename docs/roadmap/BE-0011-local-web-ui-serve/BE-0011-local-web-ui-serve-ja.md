[English](BE-0011-local-web-ui-serve.md) · **日本語**

# BE-0011 — ローカル Web UI（`bajutsu serve`）

* 提案: [BE-0011](BE-0011-local-web-ui-serve-ja.md)
* 状態: **実装済み**
* トラック: [可決済み](../README-ja.md#可決済み)
* トピック: オーサリング体験（record / GUI エディタ）

## はじめに

シナリオ / アプリ一覧・ワンクリック実行・実行ログのストリーミング・ブラウザ内レポート表示の小さなランチャ（stdlib のみ）。CI ゲートには入らない Tier 1 の利便機能。GUI エディタ（可視編集・要素ピッカー）への第一歩。

## 動機

TBD。

## 詳細設計

実装は `bajutsu/serve.py` を参照。

## 検討した代替案

TBD。

## 参考

`bajutsu/serve.py`、[cli.md](../../ja/cli.md)
