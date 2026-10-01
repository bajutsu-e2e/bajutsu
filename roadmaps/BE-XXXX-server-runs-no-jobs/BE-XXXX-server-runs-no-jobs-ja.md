[English](BE-XXXX-server-runs-no-jobs.md) · **日本語**

# BE-XXXX — server はジョブを実行せず、すべてのジョブを worker が実行する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-server-runs-no-jobs-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
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
- **実行の履歴**は、リポジトリがあるとデータベースの窓から取るので、コマンドラインの `bajutsu run` が書いた実行や、`runs/` にすでにある実行が、ローカルの UI から消えます。server は、`runs/` の下の実行のツリー（すでにあるものも、コマンドラインの `bajutsu run` が書いたものも）を、起動時と再走査のときに runs テーブルへ索引します。ファイルのストアは runs のツリーに置くので、それらのアーティファクトも解決できます。target は、リポジトリがあると組織ごとに分かれるので、ローカルの起動は 1 つの既定の組織を使います。
- **アーティファクトの保存先**には、ファイルシステムの実装を加えます。オブジェクトストアが受け付けるのは、現在 `s3://` と `gs://` だけです（[`object_store.py`](../../bajutsu/serve/server/object_store.py)）。`file://` のストアがあれば、worker が実行のツリーを書き、server が読めます。worker はこのストアにも、[BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads-ja.md) の署名付き URL のインターフェースで話しかけ、このストアの URL は server が提供します。各 URL は、server が生成して保存する HMAC の鍵（secrets の鍵と同じ規則で保存します。原子的な `0600` での作成、通常ファイル、安全でない既存のパスの拒否）で署名し、有効期限を持ち、オブジェクトのキーと content type を署名に結び付け、既存のキーの検証でストアのルートに閉じ込めます。経路は、それ以外を受け付けません。

local バックエンドと server バックエンドの違いは、これらの既定値だけになります。`--backend` フラグは意味を失うので、1 リリース非推奨にして、次のリリースで削除します。

### 分割をまたぐキャンセル

ジョブのキャンセルは、現在、server のメモリ上の `Job` にしか届かず、キュー中のジョブでは、その `Job` はプロセスを持ちません。ローカルでは、server は `run` を穏やかに止め（BE-0370）、他の種類はプロセスグループを終了させます。worker では、ハートビートが 409 を返すと、worker は実行が終わるのを待ちます。分割には、専用のキャンセルの経路が要ります。

- キュー中のジョブのキャンセルは、行をキャンセル済みにし、lease はそれを飛ばします。キャンセルはリポジトリからジョブを見つけるので、再起動の前にキューに入ったジョブもキャンセルできます。
- lease 済みのジョブのキャンセルは、行を leased のまま、キャンセル要求のフラグを立てます。フラグは、ハートビートの応答が 200 で返します。worker は、自分の `Job` をキャンセルします。`run` は穏やかに、他の種類はプロセスグループの終了で行います。結果の経路は、lease の持ち主からのキャンセル済みの結果を受け付け、それだけが行をキャンセル済みに移します。
- `worker --once` は、SIGINT と SIGTERM を同じキャンセルに対応づけます。

### 分割をまたぐライブの出力

ホスト型のモデルは、worker のコンソールログを、ジョブの完了後に届けます（[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md)、[`post_completion_logbus.py`](../../bajutsu/serve/server/post_completion_logbus.py)）。ローカルでの利用がそれを迂回していたあいだは、それで足りました。ローカルでの利用も worker を通るようになると、コンソールは再びライブでなければならず、`crawl` は探索グラフが育つあいだそれを必要とします。そこで worker はストリームを送ります。ログの断片とライブのアーティファクトの断片を、ハートビートの経路と同じく lease の持ち主を検査する worker 用の経路に送信し、server は、書き込みを受け付けるログバス（単一の server のプロセスならメモリ上のバス、複製をまたぐなら `RedisLogBus`）に、書き込みを無視する完了後のバスの代わりに公開し、Server-Sent Events（SSE）でブラウザに中継します。アップロードされた `console.log` は、耐久性のある写しとして残ります。ログのストリームは、どの種類を移すよりも先に入れるので、移した `run` もライブのコンソールを保ちます。アーティファクトのストリームは `crawl` とともに入り、最初の利用者は crawl のグラフです。これは [BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md) が未解決にしていた範囲です。

`record` は、人へ制御を渡すこともあります。現在、server は、人の応答を実行の標準入力に書きます（BE-0179）。その入力は worker が持つので、応答は逆向きに、server から worker へ、ハートビートの応答に載せて運び、worker が書きます。デバイスの引き継ぎは、server のホストではなく、worker のホストに従います。

### 1 回実行の worker

`bajutsu worker --once <spec>` は、server なしで 1 つのジョブ仕様を実行し、ジョブの終了コードで終了します。仕様は、キューがすでに保存している形（`worker_job.py` の `job_spec`）です。worker は、`--worker-config` で `worker.yaml` を読み、worker capability の検査を実行し、HTTP と署名付き URL の代わりにローカルの入出力で `execute_job_spec` を通してジョブを実行し、1 回実行のモードでは、リースしたジョブが使う、パイプでつないで統合した取得の代わりに、標準入力、標準出力、標準エラーを継承した子プロセスを起動します。実行の ID は、PASS の行ではなく、子が書いた実行のツリーから取り、起動する内部の run の入口には、解決した `--worker-config` のパスを渡します。アップロード済みのバンドルやアーティファクトの上書きを名指しする仕様は、明確なメッセージで拒否します。それらは署名付き URL で届き、1 回実行の worker にはそれがないからです。同じコマンドが、テスト、調査、次のラッパーに使われます。

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

- [ ] ローカルの既定値
- [ ] 分割をまたぐキャンセル
- [ ] 1 回実行の worker とラッパー
- [ ] ライブのログのストリームとルーティング用の executor
- [ ] `run` の移行
- [ ] `run-set`（Device Farm の batch）の移行
- [ ] `triage` の移行
- [ ] `record` と `crawl` の移行
- [ ] local の executor の削除

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
