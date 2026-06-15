[English](../../roadmap/BE-0003-m3-codegen-traces-network-ci.md) · **日本語**

# BE-0003 — codegen・トレース・ネットワーク・CI（M3）

* 提案: [BE-0003](BE-0003-m3-codegen-traces-network-ci.md)
* 状態: **実装済み**
* トラック: [可決済み](README.md#可決済み)
* トピック: マイルストーン（M1–M4）

## はじめに

XCUITest codegen、アプリトレース（`appTrace` / os_signpost）、証跡のリダクション、ネットワーク**観測**（アプリ内コレクタ + `request` アサーション）、**決定的モック**（シナリオ `mocks` → オフライン in-protocol スタブ）、および CI。

## 動機

実パイプラインで採用されるには、ネイティブテストの出力、ネットワークの観測（と決定的スタブ化）、証跡中のシークレットのリダクション、そして全変更の CI ゲートが必要。

## 詳細設計

codegen はシナリオを構造的に等価な XCUITest（Swift）へ写像（テスト時に AI なし）。`ci.yml` は Linux（py3.13）で ruff + mypy + pytest、`e2e.yml` は macOS Simulator で idb スモークと codegen→XCUITest 経路（`make -C demos/features ui-test`）を実行。すべて実機検証済み。

## 検討した代替案

外部 `mockServer` コマンドは宣言的な in-protocol `mocks` に置き換えられ、未配線のまま（保留中の提案として管理）。

## 参考

[codegen.md](../codegen.md)、[ci.md](../ci.md)、`bajutsu/codegen.py`
