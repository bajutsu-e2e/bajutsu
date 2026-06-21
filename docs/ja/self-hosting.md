[English](../self-hosting.md) · **日本語**

# Web UI のセルフホスティング

> bajutsu の Web UI（[cli](cli.md#serve)）を、自前のハードウェア上で、プライベートな Tailscale ネットワーク
> 越しにチームから到達できるよう動かします（セルフホスティングロードマップ
> [BE-0016](../../roadmaps/proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)）。今日使える
> 段階は 2 つあり、どちらも
> [BE-0051](../../roadmaps/proposals/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
> の認証と入力検証で公開を安全にしています。
>
> - **Tier A、単一 Mac。** トークン認証付きの `bajutsu serve` を 1 プロセス、1 台の Mac で動かします。本ページの
>   「Tier B」節より前がこれを扱います。
> - **Tier B、単一チームのサーバ backend。** BE-0015 のコントロールプレーン（FastAPI、Postgres、Redis、
>   S3 互換ストレージ、GitHub OAuth、RBAC、クォータ）を Linux ノードで動かし、Mac をワーカーにします。1 チーム
>   向けの構成です（シングルテナント）。後述の「Tier B、単一チームのセルフホスティング」節を参照してください。
>
> 完全マルチテナントの公開・セルフホスト構成（複数 org）は将来のままです
> （[BE-0015](../../roadmaps/proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)）。

## macOS の制約

ランナーは **iOS Simulator** を駆動し、Simulator は **GUI ログインセッション**（Aqua セッション）を必要と
します。ヘッドレスな daemon では動きません。以下の選択はすべてここから来ます:

- serve は per-user の **`LaunchAgent`**（GUI セッション）として動かす。**`LaunchDaemon` ではない**。
- 再起動後に GUI セッションを回復するよう Mac を **auto-login** にする（FileVault はコールドブート後、
  auto-login が進む前に一度だけ対話ログインが必要）。
- セッションを生かし続けるためスリープを無効化する: `sudo pmset -a sleep 0 disablesleep 1`。

## 1. LaunchAgent を生成する

`bajutsu serve --emit-launchagent` は、渡した serve フラグに対応する launchd plist を出力して、サーバを
起動せずに終了します。強いトークンを選び、plist を LaunchAgents に書き出します:

```bash
export TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
bajutsu serve --emit-launchagent --config bajutsu.config.yaml --token "$TOKEN" \
  > ~/Library/LaunchAgents/com.bajutsu.serve.plist
chmod 600 ~/Library/LaunchAgents/com.bajutsu.serve.plist   # plist はトークンを含む
```

出力される plist は、次のように動きます:

- `python -m bajutsu serve --host 127.0.0.1 --port 8765 --config …` を、**`RunAtLoad`** + **`KeepAlive`**
  付きで実行します（コマンドを動かしたのと同じインタプリタ、つまりあなたの venv を使います）。
- トークンを **`EnvironmentVariables`**（`BAJUTSU_SERVE_TOKEN`）に入れます。argv には載せないので、`ps` からは
  見えません。
- stdout/stderr を `~/Library/Logs/bajutsu-serve.{out,err}.log` に書きます。

AI パス（`record`、`--dismiss-alerts`）を使うなら、plist の `EnvironmentVariables` に `ANTHROPIC_API_KEY`
を追記してください（自動では埋め込みません）。バインドは `127.0.0.1` のままで、これを到達可能にするのは次の手順です。

## 2. ロードする

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bajutsu.serve.plist
launchctl print gui/$(id -u)/com.bajutsu.serve        # ロード確認
```

plist を編集した後に再読み込みするには、`launchctl bootout gui/$(id -u)/com.bajutsu.serve` の後で再度 bootstrap します。

## 3. Tailscale で公開する（推奨）

serve は `127.0.0.1` のままで、**Tailscale** が tailnet 内にだけ公開します。identity ベースのアクセスと
自動 TLS が備わり、公開面はありません:

```bash
tailscale serve --bg 8765    # → https://<machine>.<tailnet>.ts.net（tailnet 内のみ到達可能）
```

チームがその URL を開くと、初回に UI がトークンを尋ねます（以後はブラウザがセッション Cookie を持ちます）。
API クライアントは `Authorization: Bearer $TOKEN` を送ります。

> **`0.0.0.0` をインターネットに公開しないでください。** トークンがあっても、安全な既定はプライベートな
> tailnet です。serve はトークン無しの非 loopback `--host` を拒否しますが、公開バインドは不必要に面を広げます。
> 本当に内部ホスト名が必要なら、serve の前段に **Caddy** を置いて TLS（+ basic auth）を付け、オープンな
> インターネットからは隔離してください。

## セキュリティのまとめ（BE-0051）

セルフホストの serve は
[BE-0051](../../roadmaps/proposals/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
のハードニングに依存します。全リクエストのトークン認証、`/api/run` と `/api/record` をアプリの scenarios dir
に限定して `backend`/`udid` を検証すること、CSRF Origin チェックとセキュリティヘッダ、run dispatch の同時実行上限です。
トークンは秘匿し、Mac は tailnet 上に置き、OS は更新し続けてください。

## Tier B、単一チームのセルフホスティング（サーバ backend）

Tier A は 1 台の Mac で動く 1 プロセスです。**Tier B** は BE-0015 の**サーバ backend**、すなわち FastAPI の
コントロールプレーン（Postgres、Redis、S3 互換ストレージ（MinIO）、GitHub OAuth、RBAC、per-user クォータ）を
Linux ノードで動かし、1 台以上の Mac をワーカーにします。**シングルテナント**で、全ユーザが 1 つの default org に
属するため、**1 チーム**で使う構成です（複数 org をまたぐ分離は未実装）。すぐ動かせる一式は
[`deploy/self-host/`](../../deploy/self-host/)（compose、Dockerfile、`.env.example`）にあります。

```
        チームの端末
           │  HTTPS（Tailscale tailnet、またはホスト名で Caddy）
           ▼
   ┌───────────────────────────────────────┐  job   ┌──────────────────────────┐
   │  Linux ノード — docker compose        │ ─────▶ │  Mac ワーカー × N        │
   │  bajutsu serve --asgi --backend=server│ Redis  │  bajutsu worker          │
   │  postgres・redis・minio               │ ◀───── │  bajutsu run・Simulator  │
   └───────────────────────────────────────┘ result └──────────────────────────┘
                       └──────────── Tailscale tailnet ──────┘
```

Linux のコントロールプレーンは安価で、**Mac ワーカー**が Simulator の run を担う希少な部分です。ワーカーは
コンテナ化しません。Tier A と同じく Aqua の GUI セッションが要るためです。

### 1. コントロールプレーンを起動する

```bash
cd deploy/self-host
cp .env.example .env            # BAJUTSU_SERVE_TOKEN, POSTGRES_PASSWORD, AWS_*（MinIO）, bucket を設定
mkdir -p config && cp /path/to/bajutsu.config.yaml config/   # 公開する app/project の一覧
docker compose up -d            # postgres + redis + minio + migrate（alembic upgrade head）+ bajutsu
```

`migrate` が `bajutsu` の起動前に Alembic マイグレーションを head まで適用し、`minio-init` がバケットを
作成します。コントロールプレーンは `:8765` で待ち受けます。

公開ポートは `BIND_ADDR`（既定は `127.0.0.1`）にバインドします。Mac の worker が別ホストから Redis と
MinIO に届くようにするには、`.env` の `BIND_ADDR` をノードの tailnet IP に設定してください。公開
インターフェースを持つホストで `0.0.0.0` にはしないでください。Redis と成果物バケットを露出させてしまいます。

### 2. GitHub OAuth を足す（任意）

オペレータが数人なら共有トークン（`BAJUTSU_SERVE_TOKEN`）だけで十分です。ユーザごとのブラウザログインには、
GitHub OAuth アプリを作り（callback は `https://<your-host>/api/oauth/callback`）、`.env` に
`BAJUTSU_OAUTH_GITHUB_CLIENT_ID`／`_SECRET`／`_REDIRECT_URI` と、許可リスト `BAJUTSU_OAUTH_ALLOWED_USERS`
（任意で `BAJUTSU_OAUTH_ADMINS`／`BAJUTSU_OAUTH_VIEWERS`）を設定します。許可リストのユーザは既定で **editor**
（run 可）、admin はサーバ設定（config、API キー、provider）も変更でき、viewer は閲覧のみです。トークンは
オペレータ・CI 用の認証（full access）のまま、OAuth がチームのユーザごとのログインです。

### 3. Mac ワーカーを動かす

各 Mac で（Tier A と同じ Aqua セッション設定。auto-login と `caffeinate`/`pmset`）、`bajutsu[worker,idb]` を
インストールし、tailnet 越しに Linux ノードへ向けます:

```bash
export BAJUTSU_REDIS_URL=redis://<linux-node>.<tailnet>.ts.net:6379
export BAJUTSU_S3_BUCKET=bajutsu
export BAJUTSU_S3_ENDPOINT=http://<linux-node>.<tailnet>.ts.net:9000
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…
export ANTHROPIC_API_KEY=…     # シナリオが AI パス（record / --dismiss-alerts）を使う場合のみ
bajutsu worker
```

再起動を越えるよう、Tier A と同じく `LaunchAgent` で包みます。各ジョブはクリーンな Simulator で実行され、
`runs/<id>/` ツリーを MinIO にアップロードし、コントロールプレーンがそれを配信します。

### 4. 公開する

Tier A と同様に前段を置きます。`tailscale serve --bg 8765`（tailnet 内のみ、推奨）、または実ホスト名なら Caddy
（`docker compose --profile caddy up -d`、`BAJUTSU_PUBLIC_HOST` を設定）。ワーカーは tailnet 越しに Redis
（`:6379`）と MinIO（`:9000`）へ到達するので、ノードはプライベートな tailnet 上に置いてください。

### まだシングルテナントです

複数 org、org スコープの run 履歴、org をまたぐアクセス検査、per-org クォータは**未実装**です（BE-0015 の
マルチテナント コントロールプレーン待ち）。Tier B は**1 チーム**向けで、共有トークンと GitHub 許可リストが
アクセス境界です。
