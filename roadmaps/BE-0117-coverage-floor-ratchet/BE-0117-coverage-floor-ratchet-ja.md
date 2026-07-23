[English](BE-0117-coverage-floor-ratchet.md) · **日本語**

# BE-0117 — CLI コマンド層の残りをテストしてから、カバレッジフロアをラチェットする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0117](BE-0117-coverage-floor-ratchet-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0117") |
| 実装 PR | [#562](https://github.com/bajutsu-e2e/bajutsu/pull/562) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

姉妹提案（`roadmaps/proposals/` 配下、スラッグ `cli-command-coverage`）は、2026-07-02 の
コードベース分析レポートが名指しした3つの CLI コマンドモジュール、`doctor.py` / `record.py` /
`run.py` を対象としています。しかし `bajutsu/cli/commands/` にはそれ以外にもブランチカバレッジが
17.6% から 71.9% の範囲にとどまるモジュールが7つあり、いくつかはレポートが名指しした3つより
低い水準です。本項目は、その残り7モジュールにユニットテストを追加したうえで、`make check` の
カバレッジフロア（`Makefile` の `--cov-fail-under`）を引き上げ、両方の増分を確定させます。

## 動機

CLI コマンド層は全ユーザーの入口であるにもかかわらず、カバレッジにはばらつきがあります。姉妹提案が
すでに対象としている3モジュールを除いても、実測ブランチカバレッジは次のとおりです。

| モジュール | カバレッジ | 未カバーの行 |
|---|---|---|
| `bajutsu/cli/commands/lint.py` | 17.6% | 14–37（`lint()` 本体のほぼ全体） |
| `bajutsu/cli/commands/worker.py` | 23.3% | 31–42、62–138（`_post_json`、`_object_store`、`_write_console_log`、`worker()` のポーリングループの各分岐） |
| `bajutsu/cli/commands/mcp.py` | 40.0% | 22–31（トランスポート検証、`fastmcp` の import ガード、サーバー起動） |
| `bajutsu/cli/commands/trace.py` | 66.7% | 32–36、51–53（「run が見つからない」分岐と `--explain` のエラー分岐） |
| `bajutsu/cli/commands/crawl.py` | 67.4% | 138、180–195、212–229、262–317、338–356（オプション検証とディスパッチの各分岐） |
| `bajutsu/cli/commands/schema.py` | 71.4% | 10、12（`schema()` 本体全体。テストで一度も呼ばれていません） |
| `bajutsu/cli/commands/audit.py` | 71.9% | 68–69、79–102、134、167–205（使用方法エラーの各分岐と `_history_audit`） |

いずれも Simulator を必要としません。オプション検証、エラーメッセージの分岐、そして小さな純粋な
ヘルパー（`worker.py` の `_post_json` / `_object_store` / `_write_console_log`）であり、既存の
高速な（E2E ではない）テストスイートがすでに他の箇所で対象としているのと同じ種類のロジックです。
`doctor.py` / `record.py` / `run.py` に対して姉妹提案の `cli-command-coverage` が使っているのと
同じパターンでもあります。両提案が着地すると、ブランチカバレッジのフロアは現在測定されている
1.8 ポイント（フロア 87% に対し実測 88.8%）よりもさらに実態から乖離します。フロアを前もって
引き上げておけば、その遊びを未使用の余白として残すのではなく、リグレッションを検知する強制力に
前倒しで変えられます。

## 詳細設計

モジュールごとに作業を分解し、最後にすべてのテスト追加が着地したあとでフロアを引き上げます。

- **`lint.py`**: `lint` コマンドの各分岐に対する `CliRunner` テスト — ファイルが見つからない場合
  （終了コード 1）、ファイルが読み取れない場合（`Path.read_text` が `OSError` を送出するようモック
  し、終了コード 1）、lint エラーのあるシナリオ（終了コード 1、エラーがそのまま出力される）、
  `from:` の provenance に関する advisory（BE-0044 の `provenance_coverage`）が出ないクリーンな
  シナリオ、advisory が出るクリーンなシナリオ、の5パターンです。
- **`worker.py`**: 3つの独立したヘルパーを直接ユニットテストします — `_post_json`（200 応答、
  ボディありなしそれぞれの `HTTPError` 応答）、`_object_store`（`ImportError` の分岐では `None` を
  返し、成功時は構築したストアを返す）、`_write_console_log`（run ディレクトリがなければ何もせず、
  バッファされた行が空でも何もせず、`None` でない行を連結して `console.log` に書き込む）です。
  `worker()` のポーリングループ本体については、`_post_json` / `execute_job_spec` をモックし、
  1回のリース＋実行＋アップロード＋結果送信の往復が終わったところで例外を送出する `side_effect` を
  与えることで、実際の control plane なしに一連の流れを1周分だけ検証します。
- **`mcp.py`**: `CliRunner` テストで、未対応の `--transport`（終了コード 2、有効な選択肢を挙げた
  メッセージ）、`fastmcp` 未インストール時の経路（`bajutsu.mcp` の import が `ImportError` を送出
  するようモックし、終了コード 2）、そして `stdio` と `sse` それぞれの成功経路（`create_server` /
  `server.run` をモックし、ブロックせずに戻ることを確認）を検証します。
- **`trace.py`**: `CliRunner` テストで、`trace` の「run が見つからない」分岐（`run_dir` 未指定、
  および該当する run のない `runs/` ルート）と、`_explain` のエラー分岐（シナリオパスが存在しない
  場合、不正な YAML など `load_expanded_scenarios` の読み込みに失敗する場合）を検証します。
- **`crawl.py`**: 実機の actuator を必要としないオプション検証・ディスパッチの各分岐に対する
  `CliRunner` テストです — 未知の `--agent`（終了コード 2）、利用できないバックエンド/actuator
  （`select_actuator` が例外を送出するようモックし、終了コード 2）、`--dismiss-alerts` やガイドが
  必要とする AI クレデンシャルが欠けている場合（終了コード 2）を、姉妹提案が `run.py` の
  オプション面で使っているのと同じパターンで `fake` バックエンドに対して検証します。
- **`schema.py`**: `bajutsu schema` を呼び出し、出力が JSON としてパースでき
  `scenario_json_schema()` の出力と一致することを確認する `CliRunner` テストを1本追加します。
  コマンド本体は現状、テストで一度も実行されていません。
- **`audit.py`**: 使用方法エラーの各分岐（`--history` と `--repeat` の併用、`--history` と
  位置引数 `scenario` の併用、`scenario` も `--history` も指定しない場合）に対する `CliRunner`
  テストと、`_history_audit` の `provenance.scenarioHash` によるグルーピングおよびフラキー判定
  ロジックに対するユニットテストを、小さな合成 runs ディレクトリに対して追加します。
- **`Makefile` の `--cov-fail-under` を引き上げる。** 上記が（姉妹提案の `cli-command-coverage`
  とあわせて、どちらが後にマージされても）着地したら `make test` を実行し、pytest-cov のサマリから
  実際のブランチカバレッジの割合を読み取ります。その実測値に切り下げた（実行ごとの通常のばらつきで
  ゲートが落ちないようにするため）値を新しいフロアとして設定し、`Makefile:69` の
  `--cov-fail-under=87` の行をそれに応じて更新します。そのあとで改めて `make check` を実行し、
  新しいフロアが正確かつ安定していることを確認します。

## 検討した代替案

- **フロアを先に引き上げ、あとからテストを追加してつじつまを合わせる。** 却下しました。姉妹提案の
  `cli-command-coverage` が却下している理由と同じで、テストが存在する前にフロアを引き上げると、
  無関係な作業をブロックするか、数値を満たすためだけの雑なテストを書かせることになります。
- **`cli-command-coverage` 提案に統合し、独立した項目にしない。** 却下しました。その提案の対象
  範囲（`doctor.py` / `record.py` / `run.py`）は、元のコードベース分析レポートの指摘とちょうど
  一致しています。無関係な7モジュールを加えて対象を広げると、その提案自体の詳細設計（MECE な
  作業分解）が1つのまとまりとしてレビューしづらくなります。別々の、重複しない提案として独立させて
  おけば、どちらも単独で着地でき、フロアの引き上げは両方のあとに実施できます。
- **フロアを 87% のまま据え置き、追加の CLI モジュールには手を付けない。** 却下しました。これは
  まさに元の指摘が問題としている現状そのものである未使用の遊びであり、加えて本項目のスコープ調査で
  分かったとおり `lint.py`（17.6%）と `worker.py`（23.3%）は、元のレポートが名指しした3モジュール
  のどれよりも低い水準です。
- **新しいフロアを実測のカバレッジ割合ちょうどに設定する。** 却下しました。pytest-cov のブランチ
  カバレッジの割合は、条件付きインポートがどちらの分岐を通るかといったプラットフォームの違いなど
  により、内容がまったく同じ実行間でもわずかに変動しえます。切り下げておけば、ゲートがノイズで
  フラップ（不安定に成功・失敗を繰り返すこと）しない程度の余裕を残せます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `lint.py` のコマンド各分岐（見つからない・読み取れない・lint エラーあり・advisory の有無
      それぞれのクリーンなケース）をユニットテストする
- [x] `worker.py` の `_post_json` / `_object_store` / `_write_console_log`、および `worker()`
      ポーリングループの1周分をユニットテストする
- [x] `mcp.py` のトランスポート検証、`fastmcp` の import ガード、`stdio` / `sse` の成功経路を
      ユニットテストする
- [x] `trace.py` の「run が見つからない」分岐と `_explain` のエラー分岐をユニットテストする
- [x] `crawl.py` のオプション検証・ディスパッチの各分岐（未知の agent、利用できないバックエンド、
      AI クレデンシャル欠如）をユニットテストする
- [x] `schema.py` のコマンド本体に対する `CliRunner` テストを追加する
- [x] `audit.py` の使用方法エラーの各分岐と `_history_audit` のグルーピング/判定ロジックを
      ユニットテストする
- [x] `Makefile` の `--cov-fail-under` を新しい実測フロアまで引き上げ、`make check` がクリーンに
      通ることを確認する

- [#562](https://github.com/bajutsu-e2e/bajutsu/pull/562) — CLI テストの追加により、`lint.py` は
  17.6% から 100% へ、`worker.py` は 23.3% から 97% へ、`mcp.py` は 40% から 100% へ、`trace.py`
  は 66.7% から 100% へ、`schema.py` は 71.4% から 100% へ、`audit.py` は 71.9% から 81% へ
  （残る未カバーは実機が必要な `--repeat` 実行経路のみ）上がりました。`crawl.py` は実機を必要と
  しないオプション検証の分岐をカバーしています（コマンドの残りは実機の actuator が必要です）。
  ブランチカバレッジの合計は 89.34% まで上がったため、`Makefile` のフロアを `--cov-fail-under=87`
  から `89` へ引き上げました（`85` までずれていた `docs/ci.md` の記述と、PR テンプレートの例も
  揃えました）。

## 参考

- `bajutsu/cli/commands/lint.py:14-37`（`lint`）、カバレッジ 17.6%
- `bajutsu/cli/commands/worker.py:30-141`（`_post_json`、`worker`、`_object_store`、
  `_write_console_log`）、カバレッジ 23.3%
- `bajutsu/cli/commands/mcp.py:14-31`（`mcp`）、カバレッジ 40.0%
- `bajutsu/cli/commands/trace.py:13-54`（`trace`、`_explain`）、カバレッジ 66.7%
- `bajutsu/cli/commands/crawl.py:58-`（`crawl`）、カバレッジ 67.4%
- `bajutsu/cli/commands/schema.py:8-12`（`schema`）、カバレッジ 71.4%
- `bajutsu/cli/commands/audit.py:38-`（`audit`、`_history_audit`）、カバレッジ 71.9%
- [`Makefile:69`](../../Makefile) — 本項目が 87 から 89 へ引き上げた `--cov-fail-under` の行。
- [`pyproject.toml`](../../pyproject.toml) — フロアの算出基準となるブランチカバレッジモード
  `[tool.coverage.run] branch = true`。
- [BE-0067 — Code-quality gate hardening](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md)
  — ブランチカバレッジと 87% のフロアを導入した項目。本項目はそのフロアをさらにラチェットします。
- `roadmaps/proposals/` 配下のスラッグ `cli-command-coverage` にある姉妹提案 —
  `doctor.py` / `record.py` / `run.py` を対象とします。本項目は残り7つの CLI コマンドモジュールを
  対象とします。
- 2026-07-02 のコードベース分析レポート（技術的負債の棚卸し）に由来し、本項目のスコープ調査中に
  見つかった追加の低カバレッジモジュールも含みます。
