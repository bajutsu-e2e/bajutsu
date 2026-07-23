[English](BE-0002-m2-ai-loop-and-evidence.md) · **日本語**

# BE-0002 — AI オーサリングループと証跡（M2）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0002](BE-0002-m2-ai-loop-and-evidence-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0002") |
| 実装 PR | PR 単位の履歴より前（初期インポートにスカッシュ済みで、単一 PR なし） |
| トピック | オーサリング体験 |
<!-- /BE-METADATA -->

## はじめに

Tier 1 の AI オーサリングループ（`record`）と証跡サブシステムです。`capturePolicy` ルール、`video` / `deviceLog` のインターバル取得、レポータ（JUnit / HTML）からなります。

## 動機

シナリオを手で書くのは時間がかかり、証跡がなければ失敗の調査も難しくなります。M2 では AI にシナリオを書かせ（Tier 1）、「X のたびに取得する」という指定を再利用可能なルールへ正規化します。これにより、AI を使わない決定的な再実行でも同じ証跡を再現できます。

## 詳細設計

`Agent` 抽象（Claude 実装とシステムアラートガード）、証跡 Sink（即時のスクリーンショットと要素、simctl 経由のインターバル video / deviceLog）、`capturePolicy` のトリガールールを中心に構築しています。レポートは `manifest.json`、JUnit XML、自己完結した HTML を出力します。

## 検討した代替案

冪等な正規化と由来コメントはまだ軽量な実装にとどまっており、代替案ではなく後続の課題として扱います。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[recording.md](../../docs/ja/recording.md)、[evidence.md](../../docs/ja/evidence.md)、[reporting.md](../../docs/ja/reporting.md)、`bajutsu/record.py`
