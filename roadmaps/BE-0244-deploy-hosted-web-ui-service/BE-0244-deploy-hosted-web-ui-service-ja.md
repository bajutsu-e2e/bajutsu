[English](BE-0244-deploy-hosted-web-ui-service.md) · **日本語**

# BE-0244 — ホスト版 Web UI サービスのデプロイ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0244](BE-0244-deploy-hosted-web-ui-service-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0244") |
| トピック | Web UI のホスティング |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) は、公開ホスト版
Web UI のスタックを選定し、それを実現する**ソフトウェアをすべて出荷しました**。`serve` の 5 つの
差し替え可能な seam、永続化レイヤ、永続セッションつきの GitHub OAuth、ユーザ単位の RBAC、監査ログ、
同時実行クォータ、そしてマルチテナンシー（org モデル、リクエスト時の org 解決、org スコープの強制、
org 別ストレージ）です。これらはコードであり、すでにマージ済みです。

残っているのは**コードではありません**。実インフラの上でサービスを立ち上げる作業です。本項目はその
運用作業を追跡します。すなわち、Linux の control plane、macOS の worker プール、データベース、
オブジェクトストレージのプロビジョニング、本番の認証とシークレットの配線、そしてインターネットに
公開された稼働環境に対するセキュリティハードニング項目のクローズです。BE-0015 が*設計とソフトウェア*
なら、本項目は*デプロイ*です。切り出すことで、BE-0015 は Implemented として閉じられます
（ソフトウェアは完成しているため）。一方、独自のライフサイクルを持ち、有料かつ macOS 限定の
キャパシティに依存する運用作業は、単独で追跡できます。

これは、[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md)
（post-completion worker モデル）と
[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
（ホスト時の config source 制限）が、すでに BE-0015 というアンブレラから独立した採番項目として
切り出されたのと同じやり方です。

関連：[architecture](../../docs/ja/architecture.md)、[ci](../../docs/ja/ci.md)、セルフホスト版の対
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)。

## 動機

BE-0015 のソフトウェア基盤は、どこかで動くまでは休眠状態です。ログイン済みのユーザは、共有インフラの
上でプロジェクトを選び、Run を押し、ライブログを眺め、レポートを見ることが、まだできません。共有
インフラが存在しないからです。control plane、データベース、オブジェクトストレージ、そして（決定的に）
実際に run を実行する macOS の worker プールが、まだプロビジョニングされていません。

このデプロイは、BE-0015 が出荷したすべてとは種類の違う作業です。seam の背後の Python ではなく、
Infrastructure as Code と運用です。その多くはリポジトリの外にあります（クラウドアカウント、DNS、
TLS 証明書、シークレットマネージャ）。さらにコストの判断に左右されます。**macOS の worker が費用の
大半を占め**、きれいにゼロへスケールしないからです（MacStadium Orka ノードや EC2 Mac の最小割り当て）。
これを BE-0015 の内側に留めると、有料キャパシティのプロビジョニングを待って項目を無期限に開いたままに
するか、何もホストされていないのに Implemented にしてしまうかの、どちらかを強います。別項目にすれば
両方が正直になります。BE-0015 は「ソフトウェアは完成した」、本項目は「サービスは稼働している」です。

決定論的なコア（`bajutsu run` とレポート）とセキュリティ姿勢は、本作業では変わりません。BE-0015 の
*Migration* 節が枠づけたとおり、変わるのはその*呼び出しと配線*がクラウドへ移る点だけです。

## 詳細設計

スコープは BE-0015 の **Deployment plan (phased)** と、その **Security hardening** 節のうち稼働環境に
かかわる部分であり、これらをそのまま本項目の作業分解とします。スタックの選定（FastAPI、Caddy、
GitHub OAuth、PostgreSQL、Postgres テーブルのジョブキュー、Cloudflare R2、MacStadium Orka、
Terraform と GitHub Actions）は **BE-0015 ですでに決定済み**であり、ここで蒸し返しません。本項目は
それらを選び直すのではなく、プロビジョニングして配線することが目的です。

### Phase 1 — MVP（動く共有サービスを出荷する）

