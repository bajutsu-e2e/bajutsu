[English](BE-0016-web-ui-self-hosting.md) · **日本語**

# BE-0016 — Web UI のセルフホスティング

* 提案: [BE-0016](BE-0016-web-ui-self-hosting-ja.md)
* 状態: **提案**
* トラック: [提案](../README-ja.md#提案)
* トピック: オーサリング体験（record / GUI エディタ）

## はじめに

自前 Mac で稼働させる構成。段階 A（Tailscale + LaunchAgent で現 `serve` を即時稼働）と段階 B（Docker Compose: Postgres/Redis/MinIO/Authelia + 自前 Mac ワーカープール）。Simulator が GUI セッション必須な点を含む運用ガイド。

## 動機

TBD。

## 詳細設計

TBD —— 採用が決まった時点で具体化する。

## 検討した代替案

TBD。

## 参考

[self-hosting.md](../../ja/self-hosting.md)、`bajutsu/serve.py`
