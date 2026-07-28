[English](BE-XXXX-git-secrets-commit-guard.md) · **日本語**

# BE-XXXX — git-secrets でシークレットのコミットをブロックする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-git-secrets-commit-guard-ja.md) |
| 提案者 | [@akira-matsuda](https://github.com/akira-matsuda) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | TBD（PR を開いた時点で記入します。BE 作成 PR のため、ID と PR は本セッションでは作成しません） |
| トピック | Contributor workflow |
<!-- /BE-METADATA -->

## はじめに

本項目は、[git-secrets](https://github.com/awslabs/git-secrets) を使った自動ガードを追加し、API キーやクラウドの認証情報、秘密鍵がこのリポジトリにコミットされるのを防ぎます。ガードは 2 層で動きます。追跡対象のローカル git フックがコミット時点で即座にフィードバックを返し、CI のステップが独立に全追跡ファイルを再スキャンすることで、ローカルフックが回避された場合や配線されていない場合にも、PR がマージされる前に検出します。どちらの層も、既存の `core.hooksPath` の配線と同じ仕組みで自己修復します。`make hooks` が毎セッション、すべてを登録し直すため、どの参加者も手動のセットアップ手順を覚える必要がありません。本項目は、スコープ付きコミット subject のレビューチェックリストを実行可能な[commit-msg フック](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md)に変え、`uv.lock` を衝突なくマージするためのレビューチェックリストを実行可能な[マージドライバ](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)に変えてきた *Contributor workflow* の系譜を継ぐものです。文章でしか強制できないガードレールは、セッションが忘れうるガードレールでもあります。本項目は「シークレットをコミットしない」をコマンドに変えます。

## 動機

[`SECURITY.md`](../../SECURITY.md) はすでに、API キーをコミットしたり共有したりせず、gitignore 済みの `.env` に保管するよう伝えています。[`.gitignore`](../../.gitignore) も、その `.env` ファイル自体はツリーから除外しています。ただし、どちらの防御もそれぞれの狭い対象より先には届きません。`.gitignore` は名指ししたファイルだけを守り、`SECURITY.md` の指示は読まれるだけで実行はされないため、追跡対象のシナリオフィクスチャや設定ファイル、コミットメッセージに直接貼り付けられた認証情報を止めるものは何もありません。リポジトリは、他のガードレールではすでにこの種の隙間を許容しないという立場を取っています。[BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md) 自身の論拠は「読まれるだけで実行されない手順は劣化する」であり、だからこそスコープ付きコミット subject の規約をフックに変え、見過ごされないようにしました。シークレットはスタイル規約より重大です。漏洩した `ANTHROPIC_API_KEY` や、[`deploy/self-host/README.md`](../../deploy/self-host/README.md) がセルフホスト利用者に export させている AWS の認証情報は、スタイル上の些細な指摘ではなくセキュリティインシデントであり、スコープ外のコミット subject にすでに与えられているのと少なくとも同等の強制力に値します。

AI セッションは、本項目が塞ごうとするリスクを増幅します。人間の開発者が認証情報を貼り付けてしまうことはまれで、たいてい自分で気付きます。一方、シナリオの作成やデバッグを担うエージェントは、`.env` やターミナル、取得したネットワーク交換から読み取った値を、コミット直前のファイルへそのままコピーしてしまうことがあり、立ち止まって考え直す習慣的な間を持ちません。本プロジェクトは多数の AI セッションを並行して走らせるため（`CLAUDE.md`）、ガードはそのすべてに等しく効かなければなりません。それは、標準で有効なコマンドに支えられたフック（opt-in ではなく、クローンした時点で有効なもの）が提供できることであり、ドキュメントだけの注意書きには提供できません。

## 詳細設計

**ツールの選定。** [git-secrets](https://github.com/awslabs/git-secrets) は、`git secrets <command>` サブコマンドといくつかの git フックを追加する、依存の無い小さな bash スクリプトです。Python の依存を必要としないため `uv.lock` や `pyproject.toml` の依存グラフに触れず、設定はふつうの `git config` に保存します。これは、`uv.lock` のマージドライバや `rerere`（BE-0043）のためにこのリポジトリがすでに操作している場所と同じです。以下の設計判断はどれも、README の要約だけでなく、CI がインストールする固定コミット（後述の C）でのソース自体を読んで確かめた内容に基づきます。前面には出てこない次の 2 点が、設計を左右するからです。

- `git secrets --install` が書き込むフックファイルは、`#!/usr/bin/env bash` に続けて `git secrets --<hook 名>_hook -- "$@"` を置くだけの、正味 2 行です。本項目はこの 2 行の中身を、`--install` の実行結果として得るのではなく、追跡対象の `.githooks/` ファイルへ直接書き込みます（後述の A のとおり、各ファイルではこれをコメントのヘッダーと `command -v git-secrets` によるガードで挟みます）。これは実際の食い違いを避けるためです。`--install <target-directory>` は、その対象ディレクトリがすでに `.git` ディレクトリのように見える場合にのみそこをフックの置き場所として扱い、それ以外では配下に入れ子の `<target-directory>/hooks/` を新設してしまいます。これは、フラットで追跡対象の `.githooks/` というこのリポジトリの構成には合いません。
- `git secrets --add` と `--register-aws` は、すでに登録済みのパターンを重複登録することはありません（どちらも、パターンを追加する前に既存の `git config` の内容を確認します）。したがって、マージドライバや `rerere` を今日すでに毎回張り直しているのと同じように、`make hooks` を実行するたびにこれらを登録し直しても、結果としての設定内容はつねに安全です。ただし `--add` だけは、登録済みのパターンに対する呼び出し自体が no-op であっても終了コードが非 0 になります（`--register-aws` はそうなりません）。これをそのまま `set -e` に晒すと、カスタムパターンを一度でも登録した後の 2 回目以降の `make hooks` がすべて失敗してしまいます。そのため [`scripts/git-secrets-setup.sh`](../../scripts/git-secrets-setup.sh) は、この特定の終了コードを本当の失敗ではなく想定内のものとして扱います。

**A. 既存のフックと同じ方法で配線する。** [`.githooks/`](../../.githooks/) に新しいファイルが 2 つ加わります。

- [`.githooks/pre-commit`](../../.githooks/pre-commit) は、`git secrets --pre_commit_hook` を通じて、ステージ済みの全ファイルを禁止パターンでスキャンし、一致すればコミットを拒否します。
- [`.githooks/prepare-commit-msg`](../../.githooks/prepare-commit-msg) は、`git secrets --prepare_commit_msg_hook` を通じて、merge で取り込む側の履歴に対して同じチェックを行います。すでにシークレットを含むブランチを merge しても、それを黙って再導入しないようにするためです。

既存の [`.githooks/commit-msg`](../../.githooks/commit-msg) フックは、スコープ付き subject のチェックの手前に `git secrets --commit_msg_hook` の呼び出しを加えます。コミットメッセージ本文そのものに貼り付けられたシークレットも捕まえるためです。git は 1 つのフック名につき 1 本のスクリプトしか実行しないため、2 つのチェックはそれぞれ独立したファイルではなく、この 1 つのファイルを共有します。`git-secrets` がまだ `PATH` に無いときは、どのフックも緩やかに縮退します。既存の commit-msg フックが `uv` の不在時にすでに no-op になっているのと同じように、コミットへの影響なくスキップするので、まだインストールしていない参加者がコミットをブロックされることはなく、保護が効いていないだけの状態にとどまります。

**B. パターン登録の自己修復。** `git secrets --register-aws` は組み込みの AWS 認証情報パターンを追加します。このリポジトリはさらに、`--register-aws` の対象外となる4つの形を扱う必要があります。Anthropic の API キーや OAuth トークン（`.env.example` の `ANTHROPIC_API_KEY` と `CLAUDE_CODE_OAUTH_TOKEN`。どちらも `sk-ant-` から始まります）、貼り付けられた PEM 形式の秘密鍵ブロック（同じ PEM 形式である GitHub App や GCS のサービスアカウント鍵もこれでカバーされます）、GitHub のトークン（`ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`/`github_pat_` から始まるもの。このリポジトリは [`bajutsu/github/app.py`](../../bajutsu/github/app.py) から CI の automation bot まで、GitHub 連携が深いためです）、そして値自体には固有の形がないため変数名で検出する `BAJUTSU_SERVE_TOKEN` / `GRAFANA_ADMIN_PASSWORD`（[`deploy/self-host/README.md`](../../deploy/self-host/README.md) がオペレーターに設定させている実際のデプロイ時シークレットで、`--register-aws` 自身の AWS シークレットキーパターンと同じ `KEY=value` の形で検出します）です。`git-secrets` はすべてのパターンをこのクローンのローカル `git config` に保存します。これは `core.hooksPath` と同じく clone/pull では伝播しない設定です。そのためパターン自体は追跡対象のファイル（[`.githooks/git-secrets-patterns.txt`](../../.githooks/git-secrets-patterns.txt)）に置き、[`scripts/git-secrets-setup.sh`](../../scripts/git-secrets-setup.sh) が `make hooks` のたびにローカル `git config` へ登録し直します（前述のとおり、すでに登録済みのパターンを重複登録することはありません）。新規クローンや、`make setup` を飛ばして `make check` だけを実行したセッションも、ゲートの最初の前提条件である `hooks` が次に走った瞬間に自己修復します。`git-secrets` 自体が未インストールのときは、このスクリプトがインストール方法（`brew install git-secrets`。[`Brewfile`](../../Brewfile) にも追加済みです。あるいはソースからのビルド）を表示して正常終了し、`make hooks` を失敗させません。

**C. CI の再スキャンがローカルフックの隙間を塞ぐ。** ローカルフックが守るのは、それが配線されていて、かつ `--no-verify` で作られていないコミットに限られ、どちらも常には成り立ちません。これは、`make check` がまさに pre-push フックと CI の両方で同一に走る理由と同じ論拠です。新設する `make lint-secrets` ターゲットは、追跡対象の全ファイルを再スキャンし（`git secrets --scan`）、`make check` に組み込まれます。`actionlint` に対して `lint-actions` がすでに使っている「通知を出してスキップする」パターンをそのまま踏襲し、`git-secrets` が `PATH` に無いローカルでは通知を出してスキップし、CI では必ず先にインストールします。upstream の git-secrets は `1.3.0` タグ（2019 年）以降 GitHub Release を切っていないため、CI は流動的なブランチではなく、そのタグが指す 40 文字のコミット SHA をチェックアウトします。これは `actionlint` の SHA 固定インストーラスクリプトが持つのと同じ不変性を、別途ダウンロードしたリリース成果物を検証するのではなく、ソースのチェックアウト自体を固定することで得るものです。

**D. 正当な誤検出のための逃げ道。** `git-secrets` は、リポジトリのルートに置かれた追跡対象の [`.gitallowed`](../../.gitallowed) ファイルを、自身の例外機構として読み込みます。パターンには一致するもののシークレットではない文字列（フィクスチャの値、ドキュメント中のプレースホルダー、シェル変数の参照など）があれば、パターン自体を緩めるのではなく、そこで除外できます。実際に `make lint-secrets` をこのリポジトリの既存のツリーに対して走らせたところ、そのような例が 5 件見つかりました。そのため `.gitallowed` は空のファイルではなく、この 5 件だけを絞った、レビュー可能なエントリを持つ状態で追加します。1 件目は [`tests/test_github_app.py`](../../tests/test_github_app.py) にある、意図的に壊れた PEM フィクスチャです（鍵の本体に文字どおり `nope` を置き、パースに失敗する鍵に対するエラー経路をテストするためのもので、秘密鍵ではありません）。2 件目は [`.githooks/git-secrets-patterns.txt`](../../.githooks/git-secrets-patterns.txt) 自身に置かれた秘密鍵パターンのソース行で、そのリテラルな正規表現の文字列自体が、検出対象として書いた文字列を含むため、自分自身に一致してしまいます。残る3件は、新設した `BAJUTSU_SERVE_TOKEN` / `GRAFANA_ADMIN_PASSWORD` のパターン（前述の *B*）が、リテラルなシークレット値ではなく変数名だけを参照している既存のツリー上の箇所に一致したものです。[`deploy/self-host/.env.example`](../../deploy/self-host/.env.example) にある、自己説明的なプレースホルダー `change-me`（`.env.example` 自身の `sk-ant-...` と同じ慣習です）、[`deploy/self-host/docker-compose.yml`](../../deploy/self-host/docker-compose.yml) にあるシェルの変数展開 `${BAJUTSU_SERVE_TOKEN:?…}` / `${GRAFANA_ADMIN_PASSWORD:-…}`、そして [`bajutsu/serve/launchagent.py`](../../bajutsu/serve/launchagent.py) にある `"BAJUTSU_SERVE_TOKEN": token`（すでに検証済みの変数から代入しているだけで、リテラル値ではありません）です。

**E. ドキュメント。** [`docs/ai-development.md`](../../docs/ai-development.md)（および `docs/ja/` の対訳）に、この 2 層のガードを説明する節を追加します。[`SECURITY.md`](../../SECURITY.md)（および日本語版）にも、既存の API キーに関する注意点と並べて記載します。

## 検討した代替案

- **フックのラッパーファイルを手で書く代わりに `git secrets --install` を実行する。** 却下しました。詳細設計で述べたとおり、`--install` の対象ディレクトリの扱いは、本物の `.git` ディレクトリか、新しい `hooks/` サブディレクトリを作ってよい空のテンプレートディレクトリのどちらかを前提としており、このリポジトリのフラットで追跡対象の `.githooks/` 構成のどちらにも当てはまりません。`--install` 自身が書き込むはずの 2 行の中身を、追跡対象のファイルへそのまま書くことで食い違いを丸ごと避けられ、どのフックファイルも既存の `pre-push` や `commit-msg` フックとまったく同じ、ふつうの追跡対象テキストのままになります。
- **カスタムパターンは、参加者が手で実行する場当たり的で未文書化な `git secrets --add` に任せる。** 却下しました。ローカルの `git config` はクローン間や CI で共有されないため、すべてのセッションが正確なパターンを覚えて打ち直す必要が生じます。追跡対象のパターンファイルと `make hooks` での自己修復する登録は、別の `git config` 設定に対して BE-0043 のマージドライバがすでに取っているのと同じ形で、唯一の情報源を保ちます。
- **CI でコミット履歴全体をスキャンする（`git secrets --scan-history`）。** 本項目の範囲としては却下しました。ここでの目的は、依頼の趣旨に沿って今後シークレットがコミットされるのを防ぐことにあります。履歴全体のスキャンはより遅く、そこで見つかった過去の誤検出は、チェックを green にするまでに過去のコミット 1 つずつのトリアージを要します。今回には含めず、別項目の候補として残します。
- **ローカルの pre-commit フックだけに頼り、CI のステップを設けない。** 却下しました。フックは `--no-verify` で回避できますし、`make setup` を飛ばしたクローンでは単に配線されません。したがって、独立した CI の再スキャンだけが実際に PR をゲートします。これは、`make check` 自体がフックだけを信頼せず pre-push フックと CI の両方で走る理由とまったく同じです。

## 進捗

- [x] A。追跡対象の `pre-commit` / `prepare-commit-msg` フック。`commit-msg` に同じスキャンを追加しました。
- [x] B。パターン登録の自己修復（`make hooks` → `scripts/git-secrets-setup.sh` + `.githooks/git-secrets-patterns.txt`）。
- [x] C。`make lint-secrets` を `make check` に組み込みました。CI が固定バージョンの git-secrets をインストールして実行します。
- [x] D。`.gitallowed` による逃げ道を記載しました。
- [x] E。`docs/ai-development.md`（および日本語版）、`SECURITY.md`（および日本語版）、`CLAUDE.md`、`Brewfile` を更新しました。

## 参考

- [git-secrets](https://github.com/awslabs/git-secrets)：本項目が組み込むツールです。
- [BE-0069 — Executable contributor guardrails](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md)：本項目が継ぐ *Contributor workflow* の先例（文章の手順を自己修復するコマンドに変える）と、本項目が拡張する既存の `commit-msg` フックです。
- [BE-0043 — Conflict-resistant file flow](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)：本項目のパターン登録が再利用する、クローンごとのローカル `git config` を自己修復するパターン（`make hooks`）です。
- [`SECURITY.md`](../../SECURITY.md)、[`.env.example`](../../.env.example)：本項目が実行可能なチェックで裏付ける、既存の文章のみのシークレット取り扱い指針です。
- [`docs/ai-development.md`](../../docs/ai-development.md)：並行開発ガイドです。仕組みは「コミット前にシークレットをブロックする」節を参照してください。
