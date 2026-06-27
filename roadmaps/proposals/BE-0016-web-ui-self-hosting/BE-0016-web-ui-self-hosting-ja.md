[English](BE-0016-web-ui-self-hosting.md) · **日本語**

# BE-0016 — Web UI のセルフホスティング

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0016](BE-0016-web-ui-self-hosting-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | Web UI のホスティング（クラウド / セルフホスト） |
<!-- /BE-METADATA -->

## はじめに

本提案は、Web UI を**自前の Mac で立ち上げて稼働させる**方法を扱います。これは
[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) のマネージドな
マルチテナント公開スタックに対する、セルフホスト版の対になります。2 段階を文書化します。

- **段階 A（今日すぐ使える）。** 既存の stdlib `bajutsu serve`（`bajutsu/serve/`、すでに
  [BE-0011](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md) として実装済み）を、
  [BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
  のハードニング（トークン認証 + 入力検証）で安全に公開できるようにし、運用設定（LaunchAgent、自動
  ログイン、Tailscale）を加えるだけで*今日*動くものです。追加する小さなコードは、LaunchAgent plist を
  出力する `serve --emit-launchagent` だけです。手順書は
  [docs/ja/self-hosting.md](../../../docs/ja/self-hosting.md) にあります。
- **段階 B（将来）。**
  [BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) の将来の
  マルチテナント版を、各マネージドサービスをセルフホスト OSS（オープンソースソフトウェア）に
  置き換えて完全自前化したものです。BE-0015 で提案された（未実装の）コントロールプレーンに依存します。

項目全体としては提案ですが、その中で段階 A は今日すぐ使えるベースラインです。

## 動機

テスト基盤をマネージドクラウドに置けない、あるいは置きたくないチームがあります（コスト、データの所在、
ポリシーなどの理由から）。そうしたチームは自前のハードウェアで Web UI を動かしたいと考えます。bajutsu の
セルフホストは通常の Web サービスのセルフホストとは**異なります**。ランナーが **iOS Simulator** を
駆動するため、プロセスを動かせる場所と方法が制約されるからです。本提案は、すぐ使える経路（段階 A）と
完全セルフホストのマルチテナント目標（段階 B）の両方を記録し、運用設計を失わずチームが段階的に
採用できるようにします。

## 詳細設計

### セルフホストを規定する macOS の要件

ランナーは **iOS Simulator** を駆動します。Simulator には **GUI ログインセッション**（WindowServer /
Aqua セッション）が必要で、ヘッドレスな daemon では**動きません**。以下のセルフホスト方針はすべて
これに由来します。

- プロセスは **`LaunchAgent`**（ユーザ単位、GUI セッション）で動かします。**`LaunchDaemon` ではありません**。
- 再起動後に GUI セッションを回復できるよう Mac は**自動ログイン**にします（FileVault 有効時は、
  コールドブート後に一度だけ対話ログインしてから自動ログインが進む点に注意してください）。
- セッションを維持するため**スリープを無効化**します（`caffeinate` / `pmset`）。

これが、bajutsu のホスティングが通常の Web サービスのホスティングと運用上異なる点です。

### 段階 A：今日立ち上げる（単一 Mac、現 `serve`）

今日実際に動くのは stdlib の `bajutsu serve` + CLI だけで、すでに
[BE-0011](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md) として実装済みで、
[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
で公開向けにハードニング済みです。この段階では、**ほぼコード変更なし**（`--emit-launchagent` ヘルパー
だけ）でチームから安全に到達できるようにします。既存の `bajutsu serve` で**今日すぐ動かせます**。
手順は [docs/ja/self-hosting.md](../../../docs/ja/self-hosting.md) を参照してください。

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

**1) serve を LaunchAgent で常駐させる。** `127.0.0.1` バインドのまま（生公開しません）。plist は
`bajutsu serve --emit-launchagent --config <config.yml> --token <token>` で生成します。出力される
`com.bajutsu.serve.plist` は `python -m bajutsu serve --host 127.0.0.1 --port 8765 --config
<config.yml>` を `RunAtLoad` + `KeepAlive` 付きで実行し、トークンを `EnvironmentVariables` に入れ
（`ps` に出ません）、stdout/stderr は `~/Library/Logs/` へ。`launchctl bootstrap gui/$(id -u) …` で
ロードします。GUI セッションが要るため **LaunchAgent**（LaunchDaemon ではない）であることが必要です。

**2) セッションを維持する。** システム設定で自動ログインを有効化し、スリープを無効化します
（`sudo pmset -a sleep 0 disablesleep 1`）。

**3) 公開する（Tailscale 推奨）。** BE-0051 により serve は全リクエストにトークン認証を要求し、
`/api/run` と `/api/record` をアプリの scenarios dir に限定します。よって公開はもう未認証ではありません。
それでも安全な既定は**プライベートな tailnet** であり、公開インターネットへの `0.0.0.0` ではありません。
ID ベースのアクセスと自動 TLS により公開面はゼロになります。

