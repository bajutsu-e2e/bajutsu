[English](BE-0309-serve-postgres-ci-lane.md) · **日本語**

# BE-0309 — serve データベース層向けの実 Postgres CI レーン

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0309](BE-0309-serve-postgres-ci-lane-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0309") |
| 実装 PR | [#1347](https://github.com/bajutsu-e2e/bajutsu/pull/1347), [#1353](https://github.com/bajutsu-e2e/bajutsu/pull/1353), [#1359](https://github.com/bajutsu-e2e/bajutsu/pull/1359), [#1362](https://github.com/bajutsu-e2e/bajutsu/pull/1362), [#1365](https://github.com/bajutsu-e2e/bajutsu/pull/1365) |
| トピック | 検証とカバレッジ |
| 関連 | [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は、`serve` のデータベース層に対して、既存の SQLite レーンに加えて実 Postgres の CI レーンを追加します。

この層は、検証ギャップの最悪の形をすでに避けています。`tests/serve/test_db_migrations.py` は、`Base.metadata.create_all()` で migration を迂回せず、使い捨ての SQLite ファイルに対して実際の Alembic の `upgrade`/`downgrade` を実行します。

ただし、migration を Postgres に対して実行するテストはありません。Postgres は、ホステッドなマルチテナント運用が対象とする方言です。migration の上に乗る ORM/repository 層にも同じ穴があります。`serve` の DB テストは、これ以外すべて `create_engine("sqlite://")` を使っています。

## 動機

SQLite と Postgres は、migration がもっともバグを隠しやすい箇所で乖離します。JSON 型や配列型のカラム、サーバサイドのデフォルト値、同じ宣言的モデルに対して各方言が異なる方式で生成する制約の命名規約がその箇所です。SQLite に対して問題なく upgrade と downgrade ができる migration が、Postgres に対しては失敗することがあります。

コードベースは、この乖離をすでに認識しています。`test_db_repository.py` の FK 強制テストは、コメント自身が「Postgres-vs-SQLite gap」と呼ぶものを対象としています。migration の1つ（`0010_run_project_fk_set_null.py`、`dialect.name == "postgresql"` で分岐）と ORM モデル（Postgres でのみ選ばれる `JSONB` バリアント）も、方言固有のコードを抱えています。

方言固有のコードは、CI で実際の Postgres インスタンスに対して一度も実行されていません。書いた人もレビューした人も、SQLite だけを相手にしてきました。

この検証ギャップは `serve` の DB 層の内側に閉じています。そのため、ユーザーが自身のホステッド Postgres インスタンスに対して migration を実行するまで、ギャップは表面化しません。方言固有のバグを発見するには、最悪のタイミングです。

## 詳細設計

本節は提案の粒度で書きます。作業は以下の単位に沿って MECE に分かれます。

- **`check` を変更せず、Postgres 専用の新規ジョブを追加する**：`ci.yml` が定義するテストジョブは `check` の1つだけで、`main` はすでに `check` を必須にしています。`check` に `postgres` サービスを追加すると、コンテナの起動コストと方言固有の失敗の両方が初日から全 PR をゲートします。ゲート対象外の段階は確保できません。そこで、BE-0282 の `web-e2e.yml` 内にある `network (playwright)` ジョブと同じ形で、新規ジョブを追加します。既存の必須ジョブは書き換えません。新規ジョブは、GitHub Actions の標準的なパターンである `postgres` サービスコンテナを持ちます。そのジョブの実行中は、実際の（一時的な）Postgres インスタンスを利用できます。
- **既存の migration テストスイートを新規ジョブでも実行する**：`test_db_migrations.py` の upgrade/downgrade テストを、SQLite と新しい Postgres サービスの両方に対して実行するようにパラメータ化（または複製）します。別の仕様は書き起こさず、同じアサーションを再利用します。
- **`tests/serve/` 全体の DB に触れるテストスイートを Postgres に対して実行する**：`create_engine("sqlite://")` を呼ぶファイルは約22あります。`test_db_models.py`、`test_db_repository.py`、`test_oauth.py` の永続化テストが含まれます。migration 自体が成功しても、方言固有のカラムや制約の挙動はこれらのファイルで表面化することがあります。
- **まずゲート対象外とする**：[BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md) の前例に従い、新しいジョブをまず CI のシグナルとして着地させます。初めて追加する Postgres のサービスコンテナは、立ち上げ時の不具合を持ち込むことがあります。イメージ取得の一時的な失敗、接続タイミング、SQLite だけでは踏んだことのない方言固有の挙動がその例です。これらは通常のフレーキーさとは別の問題です。そのため、安定を確認してから必須化します。

## 検討した代替案

- **Alembic は1つの migration ファイルから両方の方言を対象にするので、SQLite のカバレッジを信頼する**：1つの migration ファイルが2つの方言を対象にすること自体が、方言固有の挙動が静かに乖離しうる理由です。Alembic は、同じ Python コードから方言ごとに異なる SQL を発行します。実際の対象方言に対して実行して初めて、Alembic が何を発行しているかを観測できます。
- **CI レーンではなく migration の差分を手動レビューして Postgres 互換性を確認する**：手動レビューは、`postgresql.JSON` の明示的な誤用のような明白なケースを捕まえます。デフォルトのサーバサイド制約名の衝突や、挙動の異なる型変換のような暗黙のケースは捕まえられません。実際のデータベースなら無償で捕まえられる種類のバグを、レビューは見逃します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] サービスコンテナを持つ Postgres 専用の CI ジョブを新規に追加する（`check` の変更ではない）。
- [x] migration の upgrade/downgrade テストと、DB に触れる広いテストスイートをそちらに対しても実行する。
  *（スライス 1 で migration テストを Postgres に対して実行しました。スライス 2 では、リスクの高い
  3 つの DB に触れるスイート（`test_db_models.py`、`test_db_repository.py`、`test_oauth.py` の永続化
  テスト）を、`serve_engine` フィクスチャの裏で追加しました。このフィクスチャは、共有 Postgres が
  必要とするテストごとの分離を後付けします。スライス 3 では残りの 18 ファイルを同じフィクスチャへ
  移しました。これで、インメモリエンジンにスキーマを構築する serve の DB テストはすべて、両方の
  方言で走ります。）*
- [x] ゲート対象外のシグナルとして CI に組み込み、安定後に必須化する。
  *（ゲート対象外のシグナルとして先に着地しました。スライス 4 で、レーンに常時報告する
  `serve db (postgres)` ゲートを与えました。このゲートは、必須の `E2E (web)` アグリゲータと同じ形です。
  path-skip が必須チェックをいつまでも保留のままにすることはなくなり、チェックを必須化できるように
  なりました。その後、管理者がこのチェックを「Require code review」のブランチ ruleset に追加したため、
  `serve db (postgres)` は必須チェックになりました。最後の一歩であるこの作業は、コードの PR の外側で
  行うリポジトリ設定です。）*

### ログ

- スライス 1：`serve-db.yml` が `postgres:16` のサービスコンテナを立ち上げ、`test_db_migrations.py` の upgrade/downgrade のアサーションを実 Postgres に対して再実行します。テストは両方の方言でパラメータ化してあります。高速ゲートでは SQLite、レーンでは `postgres` マーカーの裏で Postgres を使います。両方の方言が同じアサーションを使い、レーンは `-m postgres -n0` で実行します。migration 0010 の `postgresql` FK 分岐と JSONB カラムのバリアントは、実 Postgres での初めてのカバレッジを得ます。BE-0282 の前例に従い、当面はゲート対象外とします。
- スライス 2：共有の `tests/conftest.py` に `serve_engine` フィクスチャを追加しました。このフィクスチャは、要求したテストを両方の方言でパラメータ化します。ゲートでは SQLite、レーンでは `postgres` マーカーのパラメータを使います。また、使い捨てのインメモリ DB で SQLite が自然に得ていたテストごとのスキーマ初期化を、共有 Postgres にも与えます。リスクの高い 3 つのスイート（`test_db_models.py`、`test_db_repository.py`、`test_oauth.py` の永続化テスト）がフィクスチャを要求するようになりました。そのため、レーンは `tests/serve` ディレクトリ全体をマーカーで選択して実行します（`pytest tests/serve -m postgres -n0`）。後続のスイートは、フィクスチャを採用した時点で自動的にレーンへ加わります。実 Postgres に対して実行したところ、いくつかの repository テストが、親となる org 行を作らずに `runs` と `projects` を挿入していました。これらのテストは SQLite の FK 非強制の既定に依存していました。現在は org を先に用意し、Postgres が強制する参照整合性を尊重します。FK 非強制で `project_id` が宙に残る挙動をアサートする 1 件は、SQLite 専用のまま残します。その Postgres 側の `ON DELETE SET NULL` の対応は、姉妹テストがすでにカバーしています。
- スライス 3：DB に触れる残り 18 ファイルが `serve_engine` フィクスチャを採用しました。リスクの高い 3 ファイルに限らず、インメモリエンジンにスキーマを構築する serve の DB テストはすべて、ゲートのインメモリ SQLite とレーンの実 Postgres の両方で走ります。実 Postgres に対して実行すると、スライス 2 と同じ外部キーの乖離が現れました。secret ストアと provider-settings ストアは、`org_id` をキーにして行を挿入します。secret ストアは、`users.id` への外部キーである `updated_by` も書き込みます。SQLite の外部キー非強制の既定では、これらのキーが宙に残っていました。各ストアのテストヘルパは、親となる org 行（と書き手の行）を先に用意するようになり、両方の方言で通ります。`test_db_run_listing.py` のスキーマ無しのエラー経路テスト 1 件は、設計上 SQLite 専用のまま残します。ワークフローの変更は不要でした。レーンはすでにディレクトリをマーカーで選択しているため（`pytest tests/serve -m postgres -n0`）、後付けした各ファイルは自動的にレーンへ加わりました。
- スライス 4：`serve-db.yml` を、必須チェックに必要な「常時報告する」形へ組み替えました。ワークフローは、トリガの `paths:` フィルタをやめて、すべての PR（およびマージキュー）で発火します。重い `postgres` ジョブを走らせるかどうかは、`changes` ジョブ（`dorny/paths-filter`）が判断します。新しい `if: always()` のゲートジョブ `serve db (postgres)` は、作業ジョブの skip を pass に、作業ジョブの失敗を赤に翻訳します。必須の `E2E (web)` アグリゲータも同じパターンを使っており、path-skip が必須チェックを保留のまま残すことはありません。このスライス自体は、チェックを必須にしません。必須化は、ゲートが安定してから管理者が行う ruleset の変更です。スライス 4 は、チェックを必須にしても安全な状態にするだけです。
- 必須化：管理者が `serve db (postgres)` を、「Require code review」のブランチ ruleset の必須ステータスチェックに追加しました。レーンは、`check` や `E2E (…)` アグリゲータと同じようにマージをゲートします。この作業は本項目の最後の一歩で、これで項目は完了します。コードではなくリポジトリ設定であるため、PR の外側で反映されます。このログは、その事実を記録します。

## 参考

- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- [BE-0015 — Web UI の公開ホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)
- `bajutsu/serve/server/migrations/`、`tests/serve/test_db_migrations.py`、
  `tests/serve/test_db_models.py`、`tests/serve/test_db_repository.py`、`tests/serve/test_oauth.py`、
  `tests/serve/test_import_guard.py`、`.github/workflows/ci.yml`、`.github/workflows/web-e2e.yml`
