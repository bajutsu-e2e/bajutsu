[English](BE-0328-gitleaks-commit-guard.md) · **日本語**

# BE-0328 — gitleaks でシークレットのコミットをブロックする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0328](BE-0328-gitleaks-commit-guard-ja.md) |
| 提案者 | [@akira-matsuda](https://github.com/akira-matsuda) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0328") |
| 実装 PR | TBD（PR を開いた時点で記入します。BE 作成 PR のため、ID と PR は本セッションでは作成しません） |
| トピック | Contributor workflow |
<!-- /BE-METADATA -->

## はじめに

本項目は、[gitleaks](https://github.com/gitleaks/gitleaks) を使った自動ガードを追加し、API キーやクラウドの認証情報、秘密鍵がこのリポジトリにコミットされるのを防ぎます。ガードは 2 層で動きます。追跡対象のローカル git フックがコミット時点で即座にフィードバックを返し、CI のステップが独立に全追跡ファイルを再スキャンすることで、ローカルフックが回避された場合や配線されていない場合にも、PR がマージされる前に検出します。本項目は、スコープ付きコミット subject のレビューチェックリストを実行可能な[commit-msg フック](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md)に変え、`uv.lock` を衝突なくマージするためのレビューチェックリストを実行可能な[マージドライバ](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)に変えてきた *Contributor workflow* の系譜を継ぐものです。文章でしか強制できないガードレールは、セッションが忘れうるガードレールでもあります。本項目は「シークレットをコミットしない」をコマンドに変えます。

本提案は当初 [git-secrets](https://github.com/awslabs/git-secrets) を選んでいました。PR レビュー（[@hirosassa](https://github.com/hirosassa)）で、git-secrets は 7 年間リリースが無い一方、[gitleaks](https://github.com/gitleaks/gitleaks) は現役でメンテナンスされていると指摘を受け、マージ前に本項目を gitleaks へ作り直しました。以下の「詳細設計」は実装した gitleaks の設計をそのまま説明し、「検討した代替案」には、この同じ項目の中で一度選んでから覆した git-secrets を却下した理由を記録します。

## 動機

[`SECURITY.md`](../../SECURITY.md) はすでに、API キーをコミットしたり共有したりせず、gitignore 済みの `.env` に保管するよう伝えています。[`.gitignore`](../../.gitignore) も、その `.env` ファイル自体はツリーから除外しています。ただし、どちらの防御もそれぞれの狭い対象より先には届きません。`.gitignore` は名指ししたファイルだけを守り、`SECURITY.md` の指示は読まれるだけで実行はされないため、追跡対象のシナリオフィクスチャや設定ファイル、コミットメッセージに直接貼り付けられた認証情報を止めるものは何もありません。リポジトリは、他のガードレールではすでにこの種の隙間を許容しないという立場を取っています。[BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md) 自身の論拠は「読まれるだけで実行されない手順は劣化する」であり、だからこそスコープ付きコミット subject の規約をフックに変え、見過ごされないようにしました。シークレットはスタイル規約より重大です。漏洩した `ANTHROPIC_API_KEY` や、[`deploy/self-host/README.md`](../../deploy/self-host/README.md) がセルフホスト利用者に export させている AWS の認証情報は、スタイル上の些細な指摘ではなくセキュリティインシデントであり、スコープ外のコミット subject にすでに与えられているのと少なくとも同等の強制力に値します。

AI セッションは、本項目が塞ごうとするリスクを増幅します。人間の開発者が認証情報を貼り付けてしまうことはまれで、たいてい自分で気付きます。一方、シナリオの作成やデバッグを担うエージェントは、`.env` やターミナル、取得したネットワーク交換から読み取った値を、コミット直前のファイルへそのままコピーしてしまうことがあり、立ち止まって考え直す習慣的な間を持ちません。本プロジェクトは多数の AI セッションを並行して走らせるため（`CLAUDE.md`）、ガードはそのすべてに等しく効かなければなりません。それは、標準で有効なコマンドに支えられたフック（opt-in ではなく、クローンした時点で有効なもの）が提供できることであり、ドキュメントだけの注意書きには提供できません。

## 詳細設計

**ツールの選定。** [gitleaks](https://github.com/gitleaks/gitleaks) は単一の Go バイナリで、活発にメンテナンスされている組み込みルールセット（AWS の認証情報、ファイングレインド PAT を含む GitHub のトークン、PEM 秘密鍵、その他数百件）に加え、プロジェクト固有の内容を追跡対象の TOML 設定で追加できます。以下の設計は 2 つの性質に支えられています。

- 設定ファイル `.gitleaks.toml` は、gitleaks が直接読み込むふつうの追跡対象ファイルです。設定をローカルの `git config`（clone/pull では伝播しない、クローンごとの設定）に保存するツールとは違い、クローンごとに自己修復すべきものが何もありません。`make hooks` に gitleaks 固有の手順は一切不要です。
- gitleaks は(バイナリに組み込まれた)Go 自身の正規表現エンジンでマッチングを行い、システムの `grep` は使いません。そのため、どの参加者のマシンでも OS を問わずパターンの挙動が同一になります。これは、あるクラスのバグを構造的に排除します。本項目の以前のリビジョンは、まだ git-secrets(`grep -E` に処理を委譲します)を使っていた際、GNU 専用の正規表現構文を 2 か所出荷していました。角括弧の外の `\s` と `\b` です。POSIX 準拠のみの `grep -E`(BSD grep。macOS の既定です)はどちらもリテラル文字として扱うため、すべての参加者の Mac でローカルフックが黙って弱くなってしまうことを PR レビューが見つけました。gitleaks にはこの失敗モードがそもそもあり得ません。

**A. 追跡対象のフック。** [`.githooks/`](../../.githooks/) に新しいファイルが 2 つ加わります。

- [`.githooks/pre-commit`](../../.githooks/pre-commit) は、gitleaks 自身が文書化しているコミット前フックの呼び出し方である `gitleaks git --pre-commit --staged --redact` を通じて、ステージ済みの全ファイルを禁止パターンでスキャンし、一致すればコミットを拒否します。
- [`.githooks/prepare-commit-msg`](../../.githooks/prepare-commit-msg) は、merge コミットに対して同じスキャンを行います。このフックが起動する時点で、ステージ済みのインデックスにはすでに取り込む側のツリーが入っているため、すでにシークレットを含むブランチを merge しても、それを黙って再導入しないようにできます。

既存の [`.githooks/commit-msg`](../../.githooks/commit-msg) フックは、スコープ付き subject のチェックの手前に `gitleaks stdin --redact`(コミットメッセージのファイルをパイプで渡します)の呼び出しを加えます。コミットメッセージ本文そのものに貼り付けられたシークレットも捕まえるためです。git は 1 つのフック名につき 1 本のスクリプトしか実行しないため、2 つのチェックはそれぞれ独立したファイルではなく、この 1 つのファイルを共有します。`gitleaks` がまだ `PATH` に無いときは、どのフックも緩やかに縮退します。既存の commit-msg フックが `uv` の不在時にすでに no-op になっているのと同じように、コミットへの影響なくスキップするので、まだインストールしていない参加者がコミットをブロックされることはなく、保護が効いていないだけの状態にとどまります。`--redact` によって、実際に一致した内容が端末や CI のログに平文で出力されることもありません。

**B. `.gitleaks.toml` は既定のルールセットを置き換えるのではなく拡張します。** [`.gitleaks.toml`](../../.gitleaks.toml) は `[extend] useDefault = true` を設定し、既定でカバーされない 2 点だけを追加します。Anthropic の API キーまたは OAuth トークン(`.env.example` の `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`)と、値自体には固有の形が無いため変数名で検出する `BAJUTSU_SERVE_TOKEN` / `GRAFANA_ADMIN_PASSWORD`([`deploy/self-host/README.md`](../../deploy/self-host/README.md) がオペレーターに設定させている実際のデプロイ時シークレットです)です。Anthropic のルールだけは単純な追加では済みませんでした。既定のルールセットにはすでに `anthropic-api-key`(`sk-ant-api03-…`)と `anthropic-admin-api-key`(`sk-ant-admin01-…`)が入っていますが、どちらも `CLAUDE_CODE_OAUTH_TOKEN` の `sk-ant-oat01-` プレフィックスをカバーしません。この 2 つに加えて *3 つ目の* 独立したルールを登録したところ(ドキュメントから推測したのではなく、gitleaks v8.30.1 に対して実際に再現して確かめました)、gitleaks は 3 つのルールすべてに一致しなくなりました。修正は、`anthropic-api-key` ルールを **同じ id で** 上書きし、3 つのプレフィックスすべてをカバーする 1 本の広い正規表現にした上で、`anthropic-admin-api-key` を無効化し、`sk-ant-` に一致するルールが常に 1 つだけ登録されるようにすることでした。設定内のすべての正規表現は、単に読んで妥当そうだと判断するのではなく、実際に正しい形のシークレットに対してスクラッチのスキャンで検証しました。

**C. CI の再スキャンがローカルフックの隙間を塞ぎます。** ローカルフックが守るのは、それが配線されていて、かつ `--no-verify` で作られていないコミットに限られ、どちらも常には成り立ちません。これは、`make check` がまさに pre-push フックと CI の両方で同一に走る理由と同じ論拠です。新設する `make lint-secrets` ターゲットは、追跡対象の全ファイルを再スキャンし(`gitleaks dir . --redact`。`.gitignore` を尊重します)、`make check` に組み込まれます。`actionlint` に対して `lint-actions` がすでに使っている「通知を出してスキップする」パターンをそのまま踏襲し、`gitleaks` が `PATH` に無いローカルでは通知を出してスキップし、CI では必ず先にインストールします。インストールは `v8.30.1` リリースの tar ball を固定し、公開されているチェックサムを検証します。これは `actionlint` 自身のインストール手順がすでに使っているのと同じパターンで、リリースを切っていなかった git-secrets のときよりも gitleaks にはよく合います。

**D. `.gitleaks.toml` 内の `[[allowlists]]` が、正当な誤検出のための逃げ道です。** `gitleaks dir .` をこのリポジトリの既存のツリーに対して実際に走らせたところ、実在する誤検出が見つかりました。それぞれをパターン自体を緩めるのではなく、`targetRules` で絞り込んだ、レビュー可能なエントリにしています。[`tests/test_github_app.py`](../../tests/test_github_app.py) にある、意図的に壊れた PEM フィクスチャ(鍵の本体に文字どおり `nope` を置いたもの)は、gitleaks の既定の `private-key` ルールに一致しますが秘密鍵ではありません。`deploy/self-host/.env.example` の自己説明的なプレースホルダー `change-me` と、`deploy/self-host/docker-compose.yml` の `${VAR:?…}` / `${VAR:-…}` というシェルの変数展開は、どちらも `BAJUTSU_SERVE_TOKEN` / `GRAFANA_ADMIN_PASSWORD` をリテラルな値なしに参照しているだけです。[`bajutsu/serve/launchagent.py`](../../bajutsu/serve/launchagent.py) は、すでに検証済みの `token` 変数から代入しているだけで、リテラル値ではありません。そして gitleaks 自身の、エントロピーによるヒューリスティックである既定ルール `generic-api-key` は、Android の AVD デバイス名(`.github/workflows/android-e2e.yml`)、JS のメトリクスキーのリテラル(`bajutsu/templates/serve.metrics.mjs`)、TOTP のテストフィクスチャのシークレット(`tests/test_totp.py`、`tests/test_totp_step.py`)にも一致してしまいますが、どれも API キーではありません。

**E. ドキュメント。** [`docs/ai-development.md`](../../docs/ai-development.md)(および `docs/ja/` の対訳)に、この 2 層のガードを説明する節を追加します。[`SECURITY.md`](../../SECURITY.md)(および日本語版)にも、既存の API キーに関する注意点と並べて記載します。

## 検討した代替案

- **本項目が当初選んでいた git-secrets。** 見直しの過程で却下しました。7 年間リリースが無い一方(本項目が一時的に使っていた固定コミットも 2019 年のものでした)、gitleaks は現役でメンテナンスされています。git-secrets はシステムの `grep -E` に処理を委譲するため、GNU と POSIX の間の正規表現の移植性バグを、レビューの過程で 2 回にわたって再導入してしまいました(詳細設計を参照)。これは、どのプラットフォームでも自前の正規表現エンジンでマッチングする gitleaks には起こり得ないクラスのバグです。git-secrets のローカル `git config` によるパターン保存も、自己修復する `make hooks` の手順を必要としていましたが、gitleaks の設定はただの追跡対象ファイルであるため、本項目ではその手順自体が不要になりました。
- **CI でコミット履歴全体をスキャンする(制限なしの `gitleaks git`)。** 本項目の範囲としては却下しました。ここでの目的は、依頼の趣旨に沿って今後シークレットがコミットされるのを防ぐことにあります。履歴全体のスキャンはより遅く、そこで見つかった過去の誤検出は、チェックを green にするまでに過去のコミット 1 つずつのトリアージを要します。今回には含めず、別項目の候補として残します。
- **ローカルの pre-commit フックだけに頼り、CI のステップを設けない。** 却下しました。フックは `--no-verify` で回避できますし、`make setup` を飛ばしたクローンでは単に配線されません。したがって、独立した CI の再スキャンだけが実際に PR をゲートします。これは、`make check` 自体がフックだけを信頼せず pre-push フックと CI の両方で走る理由とまったく同じです。
- **Anthropic の OAuth トークンのプレフィックスを、既定のルールとは別の新しいルールとして登録する。** 実際に検証した結果、`sk-ant-` から始まるどのルールにも gitleaks が一致しなくなってしまうことが判明したため(詳細設計の B を参照)、却下しました。既存の `anthropic-api-key` ルールを同じ id で 1 本の統合された正規表現に上書きし、これで冗長になった `anthropic-admin-api-key` を無効化する版だけが、実際に動作すると確認できたものです。

## 進捗

- [x] A。追跡対象の `pre-commit` / `prepare-commit-msg` フック。`commit-msg` に同じスキャンを追加しました。
- [x] B。既定のルールセットを、Anthropic のキー/OAuth トークンのルールと `BAJUTSU_SERVE_TOKEN` / `GRAFANA_ADMIN_PASSWORD` のルールで拡張する `.gitleaks.toml`。
- [x] C。`make lint-secrets` を `make check` に組み込みました。CI が固定バージョンの、チェックサムを検証した gitleaks リリースをインストールして実行します。
- [x] D。実際にツリーをスキャンして見つけたすべての誤検出に対する、`.gitleaks.toml` の `[[allowlists]]` エントリ。
- [x] E。`docs/ai-development.md`(および日本語版)、`SECURITY.md`(および日本語版)、`CLAUDE.md`、`Brewfile` を更新しました。

## 参考

- [gitleaks](https://github.com/gitleaks/gitleaks)：本項目が組み込むツールです。
- [BE-0069 — Executable contributor guardrails](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md)：本項目が継ぐ *Contributor workflow* の先例（文章の手順を自己修復するコマンドに変える）と、本項目が拡張する既存の `commit-msg` フックです。
- [BE-0043 — Conflict-resistant file flow](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)：本項目の追跡対象の `.gitleaks.toml` 設定が、そもそも必要とせずに済ませている、クローンごとのローカル `git config` を自己修復するパターンです。
- [`SECURITY.md`](../../SECURITY.md)、[`.env.example`](../../.env.example)：本項目が実行可能なチェックで裏付ける、既存の文章のみのシークレット取り扱い指針です。
- [`docs/ai-development.md`](../../docs/ai-development.md)：並行開発ガイドです。仕組みは「コミット前にシークレットをブロックする」節を参照してください。
