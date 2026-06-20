[English](BE-0003-m3-codegen-traces-network-ci.md) · **日本語**

# BE-0003 — codegen・トレース・ネットワーク・CI（M3）

* 提案: [BE-0003](BE-0003-m3-codegen-traces-network-ci-ja.md)
* Author: [@0x0c](https://github.com/0x0c)
* 状態: **実装済み**
* 実装 PR: PR 単位の履歴より前（初期インポートにスカッシュ済み・単一 PR なし）
* トラック: [可決済み](../../README-ja.md#可決済み)
* トピック: マイルストーン（M1–M4）

## はじめに

XCUITest codegen、アプリトレース（`appTrace` / os_signpost）、証跡のリダクション、ネットワークの**観測**（アプリ内コレクタと `request` アサーション）、**決定的モック**（シナリオの `mocks` をオフラインの in-protocol スタブへ変換する）、そして CI です。

## 動機

実際のパイプラインで採用されるには、ネイティブテストの出力、ネットワークの観測（と決定的なスタブ化）、証跡中のシークレットのリダクション、すべての変更を CI（継続的インテグレーション）でゲートすることが必要になります。

## 詳細設計

codegen は、シナリオを構造的に等価な XCUITest（Swift）へ変換します（テスト時に AI は使いません）。`ci.yml` は Linux（py3.13）で ruff、mypy、pytest を実行し、`e2e.yml` は macOS Simulator で idb スモークシナリオと codegen から XCUITest への経路（`make -C demos/features ui-test`）を実行します。いずれも実機で検証済みです。

## 検討した代替案

外部の `mockServer` コマンドは宣言的な in-protocol `mocks` に置き換わり、配線されないまま残しています（保留中の提案として追跡しています）。

## 参考

[codegen.md](../../../docs/ja/codegen.md)、[ci.md](../../../docs/ja/ci.md)、`bajutsu/codegen.py`
