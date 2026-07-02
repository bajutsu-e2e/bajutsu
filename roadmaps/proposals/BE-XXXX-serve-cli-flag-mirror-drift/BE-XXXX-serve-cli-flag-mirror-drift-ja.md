[English](BE-XXXX-serve-cli-flag-mirror-drift.md) · **日本語**

# BE-XXXX — serve と CLI のフラグ二重管理による drift をなくす

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-cli-flag-mirror-drift-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | 開発基盤（コントリビュータ体験） |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/serve/helpers.py` は、`serve` の起動リクエストに対する CLI の argv を、`run.py` や
`record.py` がすでに宣言している `typer` フラグを手作業で列挙し直すことで組み立てています。
この 2 つ目の手動定義は、CLI 側のフラグが追加・変更されるたびに drift します。本項目では、
serve 側のフラグ面を `typer` という単一のソースオブトゥルースから導出するようにします。

## 動機

`bajutsu/serve/helpers.py:246` の `run_command` は、`python -m bajutsu run ...` の argv を、
知っているフラグごとに `cmd += [...]` を積み上げて組み立てています。`--backend`、`--udid`、
`--workers`、`--erase` / `--no-erase`、`--dismiss-alerts` / `--no-dismiss-alerts`、
`--headed` / `--no-headed`、`--baselines`、`--runs-dir`、`--upload-exec` です。これは `run` の
フラグ面を手作業で二重定義したものであり、すでに drift が起きています。
`bajutsu/cli/commands/run.py` の `run` typer コマンドは、これに加えて `--browser`、
`--browsers`、`--tag`、`--exclude`、`--schemas`、`--goldens`、`--network`、`--log-predicate`、
`--log-subsystem`、`--alert-instruction`、`--zip`、`--config-offline`、
`--require-pinned-config` を宣言していますが、`run_command` はそのいずれも渡す方法を知りません。
そのため、`serve` から起動した run は、web UI のリクエストボディに何を積んでもこれらの
オプションに到達できません。`record_command`（`bajutsu/serve/helpers.py:308`）も `record` の
フラグ面について同じパターンを繰り返しています。この 2 つの argv 組み立て関数を、それらが
再現しようとしている `typer.Option` 宣言と結びつけるものが何もないため、`run` / `record` に
今後フラグを追加するたびに、コントリビュータは `helpers.py` も合わせて更新することを覚えて
おかなければなりません。CLI 自体はこの更新なしでも問題なく動くため見落としやすく、そのギャップ
が表面化するのは、誰かが `serve` からそのフラグを操作しようとしたときだけです。規模は M です。
修正が触れるのは 2 つの関数と、`run` / `record` がオプションのメタデータを introspection 用に
どう公開するかという部分です。

## 詳細設計

この作業は、`run_command` / `record_command` がすでに出力しているすべてのフラグについては
挙動を変えません（既存の入力に対して同じ argv を生成します）。一方で、現在 `serve` から渡せない
フラグのギャップを新たに埋めます。設計は 2 つの部分からなります。

- **フラグ面を単一のソースオブトゥルースにする。** フラグごとに手作業で `cmd += [...]` を
  書く代わりに、`run_command` と `record_command` は、`run` / `record` コマンド用にすでに
  `typer` が保持しているのと同じオプションメタデータから argv を組み立てます（例えば `typer`
  の内部にある `click` オブジェクト経由でコマンドの `click.Params` を走査する方法、あるいは
  `run` / `record` が共有の宣言的なオプションバンドルを受け取り、`typer` コマンドと
  `run_command` / `record_command` の両方がそれを消費する方法 — run-command-decomposition
  項目のオプショングルーピング作業が先に着地していれば、それを土台にできます）。どちらの
  仕組みを選ぶにしても、CLI コマンドにフラグを追加すれば、2 箇所目の編集なしで `serve` からも
  使えるようにします。
  - これにより、`run_command` / `record_command` が知っているフラグが対応する CLI コマンドに
    まだ存在するかどうかを、（黙って見過ごすのではなく）import 時やテスト時に検証できるように
    もなります。新たに追加されたフラグだけでなく、*削除された* CLI フラグも検出できます。
- **現在欠けているフラグを補う。** ミラーリングの仕組みができたら、上で洗い出した、
  `run_command` が現在渡せないフラグ（`--browser`、`--browsers`、`--tag`、`--exclude`、
  `--schemas`、`--goldens`、`--network`、`--log-predicate`、`--log-subsystem`、
  `--alert-instruction`、`--zip`、`--config-offline`、`--require-pinned-config`）を配線し、
  それぞれに対応する `serve` リクエストボディの値を受け取れるようにします（これは
  `run_command` のシグネチャと、それを呼び出すリクエストボディのパース処理への追加になります。
  `run_command` が消費するリクエストボディを組み立てている `bajutsu/serve/operations.py:659`
  （`_register_and_dispatch`）と `:687`（`start_run`）を参照）。

## 検討した代替案

- **2 つの argv 組み立て関数を手動管理のリストのまま残し、CLI から drift したら失敗するテスト
  を追加する。** 主な修正としては却下しますが、念のための保険としては残す価値があります。
  テストは事後的に drift を検出しますが、そもそもコントリビュータが 2 箇所目の編集を覚えて
  おく必要をなくすわけではありません。フラグ面を単一のソースから導出することは、更新を
  忘れるリスクだけでなく、二重管理そのものを取り除きます。
- **`serve` が web UI のリクエストボディから受け取ったオプションを、自由形式のキー・バリューの
  まま `run` / `record` にそのまま渡し、CLI 自身の検証を経由しない。** 却下します。`typer` の
  オプション解析（選択肢、デフォルト値、`--erase` / `--no-erase` のような相互排他のフラグの
  組み合わせ）を迂回すると、CLI 自体なら拒否する組み合わせを `serve` が送れてしまい、検証を
  なくすのではなく `serve` のリクエスト処理側に検証を移して重複させることになります。
- **`serve` の起動リクエストを、`python -m bajutsu run` をシェルアウトするのではなく、
  `run` / `record` が内部で呼び出しているのと同じ Python 関数を通す。** より長期的な方向性
  としては検討に値します（argv 組み立てのステップ自体をなくせるため）。しかし本項目の
  スコープとしては却下します。これはフラグの二重管理を直すよりも大きな変更で、`serve` の
  プロセス分離モデル（現在は起動ごとに独立したサブプロセスとして動きます）に踏み込むもので
  あり、より広い serve-scope-boundary の議論の一部として評価するのが適切です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `run_command` の argv を、手動管理のリストではなく `run` の `typer` オプションメタデータ
      から導出する
- [ ] `record_command` の argv を、手動管理のリストではなく `record` の `typer` オプション
      メタデータから導出する
- [ ] `run_command` / `record_command` が知っているフラグが対応する CLI コマンドに
      まだ存在することを検証するチェック（テストまたは import 時のアサーション）を追加する
- [ ] 現在欠けている `run` のフラグ（`--browser`、`--browsers`、`--tag`、`--exclude`、
      `--schemas`、`--goldens`、`--network`、`--log-predicate`、`--log-subsystem`、
      `--alert-instruction`、`--zip`、`--config-offline`、`--require-pinned-config`）を、
      `run_command` とそれに渡す `serve` リクエストボディを通じて補う

まだ着手した PR はありません。

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
