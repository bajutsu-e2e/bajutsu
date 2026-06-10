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
| GitHub OAuth（Authlib） | **Authelia** or **Keycloak**（自前 IdP）/ oauth2-proxy |
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

---

## 推奨

まず **段階 A**（Tailscale + LaunchAgent）から。実在する既存システムを、ほぼコードなしでチームへ
安全に提供できる。**段階 B** へは、マルチユーザの隔離と履歴が本当に必要になってから —— つまり
[cloud-hosting](cloud-hosting.md) のコントロールプレーンが実在するようになってから移行する。