```bash
tailscale serve --bg 8765    # → https://<machine>.<tailnet>.ts.net （tailnet 内のみ到達）
```

社内ホスト名が**どうしても**必要な場合のみ、前段に **Caddy** を置いて TLS + Basic 認証
（`basic_auth` の後ろで `reverse_proxy 127.0.0.1:8765`）を設定します。ただしオープンなインターネット
には出さないでください。

この段階は**今日**チームで使えます。

### 段階 B：完全セルフホストのマルチテナント版（BE-0015 のコントロールプレーンに依存）

> **いま使える（シングルテナント）:** BE-0015 のサーバ backend がシングルテナント向けに出荷済みです
> （Postgres、Redis、S3 互換ストレージ、GitHub OAuth、RBAC、per-user クォータ）。そのため**1 チーム**向けの
> セルフホスト コントロールプレーンは今日動かせます。docker-compose 一式と手順は
> [`deploy/self-host/`](../../../deploy/self-host/) と
> [docs/ja/self-hosting.md](../../../docs/ja/self-hosting.md) にあります。これは段階 A と下記の完全マルチテナント
> 目標の橋渡しです。本節の**マルチテナント**部分（複数 org、org スコープの強制、`org_id` のテナント prefix）は
> 引き続き BE-0015 のマルチテナント コントロールプレーン待ちです。

[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) のアーキテクチャを
取り、各マネージドサービスをセルフホスト OSS に置き換えます。トポロジは **Linux 1 ノード（Docker
Compose）＋ Mac ワーカープール**で、すべてを **Tailscale tailnet** で接続します。公開面は Caddy の
`:443` だけです。

| cloud-hosting の選定 | セルフホスト置換 |
|---|---|
| Fly.io / Render（コントロールプレーン） | 自前の **Linux ノード** + **Docker Compose** |
| Fly Postgres | **postgres** コンテナ |
| Upstash Redis | **redis** コンテナ |
| Cloudflare R2（成果物） | **MinIO**（S3 互換、自前） |
| GitHub OAuth（Authlib） | **Authelia** または **Keycloak**（自前 IdP: Identity Provider、認証基盤）、または oauth2-proxy |
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

Linux ノードの `docker-compose.yml` は `caddy`（TLS + リバースプロキシ）、`authelia`（認証）、
`app`（コントロールプレーン。**未実装**、
[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) 参照）、`postgres`、
`redis`、`minio`、`prometheus`/`grafana` を配線し、ステートフルなものには named volume を割り当てます。
各 Mac ワーカーは **段階 A と同じ LaunchAgent パターン**（GUI セッション、自動ログイン、caffeinate）
で動かしますが、実行するのは **worker agent** です。agent は tailnet 越しに Linux ノードの Redis からジョブを
lease し、クリーンな Simulator で `bajutsu run --erase` を実行し、ログを返し、`runs/<id>/` ツリーを
MinIO へアップロードします。

**サイジング。** 動画証跡が重いので MinIO 用に数百 GB を見込んでください。Linux ノードは控えめでよく
（2 vCPU / 4 GB。OrbStack で Mac に同居も可能）、**Mac が占有量を支配**します。

