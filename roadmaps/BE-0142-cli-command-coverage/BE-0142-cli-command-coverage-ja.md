[English](BE-0142-cli-command-coverage.md) · **日本語**

# BE-0142 — CLI コマンド層にテストを追加する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0142](BE-0142-cli-command-coverage-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0142") |
| 実装 PR | [#618](https://github.com/bajutsu-e2e/bajutsu/pull/618) |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

CLI コマンド層は、すべてのユーザーが最初に触れる入口です。しかし、その 3 コマンドは、
コードベースの中でもカバレッジが低い部類に入ります。本項目では、`doctor`、`record`、`run` に
高速な（Simulator 不要の）ユニットテストを追加します。ユーザーが実際に触れる層での回帰を、
出荷前に検出できるようになります。

## 動機

コマンド層のカバレッジは、他のコードより低い水準にとどまっています。

- `bajutsu/cli/commands/doctor.py` は 42.7% です。
- `bajutsu/cli/commands/record.py` は 56.4% です。
- `bajutsu/cli/commands/run.py` は 66.1% です。

3 つのモジュールはいずれも、引数解析を超えたロジックを抱えています。

- **`doctor.py`** は、actuator の準備状態の解決と、現在画面の走査を行います。補助関数は
  `_claude_readiness`、`_current_screen`、`check_scenarios` で、位置はそれぞれ
  `bajutsu/cli/commands/doctor.py:135`、`:150`、`:20` です。
- **`record.py`** は、出力パスを組み立てます。`_record_out_path` が
  `bajutsu/cli/commands/record.py:31` にあります。
- **`run.py`** は、レーン、baseline、schema、golden、シナリオファイルを解決し、`--browsers`
  マトリクスを解析します。補助関数は `_resolve_lanes`、`_resolve_baselines_dir`、
  `_resolve_schemas_dir`、`_resolve_goldens_dir`、`_scenario_files`、`_parse_browsers` で、
  位置はそれぞれ `bajutsu/cli/commands/run.py:68`、`:87`、`:98`、`:108`、`:118`、`:49` です。

これらはいずれも Simulator を必要としません。ロジックの中身は、パス解決とオプション解析、
`Effective` 設定に対する分岐です。既存の高速（非 E2E）スイートが他の箇所ですでに対象としている
種類のロジックと変わりません。

この層を手薄なまま残すと、代償が生じます。すべてのユーザーの呼び出しが通る層が、回帰から
最も守られていない層になってしまうのです。

規模は M です。対象は 3 モジュールで、それぞれにすでに切り出された補助関数がいくつもあり、
まずそれらを直接の対象にできます。そのまわりに、薄い `typer` のエントリポイントがあります。

## 詳細設計

この作業は追加のみで、プロダクションの挙動は変更しません。テスト自体が、後続作業の安全網に
なります。主な例は run コマンドの分解で、本項目が着地した後に続けるのが望ましい作業です。
作業はモジュールごとに分解します。

- **`doctor.py`**：fake の actuator と driver に対して、3 つの補助関数をユニットテストします。
  - `check_scenarios`（シナリオディレクトリの検証）
  - `_claude_readiness`（`Effective` の形ごとの準備状態文字列）
  - `_current_screen`（要素ツリーの走査）

  加えて、`doctor` コマンド本体の分岐（設定ファイルなし、シナリオディレクトリなし、actuator
  利用不可）を `CliRunner` 経由でテストします。
- **`record.py`**：`_record_out_path` を、命名と衝突の各分岐にわたってユニットテストします。
  `record` コマンドのオプション処理（ターゲット解決、出力パス選択）は、fake driver に対して
  テストします。これで Simulator が不要になります。
- **`run.py`**：コマンド本体からすでに切り出されている補助関数を、`run` 関数本体とは独立に
  ユニットテストします。対象は `_parse_browsers`、`_resolve_lanes`、`_resolve_baselines_dir`、
  `_resolve_schemas_dir`、`_resolve_goldens_dir`、`_scenario_files`、`_expand_file` です。
  続けて、オプション解析の面（target、scenario、backend、tag、exclude の解決）を、`CliRunner`
  レベルのテストで押さえます。このテストは `fake` バックエンドを使うので、コマンド本体の
  ディスパッチロジックを、実際の actuator なしで検証できます。
- **3 モジュール共通**：各モジュールの新規テストは、スイートの他の箇所ですでに使われている
  `fake` driver と backend のフィクスチャを再利用します。新しいテストインフラは要らず、
  プロジェクトの高速テストの慣例とも一貫します。

## 検討した代替案

- **カバレッジを現状のまま残し、E2E（Simulator）テストでの検出に頼る。** 却下します。
  on-device スイート（`make -C demos/features e2e`）は、高速ゲートに含まれない別枠の重い経路で
  あり、すべての変更で実行されるわけではありません。コマンド層のバグは、Simulator 前提のスイート
  を実行できないコントリビュータ（Linux など）にとって発見が遅れる、あるいは発見されないままに
  なります。
  急ごしらえで価値の低いテストを書かせることになります。フロアの引き上げは後続の別項目（仮:
  coverage-floor-ratchet、TBD）として、実際のカバレッジ向上が積み上がった後に続く順序で進めます。
- **テストを書く前に、コマンド層をより薄く書き直す。** 書き直しでは、ロジックをすべて
  `cli/commands/` の外へ出します。直近の一手としては却下します。テスト網がない状態で先に面を
  作り変えると、挙動を気づかないうちに変えてしまうおそれがあります。まずカバレッジを固めます。
  そうすれば、その後の作り直しが回帰の安全網に頼れます（`run.py` に固有の run コマンドの分解項目を
  参照）。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `doctor.py` の補助関数（`check_scenarios`、`_claude_readiness`、`_current_screen`）と
      `doctor` コマンドの分岐をユニットテストする
- [x] `record.py` の `_record_out_path` と `record` コマンドのオプション処理をユニットテストする
- [x] `run.py` の切り出し済み補助関数（`_parse_browsers`、`_resolve_lanes`、
      `_resolve_baselines_dir`、`_resolve_schemas_dir`、`_resolve_goldens_dir`、
      `_scenario_files`、`_expand_file`）をユニットテストする
- [x] `fake` バックエンドに対して `run` コマンドのオプション解析・ディスパッチ面を
      `CliRunner` レベルでテストする

- 2026-07-03: 3 つのコマンドモジュールに Simulator 不要のテストを追加しました。`run.py` の
  ディレクトリ解決補助関数（`_resolve_baselines_dir` / `_resolve_schemas_dir` /
  `_resolve_goldens_dir`）と `_expand_file` の setup・component・data 各エラー分岐に直接の
  ユニットテストを与え、`doctor.py` の `check_scenarios` / `_claude_readiness` /
  `_current_screen` と `record`・`run` のコマンド本体には `fake` バックエンドで駆動する
  `CliRunner` テストを追加しました（デバイス・AI の境界のみスタブ化します）。`run` のマトリクス
  実行分岐（`run_matrix_and_report`）は対象外です。非 Web バックエンドからは到達できず、パイプ
  ライン層（`tests/runner/`）で検証されています。`make check` で確認しました。PR: [#618](https://github.com/bajutsu-e2e/bajutsu/pull/618)。

## 参考

- `bajutsu/cli/commands/doctor.py:20`、`:135`、`:150`（補助関数）、カバレッジ 42.7%
- `bajutsu/cli/commands/record.py:31`（`_record_out_path`）、カバレッジ 56.4%
- `bajutsu/cli/commands/run.py:49`、`:68`、`:87`、`:98`、`:108`、`:118`（補助関数）、
  カバレッジ 66.1%
- 関連: [BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md)（コード品質ゲートの強化）、[BE-0050](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map-ja.md)（E2E カバレッジマップ）
- 後続のカバレッジフロア引き上げ（仮: coverage-floor-ratchet、TBD）より先行する順序で進める
- 2026-07-02 のコードベース分析レポート（技術的負債の棚卸し）に由来します。
