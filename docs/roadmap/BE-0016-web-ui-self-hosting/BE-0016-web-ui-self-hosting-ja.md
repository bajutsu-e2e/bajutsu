[English](BE-0016-web-ui-self-hosting.md) · **日本語**

# BE-0016 — Web UI のセルフホスティング

* 提案: [BE-0016](BE-0016-web-ui-self-hosting-ja.md)
* 状態: **提案**
* トラック: [提案](../README-ja.md#提案)
* トピック: オーサリング体験（record / GUI エディタ）

## はじめに

個人の Mac 上で Web UI を稼働させるための構成です。段階 A では Tailscale と LaunchAgent を使って現行の `serve` をすぐに外部公開します。段階 B では Docker Compose（Postgres / Redis / MinIO / Authelia）と自前の Mac ワーカープールを組み合わせます。Simulator に GUI セッションが必要な点を含む運用ガイドも作成します。

## 動機

TBD。

## 詳細設計

TBD —— 採用が決まった時点で具体化する。

## 検討した代替案

TBD。

## 参考

[self-hosting.md](../../ja/self-hosting.md)、`bajutsu/serve.py`
