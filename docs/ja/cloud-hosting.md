[English](../cloud-hosting.md) · **日本語**

# Web UI のクラウドホスティング（公開 / マルチテナント）

> 将来構想 — **未実装**。ローカルの `bajutsu serve`（[serve.py](../../bajutsu/serve.py)）を
> **共有・公開サービス**化するための、サーバ・DB・ストレージ・デプロイの具体的な技術選定。
> 現状の UI は Tier 1 の便利機能で、`127.0.0.1` にバインドし、認証はなく、同一ホスト上で
> `bajutsu run` を subprocess 起動するだけ（[cli](cli.md) · [reporting](reporting.md)）。公開は
> アドレスの変更ではなく、**システムの形そのもの**を変える。

関連: [architecture](architecture.md) · [ci](ci.md) · [roadmap](roadmap/README.md)

---

## すべてを規定する唯一の制約

Web UI は**薄いランチャー**にすぎない。`/api/run` は `python -m bajutsu run …` を起動し、それが
`idb` + `simctl` 経由で **iOS Simulator** を駆動する。そして Simulator は **macOS 上にしか存在
しない**。つまり「Web UI をホスティング」とは実態として「**ランナーをホスティング**」であり、
ランナーには Mac が要る。汎用 Linux PaaS（Cloud Run / Vercel / Linux 上の Fly Machines）では
run を一切実行できない。

このため、現状の単一プロセス設計には無い**分離トポロジ**が必須になる:

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

安価でステートフルなマルチユーザ部分（認証・履歴・キュー・レポート閲覧）は Linux に置く。高価で
macOS 限定の部分は、「ジョブを受け取り、クリーンな Simulator で実行し、ログを流し、成果物を
アップロードする」だけのステートレスなワーカーに絞り込む。これが中心的なリファクタ:
**`serve.py` のプロセス内 `subprocess.Popen` を、ブローカーに積むジョブへ変え、リモートワーカーが
消費する**。

---

## 選定スタック（推奨）

| レイヤ | 採用 | 採用理由 | 主な代替 |
|---|---|---|---|
| **API / Web** | **FastAPI** + **Uvicorn**（本番は Gunicorn + uvicorn worker） | 非同期（ライブログの SSE（Server-Sent Events）/WebSocket）、Pydantic は**既に依存**（[pyproject](../../pyproject.toml)）、OpenAPI 自動生成、コアと同じ Python | Django（重い・同期前提）、Litestar、stdlib 継続（認証/多人数に耐えない） |
| **フロント** | `serve.py` の**単一ページ UI を踏襲**し API から配信。認証とプロジェクト選択を追加 | UI は既に 1 枚の HTML 文字列。v1 に SPA（シングルページアプリケーション）ビルドは不要 | UI が育てば後で React/Svelte |
| **リバースプロキシ + TLS** | **Caddy** | Let's Encrypt 自動 HTTPS をほぼ無設定で。プロキシ + ヘッダも素直 | nginx + certbot（設定が多い）、Traefik |
| **認証 / 認可** | **OAuth2 — GitHub**（**Authlib**）、署名 Cookie セッション、org 単位 RBAC（ロールベースアクセス制御） | 対象は開発者（GitHub を持つ）。パスワードを保持しない。org モデルが GitHub org に対応 | oauth2-proxy（エッジ）、Auth0/Clerk/WorkOS（マネージド有償）、Google OAuth |
| **system of record** | **PostgreSQL 16** + **SQLAlchemy 2.0** + **Alembic** | リレーショナルな核（org/user/project/run）+ manifest 要約を **JSONB**。マネージドが豊富（RDS/Cloud SQL/Neon/Supabase） | SQLite（多人数の並行に不可）、MySQL |
| **キュー / キャッシュ / pub-sub** | **Redis 7** | 1 つで 3 役: **ジョブブローカー**・キャッシュ・ライブログの **pub/sub 配信**（worker → Redis → SSE） | RabbitMQ/NATS（ブローカーのみ）、SQS（pub/sub なし） |
| **タスク基盤** | まず **RQ**（Redis Queue） | 小さく Redis ネイティブで読みやすい。「`bajutsu run` を積んで worker が消費」に合致 | Celery（routing/retry/beat が要るとき）、Dramatiq |
| **成果物ストレージ** | **Cloudflare R2**（S3 互換） | run ツリー（`report.html`・スクショ・**動画**・`network.json`）は大きなバイナリ。**Postgres に入れない**。R2 は**下り無料** | AWS S3（egress 課金）、MinIO（自前）、GCS |
| **macOS ワーカー** | **MacStadium Orka** | macOS VM オーケストレーション専用（"Mac 版 k8s"）。クリーンな Mac の**スケール可能なプール**を得られる唯一の選択 | AWS EC2 Mac（24h 最小割当・高価）、Scaleway Apple silicon、自前 Mac mini |
| **シークレット** | クラウドのシークレット管理（**Doppler** / プラットフォーム純正） | 集中ローテーション。org ごとに **`ANTHROPIC_API_KEY` を各自持ち込み（BYO: Bring Your Own）**（`--dismiss-alerts`・`record` のコスト/悪用を限定） | Vault（重い）、env ファイル（公開では不可） |
| **可観測性** | **Sentry**（エラー）+ **Prometheus/Grafana**（メトリクス）+ 構造化 JSON ログ | 標準・安価・ホステッド有り | OpenTelemetry collector、Datadog（有償） |
| **IaC（Infrastructure as Code）+ CI/CD（継続的インテグレーション/継続的デリバリ）** | **Terraform** + **GitHub Actions** → **GHCR** イメージ | 再現可能なインフラ。リポジトリは既に Actions 上（[ci](ci.md)） | Pulumi、手動（不可） |

