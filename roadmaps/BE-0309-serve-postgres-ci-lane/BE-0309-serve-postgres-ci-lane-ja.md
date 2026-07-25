[English](BE-0309-serve-postgres-ci-lane.md) · **日本語**

# BE-0309 — serve データベース層向けの実 Postgres CI レーン

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0309](BE-0309-serve-postgres-ci-lane-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0309") |
| 実装 PR | [#1347](https://github.com/bajutsu-e2e/bajutsu/pull/1347), [#1353](https://github.com/bajutsu-e2e/bajutsu/pull/1353) |
| トピック | 検証とカバレッジ |
| 関連 | [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`serve` の DB 層は、Postgres 方言に対する検証ギャップのうち最悪の形はすでに避けています。
`tests/serve/test_db_migrations.py` は、`Base.metadata.create_all()` で migration を迂回するのでは
なく、実際の使い捨て SQLite ファイルに対して実際の Alembic の `upgrade`/`downgrade` を実行します。
ただし、その migration も、その上に乗る ORM/repository 層も、ホステッドなマルチテナント運用が
実際に対象とする Postgres の
方言に対しては一度も実行されません。`serve` の DB テストは、これ以外すべて `create_engine
("sqlite://")` を使っています。本項目は、既存の SQLite レーンに加えて、実際の Postgres CI レーン
を追加します。

## 動機

SQLite と Postgres は、migration がもっともバグを隠しやすい箇所でまさに乖離します。JSON 型や配列型の
カラム、サーバサイドのデフォルト値、そして同じ宣言的モデルに対して各方言が異なる方式で生成する
制約命名規約です。SQLite に対しては問題なく upgrade / downgrade できる migration が、Postgres に
対しては失敗することがありえます。コードベースはこの乖離をすでに認識しています。
`test_db_repository.py` の FK 強制テストは、そのコメント自身が「Postgres-vs-SQLite gap」と呼ぶものを
名指しで対象としており、migration の1つ（`0010_run_project_fk_set_null.py`、`dialect.name ==
"postgresql"` で分岐）と ORM モデル（Postgres でのみ選ばれる `JSONB` バリアント）は、すでに方言固有の
コードを抱えています。この方言固有のコードは、CI で実際の Postgres インスタンスに対して一度も実行
されたことがなく、SQLite だけを相手に書かれ、レビューされてきました。この検証ギャップは `serve` の
DB 層の内側に完全に閉じているため、実際のユーザーが自身のホステッド Postgres インスタンスに対して
migration を実行するまで表面化しません。それは方言固有のバグを発見するには最悪のタイミングです。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **`check` を変更するのではなく、Postgres 専用の新規ジョブを追加する**：`ci.yml` が定義する
  テストジョブは `check` の1つだけで、すでに `main` の必須チェックの一部です。ここへ直接
  `postgres` サービスを追加すると、コンテナの起動コストと方言固有の失敗の両方が初日から全 PR を
  ゲートしてしまい、ゲート対象外の段階を実際には確保できません。代わりに、BE-0282 の
  `web-e2e.yml` 内の `network (playwright)` ジョブと同じく、既存の必須ジョブを書き換えるのでは
  なく、`postgres` サービスコンテナを持つ新規ジョブを追加します。GitHub Actions の標準的な
  パターンで、そのジョブの実行中だけ実際の（一時的な）Postgres インスタンスを利用できるように
  します。
- **既存の migration テストスイートをそちらでも実行する**：`test_db_migrations.py` の
  upgrade/downgrade テストを、SQLite と新しい Postgres サービスの両方に対して実行するように
  パラメータ化（または複製）し、別仕様を書き起こすのではなく同じアサーションを再利用します。あわせて、
  `tests/serve/` 全体にわたる DB に触れるテストスイート（`test_db_models.py`、
  `test_db_repository.py`、`test_oauth.py` の永続化テストをはじめ、
  `create_engine("sqlite://")` を呼ぶ約22ファイル）も Postgres に対して実行します。
  migration 自体が成功していても、方言固有のカラム
  や制約の挙動はそこで表面化しうるためです。
- **まずゲート対象外とする**：
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例に従い、新しいジョブをまず CI のシグナルとして着地させます。初めて追加する Postgres の
  サービスコンテナは、通常のフレーキーさとは別に、それ自身の立ち上げ時の不具合（イメージ取得の
  一時的な失敗、接続タイミング、SQLite だけでは踏んだことのない方言固有の挙動など）を持ち込み
  うるため、安定を確認してから必須化します。

## 検討した代替案

- **Alembic は1つの migration ファイルから両方の方言を対象にするので、SQLite のカバレッジを
  信頼する**：1つの migration ファイルが2つの方言を対象にすること自体が、方言固有の挙動が静かに
  乖離しうる理由です。Alembic は同じ Python コードから方言ごとに異なる SQL を発行し、実際の対象
  方言に対して実行して初めて、それが実際に何を発行しているかを観測できます。
- **CI レーンではなく migration の差分を手動レビューして Postgres 互換性を確認する**：手動レビュー
  は明白なケース（`postgresql.JSON` の明示的な誤用など）は捕まえられますが、暗黙のケース（デフォルトの
  サーバサイド制約名の衝突、挙動の異なる型変換）は捕まえられません。これは実際のデータベースなら
  無償で捕まえられるのに、レビューでは捕まえられない種類のバグです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] サービスコンテナを持つ Postgres 専用の CI ジョブを新規に追加する（`check` の変更ではない）。
- [ ] migration の upgrade/downgrade テストと、DB に触れる広いテストスイートをそちらに対しても実行する。
  *（スライス 1 で migration テストを Postgres に対して実行しました。スライス 2 では、リスクの高い
  3 つの DB に触れるスイート（`test_db_models.py`・`test_db_repository.py`・`test_oauth.py` の永続化
  テスト）を、共有 Postgres が必要とするテストごとの分離を後付けする `serve_engine` フィクスチャの裏で
  追加しました。`create_engine("sqlite://")` を呼ぶ残りのファイルは後続のスライスです。いずれもテスト
  ごとに新しいインメモリ DB を前提としているため、フィクスチャを採用した順にレーンへ加わります。）*
- [ ] ゲート対象外のシグナルとして CI に組み込み、安定後に必須化する。
  *（ゲート対象外のシグナルとして着地しました。必須チェックへの昇格は残っています。）*

### ログ

- スライス 1 — `serve-db.yml` が `postgres:16` のサービスコンテナを立ち上げ、
  `test_db_migrations.py` の upgrade/downgrade のアサーションを実 Postgres に対して再実行します。
  テストは両方の方言でパラメータ化してあり（高速ゲートでは SQLite、レーンでは `postgres` マーカーの
  裏で Postgres）、同じアサーションを再利用します。レーンは `-m postgres -n0` で実行します。これに
  より、migration 0010 の `postgresql` FK 分岐と JSONB カラムのバリアントに、実 Postgres での初めての
  カバレッジが得られます。BE-0282 の前例に従い、当面はゲート対象外とします。
- スライス 2 — 共有の `tests/conftest.py` に `serve_engine` フィクスチャを追加しました。これは
  テストを両方の方言でパラメータ化し（ゲートでは SQLite、レーンでは `postgres` マーカーのパラメータ）、
  使い捨てのインメモリ DB で SQLite が自然に得ていたテストごとのスキーマ初期化を、共有 Postgres にも
  与えます。リスクの高い 3 つのスイート（`test_db_models.py`・`test_db_repository.py`・`test_oauth.py`
  の永続化テスト）がこれを要求するようになり、レーンは `tests/serve` ディレクトリ全体をマーカーで選択
  して実行します（`pytest tests/serve -m postgres -n0`）。後続のスイートはフィクスチャを採用した時点で
  自動的にレーンへ加わります。実 Postgres に対して実行したところ、いくつかの repository テストが親と
  なる org 行を作らずに `runs`・`projects` を挿入しており、SQLite の FK 非強制の既定に依存していた
  ことが判明しました。これらは org を先に用意し、Postgres が強制する参照整合性を尊重するようにしました。
  FK 非強制で `project_id` が宙に残る挙動をアサートする 1 件は SQLite 専用のまま残し、その Postgres 側の
  `ON DELETE SET NULL` の対応は姉妹テストがすでにカバーしています。

## 参考

- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- [BE-0015 — Web UI の公開ホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)
- `bajutsu/serve/server/migrations/`、`tests/serve/test_db_migrations.py`、
  `tests/serve/test_db_models.py`、`tests/serve/test_db_repository.py`、`tests/serve/test_oauth.py`、
  `tests/serve/test_import_guard.py`、`.github/workflows/ci.yml`、`.github/workflows/web-e2e.yml`
