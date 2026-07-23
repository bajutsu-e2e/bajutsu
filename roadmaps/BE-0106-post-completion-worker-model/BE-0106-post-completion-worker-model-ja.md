[English](BE-0106-post-completion-worker-model.md) · **日本語**

# BE-0106 — 完了後連携 worker モデル（Redis 依存の排除）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0106](BE-0106-post-completion-worker-model-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0106") |
| トピック | Web UI のホスティング |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md), [BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md) |
<!-- /BE-METADATA -->

## はじめに

ホスト型 `bajutsu serve` のアーキテクチャ
（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）は、現在
**Redis** に 3 つの役割を担わせています。**ジョブブローカ**（RQ）、**ライブログの pub/sub バス**
（LogBus）、**セッションストア**の 3 つです。この依存は、もはや成立しない 2 つの前提に基づいて設計
されたものです。

1. **crawl がリモート worker で動く。** 動きません。crawl は分散しません。制御プレーンまたは操作者の
   マシン上でローカルに実行されます。したがって crawl のライブグラフが制御プレーンと worker の分割を
   越えることはなく、
   [BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md)
   はこの理由で保留されています。
2. **テスト実行が run 中にリアルタイムで結果をストリーミングする。** その必要はありません。分散テスト
   実行は、worker 上で run が完了した**後**に結果を収集し、オーケストレータへまとめて返します。分割を
   リアルタイムで越えなければならない実行中のアーティファクトやログ行は存在しません。

この前提の見直しにより、Redis の 3 つの役割はいずれも不要になるか、スタックにすでにある単純な代替で
置き換えられます。本提案は、Redis というインフラ依存を排除する**完了後連携 worker モデル**を設計
します。

## 動機

