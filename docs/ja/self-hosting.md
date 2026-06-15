[English](../self-hosting.md) · **日本語**

# Web UI のセルフホスティング（自前ハードウェア）

> 将来構想 — **未実装**。[cloud-hosting](cloud-hosting.md) がマネージドなマルチテナント公開スタックを
> 選定するのに対し、本ページは Web UI を**自前の Mac で立ち上げて稼働させる**方法を扱う。2 段階を
> 文書化する: **(A)** 既存の stdlib `bajutsu serve`（[serve.py](../../bajutsu/serve.py)）で*今日*動く
> もの、**(B)** [cloud-hosting](cloud-hosting.md) の将来のマルチテナント版を、各マネージドサービスを
> セルフホスト OSS に置き換えて完全自前化したもの。

関連: [cloud-hosting](cloud-hosting.md) · [cli](cli.md) · [ci](ci.md)

---

## セルフホストを規定する macOS の落とし穴

ランナーは **iOS Simulator** を駆動し、Simulator には **GUI ログインセッション**（WindowServer /
Aqua セッション）が要る —— ヘッダレスな daemon では**動かない**。以下のセルフホスト方針はすべて
これに由来する:

- プロセスは **`LaunchAgent`**（ユーザ単位・GUI セッション）で動かす。**`LaunchDaemon` ではない**。
- 再起動後に GUI セッションを回復できるよう Mac は**自動ログイン**にする（FileVault 有効時は、
  コールドブート後に一度だけ対話ログインしてから自動ログインが進む点に注意）。
- セッションを生かし続けるため**スリープを無効化**する（`caffeinate` / `pmset`）。

これが、bajutsu のホスティングが通常の Web サービスのホスティングと運用上異なる点。

---

## 段階 A — 今日立ち上げる（単一 Mac・現 `serve.py`）

今日実際に動くのは stdlib の `bajutsu serve` + CLI だけ。この段階では、**ほぼコード変更なし**で
チームから安全に到達できるようにする。

```
            チーム端末
               │  HTTPS（Tailscale tailnet 内のみ）
               ▼
   ┌─────────────────────────────────────┐
   │  Mac mini (Apple Silicon)           │
   │  · Xcode + Simulator + idb_companion│
   │  · bajutsu serve  (LaunchAgent)     │  ← 127.0.0.1:8765
   │  · tailscale serve → tailnet HTTPS  │
   │  · 自動ログイン + caffeinate        │
   └─────────────────────────────────────┘
```

**ハード**: Mac mini M2/M4、メモリ 16GB（Simulator 複数同時なら 32GB）。

**1) serve を LaunchAgent で常駐** — `127.0.0.1` バインドのまま（生公開しない）。
`~/Library/LaunchAgents/com.bajutsu.serve.plist` で
`python -m bajutsu serve --host 127.0.0.1 --port 8765 --scenarios <dir>` を `RunAtLoad` +
`KeepAlive` 付きで実行。`ANTHROPIC_API_KEY` を `EnvironmentVariables` に（`--dismiss-alerts` 用）、
stdout/stderr は `~/Library/Logs/` へ。`launchctl bootstrap gui/$(id -u) …` でロード。GUI
セッションが要るため **LaunchAgent**（LaunchDaemon ではない）であること。

**2) セッションを生かす**。システム設定で自動ログインを有効化し、スリープを無効化
（`sudo pmset -a sleep 0 disablesleep 1`）。

**3) 公開 — Tailscale（推奨）**。`serve.py` は**認証ゼロ**で、`/api/run` は**クライアント指定の
シナリオパス**を実行する（[serve.py](../../bajutsu/serve.py) の `run_command`/`do_POST`）。よって
**公開インターネットへ `0.0.0.0` で出すのは不可**。代わりにプライベートな tailnet 上に置く —— ID
ベースのアクセス + 自動 TLS で、公開面はゼロ:

```bash
tailscale serve --bg 8765    # → https://<machine>.<tailnet>.ts.net （tailnet 内のみ到達）
```

社内ホスト名が**どうしても**必要な場合のみ、前段に **Caddy** を置いて TLS + Basic 認証
（`basic_auth` の後ろで `reverse_proxy 127.0.0.1:8765`）。ただし未認証・任意パスの面があるため、
オープンなインターネットには出さないこと。

この段階は**今日**チームで使える。

---

