[English](BE-0173-slim-web-worker-image.md) · **日本語**

# BE-0173 — 軽量な Linux web worker のコンテナイメージ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0173](BE-0173-slim-web-worker-image-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0173") |
| 実装 PR | [#718](https://github.com/bajutsu-e2e/bajutsu/pull/718) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads-ja.md), [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md), [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md), [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) |
<!-- /BE-METADATA -->

## はじめに

web（Playwright）backend 向けに、**軽量でコンテナ化された Linux worker** を用意します。これにより、ホスト型構成では `bajutsu worker` を手作業で構築したマシンではなく小さな OCI イメージとして動かせます。イメージには worker が実行時に本当に必要とする依存の閉包だけ、つまり base パッケージと `web` backend、そして run が実際に到達するアサーション用の extra（`visual`・`schema`）だけを載せ、control plane 側の依存（`server`・`db`・`oauth`・`cloud`）と AI の SDK（`ai`）は意図的に外します。これが可能になったのは [BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads-ja.md) が worker を **credential も cloud SDK も持たない**構成にしたからです。worker は control plane とオブジェクトストレージに対してすべて平文の HTTP で通信するため、worker のイメージには `boto3`/GCS SDK も、焼き込む cloud のシークレットも要りません。

## 動機

セルフホストのデプロイ一式（[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)、`deploy/self-host/`）は、現状では Linux の control plane をコンテナで、**Mac worker をベアメタルで**動かしています。Mac worker をコンテナ化していないのは、iOS Simulator（idb backend）のために Aqua の GUI セッションを必要とするからです。一方、web（Playwright）backend は **Linux 上でヘッドレスに**動くため、web worker にはその制約がなく、コンテナにできます。web にホスティングするときは、**フリートに Linux の web worker を入れることが目標の構成**になります。であれば、web worker には VM 上での場当たり的な `uv sync` ではなく、再現性のある一級のイメージが欲しくなります。

*スリムな*イメージをパッケージ全体のインストールで済ませず、あえて設計する価値があるのは次の2点からです。

- **デプロイの重さ。** 依存一式をそのまま入れる素朴なイメージは、control plane のスタック（`fastapi`/`uvicorn`、`sqlalchemy`/`alembic`/`psycopg`）、cloud の SDK（`boto3`・GCS）、AI の SDK（`anthropic`）まで引き込みます。これらは worker が一度も import しない、数百 MB 規模の wheel です。これらを落とせるようにしたのがまさに BE-0160 でした。worker のネットワーク依存は HTTP クライアントであって cloud SDK ではありません。残る重量は Playwright が必要とする Chromium バイナリが支配的ですが、それも固定ではありません。ヘッドレスの Linux worker はウィンドウを描画しないので、フルの headed ビルドではなく Chromium の**ヘッドレスシェル**（`playwright install --only-shell`）を入れます。これは Playwright がヘッドレス起動で既に自動選択するものです。これによりブラウザを数十 MB 削り、システムライブラリの依存も軽くなります。その上に積まれる避けられる分と合わせて、イメージが小さいほどスケールするフリート全体で pull と cold start が速くなります。
- **名前の付いた実行時閉包。** 現状のドキュメントは worker を `bajutsu[idb]` としてインストールさせていますが、run は `pillow`（visual アサーション）と `jsonschema`（`responseSchema` アサーション）にも到達します。つまり `bajutsu[idb]` だけでは*不足*で、そうした run はアサーションの時点で遅延的に失敗します。「worker が何をインストールすべきか」を表す単一の名前が、どちらの backend にも存在しません。この閉包を定義すれば Mac worker のインストールの穴も塞げますし、これはコンテナのビルドがそのまま入力として使うものです。

web worker はフリートにとって **新しい capability 軸**でもあります。[BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md) はジョブを capability で worker に振り分けますが、異種フリートを iOS ランタイムやデバイスクラスの違う Mac worker として描いており、**backend（idb か web か）**をルーティングの次元としてはまだ扱っていません。Linux の web worker は `backend=web` という capability を持ち込みます。スリムイメージはその capability を配る車体であり、BE-0166 は web ジョブをそこへ振り分ける配車です。本項目ではイメージと実行時閉包を追加し、BE-0166 に backend の次元を加える作業はそちら側で追跡します（*Detailed design* の §5 を参照）。

## 詳細設計

作業はデプロイ成果物に加えて、それを正しく保ちスリムさを保つためのパッケージングとガードです。ここには決定論的な `run`/CI ゲートに触れる要素も、判定の経路に LLM を載せる要素もありません。パッケージングと Dockerfile とドキュメントだけです。

### 1. worker 実行時閉包の extra を定義する

`pyproject.toml` に、backend ごとに worker が必要とするものを正確に表す合成 extra を、既存の単一目的 extra から組み立てて追加します（バージョンの二重管理はしません）。

- `worker-web = ["bajutsu[web,visual,schema]"]`
- `worker-idb = ["bajutsu[idb,visual,schema]"]`

`ai` extra は **opt-in** のまま閉包の外に置きます。worker が `ai` を必要とするのは、シナリオが AI の作成・調査経路（`record`、`run --dismiss-alerts`）を使うときだけで、これは base を AI-free に保つ方針とも整合します。コンテナのビルド（§2）と Mac worker のインストール手順の両方が `worker-web` / `worker-idb` を使うので、「worker が何をインストールするか」が一箇所にまとまります。

### 2. web worker 用のマルチステージ Dockerfile

`deploy/self-host/worker-web.Dockerfile` をマルチステージビルドとして追加します。

- **ビルドステージ**: `bajutsu[worker-web]` を仮想環境にインストールします（editable ではない、非 editable なインストール）。
- **最終ステージ**: スリムな Python ベース + 仮想環境 + Playwright が必要とする Chromium ブラウザだけを載せ、`server`/`db`/`oauth`/`cloud`/`ai` からは何も入れません。ブラウザは `playwright install --with-deps --only-shell chromium` で入れます。フルの headed Chromium ではなく**ヘッドレスシェル**です。Linux の worker は常にヘッドレスで、Playwright はヘッドレス起動でシェルを自動選択するため（ドライバは変更不要）、シェルは数十 MB 軽く、`--with-deps` のシステムライブラリも小さくなります。コンテナはサンドボックスを弱めない限り root では Chromium の実行を拒むので、最終ステージは worker を非特権ユーザで動かします（ドライバは既定のサンドボックス付き起動フラグのままで、app 非依存です）。この自前導入のスリムベースと upstream の Playwright イメージの比較は代替案 B で扱います。
- エントリポイントは `bajutsu worker` を実行し、ベアメタルの worker と同様に環境変数（`BAJUTSU_SERVER_URL`、`BAJUTSU_TOKEN`）で設定します。そして BE-0160 のとおり、オブジェクトストレージの credential は**一切**持ちません。

### 3. compose サービス + セルフホストのドキュメント

`deploy/self-host/docker-compose.yml` に **optional** な `worker-web` サービスを追加します（デフォルトは無効なので、idb のみのシングルテナント構成は変わりません）。そして **異種フリート**を `docs/self-hosting.md` とその日本語版に記述します。ベアメタル（Aqua GUI）の Mac idb worker と、コンテナの Linux web worker が、同じ control plane から HTTP でジョブを lease する構成です。web worker のイメージは cloud SDK もシークレットも必要としないこと（BE-0160）と、それを支える extra（§1）を明記します。

### 4. cold start / 閉包のガードテスト

worker の import 閉包がスリムなまま保たれることを検証するテストを追加します。`worker` コマンドの経路を import しても、`fastapi`/`uvicorn`・`sqlalchemy`・`boto3`/GCS・`anthropic` を import してはならない、という内容です。これにより、紛れ込んだ top-level import がイメージをこっそり肥大させたり worker の cold start を遅らせたりできなくなります。既存の import-guard の手法（`tests/serve/test_import_guard.py`）を worker のエントリに広げるもので、標準のゲートで動きます（Simulator 不要、Linux で動作）。

### 5. BE-0166: backend を capability 軸に加える（項目横断）

`web` のジョブは web worker だけが、`idb` のジョブは Mac worker だけが lease するよう、`backend`（idb か web か）をルーティングの次元として記録します。これは [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md) の設計への小さな追記であって、本項目で書く新規コードではありません。本項目と BE-0166 は相互に参照し合います。（BE-0166 が入るまでは、web のみ・idb のみの均質なフリートは影響を受けません。これが効いてくるのはフリートが backend を混在させたときだけです。）

## 検討した代替案

### A. worker のイメージにパッケージ全体を入れる

イメージを `bajutsu[web]` 一式や、依存の全部から組み立てる方法です。書くのは簡単ですが、worker が一度も import しない control plane / cloud / AI のスタックまで引き込みます。これが本項目で取り除く「避けられる重量」です。それらを安全に落とせるようにしたのが BE-0160 なので、落とさないのはその成果を捨てることになります。

### B. upstream の Playwright Python イメージを土台にするか、自分で `playwright install` するか

最終ステージを `mcr.microsoft.com/playwright/python` の上に構築すると、ブラウザと OS ライブラリがバージョン整合済みで導入された状態になりますが、ベースが大きく制御しにくくなり、外部ベースへの依存が増えます。Chromium を自分でスリムな Python ベースに入れると、ベースは最小で完全に自分たちの管理下に置けますが、`--with-deps` のシステムライブラリ導入を保守する手間が生じます。**採用したのは自前導入のスリムベース**です。狙いが最小イメージそのものである以上、ここでは制御しやすさが効きますし、upstream のイメージは意図的に外すフルの headed ブラウザを同梱してしまうためです。

### E. フルの headed Chromium か、ヘッドレスシェル（`--only-shell`）か

Playwright 1.49 以降、`playwright install chromium` はフルの headed な Chrome-for-Testing ビルドと、別個の `chromium-headless-shell` の両方を取得し、Playwright はヘッドレス起動でシェルを自動選択します。Linux の worker は常にヘッドレスなので、イメージにはシェルだけを入れ（`--only-shell`）、フルビルドをスキップします。ブラウザが数十 MB 軽く、`--with-deps` も小さくなり、しかも**コード変更は不要**です。ドライバの `launch(headless=True)` が既にシェルへ解決するからです。シェルは headed モード、ブラウザ拡張、ページ内 PDF 描画を落とし、スクリーンショットはフルの Chrome とピクセル単位で一致しませんが、いずれもヘッドレスの E2E worker のクリック / 遷移 / network interception / screenshot / 動画の経路には影響しません。（visual のベースラインは元々 backend とプラットフォームに固有で、同じ worker で取得・比較するので、シェルが新しい parity の軸を持ち込むわけでもありません。）フルビルドを保持すると worker が一度も使わない機能のためにイメージサイズを払うことになるので、採用しません。

### C. Mac（idb）worker もコンテナ化する

スコープ外であり、実現できません。idb backend は Aqua の GUI セッションと iOS Simulator を必要とし、Linux コンテナはそれを提供できないからです。Mac worker はベアメタルのままです。§1 の `worker-idb` extra はそのベアメタルのインストールを改善します。コンテナの話は性質上 web 専用です。

### D. worker を独立した配布物（`bajutsu-worker` wheel）に分割する

worker/run/backend のコードだけを持つ2つ目のパッケージを公開する方法です。これは `serve`/`mcp`/`ai` モジュールの*ソース*を削れますが、それは無視できるサイズの純粋な Python です。真の重量は依存にあり、そこは extra（§1）が既にゲートしています。配布物を分割することは「1 パッケージ = 1 つの決定論的コア」という形に反し、わずかなサイズ削減のためにリリース手順の負担を増やすので、採用しません。

## 進捗

> 作業の進行に合わせて最新に保ってください。チェックリストは *Detailed design* の MECE な作業分解を反映し（作業単位ごとに1つ）、ログは変更内容とその時期を（古い順に）PR へのリンク付きで記録します。

- [x] `worker-web` / `worker-idb` の実行時閉包 extra を `pyproject.toml` に定義する
- [x] `deploy/self-host/` にマルチステージの web worker Dockerfile を追加する
- [x] optional な `worker-web` compose サービス + `docs/self-hosting.md`（と日本語版）の異種フリート記述
- [x] worker エントリの cold start / import 閉包ガードテスト
- [x] BE-0166 に `backend` の capability 軸を追記する（相互参照）

[#718](https://github.com/bajutsu-e2e/bajutsu/pull/718) — スリムな web worker イメージを追加。`worker-web` / `worker-idb` の閉包 extra、`bajutsu[worker-web]` と Chromium のヘッドレスシェル（`--only-shell`）だけを入れて非特権ユーザで動かすマルチステージの `deploy/self-host/worker-web.Dockerfile`、既定で無効（`web-worker` profile の裏）の optional な `worker-web` compose サービスと両言語の異種フリート記述、worker の import 閉包ガード（`tests/serve/test_import_guard.py`）、そして BE-0166 への相互参照となる `backend` capability 軸の追記を含みます。

## 参考

- [BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads-ja.md) — credential も cloud SDK も持たない worker（enabler。worker のイメージに cloud SDK もシークレットも要らなくなった）
- [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md) — capability ルーティングのキュー（web ジョブを web worker へ振り分ける配車。`backend` を capability 軸として得る）
- [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) — Web UI のセルフホスト（このイメージが加わるデプロイ一式）
- [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md) — 完了後 worker モデル（イメージが動かす worker↔control plane の HTTP ループ）
- [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) — web（Playwright）backend（コンテナ化した worker を可能にする、Linux 上でヘッドレスに動く backend）
- `pyproject.toml` — この提案が組み立てる `worker`（BE-0160 以降は空）・`web`・`visual`・`schema` の extra
- `deploy/self-host/` — web worker のイメージとサービスが拡張する compose + Dockerfile 一式
- `docs/self-hosting.md` — 異種フリート（Mac idb + Linux web）を追記するトポロジのドキュメント
