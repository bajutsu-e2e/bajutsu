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

Bajutsu の決定的な開発時ゲート、すなわち `make check`（lock-check、format-check、ruff、shellcheck、
actionlint、mypy、pytest とカバレッジ）を pre-push フックと CI が共有する仕組みが、多数の並行ブランチを
衝突させずに緑へ保つ土台になっています（[CLAUDE.md](../../CLAUDE.md)、[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)）。
そのゲート自体を監査したところ、土台は堅牢だが、確定した忠実性の不具合と、いくつか欠けている層が見つかりました。
本項目はゲートを強化します。CI が構造的に `make check` を反映するようにし、ロードマップ用スクリプトを型検査の
対象に加え、分岐カバレッジを導入し、さらに2つのセキュリティ層（セキュリティ linter と依存の脆弱性
ゲート）を追加します。CodeQL は GitHub の default setup のままにします。

これは純粋に開発者向けの基盤であり、ツールの挙動やランタイム、シナリオの意味論には一切触れません。prime
directive の範囲内にとどまり、決定的ゲートに増えるのは検査であって LLM ではありません。

## 動機

- **CI が `make check` からドリフトしていました。** CI は各ゲート手順を Makefile のターゲット呼び出しではなく
  手書きで複製していたため、複製した引数一覧がずれえます。実際すでに2か所ずれていました。shellcheck の対象
  ファイル一覧が `scripts/merge-roadmap-index.sh` と `demos/tour/demo.sh` を欠いており（pre-push フックは
  この2つも lint する）、mypy のターゲットとカバレッジ呼び出しも複製されていて次にずれる手前でした。共有
  ゲートの約束、「ローカルで緑なら CI も緑」が静かに損なわれていました。
- **ロードマップ用スクリプトがゲートで型検査されていませんでした。** `mypy` の対象は `bajutsu demos` だけで、
  ロードマップの不変条件を司る `scripts/`（索引生成、promote、ID 採番）は型検査なしで動いていました。ここの
  型バグはロードマップを守る仕組みそのものを壊しえます。
- **カバレッジが行ベースかつ全体合算のみでした。** 未テストの分岐は数に入らず、パッケージ全体に対する
  単一の85%床は、よくカバーされたモジュールの陰に薄いモジュールを隠せました。
- **ゲートにセキュリティ lint がありませんでした。** subprocess を起動し、認証を扱い、secret を解決するツールで
  ありながら、`S`（bandit）lint がなく、ハードコードされた secret、安全でないハッシュ、危険なデシリアライザ
  が指摘されませんでした。（SAST 自体は CodeQL の default setup がすでに提供していました。）
- **依存の脆弱性ゲートがありませんでした。** Dependabot は版上げを提案するが、ロックされた依存グラフに既知の CVE
  があっても CI は落ちませんでした。

## 詳細設計

### CI が構造的に `make check` を反映する

`check` ジョブの各手順は、コマンドを再掲する代わりに Makefile のターゲット（`make lock-check /
format-check / lint / lint-sh / typecheck / test`）を呼びます。コマンドと対象ファイル、ターゲットの一覧は
Makefile の1か所だけに存在することになり、CI はローカルゲートや pre-push フックからドリフトしようがありません。
`UV_NO_SYNC=1` をジョブレベルに設定し、従来の `uv run --no-sync` と同じ速度を保ちます（環境は明示的な
`uv sync` 手順ですでに整っている）。actionlint だけはインラインに残します。`./actionlint`（PATH 外）として
インストールされ、`make lint-actions` は actionlint が PATH 上にあるときだけ実行する作りだからです。

### ロードマップ用スクリプトの型検査

`make typecheck` の対象に `scripts` を加えました（`mypy bajutsu demos scripts`）。これで現れた1件の型エラーは
修正しました。`tests/` は意図的に外します。strict な mypy をテスト群にかけると数百件の指摘が出るため、別の作業と
します。

### 分岐カバレッジ

`[tool.coverage.run] branch = true` により、全行が実行されていても未テストの `if`／`else` の経路が床に
対して数えられます。得た改善を固定するため床を85%から87%（実測 87.40%）へ引き上げました。今後さらに段階的に
上げられます。

### セキュリティ linter（ruff `S`／flake8-bandit）

