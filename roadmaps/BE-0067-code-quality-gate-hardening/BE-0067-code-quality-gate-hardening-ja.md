[English](BE-0067-code-quality-gate-hardening.md) · **日本語**

# BE-0067 — コード品質ゲートの強化（CI の忠実性、セキュリティ lint、サプライチェーン）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0067](BE-0067-code-quality-gate-hardening-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0067") |
| 実装 PR | [#170](https://github.com/bajutsu-e2e/bajutsu/pull/170) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の決定的な開発時ゲートは `make check` で、lock-check、format-check、ruff、shellcheck、actionlint、mypy、pytest とカバレッジを実行します。pre-push フックと CI が、これを反映しています。このゲートのおかげで、多数の並行ブランチが衝突せずに緑を保てます（[CLAUDE.md](../../CLAUDE.md)、[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)）。

ゲート*自体*を監査したところ、土台は堅牢でしたが、確定した忠実性の不具合が 1 つと、欠けている層がいくつか見つかりました。本項目は、ゲートを次の 4 点で強化します。

- CI が構造的に `make check` を反映するようにします。
- ロードマップ用スクリプトを、型検査の対象に加えます。
- 分岐カバレッジを導入します。
- セキュリティ linter と依存の脆弱性ゲートで、2 つのセキュリティ層を追加します。

CodeQL は、GitHub の default setup のままにします。

これは純粋に開発者向けの基盤です。ツールの挙動、ランタイム、シナリオの意味論には一切触れず、prime directive の範囲内にとどまります。決定的ゲートに増えるのは検査であり、LLM ではありません。

## 動機

- **CI が `make check` からドリフトしていました。** CI は、各ゲート手順を Makefile のターゲット呼び出しではなく、手書きで複製していました。複製した引数の一覧は、ずれえます。実際に 2 か所がずれていました。shellcheck の対象ファイルの一覧が、`scripts/merge-roadmap-index.sh` と `demos/tour/demo.sh` を欠いていました（pre-push フックは、この 2 つも lint します）。mypy のターゲットとカバレッジの呼び出しも複製されており、次にずれる状態でした。共有ゲートの約束である「ローカルで緑なら CI も緑」が、静かに損なわれていました。
- **ロードマップ用スクリプトが、ゲートで型検査されていませんでした。** `mypy` の対象は `bajutsu demos` だけでした。ロードマップの不変条件を司る `scripts/`（索引生成、promote、ID 採番）は、型検査なしで動いていました。ここの型バグは、ロードマップを守る仕組みそのものを壊しえます。
- **カバレッジが、行ベースかつ全体合算だけでした。** 未テストの分岐は数に入りませんでした。パッケージ全体に対する単一の 85% の床は、よくカバーされたモジュールの陰に、薄いモジュールを隠せました。
- **ゲートにセキュリティ lint がありませんでした。** このツールは、subprocess を起動し、認証を扱い、secret を解決します。それでも `S`（bandit）lint がなく、ハードコードされた secret、安全でないハッシュ、危険なデシリアライザは指摘されませんでした。（静的アプリケーションセキュリティテスト（SAST）自体は、CodeQL の default setup がすでに提供していました。）
- **依存の脆弱性ゲートがありませんでした。** Dependabot は版上げを提案しますが、ロックされた依存グラフに既知の CVE があっても、CI は落ちませんでした。

## 詳細設計

### CI が構造的に `make check` を反映する

`check` ジョブの各手順は、コマンドを再掲する代わりに、Makefile のターゲット（`make lock-check / format-check / lint / lint-sh / typecheck / test`）を呼びます。コマンドと、対象ファイルやターゲットの一覧は、Makefile の 1 か所にだけ存在します。CI は、ローカルゲートや pre-push フックからドリフトしようがありません。

`UV_NO_SYNC=1` は、ジョブレベルに設定します。従来の `uv run --no-sync` と同じ速度を保つためで、環境は、明示的な `uv sync` の手順ですでに整っています。actionlint だけは、インラインに残します。ジョブは actionlint を `./actionlint` として入れるので、PATH 上にはありません。`make lint-actions` は、PATH 上にあるときだけ actionlint を実行します。

### ロードマップ用スクリプトの型検査

`make typecheck` の対象に、`scripts` を加えました（`mypy bajutsu demos scripts`）。これで現れた 1 件の型エラーは修正しました。`tests/` は、意図的に外します。strict な mypy をテスト群にかけると、数百件の指摘が出るので、別の作業とします。

### 分岐カバレッジ

`[tool.coverage.run] branch = true` により、全行が実行されていても、未テストの `if` / `else` の経路が床に対して数えられます。得た改善を固定するため、床を 85% から 87%（実測 87.40%）へ引き上げました。今後、さらに段階的に上げられます。

### セキュリティ linter（ruff `S` / flake8-bandit）

ruff の `S` ルールを有効にしました。次の 2 つは、グローバルに ignore します。

- `S101`（assert）。assert は、コードベース全体で内部の不変条件を表すために使われ、bajutsu は `-O` で実行されることがありません。
- `S603`（subprocess）。すべての subprocess 呼び出しで発火しますが、こちらの呼び出しは argv 配列（`shell=False`）です。本当に危険な `shell=True` の形は、`S602` が引き続き捕捉します。

`tests/` と `demos/` は、`S` カテゴリ全体を除外します。`scripts/` は、`S607` を除外します。git や uv を PATH 経由で起動するためです。

`bajutsu/` に残る指摘は、個別に処理しました。実体のある指摘は 2 件で、修正しました。

- 非暗号用途の dedup キーに対する、`hashlib.sha1(..., usedforsecurity=False)`。
- OAuth エンドポイント定数の、`_TOKEN` から `_EXCHANGE_URL` への改名。ハードコードされた secret（`S105`）に見えないようにするためです。

確認済みの誤検知 2 件には、抑制を付けました。

- http と https に限定済みの `urlopen` への、インラインの `noqa`（`S310`）。
- `_yaml.py` の `yaml.load`（`SafeLoader` のサブクラス）への、ファイル単位の `S506` の ignore。ファイル単位にするのは、行を main と完全に一致させるためです。main では、CodeQL が同じ指摘をすでに誤検知として dismiss しています。

### SAST（CodeQL default setup を維持）

CodeQL は、GitHub の **default setup** のままにします。default setup は、Python、GitHub Actions、JavaScript/TypeScript、Swift を、保守ゼロかつ自動更新で走査します。本項目は、advanced なワークフローをあえてコミットしません。

- Python のみのワークフローは、default setup と競合します。default setup を無効にすると、残り 3 言語の網羅を落とします。
- 4 言語すべてに合わせたワークフローは、macOS の Swift ビルドを増やします。小さく、Foundation だけを使う BajutsuKit には、見合いません。

したがって、ここでのセキュリティ強化は、`S` linter と、後述の依存脆弱性ゲートです。CodeQL は、そのままにします。

### 依存の脆弱性ゲート（pip-audit）

CI の `audit` ジョブが、ロックされた依存グラフ（`uv export`、ランタイムと出荷するすべての extra）を書き出します。固定された版は、`pip-audit --no-deps` で監査します。既知の CVE があればジョブが落ち、確認済みの advisory は `--ignore-vuln` で受け入れます。このゲートは、版上げを提案する Dependabot を、固いチェックで補完します。リポジトリ設定で Dependabot の **security updates** を有効にするのが、合わせ技として推奨です。

## 検討した代替案

- **CI で `make check` を丸ごと実行する**（ターゲット単位の手順にしない）。却下します。手順ごとの名前は、CI の UI を読みやすくします。また `make check` は、CI では無意味な `hooks` ターゲット（git config）に依存します。ターゲット単位の呼び出しなら、UI と単一の source の両方を保てます。
- **欠けている shellcheck の 2 ファイルだけを足す。** 却下します。症状への対処にすぎず、手書きの複製はまたずれます。CI を Makefile 経由にすれば、複製そのものがなくなります。
- **いま `tests/` を mypy に加える。** 見送ります。数百件の指摘が出るので、緩和した per-module 設定を伴う、集中した後続作業とします。
- **まず Proposal として書く**（`BE-XXXX` プレースホルダ）。ここでは不要です。本作業は同じ変更で実装済みなので、*コントリビューターワークフロー* のトピックに、実装済みとして直接置きます。[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md) の姉妹項目になります。
- **advanced な CodeQL ワークフローをコミットする**（Python のみ、または 4 言語すべて）。却下します。リポジトリの default setup と競合します。Python のみだと、default setup を無効にした後に、他言語の網羅を落とします。4 言語すべてだと、わずかな価値のために macOS の Swift ビルドを増やします。default setup が、広く保守された SAST をすでに提供しています。
- **より重い構え**（mutation testing、`tests/` の型付け、ファイル単位のカバレッジの床）。範囲外とし、今後の手として記録します。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- [CLAUDE.md](../../CLAUDE.md)：契約としてのゲート。`make check` を CI と pre-push フックが反映します。
- [BE-0043 — コンフリクトに強いファイル流動](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)：本項目が拡張する、コントリビューターワークフローの姉妹項目（自己修復フック、索引の生成）。
- [.github/workflows/ci.yml](../../.github/workflows/ci.yml)、[Makefile](../../Makefile)、[pyproject.toml](../../pyproject.toml)：本項目が強化するゲート。
