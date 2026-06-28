[English](../self-hosting.md) · **日本語**

# Web UI のセルフホスティング

> bajutsu の Web UI（[cli](cli.md#serve)）を、自前のハードウェア上で、プライベートな Tailscale ネットワーク
> 越しにチームから到達できるよう動かします（セルフホスティングロードマップ
> [BE-0016](../../roadmaps/proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）。今日使える
> 段階は 2 つあり、どちらも
> [BE-0051](../../roadmaps/implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
> の認証と入力検証で公開を安全にしています。
>
> - **Tier A、単一 Mac。** トークン認証付きの `bajutsu serve` を 1 プロセス、1 台の Mac で動かします。本ページの
>   「Tier B」節より前がこれを扱います。
> - **Tier B、セルフホストのサーバ backend。** BE-0015 のコントロールプレーン（FastAPI、Postgres、Redis、
>   S3 互換ストレージ、GitHub OAuth、RBAC、クォータ）を Linux ノードで動かし、Mac をワーカーにします。既定では
>   シングルテナントで動き、config で org を宣言すれば**複数 org**に対応します（後述の「Tier B、サーバ backend の
>   セルフホスティング」節を参照）。
>
> フルマネージドの公開クラウド提供（ホスト型の Mac ワーカープール＋IaC）は将来のままです
> （[BE-0015](../../roadmaps/proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）。

## macOS の制約

ランナーは **iOS Simulator** を駆動し、Simulator は **GUI ログインセッション**（Aqua セッション）を必要と
します。ヘッドレスな daemon では動きません。以下の選択はすべてここから来ます:

- serve は per-user の **`LaunchAgent`**（GUI セッション）として動かす。**`LaunchDaemon` ではない**。
- 再起動後に GUI セッションを回復するよう Mac を **auto-login** にする（FileVault はコールドブート後、
  auto-login が進む前に一度だけ対話ログインが必要）。
- セッションを生かし続けるためスリープを無効化する: `sudo pmset -a sleep 0 disablesleep 1`。

これらの制約は **iOS Simulator（idb）** backend に固有のものです。**web（Playwright）** backend はヘッドレスの
ブラウザを動かすため、どれも必要ありません。Mac でも Linux でも（Tier B のノード上でも）serve でき、web だけの
構成ならこの節は読み飛ばせます。

## 1. LaunchAgent を生成する

> **先に、この agent が使う venv へ backend のランタイム依存を入れてください。** agent は
> `python -m bajutsu serve` を直接起動するため、`make serve` と違って依存をオンデマンドでは入れません。
> iOS Simulator（idb）backend なら `make deps`（`idb` クライアント、`idb_companion`、xcodegen）。
> web（Playwright）backend なら `uv sync --extra web && playwright install chromium`。これを飛ばすと、
> 実行のディスパッチ時に `no available actuator` で失敗します。

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

出力された plist が設定しない項目が 2 つあり、どちらも `EnvironmentVariables` に追記します。

- `ANTHROPIC_API_KEY`：AI パス（`record`、`--dismiss-alerts`）に必要です（自動では埋め込みません）。Bedrock
  プロバイダを使うなら、代わりにここへ `BAJUTSU_AI_PROVIDER` と `BAJUTSU_BEDROCK_MODEL`、AWS の認証情報を置きます。
- `PATH`：idb backend のときだけ必要です。launchd は最小の `PATH` で agent を起動し、bajutsu は `idb` と
  `idb_companion` を `PATH` 経由で探すため、これがないと `make deps` 済みでも実行が `no available actuator` で
  失敗します。Homebrew の bin と venv の bin を含めてください（web backend は Playwright を import で見つけるので
  `PATH` の追記は要りません）。

XML を手で編集せずに、PlistBuddy で両方を追記できます（`.venv` が解決できるよう、リポジトリ root で実行してください）。

```bash
PLIST=~/Library/LaunchAgents/com.bajutsu.serve.plist
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:ANTHROPIC_API_KEY string sk-ant-…" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:PATH string $(brew --prefix)/bin:/usr/bin:/bin:/usr/sbin:/sbin:$(pwd)/.venv/bin" "$PLIST"
```

serve のバインドは `127.0.0.1` のままで、これを到達可能にするのは次の手順です。

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
[BE-0051](../../roadmaps/implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
のハードニングに依存します。全リクエストのトークン認証、`/api/run` と `/api/record` をアプリの scenarios dir
に限定して `backend`/`udid` を検証すること、CSRF Origin チェックとセキュリティヘッダ、run dispatch の同時実行上限です。
トークンは秘匿し、Mac は tailnet 上に置き、OS は更新し続けてください。

## アップロードされた config のコマンド実行（BE-0090）

アップロードされた `.zip` バンドルは、テスト対象アプリを起動するシェルコマンドを `launchServer.cmd`
（および、配線され次第 `mockServer.cmd`）に持つ config を同梱できます。このコマンドは**信頼できない入力**なので、
`serve` がホスト上で直接実行することはありません。**アップロードされた**バンドルの run がこのコマンドを必要とする
ときの挙動は、`--upload-exec` オプション（ホスト型 backend では環境変数 `BAJUTSU_UPLOAD_EXEC`）で選びます。これが
効くのはアップロード由来の config だけで、ローカルや Git 由来の config は運用者が信頼しているため影響を受けません。

- **`sandbox`**（既定）：コマンドを使い捨ての **Docker** コンテナの中で実行し、`serve` ホストには決して触れさせません。
  バンドルはランタイムを、`dockerImage`（公開済みのベースイメージ。例 `node:20-slim`）か `dockerfile`（バンドル相対の
  パスで、`serve` が `docker build` でビルドします）のどちらか一方で宣言し、加えて `port`（コンテナ内の待ち受けポート）
  を指定します。コンテナはハードニングされ（`--rm`、全ケーパビリティの剥奪、権限昇格の禁止、読み取り専用ルート
  ファイルシステムと `tmpfs` のスクラッチ、非 root ユーザ、CPU・メモリ・pid の上限、そして単一のポートだけを
  **ループバック**のホストポートへ publish）、run の後に破棄します。`serve` ホストには Docker が必要です。
- **`reuse`**：アップロードされたコマンドは実行せず、`baseUrl` ですでに応答している運用者提供のサーバをプローブする
  だけです。何も応答しなければ、何かを起動するのではなく run を fail loud させます。
- **`deny`**：アップロードされたコマンドをきっぱり拒否します。`reuse` と同様、外部で応答するサーバがあれば受け入れ、
  なければ run を fail loud させます。

どのモードもアップロードされたコマンドをホスト上で実行することはなく、こっそりそこへフォールバックすることも
ありません。ブロックされた、あるいは設定の誤った `launchServer` は、flaky に見える run ではなく明確なエラーで
失敗します。その判断（denied / reused / sandboxed、sandbox のときは使ったイメージ）は run の `manifest.json` の
provenance に記録されるので、「この run は何を実行し、何を抑止したのか」をあとから answerable に保てます。

## Tier B、サーバ backend のセルフホスティング

Tier A は 1 台の Mac で動く 1 プロセスです。**Tier B** は BE-0015 の**サーバ backend**、すなわち FastAPI の
コントロールプレーン（Postgres、Redis、S3 互換ストレージ（MinIO）、GitHub OAuth、RBAC、per-user クォータ）を
Linux ノードで動かし、1 台以上の Mac をワーカーにします。既定では**シングルテナント**（全ユーザが 1 つの
default org）で動き、config で org を宣言すれば**複数 org**に対応します（後述の「複数 org」を参照）。すぐ動かせる
一式は [`deploy/self-host/`](../../deploy/self-host/)（compose、Dockerfile、`.env.example`）にあります。

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

ログインは常に `read:org` scope を要求し、ユーザを GitHub org メンバーシップから org に対応づけられるようにします
（config の `githubOrgs`）。そのため同意画面には常に organization へのアクセスが表示されます。シングルテナント構成
（`orgs:` ブロック無し）では、その org 情報を使わないだけです。

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

> **run 履歴のデータベース記録。** run は worker で実行されるので、run 履歴一覧のために Postgres へ記録するのは
> worker です。これを有効にするには `bajutsu[worker,idb,db]` をインストールし、worker に `BAJUTSU_DATABASE_URL`
> （tailnet 越しの Postgres ノード、コントロールプレーンと同じ URL）を渡してください。無くても run 自体は動き、
> 成果物も配信されますが、永続化された履歴一覧には現れません。

### 4. 公開する

Tier A と同様に前段を置きます。`tailscale serve --bg 8765`（tailnet 内のみ、推奨）、または実ホスト名なら Caddy
（`docker compose --profile caddy up -d`、`BAJUTSU_PUBLIC_HOST` を設定）。ワーカーは tailnet 越しに Redis
（`:6379`）と MinIO（`:9000`）へ到達するので、ノードはプライベートな tailnet 上に置いてください。

### 複数 org

1 つの backend で複数チームをホストするには、マウントした config に org を宣言します。各 org に所属メンバー
（GitHub login や GitHub org）と、その org が持つ targets を指定します（[configuration](configuration.md#orgsマルチテナントのサーバ-backend)を参照）。

```yaml
orgs:
  acme:
    members: [alice, bob]
    githubOrgs: [acme-gh]    # この GitHub org の全員（ログインは read:org scope を要求）
    targets: [demo, checkout]
  globex:
    members: [carol]
    targets: [other]
```

各ユーザは自分の org にスコープされます。自 org の targets だけが見え、別 org の run／scenario／成果物は not-found
または 403 になり、各 org の artifacts／scenarios／baselines はその org 専用のオブジェクトストレージ prefix の下に
置かれます。`orgs:` ブロックが無ければ backend はシングルテナント（1 つの default org）のままで、共有トークンと
GitHub 許可リストがアクセス境界です。フルマネージドの公開クラウド提供（ホスト型の Mac ワーカープール＋IaC）は
BE-0015 で今後の作業です。

## 運用ログ

ホスト型の serve は、自身の診断トレースを出力します。**構造化された JSON を標準出力に書き出し、秘密情報は
マスクします**。これにより、1 つのユーザ操作を制御プレーンとワーカーをまたいで追跡できます。これは、すでに
ある 3 つのログ面とは別物です。テスト対象の**証跡（evidence）**、ライブの**実行出力**ストリーム、そして
誰が何をしたかの**監査ログ**のいずれでもありません。出力された行を集約する作業（標準出力をログ基盤へ送る作業）は
デプロイ側の責務で、ツールは行を生成するだけです。

形式と詳細度は、起動時に一度だけ読まれる 2 つの環境変数で選びます。

| 変数 | 既定値 | 意味 |
|---|---|---|
| `BAJUTSU_LOG_FORMAT` | `json` | 構造化された serve チャネルなら `json`、人が読みやすい 1 行なら `text`。 |
| `BAJUTSU_LOG_LEVEL` | `INFO` | 標準のレベル名（`DEBUG`／`INFO`／`WARNING`／`ERROR`／`CRITICAL`）。 |

決定的な `run`／CI 経路はこのチャネルを設定しないので、影響を受けません。静かなまま、標準ライブラリだけで動きます。

**相関付け。** 各 JSON 行は、1 つの操作を端から端まで追うための id を持ちます。リクエスト境界で発番される
`request_id` と、ワーカーで束縛される `job_id`／`org`／`actor`／`run_id` です。制御プレーンのリクエストと、それが
起動したワーカーの run が、プロセスをまたいで同じ id の**値**を共有します。行は次のような形です。

```json
{"ts": "2026-06-28T12:00:00+00:00", "level": "INFO", "logger": "bajutsu.serve.operations",
 "event": "run.dispatched", "msg": "job dispatched", "request_id": "…", "org": "acme",
 "job_id": "…"}
```

`event` フィールドは安定したイベント名（`run.dispatched`、`quota.rejected`、`worker.job.started`、
`worker.job.finished`、`artifact.upload.failed` など）を示すので、これを対象に grep やアラートを設定できます。

**マスクは構造的です。** 1 つのフィルタがルートロガーに置かれるので、書き出される前に**すべての**行が
スキャンされます。サードパーティのライブラリが出した行も対象です。正しさは、各呼び出し箇所がマスクを
忘れないことに依存しません。既知の秘密の**値**（オペレータトークン、OAuth クライアントシークレット、
`ANTHROPIC_API_KEY`、および run の実行中はその run が解決した `${secrets.X}`）と、機微なフィールド**名**
（`authorization`、`token`、`secret`、`password`、`cookie`、`api_key`）をマスクし、`[REDACTED]` に置き換えます。
