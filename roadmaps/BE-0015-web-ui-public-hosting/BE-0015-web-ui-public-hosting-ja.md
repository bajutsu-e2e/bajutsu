[English](BE-0015-web-ui-public-hosting.md) · **日本語**

# BE-0015 — Web UI の公開ホスティング

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0015](BE-0015-web-ui-public-hosting-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0015") |
| 実装 PR | [#105](https://github.com/bajutsu-e2e/bajutsu/pull/105), [#106](https://github.com/bajutsu-e2e/bajutsu/pull/106), [#108](https://github.com/bajutsu-e2e/bajutsu/pull/108), [#112](https://github.com/bajutsu-e2e/bajutsu/pull/112), [#117](https://github.com/bajutsu-e2e/bajutsu/pull/117), [#118](https://github.com/bajutsu-e2e/bajutsu/pull/118), [#119](https://github.com/bajutsu-e2e/bajutsu/pull/119), [#120](https://github.com/bajutsu-e2e/bajutsu/pull/120), [#121](https://github.com/bajutsu-e2e/bajutsu/pull/121), [#122](https://github.com/bajutsu-e2e/bajutsu/pull/122), [#127](https://github.com/bajutsu-e2e/bajutsu/pull/127), [#129](https://github.com/bajutsu-e2e/bajutsu/pull/129), [#130](https://github.com/bajutsu-e2e/bajutsu/pull/130), [#131](https://github.com/bajutsu-e2e/bajutsu/pull/131), [#132](https://github.com/bajutsu-e2e/bajutsu/pull/132), [#133](https://github.com/bajutsu-e2e/bajutsu/pull/133), [#134](https://github.com/bajutsu-e2e/bajutsu/pull/134), [#139](https://github.com/bajutsu-e2e/bajutsu/pull/139), [#143](https://github.com/bajutsu-e2e/bajutsu/pull/143), [#149](https://github.com/bajutsu-e2e/bajutsu/pull/149), [#150](https://github.com/bajutsu-e2e/bajutsu/pull/150), [#151](https://github.com/bajutsu-e2e/bajutsu/pull/151), [#152](https://github.com/bajutsu-e2e/bajutsu/pull/152), [#153](https://github.com/bajutsu-e2e/bajutsu/pull/153), [#156](https://github.com/bajutsu-e2e/bajutsu/pull/156), [#157](https://github.com/bajutsu-e2e/bajutsu/pull/157), [#159](https://github.com/bajutsu-e2e/bajutsu/pull/159) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md), [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は、公開ホスト版 Web UI の**設計とそれを実現するソフトウェア**であり、そのソフトウェアは
**すべて着地しました**。local/server パリティに加えて、認証、永続化、RBAC、監査、クォータ、そして
**マルチテナンシー**を備えたサーバ backend がすでに出荷されています（下記「移行」「永続化と認証」
「§8 マルチテナンシー」を参照）。残っているのは、実インフラ上への**運用デプロイ**です。すなわち、
control plane、macOS worker プール、データベース、オブジェクトストレージのプロビジョニング、本番の
認証とシークレットの配線、そして稼働環境のセキュリティ項目のクローズです。このデプロイは種類の違う
作業であり（インフラと運用であって、有料かつ macOS 限定のキャパシティに左右されます）、
[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md) や
[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
がこのアンブレラから切り出されたのと同じやり方で、独立した項目「ホスト版 Web UI サービスのデプロイ」
として別に追跡します。本提案は、ローカルの `bajutsu serve`（`bajutsu/serve/`）を**共有の公開サービス**に
するための、サーバ、DB、ストレージ、デプロイの具体的な技術選定です。
現状の UI は Tier 1 の便利機能で、`127.0.0.1` にバインドし、認証はなく、同一ホスト上で
`bajutsu run` を subprocess 起動するだけです（[cli](../../docs/ja/cli.md#serve) ·
[reporting](../../docs/ja/reporting.md)）。公開によって変わるのはアドレスだけではなく、**システムの
形そのもの**です。Web UI は**薄いランチャー**なので、これをホスティングするとは実質「**ランナーを
ホスティングする**」ことであり、それは Linux のコントロールプレーンと macOS ワーカープールから
成る共有公開サービスへとつながります。

関連: [architecture](../../docs/ja/architecture.md) · [ci](../../docs/ja/ci.md) · セルフホスト版の対
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)。

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
   └──────────────────────────┘  HTTP  └───────────────────────────────┘
       安価・水平スケール               高価・macOS 限定・隔離が必要
```

安価でステートフルなマルチユーザ部分（認証、履歴、キュー、レポート閲覧）は Linux に置きます。高価で
macOS 限定の部分は、ジョブを受け取り、クリーンな Simulator で実行し、ログを流し、成果物を
アップロードするだけのステートレスなワーカーに絞り込みます。これが中心的なリファクタです。
**`serve` のプロセス内 `subprocess.Popen` を、ブローカーに積むジョブへ変え、リモートワーカーが
消費する形にします。**

## 詳細設計

### 選定スタック（推奨）

| レイヤ | 採用 | 採用理由 | 主な代替 |
|---|---|---|---|
| **API / Web** | **FastAPI** + **Uvicorn**（本番は Gunicorn + uvicorn worker） | 非同期（ライブログの SSE（Server-Sent Events）/WebSocket）、Pydantic は**既に依存**（[pyproject](../../pyproject.toml)）、OpenAPI 自動生成、コアと同じ Python | Django（重く同期前提）、Litestar、stdlib 継続（認証/多人数に耐えない） |
| **フロント** | `serve` の**単一ページ UI を踏襲**し API から配信。認証とプロジェクト選択を追加 | UI は既に 1 枚の HTML 文字列。v1 に SPA（シングルページアプリケーション）ビルドは不要 | UI が育てば後で React/Svelte |
| **リバースプロキシ + TLS** | **Caddy** | Let's Encrypt 自動 HTTPS をほぼ無設定で実現。プロキシとヘッダ設定も簡潔 | nginx + certbot（設定が多い）、Traefik |
| **認証 / 認可** | **OAuth2（GitHub プロバイダ）**（**Authlib**）、署名 Cookie セッション、org 単位 RBAC（ロールベースアクセス制御） | 対象は開発者（GitHub を持つ）。パスワードを保持しない。org モデルが GitHub org に対応 | oauth2-proxy（エッジ）、Auth0/Clerk/WorkOS（マネージド有償）、Google OAuth |
| **system of record** | **PostgreSQL 16** + **SQLAlchemy 2.0** + **Alembic** | リレーショナルな核（org/user/project/run）と manifest 要約用の **JSONB**。マネージドが豊富（RDS/Cloud SQL/Neon/Supabase） | SQLite（多人数の並行に不可）、MySQL |
| **キュー / ジョブ分配** | **Postgres `jobs` テーブル**（HTTP で lease） | worker が `POST /api/worker/lease` をポーリングし、制御プレーンが `SELECT … FOR UPDATE SKIP LOCKED` で lease します。ブローカプロセスは不要です。Redis 7 / RQ から置き換え（[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md)） | Redis/RQ（削除済み）、RabbitMQ/NATS、SQS |
| **セッション** | **Postgres `sessions` テーブル** | system of record と同じデータベースでセッションを保持します。再起動をまたぎ、レプリカ間で共有されます。Redis セッションストアから置き換え（[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md)） | Redis（削除済み）、署名つき Cookie |
| **成果物ストレージ** | **Cloudflare R2**（S3 互換） | run ツリー（`report.html`、スクショ、**動画**、`network.json`）は大きなバイナリです。**Postgres に入れません**。R2 は**下り無料** | AWS S3（egress 課金）、MinIO（自前）、GCS |
| **macOS ワーカー** | **MacStadium Orka** | macOS VM オーケストレーション専用（「Mac 版 k8s」）。クリーンな Mac の**スケール可能でスケジュール可能なプール**を得られる唯一の選択肢 | AWS EC2 Mac（24h 最小割当で高価）、Scaleway Apple silicon、自前 Mac mini |
| **シークレット** | クラウドのシークレット管理（**Doppler** / プラットフォーム純正: Fly/AWS Secrets Manager） | 集中ローテーション。org ごとに **`ANTHROPIC_API_KEY` を各自持ち込み（BYO: Bring Your Own）**（`--dismiss-alerts`、`record` のコスト/悪用を org 単位で限定） | Vault（重い）、env ファイル（公開では不可） |
| **可観測性** | **Sentry**（エラー）+ **Prometheus/Grafana**（メトリクス）+ 構造化 JSON ログ | 標準的で安価、ホステッドあり | OpenTelemetry collector、Datadog（有償） |
| **IaC（Infrastructure as Code）+ CI/CD（継続的インテグレーション/継続的デリバリ）** | **Terraform** + **GitHub Actions** → **GHCR**（GitHub Container Registry）イメージ | 再現可能なインフラ。リポジトリは既に Actions 上（[ci](../../docs/ja/ci.md)） | Pulumi、手動（不可） |

### 各構成要素の役割

#### コントロールプレーン（Linux、安価、水平スケール）
今日の `serve` の発展形です。エンドポイント（すべて認証付き）:

- `GET /` → プロジェクト単位の UI（scenario/app の候補は**ファイルシステムではなく DB** から）。
- `POST /api/run` → リクエストを**呼び出し元のプロジェクトに対して検証**します（クライアント指定の
  ファイルパスは禁止。セキュリティ参照）。`run` 行を書き、**ジョブを enqueue**（Postgres `jobs`
  テーブルの `queued` 行）して id を返します。
- `GET /api/runs/stream/<id>` → ライブログの **SSE** ストリーム（現 UI の 1 秒ポーリングを置換）。
  `LogBus` シーム経由です（[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md)
  が当初ここで想定していた Redis pub/sub を置き換えました）。
- `GET /runs/<id>/…` → レポート資産を **短命の署名付き R2 URL** で配信（現状のローカル
  `_serve_run_file` を置換）。

#### ジョブ分配（コントロールプレーン ↔ ワーカー）

ジョブ分配には Postgres の `jobs` テーブルを HTTP 越しに lease する方式を使います
（[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md)）。
run は `queued` の行になり、worker が `POST /api/worker/lease` でポーリングして lease します。run
完了後、worker は run ツリー（`console.log` 含む）をオブジェクトストレージにアップロードし、結果を
`POST /api/worker/result` に返します。制御プレーンが完了した run を記録するので、worker はデータベースへの
アクセスを必要としません。Redis や RQ は不要です。

#### macOS ワーカー（ステートレス、隔離、使い捨て）
Orka が払い出す各 Mac 上の小さな Python エージェント（launchd サービス）:

1. HTTP でジョブを lease します（`POST /api/worker/lease`）。
2. そのプロジェクトの**シナリオをコントロールプレーン/オブジェクトストアから取得**します
   （クライアントが選んだパスは使いません）。
3. **消去済みのクリーンな Simulator** を用意します（`bajutsu run --erase`）。公開マルチテナントは
   **隔離必須**で、これはローカル UI の高速 `--no-erase` 再利用ループを意図的に捨てることを意味します。
4. stdout をコントロールプレーンの `LogBus` に流し、完了時に **`runs/<id>/` ツリーを R2 に
   アップロード**し、結果（exit code、run id、manifest 要約）をコントロールプレーンへ `POST` します。
5. Simulator を破棄します。

### デプロイ計画（段階的）

#### フェーズ 1：MVP、最短で出す
- **コントロールプレーン**をコンテナ化し、**Fly.io**（or Render）へ。マネージドの **Fly Postgres**
  （ジョブキューとセッションもここに載ります）。成果物は **Cloudflare R2**。TLS はプラットフォーム/Caddy。**GitHub OAuth**。
- **ワーカー**: **MacStadium Orka 1 ノード**でエージェント稼働。シナリオや app 設定はプロジェクト
  単位で Postgres/R2 に保存。
- **シークレット**は Fly/Doppler。各 org が自分の `ANTHROPIC_API_KEY` を持ち込みます。
- ゴール: ログイン済みユーザがプロジェクト + シナリオを選び Run を実行し、ライブログを見て、レポートを
  閲覧します。共有インフラ上で安全に、end-to-end で行えるようにします。

#### フェーズ 2：スケール
- コントロールプレーン → **Kubernetes**（GKE/EKS）、マネージド **Cloud SQL/RDS**。
- Postgres `jobs` のキュー深さ駆動の **Orka オートスケール** Mac プール。org ごとの**並行数クォータ**。
- R2 前段に成果物 **CDN（コンテンツデリバリネットワーク）**。必要ならコントロールプレーンをマルチリージョン化。
- フル可観測性（Sentry + Grafana ダッシュボード + キュー深さ/ワーカー健全性のアラート）。

#### コストの実情
Linux のコントロールプレーン、Postgres、R2 は**安価で弾力的**です。一方 **Mac が費用を支配**し、
きれいにゼロスケールしません（Orka ノード / EC2 Mac の 24h 最小）。プールは**少数のウォームフロア +
キュー深さ**で設計し、決定的な*ゲート*は使い捨て CI（[ci](../../docs/ja/ci.md)）に寄せて、ホステッド
プールは回帰テストの物量ではなく**対話的オーサリングだけ**を担わせます。

### セキュリティ強化（公開前に必須）

現 `serve` が安全なのは localhost 限定かつ単一ユーザだからです。公開はこの両方の前提を壊すため、以下は
任意ではありません。

- **全エンドポイントに認証**（OAuth + org 単位 RBAC）。run 起動をユーザ/org 単位で**レート制限**します。
- **任意パスでのシナリオ実行を排除**します。現状 `/api/run` は `body["scenario"]` を、`scenarios_dir`
  内かの検査なしに `bajutsu run` の argv へそのまま渡します（`bajutsu/serve/` の
  `run_command`/`do_POST`）。ホステッドでは**シナリオはプロジェクト単位で保存し worker が id で
  取得**します。クライアントはパスを指定せず、`backend`/`udid` も自由文字列でなく**許可リスト**で検証します。
- **org ごとの BYO `ANTHROPIC_API_KEY`**。AI 機能（`--dismiss-alerts`、`record`）のコスト/悪用を
  鍵の持ち主の org に限定します。
- **ワーカーのサンドボックス化**。シナリオは実質「デバイスを操作する非信頼コード」です。各 run を
  **使い捨て Mac/Simulator** で、**egress 許可リスト**付き、**テナント間でシークレット共有なし**で実行します。
- **署名付きで期限の切れる成果物 URL**、**CORS/CSRF** 対策、標準セキュリティヘッダ、そして誰が何をいつ
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

[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
が認証と入力検証を出荷済みで、純粋ヘルパ（`list_scenarios`、`list_runs`、`run_command`、`Job` モデル）も
すでに `bajutsu/serve/helpers.py` に切り出されています。したがって:

1. 切り出し済みの純粋ヘルパと埋め込み HTML を FastAPI アプリへ移し、v1 フロントとします。
2. **キュー型の `RunExecutor`**（Postgres `jobs` テーブルへ enqueue）と、今 `run_job` がビルドするのと
   同じ `bajutsu run` argv を実行する worker エントリポイントを用意します。
3. **サーバ版の `LogBus`** と、ローカルのファイル読み取りを **R2 署名付き URL のリダイレクト**に
   置き換える **オブジェクトストレージの `ArtifactStore`** を用意します。
4. **OAuth + Postgres**（org/project/run）と、ファイルシステムではなくストレージから id で解決する
   **プロジェクト単位の `ScenarioStore`** を追加します。**シングルテナント backend 向けに出荷済み**で、下記の
   7a/7b/7c が着地分と今後を詳述します。
5. **Orka ワーカー 1 台**を立て、上記セキュリティ項目を閉じてから、プールをスケールします。

各ステップは独立に出荷でき、テストもできます。決定的コア（`bajutsu run` とレポート）は終始不変で、変わるのは
その*起動方法*と*配管*がクラウドへ移ることだけです。

### 永続化と認証（シングルテナント backend に出荷済み、7a/7b/7c）

移行のステップ 1〜3（5 つの差し替え可能な seam）は着地済みで、**ステップ 4、すなわち永続化と認証も、
シングルテナントのサーバ backend 向けに出荷されました**。次の 3 つの不変条件は維持されています。ローカルの
挙動を変えないこと。各スライスのテストを Linux ゲートで完結させること（Simulator も、稼働中の Postgres、
オブジェクトストレージも要らない）。各スライスを既存の seam パターンに倣わせること（Protocol と注入
実装の組で、optional extra の背後に遅延 import し、既定の `serve`/CLI 経路では読み込まない）。ここでの
「シングルテナント」は、固定の default org に全ユーザが属する形を指します。マルチテナントの org スコープ部分は
この backend の上にあとから着地しました（後述の「§8 マルチテナンシー」を参照）。

#### 7a 永続化レイヤ（#144、#145）

5 本目の seam `Repository`（`bajutsu/serve/server/db.py`）を、`ObjectStore` と同じ作りで置きました。Protocol、
注入する SQLAlchemy 2.0 実装、環境変数から組み立てるファクトリ、遅延 import です。テーブル一式と外部キーは
最初の Alembic マイグレーションで決め切っています（後から足すのは、ゲートが使う SQLite では苦痛だからです）。
`users.role` は後続のマイグレーション（0002）で追加しました。

```
orgs       id, slug（一意）, name, created_at
users      id, org_id → orgs, email（一意）, github_login, role, created_at
projects   id, org_id → orgs, name（= config のアプリ名）, created_at, unique(org_id, name)
runs       id, org_id → orgs, project_id → projects, created_by → users,
           status, ok, created_at, summary（JSONB）
audit_log  id, org_id → orgs, actor_id → users, action, target, at, detail（JSONB）
```

`org_id` を全テーブルに通してあるのは、マルチテナント化したときに org 単位で絞り込めるようにするためです。
可変な manifest 要約と監査の詳細だけを `JSON().with_variant(JSONB, "postgresql")` にしているので、同じモデルが
SQLite（ゲート）でも Postgres（本番）でも動きます。配線は seam を組み立てる唯一の場所 `_build_server_state` に
置き、`BAJUTSU_DATABASE_URL` で切り替えます。未設定のとき、およびローカル backend では `repository` は `None` の
ままで、データベースなしでも挙動は変わりません。`db` extra が `sqlalchemy`、`alembic`、`psycopg` を担います。
（`Repository` は `record_run`／`get_run`／`list_runs` を備えます。run の一覧は、データベースを配線したら DB から
返し（後述の 7c-4）、無いときはオブジェクトストレージから取得します。）

#### 7b GitHub OAuth と永続セッション（#148、#149）

セッションをインメモリの `set[str]` から `SessionStore` seam へ移しました。ローカルはインメモリ（再起動で
消える）、サーバは TTL 付きの永続ストア（再起動を越え、レプリカを跨ぐ）です（当初は Redis。その後
[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md) が
このサーバ側ストアを Postgres `sessions` テーブルへ移しました）。**Authlib による GitHub OAuth** を、
ブラウザ向けの追加のサインイン手段として加えました。`/api/oauth/login` と `/api/oauth/callback`、CSRF state
cookie、そして不透明な session id を入れた HttpOnly cookie（サーバ側ストアで検証）からなり、
**GitHub username の許可リスト**（`BAJUTSU_OAUTH_ALLOWED_USERS`）で
制限し、ログインした login をセッションの identity として紐付けます。共有トークン（BE-0051）とは共存し、
トークンはオペレータの認証（full access、CI など）、OAuth はユーザごとのブラウザログインです。`operations` 層は
provider に依存せず、認証は handler/app のミドルウェアに置きます。依存は `authlib` extra が担います。

#### 7c identity、RBAC、監査、クォータ（シングルテナント）（#150、#151、#152）

データベースを配線したサーバ backend で動きます。

- **identity 永続化と監査ログ**（#150）: OAuth ログインがユーザを default org に upsert し、run/record/crawl が
  「誰が、何を、いつ」を `audit_log` に記録します。
- **per-user RBAC**（#151）: 各ユーザに role を持たせます。viewer（閲覧のみ）、editor（run/record/crawl/approve/
  保存）、admin（config、API キー、provider）です。role は env 方針（`BAJUTSU_OAUTH_ADMINS`／
  `BAJUTSU_OAUTH_VIEWERS`、既定 editor）から毎回のログイン時に再計算するので、方針変更にデータマイグレーションは
  要りません。強制は認証と同じ gate 層に置き、gate されるのは OAuth セッションだけで、オペレータのトークンは
  full access のままです。
- **per-user 並行クォータ**（#152）: `BAJUTSU_MAX_CONCURRENT_PER_USER` が 1 ユーザの同時実行数を抑え、誰も
  希少なデバイスプールを枯渇させないようにします（org が 1 つの間、per-org クォータは既存のグローバル
  `max_concurrent` と同義です）。
- **DB からの run 一覧**（7c-4）: 完了した run を `runs` テーブルに記録し（id、org、起動したユーザ、合否、
  manifest サマリ）、run 履歴のエンドポイントは artifact ストアを走査せずに、その org の記録済み run を返します。
  データベースが無いとき（local／stdlib serve）は従来どおり artifact ストアから直接読むので、挙動は変わりません。
  一覧はすでに org スコープで、org 解決が入るまでは単一の default org に解決されるだけです。

#### 8 マルチテナント（org モデル、解決、強制、org ごとのストレージ）

シングルテナント backend の上に、実マルチテナントを載せました。org モデルは **config で宣言**するので、
app-agnostic を保ちます。

```yaml
orgs:
  acme:
    members: [alice, bob]      # 明示の GitHub login、および／または…
    githubOrgs: [acme-gh]      # …この GitHub org の全員
    apps: [demo, checkout]
  globex:
    members: [carol]
    apps: [other]
```

どの org にも挙げられていない login や app は単一の `default` org に入るので、`orgs:` ブロックが無い config は
シングルテナントのままで、出荷済みの挙動は変わりません。

- **org 解決（8b）**: OAuth ログイン時にユーザを config の org に割り当て、user 行に保存します。以後のリクエストは
  その行から actor の org を解決します（`ServeState.org_of`）。org は、明示の `members` 列挙か、それが無ければ
  ユーザの **GitHub org メンバーシップ**から決まります（`org_for_identity`）。OAuth フローは `read:org` を要求して
  `/user/orgs` を読み、`githubOrgs` エントリが GitHub org を bajutsu org に対応づけます。run 一覧、run レコード、
  監査エントリは解決した org の下で読み書きします。
- **org スコープの強制（8c）**: ユーザは自 org の apps だけが見え、別 org の app での run/record/crawl 開始や保存は
  403、別 org の scenario や run 成果物の読み取りは not-found（存在を漏らさない）になります。
- **org ごとのストレージ（8d）**: サーバ backend は各 org の artifacts／scenarios／baselines を自分の
  オブジェクトストレージ prefix（`<base><org>/`）の下に置きます。default org は base prefix のままなので、
  シングルテナントのレイアウトは変わりません。org は job spec で運ばれ、worker は制御プレーンが配信するのと同じ
  prefix を読み書きします。

サーバ backend の run は worker で実行されるので、それを system of record に記録するのは **worker** です。worker に
`db` extra と `BAJUTSU_DATABASE_URL` を与えれば、完了した run はその org／actor の下に記録されます（無ければ no-op
＝run は動くが一覧には出ません）。

## 検討した代替案

汎用 Linux PaaS（Cloud Run / Vercel / Linux 上の Fly Machines）は最初から却下です。Simulator が
macOS 上にしか存在しないため run を実行できず、Web UI をホスティングするとは必然的に Mac に縛られた
ランナーをホスティングすることだからです。それ以外も、選定スタックの各レイヤに却下した対があります。

- **API / Web**: Django（重く同期前提）、Litestar、stdlib サーバ継続（認証/多人数に耐えない）。
  非同期でかつ既に依存ツリーにある Pydantic を再利用できる FastAPI を採って却下しました。
- **リバースプロキシ + TLS**: nginx + certbot（設定が多い）、Traefik。ほぼ無設定の自動 HTTPS を
  持つ Caddy を採って却下しました。
- **認証 / 認可**: oauth2-proxy（エッジ）、Auth0/Clerk/WorkOS（マネージド有償）、Google OAuth。
  対象が既に GitHub を持ち org モデルが GitHub org に対応する GitHub OAuth を採って却下しました。
- **system of record**: SQLite（多人数の並行に不可）、MySQL。JSONB を持ちマネージドが豊富な
  PostgreSQL を採って却下しました。
- **キュー / ジョブ分配**: 当初は Redis 7 + RQ を採用していました（ブローカー専用の代替として
  RabbitMQ/NATS・SQS、より重いタスク基盤として Celery/Dramatiq もありました）。しかし
  [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md) が、
  これを **HTTP 越しに lease する Postgres `jobs` テーブル**へ置き換えました。ブローカー・キャッシュ・
  タスク基盤の別プロセスは不要になり、セッションも Postgres テーブルへ移りました。
- **成果物ストレージ**: AWS S3（egress 課金）、MinIO（自前）、GCS。S3 互換で下り無料の
  Cloudflare R2 を採って却下しました。
- **macOS ワーカー**: AWS EC2 Mac（24h 最小割当で高価）、Scaleway Apple silicon、自前 Mac mini。
  クリーンな Mac のスケール可能でスケジュール可能なプールを得られる唯一の選択肢 MacStadium Orka を
  採って却下しました。
- **シークレット**: Vault（重い）、env ファイル（公開では不可）。クラウドのシークレット管理を
  採って却下しました。
- **可観測性**: OpenTelemetry collector、Datadog（有償）。Sentry + Prometheus/Grafana を基本線と
  する案に対する代替です。
- **IaC + CI/CD**: Pulumi、手動（不可）。リポジトリが既に Actions 上にあるため Terraform +
  GitHub Actions を採って却下しました。

## 進捗

- [x] 7a。永続化層（`Repository`、`bajutsu/serve/server/db.py`）（[#144](https://github.com/bajutsu-e2e/bajutsu/pull/144)、[#145](https://github.com/bajutsu-e2e/bajutsu/pull/145)）。
- [x] 7b。GitHub OAuth（Authlib）と永続セッション（[#148](https://github.com/bajutsu-e2e/bajutsu/pull/148)、[#149](https://github.com/bajutsu-e2e/bajutsu/pull/149)）。
- [x] 7c。単一テナントのバックエンドに対する識別・RBAC・監査・クォータ（[#150](https://github.com/bajutsu-e2e/bajutsu/pull/150)〜[#152](https://github.com/bajutsu-e2e/bajutsu/pull/152)）。
- [x] 8。マルチテナンシー。組織モデル、リクエストの解決、強制、組織ごとのストレージ
  （[#156](https://github.com/bajutsu-e2e/bajutsu/pull/156)、[#159](https://github.com/bajutsu-e2e/bajutsu/pull/159)）。

マルチテナンシーまで含めて、それを実現するソフトウェアはすべて着地したので（#105〜#159）、この
設計とソフトウェアの項目は**実装済み**です。残る公開サービスの**運用デプロイ**（Phase 1 の MVP と
Phase 2 のスケール、および稼働環境のセキュリティハードニング）は、独立した項目「ホスト版 Web UI
サービスのデプロイ」として別に追跡します。

## 参考

`bajutsu/serve/`、[ci](../../docs/ja/ci.md)、[architecture](../../docs/ja/architecture.md)、
[reporting](../../docs/ja/reporting.md)、[cli](../../docs/ja/cli.md#serve)、セルフホスト版の対
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)、そして
[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging-ja.md) — 上記「構造化 JSON ログ」の可観測性行を実体化する運用ログを設計します。
