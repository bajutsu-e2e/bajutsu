[English](BE-0003-m3-codegen-traces-network-ci.md) · **日本語**

# BE-0003 — codegen・トレース・ネットワーク・CI（M3）

* 提案: [BE-0003](BE-0003-m3-codegen-traces-network-ci-ja.md)
* 状態: **実装済み**
* 実装 PR: PR 単位の履歴より前（初期インポートにスカッシュ済み・単一 PR なし）
* トラック: [可決済み](../README-ja.md#可決済み)
* トピック: マイルストーン（M1–M4）

## はじめに

XCUITest codegen、アプリトレース（`appTrace` / os_signpost）、証跡のリダクション、ネットワーク**観測**（アプリ内コレクタ + `request` アサーション）、**決定的モック**（シナリオ `mocks` → オフライン in-protocol スタブ）、および CI。

## 動機

実パイプラインで採用されるには、ネイティブテストの出力、ネットワークの観測（と決定的スタブ化）、証跡中のシークレットのリダクション、そして全変更の CI（継続的インテグレーション）ゲートが必要です。

## 詳細設計

codegen はシナリオを構造的に等価な XCUITest（Swift）へ変換します（テスト時に AI は使用しません）。`ci.yml` は Linux（py3.13）で ruff + mypy + pytest を実行し、`e2e.yml` は macOS Simulator で idb スモークシナリオと codegen→XCUITest 経路（`make -C demos/features ui-test`）を実行します。すべて実機で検証済みです。

## 検討した代替案

外部 `mockServer` コマンドは宣言的な in-protocol `mocks` に置き換えられ、未配線のまま管理しています（保留中の提案として追跡）。

## 参考

[codegen.md](../../ja/codegen.md)、[ci.md](../../ja/ci.md)、`bajutsu/codegen.py`