1. **control plane のホスティング。** サーバ backend をコンテナ化し（GHCR イメージ）、**Fly.io**
   （または Render）へデプロイします。リバースプロキシと自動 TLS は、プラットフォームまたは Caddy で
   まかないます。
2. **system of record。** マネージドの **PostgreSQL**（Fly Postgres）をプロビジョニングし、Alembic
   マイグレーションを実行します。`BAJUTSU_DATABASE_URL` を向けて、run 履歴、アイデンティティ、監査、
   Postgres テーブルのジョブキューを稼働させます。
3. **アーティファクトストレージ。** **Cloudflare R2** バケットを作成し、オブジェクトストア版の
   `ArtifactStore` と org 別プレフィックスを配線します。レポート資産は短命の署名付き URL で配信します。
4. **macOS worker を 1 台。** **MacStadium Orka** ノードを 1 台プロビジョニングし、worker エージェントを
   launchd サービスとして導入します。`db` extra と `BAJUTSU_DATABASE_URL` を与え、完了した run が
   その org と実行者のもとに記録されるようにします。lease → run（`--erase`）→ upload → result の
   ループが、デプロイした control plane に対して回ることを確認します。
5. **本番の認証とシークレット。** デプロイした origin 向けに **GitHub OAuth** アプリを登録します。
   セッションと OAuth の設定、および org 別の `ANTHROPIC_API_KEY` を、シークレットマネージャ
   （Fly / Doppler）に格納します。
6. **受け入れ条件：** ログイン済みユーザがプロジェクトとシナリオを選び、Run を押し、SSE でライブログを
   眺め、レポートを見る流れが、共有インフラの上で端から端まで通ること。単一の default org と、もう
   1 つ設定した org の両方で確認します。

### Phase 2 — スケール

7. **オーケストレーション。** control plane を **Kubernetes**（GKE / EKS）へ移し、マネージドの
   Postgres（Cloud SQL / RDS）を併用します。
8. **worker のオートスケール。** ジョブキューの深さに応じて **Orka** の Mac プールをスケールさせ、
   小さな暖機フロアを残します。希少なプールには **org 単位の同時実行クォータ**を強制します。
9. **配信とマルチリージョン。** レポート資産のために R2 の前段へ **CDN** を置きます。必要に応じて
   control plane をマルチリージョン化します。
10. **可観測性。** **Sentry**（エラー）と **Prometheus / Grafana**（メトリクス）を立ち上げ、キューの
    深さと worker の健全性にアラートを設定します。構造化 JSON ログ
    （[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging-ja.md)）を出力します。

### セキュリティハードニング（稼働環境でのクローズ）

11. run のディスパッチに対するユーザ単位、org 単位の**レート制限**。
12. デプロイ環境での **worker サンドボックス化**。run ごとに揮発的な Mac と Simulator を使い、
    **egress の allowlist** を設け、テナントをまたぐシークレットの再利用を禁じます。
13. **エッジの防御**。CORS / CSRF、標準的なセキュリティヘッダ、そして署名付きで期限切れするアーティ
    ファクト URL を、稼働 origin に対して検証します。

各番号の単位は独立して出荷でき、下記 *進捗* に一対一で対応します。

## 検討した代替案

- **残作業を BE-0015 の内側に留める。** 却下します。有料キャパシティのプロビジョニングを待って
  BE-0015 を無期限に開いたままにするか、何もホストされていないのに Implemented にしてしまうかを強います。
  切り出しは既存の前例（BE-0106 と BE-0108 は同じアンブレラから切り出された）に沿います。
- **デプロイをセルフホスト項目
  [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) に畳み込む。** 却下します。
  BE-0016 は*自分の* Mac でスタックを動かす話であり、本項目は BE-0015 が選定した*マネージドで
  マルチテナントの公開*デプロイです。対象読者もインフラもコストモデルも異なります。
- **スタックを選び直す。** スコープ外です。スタックは BE-0015 の *Detailed design* と
  *Alternatives considered* で決定済みであり、本項目はその選択をプロビジョニングするのであって、
  蒸し返すものではありません。

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
