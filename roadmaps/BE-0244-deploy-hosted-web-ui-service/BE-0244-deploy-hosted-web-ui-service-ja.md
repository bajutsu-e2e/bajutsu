[English](BE-0244-deploy-hosted-web-ui-service.md) · **日本語**

# BE-0244 — ホスト版 Web UI サービスのデプロイ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0244](BE-0244-deploy-hosted-web-ui-service-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **却下** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0244") |
| トピック | Web UI のホスティング |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) |
<!-- /BE-METADATA -->

## はじめに

> **却下。サービスはすでに稼働しています。** 共有のホスト版 Web UI サービスは、下記の段階的な計画とは
> 別の経路ですでに稼働しています。そのため、本項目が追跡していたプロビジョニング作業は不要になりました。
> 本項目は記録として残します。

[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) は、公開ホスト版
Web UI のスタックを選定しました。あわせて、それを実現する**ソフトウェアをすべて出荷しました**。

- `serve` の 5 つの差し替え可能な seam
- 永続化レイヤ
- 永続セッションつきの GitHub OAuth
- ユーザ単位のロールベースアクセス制御（RBAC）
- 監査ログ
- 同時実行クォータ
- マルチテナンシー（org モデル、リクエスト時の org 解決、org スコープの強制、org 別ストレージ）

これらはコードであり、すでにマージ済みです。

残っているのは**コードではありません**。実インフラの上でサービスを立ち上げる作業です。本項目は、その
運用作業を追跡します。

- Linux の control plane、macOS の worker プール、データベース、オブジェクトストレージをプロビジョニングする
- 本番の認証とシークレットを配線する
- インターネットに公開された稼働環境に対して、セキュリティハードニング項目を閉じる

BE-0015 が*設計とソフトウェア*であり、本項目は*デプロイ*です。切り出すことで、BE-0015 は
Implemented として閉じられます。ソフトウェアが完成しているためです。運用作業は独自のライフサイクルを
持ち、有料かつ macOS 限定のキャパシティに依存するので、単独で追跡します。

この切り出しは、[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md)
（post-completion worker モデル）と
[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
（ホスト時の config source 制限）が、すでに BE-0015 というアンブレラから独立した採番項目として
切り出されたのと同じやり方です。

関連：[architecture](../../docs/ja/architecture.md)、[ci](../../docs/ja/ci.md)、セルフホスト版の対
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)。

## 動機

BE-0015 のソフトウェア基盤は、どこかで動くまでは休眠状態です。ログイン済みのユーザは、共有インフラの
上でプロジェクトを選び、Run を押し、ライブログを眺め、レポートを見ることが、まだできません。共有
インフラが存在しないからです。control plane、データベース、オブジェクトストレージは、まだ
プロビジョニングされていません。macOS の worker プールも同じです。worker プールは run を実際に実行するので、
とりわけ重要です。

このデプロイは、BE-0015 が出荷したすべてとは種類の違う作業です。

- seam の背後の Python ではなく、Infrastructure as Code と運用である。
- 多くがリポジトリの外にある（クラウドアカウント、ドメインネームシステム（DNS）のレコード、トランスポート層
  セキュリティ（TLS）の証明書、シークレットマネージャ）。
- コストの判断に左右される。**macOS の worker が費用の大半を占め**、きれいにゼロへスケールしない
  （MacStadium Orka ノードや EC2 Mac の最小割り当て）。

これを BE-0015 の内側に留めると、次のどちらかになります。有料キャパシティのプロビジョニングを待って、
項目が無期限に開いたままになります。あるいは、何もホストされていないのに Implemented にしてしまいます。
別項目にすれば、両方が正直になります。BE-0015 は「ソフトウェアは完成した」を表し、本項目は「サービスは
稼働している」を表します。

決定論的なコア（`bajutsu run` とレポート）とセキュリティ姿勢は、本作業では変わりません。BE-0015 の
*Migration* 節が枠づけたとおり、変わるのは*呼び出しと配線*がクラウドへ移る点だけです。

## 詳細設計

スコープは、BE-0015 の **Deployment plan (phased)** と、その **Security hardening** 節のうち稼働環境に
かかわる部分です。本項目は、この両方を作業分解としてそのまま採ります。スタックの選定は **BE-0015 ですでに
決定済み**です。

- FastAPI
- Caddy
- GitHub OAuth
- PostgreSQL
- Postgres テーブルのジョブキュー
- Cloudflare R2
- MacStadium Orka
- Terraform と GitHub Actions

本項目は、これらの選択をプロビジョニングして配線します。選び直すことはしません。

### Phase 1 — MVP（動く共有サービスを出荷する）

1. **control plane のホスティング。** サーバ backend をコンテナ化し（GitHub Container Registry（GHCR）の
   イメージ）、**Fly.io**（または Render）へデプロイします。リバースプロキシと自動 TLS は、
   プラットフォームまたは Caddy がまかないます。
2. **system of record。** マネージドの **PostgreSQL**（Fly Postgres）をプロビジョニングし、Alembic
   マイグレーションを実行します。`BAJUTSU_DATABASE_URL` をそのデータベースに向けます。これで、run 履歴、
   アイデンティティ、監査、Postgres テーブルのジョブキューが稼働します。
3. **アーティファクトストレージ。** **Cloudflare R2** バケットを作成します。オブジェクトストア版の
   `ArtifactStore` と org 別プレフィックスを配線します。レポート資産は短命の署名付き URL で配信します。
