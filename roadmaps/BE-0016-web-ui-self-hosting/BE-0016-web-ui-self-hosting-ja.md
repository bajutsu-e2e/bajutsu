[English](BE-0016-web-ui-self-hosting.md) · **日本語**

# BE-0016 — Web UI のセルフホスティング

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0016](BE-0016-web-ui-self-hosting-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0016") |
| 実装 PR | [#103](https://github.com/bajutsu-e2e/bajutsu/pull/103), [#154](https://github.com/bajutsu-e2e/bajutsu/pull/154), [#365](https://github.com/bajutsu-e2e/bajutsu/pull/365), [#367](https://github.com/bajutsu-e2e/bajutsu/pull/367), [#507](https://github.com/bajutsu-e2e/bajutsu/pull/507), [#674](https://github.com/bajutsu-e2e/bajutsu/pull/674) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本提案は、Web UI を**自前の Mac で立ち上げて稼働させる**方法を扱います。これは
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) のマネージドな
マルチテナント公開スタックに対する、セルフホスト版の対になります。2 段階を文書化します。

- **段階 A（今日すぐ使える）。** 既存の stdlib `bajutsu serve`（`bajutsu/serve/`、すでに
  [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md) として実装済み）を、
  [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
  のハードニング（トークン認証 + 入力検証）で安全に公開できるようにし、運用設定（LaunchAgent、自動
  ログイン、Tailscale）を加えるだけで*今日*動くものです。追加する小さなコードは、LaunchAgent plist を
  出力する `serve --emit-launchagent` だけです。手順書は
  [docs/ja/self-hosting.md](../../docs/ja/self-hosting.md) にあります。
- **段階 B（サーバ backend と Mac ワーカープール）。**
  [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) のサーバ
  backend を、各マネージドサービスをセルフホスト OSS（オープンソースソフトウェア）に置き換えて自前化した
  ものです。その単一ノード版は、**複数 org の隔離を含めて**すでに出荷済みで今日動かせます
  （[`deploy/self-host/`](../../deploy/self-host/)）。残るのは、そのノードを障害に強く org 間で公平な
  *プール*へ育てることです。フルマネージドな公開クラウドは BE-0015 の領分のままです。

本項目は、**出荷済みのセルフホストのベースライン**、すなわち段階 A と単一ノードの段階 B（複数 org の隔離、
org 単位の同時実行上限、ワーカー死活監視による再投入を含む）を記録します。その単一ノードを障害に強く
org 間で公平な*プール*へ育てる先の作業は、五つの焦点を絞ったロードマップ項目に分割し、それぞれを個別に
追跡します（後述の「1 ノードからプールへ：別項目として追跡」を参照してください）。本項目はそれらを
束ねるアンブレラです。

## 動機

テスト基盤をマネージドクラウドに置けない、あるいは置きたくないチームがあります（コスト、データの所在、
ポリシーなどの理由から）。そうしたチームは自前のハードウェアで Web UI を動かしたいと考えます。bajutsu の
セルフホストは通常の Web サービスのセルフホストとは**異なります**。ランナーが **iOS Simulator** を
駆動するため、プロセスを動かせる場所と方法が制約されるからです。本提案は、すぐ使える経路（段階 A）、
今日動かせる単一ノードのマルチテナント backend（段階 B の現状）、そしてそれをプールへ育てる設計を記録し、
運用設計を失わずチームが段階的に採用できるようにします。

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
[BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md) として実装済みで、
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
で公開向けにハードニング済みです。この段階では、**ほぼコード変更なし**（`--emit-launchagent` ヘルパー
だけ）でチームから安全に到達できるようにします。既存の `bajutsu serve` で**今日すぐ動かせます**。
手順は [docs/ja/self-hosting.md](../../docs/ja/self-hosting.md) を参照してください。

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

### 段階 B：Mac ワーカープールを備えたセルフホスト コントロールプレーン

段階 B は BE-0015 のサーバ backend を動かします。Linux ノード上の FastAPI コントロールプレーンに、
Tailscale tailnet 越しにジョブを lease する Mac ワーカーのプールが連なる構成です。この多くは
**すでに出荷済み**なので、本節は 2 部構成にします。**いま動くもの**（動かせるスタックと、すでに org
境界をまたいで効くマルチテナント隔離）と、**残る作業**（その単一ノードを、org 間で公平なまま障害に強い
プールへ育てる作業：org をまたぐ公平性、能力別ルーティング、高可用性、可観測性）です。真に将来なのは
**フルマネージドな公開クラウド**（ホスト型 Mac プールと infrastructure-as-code）であり、これは本項目では
なく [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) の領分です。

#### いま動くもの

単一ノードのサーバ backend は今日動かせます。docker-compose 一式、手順、複数 org の設定は
[`deploy/self-host/`](../../deploy/self-host/) と
[docs/ja/self-hosting.md](../../docs/ja/self-hosting.md) にあります。出荷済みの内容は次のとおりです。

- **コントロールプレーンのスタック**：`docker-compose.yml` が `postgres`、`minio`（S3 互換
  ストレージ）、一度きりの `migrate`（Alembic を head まで適用）、`bajutsu` アプリ
  （`serve --asgi --backend=server`）、公開ホスト名用の任意 `caddy` プロファイルを配線し、ステートフルな
  サービスにはそれぞれ named volume を割り当てます。Mac ワーカーはコンテナ化しません。段階 A と同じく
  Aqua のグラフィカルユーザインタフェース（GUI）セッションが必要で、`bajutsu worker` を動かします。
- **認証とロール**：アプリ自身に組み込んだ GitHub OAuth（open authorization、別建ての認証基盤ではあり
  ません）で、3 ロール（admin / editor / viewer、ロールベースアクセス制御、RBAC）を持ちます。
  [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) は、これらのロールを
  手作業のログイン許可リストではなく GitHub organization と Team のメンバーシップから導きます。org
  メンバーシップがサインインを制御して viewer を、org の `editorTeam` が editor を、サーバ全体で 1 つの
  admin Team が admin を付与します。
- **複数 org の隔離**：マウントした設定に `orgs:` ブロックを宣言すると、同じ backend がマルチテナントに
  なります。各ユーザは GitHub ログインまたは GitHub org の所属で自分の org にスコープされ（ログインは
  `read:org` スコープを要求します）、自分の org のターゲットだけが見え、org をまたぐ run やシナリオや
  成果物の読み取りは not-found か 403 を返します。各 org の成果物、シナリオ、ベースラインは org ごとの
  オブジェクトストア prefix（`org_prefix`）の下に置かれます。`orgs:` ブロックがなければ backend は
  シングルテナント（既定の単一 org）のままです。
- **ジョブ分配**：コントロールプレーンは `DbQueueExecutor` を介して Postgres の `jobs` テーブルに
  `queued` の行を挿入します。`bajutsu worker` は `POST /api/worker/lease` を HTTP でポーリングして
  lease し、**使い捨ての Simulator**（`--erase`）でジョブを実行し、`runs/<id>/` ツリー（`console.log`
  含む）を MinIO にアップロードし、結果を `POST /api/worker/result` に返します。Redis や RQ は不要で、
  worker に必要なのは HTTP とオブジェクトストレージクライアントだけです
  （[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md)）。
- **クォータ**：**グローバル**な同時実行上限（`--max-concurrent-runs`、既定 4）、**ユーザ単位**の上限
  （`max_concurrent_per_user`）、**org 単位**の上限（`max_concurrent_per_org`、
  [#367](https://github.com/bajutsu-e2e/bajutsu/pull/367)。`0` = 無制限なので単一テナントは無影響）が、
  1 人の呼び出し元や 1 つの org が希少なデバイスを独占するのを防ぎます。`try_register`
  （`bajutsu/serve/jobs.py`）でロックの下で原子的に強制します。上限を超えたジョブは拒否します（HTTP 429）。
  その拒否を公平な*保留*へ変えるのは、後述の分割項目の一つです。
- **ワーカー死活監視とジョブ再投入**：各 lease は時限を持ちます。`bajutsu worker` は定期的なハートビート
  （`POST /api/worker/heartbeat`）で lease を更新し、コントロールプレーンはタイムアウトを過ぎても
  ハートビートのない lease を回収して、ジョブを `queued` に戻し別のワーカーへ渡します
  （`reclaim_expired_leases`。`lease_job` のたびに掃引します）。ワーカーを殺し続ける poison ジョブは
  無限に再投入せず試行回数の上限で失敗にします（[#507](https://github.com/bajutsu-e2e/bajutsu/pull/507)）。

次の対応表は、残るマネージドサービスの選択肢の一覧です。**いま出荷済み**の列は、`deploy/self-host/` が
すでに使っているものと、水平スケール時の候補にとどまるものを区別します。

| cloud-hosting の選定 | セルフホスト置換 | いま出荷済み |
|---|---|---|
| Fly.io / Render（コントロールプレーン） | 自前の **Linux ノード** + **Docker Compose** | ✅ |
| Fly Postgres | **postgres** コンテナ | ✅ |
| Cloudflare R2（成果物） | **MinIO**（S3 互換、自前） | ✅ |
| GitHub OAuth（Authlib） | アプリ内蔵の GitHub OAuth、または自前の認証基盤（Authelia / Keycloak / oauth2-proxy） | ✅（アプリ内蔵の GitHub OAuth） |
| Caddy / TLS | **Caddy**（Let's Encrypt または内部 CA） | ✅（任意の `caddy` プロファイル） |
| MacStadium Orka（Mac プール） | 自前の **Mac mini プール（1…N 台）** + worker を `LaunchAgent` 常駐 | ✅（単一ワーカー。プール化は下記） |
| Doppler（secrets） | **SOPS + age** または Vault（または権限を絞った `.env`） | `.env` |
| Sentry / Grafana（可観測性） | **GlitchTip** + 自前 **Prometheus/Grafana** | ⏳（下記） |

```
        チーム端末
           │  HTTPS（Tailscale tailnet、または Caddy のホスト名）
           ▼
   ┌────────────────────────────────────────┐  lease  ┌──────────────────────────┐
   │  Linux ノード — docker compose         │ ◀────── │  Mac ワーカー × N        │
   │  bajutsu serve --asgi --backend=server │   HTTP  │  bajutsu worker（lease /  │
   │  postgres（jobs = キュー） · minio     │ ──────▶ │   heartbeat / result を   │
   │  (· caddy)                             │  result │   ポーリング） run --erase│
   └────────────────────────────────────────┘         │  Simulator (GUI session) │
                       └──────────── Tailscale tailnet ───────┴──────────────────┘
```

**サイジング。** 動画証跡が重いので MinIO 用に数百 GB を見込んでください。Linux ノードは控えめでよく
（2 vCPU / 4 GB。OrbStack で Mac に同居も可能です）、占有量は **Mac が支配**します。

#### 1 ノードからプールへ：別項目として追跡

ここまでの内容は **1 つの** Linux ノードと **1〜数台の** Mac ワーカーで動きます。これを、org 間で
公平なまま障害に強いプールへ変えるのは先の作業です。各要素は互いに独立していて相応の規模があり、多くは
Linux だけで動く `make check` ゲート（Docker も Mac もなし）では実行できないデプロイや運用の関心事です。
そこでこのアンブレラから**五つの焦点を絞ったロードマップ項目へ分割**しました。この作業が土台にした
org 単位の上限とワーカー死活監視による再投入は、すでに出荷済みです（「いま動くもの」を参照してください）。
各後続項目は、出荷済みの土台、具体設計、検証方法を示します。機械的に検証できる面を持つのは三つで
（重み付き公平分配は全面的に、能力別ルーティングと可観測性は部分的に）、制御プレーンのスケールアウトと
高可用性は手動で検証します。トピック別に挙げます。

1. **組織間で公平な重み付きジョブ分配**。org 単位の上限による 429 拒否を*保留*へ変えます。org 別の
   待機キューとラウンドロビンのディスパッチャで、逼迫時にも希少なプールを公平に保ちます。*全面的に*
   ゲートで検証できる唯一の部分です（Mac を使わず `ServeState` に対して単体テストします）。
   `bajutsu/serve/operations.py` と `jobs.py` に入ります。
2. **能力に基づくジョブキューの振り分け**。能力別キュー（`q:ios18`、`q:ipad`）により、ジョブが実際に
   それを実行できるワーカーからのみリースされるようにします。リースの絞り込みはゲートで検証でき、
   複数デバイスの振り分けは手動で検証します。決定的な `run` は変えません。
3. **ロードバランサ配下での制御プレーンのスケールアウト**。最小接続数のロードバランサ配下で N 個の
   ステートレスなアプリレプリカを動かし、スティッキーセッションを持ちません（セッションは Postgres に
   存在します）。手動で検証します。
4. **セルフホストの高可用性**。唯一のステートフルな単一障害点（SPOF）を取り除きます。Postgres の
   プライマリとレプリカ、冗長化したロードバランサ、バックアップ済みのオブジェクトストアです。手動で
   検証します。
5. **serve のメトリクスと可観測性**。`/metrics` エンドポイント（キューの深さ、org ごとの実行中ジョブ数、
   実行時間、ワーカーの生存）と、任意の Prometheus・Grafana コンテナです。エンドポイントはゲートで
   検証でき、コンテナは手動で検証します。

この五つはそれぞれ独立した提案であり、本項目を由来として参照します。本項目は、出荷済みのセルフホストの
ベースラインを束ねるアンブレラであり続けます。（本項目のメタデータからの相互リンクは、CI が後続項目の
BE ID を採番したあとに追記します。）

#### マルチテナント：4 つの軸の再整理

マルチテナント設計は 4 つの軸に乗ります。3 つは出荷済みで、1 つ（公平性）が上記の後続項目
「組織間で公平な重み付きジョブ分配」です。

- **データ隔離：出荷済み。** 各 org の成果物、シナリオ、ベースラインは org ごとのオブジェクトストア
  prefix の下にあり、全クエリが org スコープなので org をまたぐ読み取りは not-found / 403 を返します。
  防御多層化としての Postgres 行レベルセキュリティ（RLS）は、すでに境界を強制しているアプリ層スコープの
  上に重ねる将来のハードニングです。
- **実行隔離：出荷済み（既知のギャップあり）。** 各 run は使い捨ての Simulator（`--erase`）で動くので、
  共有 Mac 上で run の間に何も残りません。残るギャップ：シークレットは今日**サーバ単一**の API キーで、
  org ごとに注入されるシークレットではありません。また**ジョブ単位の egress 制御**や、高隔離テナントへの
  **Mac 丸ごと専有**は、まだ自動化されていない運用者の選択です。
- **公平性、ノイジーネイバー対策：一部出荷済み。** ユーザ単位の上限に加え、**org 単位**の上限も入りました
  （いずれも上限超過のリクエストを拒否します）。希少なプールに対する重み付き公平スケジューリング（拒否で
  なく保留する形）が、後続項目「組織間で公平な重み付きジョブ分配」です。
- **認可境界：出荷済み。** 全リクエストが org（OAuth の claim）を持ち、各エンドポイントで org スコープを
  強制し、org 内で RBAC を適用し、ワーカーにはそのジョブの org コンテキストだけを渡します。

まとめると、**前段のロードバランサは安価なコントロールプレーンだけ**を分散し、**実体の分散はプル型
キューとワーカーのスロット**が担い、**マルチテナント = org スコープのデータ + 使い捨て Simulator
（実行）+ テナント別クォータと公平スケジューリング（資源）**で実現します。データ、実行、認可の軸は
すでに org 境界をまたいでおり、Mac プールは弾力的に増えないため、**残る作業の中心は公平性と可用性**です。

### 推奨

まず **段階 A**（Tailscale + LaunchAgent）から始めてください。実在する既存システムを、ほぼコードなしで
単一 Mac 上にチームへ安全に提供できます。マルチユーザの履歴と隔離が必要になったら **段階 B** へ移ります。
**複数 org の隔離**を含む単一ノードのコントロールプレーンは今日動かせます
（[`deploy/self-host/`](../../deploy/self-host/)）。**分割したプール化の作業**（org をまたぐ公平性、
能力別ルーティング、制御プレーンのスケールアウト、高可用性、可観測性。いずれも本項目を由来とする個別の
ロードマップ項目です）は、プールと競合が現実になってから着手すれば十分です。そして
**フルマネージドな公開クラウド**の提供は本項目ではなく
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) の領分のままです。

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
- **ユーザ単位のクォータだけと、org 単位クォータの追加**。ユーザ単位の上限はすでに出荷済みで、
  シングルテナントのデプロイならこれで十分です。しかし、多数のユーザが各自ユーザ単位の上限内に
  とどまる *org* 全体は縛れないので、マルチ org プールでは同じ仕組みの上に org 単位の上限を重ねる必要が
  あります（出荷済み。保留にする残り半分が後続項目「組織間で公平な重み付きジョブ分配」です）。一方を他方で
  置き換えるのでなく、両方の上限を保つのは意図的です。
- **純 FIFO スケジューリングと重み付き公平スケジューリング**。org をまたぐ場面では前者を却下しました。
  純 first-in, first-out（FIFO）では 1 つの org が希少な Mac プールを独占できてしまいます。org 別
  pending キューと、クォータを尊重するラウンドロビンのディスパッチャにより、競合下でもプールを公平に
  保てます。

## 進捗

本項目は**実装済み**です。出荷済みのセルフホストのベースラインを追跡します。単一ノードを障害に強く
org 間で公平なプールへ育てる作業は、五つの個別のロードマップ項目として追跡し（「1 ノードからプールへ：
別項目として追跡」を参照してください）、それぞれが自身の `Progress` を持ちます。

- [x] Tier A。`serve` を LaunchAgent として動かし、セッションを維持し、Tailscale で公開します。
- [x] Tier B の単一ノード。コントロールプレーンの compose スタック、GitHub OAuth + RBAC、組織間の分離、エフェメラルな Simulator を使い Postgres の `jobs` テーブルを HTTP で lease するジョブ分配、全体・ユーザーごとのクォータ（`deploy/self-host/`）。
- [x] 組織ごとの同時実行上限（`max_concurrent_per_org`）（[#367](https://github.com/bajutsu-e2e/bajutsu/pull/367)）。
- [x] ワーカーの死活監視とジョブの再投入。`jobs` テーブル上の lease ハートビートとタイムアウト回収に、再投入の試行回数上限を組み合わせます。
- [x] 残るプール化の作業（重み付き公平分配、能力別ルーティングキュー、制御プレーンのスケールアウト、高可用性、可観測性）を、本項目を由来とする五つの焦点を絞ったロードマップ項目へ分割しました。

単一ノードは現在動かせます（[#103](https://github.com/bajutsu-e2e/bajutsu/pull/103)、[#154](https://github.com/bajutsu-e2e/bajutsu/pull/154)、[#365](https://github.com/bajutsu-e2e/bajutsu/pull/365)、[#367](https://github.com/bajutsu-e2e/bajutsu/pull/367)）。プール化の作業は分割した各項目に移りました。

- ワーカー死活監視とジョブ再投入。lease を更新するハートビート（`POST /api/worker/heartbeat`）、死んだワーカーの lease を再投入し試行回数の上限を超えたジョブを失敗にする `reclaim_expired_leases`（`lease_job` のたびに掃引）、そしてワーカー側のハートビートループを実装しました。あわせて、本項目の Tier B スタック、図、残作業の各項目を BE-0106 の Redis を使わない HTTP ワーカーモデルに合わせて再構成しました（[#507](https://github.com/bajutsu-e2e/bajutsu/pull/507)）。
- 残るプール化の作業を五つの焦点を絞ったロードマップ項目（重み付き公平分配、能力別ルーティングキュー、制御プレーンのスケールアウト、高可用性、可観測性）へ分割し、このアンブレラを実装済みへ移して、出荷済みのセルフホストのベースラインを記録しました（[#674](https://github.com/bajutsu-e2e/bajutsu/pull/674)）。

## 参考

`bajutsu/serve/`、[docs/ja/self-hosting.md](../../docs/ja/self-hosting.md)（段階 A と段階 B の手順書）、
[`deploy/self-host/`](../../deploy/self-host/)（動かせる単一ノードの compose 一式）、
[cli.md](../../docs/ja/cli.md#serve)、[ci.md](../../docs/ja/ci.md)、
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
（公開を安全にするハードニング）、
[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging-ja.md)（可観測性の作業が
土台にする構造化 serve ログ）、
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)（cloud-hosting の対）、
[BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)
