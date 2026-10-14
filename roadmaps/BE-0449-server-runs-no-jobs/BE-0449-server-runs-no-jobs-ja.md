[English](BE-0449-server-runs-no-jobs.md) · **日本語**

# BE-0449 — server はジョブを実行せず、すべてのジョブを worker が実行する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0449](BE-0449-server-runs-no-jobs-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0449") |
| 実装 PR | [#2167](https://github.com/bajutsu-e2e/bajutsu/pull/2167)（単位 1 の 3 分割のうち 1）、[#2168](https://github.com/bajutsu-e2e/bajutsu/pull/2168)（単位 1 の 3 分割のうち 2）、[#2170](https://github.com/bajutsu-e2e/bajutsu/pull/2170)（単位 1 の 3 分割のうち 3）、[#2171](https://github.com/bajutsu-e2e/bajutsu/pull/2171)（単位 2）、[#2172](https://github.com/bajutsu-e2e/bajutsu/pull/2172)（単位 4）、[#2173](https://github.com/bajutsu-e2e/bajutsu/pull/2173)（単位 3）、[#2182](https://github.com/bajutsu-e2e/bajutsu/pull/2182)（単位 5 の 2 分割のうち 1：機能を失わないローカル起動） |
| トピック | Hosting the web UI |
<!-- /BE-METADATA -->

## はじめに

server は、制御を担うだけの存在になります。リクエストを受け付け、キューを持ち、UI とレポートを提供しますが、ジョブは一切実行しません。ここでの**ジョブ**は、キューを通る仕事の単位で、`run`（ビルドの段階を含む）、`run-set`（Device Farm へのファンアウト）、`triage`、`record`、`crawl` です。ジョブは、iOS、Android、web を問わず、worker が実行します。1 台のマシンでは、運用者が `bajutsu serve` と `bajutsu worker` の 2 つのプロセスを起動します。server なしでは、`bajutsu worker --once` が 1 つのジョブ仕様を実行し、`bajutsu run` は、その 1 回実行モードの薄いラッパーになります。

現在、server がジョブを実行する方法は 2 つあります。local バックエンドは server のプロセスの中で実行し、ホスト型のバックエンドは worker のためにキューに入れます。本項目は、前者を取り除いて、実行の経路を 1 本にし、どの機能も 1 回だけ書けば済むようにします。キューの外でデバイスやモデルを使う対話的な補助操作（capture セッション、enrich、`doctor` の画面の探り、Simulator の一覧、スクリーンショット）はジョブではなく、それらの移行は別の作業です。

## 動機

local バックエンドは、既定で `LocalExecutor` を使います（[`serve_state.py:138`](../../bajutsu/serve/state/serve_state.py)）。server バックエンドは、`BAJUTSU_DATABASE_URL` が設定されていれば `DbQueueExecutor` を選び、なければ `LocalExecutor` に戻ります（[`serve/__init__.py:582`](../../bajutsu/serve/__init__.py)）。local の executor は、ジョブごとにスレッドを起こし、`run_job` が `python -m bajutsu run`（または `record`、`crawl` など）をサブプロセスとして起動します（[`local_executor.py`](../../bajutsu/serve/executor/local_executor.py)、[`jobs.py`](../../bajutsu/serve/jobs.py)）。worker の経路は、`execute_job_spec` を通じて同じ `run_job` に届きます（[`worker_job.py`](../../bajutsu/serve/server/worker_job.py)）。ただしその周りには、lease、ハートビート、署名付きアップロード、データベースを往復しなければならないジョブ仕様という、もう 1 組の仕組みがあります。

2 つの経路を持ち続けると、どの機能も 2 つの経路にまたがる機能になり、2 つ目の経路が遅れます。3 つの例があります。

- **Device Farm の投入は、server のプロセスの中でしか動きません。** worker の経路は、パッケージの置き場を持たない状態を自分で作るので、キューから lease した Device Farm のジョブは検証に失敗します（[`jobs.py`](../../bajutsu/serve/jobs.py)）。
- **BE-0431 のジョブ単位のアーティファクトの上書きは、worker の経路でしか動きません。** 単一プロセスの server はそれを拒否し、`run-set` は、ファンアウトがまだ分割構成で動かないため、すべての構成で拒否します（[`dispatch.py`](../../bajutsu/serve/operations/dispatch.py)）。
- **crawl のライブの探索グラフは、共有ファイルシステムを前提にしています。** worker の分割がそれを壊します。[BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md) はこの欠落を記録して Deferred になり、[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md) は crawl が control plane から出ないことを前提としていました。

実装後に確認できる違いは 3 つあります。server のプロセスは、`bajutsu run`、`record`、`crawl` のサブプロセスを起動せず、テストでそれを確かめられます。worker が動いていないときに投入したジョブは、キューに残り、UI は、ジョブが server の中で動く代わりに、利用できる worker がないと伝えます。そしてホスト型の worker で動く機能、たとえば Device Farm の投入は、追加のコードなしに、1 台のマシンでも動きます。

## 詳細設計

### 役割

| | server（`bajutsu serve`） | worker（`bajutsu worker`） |
|---|---|---|
| リクエストを受け付け、UI とレポートを提供する | する | しない |
| キュー、セッション、設定の紐付け、実行の索引を持つ | 持つ | 持たない |
| ジョブを実行する（デバイス、ブラウザ、ビルド、AI 呼び出し、クラウドへの投入） | 決してしない | 常にする |
| Device Farm の投入に使う AWS のロールを持つ | 持たない | 持つ |
| AI のキーと、ユーザーが UI で設定した secrets を持つ | 現在と同じく書き込み専用で持つ | ジョブごとに受け取る |

### secrets と認証情報

現在、UI は AI のキーと `${secrets.X}` の値を server の環境に書き、起動された実行がそれを継承します（[`jobs.py`](../../bajutsu/serve/jobs.py)）。worker は server と環境を共有しないので、値を運ぶ必要があります。server は、値を現在と同じく書き込み専用（[BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)）で持ち、ジョブに要る値を、worker の lease の応答で渡します。値は、キューがデータベースに保存するジョブ仕様には入りません。Device Farm の投入に使う AWS のロールは運びません。Device Farm の投入の項目が述べるとおり、worker が自分の環境に持ちます。

### 1 台のマシンと 2 つのプロセス

1 台のマシンでは、運用者が `bajutsu serve` と `bajutsu worker` を起動します。server だけでは何も実行されず、server はそのことを伝えます。稼働中の worker がないキュー中のジョブには、「利用できる worker がない」という通知が付きます。worker の起動を忘れた運用者は、通知を見て、黙って待たずに済みます。

ホスト型のデプロイは、すでにキュー、セッション、アーティファクトの置き場を必要とします。ローカルでの起動は、クラウドのサービスなしにそれらを用意しなければなりません。ローカルの起動にリポジトリを付けると、保存先以外も変わるので、既定値を列挙します。

- **キューの保存先**は、既定で SQLite です。runs のディレクトリの隣のファイルとし、キュー中のジョブは再起動後も残ります。PostgreSQL は、現在と同じく `BAJUTSU_DATABASE_URL` で選びます。worker は HTTP で server に届き、データベースを開かないので、同時に書くのは server 自身のリクエストのスレッドです。SQLite は `FOR UPDATE SKIP LOCKED` を使わないので、すべての状態遷移を、更新した行数を確かめる条件つきの `UPDATE` にし、遷移ごとに述語を持たせます。lease は `status = 'queued'`、ハートビート、結果、キャンセルは `status = 'leased' AND leased_by = :worker`、回収は `status = 'leased' AND leased_at < :cutoff` です。あるいは、各遷移を `BEGIN IMMEDIATE` の下で実行します。ファイルに置いた SQLite で、同時に lease するテストを行い、ハートビートと回収を競わせます。エンジンは、ログ先行書き込み（write-ahead logging、WAL）とビジータイムアウトを設定します。
- **マイグレーション**は、既定の SQLite では自動で実行します。現在の server は Alembic を実行しないからです。
- **secrets の鍵** `BAJUTSU_SECRETS_KEY` は、現在、データベースを使う起動で必須です。ローカルの起動は、鍵を生成して runs のディレクトリの隣に保存します。ファイルは、所有者だけが読める権限（`0600`）の通常ファイル（symlink ではないもの）として原子的に作成し、安全でない既存のパスがあれば起動に失敗します。秘密を含むファイルの扱いは、`bajutsu/common/run_meta/artifact_perms.py` に合わせます。共有ホストで誰でも読める鍵は、BE-0136 の保護を無効にするからです。
- **`hosted`** は、デプロイの設定のままにします。リポジトリがそれを含意すると、BE-0108 のファイルブラウザが消え、デバイスの前にいる作者に対して `record` のデバイスの引き継ぎ（BE-0185）が拒否されます。
- **実行の履歴**は、リポジトリがあるとデータベースの窓から取るので、コマンドラインの `bajutsu run` が書いた実行や、`runs/` にすでにある実行が、ローカルの UI から消えます。server は、`runs/` の下の実行のツリー（すでにあるものも、コマンドラインの `bajutsu run` が書いたものも）を、起動時と再走査のときに runs テーブルへ索引します。ファイルのストアは default org の `artifacts/` プレフィックスに runs のツリーをマウントするので、それらのアーティファクトも解決できます。target は、リポジトリがあると組織ごとに分かれるので、ローカルの起動は 1 つの既定の組織を使います。
- **アーティファクトの保存先**には、ファイルシステムの実装を加えます。オブジェクトストアが受け付けるのは、現在 `s3://` と `gs://` だけです（[`object_store.py`](../../bajutsu/serve/server/object_store.py)）。`file://` のストアがあれば、worker が実行のツリーを書き、server が読めます。worker はこのストアにも、[BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads-ja.md) の署名付き URL のインターフェースで話しかけ、このストアの URL は server が提供します。各 URL は、server が生成して保存する HMAC の鍵（secrets の鍵と同じ規則で保存します。原子的な `0600` での作成、通常ファイル、安全でない既存のパスの拒否）で署名し、有効期限を持ち、オブジェクトのキーと content type を署名に結び付け、既存のキーの検証でストアのルートに閉じ込めます。経路は、それ以外を受け付けません。

local バックエンドと server バックエンドの違いは、これらの既定値だけになります。`--backend` フラグは意味を失うので、1 リリース非推奨にして、次のリリースで削除します。

### 分割をまたぐキャンセル

ジョブのキャンセルは、現在、server のメモリ上の `Job` にしか届かず、キュー中のジョブでは、その `Job` はプロセスを持ちません。ローカルでは、server は `run` を穏やかに止め（BE-0370）、他の種類はプロセスグループを終了させます。worker では、ハートビートが 409 を返すと、worker は実行が終わるのを待ちます。分割には、専用のキャンセルの経路が要ります。

- キュー中のジョブのキャンセルは、行をキャンセル済みにし、lease はそれを飛ばします。キャンセルはリポジトリからジョブを見つけるので、再起動の前にキューに入ったジョブもキャンセルできます。
- lease 済みのジョブのキャンセルは、行を leased のまま、キャンセル要求のフラグを立てます。フラグは、ハートビートの応答が 200 で返します。worker は、自分の `Job` をキャンセルします。`run` は穏やかに、他の種類はプロセスグループの終了で行います。結果の経路は、lease の持ち主からのキャンセル済みの結果を受け付け、それだけが行をキャンセル済みに移します。

### 分割をまたぐライブの出力

ホスト型のモデルは、worker のコンソールログを、ジョブの完了後に届けます（[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md)、[`post_completion_logbus.py`](../../bajutsu/serve/server/post_completion_logbus.py)）。ローカルでの利用がそれを迂回していたあいだは、それで足りました。ローカルでの利用も worker を通るようになると、コンソールは再びライブでなければならず、`crawl` は探索グラフが育つあいだそれを必要とします。そこで worker はストリームを送ります。ログの断片とライブのアーティファクトの断片を、ハートビートの経路と同じく lease の持ち主を検査する worker 用の経路に送信し、server は、書き込みを受け付けるログバス（単一の server のプロセスならメモリ上のバス、複製をまたぐなら `RedisLogBus`）に、書き込みを無視する完了後のバスの代わりに公開し、Server-Sent Events（SSE）でブラウザに中継します。アップロードされた `console.log` は、耐久性のある写しとして残ります。ログのストリームは、どの種類を移すよりも先に入れるので、移した `run` もライブのコンソールを保ちます。アーティファクトのストリームは `crawl` とともに入り、最初の利用者は crawl のグラフです。これは [BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md) が未解決にしていた範囲です。

`record` は、人へ制御を渡すこともあります。現在、server は、人の応答を実行の標準入力に書きます（BE-0179）。その入力は worker が持つので、応答は逆向きに、server から worker へ、ハートビートの応答に載せて運び、worker が書きます。デバイスの引き継ぎは、server のホストではなく、worker のホストに従います。

### 1 回実行の worker

`bajutsu worker --once <spec>` は、server なしで 1 つのジョブ仕様を実行し、ジョブの終了コードで終了します。仕様は、キューがすでに保存している形（`worker_job.py` の `job_spec`）です。worker は、`--worker-config` で `worker.yaml` を読み、worker capability の検査を実行し、HTTP と署名付き URL の代わりにローカルの入出力で `execute_job_spec` を通してジョブを実行し、1 回実行のモードでは、リースしたジョブが使う、パイプでつないで統合した取得の代わりに、標準入力、標準出力、標準エラーを継承した子プロセスを起動します。実行の ID は、PASS の行ではなく、子が書いた実行のツリーから取り、起動する内部の run の入口には、解決した `--worker-config` のパスを渡します。アップロード済みのバンドルやアーティファクトの上書きを名指しする仕様は、明確なメッセージで拒否します。それらは署名付き URL で届き、1 回実行の worker にはそれがないからです。`worker --once` は、SIGINT と SIGTERM を、リース済みのジョブがハートビートで受け取るのと同じキャンセルに対応づけます。同じコマンドが、テスト、調査、次のラッパーに使われます。

### 薄いラッパーとしての `bajutsu run`

`bajutsu run` は、引数からジョブ仕様を作り、1 回実行の worker の経路を呼ぶので、コマンドラインと lease したジョブが 1 つの実装を通ります。避けるべき罠が 1 つあります。`run_job` は `job.cmd` に入っているものをそのまま起動し、現在の `run_command` は `python -m bajutsu run` を出すので、`run_job` を呼ぶ公開の `run` は、自分自身を起動してしまいます。そこで現在の `run` の本体を内部の入口に移し、仕様を作る側がその入口を出すようにします。変更の前にキューに入った仕様は、なお `-m bajutsu run` を名指ししますが、ラッパーに入り、ラッパーが内部の入口を 1 回起動するので、再帰はしません。

ラッパーは、公開の挙動を保ちます。

- 作る仕様は `udids` と `build` を空にするので、コマンドラインは自分のデバイスの取得をそのまま使います。
- 内部のプロセスは標準入力、標準出力、標準エラーを継承するので、PASS または FAIL の行は標準出力に、進行は標準エラーに残ります。
- SIGINT と SIGTERM は同じキャンセルの経路に渡すので、割り込みが、デバイスを操作し続ける実行を取り残すことはありません。
- ラッパーは、子の終了コードで終了し、シグナルで終わったときは 128 にシグナル番号を足した値で終了します。

同等性のテストは、`bajutsu run` をサブプロセスとして実行します。既存のスイートはプロセス内で呼ぶので、増えた層を捉えられないからです。この層は 1 つのプロセスを費やしますが、パイプラインを 1 本にするための費用として受け入れます。

ラッパーはローカルのコマンドなので、`environment` が `local` でない `worker.yaml` を拒否します。Device Farm への投入は、環境が `devicefarm` の worker の仕事です。そのファイルで `bajutsu worker --once` を実行すれば、server なしで 1 つの Device Farm のジョブ仕様を投入できます。

### executor の継ぎ目

移行の期間は、ジョブの種類ごとに 1 つのルーティング用の executor が選びます。移した種類は `DbQueueExecutor`、残りは `LocalExecutor` です。最後には、`RunExecutor` の実装は `DbQueueExecutor` だけになります。`LocalExecutor`、単一プロセスの server のために存在する `dispatch` の上書き拒否の分岐、server の `_run_batch_job`、server の `register_batch_providers` は、取り除きます。server 側の Device Farm の数を絞るためだけに存在した設定も取り除きます。target の `cloudBatchBudget`、依頼の `deviceBudget`、ジョブ管理の `max_concurrent_batch`、`try_register(device_budget=…)` です。この削除は、Device Farm の投入の項目が担い、`maxJobConcurrency` を名指しした通知を伴う 1 リリースの非推奨のあとに行います。config のスキーマは未知のキーを拒否するので、いきなり削除すると既存のファイルが壊れるからです。そのあと、Device Farm の同時実行の数は、worker の `maxJobConcurrency` になります。

### 作業の順序

終着点は、server が何も実行しないことで、そこへの道は、ジョブの種類ごとに 1 つずつ進め、それぞれの段階を単独でレビューできるようにします。移した種類はどれもローカルの既定値、キャンセルの経路、1 回実行の worker、ログのストリームを必要とするので、これらを先に入れます。次に `run`、続けて `run-set`（Device Farm へのファンアウト）、次に `triage`、最後に `record` と `crawl` を移します。後の 2 つは、アーティファクトのストリームと引き継ぎの中継を必要とするからです。`LocalExecutor` を取り除くのは、最後の種類が移ってからです。

### 境界

本項目は、ジョブが何をするか、判定をどう決めるか、実行のツリーを変えません。server を通らなかった CLI だけのコマンドも、「はじめに」で挙げた対話的な補助操作も、当面 server に残します。1 台のマシンの UI は、worker のプロセス 1 つにつき、1 度に 1 つのジョブを動かします。ローカルの worker が受け付ける `maxJobConcurrency` は 1 だけだからです。ジョブごとのデバイス割り当てが入るまでの回避策は、それぞれ専用のデバイスを持つ複数の worker のプロセスです。レビューで見てほしい未解決の点が 4 つあります。

- ジョブ管理の全体、ユーザー単位、組織単位の上限は、待機中のジョブも数え、上限に達すると `run-set` は部分的な成功を返します。クラウドのジョブを上限から除くかどうかは、未決です。
- ファイルシステムのストアの署名付き URL は server が提供するので、同じマシンの worker から server に届く必要があります。ローカルの worker は、ループバックのアドレスを使います。
- 1 つの SQLite ファイルから 2 つの worker が同時に lease するときは、上の条件つきの更新に頼り、同時 lease のテストがその確認になります。
- server は、単一のホスト OS でしか動かないドライバーのジョブの必須集合に `host:<os>` トークンを加えるので、まだ `host:*` を広告しない worker は、それに合致しません。server より先に worker を更新します。

### プライムディレクティブへの適合

- **AI による判定の排除。** `triage`、`record`、`crawl` の AI 呼び出しを worker に移すことは、それが動く場所を変えるだけで、決めることは変えません。`run` の経路は、引き続きモデル呼び出しを含まず、判定は機械的に検査できるアサーションから決まります。
- **決定性優先。** 2 本の実行経路が 1 本になるので、修正も機能も 1 回で済み、利用できる worker がないという通知が、何も知らせない待機を置き換えます。
- **アプリ非依存。** 本項目が触れるのは server と worker で、アプリの設定は変わりません。

### 作業分解（MECE）

1. **ローカルの既定値。** 署名付き URL を持つファイルシステムのオブジェクトストア、安全な条件つきの lease を持つ SQLite の既定値、自動のマイグレーション、生成する secrets の鍵、独立したままの `hosted`、コマンドラインと既存の実行を残す実行の履歴を加え、`bajutsu serve` がクラウドのサービスなしに起動できるようにします。
2. **分割をまたぐキャンセル。** キュー中のジョブをキャンセル済みにし、ハートビートの応答にキャンセルのフラグを載せ、worker が自分のジョブをキャンセルするようにします。
3. **1 回実行の worker とラッパー。** `bajutsu worker --once`、仕様を作る側が出す内部の run の入口、ストリーム、シグナル、終了コードの規則を持つ `bajutsu run` のラッパーを加えます。
4. **ライブのログのストリームとルーティング用の executor。** ログのストリームの経路と server 側の中継を加え、移行の期間、ジョブの種類ごとに `DbQueueExecutor` か `LocalExecutor` へ振り分けます。
5. **`run` の移行。** `run`（ビルドの段階を含む）を `DbQueueExecutor` だけに通し、AI のキーと secrets を lease の応答で中継し、利用できる worker がないという通知を加えます。
6. **`run-set`（Device Farm の batch）の移行。** これは Device Farm の投入の項目が担います。server 内の投入を取り除き、server 側の予算の設定は、1 リリースの非推奨を経て取り除きます。
7. **`triage` の移行。** worker で実行します。
8. **`record` と `crawl` の移行。** worker で実行し、ライブの crawl のグラフをアーティファクトのストリームに載せ、`record` の引き継ぎの中継を加えます。
9. **local の executor の削除。** `LocalExecutor` とプロセス内の分岐を削除し、`--backend` を非推奨にし、`docs/self-hosting.md`、`docs/architecture.md`、`docs/cli.md` とその日本語ミラーを更新します。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| local の executor を残す | 1 台のマシンでは、server が自分でジョブを実行します。 | 実行の経路が 2 本残り、どの機能も、一方の経路に先に入ったままです。 |
| server が worker を内包する | `bajutsu serve` が同じプロセスの中で worker を動かします。 | 起動のモードが 1 つ増え、本項目が引く境界が隠れます。2 つのプロセスなら、どのデプロイも同じ形になります。 |
| `bajutsu run` をそのままにする | `run` が、worker のパイプラインの隣に自分のパイプラインを持ち続けます。 | コマンドラインと lease したジョブが、食い違いうる 2 つの実装を持ち続けます。 |
| run 系だけを移す | `record` と `crawl` は server に残します。 | server がジョブを実行し続け、local の executor を取り除けません。 |
| すべての種類を一度に移す | 1 つの変更で、全種類とライブのストリームを移します。 | 変更が大きすぎてレビューできず、1 つの種類の回帰が残りを止めます。 |
| UI の secrets のエンドポイントを廃止する | secrets は worker の環境でだけ設定します。 | UI で secrets を設定していた利用者が、その手段を失います。ジョブごとの中継なら残せます。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] ローカルの既定値
- [x] 分割をまたぐキャンセル
- [x] 1 回実行の worker とラッパー
- [x] ライブのログのストリームとルーティング用の executor
- [ ] `run` の移行
- [ ] `run-set`（Device Farm の batch）の移行
- [ ] `triage` の移行
- [ ] `record` と `crawl` の移行
- [ ] local の executor の削除