4. **macOS worker を 1 台。** **MacStadium Orka** ノードを 1 台プロビジョニングし、worker エージェントを
   launchd サービスとして導入します。エージェントに `db` extra と `BAJUTSU_DATABASE_URL` を与え、完了した
   run がその org と実行者のもとに記録されるようにします。lease → run（`--erase`）→ upload → result の
   ループが、デプロイした control plane に対して回ることを確認します。
5. **本番の認証とシークレット。** デプロイした origin 向けに **GitHub OAuth** アプリを登録します。
   セッションと OAuth の設定、および org 別の `ANTHROPIC_API_KEY` を、シークレットマネージャ
   （Fly または Doppler）に格納します。
6. **受け入れ条件。** ログイン済みユーザがプロジェクトとシナリオを選び、Run を押し、サーバ送信イベント
   （SSE）でライブログを眺め、レポートを見ます。この流れが、共有インフラの上で端から端まで通ることが
   条件です。単一の default org と、もう 1 つ設定した org の両方で確認します。

### Phase 2 — スケール

7. **オーケストレーション。** control plane を **Kubernetes**（GKE または EKS）へ移し、マネージドの
   Postgres（Cloud SQL または RDS）を併用します。
8. **worker のオートスケール。** ジョブキューの深さに応じて **Orka** の Mac プールをスケールさせ、
   小さな暖機フロアを残します。希少なプールには **org 単位の同時実行クォータ**を強制します。
9. **配信とマルチリージョン。** レポート資産のために、R2 の前段へコンテンツデリバリネットワーク
   （**CDN**）を置きます。必要に応じて control plane をマルチリージョン化します。
10. **可観測性。** **Sentry**（エラー）と **Prometheus / Grafana**（メトリクス）を立ち上げます。
    キューの深さと worker の健全性にアラートを設定します。構造化 JSON ログ
    （[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging-ja.md)）を出力します。

### セキュリティハードニング（稼働環境でのクローズ）

11. run のディスパッチに対するユーザ単位、org 単位の**レート制限**。
12. デプロイ環境での **worker サンドボックス化**。run ごとに揮発的な Mac と Simulator を使い、
    **egress の allowlist** を設け、テナントをまたぐシークレットの再利用を禁じます。
13. **エッジの防御**。オリジン間リソース共有（CORS）とクロスサイトリクエストフォージェリ（CSRF）への
    防御、標準的なセキュリティヘッダ、署名付きで期限切れするアーティファクト URL を、稼働 origin に
    対して検証します。

各番号の単位は独立して出荷できます。下記 *進捗* は、その単位に一対一で対応します。

## 検討した代替案

- **残作業を BE-0015 の内側に留める。** 却下します。有料キャパシティのプロビジョニングを待って
  BE-0015 が無期限に開いたままになるか、何もホストされていないのに Implemented にしてしまいます。
  切り出しは既存の前例に沿います。BE-0106 と BE-0108 は、同じアンブレラから切り出されました。
- **デプロイをセルフホスト項目
  [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) に畳み込む。** 却下します。
  BE-0016 は*自分の* Mac でスタックを動かす話です。本項目は、BE-0015 が選定した*マネージドで
  マルチテナントの公開*デプロイです。対象読者もインフラもコストモデルも異なります。
- **スタックを選び直す。** スコープ外です。スタックは BE-0015 の *Detailed design* と
  *Alternatives considered* で決定済みです。本項目はその選択をプロビジョニングするだけで、
  蒸し返しません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 1 — control plane のホスティング（コンテナ化 → Fly.io / Render、リバースプロキシと TLS）。
- [ ] 2 — マネージド PostgreSQL のプロビジョニング、Alembic マイグレーション実行、`BAJUTSU_DATABASE_URL` の配線。
- [ ] 3 — Cloudflare R2 バケットと、org 別プレフィックスおよび署名付き URL を備えたオブジェクトストア版 `ArtifactStore`。
- [ ] 4 — MacStadium Orka の worker エージェント（launchd）を 1 台、`db` extra つきで。lease → run → upload → result を確認。
- [ ] 5 — 本番の GitHub OAuth アプリとシークレットマネージャ（セッション、org 別 `ANTHROPIC_API_KEY`）。
- [ ] 6 — Phase 1 の受け入れ：2 つの org で共有インフラ上の端から端までの run。
- [ ] 7 — Kubernetes の control plane とマネージド Postgres。
- [ ] 8 — キューの深さに応じた Orka のオートスケールと org 単位の同時実行クォータ。
- [ ] 9 — R2 前段の CDN、必要に応じたマルチリージョン control plane。
- [ ] 10 — 可観測性（Sentry と Prometheus / Grafana とアラート）。
- [ ] 11 — run ディスパッチのユーザ単位、org 単位のレート制限。
- [ ] 12 — worker サンドボックス化（揮発的な Mac と Simulator、egress allowlist、テナント間シークレット再利用の禁止）。
- [ ] 13 — エッジの防御（CORS / CSRF、セキュリティヘッダ、署名付きで期限切れするアーティファクト URL）を稼働環境で検証。

## 参考

`bajutsu/serve/`、[ci](../../docs/ja/ci.md)、[architecture](../../docs/ja/architecture.md)、
[reporting](../../docs/ja/reporting.md)、[cli](../../docs/ja/cli.md#serve)、設計とソフトウェアの項目
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、セルフホスト版の対
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)、そして上記の可観測性行を
実体化する運用ログを設計する
[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging-ja.md)。
