[English](BE-XXXX-serve-postgres-ci-lane.md) · **日本語**

# BE-XXXX — serve データベース層向けの実 Postgres CI レーン

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-postgres-ci-lane-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Web UI のホスティング（クラウド / セルフホスト） |
| 関連 | [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md) |
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

- **CI に Postgres のサービスコンテナを用意する**：CI の `check` ジョブ（`ci.yml`。現在は
  `tests/serve/` を含む全テストを実行する唯一のテストジョブです）に `postgres` サービスを追加
  します。GitHub Actions の標準的なパターンで、ジョブの実行中は実際の（一時的な）Postgres
  インスタンスを利用できるようにします。`serve` に触れない PR も含め、すべての PR がこの
  コンテナの起動を待つことになる点に注意が必要です。
- **既存の migration テストスイートをそちらでも実行する**：`test_db_migrations.py` の
  upgrade/downgrade テストを、SQLite と新しい Postgres サービスの両方に対して実行するように
  パラメータ化（または複製）し、別仕様を書き起こすのではなく同じアサーションを再利用します。あわせて、
  DB に触れる広いテストスイート（`test_db_models.py`、`test_db_repository.py`、`test_oauth.py` の
  永続化テスト）も Postgres に対して実行します。migration 自体が成功していても、方言固有のカラム
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

- [ ] `serve` の CI ジョブに Postgres のサービスコンテナを追加する。
- [ ] migration の upgrade/downgrade テストと、DB に触れる広いテストスイートをそちらに対しても実行する。
- [ ] ゲート対象外のシグナルとして CI に組み込み、安定後に必須化する。

## 参考

- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/serve/server/migrations/`、`tests/serve/test_db_migrations.py`、
  `tests/serve/test_db_models.py`、`tests/serve/test_db_repository.py`、`tests/serve/test_oauth.py`、
  `tests/serve/test_import_guard.py`、`.github/workflows/ci.yml`