#### 負荷分散：2 つの別問題

コントロールプレーン（HTTP、安価、水平スケール）と **Mac プール**（希少、低並列、処理が遅い）では、
最適な手法が**正反対**になります。これを 1 つの「ロードバランサ」として扱わないでください。

**コントロールプレーンの負荷分散（容易、標準的）。** ステートレスな FastAPI を **N レプリカ**並べ、
前段の **Caddy/HAProxy** で分散します。

- long-lived な SSE（Server-Sent Events）接続が多いので、ヘルスチェックを付けたうえで round-robin より **least-conn** を使います。
- **sticky セッションは使いません**。署名 Cookie/JWT（JSON Web Token）を使うか、セッションを Redis に外出しして、どの
  レプリカでも任意リクエストを処理できるようにします。
- **SSE はシャード不要**です。ライブログの実体は Redis pub/sub なので、*どの*レプリカでも*任意*の run の
  ログを配信できます。LB（ロードバランサ）は SSE を**バッファリングしない**設定（flush / no-buffer）にしてください。async uvicorn は
  少ない worker で多数の SSE を処理しますが、想定同時視聴数に合わせて並列度を確保してください。

**ワーカーの「負荷分散」＝ジョブスケジューリング（本丸）。** Mac は同時 Simulator 数 **K** が小さく
（RAM 制約で 1〜3）、1 run は数分かかります。鉄則は **push ではなく pull** です。

- **プル型キュー。** ワーカーは *Simulator スロットが空いたときだけ* Redis からジョブを lease します。
  これだけで**自動負荷分散 + バックプレッシャ**が成立し、中央スケジューラが各 Mac の負荷を追跡する必要が
  なくなります。
- **スロット = 並列度。** 各ワーカーの concurrency を物理 Simulator スロット数に固定します（RQ/Celery の
  worker concurrency）。総スループット = 全 Mac のスロット合計。
- **能力別ルーティング。** デバイス / iOS runtime 別にキューを分けます（`q:ios18`、`q:ipad`）。その
  runtime を持つ Mac だけが該当キューを subscribe します。
- **lease + heartbeat → 再投入。** Mac が run 途中で落ちたらジョブを re-queue します（Celery の ack-late /
  RQ の死活監視）。Mac プールは希少なのでジョブを取りこぼさないようにします。

#### マルチテナント

4 つの軸があります。

- **データ隔離。** 共有 Postgres + 全テーブルに `org_id`、アプリ層で**全クエリを org スコープ**にし、
  防御多層化に **Postgres RLS（行レベルセキュリティ）** ポリシーを併用します。MinIO は**テナント prefix**
  （`artifacts/<org_id>/runs/…`）で管理し、配信は org スコープの**署名付き URL** のみ経由とします。自前運用では
  スキーマ/DB 分離より **共有スキーマ + `org_id` + RLS** が実用的です。
- **実行隔離**（シナリオは実質「デバイスを操る非信頼コード」）。**run ごとに使い捨て Simulator**
  （`--erase` / 都度 create+delete）を使い、同じ Mac でもテナント間で状態、keychain、スクショ、
  ネットワークが残らないようにします。org の `ANTHROPIC_API_KEY` やアプリ認証は**ジョブのプロセス env にだけ
  注入**し、永続させず、終了後に消します。高隔離テナントには **Mac 丸ごと専有**（または macOS VM）とし、
  同一 Mac で 2 テナントの Simulator を同時走行させません（利用率と隔離のトレードオフです）。**ジョブ単位の egress
  制御**も追加します。
- **公平性、ノイジーネイバー対策。** enqueue 時に**テナント別 同時実行クォータ**を強制します（org の
  in-flight 数を数え、超過分は org 別 pending キューで保留）。1 テナントが希少な Mac を独占できないようにするためです。
  純 FIFO（先入れ先出し）を**重み付き公平スケジューリング**に置換します。テナント別キュー + ディスパッチャが
  pending を持つ org をラウンドロビン（クォータ尊重）してワーカー向けキューへ供給します。優先度ティアも
  同じ仕組みで実現します。
