[English](BE-0129-serve-scope-boundary.md) · **日本語**

# BE-0129 — serve のスコープを画定し、ホスト固有の関心事を共有 config から締め出す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0129](BE-0129-serve-scope-boundary-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0129") |
| トピック | Web UI のホスティング（クラウド / セルフホスト） |
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

肥大化はコアの内部にも及んでいます。`bajutsu/config.py:339` は `OrgConfig` を定義しており、`Config` 自体も `orgs: dict[str, OrgConfig]` フィールド（`config.py:357`）と、それを解決する4つのヘルパー（`config.py:380-415`）を抱えています。これらはローカルの Simulator に対して `bajutsu run` を実行する個人開発者にとって何の意味も持ちません。存在理由は `serve` のホスト型マルチテナントデプロイ（BE-0015）を支えるためだけです。

`Config` はあらゆるエントリポイントがパースするスキーマです。そのためホスティングの関心事が共有 config の表面に恒久的な負荷として居座ってしまっており、これは `Effective`/`Config` が本来体現すべき「app-agnostic」かつ「決定的なコアを不変に保つ」という前提に真っ向から反します。

深刻度は高です。これはバグではなくアーキテクチャの漸進的な劣化ですが、ホスティング関連の新機能（BE-0015、BE-0016、BE-0051）が積み重なるたびに、境界を事後的に引き直すコストは増していきます。しかも config スキーマは、あらゆるバックエンドとターゲットが依存する部分です。

## 詳細設計

境界は、サイズの上限やパッケージ分割ではなく、一つの具体的な移設と機械的に検査できる規則で引きます。

| # | 作業 | 具体的な内容 |
|---|---|---|
| 1 | 規則を文書化する | `docs/` と `docs/ja/` に境界を名指しする短いアーキテクチャノートを追加する |
| 2 | ゲートの検査で強制する | `tests/test_serve_boundary.py`。抽象構文木ベースの import 検査を `make check` に組み込む |
| 3 | `OrgConfig` を `config.py` から追い出す | 新設の `bajutsu/serve/orgs.py` へ移し、`load_config` を分割する |
| 4 | `serve.js` にガードレールを与える | `node --check` と最小限の ESLint を `make lint` に組み込み、Jest/Vitest は先送りする |

### 1. 規則を文書化する

`bajutsu/config.py`、`bajutsu/drivers/`、`bajutsu/runner/`、`bajutsu/scenario/` はホストに依存しないままとし、組織・ロール・テナンシー・課金といった概念も、`db`（SQLAlchemy、Alembic、psycopg）や `oauth`（Authlib）の extra も一切持ち込みません。ホスティングの関心事を持つのは `bajutsu/serve/` だけです。

このノートは次に述べる検査を指し示すだけにとどめ、規則をレビュアーの記憶に頼らせません。

### 2. サイズの上限ではなく、ゲートの検査で強制する

この境界は現時点ですでに成り立っています。コアの各モジュールを調べたところ、`bajutsu.serve` や `db`・`oauth` extra への import は一件もありませんでした。`bajutsu/serve/server/logbus.py` と `sessions.py` は Redis クライアントを直接 import せず、注入された `RedisLike` プロトコル越しに扱っています。つまり `serve` 自体、Redis を必須の依存として抱えてすらいません。

`tests/test_serve_boundary.py` は、`bajutsu/config.py`、`bajutsu/drivers/`、`bajutsu/runner/`、`bajutsu/scenario/` の抽象構文木を辿り、これらが `bajutsu.serve` または `db`・`oauth` extra のパッケージを import していれば失敗します。これを `make check` に組み込めば、「`serve` が境界内にとどまっている」という性質は判断ではなくゲートが捕まえる退行になります。分離はすでに依存関係のレベルで実現しているので、`bajutsu-serve` のような別配布物に切り出す必要はなく、この検査はその状態を固定するだけです。

### 3. `OrgConfig` と組織用ヘルパーを `bajutsu/serve/orgs.py` へ移す

`OrgConfig`、`DEFAULT_ORG`、`org_for_user`、`org_for_target`、`org_for_identity`、`targets_for_org` を呼んでいる箇所は、`serve/__init__.py`、`authz.py`、`jobs.py`、`operations.py`、`server/worker_job.py` の5箇所で、いずれもすでに `bajutsu/serve/` の内部です。コアからの呼び出しは一件もないため、この移設は探索の要る設計判断ではなく機械的な作業です。

| シンボル | 現在の場所 | 移設後 |
|---|---|---|
| `OrgConfig` | `config.py:339-348` | `bajutsu/serve/orgs.py` |
| `DEFAULT_ORG` | `config.py:376-378` | `bajutsu/serve/orgs.py` |
| `org_for_user` / `org_for_target` / `org_for_identity` / `targets_for_org` | `config.py:380-415` | `bajutsu/serve/orgs.py`。引数を `config: Config` から `orgs: dict[str, OrgConfig]` へ絞り込む |
| `Config.orgs` フィールド | `config.py:357` | 削除 |
| `load_config` | `config.py:649-652` | `parse_config_dict`（検証のみ）と `load_config`（I/O）に分割 |