## 段階 B — 完全セルフホストのマルチテナント版（cloud-hosting のシステムを将来構築した場合）

[cloud-hosting](cloud-hosting.md) のアーキテクチャを取り、各マネージドサービスをセルフホスト OSS に
置き換える。トポロジは **Linux 1 ノード（Docker Compose）＋ Mac ワーカープール**で、すべてを
**Tailscale tailnet** で接続。公開面は Caddy の `:443` だけ。

| cloud-hosting の選定 | セルフホスト置換 |
|---|---|
| Fly.io / Render（コントロールプレーン） | 自前の **Linux ノード** + **Docker Compose** |
| Fly Postgres | **postgres** コンテナ |
| Upstash Redis | **redis** コンテナ |
| Cloudflare R2（成果物） | **MinIO**（S3 互換・自前） |
| GitHub OAuth（Authlib） | **Authelia** or **Keycloak**（自前 IdP: Identity Provider、認証基盤）/ oauth2-proxy |
| Caddy / TLS | **Caddy**（Let's Encrypt or 内部 CA） |
| MacStadium Orka（Mac プール） | 自前の **Mac mini プール（1…N 台）** + worker agent を `LaunchAgent` 常駐 |
| Doppler（secrets） | **SOPS + age** or Vault（or 権限を絞った `.env`） |
| Sentry / Grafana（可観測性） | **GlitchTip** + 自前 **Prometheus/Grafana** |

```
            チーム端末
               │  HTTPS :443
               ▼
   ┌──────────────────────────────────────────────┐        ┌─────────────────────────┐
   │  Linux ノード — docker compose                │ Redis  │  Mac ワーカー × N        │
   │  caddy · authelia · app · postgres · redis    │ ─────▶ │  worker agent           │
   │  minio · prometheus · grafana                 │ ◀───── │  bajutsu run --erase    │
   └──────────────────────────────────────────────┘  job   │  Simulator (GUI session)│
                  └──────────────── Tailscale tailnet ──────┴─────────────────────────┘
```

Linux ノードの `docker-compose.yml` は `caddy`（TLS + リバースプロキシ）・`authelia`（認証）・
`app`（コントロールプレーン —— **未実装**、[cloud-hosting](cloud-hosting.md) 参照）・`postgres`・
`redis`・`minio`・`prometheus`/`grafana` を配線し、ステートフルなものには named volume を割り当てる。
各 Mac ワーカーは **段階 A と同じ LaunchAgent パターン**（GUI セッション・自動ログイン・caffeinate）
で、ただし **worker agent** を動かす。agent は tailnet 越しに Linux ノードの Redis からジョブを
lease し、クリーンな Simulator で `bajutsu run --erase` を実行、ログを返し、`runs/<id>/` ツリーを
MinIO へアップロードする。

**サイジング**。動画証跡が重いので MinIO 用に数百 GB を見込む。Linux ノードは控えめでよい
（2 vCPU / 4 GB。OrbStack で Mac に同居も可）。**Mac が占有量を支配**する。

### 負荷分散 —— 2 つの別問題

これを 1 つの「ロードバランサ」と捉えない。コントロールプレーン（HTTP・安価・水平スケール）と
**Mac プール**（希少・低並列・低速）では、最適な手法が**逆**になる。

**コントロールプレーンの負荷分散（容易・標準的）**。ステートレスな FastAPI を **N レプリカ**並べ、
前段の **Caddy/HAProxy** で分散:

- long-lived な SSE（Server-Sent Events）接続が多いので round-robin より **least-conn** を推奨。ヘルスチェック付き。
- **sticky セッションを使わない** —— 署名 Cookie/JWT、or セッションを Redis に外出しし、どの
  レプリカでも任意リクエストを処理可能にする。
- **SSE はシャード不要**: ライブログの真実は Redis pub/sub なので、*どの*レプリカでも*任意*の run の
  ログを配信できる。LB は SSE を**バッファリングしない**設定（flush / no-buffer）に。async uvicorn は
  少ない worker で多数の SSE を捌くが、想定同時視聴数に合わせて並列度は確保する。

**ワーカーの「負荷分散」＝ジョブスケジューリング（本丸）**。Mac は同時 Simulator 数 **K** が小さく
（RAM 制約で 1〜3）、1 run は数分。鉄則は **push ではなく pull**:

