[English](BE-XXXX-cli-command-coverage.md) · **日本語**

# BE-XXXX — CLI コマンド層にテストを追加する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-cli-command-coverage-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | 開発基盤（コントリビュータ体験） |
<!-- /BE-METADATA -->

## はじめに

CLI コマンド層はすべてのユーザーが最初に触れる入口ですが、その 3 コマンドはコードベースの中でも
カバレッジが低い部類に入ります。本項目では `doctor` / `record` / `run` に高速な（Simulator 不要
の）ユニットテストを追加し、ユーザーが実際に触れる層での回帰を出荷前に検出できるようにします。

## 動機

コマンド層のカバレッジは他のコードより低く、`bajutsu/cli/commands/doctor.py` は 42.7%、
`bajutsu/cli/commands/record.py` は 56.4%、`bajutsu/cli/commands/run.py` は 66.1% にとどまって
います。3 モジュールとも引数解析を超えたロジックを抱えています。`doctor.py` は actuator の準備
状態解決と現在画面の走査を行い（`_claude_readiness`、`_current_screen`、`check_scenarios` は
それぞれ `bajutsu/cli/commands/doctor.py:135`、`:150`、`:20`）、`record.py` は出力パスを組み立て
（`_record_out_path` は `bajutsu/cli/commands/record.py:31`）、`run.py` はレーン・baseline・
schema・golden・シナリオファイルの解決と `--browsers` マトリクスの解析を行います
（`_resolve_lanes`、`_resolve_baselines_dir`、`_resolve_schemas_dir`、`_resolve_goldens_dir`、
`_scenario_files`、`_parse_browsers` はそれぞれ `bajutsu/cli/commands/run.py:68`、`:87`、`:98`、
`:108`、`:118`、`:49`）。これらはいずれも Simulator を必要としません。パス解決とオプション解析、
`Effective` 設定に対する分岐であり、既存の高速（非 E2E）スイートが他の箇所ですでに対象としている
種類のロジックと変わりません。この層を手薄なまま残すと、すべてのユーザーの呼び出しが通る層が、
回帰から最も守られていない層になってしまいます。規模は M で、3 モジュールそれぞれにすでに切り出
された補助関数がいくつもあり、まずそれらを直接の対象にできます。

## 詳細設計

この作業は追加のみで、プロダクションの挙動は変更しません。テスト自体が後続作業（とりわけ
run コマンドの分解）の安全網になります。作業はモジュールごとに分解します。

- **`doctor.py`**: `check_scenarios`（シナリオディレクトリの検証）、`_claude_readiness`
  （`Effective` の形ごとの準備状態文字列）、`_current_screen`（要素ツリーの走査）を fake の
  actuator/driver に対してユニットテストし、加えて `doctor` コマンド本体の分岐（設定ファイル
  なし、シナリオディレクトリなし、actuator 利用不可）を `CliRunner` 経由でテストします。
- **`record.py`**: `_record_out_path` を命名・衝突の各分岐にわたってユニットテストし、
  `record` コマンドのオプション処理（ターゲット解決、出力パス選択）を fake driver に対して
  テストすることで Simulator を不要にします。
- **`run.py`**: コマンド本体からすでに切り出されている補助関数 —
  `_parse_browsers`、`_resolve_lanes`、`_resolve_baselines_dir`、`_resolve_schemas_dir`、
  `_resolve_goldens_dir`、`_scenario_files`、`_expand_file` — を `run` 関数本体とは独立に
  ユニットテストし、続けてオプション解析の面（target/scenario/backend/tag/exclude の解決）を
  `fake` バックエンドを使った `CliRunner` レベルのテストで押さえ、コマンド本体のディスパッチ
  ロジックを実際の actuator なしで検証します。
- 各モジュールの新規テストは、スイートの他の箇所ですでに使われている `fake` driver/backend の
  フィクスチャを再利用し、新しいテストインフラを追加せずにプロジェクトの高速テストの慣例と
  一貫させます。

## 検討した代替案

- **カバレッジを現状のまま残し、E2E（Simulator）テストでの検出に頼る。** 却下します。
  on-device スイート（`make -C demos/features e2e`）は高速ゲートに含まれない別枠の重い経路で
  あり、すべての変更で実行されるわけではありません。コマンド層のバグは、Simulator 前提のスイート
  を実行できないコントリビュータ（Linux など）にとって発見が遅れる、あるいは発見されないままに
  なります。
- **先にカバレッジフロアを引き上げ、それを満たすためにテストを後追いで書く。** 却下します。
  テストが存在する前にフロアを引き上げると、無関係な作業をブロックするか、数値を満たすためだけの
  急ごしらえで価値の低いテストを書かせることになります。フロアの引き上げ（別項目の
  coverage-floor-ratchet として管理）は、実際のカバレッジ向上が積み上がった後に続く順序で
  進めます。
- **テストを書く前に、コマンド層をより薄く（ロジックをすべて `cli/commands/` の外へ）書き直す。**
  直近の一手としては却下します。テスト網がない状態で先に面を作り変えると、挙動を気づかないうちに
  変えてしまうおそれがあります。まずカバレッジを固め、その後の作り直し（`run.py` に固有の
  run コマンドの分解項目を参照）が回帰の安全網に頼れるようにします。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `doctor.py` の補助関数（`check_scenarios`、`_claude_readiness`、`_current_screen`）と
      `doctor` コマンドの分岐をユニットテストする
- [ ] `record.py` の `_record_out_path` と `record` コマンドのオプション処理をユニットテストする
- [ ] `run.py` の切り出し済み補助関数（`_parse_browsers`、`_resolve_lanes`、
      `_resolve_baselines_dir`、`_resolve_schemas_dir`、`_resolve_goldens_dir`、
      `_scenario_files`、`_expand_file`）をユニットテストする
- [ ] `fake` バックエンドに対して `run` コマンドのオプション解析・ディスパッチ面を
      `CliRunner` レベルでテストする

まだ着手した PR はありません。

## 参考

- `bajutsu/cli/commands/doctor.py:20`、`:135`、`:150`（補助関数）、カバレッジ 42.7%
- `bajutsu/cli/commands/record.py:31`（`_record_out_path`）、カバレッジ 56.4%
- `bajutsu/cli/commands/run.py:49`、`:68`、`:87`、`:98`、`:108`、`:118`（補助関数）、
  カバレッジ 66.1%
- 関連: BE-0067（コード品質ゲートの強化）、BE-0050（E2E カバレッジマップ）
- coverage-floor-ratchet 項目より先行する順序で進める
- 2026-07-02 のコードベース分析レポート（技術的負債の棚卸し）に由来します。
