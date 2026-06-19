[English](BE-0002-m2-ai-loop-and-evidence.md) · **日本語**

# BE-0002 — AI オーサリングループと証跡（M2）

* 提案: [BE-0002](BE-0002-m2-ai-loop-and-evidence-ja.md)
* 状態: **実装済み**
* 実装 PR: PR 単位の履歴より前（初期インポートにスカッシュ済み・単一 PR なし）
* トラック: [可決済み](../../README-ja.md#可決済み)
* トピック: マイルストーン（M1–M4）

## はじめに

Tier 1 の AI オーサリングループ（`record`）と証跡サブシステム: `capturePolicy` ルール、`video` / `deviceLog` のインターバル取得、レポータ（JUnit / HTML）。

## 動機

シナリオを手で書くのは時間がかかり、証跡なしでは失敗の調査が困難です。M2 は AI にシナリオを書かせ（Tier 1）、「X のたびに取得」を再利用可能なルールに正規化して、AI なしの決定的な再実行でも同じ証跡を再現できるようにします。

## 詳細設計

`Agent` 抽象（Claude 実装 + システムアラートガード）、証跡 Sink（即時スクショ / 要素、simctl 経由のインターバル video / deviceLog）、`capturePolicy` トリガールールを中心に構築しています。レポートは `manifest.json` + JUnit XML + 自己完結 HTML を出力します。

## 検討した代替案

冪等正規化 / 由来コメントは依然として軽量な実装にとどまっており、代替案ではなく後続課題として扱います。

## 参考

[recording.md](../../../docs/ja/recording.md)、[evidence.md](../../../docs/ja/evidence.md)、[reporting.md](../../../docs/ja/reporting.md)、`bajutsu/record.py`