- **認可境界。** 全リクエストが org（OAuth/Authelia の claim）を持ちます。全エンドポイントで org スコープ
  を強制し、org 内 RBAC（ロールベースアクセス制御）を適用し、ワーカーには**そのジョブの org コンテキストとスコープ済みシークレットだけ**
  渡します。

#### 水平スケール後のセルフホスト SPOF（単一障害点）

水平スケールすると、**Redis と Postgres が単一障害点（SPOF: Single Point of Failure）**になります（Redis はキュー、pub/sub、クォータ
状態を保持します）。**Redis は Sentinel で HA（高可用性）化**し、**Postgres** は primary+replica（Patroni）か最低でも
堅実なバックアップを取ります（単一ノードでも可ですが **SPOF と明記**してください）。**LB 自身**も keepalived/VRRP（Virtual Router
Redundancy Protocol）または DNS で冗長化します。

```
[DNS] → [HAProxy ×2 (VRRP)] → [FastAPI ×N] ─┬─ Postgres (primary+replica)
                                            ├─ Redis (Sentinel)   ← キュー / pub-sub / クォータ
                                            └─ MinIO (tenant prefix)
                                                   │ プル型キュー（device別 + org公平）
                              Mac mini プール ×M（各 K スロット・run毎 erase・専有/隔離）
```

まとめると: **前段 LB は安価なコントロールプレーンだけ**を分散し、**実体の分散はプル型キュー + スロット**が担い、
**マルチテナント = `org_id`/RLS（データ）＋ 使い捨て Simulator（実行）＋ テナント別クォータと公平
スケジューリング（資源）** で実現します。Mac プールは弾力的に増えないため、**公平性と隔離が設計の
中心**になります。

### 推奨

まず **段階 A**（Tailscale + LaunchAgent）から始めてください。実在する既存システムを、ほぼコードなしでチームへ
安全に提供できます。**段階 B** へは、マルチユーザの隔離と履歴が本当に必要になってから、つまり
[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) のコントロールプレーンが
実在するようになってから移行します。

## 検討した代替案

- **`LaunchAgent` ではなく `LaunchDaemon`**。却下しました。daemon には GUI / Aqua セッションがなく、Simulator は
  それなしには動きません。ユーザ単位の `LaunchAgent`（＋自動ログイン＋caffeinate）は好みの問題ではなく必須です。
- **公開インターネットへの `0.0.0.0` バインド**。却下しました。BE-0051 のトークン認証があっても、公開バインドは
  攻撃面を不必要に広げます。プライベートな tailnet（Tailscale）を使い、社内ホスト名が必要な場合のみ
  Caddy + Basic 認証を前段に置きます。serve はトークンなしの非 loopback `--host` を既に拒否します。
- **plist をテンプレートファイル（またはドキュメント中のシェル断片）として埋め込む案と `serve --emit-launchagent`**。
  却下しました。オペレータが渡すフラグから plist を生成すれば、argv、インタプリタパス、トークンの配置が
  一箇所で正しく保たれ、コピペによるズレが生じません。
- **スキーマ別 / DB 別テナントと、共有スキーマ + `org_id` + RLS**。自前運用では前者を却下しました。テナント別の
  スキーマ/DB はマイグレーションと接続管理のオーバーヘッドを増やします。共有スキーマ + `org_id` + Postgres RLS の
  方が運用負荷をはるかに抑えつつ隔離を実現できます。
- **純 FIFO スケジューリングと重み付き公平スケジューリング**。前者を却下しました。純 FIFO では 1 テナントが希少な Mac
  プールを独占できてしまいます。テナント別キューとクォータを尊重するラウンドロビンのディスパッチャにより、
  競合下でもプールを公平に保てます。

## 参考

`bajutsu/serve/`、[docs/ja/self-hosting.md](../../../docs/ja/self-hosting.md)（段階 A の手順書）、
[cli.md](../../../docs/ja/cli.md#serve)、[ci.md](../../../docs/ja/ci.md)、
[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
（公開を安全にするハードニング）、
[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)（cloud-hosting の対）、
[BE-0011](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)
