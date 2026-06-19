[English](BE-0017-mcp-server.md) · **日本語**

# BE-0017 — MCP サーバ化

* 提案: [BE-0017](BE-0017-mcp-server-ja.md)
* 状態: **実装済み**
* トラック: [承認済み](../../README-ja.md#承認済み)
* トピック: 統合・自動化（MCP 化）

## はじめに

Bajutsu のコマンドを MCP（Model Context Protocol）ツールとして公開し、AI エージェント（Claude Desktop / Code）から直接呼び出せるようにします。Tier 1（AI オーサリング・調査）との統合に適した機能で、決定的ゲートは変更しません。

## 詳細設計

MCP サーバは `bajutsu/mcp/` に実装（optional dependency `fastmcp>=2.0.0`）。

**ツール:**
- `bajutsu_doctor(app, udid)` — 現在の画面のアクセシビリティ規約スコアを返す（in-process）
- `bajutsu_run(app, scenario, ...)` — シナリオを決定的に実行（subprocess）

**リソース:**
- `bajutsu://runs/{run_id}/manifest.json` — 実行結果の構造化 JSON
- `bajutsu://runs/{run_id}/report.html` — 自己完結 HTML レポート
- `bajutsu://runs/latest/manifest.json` — 最新の実行結果

**エントリポイント:** `bajutsu mcp [--config ...] [--runs ...] [--transport stdio|sse]`

インストール: `uv pip install bajutsu[mcp]`

## 検討した代替案

- `run` の in-process 実行（却下: デバイスプール管理が複雑。subprocess の方がシンプルで `serve` と同じパターン）
- HTTP transport をデフォルトに（却下: stdio が Claude Desktop / Code 統合の標準）

## 参考

`bajutsu/mcp/`、[cli.md](../../../docs/ja/cli.md)