ruff の `S` ルールを有効化しました。`S101`（assert）と `S603`（subprocess）はグローバルに ignore します。assert
はコードベース全体で内部不変条件を表すために使われ、bajutsu は assert が除去される `-O` で実行されることが
ありません。`S603` はすべての subprocess 呼び出しで発火するが、こちらの呼び出しは argv 配列（`shell=False`）で
あり、本当に危険な `shell=True` の形は `S602` が引き続き捕捉します。`tests/` と `demos/` は `S` カテゴリ全体
を、`scripts/` は `S607` を除外します（git や uv を PATH 経由で起動するため）。`bajutsu/` に残る指摘は個別に
処理しました。実体のある修正が2件（非暗号用途の dedup キーに対する `hashlib.sha1(..., usedforsecurity=False)`
と、OAuth エンドポイント定数を `_TOKEN` から `_EXCHANGE_URL` に改名し、ハードコードされた secret（`S105`）
に見えないようにしたこと）。残りは確認済みの誤検知への抑制で、インライン `noqa` は http／https に限定済みの
`urlopen`（`S310`）、`_yaml.py` の `yaml.load`（`SafeLoader` のサブクラス）は `S506` をファイル単位で
ignore します。後者は行を main と完全に一致させ、main で同じ指摘がすでに false positive として dismiss 済み
の状態に保つためです。

### SAST（CodeQL default setup を維持）

CodeQL は GitHub の **default setup** のままにします。default setup は Python、GitHub Actions、
JavaScript/TypeScript、Swift をゼロ保守かつ自動更新で走査します。本項目はあえて advanced ワークフローを
コミットしません。Python のみの advanced は default setup と競合し、default setup を無効化すると残り3言語の
網羅を落とします。4言語すべてに合わせると、小さく純粋に Foundation だけの BajutsuKit のために macOS の Swift
ビルドを増やすことになり、見合いません。よってここでのセキュリティ強化は `S` linter と後述の依存脆弱性ゲートで
あり、CodeQL はそのままにします。

### 依存の脆弱性ゲート（pip-audit）

CI の `audit` ジョブが、ロックされた依存グラフ（`uv export`、ランタイムと出荷するすべての extra）を
書き出し、固定された版を `pip-audit --no-deps` で監査します。既知の CVE があればジョブが落ち、確認済みの advisory は
`--ignore-vuln` で受け入れます。これは版上げを提案する Dependabot を固いゲートで補完します。リポジトリ設定で
Dependabot の security updates を有効にするのが、合わせ技として推奨です。

## 検討した代替案

- **CI で `make check` を丸ごと実行する**（ターゲット単位の手順ではなく）：却下。手順ごとの名前は CI の
  UI を読みやすくし、また `make check` は CI では無意味な `hooks` ターゲット（git config）に依存します。
  ターゲット単位の呼び出しなら UI と単一の source の両方を保てます。
- **欠けている shellcheck の2ファイルだけ足す**：却下。症状への対処にすぎず、手書きの複製はまたずれます。
  CI を Makefile 経由にすれば複製そのものがなくなります。
- **いま `tests/` を mypy に加える**：見送り。数百件の指摘が出るため、緩和した per-module 設定を伴う
  集中した後続作業とします。
- **まず Proposal として書く（`BE-XXXX` プレースホルダ）**：ここでは不要。本作業は同じ変更で実装済みな
  ので、*コントリビューターワークフロー* のトピックに実装済みとして直接置き、[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)
  の姉妹項目とします。
- **advanced な CodeQL ワークフローをコミットする**（Python のみ、または4言語すべて）：却下。リポジトリの
  default setup と競合し、Python のみだと default setup 無効化後に他言語の網羅を落とし、4言語すべてだと
  marginal な価値のために macOS の Swift ビルドを増やします。default setup がすでに広く保守された SAST を提供します。
- **より重い構え**（mutation testing、`tests/` の型付け、ファイル単位のカバレッジ床）：範囲外。今後の手
  として記録します。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- [CLAUDE.md](../../CLAUDE.md) — 契約としてのゲート。`make check` を CI と pre-push フックが反映します。
- [BE-0043 — コンフリクトに強いファイル流動](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)
  — 本項目が拡張する、コントリビューターワークフローの姉妹項目（自己修復フック、索引の生成）。
- [.github/workflows/ci.yml](../../.github/workflows/ci.yml)、[Makefile](../../Makefile)、[pyproject.toml](../../pyproject.toml)
  — 本項目が強化するゲート。