Redis は、セルフホスト型の全デプロイに運用上の複雑さを持ち込みます
（[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）。
実行するコンテナ、監視、バックアップ。制御プレーンと worker 間のネットワーク面。Sentinel
クラスタに昇格させなければ単一障害点になるコンポーネント（BE-0016 の高可用性の項目）です。
run 中のライブストリーミングが不要になった現在、Redis が担っている 3 つの役割はそれぞれ
より単純な手段で代替できます。

| 現在の Redis の役割 | 導入した理由 | 不要になった理由 |
|---|---|---|
| **ジョブブローカ（RQ）** | `BRPOP` で worker プールへジョブを分配する | worker が制御プレーンに HTTP でポーリングする（または制御プレーンが worker へプッシュする）方式にすれば、ブローカプロセスは不要です |
| **LogBus（pub/sub）** | worker の stdout 各行を SSE でブラウザへリアルタイム中継する | 結果は完了後に収集します。ログは結果ペイロードの一部として届くため、実行中に行単位でストリーミングする必要はありません |
| **セッションストア** | 制御プレーンの再起動やレプリカ間で Web セッションを永続化する | PostgreSQL（スタックにすでにある）でセッションを格納できます。2 つ目のステートフルなサービスは不要です |

Redis を排除すると、デプロイのトポロジは 5 コンテナ（`app`、`postgres`、`redis`、`minio`、`caddy`）
から 4 コンテナになります。pub/sub とブローカのネットワーク面がなくなり、高可用性の検討事項（Redis
Sentinel）そのものが運用チェックリストから消えます。

## 詳細設計

ローカルとサーバのホスティングを分岐させる 4 つの差し替え可能なシームは、すでにあります
（`RunExecutor`、`LogBus`、`SessionStore`、そして system of record の `Repository`）。本設計は、
**Redis に手を伸ばしている 3 つのサーバ側シーム実装を**、Postgres と HTTP を土台にしたものへ
**置き換えます**。**ローカル実装はそのまま**にします。ノート PC の `bajutsu serve` が使うのは
`LocalExecutor`、`InMemoryLogBus`、`InMemorySessionStore` であり、本変更がこれらの読み込みを
変えることはありません。すでにデプロイに含まれ、すでに `Repository` シームの土台でもある Postgres が、
サーババックエンドの必要とする唯一のステートフルな依存になります。

### 1. ジョブ分配：HTTP で lease する Postgres の jobs テーブル

現在、`QueueExecutor.dispatch` は `execute_job_spec(job_spec)` を RQ の `Queue` に enqueue し、
`bajutsu worker` は `BRPOP` で lease する RQ の `Worker` を動かします。どちらも置き換えます。

- **`jobs` テーブル**（既存スキーマへの新しい Alembic マイグレーション）：`id`、`org_id`、`spec`
  （JSONB。worker がすでに再構築に使う `job_spec` のペイロード）、`status`
  （`queued` → `leased` → `done`/`failed`）、`leased_at`、`leased_by`、`result`（JSONB、完了時に
  書き込み）、`created_at`。`Repository` シームに `enqueue_job`、`lease_job`、`complete_job` を
  加えます。
- **`DbQueueExecutor`**（新しいサーバ `RunExecutor`）は、Redis に enqueue する代わりに `queued`
  の行を挿入します。
- **制御プレーンの 2 つの worker 向け HTTP エンドポイント**（operator トークン認証、BE-0051）。
  `POST /api/worker/lease` は最古の `queued` ジョブを原子的に lease し（Postgres の
  `SELECT … FOR UPDATE SKIP LOCKED` により 2 つの worker が同じジョブを取ることはありません）、その
  spec を返すか、キューが空なら `204 No Content` を返します。`POST /api/worker/result` は完了した
  結果を受け取り、ジョブを `done` にします。
- **`bajutsu worker` は HTTP のポーリングループになります**。lease → （ジョブがあれば）
  `execute_job_spec` を実行 → run ツリーをアップロード → 結果を post。（なければ）短い間隔だけ待って
  また lease します。この待機は制御プレーン側のインフラのポーリングであって、run の中の固定 `sleep`
  では**ありません**。worker が spawn する決定的な `bajutsu run` は 1 バイトも変わらないので、
  prime directive #2（run/ゲートに `sleep` を置かない）には触れません。これは `RedisLogBus` が
  すでに使っているポーリングと同じ形です。

worker は**プル型のままで、アドレス可能である必要はありません**。`BRPOP` が与えていたのと同じ性質を
保つので、家庭用 NAT や tailnet の背後にいる worker でも動きます。lease が古くなった行（run の途中で
落ちた worker）は、`leased_at` がタイムアウトを過ぎた `leased` の行です。これを再キュー（`queued` に
戻す）するのは、BE-0016 の「worker の生存確認とジョブ再キュー」項目の Postgres での自然な形であり、
Redis の ack-late に委ねず本項目に取り込みます。

### 2. 結果の収集：アーティファクトはオブジェクトストレージへ、メタデータは制御プレーンへ

すでにある分担を保ちます。**大きなアーティファクトはオブジェクトストレージへ直接**、**小さな
メタデータだけが制御プレーンへ**渡ります。run 後、worker は `runs/<id>/` ツリーを今と同じく
オブジェクトストレージへアップロードし（動画が制御プレーンを経由することはありません）、続いて
`/api/worker/result` へ終端メタデータ（`run_id`、終了ステータス、`ok`、manifest の要約）を `POST`
します。**制御プレーン**がその POST から完了 run を system of record に記録します。したがって今と
異なり、**worker はもはや `BAJUTSU_DATABASE_URL` も `db` extra も必要としません**。記録は、すでに
データベースを所有する唯一のプロセスへ移ります。worker の依存は HTTP クライアントとオブジェクト
ストレージだけに縮みます。

### 3. ライブログ：ライブストリームではなく完了後のコンソールログ

サーバモードは行単位のライブストリーミングをやめます（スコープ確定済み）。run 中、ブラウザは
**実行中**の状態を見せ、ジョブが完了すると**全ログ**を見せます。

- worker は run の stdout を `runs/<id>/console.log` に書き、run ツリーの残りと一緒にアップロード
  します。アーティファクトが 1 つ増えるだけで、新しい伝送路はありません。
- **`PostCompletionLogBus`**（新しいサーバ `LogBus`）は、既存の `/events` の契約を保ちます。後から
  購読を開いてもよく、ジョブ完了で終わるストリームです。ただしその供給元を jobs テーブルとオブジェクト
  ストレージにします。ジョブが `queued`/`leased` の間は定期的なハートビートを流し（接続と「実行中」
  状態を保ちます）、ジョブが `done` になったらアップロード済みの `console.log` を流して閉じます。
  ブラウザと `/events` ルートは変わりません。変わるのは*行の出所だけ*なので、SSE クライアントは
  そのまま動きます。
- **ローカルモードは変わりません**。`InMemoryLogBus` は同じ `/events` エンドポイント越しに、今も
  プロセス内でライブに流します。2 つのモードは 1 つのブラウザコード経路を保ちます。

### 4. セッション：Postgres バックのストア

`RedisSessionStore` を **`SqlSessionStore`** に置き換え、既存のエンジンの上に載せます。同じ
マイグレーションで `sessions` テーブル（`id`、`identity`、`expires_at`）を加えます。`issue` は
`expires_at = now + ttl` の行を挿入します。`valid` は行が無いか期限切れなら無効とみなします。
`identity` は束ねられた GitHub ログイン（共有トークンログインなら None）を返します。期限は読み取り時に
強制し、衛生のため期限切れ行を定期的に削除します。セッションは Redis ストアと同じく再起動をまたぎ、
レプリカ間で共有され、2 つ目のステートフルなサービスを要しません。

### 5. 配線と移行パス

`_build_server_state` は `redis`/`rq` の import をやめます。`executor` は `DbQueueExecutor`、
`logbus` は `PostCompletionLogBus`、`sessions` は `SqlSessionStore` になり、いずれも `Repository`
のエンジンの上に載ります。ジョブとセッションが Postgres に置かれるため、
**`--backend=server` に `BAJUTSU_DATABASE_URL` が必須になります**（以前は任意でした）。Postgres は
すでに推奨スタックに含まれていたので、これは実運用がすでに動かしていた形を明文化するものです。
`worker` extra からは RQ と Redis を落とします。`deploy/self-host` は `redis` コンテナを失い
（5 サービス → 4）、セルフホスティングのドキュメントと BE-0015/BE-0016 のアーキテクチャ節を Redis
なしのトポロジへ更新します。

上のすべては、**Linux ゲート上で SQLite とフェイクを使って**検証します。jobs テーブルの lease、
HTTP の lease/result ハンドラ、`PostCompletionLogBus`、`SqlSessionStore` はいずれも、Redis も
Postgres も Mac も要らない機械チェック可能なユニットテストを持ちます。どの経路にも LLM は入らず
（prime directive #1）、決定的な run は変わらず（#2）、ここに app 固有のものはありません（#3）。

### 作業分解（MECE）

1. **セッションストア → Postgres。** `sessions` テーブルとマイグレーション、`SqlSessionStore`、配線。
   分配の変更から独立しているので、最初に入れ、それ単独で Redis の 1 役割を消します。
2. **ジョブ分配＋結果収集＋完了後ログ。** 結合した中核です。`jobs` テーブルとマイグレーション、
   `Repository.enqueue_job`/`lease_job`/`complete_job`、`DbQueueExecutor`、`/api/worker/lease` と
   `/api/worker/result` エンドポイント、`bajutsu worker` の HTTP ループ、worker の `console.log`
   アップロード、`PostCompletionLogBus`。worker と制御プレーンの経路から Redis を完全に外せるように
   するスライスです。
3. **デプロイとドキュメントの片付け。** `deploy/self-host` と `worker` extra から `redis` を落とし、
   サーババックエンドに `BAJUTSU_DATABASE_URL` を必須化し、`docs/self-hosting.md`（＋ `docs/ja/`）を
   更新し、BE-0015/BE-0016 の Redis 節（「見直し中」の注記を外して）を Redis なしのアーキテクチャへ
   書き換えます。

## 検討した代替案

- **Redis を残しつつ LogBus だけ削除する。** pub/sub の役割は消えますが、ジョブブローカとセッション
  に Redis が残ります。変更は小さくなりますが、ブローカとセッションストアも置き換えられる状況で
  Redis 依存を丸ごと排除する機会を逃します。
- **Redis をより軽量なメッセージキューに置き換える（SQLite バックのキュー、Postgres の
  LISTEN/NOTIFY など）。** ブローカの抽象が依然として必要です。HTTP ポーリングやプッシュで同じ目的を
  より少ない可動部品で果たせるなら、ブローカ自体が必要かどうかが問われます。
- **ライブログストリーミングを残しつつ Redis pub/sub ではなく HTTP ロングポーリングで行う。**
  リアルタイムの UX は維持されますが、実行中に worker がログをプッシュし続ける必要があり、完了後連携
  モデルと矛盾します。

## 進捗

> 開発の進行に合わせて常に最新に保ちます。チェックリストは *詳細設計* の MECE な作業分解に対応し、
> ログには変更内容と時期（古い順）を PR へのリンクとともに記録します。

- [x] 1 — セッションストア → Postgres（`sessions` テーブルとマイグレーション、`SqlSessionStore`、配線）。
- [x] 2 — ジョブ分配＋結果収集＋完了後ログ（`jobs` テーブルとマイグレーション、`Repository` の lease
  メソッド群、`DbQueueExecutor`、`/api/worker/lease` と `/api/worker/result`、`bajutsu worker` の
  HTTP ループ、`console.log` アップロード、`PostCompletionLogBus`）。
- [x] 3 — デプロイとドキュメントの片付け（`deploy/self-host` と `worker` extra から `redis` を落とし、
  `docs/self-hosting.md` ＋ `docs/ja/` を更新し、BE-0015/BE-0016 の Redis 節を書き換え）。

ログ：

- 2026-07-02 — 確定した前提（完了後の結果収集、サーバモードでのライブストリーミングなし）から設計を
  具体化。詳細設計と本作業分解のスコープを確定。
- 2026-07-02 — スライス 1+2 出荷：`SqlSessionStore`、`DbQueueExecutor`、`PostCompletionLogBus`、
  worker HTTP エンドポイント、`bajutsu worker` HTTP ループ（#445）。
- 2026-07-02 — スライス 3 出荷：Redis をデプロイ、依存、ドキュメント、ロードマップから削除。
- 2026-07-13 — 訂正：スライス 3 のロードマップ書き換えは BE-0016 のみを対象とし、BE-0015 は
  漏れていました（Deployment plan・Migration・セッション・Alternatives が Redis/RQ の記述のまま
  でした）。BE-0015 の Redis/RQ 節は後日、BE-0015 のステータス変更 PR（#986）で本モデルに整合
  させました。

## 参考

- [BE-0015 — Web UI の公開ホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)
  — 本項目が Redis 依存を見直す公開クラウドのアーキテクチャです。影響するセクション：スタック表
  （Redis 7 / RQ の行）、ジョブキューのセクション、worker の説明、Phase 1/2 のデプロイ、移行の
  LogBus シーム、代替案の Redis 対 RabbitMQ/NATS/SQS。
- [BE-0016 — Web UI のセルフホスティング](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)
  — Docker Compose スタックに `redis` コンテナを含むセルフホストのアーキテクチャです。影響する
  セクション：Tier B のスタック説明、ジョブ分配の段落、残作業の各項目（capability ルーティング、
  worker の生存確認、制御プレーンのスケールアウト、高可用性）、アーキテクチャ図。
- [BE-0070 — 制御プレーンと worker をまたいだ実行中アーティファクトのライブ表示](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md)
  — crawl は分散せず、テスト実行は完了後に結果を連携するため保留されました。本項目は同じ前提の変更を
  ホスティングアーキテクチャに反映するものです。
- ソースの該当箇所：`bajutsu/serve/server/executor.py`（`QueueExecutor`）、
  `bajutsu/serve/server/logbus.py`（`RedisLogBus`）、
  `bajutsu/serve/server/sessions.py`（`RedisSessionStore`）、
  `bajutsu/cli/commands/worker.py`（`bajutsu worker` CLI）、
  `bajutsu/serve/server/worker_job.py`（ジョブ実行とアーティファクトのアップロード）。