ログ：

- [#2167](https://github.com/bajutsu-e2e/bajutsu/pull/2167)（単位 1 の 3 分割のうち 1：キューのストア）— `SqlRepository` のジョブの状態遷移
  （リース、heartbeat、結果、reclaim）を、それぞれ 1 つの条件付き `UPDATE` にしました。競合の勝ち負けは
  更新した行数で決まり、SQLite でも PostgreSQL でも同じです。これまでは読み取りのあとに書き込んでいて、
  SQLite では行ロックを取っていませんでした。ファイルに置いた SQLite のエンジンには、ログ先行書き込み
  （WAL）と busy timeout を設定します。`repository_from_env(default_url=…)` は、既定のデータベースを
  使う前に最新のリビジョンへマイグレーションします。一方、運用者が設定した `BAJUTSU_DATABASE_URL` は、
  これまでどおりサーバーがマイグレーションしません。ファイルに置いた SQLite で並行してリースする
  テストと、heartbeat と reclaim を競わせるテストを加えました。単位 1 の残り（秘密鍵の生成、署名付き
  URL を使う `file://` の成果物ストア、ローカル起動の配線）は後続の PR で入れます。
- [#2168](https://github.com/bajutsu-e2e/bajutsu/pull/2168)（単位 1 の 3 分割のうち 2：鍵とファイルストア）— `local_keys` が秘密鍵と URL の
  署名鍵を初回に生成します。保存先は、runs のディレクトリの隣の `.bajutsu/` です。鍵のファイルは、
  `O_EXCL | O_NOFOLLOW` で開いたモード `0600` の一時ファイルに書いて同期し、ハードリンクで所定の名前に
  置きます。このため、同時に起動したプロセスは先に置かれた鍵を読みます。既存のパスがシンボリックリンク、
  通常のファイル以外、他のアカウントの所有、グループや他者に開いた権限のいずれかに当たるときは、
  使わずに拒否します。`fernet_from_env_or_local` は `BAJUTSU_SECRETS_KEY` を優先し、未設定のときは
  ローカルの鍵を使います。`FileObjectStore` はディレクトリの上に `ObjectStore` を実装し、どのキーも
  シンボリックリンクを経由する場合を含めてルートの内側に閉じ込め、所有者だけの権限で不可分に書き込みます。
  署名付き URL の宛先は、FastAPI のアプリに加えた `GET` と `PUT` の `/api/store/objects/{key}` です。
  この経路を認可するのは署名だけで、署名はメソッド、キー、コンテンツタイプ、有効期限に対する
  HMAC-SHA256 です。経路はオブジェクトストアが `FileObjectStore` のときだけ存在し、デフォルトの起動は、
  3 番目の PR で配線するまで変わりません。
- [#2170](https://github.com/bajutsu-e2e/bajutsu/pull/2170)（単位 1 の 3 分割のうち 3：ローカル起動）— `bajutsu serve --backend server` が、
  クラウドのサービスや環境変数がなくても起動するようになりました。`BAJUTSU_SERVER_STORE` と
  `BAJUTSU_DATABASE_URL` がどちらも未設定なら、サーバーは `.bajutsu/queue.db` にマイグレーション済みの
  SQLite のキューを持ちます。シークレットを暗号化するキーは、`BAJUTSU_SECRETS_KEY` が未設定なら
  `.bajutsu/secrets.key` に生成します。設定したデータベースに `BAJUTSU_SECRETS_KEY` がない場合は、
  これまでどおり起動に失敗します。バケットを指定してデータベースを指定しないデプロイは、これまでどおり
  プロセス内で実行します。レプリカごとに SQLite のファイルを持つとキューが分かれるためです。バケットが
  なければ、成果物ストアは `FileObjectStore` です。バケットと同じキーの配置で `.bajutsu/objects/` に
  置き、default org の `artifacts/` プレフィックスにだけ runs のツリーをマウントします。このため、
  `bajutsu run` が書いた run も動かさずに読め、default org のストアから他の org の run やストアの
  フォルダを指すこともできません。ストアはローカルの鍵で署名し、宛先はサーバーのループバックの URL です。
  そのためローカル起動は `--port 0` を拒否し、署名付き URL の経路を配信する FastAPI のトランスポートへ
  自動で切り替えます。`hosted` は `BAJUTSU_HOSTED` から読むようにしました。未設定のときはバケットを
  指定したかどうかに従うので、ローカル起動ではファイルブラウザとデバイスの引き継ぎが残ります。
  サーバーは起動時に、`runs/` の下の run のツリーをすべて、既定の org の runs テーブルへ登録します。
  登録には新しい追加専用の `record_run_if_absent` を使うので、完了した job の行を上書きしません。
  admin の `POST /api/runs/rescan` は同じ登録を必要なときに行い、ローカルの配置がなければ `409` を
  返します。これで単位 1 が完了しました。
- [#2171](https://github.com/bajutsu-e2e/bajutsu/pull/2171)（単位 2）— キャンセルが、分割のどちら側にある job にも届くようになりました。
  キャンセルの経路はまず jobs テーブルから job を探すので、再起動の前にキューへ入った job もキャンセル
  できます。行のない job は、これまでどおりプロセス内でキャンセルします。キュー中の job は
  `status = 'queued'` を条件とする `UPDATE` で `cancelled` になり、リースが返すことはありません。
  リース済みの job はリースを保ったまま、新しい `cancel_requested` 列（マイグレーション `0022`）に
  印を付けます。ハートビートは `{"ok": true, "cancel": true}` を返し、worker は `jobs.cancel_job` で
  自分の `Job` をキャンセルします。`run` は穏当に止め、それ以外の種類はプロセスグループごと終了します。
  `execute_job_spec` は run の開始前に、組み立てた `Job` をハートビートのループへ渡します。バンドルの
  取得中に届いたキャンセルでは、run を始めません。結果の経路は `"cancelled": true` をリースの持ち主から
  だけ受け付け、`status = 'leased' AND leased_by = :worker` を条件とする `UPDATE` で記録します。job が
  終わったあとのキャンセルは何も変えず、終了時の状態を返します。キャンセルの印が付いたままリースが
  回収された job は、試行回数の上限に達していても、キューへ戻さず `cancelled` で終えます。`cancelled` は
  終了状態に加わり、job の同時実行枠を解放し、ログのストリームを閉じます。`0022` のダウングレードは
  `cancelled` を `failed` に戻します。job の登録からキュー投入までのあいだに届いたキャンセルも、どちらの
  順序でも行に届きます。`DbQueueExecutor.dispatch` はメモリ上でキャンセル済みの job の行をキャンセルし、
  キャンセルの経路はプロセス内のキャンセルのあとで jobs テーブルを読み直します。サインインした呼び出し元は、
  プロセス内の job も jobs テーブルの job も、その org が自分のアクティブな org か所属する org のときに
  限りキャンセルできます。それ以外の job には `404` を返します。job のビューは、人に対しては org で
  絞り込まないままです。経路は machine principal も job の読み出しと同じく自分の
  org に限りますが、ゲートが machine をキャンセルの経路に通さないので、この検査は防御のためのものです。
  リース済みの job のキャンセルには、この版以降の worker が要ります。古い worker はハートビートの
  `cancel` を無視するからです。`worker --once` での SIGINT と SIGTERM の対応付けは単位 3 で入ります。
- [#2172](https://github.com/bajutsu-e2e/bajutsu/pull/2172)（単位 4）— キューに入った job のコンソールが、再びライブで表示されるようになりました。
  worker はログの行を、新しい `POST /api/worker/log` へおよそ 1 秒ごとに送り、結果の送信の前にも
  5 秒の持ち時間でもう 1 度送ります。状態を持つ行単位の伏せ字処理が資格情報の形を隠し、秘密鍵の
  ブロックは BEGIN の行から END の行まで、行と送信をまたいで伏せ字にします。server も、届いた各行に
  行単位の伏せ字処理をもう 1 度かけます。ハンドオフの要求の行はライブのストリームに流しません。経路は、
  新しい読み取り専用の `Repository.holds_lease` でリースを持つ worker の送信だけを受け付け、それ以外には
  `409` を返します。worker はその `409` を、ハートビートの `409` と同じくリースの喪失として扱います。
  1 回の送信は 500 行までで、8 KiB を超える行は server が切り詰めます。1 つの job のライブの行が
  50,000 行か 16 MiB を超えると、server は打ち切りの通知を 1 行だけ流して残りを捨てます。この行数は server の
  プロセスごとに数えます。worker は送れなかった行をログに記録して捨て、未送信の行の待ち行列に上限を
  設け、古い control plane から `401`、`404`、`405` が返ると送信をやめます。server 側では、リポジトリが
  あるとき（ローカルの起動を含みます）、完了後のログのバスに代えて新しい `QueueLogBus` を使います。
  `QueueLogBus` は届いた行をメモリ上のバスへ流し、新しい `BAJUTSU_REDIS_URL` があれば `RedisLogBus` へ
  流します。`RedisLogBus` は送信の 1 回分を 1 つのコマンドで積み、書き込みのたびにリストの寿命を
  設定するようになりました。メモリ上のバスは、終わった job のチャネルを、後から 256 個の job が
  終わった時点で捨てます。ストリームを閉じるのは、これまでどおり結果の経路です。ストリームは待機中に
  jobs テーブルも確認するので、閉じる操作がこのバスに届かずに終わった job でも、ストリームは終わります。
  終わった job を開いた閲覧者と、1 行も受け取らなかったライブの閲覧者は、アップロードされた
  `console.log` を読みます。ライブのバスが失敗した場合も、その完了後の読み出しに戻ります。`Job.kind` は、
  `batch` か、`cmd` が起動するサブコマンドからジョブの種類を導きます。`RoutingExecutor` は、
  `queued_kinds` に含まれる種類と種類の不明な job を `DbQueueExecutor` へ、それ以外を `LocalExecutor` へ
  送ります。server はすべての種類をキューに送る設定で組むので、振る舞いは変わりません。ジョブ単位の
  アーティファクトの上書きを拒む分岐は、executor の型ではなく、`run` の job の行き先で判断するように
  なりました。バケットだけの構成と `--backend local` は変わりません。worker は、`execute_job_spec` の新しい
  `before_upload` のフックを通じて、run ツリーのアップロードの前に `console.log` をそのツリーへ書くように
  なりました。これまではアップロードのあとに書いていたため、`console.log` はアーティファクトストアに
  届いていませんでした。`crawl` のためのライブのアーティファクトのストリームは、単位 8 で入ります。
- [#2173](https://github.com/bajutsu-e2e/bajutsu/pull/2173)（単位 3）— `bajutsu run` は、1 回実行の worker 経路を通る薄いラッパーへ変わりました。
  コマンドの本体は内部の入口 `python -m bajutsu.run.internal` に移しました。隠しサブコマンドではなく
  独立したモジュールにしたので、`bajutsu --help` にも BE-0101 のコマンド一覧にも現れず、そのプロセスは
  run の本体だけを読み込みます。`run_command`、`triage --rerun` のコマンド、MCP の `run` ツールは
  このモジュールを出力します。`Job.kind` と `_with_worker_config` はこのモジュールを認識し、変更前に
  キューに入った仕様の `-m bajutsu run` も引き続き受け付けます。`_local_interpreter` はその古い形を
  内部のモジュールに書き換えるので、そうした仕様はキューに入れたときと同じく 1 プロセスで動きます。
  ラッパーは本体と同じシグネチャを持つので、フラグの集合は 1 つです。ラッパーは、呼び出し側が
  入力したフラグを値にかかわらずすべて引き渡します。既定値と環境変数の値は run 自身に解決させます。
  `--label` はコマンドラインの解析時に検査するので、長すぎるラベルはラッパーが呼び出し側の言葉で
  拒否します。`bajutsu worker --once <spec.json>` は、何もしない `LocalWorkerIO` を使い、リポジトリを
  持たずに、`execute_job_spec` で仕様を 1 つ実行します。`run_job` の新しい `inherit_stdio` モードは、
  呼び出し側の標準ストリームのまま（公開の `bajutsu run` だけは呼び出し側の `PATH` も保ちます）、独自のセッションで子プロセスを起動し、新しい run ツリー
  から run ID を読みます。呼び出し側は待つあいだ生存確認のパイプを開いたままにします。パイプが終端に
  達すると内部の run は自分に SIGTERM を送るので、SIGKILL で終了したラッパーがデバイスを動かし続ける
  run を残すことはありません。`--once` は、バンドル、アーティファクトの上書き、Device Farm のバッチ、
  `local` 以外の worker ファイル（BE-0448 を案内します）、型の誤った `udids` と `materials`、
  カレントディレクトリの別の内容のファイルを上書きする materials を拒否します。SIGINT、SIGTERM、
  SIGHUP は単位 2 のキャンセルになります。2 回目の SIGINT は子プロセスのグループをすぐに終了させます。
  SIGTSTP は子プロセスのグループを停止してからラッパーを停止し、SIGCONT は両方を再開します。
  呼び出し側がすでに無視しているシグナルは無視したままにします。終了コードは子プロセスの終了コードか、
  128 にシグナル番号を足した値です。`--step` のプロンプトは端末の入力を待つあいだ run のキャンセルを
  確認するようになったので、プロンプトでの Ctrl-C でも run はレポートを書いて終わります。
  `bajutsu run` への Ctrl-C と SIGTERM の終了コードは、1 プロセスのときの 1 から 130 と 143 に
  変わりました。Ctrl-C は run のドライバーの子プロセスに直接は届かなくなり、バックグラウンドの
  `--step` の run は端末の入力で停止しなくなりました。サブプロセスでの同等性テストは、成功、失敗、
  使い方の誤り、2 つのシグナル、SIGKILL されたラッパー、2 回目の Ctrl-C、Ctrl-Z、疑似端末での `--step`
  のプロンプト、`--once` での変更前の仕様を確かめます。往復のテストは、`run` のすべてのオプションを
  描画して内部の入口で解析し直します。プロセス内の CLI テストは内部の入口を呼びます。
  worker はコントロールプレーンより先に、または同時に更新する必要があります。この変更より前のビルドの
  worker は、キューに入った `-m bajutsu.run.internal` を自分のインタプリタに置き換えないので、リースした
  `run` がすべて失敗します。
- [#2182](https://github.com/bajutsu-e2e/bajutsu/pull/2182)（単位 5 の 2 分割のうち 1：機能を失わないローカル起動）— ローカル起動は、
  `--backend local` の利用者が使える機能を残すようになりました。これで、2 分割のうち 2 で
  ローカル backend をこの形へ移せます。ただし 1 点は 2 分割のうち 2 まで残ります。ローカル起動は
  すべての種類の job をキューに積み、キューに入った job は、リースが中継するまで保存した secret を
  受け取りません。hosted でないデプロイでは、FastAPI のアプリが `local_only` の
  ルートを配信します。キャプチャモード、ライブなステップ選択、`/api/ant/login` がこれにあたります。
  stdlib のハンドラと同じ動きをするスクリーンショットのハンドラを加え、起動時の読み取りはキャプチャを
  利用可能と報告します。hosted のデプロイは、これらのルートを引き続き拒否します。`SecretStore` に、
  平文を読む唯一の口として `values_for_job` を加えました。`DbSecretStore` はその org の行を復号し、
  今のキーで復号できない行は警告を出して飛ばします。`EnvSecretStore` は、自分が書いた secret の名前を
  ロックを取って返します。プロセス内の job のスレッド（`jobs.run_job_in_process`）が、
  `jobs.job_secret_env` を通じて値を新しい `Job.secret_env` に入れます。ストアのエラーは、理由を
  示す行を残してその job だけを失敗させます。`_spawn_env` はこのフィールドを、プロバイダの上書きより下に重ねます。
  このフィールドは job の仕様にも `repr` にも入りません。job は config を取得しないので、Git の
  認証情報も入れません。`serve.jobs` の外で `values_for_job` や `job_secret_env` を直接呼ぶと
  テストが失敗するので、値に手を伸ばす HTTP のハンドラはこのテストで見つかります。
  2 分割のうち 2 では、同じ読み出しをリースの応答で中継します。default org について、ローカル起動は
  シナリオを割り当てた config のツリーに、ベースラインを `--baselines` のディレクトリに置きます。
  そのため、保存したシナリオは一覧に戻り、承認したベースラインは run が読むものと一致します。
  `FileObjectStore` は複数の割り当てを持てるようになり、ローカル起動は `--baselines` のディレクトリを
  `baselines/` に割り当てます。worker の署名付きダウンロードも、承認したベースラインを読みます。
  runs ディレクトリや state ディレクトリを含む `--baselines` は起動時に拒否します。default org の配置が
  使う名前（`artifacts`、`baselines`、`scenarios`、`uploads`）は、API でも `orgs:` でも org の slug に
  使えません。
  プロバイダの設定も `provider-settings.json` に残します。2 つの backend が 1 つの写しを共有するので、
  データベースの写しがずれることはありません。他の org はバケットと同じ配置のままです。FastAPI、
  uvicorn、SQLAlchemy、Alembic、`cryptography` は基本の依存に移りました。`server` の extra には boto3 が、
  `db` の extra には psycopg が残ります。どちらかの extra を指定したインストールのコマンドも、
  そのまま解決します。CLI は引き続きサーバの依存を 1 つも import しません。`--asgi` のインストール確認は
  なくなり、サーバ backend のインストールの案内はクラウドのクライアントだけを挙げます。ローカル backend の
  executor と既定値は変えていません。

## 参考

- worker capability の項目（slug `worker-capability`）：`worker.yaml`、`--worker-config`、`maxJobConcurrency`、および 1 回実行の worker が実行する worker capability の検査を定義します。
- Device Farm の投入の項目（slug `devicefarm-worker-dispatch`）：Device Farm の batch を worker に移し、server 側の予算の設定を廃止します。
- [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md)：worker の lease モデルと完了後のログ。
- [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)：書き込み専用の secrets。
- [BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads-ja.md)：worker のための署名付き URL による入出力。
- [BE-0204](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support-ja.md)：server のオブジェクトストア。
- [BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md)：分割をまたぐライブのアーティファクト。
- [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md)：capability によるルーティング。
- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md)：本項目が server から取り除く、プロセス内の Device Farm の投入。
- [BE-0431](../BE-0431-job-scoped-artifact-override/BE-0431-job-scoped-artifact-override-ja.md)：ジョブ単位のアーティファクトの上書き。