---

## 各構成要素の役割

### コントロールプレーン（Linux・安価・水平スケール）
今日の `serve.py` の発展形。エンドポイント（すべて認証付き）:

- `GET /` → プロジェクト単位の UI（scenario/app の候補は**ファイルシステムではなく DB** から）。
- `POST /api/run` → リクエストを**呼び出し元のプロジェクトに対して検証**（クライアント指定の
  ファイルパスは禁止 — セキュリティ参照）。`run` 行を書き、**RQ ジョブを enqueue** して id を返す。
- `GET /api/runs/stream/<id>` → ライブログの **SSE** ストリーム（現 UI の 1 秒ポーリングを置換）。
  worker が PUBLISH する Redis チャネルを購読。
- `GET /runs/<id>/…` → レポート資産を **短命の署名付き R2 URL** で配信（現状のローカル
  `_serve_run_file` を置換）。

### ジョブキュー（コントロールプレーン ↔ ワーカー）
Redis をブローカーに。run は `{run_id, project, scenario_ref, app, options, byo_key_ref}` の
ジョブになる。worker が `BRPOP`/lease する。worker はログ行と状態を run 単位の Redis チャネルに
`PUBLISH` し、コントロールプレーンの SSE がそれを購読する。

### macOS ワーカー（ステートレス・隔離・使い捨て）
Orka が払い出す各 Mac 上の小さな Python エージェント（launchd サービス）:

1. Redis からジョブを lease。
2. そのプロジェクトの**シナリオをコントロールプレーン/オブジェクトストアから取得**
   （クライアントが選んだパスは使わない）。
3. **消去済みのクリーンな Simulator** を用意（`bajutsu run --erase`）。公開マルチテナントは
   **隔離必須**で、これはローカル UI の高速 `--no-erase` 再利用ループを意図的に捨てることを意味する。
4. stdout を Redis に流し、完了時に **`runs/<id>/` ツリーを R2 にアップロード**し、結果（exit code・
   run id・manifest 要約）をコントロールプレーンへ `POST`。
5. Simulator を破棄。

---

## デプロイ計画（段階的）

### フェーズ 1 — MVP・最短で出す
- **コントロールプレーン**をコンテナ化 → **Fly.io**（or Render）。マネージドの **Fly Postgres** +
  **Upstash Redis**。成果物は **Cloudflare R2**。TLS はプラットフォーム/Caddy。**GitHub OAuth**。