`_Model` は typo 対策として意図的に `extra="forbid"`（`config.py:27`）を設定しています。`orgs` フィールドを削除すると、トップレベルに `orgs:` を持つ YAML はすべて `Config.model_validate` で弾かれるようになるため、`serve` は生の設定文書をそのまま `Config` に渡せなくなります。

`bajutsu/serve/orgs.py` には `load_serve_config(text: str) -> tuple[Config, dict[str, OrgConfig]]` を新設します。生の YAML を一度パースし、`orgs` キーを取り除いてから残りを `parse_config_dict` に渡し、取り除いた側は `serve` 内で `OrgConfig` として検証します。上記5箇所の呼び出し元は、`org_for_*` と `load_config` の import 元を `bajutsu.config` から `bajutsu.serve.orgs` に切り替えます。

ローカルの `bajutsu run` / `bajutsu record` はそのまま `load_config` を呼び続け、`OrgConfig` を構築することも目にすることもありません。

### 4. `serve.js` には lint と構文検査を与え、本格的なテストフレームワークはまだ導入しない

1,575行のテストなしバニラ JavaScript はそれ自体がスコープ肥大の一症状ですが、まず見合った一歩は軽いガードレールです。`bajutsu/templates/serve.js` を対象にした最小限の ESLint のフラット設定を追加し、`node --check` と `npx eslint` を `make lint` に組み込みます。Node が入っていない環境では、`actionlint` と同じように通知つきでスキップします。

Jest や Vitest のようなコンポーネント・ユニットテストの基盤は、`serve.js` が導入するに値するだけの分岐ロジックを抱えるようになるまで明示的に先送りします。その判断条件をここに記録しておくことで、先送りが見落としではなく決定として残ります。

---

これはスコープを画定し、ゲートを整える作業であり、振る舞いの変更ではありません。`serve` がユーザーに対して行うことは変わらず、そのコードと config と依存関係がどこに存在してよいかだけが変わります。したがって3つのプライムディレクティブすべてに構造的に準拠します。新設する2つのゲート検査は `make check` の側で走るのであり、`run` の内部では動きません。

## 検討した代替案

| 代替案 | 判断 | 理由 |
|---|---|---|
| 何もせず、現状のまま `serve` を成長させ続ける | 却下 | config の漏れは複利的に悪化する。将来のホスティング機能（SSO、課金、監査ログの保持）が `config.py` へ紛れ込む前例になる |
| `serve` を別配布物（例えば `bajutsu-serve`）や別リポジトリへ分割する | いまは却下 | `db`・`oauth` の extra と、注入された `RedisLike` プロトコルが分離をすでに依存関係のレベルで実現している。分割はゲート検査がすでに解決している問題に追加コストをかけるだけになる。`serve` が独立したリリース周期を必要とするようになれば再検討する |
| 境界画定が終わるまで `serve` の機能開発を凍結する | 却下 | `serve` のハードニングとホスティング（BE-0015、BE-0016、BE-0051）はいずれも価値のある進行中のトラックであり、境界画定はそれらと並行して段階的に進めるべき |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `serve`/コア境界の規則を文書化する（ホスティングの関心事は `bajutsu/serve/` に限定）
- [ ] `tests/test_serve_boundary.py` を追加し、抽象構文木ベースの import 検査を `make check` に組み込む
- [ ] `OrgConfig` / `DEFAULT_ORG` / `org_for_*` ヘルパーを `bajutsu/serve/orgs.py` へ移し、`load_config` を `parse_config_dict` と `load_config` に分割する
- [ ] `bajutsu/templates/serve.js` 向けに `node --check` と最小限の ESLint 設定を `make lint` に組み込む

まだ着手した PR はありません。

## 参考

| 場所 | 内容 |
|---|---|
| `bajutsu/config.py:27` | `_Model` の `extra="forbid"`。`orgs` を `Config` から削除すると、まだ `orgs:` を宣言している YAML を破壊的に弾くようになる typo 対策 |
| `bajutsu/config.py:339` | `OrgConfig`、ホスト向けマルチテナンシー config |
| `bajutsu/config.py:357` | `Config.orgs` フィールド |
| `bajutsu/config.py:376-415` | `DEFAULT_ORG`、`org_for_user` / `org_for_target` / `org_for_identity` / `targets_for_org` |
| `bajutsu/config.py:649-652` | `load_config`。`parse_config_dict` と `load_config` に分割するフック |
| `bajutsu/serve/__init__.py`、`authz.py`、`jobs.py`、`operations.py`、`server/worker_job.py` | 組織用ヘルパーの現在の呼び出し元。すべてすでに `bajutsu/serve/` の内部 |
| `bajutsu/serve/server/logbus.py`、`sessions.py` | 既存の `RedisLike` プロトコル注入。`redis` を必須の依存にしていないパターン |
| `pyproject.toml:39-42` | `db`（SQLAlchemy、Alembic、psycopg）と `oauth`（Authlib）の optional extra。すでにこれらの依存関係をコアのインストールから締め出している |

- 関連: BE-0011（ローカル Web UI serve）、BE-0051（ホスティング向け serve ハードニング）、BE-0015（Web UI 公開ホスティング）、BE-0016（Web UI セルフホスティング）
- 2026-07-02 のコードベース分析レポート（設計）に由来します。
