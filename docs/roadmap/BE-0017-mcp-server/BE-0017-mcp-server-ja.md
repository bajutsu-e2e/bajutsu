[English](BE-0017-mcp-server.md) · **日本語**

# BE-0017 — MCP サーバ化

* 提案: [BE-0017](BE-0017-mcp-server-ja.md)
* 状態: **提案**
* トラック: [提案](../README-ja.md#提案)
* トピック: 統合・自動化（MCP 化）

## はじめに

`run`・`doctor`・`record`・`codegen` を MCP（Model Context Protocol）ツールとして公開し、Claude 等のエージェントから直接呼び出せるようにします。Tier 1（AI オーサリング）との統合に適した機能です。

## 動機

TBD。

## 詳細設計

TBD —— 採用が決まった時点で具体化する。

## 検討した代替案

TBD。

## 参考

[cli.md](../../ja/cli.md)、`bajutsu/agent.py` / `claude_agent.py`
