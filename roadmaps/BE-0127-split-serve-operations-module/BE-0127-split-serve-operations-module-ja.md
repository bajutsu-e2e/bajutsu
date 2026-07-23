[English](BE-0127-split-serve-operations-module.md) · **日本語**

# BE-0127 — serve operations の巨大モジュールを分割する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0127](BE-0127-split-serve-operations-module-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0127") |
| 実装 PR | [#619](https://github.com/bajutsu-e2e/bajutsu/pull/619) |
| トピック | Web UI のホスティング |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/serve/operations.py` は、1,376 行・約 669 ステートメントの、`serve` のすべての
エンドポイントに 1 ファイルで応答する巨大モジュールに成長しています。本項目では、web UI 自体が
すでに持っているタブ・リソースの軸に沿ってこれをまとまりのあるモジュール群へ分割し、
あわせてこのモジュールに集中している `Any` 型の使用を締めます。

## 動機

`operations.py` 自身のモジュール docstring は、その役割を的確に述べています。「各 serve
エンドポイントの背後にあるオーケストレーションを stdlib の HTTP ハンドラから切り出し、
ローカルの stdlib サーバーとホスト型の FastAPI コントロールプレーンが **1 つの** 実装を
共有できるようにする」というものです。この、フレームワークに依存しないファサードという設計自体
は健全です（これによってローカル／セルフホストとクラウドホストの `serve` がパリティを保てて
おり、BE-0011 と BE-0051 を参照）。しかし、そのファサード自体が未分化の 1 ファイルに
なってしまっています。上から下まで読むと、設定・プロバイダ・API キーの管理、doctor・preflight
チェック、ライブログの SSE 配信、run・record・crawl のディスパッチ、設定バンドルのアップロード、
capture セッション管理、シナリオ解決、enrichment という、それぞれ独立したリソース領域に応答して
おり、しかもその境界はすでにセクションコメントとして書かれています（例えば
`bajutsu/serve/operations.py:464` の `# --- Doctor / preflight (BE-0024) ---`、`:1042` の
`# --- Capture (BE-0012) ---`）。これだけの規模の 1 ファイルはナビゲーションが遅く、
モジュール中の `Any` の使用（52 箇所）がほぼすべてここに集中しているため、どのリソースへの
変更であっても同じ場所に触れることになり、`serve` のどの機能に取り組むコントリビュータも同じ
ファイルを編集することになるため、並行セッション間のマージコンフリクトの面が増えます
（`docs/ai-development.md` の並行作業ガイダンスを参照）。規模は L です。モジュールは大きい
ものの、リソースの境界はすでにコード自身のコメントに見えているため、この分割はファサードの
公開契約を再設計するのではなく、すでにまとまっている関数群を再配置する作業になります。

## 詳細設計

この作業は挙動を変えずに行います。`operations.py` から現在エクスポートされているすべての関数は
シグネチャと挙動を保つため、そこを呼び出す 2 つの HTTP シェル（ローカルの stdlib サーバーと
ホスト型の FastAPI コントロールプレーン）はいずれも import パス以外の変更を必要としません。
これは、より広い設計レベルの兄弟項目である serve-scope-boundary の具体的な対応物です。
serve-scope-boundary が「そもそも `serve` に何が属するべきか」を扱うのに対し、本項目は
「すでにそこに属しているものをどう整理するか」を扱います。分割は、ファイルのセクションコメント
にすでに暗黙的に現れているリソースの軸に従います。

- **設定・プロバイダ・API キーモジュール** — `config_info`、`bind_config`、`bind_git_config`、
  `set_api_key`、`set_provider`、`api_key_info`、`provider_info`、`_valid_key_env_name`、
  `_active_key_env`、`_confined_config_path`
  （`bajutsu/serve/operations.py:131`〜`270`、`:523`〜`:647`）。
- **doctor・preflight モジュール** — `# --- Doctor / preflight (BE-0024) ---` セクション配下の
  `doctor_check` とその補助関数（`bajutsu/serve/operations.py:464`〜`520`）。
- **run・record・crawl ディスパッチモジュール** — `start_run`、`start_record`、`start_crawl`、
  `_register_and_dispatch`、`_boot_targets`、`_device_args`、`_bool_flag`
  （`bajutsu/serve/operations.py:153`〜`178`、`:659`〜`985`）。
- **ライブログ SSE モジュール** — `format_sse`、`job_log_events`、`_job_event_pairs`、
  `_terminal_payload`、`job_sse`、`_job_sse_frames`（`bajutsu/serve/operations.py:408`〜`463`）。
- **設定バンドルアップロードモジュール** — `# --- Upload a bundle as the active config
  (BE-0073) ---` セクション配下の `_safe_filename`、`bind_upload_config`
  （`bajutsu/serve/operations.py:785`〜`855`）。
- **capture セッションモジュール** — `# --- Capture (BE-0012) ---` セクション配下の
  `start_capture`、`mark_capture`、`finish_capture`、`_default_driver_factory`
  （`bajutsu/serve/operations.py:1042`〜`1205`）。
- **シナリオ・run アーティファクト読み取りモジュール** — `list_scenarios`、`read_scenario`、
  `_step_artifacts`、`_step_action_fields`、`_valid_step_id`、`_find_sid`、`job_view`、
  `run_file`、`runs_payload`、`save_scenario`、`approve_baseline`、`resolve_scenario_pick`、
  `browse_fs`、`simulators_payload`、`list_targets_payload`、`_primary_backend`、`cancel_job`
  （`bajutsu/serve/operations.py:96`〜`131`、`:182`〜`308`、`:364`〜`408`、`:985`〜`1042`、
  `:1205`〜`1288`）。
- **enrichment モジュール** — 独立したセクションを持つ `start_enrich`
  （`bajutsu/serve/operations.py:1288`〜`1376`）。

新しいモジュールはそれぞれ、同じ `(payload, status)` の戻り値の約束を保ち、`ServeState` と
すでにパース済みの入力を受け取る、モジュール docstring に記述された既存の契約に従います。
薄い `operations/__init__.py`（またはそれに相当するもの）が公開面全体を再エクスポートし、
呼び出し側が 1 回のアトミックなリネームではなく段階的に移行できるようにします。`Any` の締め込みは
モジュールを切り出すたびにそのモジュールの範囲で行い、各分割を、まだ `Any` のままのパラメータ
（多くは JSON リクエストボディ）を `TypedDict` やより狭い型に置き換える自然な機会とします。
ファサード全体を一度に締め込む必要はありません。

## 検討した代替案

- **`operations.py` を 1 ファイルのまま残し、セクションコメントでのナビゲーションに頼る。**
  却下します。コメント自体はすでに存在しており、それでもファイルが 1,376 行まで成長したという
  事実がその限界を示しています。コメントはマージコンフリクトの面を減らすことも、特定の
  リソース領域を独立してレビュー可能にすることもありません。
- **リソースではなく HTTP メソッド（GET/POST）で分割する。** 却下します。メソッドベースの
  分割では、単一のリソースの読み取りと書き込み（`read_scenario` と `save_scenario` など）が
  別々のファイルに散らばってしまい、1 つの機能に取り組むコントリビュータにとっては上記の
  リソースベースの分割よりまとまりが悪くなります。
- **ファサードをモジュールレベルの関数ではなく（リソースごとの）クラス群として書き直す。**
  却下します。既存の、関数ベースで `ServeState` を明示的な引数に取るスタイルはテストしやすく、
  コードベースの他の部分の慣例とも一致しています。ここでクラスを導入することは、純粋な
  再編成であるべき作業に、無関係な 2 つ目の設計変更を混ぜ込むことになります。
- **分割と `Any` の締め込みを別々の作業として行う。** 検討しましたが、各分割の中で段階的に
  締め込む方針を採り、却下しました。分割が終わった後に専用の型付けパスを待つと、再配置した
  すべての関数をもう一度読み直すことになります。一方、まとまりのあるグループを切り出す
  そのときに型を締めるのは、同じ変更への自然で低コストな追加です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 設定・プロバイダ・API キーモジュールを切り出す（`operations/config.py`）
- [x] doctor・preflight モジュールを切り出す（`operations/doctor.py`）
- [x] run・record・crawl ディスパッチモジュールを切り出す（`operations/dispatch.py`）
- [x] ライブログ SSE モジュールを切り出す（`operations/sse.py`）
- [x] 設定バンドルアップロードモジュールを切り出す（`operations/upload.py`）
- [x] capture セッションモジュールを切り出す（`operations/capture.py`）
- [x] シナリオ・run アーティファクト読み取りモジュールを切り出す（`operations/reads.py`）
- [x] enrichment モジュールを切り出す（`operations/enrich.py`）
- [x] worker HTTP API モジュールを切り出す（`operations/worker.py`）。提案の執筆後に BE-0106 が
      ファイルを増やしたぶんで、上記 8 つと同じリソースの軸に沿って切り出しました
- [x] 既存の呼び出し側（両方の HTTP シェル）が 1 回のアトミックなリネームなしで移行できるよう
      再エクスポート用のシムを追加する。パッケージの `operations/__init__.py` が公開面全体を
      再エクスポートするため、`ops.<name>` 形式の呼び出しはすべて変更なしで動きます。これは
      削除せず、フレームワークに依存しない恒久的なファサードとして残します。ローカル・セルフホスト
      とクラウドホストの `serve` に 1 つの実装を共有させている継ぎ目そのものであり、削除すると
      ファサードが submodule への直接 import に散らばってしまうためです

- 2026-07-03: `operations.py`（1,438 行）を `operations/` パッケージへ分割しました。9 つの
  リソース submodule に加えて、横断する 3 つの private helper（`_device_args`、
  `_resolve_org_or_forbid`、`_default_driver_factory`）を集めた `_common.py` と、再エクスポート
  ファサードの `__init__.py` を置いています。挙動は変えていません。各関数のシグネチャと本体は
  そのままなので、2 つの HTTP シェルもすべてのテストも従来どおり `ops.<name>` で公開面に届きます。
  reads モジュールを切り出す際に `_primary_backend` の `config: Any` を `config: Config` へ
  締めました。ファサードの契約を固定する `tests/serve/test_operations_package.py` を追加しています。
  （[#619](https://github.com/bajutsu-e2e/bajutsu/pull/619)）

## 参考

- `bajutsu/serve/operations.py:1`〜`1376`（1,376 行、約 669 ステートメント、`Any` の使用 52 箇所）
- `bajutsu/serve/operations.py:464`（`# --- Doctor / preflight (BE-0024) ---`）、`:1042`
  （`# --- Capture (BE-0012) ---`）、`:785`（アップロードバンドルのセクション）、`:1288`
  （enrichment のセクション） — 分割が従うセクションコメント
- 関連: BE-0011（ローカル web UI の serve）、BE-0051（ホスティングに向けた serve の堅牢化）
- この具体的なモジュール分割のより広い設計レベルの対応物として、serve-scope-boundary 項目も
  参照
- 2026-07-02 のコードベース分析レポート（技術的負債の棚卸し）に由来します。
