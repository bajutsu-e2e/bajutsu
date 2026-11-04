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

マイルストーン 2（M2）では、[Tier 1](../../docs/ja/glossary.md#2-つの層) の AI オーサリングループ（`record`）と、[証跡](../../docs/ja/glossary.md#証跡-capturepolicy-trace-triage)サブシステムを提供します。サブシステムは 3 つで構成します。`capturePolicy` ルール、`video` と `deviceLog` のインターバル取得、レポータ（JUnit と HTML）です。

## 動機

シナリオを手で書くのは時間がかかり、証跡がなければ失敗の調査も難しくなります。M2 では AI にシナリオを書かせます（Tier 1）。あわせて、「X のたびに取得する」という指定を再利用可能なルールへ正規化します。これにより、AI を使わない決定的な再実行でも同じ証跡を再現できます。

## 詳細設計

M2 は、次の 3 つを中心に構築しています。

- `Agent` 抽象（Claude 実装とシステムアラートガード）
- 証跡 Sink（即時のスクリーンショットと要素、simctl 経由のインターバル `video` / `deviceLog`）
- `capturePolicy` のトリガールール

レポートは `manifest.json`、JUnit XML、自己完結した HTML を出力します。

## 検討した代替案

冪等な正規化と由来コメントは、まだ軽量な実装にとどまっています。代替案ではなく、後続の課題として扱います。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[recording.md](../../docs/ja/recording.md)、[evidence.md](../../docs/ja/evidence.md)、[reporting.md](../../docs/ja/reporting.md)、`bajutsu/record.py`
