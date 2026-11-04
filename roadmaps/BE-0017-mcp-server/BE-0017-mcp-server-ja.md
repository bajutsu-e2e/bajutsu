[English](BE-0017-mcp-server.md) · **日本語**

# BE-0017 — MCP サーバ化

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0017](BE-0017-mcp-server-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0017") |
| 実装 PR | [#44](https://github.com/bajutsu-e2e/bajutsu/pull/44) |
| トピック | 統合と自動化 |
<!-- /BE-METADATA -->

## はじめに

Bajutsu のコマンドを Model Context Protocol（MCP）のツールとして公開します。Claude Desktop や Claude Code などの AI エージェントが、そのツールを直接呼び出せるようになります。対象は、AI がシナリオを書き失敗を調べる [Tier 1](../../docs/ja/glossary.md#2-つの層) です。決定的ゲートは変更しません。

## 動機

Bajutsu は AI エージェントを Tier 1 の主要な利用者として扱います。エージェントの役割は、シナリオを書くことと失敗を調べることです。ところがシェル越しに Bajutsu へ届くエージェントは、CLI の呼び出しを組み立てます。さらに標準出力を受け取り、自由形式の出力を解析して、何が起きたかを知らねばなりません。

MCP ツールを使えば、エージェントは型のある窓口を直接呼べます。各実行のマニフェストとレポートを MCP リソースとして公開すれば、ログをかき集める代わりに構造化された結果を読めます。合否を判定するのは決定的なランナーだけなので、ゲートは変わりません。MCP が変えるのは、AI が担うオーサリングと調査の経路を、シェルなしで使えるようにする点だけです。

## 詳細設計

MCP サーバは `bajutsu/mcp/` に実装します。オプション依存として `fastmcp>=2.0.0` が必要です。

**ツール:**
- `bajutsu_doctor(app, udid)`：現在の画面のアクセシビリティ規約スコアを返します（in-process）。
- `bajutsu_run(app, scenario, ...)`：シナリオを決定的に実行します（subprocess）。

**リソース:**
- `bajutsu://runs/{run_id}/manifest.json`：実行結果の構造化 JSON です。
- `bajutsu://runs/{run_id}/report.html`：自己完結した HTML レポートです。
- `bajutsu://runs/latest/manifest.json`：最新の実行結果です。

**エントリポイント:** `bajutsu mcp [--config ...] [--runs ...] [--transport stdio|sse]`

インストール: `uv pip install bajutsu[mcp]`

## 検討した代替案

- **`run` の in-process 実行。** 却下しました。デバイスプールの管理が複雑だからです。subprocess のほうが単純で、`serve` と同じパターンになります。
- **HTTP transport を既定にする案。** 却下しました。Claude Desktop と Claude Code の統合では stdio が標準だからです。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

`bajutsu/mcp/`、[cli.md](../../docs/ja/cli.md)