- **ワーカー**: **MacStadium Orka 1 ノード**でエージェント稼働。シナリオ・app 設定はプロジェクト
  単位で Postgres/R2 に保存。
- **シークレット**は Fly/Doppler。各 org が自分の `ANTHROPIC_API_KEY` を持ち込む。
- ゴール: ログイン済みユーザがプロジェクト+シナリオを選び Run、ライブログを見て、レポートを閲覧する
  — 共有インフラ上で安全に、end-to-end で。

### フェーズ 2 — スケール
- コントロールプレーン → **Kubernetes**（GKE/EKS）、マネージド **Cloud SQL/RDS** +
  **ElastiCache/Upstash**。
- キュー深さ駆動の **Orka オートスケール** Mac プール。org ごとの**並行数クォータ**。
- R2 前段に成果物 **CDN**。必要ならコントロールプレーンをマルチリージョン化。
- フル可観測性（Sentry + Grafana ダッシュボード + キュー深さ/ワーカー健全性のアラート）。

### コストの実情（正直なところ）
Linux のコントロールプレーン・Postgres・Redis・R2 は**安価で弾力的**。一方 **Mac が費用を支配**し、
きれいにゼロスケールしない（Orka ノード / EC2 Mac の 24h 最小）。プールは**少数のウォームフロア +
キュー深さ**で設計し、決定的な*ゲート*は使い捨て CI（[ci](ci.md)）に寄せて、ホステッドプールは
回帰の物量ではなく**対話的オーサリングだけ**を担わせる。

---

## セキュリティ強化（公開前に必須）

現 `serve.py` が安全なのは localhost 限定かつ単一ユーザ*だから*。公開は両方の前提を壊すので、以下は
任意ではない:

- **全エンドポイントに認証**（OAuth + org 単位 RBAC）。run 起動をユーザ/org 単位で**レート制限**。
- **任意パスでのシナリオ実行を排除**。現状 `/api/run` は `body["scenario"]` を、`scenarios_dir`
  内かの検査なしに `bajutsu run` の argv へそのまま渡す（[serve.py](../../bajutsu/serve.py) の
  `run_command`/`do_POST`）。ホステッドでは**シナリオはプロジェクト単位で保存し worker が id で
  取得**する。クライアントはパスを名指しせず、`backend`/`udid` も自由文字列でなく**許可リスト**で検証。
- **org ごとの BYO `ANTHROPIC_API_KEY`**。AI 機能（`--dismiss-alerts`・`record`）のコスト/悪用を
  鍵の持ち主の org に限定。
- **ワーカーのサンドボックス化**。シナリオは実質「デバイスを操作する非信頼コード」。各 run を
  **使い捨て Mac/Simulator** で、**egress 許可リスト**付き、**テナント間でシークレット共有なし**で実行。
- **署名付き・期限切れの成果物 URL**。**CORS/CSRF** 対策。標準セキュリティヘッダ。誰が何をいつ
  実行したかの**監査ログ**。
- **org 単位のクォータ/並行上限**。1 テナントが希少で高価な Mac プールを枯渇させないため。

---

## `serve.py` からの移行（ゼロからの書き直しではなく段階的に）

1. 純粋ヘルパ（`list_scenarios`・`list_runs`・`run_command`・`Job` モデル）を FastAPI アプリへ移し、
   既存 HTML を v1 フロントとして流用。
2. プロセス内 `run_job` を **enqueue → RQ** に置換。今ビルドしているのと同じ `bajutsu run` argv を
   実行する worker エントリポイントを追加。
3. ポーリングを **Redis pub/sub 上の SSE** に、ローカル `_serve_run_file` を **R2 署名付き URL** に置換。
4. **OAuth + Postgres**（org/project/run）を追加。シナリオ/app ソースをファイルシステムから
   プロジェクト単位ストレージへ移す。
5. **Orka ワーカー 1 台**を立て、上記セキュリティ項目を閉じてから、プールをスケール。

各ステップは独立に出荷・テスト可能で、決定的コア（`bajutsu run`・レポート）は終始不変 — 変わるのは
その*起動方法*と*配管*がクラウドへ移ることだけ。
