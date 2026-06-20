[English](BE-0015-web-ui-public-hosting.md) · **日本語**

# BE-0015 — Web UI の公開ホスティング

* 提案: [BE-0015](BE-0015-web-ui-public-hosting-ja.md)
* Author: [@0x0c](https://github.com/0x0c)
* 状態: **提案**
* トラック: [提案](../../README-ja.md#提案)
* トピック: Web UI のホスティング（クラウド / セルフホスト）

## はじめに

将来構想です。**公開サービス自体はまだ未実装**ですが、その土台となる local/server パリティの足場は
着地済みです（下記「移行」を参照）。本提案は、ローカルの `bajutsu serve`（`bajutsu/serve/`）を
**共有・公開サービス**化するための、サーバ・DB・ストレージ・デプロイの具体的な技術選定です。
現状の UI は Tier 1 の便利機能で、`127.0.0.1` にバインドし、認証はなく、同一ホスト上で
`bajutsu run` を subprocess 起動するだけです（[cli](../../../docs/ja/cli.md#serve) ·
[reporting](../../../docs/ja/reporting.md)）。公開によって変わるのはアドレスだけではなく、**システムの
形そのもの**です。Web UI は**薄いランチャー**なので、これをホスティングするとは実質「**ランナーを
ホスティングする**」ことであり、それは Linux のコントロールプレーンと macOS ワーカープールから
成る共有公開サービスへとつながります。

関連: [architecture](../../../docs/ja/architecture.md) · [ci](../../../docs/ja/ci.md) · セルフホスト版の対
[BE-0016](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)。

## 動機

Web UI は**薄いランチャー**です。`/api/run` は `python -m bajutsu run …` を起動し、それが
`idb` + `simctl` 経由で **iOS Simulator** を駆動します。そして Simulator は **macOS 上にしか存在
しません**。つまり「Web UI をホスティングする」とは実質「**ランナーをホスティングする**」であり、
ランナーには Mac が必要です。汎用 Linux PaaS（Cloud Run / Vercel / Linux 上の Fly Machines）では
run を実行できません。

このため、現状の単一プロセス設計には無い**分離トポロジ**が必要になります。

```
        ブラウザ（多数のユーザ）
              │  HTTPS + OAuth
              ▼
   ┌──────────────────────────┐        ┌───────────────────────────────┐
   │  コントロールプレーン     │        │   macOS ワーカープール          │
   │  (Linux)                  │  job   │  bajutsu run · idb · Simulator │
   │  FastAPI · Postgres · S3  │ ─────▶ │  run ごとに使い捨て Simulator   │
   │  enqueue + レポート配信   │ ◀───── │  ログ配信 · 成果物アップロード  │
   └──────────────────────────┘  Redis └───────────────────────────────┘
       安価・水平スケール               高価・macOS 限定・隔離が必要
```

安価でステートフルなマルチユーザ部分（認証・履歴・キュー・レポート閲覧）は Linux に置きます。高価で
macOS 限定の部分は、ジョブを受け取り、クリーンな Simulator で実行し、ログを流し、成果物を
アップロードするだけのステートレスなワーカーに絞り込みます。これが中心的なリファクタです。
**`serve` のプロセス内 `subprocess.Popen` を、ブローカーに積むジョブへ変え、リモートワーカーが
消費する形にします。**

## 詳細設計

### 選定スタック（推奨）

| レイヤ | 採用 | 採用理由 | 主な代替 |
|---|---|---|---|
| **API / Web** | **FastAPI** + **Uvicorn**（本番は Gunicorn + uvicorn worker） | 非同期（ライブログの SSE（Server-Sent Events）/WebSocket）、Pydantic は**既に依存**（[pyproject](../../../pyproject.toml)）、OpenAPI 自動生成、コアと同じ Python | Django（重い・同期前提）、Litestar、stdlib 継続（認証/多人数に耐えない） |
| **フロント** | `serve` の**単一ページ UI を踏襲**し API から配信。認証とプロジェクト選択を追加 | UI は既に 1 枚の HTML 文字列。v1 に SPA（シングルページアプリケーション）ビルドは不要 | UI が育てば後で React/Svelte |
| **リバースプロキシ + TLS** | **Caddy** | Let's Encrypt 自動 HTTPS をほぼ無設定で実現。プロキシとヘッダ設定も簡潔 | nginx + certbot（設定が多い）、Traefik |
| **認証 / 認可** | **OAuth2（GitHub プロバイダ）**（**Authlib**）、署名 Cookie セッション、org 単位 RBAC（ロールベースアクセス制御） | 対象は開発者（GitHub を持つ）。パスワードを保持しない。org モデルが GitHub org に対応 | oauth2-proxy（エッジ）、Auth0/Clerk/WorkOS（マネージド有償）、Google OAuth |
| **system of record** | **PostgreSQL 16** + **SQLAlchemy 2.0** + **Alembic** | リレーショナルな核（org/user/project/run）と manifest 要約用の **JSONB**。マネージドが豊富（RDS/Cloud SQL/Neon/Supabase） | SQLite（多人数の並行に不可）、MySQL |
| **キュー / キャッシュ / pub-sub** | **Redis 7** | 1 つで 3 役。**ジョブブローカー**・キャッシュ・ライブログの **pub/sub 配信**（worker → Redis → SSE） | RabbitMQ/NATS（ブローカーのみ）、SQS（ブローカーのみ・pub/sub なし） |
| **タスク基盤** | まず **RQ**（Redis Queue） | 小さく Redis ネイティブで読みやすい。「`bajutsu run` を積んで worker が消費」に合致 | Celery（routing/retry/beat が要るときに採用）、Dramatiq |
| **成果物ストレージ** | **Cloudflare R2**（S3 互換） | run ツリー（`report.html`・スクショ・**動画**・`network.json`）は大きなバイナリです。**Postgres に入れません**。R2 は**下り無料** | AWS S3（egress 課金）、MinIO（自前）、GCS |
| **macOS ワーカー** | **MacStadium Orka** | macOS VM オーケストレーション専用（「Mac 版 k8s」）。クリーンな Mac の**スケール可能でスケジュール可能なプール**を得られる唯一の選択肢 | AWS EC2 Mac（24h 最小割当・高価）、Scaleway Apple silicon、自前 Mac mini |
| **シークレット** | クラウドのシークレット管理（**Doppler** / プラットフォーム純正: Fly/AWS Secrets Manager） | 集中ローテーション。org ごとに **`ANTHROPIC_API_KEY` を各自持ち込み（BYO: Bring Your Own）**（`--dismiss-alerts`・`record` のコスト/悪用を org 単位で限定） | Vault（重い）、env ファイル（公開では不可） |
| **可観測性** | **Sentry**（エラー）+ **Prometheus/Grafana**（メトリクス）+ 構造化 JSON ログ | 標準・安価・ホステッドあり | OpenTelemetry collector、Datadog（有償） |
| **IaC（Infrastructure as Code）+ CI/CD（継続的インテグレーション/継続的デリバリ）** | **Terraform** + **GitHub Actions** → **GHCR**（GitHub Container Registry）イメージ | 再現可能なインフラ。リポジトリは既に Actions 上（[ci](../../../docs/ja/ci.md)） | Pulumi、手動（不可） |

### 各構成要素の役割

#### コントロールプレーン（Linux、安価、水平スケール）
今日の `serve` の発展形です。エンドポイント（すべて認証付き）:

- `GET /` → プロジェクト単位の UI（scenario/app の候補は**ファイルシステムではなく DB** から）。
- `POST /api/run` → リクエストを**呼び出し元のプロジェクトに対して検証**します（クライアント指定の
  ファイルパスは禁止。セキュリティ参照）。`run` 行を書き、**RQ ジョブを enqueue** して id を返します。
- `GET /api/runs/stream/<id>` → ライブログの **SSE** ストリーム（現 UI の 1 秒ポーリングを置換）。
  worker が PUBLISH する Redis チャネルを購読します。
- `GET /runs/<id>/…` → レポート資産を **短命の署名付き R2 URL** で配信（現状のローカル
  `_serve_run_file` を置換）。

#### ジョブキュー（コントロールプレーン ↔ ワーカー）
Redis をブローカーとして使います。run は `{run_id, project, scenario_ref, app, options, byo_key_ref}` の
ジョブになります。worker が `BRPOP`/lease します。worker はログ行と状態を run 単位の Redis チャネルに
`PUBLISH` し、コントロールプレーンの SSE がそれを購読します。

#### macOS ワーカー（ステートレス、隔離、使い捨て）
Orka が払い出す各 Mac 上の小さな Python エージェント（launchd サービス）:

1. Redis からジョブを lease します。
2. そのプロジェクトの**シナリオをコントロールプレーン/オブジェクトストアから取得**します
   （クライアントが選んだパスは使いません）。
3. **消去済みのクリーンな Simulator** を用意します（`bajutsu run --erase`）。公開マルチテナントは
   **隔離必須**で、これはローカル UI の高速 `--no-erase` 再利用ループを意図的に捨てることを意味します。
4. stdout を Redis に流し、完了時に **`runs/<id>/` ツリーを R2 にアップロード**し、結果（exit code・
   run id・manifest 要約）をコントロールプレーンへ `POST` します。
5. Simulator を破棄します。

### デプロイ計画（段階的）

#### フェーズ 1：MVP、最短で出す
- **コントロールプレーン**をコンテナ化し、**Fly.io**（or Render）へ。マネージドの **Fly Postgres** +
  **Upstash Redis**。成果物は **Cloudflare R2**。TLS はプラットフォーム/Caddy。**GitHub OAuth**。
- **ワーカー**: **MacStadium Orka 1 ノード**でエージェント稼働。シナリオ・app 設定はプロジェクト
  単位で Postgres/R2 に保存。
- **シークレット**は Fly/Doppler。各 org が自分の `ANTHROPIC_API_KEY` を持ち込みます。
- ゴール: ログイン済みユーザがプロジェクト + シナリオを選び Run を実行し、ライブログを見て、レポートを
  閲覧する。共有インフラ上で安全に、end-to-end で行えるようにします。

#### フェーズ 2：スケール
- コントロールプレーン → **Kubernetes**（GKE/EKS）、マネージド **Cloud SQL/RDS** +
  **ElastiCache/Upstash**。
- キュー深さ駆動の **Orka オートスケール** Mac プール。org ごとの**並行数クォータ**。
- R2 前段に成果物 **CDN（コンテンツデリバリネットワーク）**。必要ならコントロールプレーンをマルチリージョン化。
- フル可観測性（Sentry + Grafana ダッシュボード + キュー深さ/ワーカー健全性のアラート）。

#### コストの実情
Linux のコントロールプレーン・Postgres・Redis・R2 は**安価で弾力的**です。一方 **Mac が費用を支配**し、
きれいにゼロスケールしません（Orka ノード / EC2 Mac の 24h 最小）。プールは**少数のウォームフロア +
キュー深さ**で設計し、決定的な*ゲート*は使い捨て CI（[ci](../../../docs/ja/ci.md)）に寄せて、ホステッド
プールは回帰テストの物量ではなく**対話的オーサリングだけ**を担わせます。

### セキュリティ強化（公開前に必須）

現 `serve` が安全なのは localhost 限定かつ単一ユーザだからです。公開はこの両方の前提を壊すため、以下は
任意ではありません。

- **全エンドポイントに認証**（OAuth + org 単位 RBAC）。run 起動をユーザ/org 単位で**レート制限**します。
- **任意パスでのシナリオ実行を排除**します。現状 `/api/run` は `body["scenario"]` を、`scenarios_dir`
  内かの検査なしに `bajutsu run` の argv へそのまま渡します（`bajutsu/serve/` の
  `run_command`/`do_POST`）。ホステッドでは**シナリオはプロジェクト単位で保存し worker が id で
  取得**します。クライアントはパスを指定せず、`backend`/`udid` も自由文字列でなく**許可リスト**で検証します。
- **org ごとの BYO `ANTHROPIC_API_KEY`**。AI 機能（`--dismiss-alerts`・`record`）のコスト/悪用を
  鍵の持ち主の org に限定します。
- **ワーカーのサンドボックス化**。シナリオは実質「デバイスを操作する非信頼コード」です。各 run を
  **使い捨て Mac/Simulator** で、**egress 許可リスト**付き、**テナント間でシークレット共有なし**で実行します。
- **署名付き・期限切れの成果物 URL**、**CORS/CSRF** 対策、標準セキュリティヘッダ、そして誰が何をいつ
  実行したかの**監査ログ**。
- **org 単位のクォータ/並行上限**。1 テナントが希少で高価な Mac プールを枯渇させないようにします。

### stdlib の `serve` からの移行（ゼロからの書き直しではなく段階的に）

**土台はすでに着地済みです。** `bajutsu serve` はパッケージ（`bajutsu/serve/`）になり、差し替え可能な
4 つの **seam** を中心に構成されています。各 seam にはローカル実装が `main` 上にあるため、下記の各ステップは
「ローカル実装を、既存の seam の背後でサーバ実装に差し替える」作業であって、書き直しではありません。

- **`RunExecutor`**：ジョブ起動（ローカルは `run_job` を回すプロセス内デーモンスレッド）。
- **`LogBus`**：ライブログ配信（ローカルはインメモリのバッファ。UI はすでに SSE で配信済み）。
- **`ArtifactStore`**：run 成果物の読み取り（ローカルは `runs_dir` に封じ込めたファイルシステム）。
- **`ScenarioStore`**：シナリオ解決（ローカルはアプリの scenarios dir に封じ込め）。

[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
が認証と入力検証を出荷済みで、純粋ヘルパ（`list_scenarios`・`list_runs`・`run_command`・`Job` モデル）も
すでに `bajutsu/serve/helpers.py` に切り出されています。したがって:

1. 切り出し済みの純粋ヘルパと埋め込み HTML を FastAPI アプリへ移し、v1 フロントとします。
2. **キュー型の `RunExecutor`**（enqueue → RQ）と、今 `run_job` がビルドするのと同じ `bajutsu run` argv を
   実行する worker エントリポイントを用意します。
3. **Redis pub/sub による `LogBus`** と、ローカルのファイル読み取りを **R2 署名付き URL のリダイレクト**に
   置き換える **オブジェクトストレージの `ArtifactStore`** を用意します。
4. **OAuth + Postgres**（org/project/run）と、ファイルシステムではなくストレージから id で解決する
   **プロジェクト単位の `ScenarioStore`** を追加します。下記の 7a/7b/7c がこのステップを詳述します。
5. **Orka ワーカー 1 台**を立て、上記セキュリティ項目を閉じてから、プールをスケールします。

各ステップは独立に出荷・テスト可能です。決定的コア（`bajutsu run`・レポート）は終始不変で、変わるのは
その*起動方法*と*配管*がクラウドへ移ることだけです。

### 永続化と認証（残りのスライス、7a/7b/7c）

シングルテナントのサーバ backend は、いまや 5 つの seam（前掲の 4 つに、視覚回帰のベースラインを扱う
**`BaselineStore`** を加えたもの）でローカルと機能パリティに達しており、移行のステップ 1〜3 は着地済みです。
残るのは**ステップ 4、すなわち永続化と認証**で、これがシングルテナントの backend をマルチテナントへ
変えます。`ScenarioStore` とオブジェクトストレージはすでにテナント prefix（`<org>/`）でパラメータ化されて
いるため、このスライスは既存の seam の上にデータベースと認証を載せる作業になります。進め方は 3 つの不変条件
に従います。ローカルの挙動を変えないこと。各スライスのテストを Linux ゲートで完結させること（Simulator も、
稼働中の Postgres・Redis・オブジェクトストレージも要らない）。各スライスを既存の seam パターンに倣わせること
（Protocol と注入実装の組で、optional extra の背後に遅延 import し、既定の `serve`/CLI 経路では決して
読み込まない）。これを独立に出荷できる 3 つのスライスに分けます。

#### 7a 永続化レイヤ（`Repository` seam）

5 本目の seam である `Repository` を、`ObjectStore` と同じ作りで `bajutsu/serve/server/db.py` に置きます。
Protocol、注入する SQLAlchemy 2.0 実装、環境変数から組み立てるファクトリ、そして遅延 import です。スキーマは
最初の Alembic マイグレーションで決め切ります。後から外部キーを足すのは、ゲートが使う SQLite では苦痛だから
です。7a で読み書きするのは `runs` だけですが、テーブルは一式を定義します。

```
orgs       id, slug（一意）, name, created_at
users      id, org_id → orgs, email（一意）, github_login（7b で使用）, created_at
projects   id, org_id → orgs, name（= config のアプリ名）, created_at, unique(org_id, name)
runs       id, org_id → orgs, project_id → projects, created_by → users,
           status, ok, created_at, summary（JSONB）
audit_log  id, org_id → orgs, actor_id → users, action, target, at, detail（JSONB）
```

`org_id` を全テーブルに通してあるのは、7c の org 単位のスコープとクォータがこの列で絞り込むためです。
リレーショナルなコア（id、status、タイムスタンプ）は通常の列に置き、可変な manifest 要約と監査の詳細だけを
`JSON().with_variant(JSONB, "postgresql")` にします。これで同じモデルが SQLite（ゲート）でも
Postgres（本番）でも動きます。7a が seam に実装するのは `runs` のメソッド、`record_run`／`get_run`／
`list_runs` だけで、境界型 `RunRecord` を返すことで ORM の行が seam の外へ漏れないようにします。
`orgs`／`users`／`projects`／`audit_log` の振る舞いは 7b と 7c で加えます。配線は seam を組み立てる唯一の場所で
ある `_build_server_state` に置き、`BAJUTSU_DATABASE_URL` で切り替えます。これが未設定のとき、および
ローカル backend では `repository` は `None` のままなので、挙動は変わりません。テストは各テスト関数の中で
インメモリの SQLite エンジンを組み立てます（fixture は使いません）。import ガードはすでに `sqlalchemy`、
`alembic`、`psycopg` を既定経路で禁止しています。`db` extra が `sqlalchemy`、`alembic`、`psycopg` を
担います。出荷は 2 つの PR に分けます。7a-1（スキーマと repository と SQLite テスト。既存ファイルには
触れない）と、7a-2（`_build_server_state` の配線と Alembic）です。

#### 7b GitHub OAuth と永続セッション

いまのセッションは `ServeState` 上のインメモリな `set[str]` にあり、再起動を越えられず、worker プロセスを
跨げません。7b はこれをデータベース（または Redis）へ移し、**Authlib による GitHub OAuth** を加えます。
login と callback の対、署名付き cookie セッション、そして OAuth の identity を `orgs`／`users` の upsert へ
解決する処理です。`operations` 層は token に依存しないままにし、認証は
[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
が置いたとおり handler/app のミドルウェアに残します。依存は `authlib` extra が担います。

#### 7c org 単位の RBAC、監査ログ、クォータ

最後のスライスがテナント分離を強制します。各ユーザは org の中でロール（viewer / editor / admin）を持ち、
すべてのエンドポイントが org スコープを検査します。org をまたぐアクセスは 403 を返します。`audit_log` には
誰が何をいつ実行したかを記録し、org 単位の並行クォータを enqueue 時に適用して、1 テナントが希少な Mac
プールを枯渇させないようにします。`ScenarioStore`、オブジェクトストレージ、`BaselineStore` にすでにある
テナント prefix へ、解決した `org_id` を渡します。これで成果物、シナリオ、ベースラインがすべて org スコープに
なります。契約は変えません。

## 検討した代替案

汎用 Linux PaaS（Cloud Run / Vercel / Linux 上の Fly Machines）は最初から却下です。Simulator が
macOS 上にしか存在しないため run を実行できず、Web UI をホスティングするとは必然的に Mac に縛られた
ランナーをホスティングすることだからです。それ以外も、選定スタックの各レイヤに却下した対があります。

- **API / Web**: Django（重い・同期前提）、Litestar、stdlib サーバ継続（認証/多人数に耐えない）。
  非同期でかつ既に依存ツリーにある Pydantic を再利用できる FastAPI を採って却下しました。
- **リバースプロキシ + TLS**: nginx + certbot（設定が多い）、Traefik。ほぼ無設定の自動 HTTPS を
  持つ Caddy を採って却下しました。
- **認証 / 認可**: oauth2-proxy（エッジ）、Auth0/Clerk/WorkOS（マネージド有償）、Google OAuth。
  対象が既に GitHub を持ち org モデルが GitHub org に対応する GitHub OAuth を採って却下しました。
- **system of record**: SQLite（多人数の並行に不可）、MySQL。JSONB を持ちマネージドが豊富な
  PostgreSQL を採って却下しました。
- **キュー / キャッシュ / pub-sub**: RabbitMQ/NATS（ブローカーのみ）、SQS（ブローカーのみ・pub/sub なし）。
  ブローカー・キャッシュ・pub/sub 配信を 1 つで賄う Redis を採って却下しました。
- **タスク基盤**: Celery（当初は機能過多）、Dramatiq。まず RQ で始めて却下しました。routing/retry/beat が
  必要になれば後で Celery を採用できます。
- **成果物ストレージ**: AWS S3（egress 課金）、MinIO（自前）、GCS。S3 互換で下り無料の
  Cloudflare R2 を採って却下しました。
- **macOS ワーカー**: AWS EC2 Mac（24h 最小割当・高価）、Scaleway Apple silicon、自前 Mac mini。
  クリーンな Mac のスケール可能でスケジュール可能なプールを得られる唯一の選択肢 MacStadium Orka を
  採って却下しました。
- **シークレット**: Vault（重い）、env ファイル（公開では不可）。クラウドのシークレット管理を
  採って却下しました。
- **可観測性**: OpenTelemetry collector、Datadog（有償）。Sentry + Prometheus/Grafana を基本線と
  する案に対する代替です。
- **IaC + CI/CD**: Pulumi、手動（不可）。リポジトリが既に Actions 上にあるため Terraform +
  GitHub Actions を採って却下しました。

## 参考

`bajutsu/serve/`、[ci](../../../docs/ja/ci.md)、[architecture](../../../docs/ja/architecture.md)、
[reporting](../../../docs/ja/reporting.md)、[cli](../../../docs/ja/cli.md#serve)、およびセルフホスト版の対
[BE-0016](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)。
