[English](BE-0003-m3-codegen-traces-network-ci.md) · **日本語**

# BE-0003 — codegen・トレース・ネットワーク・CI（M3）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0003](BE-0003-m3-codegen-traces-network-ci-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0003") |
| 実装 PR | PR 単位の履歴より前（初期インポートにスカッシュ済みで、単一 PR なし） |
| トピック | codegen 網羅性 |
<!-- /BE-METADATA -->

## はじめに

マイルストーン 3（M3）では、次の 5 つの機能を提供します。

- XCUITest codegen
- アプリトレース（`appTrace` / os_signpost）
- 証跡のリダクション
- ネットワークの**観測**（アプリ内コレクタと `request` アサーション）
- **決定的モック**（シナリオの `mocks` をオフラインの in-protocol スタブへ変換する）

M3 は、継続的インテグレーション（CI）も加えます。

## 動機

実際のパイプラインがツールを採用するには、次の 4 つが必要です。ネイティブテストの出力、ネットワークの観測（と決定的なスタブ化）、証跡中のシークレットのリダクション、すべての変更に対する CI のゲートです。

## 詳細設計

codegen は、シナリオを構造的に等価な XCUITest（Swift）へ変換します。テスト時に AI は使いません。変更のゲートは、次の 2 つのワークフローが担います。

- `ci.yml` は、Linux（py3.13）で ruff、mypy、pytest を実行します。
- `e2e.yml` は、macOS Simulator で idb スモークシナリオと、codegen から XCUITest への経路（`make -C demos/features ui-test`）を実行します。

どちらも実機で検証済みです。

## 検討した代替案

宣言的な in-protocol `mocks` が、外部の `mockServer` コマンドに置き換わりました。このコマンドは配線されないまま残しており、保留中の提案として追跡しています。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[codegen.md](../../docs/ja/codegen.md)、[ci.md](../../docs/ja/ci.md)、`bajutsu/codegen.py`
