[English](BE-0015-web-ui-public-hosting.md) · **日本語**

# BE-0015 — Web UI の公開ホスティング

* 提案: [BE-0015](BE-0015-web-ui-public-hosting-ja.md)
* 状態: **提案**
* トラック: [提案](../README-ja.md#提案)
* トピック: オーサリング体験（record / GUI エディタ）

## はじめに

ローカルの `serve` を共有・公開サービスへ移行します。コントロールプレーン（Linux: FastAPI + Postgres + Redis + R2）と macOS ワーカープール（Orka）に分離し、認証・隔離・per-run Simulator を追加します。`subprocess.Popen` をジョブキューに置き換える中核部分のリファクタリングが必要です。

## 動機

TBD。

## 詳細設計

TBD —— 採用が決まった時点で具体化する。

## 検討した代替案

TBD。

## 参考

[cloud-hosting.md](../../ja/cloud-hosting.md)、`bajutsu/serve.py`
