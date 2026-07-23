[English](BE-0169-serve-metrics-observability.md) · **日本語**

# BE-0169 — serve のメトリクスと可観測性エンドポイント

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0169](BE-0169-serve-metrics-observability-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0169") |
| 実装 PR | [#719](https://github.com/bajutsu-e2e/bajutsu/pull/719) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md), [BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging-ja.md) |
| 由来 | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) |
<!-- /BE-METADATA -->

## はじめに

セルフホストの serve バックエンド（[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）は、
すでに秘密情報を伏せた**構造化 JSON ログ**を標準出力へ出しています
（[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging-ja.md)）。それをログ基盤へ送るのは
デプロイ側の役目です。足りないのは**メトリクス**です。キューの深さ、組織ごとの実行中ジョブ数、実行時間、
ワーカーの生存を一目で見る手段がありません。この提案は、`/metrics` エンドポイントと、compose スタックへの
任意の Prometheus・Grafana コンテナを追加します。BE-0016 の「単一ノードをプールへ育てる」作業から
切り出したものです。

## 動機

Mac プールの運用では、ログだけではうまく見えてこない問いに答えることになります。キューは滞留しているか、
いまどの組織がプールを消費しているか、実行はいつもより遅くないか、ワーカーは死んでいないか、といった問いです。
構造化ログ（BE-0055）はイベントを捉えますが、育っていくプールを見守る運用者には、飽和や生存の問題が
停止になる前に気付き、プールの規模を見積もるための**時系列の信号**が必要です。スクレイプ可能なメトリクス
エンドポイントとチャート基盤が、それを得る標準的な方法であり、BE-0016 の可観測性の話で唯一まだ出荷されて
いない部分です。

## 詳細設計

エンドポイントは Python 側の面を持つ唯一の部分であり（したがってゲートで検証できる契約を持ちます）、
コンテナは手動で検証するデプロイの関心事です。作業分解は次のとおりです。

1. **`/metrics` エンドポイント。** serve バックエンドから Prometheus 形式のメトリクスを、少なくとも次を
   含めて公開します。**キューの深さ**（待機中のジョブ。組織別・能力別が存在する場合はそれも）、
   **組織ごとの実行中ジョブ数**、**実行時間**、**ワーカーの生存**（リースとハートビートの新しさ）。
   メトリクスの面は制御プレーンがすでに追跡している状態（`jobs` テーブルとリース・ハートビートの記録）から
   導くため、新しい記録を足すのではなく既存の状態を読みます。
2. **compose 配線。** `deploy/self-host/` へ、`prometheus`（`/metrics` をスクレイプ）と `grafana`
   （それを可視化）のコンテナ、および初期ダッシュボードを追加します。これらは既存の `caddy` プロファイルと
   同様に任意であり、最小構成のデプロイは変わりません。
3. **エンドポイントの認証。** `/metrics` は serve の他の部分と同じ公開ルール（BE-0051）に従わなければ
   ならず（認証なしの公開面ではありません）、BE-0055 の秘匿と一貫して、秘密情報を決して漏らしてはなりません。

**検証。** `/metrics` エンドポイントは Python 側の面を持ち、Simulator を使わず単体テストします。既知の
待機中・実行中ジョブを持つ `ServeState` に対して、描画されたメトリクスが期待どおりのキューの深さと組織別の
実行中数を報告すること、出力に秘密情報が現れないことを確かめます。Prometheus と Grafana のコンテナは
デプロイ上で手動で検証します（スクレイプが成功し、ダッシュボードが時系列を描くこと）。

**調整メモ。** `/metrics` ルートは `bajutsu/serve/` に触れるため、進行中のほかの serve 作業と同じ調整メモが
付きます。その面を編集中の未マージ PR がマージされたあとに入れるか、コンフリクトを避けるよう調整します。

## 検討した代替案

- **構造化ログだけに頼る。** 却下します。ログは時系列ではなくイベントであり、キューの深さやワーカーの生存を
  ログのスクレイプから導くのは脆く、遅れます。第一級のメトリクスエンドポイントが、これらの信号を得るための
  標準的で摩擦の少ない情報源です。
- **スクレイプ可能なエンドポイントの代わりに外部のホスト型 APM へメトリクスを送る。** セルフホストの既定
  としては却下します。セルフホストのスタックが避けようとしている外部依存を復活させます。Prometheus 形式の
  `/metrics` エンドポイントはセルフホストの Prometheus・Grafana と組み合わさり、望むならホスト型の
  スクレイパを向けることも依然として可能です。
- **Prometheus・Grafana を任意プロファイルではなく常時有効として同梱する。** 却下します。すべての
  セルフホストが追加のコンテナを望むわけではありません。（`caddy` と同様に）任意にすることで、最小構成の
  デプロイを軽く保ちつつ、望むときには一式を提供できます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] キューの深さ、組織ごとの実行中数、実行時間、ワーカーの生存を公開する `/metrics` エンドポイント。
- [x] `/metrics` が serve の公開ルールに従い、秘密情報を漏らさない（単体テスト）。
- [x] `deploy/self-host/` への任意の `prometheus` + `grafana` コンテナと初期ダッシュボード。

- [#719](https://github.com/bajutsu-e2e/bajutsu/pull/719) — `/metrics` エンドポイントを 2 つの serve backend（stdlib ハンドラと FastAPI コントロール
  プレーン）に出荷しました。制御プレーンがすでに追跡している状態から Prometheus 形式のメトリクスを描画します。
  org ごとの実行中 job 数（`state.jobs`）と、データベースを配線したときのキューの深さ、リース済み job 数、
  ワーカーのハートビート鮮度、もっとも古い実行中 run（新設の一括読み取り `Repository.metrics_snapshot`）です。
  エンドポイントは serve の認証ゲート（BE-0051）の背後にあり、出力はカウント、経過時間、org やワーカーの
  識別子だけで、job の spec やトークンは含まないため、スクレイプが秘密を漏らすことはありません。
  `deploy/self-host/` に任意の `metrics` compose プロファイル（`prometheus` + `grafana`、組み込み済みの
  データソース、初期ダッシュボード）を追加し、セルフホストの README と `docs/self-hosting.md`（および
  `docs/ja/`）に記載しました。

## 参考

`bajutsu/serve/`（`/metrics` が入る先）、`deploy/self-host/`（コンテナが加わる compose スタック）、
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)（この項目の由来である
セルフホストのアンブレラ）、[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging-ja.md)（この
メトリクス作業が補完する構造化 serve ログ）、
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)（`/metrics`
が従うべき公開ルール）。
