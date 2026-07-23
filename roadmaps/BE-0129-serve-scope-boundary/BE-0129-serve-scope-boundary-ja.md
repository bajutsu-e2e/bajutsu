[English](BE-0129-serve-scope-boundary.md) · **日本語**

# BE-0129 — serve のスコープを画定し、ホスト固有の関心事を共有 config から締め出す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0129](BE-0129-serve-scope-boundary-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0129") |
| 実装 PR | [#665](https://github.com/bajutsu-e2e/bajutsu/pull/665) |
| トピック | Web UI のホスティング |
<!-- /BE-METADATA -->

## はじめに

`serve` はローカルプレビュー用のサーバーから、リポジトリ内で最大かつ最も成長の速い
サブシステムへと肥大化しました。組織やロールといったホスト固有の関心事も、決定的な
コアが依存する共有スキーマ `bajutsu/config.py` に漏れ出し始めています。

本提案は `serve` のスコープを明示的に画定し、マルチテナントホスティングの関心事を、ツール全体が共有する config スキーマから締め出します。

## 動機

| 対象 | 規模 | 備考 |
|---|---|---|
| `bajutsu/serve/`（`server/` を含む） | 約30モジュール、6,920行 | SQLAlchemy、Alembic、OAuth、RBAC、オブジェクトストア、Redis ベースのワーカー |
| `bajutsu/templates/serve.js` | 1,575行 | バニラ JavaScript、ビルドもテストもなし |
| `bajutsu/serve/operations.py` | 1,376行 | 別途、`operations.py` を分割する後続提案（ID未確定）で扱う |
| `bajutsu/`（コア全体、Python） | 約30,000行 | `serve` と `serve.js` を合わせるとこの5分の1程度 |

これらはいずれも、ローカル CLI ツールのコアが本来知る必要のないインフラです。

肥大化はコアの内部にも及んでいます。`bajutsu/config.py:352` は `OrgConfig` を定義しており、`Config` 自体も `orgs: dict[str, OrgConfig]` フィールド（`config.py:370`）と、それを解決する4つのヘルパー（`config.py:393-429`）を抱えています。これらはローカルの Simulator に対して `bajutsu run` を実行する個人開発者にとって何の意味も持ちません。存在理由は `serve` のホスト型マルチテナントデプロイ（BE-0015）を支えるためだけです。

`Config` はあらゆるエントリポイントがパースするスキーマです。そのためホスティングの関心事が共有 config の表面に恒久的な負荷として居座ってしまっており、これは `Effective`/`Config` が本来体現すべき「app-agnostic」かつ「決定的なコアを不変に保つ」という前提に真っ向から反します。

決定的な `run` パスも同じスキーマをパースします。見落としやすいのは、ホスト型の構成では `run` が組織情報を含む config を読む点です。運用者は `orgs:` を、`run` がそのまま消費する `bajutsu.config.yaml` に宣言します。`bajutsu/serve/operations/dispatch.py` はこの config の全文を `materials` としてリモートワーカーへ送り（ローカルのサーバーモードでは同じファイルを `--config` で指し）、コア側のローダー（`bajutsu/cli/_shared.py` の `load_config` と `bajutsu/mcp/tools.py`）がそれを読みます。つまり組織モデルは*スキーマ*上の負荷であるだけでなく、`Config` がたまたま `orgs` フィールドを持っているために、決定的な `run` が現時点ですでに許容している値でもあります。このフィールドを削除する変更は、`run` が組織情報を含む config を読めるまま保たなければなりません。さもなければ文書化済みのホスト型デプロイ（`orgs:` は `docs/configuration.md` と `docs/self-hosting.md` に記載があります）を壊します。境界の実装がフィールドの単純削除ではなくローダーに属するのは、まさにこのためです。

深刻度は高です。これはバグではなくアーキテクチャの漸進的な劣化ですが、ホスティング関連の新機能（BE-0015、BE-0016、BE-0051）が積み重なるたびに、境界を事後的に引き直すコストは増していきます。しかも config スキーマは、あらゆるバックエンドとターゲットが依存する部分です。

## 詳細設計

境界は、サイズの上限やパッケージ分割ではなく、一つの具体的な移設と機械的に検査できる規則で引きます。

| # | 作業 | 具体的な内容 |
|---|---|---|
| 1 | 規則を文書化する | `docs/` と `docs/ja/` に境界を名指しする短いアーキテクチャノートを追加する |
| 2 | ゲートで強制する | import-linter の forbidden 契約（BE-0112）で core を `db`/`oauth` extra から遠ざけ、`make lint-imports` で検査する |
| 3 | `OrgConfig` を `config.py` から追い出す | 新設の `bajutsu/serve/orgs.py` へ移し、`load_config` を分割する |
| 4 | `serve.js` にガードレールを与える | `node --check` と最小限の ESLint を `make check` 内の `make lint-js` ステップに組み込み、Jest/Vitest は先送りする |

### 1. 規則を文書化する

`bajutsu/config.py`、`bajutsu/drivers/`、`bajutsu/runner/`、`bajutsu/scenario/` はホストに依存しないままとし、組織・ロール・テナンシー・課金といった概念も、`db`（SQLAlchemy、Alembic、psycopg）や `oauth`（Authlib）の extra も一切持ち込みません。ホスティングの関心事を持つのは `bajutsu/serve/` だけです。

このノートは次に述べる契約を指し示すだけにとどめ、規則をレビュアーの記憶に頼らせません。

### 2. サイズの上限ではなく、ゲートで強制する

この境界は現時点ですでに成り立っています。コアの各モジュールを調べたところ、`bajutsu.serve` や `db`・`oauth` extra への import は一件もありませんでした。`bajutsu/serve/server/logbus.py` と `sessions.py` は Redis クライアントを直接 import せず、注入された `RedisLike` プロトコル越しに扱っています。つまり `serve` 自体、Redis を必須の依存として抱えてすらいません。

BE-0112 が import-linter のレイヤモデル（`pyproject.toml` の `[tool.importlinter]`、`make check` 内の `make lint-imports` が実行）を導入しており、その periphery 契約が*すでに*コアからの `bajutsu.serve` の import を禁止しています。そこで、その半分を重複させる抽象構文木ベースのテストを別に足すのではなく、レイヤグラフでは表現できない部分を新しい forbidden 契約「決定性コアは db/oauth のホスティング extra を持たない」で押さえます。これは `bajutsu.config` / `bajutsu.drivers` / `bajutsu.runner` / `bajutsu.scenario` が外部の `db`・`oauth` パッケージ（`sqlalchemy`、`alembic`、`psycopg`、`cryptography`、`authlib`）を import することを禁止し、`include_external_packages` により import-linter が外部 import も検出します。この 2 つの契約により、「`serve` が境界内にとどまっている」という性質は判断ではなくゲートが捕まえる退行になります。分離はすでに依存関係のレベルで実現しているので、`bajutsu-serve` のような別配布物に切り出す必要はありません。

### 3. `OrgConfig` と組織用ヘルパーを `bajutsu/serve/orgs.py` へ移す

呼び出し元はいずれもすでに `bajutsu/serve/` の内部です。組織用ヘルパー（`org_for_*` / `targets_for_org`）を呼ぶのは `serve/__init__.py`、`authz.py`、`operations/reads.py` の3箇所で、`DEFAULT_ORG` はさらに `jobs.py` と `server/worker_job.py` からも使われています。コアからはいずれも一件も呼ばれていないため、この移設は探索の要る設計判断ではなく機械的な作業です。

| シンボル | 現在の場所 | 移設後 |
|---|---|---|
| `OrgConfig` | `config.py:352-362` | `bajutsu/serve/orgs.py` |
| `DEFAULT_ORG` | `config.py:390` | `bajutsu/serve/orgs.py` |
| `org_for_user` / `org_for_target` / `org_for_identity` / `targets_for_org` | `config.py:393-429` | `bajutsu/serve/orgs.py`。引数を `config: Config` から `orgs: dict[str, OrgConfig]` へ絞り込む |
| `Config.orgs` フィールド | `config.py:370` | 削除 |
| `load_config` | `config.py:663` | `parse_config_dict`（検証のみ）と `load_config`（I/O）に分割。後者はトップレベルの `orgs` を取り除く |

`_Model` は typo 対策として意図的に `extra="forbid"`（`config.py:44`）を設定しており、他のすべてのフィールドではこの設定を維持します。ただし `orgs` は typo とは事情が異なります。決定的な `run` はホスト型の構成で組織情報を含む config を正当に読むため（動機を参照）、`Config.orgs` フィールドを削除すると、`Config.model_validate` はその config を「Extra inputs are not permitted: orgs」で弾き、`run` を壊してしまいます。そこでコア側のローダーは `orgs` を、**意味を解さず単に取り除く serve 所有のキー**として扱います。`parse_config_dict` は検証の前にトップレベルの `orgs` を取り除くので、コアは完全に組織非依存（`OrgConfig` も組織の意味論も持たない）でありながら、組織情報を含む config に対して `run` を動かし続けます。その一方で `extra="forbid"` は本物の typo をすべて捕まえ続けます。コアは `orgs` を解釈しません。ただ弾かないだけです。

`load_config`（`config.py:663`）は、`parse_config_dict(data: dict) -> Config`（`orgs` を取り除いてから検証）と `load_config(text: str) -> Config`（`parse_config_dict` の上に YAML I/O を重ねる）に分割します。既存のコア側と serve 側の `load_config` 呼び出し元は、いずれもシグネチャを変えずに済みます。

`bajutsu/serve/orgs.py` には `load_serve_config(text: str) -> tuple[Config, dict[str, OrgConfig]]` を新設します。生の YAML を一度パースし、`orgs` キーを取り除いてから残りを `parse_config_dict` に渡し、取り除いた側は `serve` 内で `dict[str, OrgConfig]` として検証します。組織モデルを必要とする serve 側の呼び出し元は、`org_for_*` と `targets_for_org` の import 元を `bajutsu.config` から `bajutsu.serve.orgs` に切り替え、組織情報を `Config.orgs` フィールドからではなく `load_serve_config`（`serve/helpers.py` のキャッシュ付きローダー経由）から受け取ります。

ローカルの `bajutsu run` / `bajutsu record` はそのまま `load_config` を呼び続け、`OrgConfig` を構築することも目にすることもありません。ホスト型の `run` が組織情報を含む config を読む場合も、コア側のローダーがキーを弾かずに取り除くため、これまでどおり動きます。

### 4. `serve.js` には lint と構文検査を与え、本格的なテストフレームワークはまだ導入しない

1,575行のテストなしバニラ JavaScript はそれ自体がスコープ肥大の一症状ですが、まず見合った一歩は軽いガードレールです。`bajutsu/templates/serve.js` を対象にした最小限の ESLint のフラット設定（`eslint.config.mjs`）を追加し、`node --check` と eslint を `make check` 内の `make lint-js` ステップに組み込みます。`node --check` は Node があればどこでも（CI ランナーを含めて）走ります。eslint はすでに解決できるときだけ走るので、ゲートが eslint をダウンロードすることはありません。Node が入っていない環境では、`actionlint` と同じように通知つきでスキップするので、`make check` はどこでも走ります。

Jest や Vitest のようなコンポーネント・ユニットテストの基盤は、`serve.js` が導入するに値するだけの分岐ロジックを抱えるようになるまで明示的に先送りします。その判断条件をここに記録しておくことで、先送りが見落としではなく決定として残ります。

---

これはスコープを画定し、ゲートを整える作業であり、振る舞いの変更ではありません。`serve` がユーザーに対して行うことは変わらず、そのコードと config と依存関係がどこに存在してよいかだけが変わります。したがって3つのプライムディレクティブすべてに構造的に準拠します。新設する2つのゲート検査は `make check` の側で走るのであり、`run` の内部では動きません。

## 検討した代替案

| 代替案 | 判断 | 理由 |
|---|---|---|
| 何もせず、現状のまま `serve` を成長させ続ける | 却下 | config の漏れは複利的に悪化する。将来のホスティング機能（SSO、課金、監査ログの保持）が `config.py` へ紛れ込む前例になる |
| コア側のローダーがトップレベルの `orgs:` を**弾く**（文書全体に `extra="forbid"` を効かせる） | 却下 | ホスト型の `run` は組織情報を含む `bajutsu.config.yaml` を正当に読む（`materials` としてワーカーへ送るか `--config` で渡す）ため、`orgs:` を弾くと文書化済みのデプロイを壊す。コア側のローダーでキーを取り除けば `run` は動き続け、組織非依存も保たれ、しかも `extra="forbid"` は他のすべての typo を捕まえ続ける |
| コア側のローダーではなく serve から run への境界で `orgs:` を取り除く | 却下 | コア側では `extra="forbid"` が `orgs:` を弾き続けられるが、`dispatch.py` が config の文面を送る前に書き換える必要が生じ、さらにローカルのサーバーモードの run では実ファイルを `--config` で渡す代わりに取り除いた一時ファイルを materialize しなければならない。しかも運用者が組織情報を含む config に対して手で `bajutsu run` を実行すれば、やはり失敗する。あらゆるエントリポイントが共有する一つのローダーでキーを一度だけ取り除くほうが、単純で統一的である |
| `serve` を別配布物（例えば `bajutsu-serve`）や別リポジトリへ分割する | いまは却下 | `db`・`oauth` の extra と、注入された `RedisLike` プロトコルが分離をすでに依存関係のレベルで実現している。分割はゲート検査がすでに解決している問題に追加コストをかけるだけになる。`serve` が独立したリリース周期を必要とするようになれば再検討する |
| 境界画定が終わるまで `serve` の機能開発を凍結する | 却下 | `serve` のハードニングとホスティング（BE-0015、BE-0016、BE-0051）はいずれも価値のある進行中のトラックであり、境界画定はそれらと並行して段階的に進めるべき |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `serve`/コア境界の規則を文書化する（ホスティングの関心事は `bajutsu/serve/` に限定）
- [x] ゲートで強制する。BE-0112 の import-linter がすでにコアからの `bajutsu.serve` の import を禁止しているため、抽象構文木ベースのテストを別に足す代わりに、新しい forbidden 契約で `config.py` / `drivers/` / `runner/` / `scenario/` を `db`・`oauth` extra から遠ざける（`include_external_packages` で外部 import も検出）。`make check` 内の `make lint-imports` で走る
- [x] `OrgConfig` / `DEFAULT_ORG` / `org_for_*` / `targets_for_org` を `bajutsu/serve/orgs.py` へ移し（シグネチャを `dict[str, OrgConfig]` に絞り込む）、`load_config` を `parse_config_dict` と `load_config` に分割し、`load_serve_config` を追加する
- [x] コア側のローダーがトップレベルの `orgs:` を**取り除く**（組織情報を含む config でも `run` が読めるまま保ち、`extra="forbid"` は他の typo を捕まえ続ける）。serve 側の消費点は組織情報を `Config.orgs` ではなく `load_serve_config` から受け取る
- [x] `bajutsu/templates/serve.js` 向けに `node --check` と最小限の ESLint 設定（`eslint.config.mjs`）を、`make check` 内の `make lint-js` ステップとして組み込む

ログ:

- [#665](https://github.com/bajutsu-e2e/bajutsu/pull/665) — コアと `serve` の境界を引く。組織モデルを `bajutsu/serve/orgs.py` へ移し、コア側のローダーで `orgs:` を取り除き、db/oauth の import-linter 契約を追加し、`architecture.md`（両言語）に規則を記載し、`serve.js` の `lint-js` ガードレールを追加した。

## 参考

| 場所 | 内容 |
|---|---|
| `bajutsu/config.py:44` | `_Model` の `extra="forbid"`。他のすべてのフィールドの typo を捕まえ続ける対策（`orgs` はコア側のローダーが検証前に取り除くので弾かれない） |
| `bajutsu/config.py:352` | `OrgConfig`、ホスト向けマルチテナンシー config |
| `bajutsu/config.py:370` | `Config.orgs` フィールド |
| `bajutsu/config.py:390-429` | `DEFAULT_ORG`、`org_for_user` / `org_for_target` / `org_for_identity` / `targets_for_org` |
| `bajutsu/config.py:663` | `load_config`。`parse_config_dict` と `load_config`（後者はトップレベルの `orgs` を取り除く）に分割するフック |
| `bajutsu/serve/__init__.py`、`authz.py`、`operations/reads.py` | 組織用ヘルパー（`org_for_*` / `targets_for_org`）の呼び出し元。`DEFAULT_ORG` はさらに `jobs.py` と `server/worker_job.py` からも使われる。すべてすでに `bajutsu/serve/` の内部 |
| `bajutsu/serve/helpers.py:94-125` | `_load_config_cached` / `load_config_file`。組織情報の消費点が経由するキャッシュ付きの serve 側ローダー。ここに `load_serve_config` を通し、組織情報をキャッシュ済みの `Config` とともに運ぶ |
| `bajutsu/serve/operations/dispatch.py:117`、`bajutsu/cli/_shared.py:192`、`bajutsu/mcp/tools.py:21` | ホスト型の構成で組織情報を含む config を読む `run` パス。コア側のローダーが `orgs` を弾かずに取り除かねばならない理由 |
| `bajutsu/serve/server/logbus.py`、`sessions.py` | 既存の `RedisLike` プロトコル注入。`redis` を必須の依存にしていないパターン |
| `pyproject.toml:39-42` | `db`（SQLAlchemy、Alembic、psycopg）と `oauth`（Authlib）の optional extra。すでにこれらの依存関係をコアのインストールから締め出している |

- 関連: BE-0011（ローカル Web UI serve）、BE-0051（ホスティング向け serve ハードニング）、BE-0015（Web UI 公開ホスティング）、BE-0016（Web UI セルフホスティング）
- 2026-07-02 のコードベース分析レポート（設計）に由来します。
