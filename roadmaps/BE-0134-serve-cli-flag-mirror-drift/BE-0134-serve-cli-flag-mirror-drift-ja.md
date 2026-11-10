[English](BE-0134-serve-cli-flag-mirror-drift.md) · **日本語**

# BE-0134 — serve と CLI のフラグ二重管理による drift をなくす

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0134](BE-0134-serve-cli-flag-mirror-drift-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0134") |
| 実装 PR | [#621](https://github.com/bajutsu-e2e/bajutsu/pull/621) |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/serve/helpers.py` は、`serve` の起動リクエストに対する CLI の argv を手作業で組み立てています。
`run.py` と `record.py` がすでに宣言している `typer` フラグを、もう一度列挙する方式です。この 2 つ目の
手動定義は、CLI 側でフラグが追加・変更されるたびに drift します。本項目では、serve 側のフラグ面を
`typer` という単一のソースオブトゥルースから導出します。

## 動機

`bajutsu/serve/helpers.py:246` の `run_command` は、`python -m bajutsu run ...` の argv を組み立てます。
知っているフラグごとに `cmd += [...]` を積み上げる方式です。対象は `--backend`、`--udid`、
`--workers`、`--erase` / `--no-erase`、`--dismiss-alerts` / `--no-dismiss-alerts`、
`--headed` / `--no-headed`、`--baselines`、`--runs-dir`、`--upload-exec` です。この手作業のリストは
`run` のフラグ面の二重定義であり、すでに drift が起きています。

`bajutsu/cli/commands/run.py` の `run` typer コマンドは、さらに次のフラグを宣言しています。
`--browser`、`--browsers`、`--tag`、`--exclude`、`--schemas`、`--goldens`、`--network`、
`--log-predicate`、`--log-subsystem`、`--alert-instruction`、`--zip`、`--config-offline`、
`--require-pinned-config` です。`run_command` は、このいずれも渡せません。そのため、`serve` から
起動した run は、web UI のリクエストボディに何を積んでもこれらのオプションに到達できません。
`record_command`（`bajutsu/serve/helpers.py:308`）も、`record` のフラグ面について同じパターンを
繰り返しています。

2 つの argv 組み立て関数と、それが再現する `typer.Option` 宣言とを結びつけるものは何もありません。
そのため、`run` や `record` にフラグを追加するたびに、コントリビュータは `helpers.py` の更新も
覚えておく必要があります。CLI 自体はこの更新がなくても動くので、見落としやすい作業です。ギャップが
表面化するのは、誰かが `serve` からそのフラグを操作しようとしたときだけです。

規模は M です。修正が触れるのは 2 つの関数と、`run` / `record` がオプションのメタデータを
introspection 用に公開する部分です。

## 詳細設計

この作業は、`run_command` / `record_command` がすでに出力しているフラグの挙動を変えません。既存の
入力に対しては、同じ argv を生成します。一方で、現在 `serve` から渡せないフラグのギャップは新たに
埋めます。設計は 2 つの部分からなります。

- **フラグ面を単一のソースオブトゥルースにする。** `run_command` と `record_command` は、フラグごとに
  手作業で `cmd += [...]` を書くのをやめます。代わりに、`typer` が `run` / `record` コマンド用に
  すでに保持しているオプションメタデータから argv を組み立てます。候補の仕組みは 2 つあります。
  - `typer` の内部にある `click` オブジェクト経由で、コマンドの `click.Command.params`
    （`click.Parameter` のリスト）を走査する。
  - `run` / `record` が共有の宣言的なオプションバンドルを受け取り、`typer` コマンドと
    `run_command` / `record_command` の両方がそれを消費する。run-command-decomposition 項目の
    オプショングルーピング作業が先に着地していれば、それを土台にできます。

  どちらの仕組みを選ぶ場合も、CLI コマンドにフラグを追加すれば、2 箇所目の編集なしで `serve` からも
  使えるようにします。
  - 同じメタデータにより、`run_command` / `record_command` が出力できるフラグが対応する CLI コマンドに
    まだ存在するかを、黙って見過ごさずに import 時やテスト時に検証できます。新たに追加された
    フラグだけでなく、*削除された* CLI フラグも検出できます。
- **現在欠けているフラグを補う。** ミラーリングの仕組みができたら、`run_command` が現在渡せない
  フラグを配線します。対象は `--browser`、`--browsers`、`--tag`、`--exclude`、`--schemas`、
  `--goldens`、`--network`、`--log-predicate`、`--log-subsystem`、`--alert-instruction`、`--zip`、
  `--config-offline`、`--require-pinned-config` です。各フラグは、対応する `serve` リクエストボディの値を
  受け取ります。これは `run_command` のシグネチャと、それを呼び出すリクエストボディのパース処理への
  追加です。`run_command` が消費するリクエストボディは、`bajutsu/serve/operations.py:659`
  （`_register_and_dispatch`）と `:687`（`start_run`）が組み立てています。

## 検討した代替案

- **2 つの argv 組み立て関数を手動管理のリストのまま残し、CLI から drift したら失敗するテストを追加
  する。** 主な修正としては却下しますが、念のための保険としては残す価値があります。テストは事後的に
  drift を検出するだけで、コントリビュータが 2 箇所目の編集を覚えておく必要はなくなりません。
  フラグ面を単一のソースから導出すれば、更新を忘れるリスクだけでなく、二重管理そのものがなくなります。
- **`serve` が web UI のリクエストボディから受け取ったオプションを、自由形式のキー・バリューのまま
  `run` / `record` に渡し、CLI 自身の検証を経由しない。** 却下します。`typer` のオプション解析
  （選択肢、デフォルト値、`--erase` / `--no-erase` のような相互排他のフラグの組み合わせ）を迂回すると、
  CLI 自体なら拒否する組み合わせを `serve` が送れてしまいます。検証はなくならず、`serve` の
  リクエスト処理側に移って重複します。
- **`serve` の起動リクエストを、`python -m bajutsu run` をシェルアウトせずに、`run` / `record` が
  内部で呼び出しているのと同じ Python 関数に通す。** argv 組み立てのステップ自体をなくせるため、
  より長期的な方向性としては検討に値します。しかし本項目のスコープとしては却下します。`serve` の
  プロセス分離モデル（現在は起動ごとに独立したサブプロセスとして動きます）に、フラグの二重管理を
  直すのに必要な範囲を超えて踏み込むからです。より広い serve-scope-boundary の議論の一部として
  評価するのが適切です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `run_command` の argv を、手動管理のリストではなく `run` の `typer` オプションメタデータ
      から導出する
- [x] `record_command` の argv を、手動管理のリストではなく `record` の `typer` オプション
      メタデータから導出する（同じパターンだった `crawl_command` も一貫性のために揃えました）
- [x] `run_command` / `record_command` が知っているフラグが対応する CLI コマンドに
      まだ存在することを検証するチェック（テストまたは import 時のアサーション）を追加する
- [x] 現在欠けている `run` のフラグ（`--browser`、`--browsers`、`--tag`、`--exclude`、
      `--schemas`、`--goldens`、`--network`、`--log-predicate`、`--log-subsystem`、
      `--alert-instruction`、`--zip`、`--config-offline`、`--require-pinned-config`）を、
      `run_command` とそれに渡す `serve` リクエストボディを通じて補う

- フラグ面を単一のソースオブトゥルースから導出するようにしました。`bajutsu/serve/_cli_flags.py` が
  各コマンドの `typer` / `click` オプションを introspection し、`flag_args` がそのメタデータから
  各フラグを組み立てます。これにより、`bajutsu/serve/helpers.py` の `run_command` /
  `record_command` / `crawl_command` は、フラグの綴りや on/off の形を手作業で列挙しなくなりました。
- `flag_args` は、コマンドに存在しない名前を渡されると例外を送出します。完全性テスト
  （`tests/serve/test_cli_flag_mirror.py`）は、すべての CLI フラグが分類済みであることを要求します。
  フラグの改名・削除や、新しく追加したフラグが serve から到達できないまま残ることは、いずれも
  ゲートで検出されます。
- 上記の `run` のフラグは補いました。`start_run`（`bajutsu/serve/operations/dispatch.py`）は、
  クライアントから受け取っても安全なフラグをリクエストボディから配線します。ホストのディレクトリパス
  である `--schemas` / `--goldens` と、Git config の取得方法を制御する `--config-offline` /
  `--require-pinned-config` は、config 駆動のままにしています。serve のクライアントが任意のホスト
  パスを指定できないようにするためです（BE-0051）。

## 参考

- `bajutsu/serve/helpers.py:246`（`run_command`）、`:308`（`record_command`）
- `bajutsu/cli/commands/run.py:186`（`run_command` がフラグをミラーしている `run` typer
  コマンド）
- `bajutsu/serve/operations.py:659`（`_register_and_dispatch`）、`:687`（`start_run`） —
  `run_command` が消費するリクエストボディを組み立てている呼び出し元
- 関連: BE-0069（実行可能なコントリビュータガードレール）、BE-0043（コンフリクトに強い
  ファイルフロー／自動レジストリ）
- run-command-decomposition 項目のオプショングルーピング作業も参照。本項目はそれを土台に
  できます
- 2026-07-02 のコードベース分析レポート（技術的負債の棚卸し）に由来します。