- **プル型キュー**。ワーカーは *Simulator スロットが空いたときだけ* Redis からジョブを lease する。
  これだけで**自動負荷分散 + バックプレッシャ**が成立 —— 中央スケジューラが各 Mac の負荷を知る必要
  がない。
- **スロット = 並列度**。各ワーカーの concurrency を物理 Simulator スロット数に固定（RQ/Celery の
  worker concurrency）。総スループット = 全 Mac のスロット合計。
- **能力別ルーティング**。デバイス / iOS runtime 別にキューを分け（`q:ios18`・`q:ipad`）、その
  runtime を持つ Mac だけが該当キューを subscribe する。
- **lease + heartbeat → 再投入**。Mac が run 途中で落ちたらジョブを re-queue（Celery の ack-late /
  RQ の死活監視）。Mac プールは希少なので取りこぼさない。

### マルチテナント

4 つの軸:

- **データ隔離**。共有 Postgres + 全テーブルに `org_id`、アプリ層で**全クエリを org スコープ**、
  防御多層化に **Postgres RLS（行レベルセキュリティ）** ポリシーを併用。MinIO は**テナント prefix**
  （`artifacts/<org_id>/runs/…`）で、配信は org スコープの**署名付き URL** のみ。自前運用では
  スキーマ/DB 分離より **共有スキーマ + `org_id` + RLS** が勝る。
- **実行隔離**（シナリオは実質「デバイスを操る非信頼コード」）。**run ごとに使い捨て Simulator**
  （`--erase` / 都度 create+delete）で、同じ Mac でもテナント間で状態・keychain・スクショ・
  ネットワークが残らない。org の `ANTHROPIC_API_KEY` やアプリ認証は**ジョブのプロセス env にだけ
  注入**し、永続させず、終了後に消す。高隔離テナントには **Mac 丸ごと専有**（or macOS VM）とし、
  同一 Mac で 2 テナントの Simulator を同時走行させない —— 利用率 vs 隔離。**ジョブ単位の egress
  制御**も追加。
- **公平性・ノイジーネイバー対策**。enqueue 時に**テナント別 同時実行クォータ**を強制（org の
  in-flight 数を数え、超過分は org 別 pending キューで保留）し、1 テナントが希少な Mac を独占でき
  ないように。純 FIFO を**重み付き公平スケジューリング**に置換: テナント別キュー + ディスパッチャが
  pending を持つ org をラウンドロビン（クォータ尊重）してワーカー向けキューへ供給。優先度ティアも
  同じ仕組みに乗る。
- **認可境界**。全リクエストが org（OAuth/Authelia の claim）を持つ。全エンドポイントで org スコープ
  を強制、org 内 RBAC（ロールベースアクセス制御）、ワーカーには**そのジョブの org コンテキストとスコープ済みシークレットだけ**
  渡す。

### 水平スケール後のセルフホスト SPOF（単一障害点）

水平スケールした途端、**Redis と Postgres が単一障害点（SPOF: Single Point of Failure）**になる（Redis はキュー・pub/sub・クォータ
状態を握る）。**Redis は Sentinel で HA（高可用性）化**、**Postgres** は primary+replica（Patroni）か最低でも
堅実なバックアップ（単一ノードでも可、ただし **SPOF と明記**）、**LB 自身**も keepalived/VRRP or
DNS で冗長化する。

```
[DNS] → [HAProxy ×2 (VRRP)] → [FastAPI ×N] ─┬─ Postgres (primary+replica)
                                            ├─ Redis (Sentinel)   ← キュー / pub-sub / クォータ
                                            └─ MinIO (tenant prefix)
                                                   │ プル型キュー（device別 + org公平）
                              Mac mini プール ×M（各 K スロット・run毎 erase・専有/隔離）
```

要するに: **前段 LB は安価な制御層だけ**を分散し、**実体の分散はプル型キュー + スロット**が担い、
**マルチテナント = `org_id`/RLS（データ）＋ 使い捨て Simulator（実行）＋ テナント別クォータと公平
スケジューリング（資源）** で実現する。Mac プールは弾力的に増えないため、**公平性と隔離が設計の
中心**になる。

---

## 推奨

まず **段階 A**（Tailscale + LaunchAgent）から。実在する既存システムを、ほぼコードなしでチームへ
安全に提供できる。**段階 B** へは、マルチユーザの隔離と履歴が本当に必要になってから —— つまり
[cloud-hosting](cloud-hosting.md) のコントロールプレーンが実在するようになってから移行する。
