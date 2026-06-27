[English](BE-0088-overlap-simulator-boot.md) · **日本語**

# BE-0088 — Simulator の boot をビルドと並行させる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0088](BE-0088-overlap-simulator-boot-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トピック | 実機検証（M1 クローズアウト） |
<!-- /BE-METADATA -->

## はじめに

iOS Simulator の boot を非同期で開始し、アプリのビルドと並行して進めることで、オンデバイス
E2E ジョブのクリティカルパスから外します。boot 自体を速くするのではなく、ビルドが既に費やして
いる時間に ~80 秒の boot を重ね、実時間としての上乗せをなくす変更です。

## 動機

まっさらな GitHub macOS ランナーでは、Simulator の初回 boot に ~80 秒かかります（初回 boot の
データマイグレーション）。ランナーは使い捨てなので warm boot の恩恵も得られません。直近の run を
計測すると、boot とビルドは互いに独立しているにもかかわらず、boot はビルドの「後ろ」に逐次で
並んでいました。

- **smoke**: Build 36s → Boot 81s → Install 13s → Run 218s（ジョブ ~384s）
- **xcuitest**: Boot 85s → Codegen+xcodebuild 212s（ジョブ ~335s）

boot はビルドに依存しないため、両者を続けて実行すると重ねられるはずの時間を取りこぼします。
`simctl boot` は CoreSimulator（launchd サービス）に boot を依頼してすぐ返るだけで、その後の
boot はステップのシェルとは独立して進みます。そこで boot を先に開始し、「booted」を待つのはアプリ
をインストール/実行する直前だけにできます。決定性は保たれます。操作の前には必ず booted 状態を
待つので、準備の終わっていないデバイスに触れることはありません。

## 詳細設計

- **`boot-simulator` アクションを「開始のみ」にする。** デバイスを選び（まず *既に booted* の
  ものを優先＝boot コスト 0、次にプリインストール済みの利用可能な iPhone、最後に新規作成）、
  `simctl boot`（非同期）を発行し、`bootstatus` を待たずに UDID を返します。
- **smoke** は `make sample-build` の前に boot を開始し、~80 秒の boot を 36 秒のビルドに重ね、
  その後の専用 `simctl bootstatus` ステップで残りを待ってからアプリをインストールします。
- **xcuitest** は `make ui-test` の前に boot を開始します。明示的な待機は加えません。
  `xcodebuild test` は test フェーズに達した時点で対象デバイスが ready になるまでブロックし、
  その頃には codegen のビルドと重なった boot が完了しているためです。

E2E ゲートへの正味の効果（ゲートの実時間は長辺のジョブ＝smoke）は、smoke のクリティカルパスから
~36 秒です。xcuitest 単体は最大 ~85 秒を削れ、smoke がゲートの長辺のままでもこのジョブの
フィードバックは速くなります。smoke の支配的コスト（Run scenarios、idb 操作の ~218 秒）はこの
変更では変わりません。そちらは boot ではなく
[BE-0087](../../in-progress/BE-0087-idb-action-settle/BE-0087-idb-action-settle-ja.md) の領域です。

## 検討した代替案

- **CoreSimulator のデバイスデータをキャッシュして warm boot 化する。** 却下。デバイス UDID が
  キャッシュ内のパスに埋め込まれ、runtime バージョンの完全一致が必須で、データディレクトリは
  大きく絶対パスを含み、古い復元はビルド/boot 失敗を招きます。労力が大きく脆く、DerivedData で
  ちょうど撤去した「確実にヒットしないキャッシュ」と同じ轍を踏みます。
- **より軽いデバイス種別を選んで boot を速くする。** 効果は小さく不安定です。デバイス種別に
  かかわらず初回 boot のデータマイグレーションが支配的だからです。
- **boot 待ちを省いて操作側のリトライに任せる。** 却下。準備の終わっていないデバイスを操作する
  ことになり、プライムディレクティブが禁じる決定性の破壊にあたります。

## 参考

- [BE-0087](../../in-progress/BE-0087-idb-action-settle/BE-0087-idb-action-settle-ja.md) — idb
  アクションのタイミング堅牢化（boot とは別の Run scenarios のコスト）。
- [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) —
  決定性/flaky 監査。
