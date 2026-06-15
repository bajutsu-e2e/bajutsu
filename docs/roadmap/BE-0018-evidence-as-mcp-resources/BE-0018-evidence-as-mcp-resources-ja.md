[English](BE-0018-evidence-as-mcp-resources.md) · **日本語**

# BE-0018 — 証跡を MCP リソースで返す

* 提案: [BE-0018](BE-0018-evidence-as-mcp-resources-ja.md)
* 状態: **実装済み**
* トラック: [承認済み](../README-ja.md#承認済み)
* トピック: 統合・自動化（MCP 化）

## はじめに

実行証跡を MCP リソースとして公開し、AI エージェントがファイルシステムアクセスなしに結果、スクリーンショット、要素ツリー、ネットワークログを読めるようにします。

## 詳細設計

リソースは `bajutsu/mcp/resources.py` に登録され、MCP サーバ（BE-0017）経由で提供されます。

| リソース URI | 内容 |
|---|---|
| `bajutsu://runs/{run_id}/manifest.json` | 実行結果の構造化 JSON |
| `bajutsu://runs/{run_id}/report.html` | 自己完結 HTML レポート |
| `bajutsu://runs/{run_id}/junit.xml` | CI 向け JUnit XML |
| `bajutsu://runs/{run_id}/artifact/{path*}` | 任意のネストされたアーティファクト（スクリーンショット、elements.json、network.json、動画、デバイスログ） |
| `bajutsu://runs/latest/manifest.json` | 最新の実行結果の manifest |

テキストファイル（JSON/XML/HTML/YAML/log）は文字列で、バイナリファイル（PNG、MP4）はバイト列で返されます。すべてのパスはトラバーサル検証済み（`runs/` からの脱出およびクロスラン読み取りを防止）です。

## 検討した代替案

- アーティファクト種別ごとに別リソースを定義（却下: ワイルドカード `{path*}` パターンの方がシンプルで、現在・将来のアーティファクト種別をすべてカバー）

## 参考

`bajutsu/mcp/resources.py`、[reporting.md](../../ja/reporting.md)、[evidence.md](../../ja/evidence.md)
