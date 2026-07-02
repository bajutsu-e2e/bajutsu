[English](BE-XXXX-post-completion-worker-model.md) · **日本語**

# BE-XXXX — 完了後連携 worker モデル（Redis 依存の排除）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-post-completion-worker-model-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トピック | Web UI のホスティング（クラウド / セルフホスト） |
| 関連 | [BE-0015](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md), [BE-0070](../../deferred/BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md) |
<!-- /BE-METADATA -->

## はじめに

ホスト型 `bajutsu serve` のアーキテクチャ
（[BE-0015](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、
[BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）は、現在
**Redis** に 3 つの役割を担わせています。**ジョブブローカ**（RQ）、**ライブログの pub/sub バス**
（LogBus）、**セッションストア**の 3 つです。この依存は、もはや成立しない 2 つの前提に基づいて設計
されたものです。

1. **crawl がリモート worker で動く。** 動きません。crawl は分散しません。制御プレーンまたは操作者の
   マシン上でローカルに実行されます。したがって crawl のライブグラフが制御プレーンと worker の分割を
   越えることはなく、
   [BE-0070](../../deferred/BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md)
   はこの理由で保留されています。
2. **テスト実行が run 中にリアルタイムで結果をストリーミングする。** その必要はありません。分散テスト
   実行は、worker 上で run が完了した**後**に結果を収集し、オーケストレータへまとめて返します。分割を
   リアルタイムで越えなければならない実行中のアーティファクトやログ行は存在しません。

この前提の見直しにより、Redis の 3 つの役割はいずれも不要になるか、スタックにすでにある単純な代替で
置き換えられます。本提案は、Redis というインフラ依存を排除する**完了後連携 worker モデル**を設計
します。

## 動機

Redis は、セルフホスト型の全デプロイに運用上の複雑さを持ち込みます
（[BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）。
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

TBD。設計で扱う必要がある論点は以下のとおりです。

1. **ブローカなしのジョブ分配。** Redis/RQ なしで制御プレーンが worker にジョブを配る方法です。候補
   として、HTTP ベースのポーリング（worker が Postgres をバックにした `/api/jobs/lease` エンドポイント
   からプルする）と、HTTP ベースのプッシュ（制御プレーンが worker の API を直接呼ぶ。ただし現在のプル
   モデルを反転させ、worker がアドレス可能であることを求めます）があります。
2. **結果の収集。** run 完了後に worker が完全な結果（終了コード、manifest の要約、ログ、アーティ
   ファクト参照）を制御プレーンに返す方法です。worker がアーティファクトをオブジェクトストレージ
   （MinIO/R2）にアップロードしてから結果メタデータを制御プレーンへ POST する方式か、すべてを単一の
   ペイロードで返す方式が考えられます。
3. **セッションの移行。** `RedisSessionStore` から Postgres バックのセッションストアへの移行です。
   サーバ側の状態を持たない署名つき Cookie 方式も候補になります。
4. **run 中のブラウザ UX。** ライブログのストリーミングがなくなると、ブラウザは「実行中」状態を見た
   後に最終結果を受け取ります。完了のポーリングか待機かの UX 上のトレードオフ、および軽量な進捗
   シグナル（ハートビートやフェーズ表示など、完全なログストリーミングなし）が複雑さに見合うかどうかを
   検討します。
5. **移行パス。** 既存の `QueueExecutor`、`RedisLogBus`、`RedisSessionStore` の各シームを新しい
   実装へ切り替える方法です。ローカルバックエンド（インメモリ／インプロセス実装を使用し、本変更の
   影響を受けません）を壊さないようにします。

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

- [ ] TBD — 設計のスコープが固まり次第、作業分解（MECE）をここに列挙します。

## 参考

- [BE-0015 — Web UI の公開ホスティング](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)
  — 本項目が Redis 依存を見直す公開クラウドのアーキテクチャです。影響するセクション：スタック表
  （Redis 7 / RQ の行）、ジョブキューのセクション、worker の説明、Phase 1/2 のデプロイ、移行の
  LogBus シーム、代替案の Redis 対 RabbitMQ/NATS/SQS。
- [BE-0016 — Web UI のセルフホスティング](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)
  — Docker Compose スタックに `redis` コンテナを含むセルフホストのアーキテクチャです。影響する
  セクション：Tier B のスタック説明、ジョブ分配の段落、残作業の各項目（capability ルーティング、
  worker の生存確認、制御プレーンのスケールアウト、高可用性）、アーキテクチャ図。
- [BE-0070 — 制御プレーンと worker をまたいだ実行中アーティファクトのライブ表示](../../deferred/BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md)
  — crawl は分散せず、テスト実行は完了後に結果を連携するため保留されました。本項目は同じ前提の変更を
  ホスティングアーキテクチャに反映するものです。
- ソースの該当箇所：`bajutsu/serve/server/executor.py`（`QueueExecutor`）、
  `bajutsu/serve/server/logbus.py`（`RedisLogBus`）、
  `bajutsu/serve/server/sessions.py`（`RedisSessionStore`）、
  `bajutsu/cli/commands/worker.py`（`bajutsu worker` CLI）、
  `bajutsu/serve/server/worker_job.py`（ジョブ実行とアーティファクトのアップロード）。
