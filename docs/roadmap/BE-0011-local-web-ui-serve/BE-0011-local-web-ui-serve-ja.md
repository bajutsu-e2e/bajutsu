[English](BE-0011-local-web-ui-serve.md) · **日本語**

# BE-0011 — ローカル Web UI（`bajutsu serve`）

* 提案: [BE-0011](BE-0011-local-web-ui-serve-ja.md)
* 状態: **実装済み**
* トラック: [可決済み](../README-ja.md#可決済み)
* トピック: オーサリング体験（record / GUI エディタ）

## はじめに

シナリオ / アプリ一覧の表示、ワンクリック実行、実行ログのストリーミング、ブラウザ内レポート表示を提供する小規模なランチャです（stdlib のみ使用）。CI ゲートには含まれない Tier 1 の利便機能です。予定している GUI エディタ（可視編集・要素ピッカー）の基盤にもなります。

## 動機

TBD。

## 詳細設計

実装は `bajutsu/serve.py` を参照。

## 検討した代替案

TBD。

## 参考

`bajutsu/serve.py`、[cli.md](../../ja/cli.md)
