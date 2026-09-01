[English](BE-XXXX-bajutsu-feature-package-topology.md) · **日本語**

# BE-XXXX — `bajutsu/` を、機能ディレクトリと共通基盤 `common/` へ再編する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-bajutsu-feature-package-topology-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Codebase quality & technical debt |
| 関連 | [BE-0257](../BE-0257-layer-package-topology/BE-0257-layer-package-topology-ja.md)、[BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement-ja.md)、[BE-0135](../BE-0135-module-naming-debt/BE-0135-module-naming-debt-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/` には313個の Python ファイルがあり、行数は合計でおよそ77,300行です。現状はここに3種類の
整理原理が同居しています。`crawl/`、`serve/`、`mcp/`、`codegen/` のように、すでに機能名そのものを
ディレクトリ名に持つもの。[BE-0257](../BE-0257-layer-package-topology/BE-0257-layer-package-topology-ja.md)
が決定的コアから役割ごとに切り出したパッケージ（`drivers/`、`evidence/`、`orchestrator/`、
`runner/`、`assertions/`、`config/`、`scenario/`、`report/`、`platform_lifecycle/`）。そして、
`bajutsu/` 直下にパッケージを持たずフラットに並ぶ、残り44個のモジュールです。このフラットな残りの
中には `run`、`record`、`triage` という3つの機能がまるごと含まれています。いずれも1つか数個の
ファイルしかなく、`crawl/` や `serve/` のように機能名を名乗るディレクトリを持ちません。本提案は
BE-0257 が始めた再編を完了させるものです。`bajutsu/` が持つすべてのモジュールを、9つの機能
ディレクトリ（`run/`、`crawl/`、`record/`、`triage/`、`serve/`、`mcp/`、`codegen/`、`analysis/`）の
いずれか、または各機能が共通して利用する決定的コアと AI 周辺インフラをはじめて1つの名前でまとめる
新設の `common/` パッケージへ移します。どの段階もファイルの移動と import 文の書き換えのみであり、
実行時の挙動は変えません。

## 動機

モジュールがどの機能に属するかは、それが置かれている場所から見えるべきです。これは BE-0257 が
アーキテクチャレイヤについて掲げた主張と同じものを、機能という軸に当てはめたものです。BE-0257 は
`codegen`、`crawl`、`github`、`agents`、`evidence`/`analysis`、`analytics` という6つの密なクラスタを
パッケージ化し、その直接の帰結として、`pyproject.toml:300-419` の
`[tool.importlinter.contracts]` ブロックがクラスタごとのモジュールを手作業で列挙する分量を
縮めました。BE-0257 が手つかずのまま残したクラスタこそ、本提案が引き受ける対象です。BE-0257 が
名前だけ挙げて移動しなかった決定的コアの役割別パッケージ（`drivers/`、`evidence/`、
`orchestrator/`、`runner/`、`assertions/`、`config/`、`scenario/`、`report/`、
`platform_lifecycle/`）、そして BE-0257 の対象範囲そのものが空けていた隙間、すなわちユーザーが
もっとも頻繁に使う3つのコマンド `run`、`record`、`triage` です。この3つは、これまで一度も自分の
機能ディレクトリを持ったことがありません。`record` の観察、提案、実行、出力のループはフラットな
`record.py` と `record_capture.py` に、`triage` の M4 自己修復エンジンはフラットな `triage.py` に
あります。`run` のプロビジョニング判断、lease の取得と解放、plan の構築、ツリー中最大のコマンド
モジュールにあたるロジックは、`run` という名を持つパッケージのどこにもなく、
`cli/commands/run.py` にあります。`bajutsu run` がデバイスをどうプロビジョニングするかを変えたい
コントリビュータは、そのコードが CLI の配線を表すディレクトリ、`cli/commands/` の中にあることを、
あらかじめ知っている必要があります。

BE-0257 は、レイヤをまたいだ名前の衝突を3組挙げましたが、解消はしませんでした。
`bajutsu/mailbox.py` と `bajutsu/runner/mailbox.py`、`bajutsu/object_store.py` と
`bajutsu/serve/server/object_store.py`、`bajutsu/handoff.py` と `bajutsu/cli/handoff.py` です。
どの組も、どちらの import パスがどちらのファイルに解決されるかを知っていなければ区別できません。
3組とも BE-0257 の6クラスタの対象外だったため、今日もそのまま未解決です。同じ理由でもう1組
存在します。`bajutsu/totp.py` と `bajutsu/orchestrator/actions/handlers/totp.py` です。フラットな
モジュールをすべて名前を持つパッケージへ移せば、それぞれの組は自己文書化された別々のパスへ解決
されます。BE-0257 が自らの6クラスタの内側の衝突に適用したのと同じ解決です。

最後に、`bajutsu/common/` という名前は今日まだ存在しません。決定的コアと、それを支える AI 周辺
インフラ（`agents/`、`ai/`、`analytics/`、`github/`、`cloud/`）は、合わせるとおよそ15個の
トップレベルの役割別パッケージと、44個のフラットファイルの大半とを占めています。それだけの共有
基盤でありながら、そう名乗るディレクトリがありません。本提案が着地すれば、読み手はパスだけを見て
機能コマンドとその土台にある共有基盤とを見分けられるようになります。ある機能が import するが
自分では所有していないものはすべて `bajutsu/common/` 配下に置かれ、`bajutsu/` 直下には9つの機能
ディレクトリと `common/`、そしてどの機能にも属さない一握りのコマンド（`doctor`、`lint`、
`schema`）のための薄い `cli/` パッケージだけが残ります。

## 詳細設計

作業はクラスタごとに MECE です。BE-0257 が敷いた前例に倣い、各段階は個別のフォローアップ PR として
着地し、`make lint-imports` と `make check` で独立に検証できます。各段階は自分が移動するモジュール
への import をリポジトリ全体にわたって同じ PR の中で書き換えるため、どの段階も他の段階が先に着地
することに依存しません。すでに `__init__.py` による re-export がある箇所では、公開の import パスを
そのまま保ちます。`bajutsu/report/__init__.py`（BE-0043）がすでに確立しているパターンと同じです。
9つの段階はいずれも、実行時のロジックには手を入れない、ファイルの移動と import の書き換えのみです。
コードの移動を伴わない10番目の段階が、どのゲートも強制しない文章と図を仕上げます。

移動を伴う9つの段階は、いずれも同じ5つの設定面に触れます。ある段階の PR は、その段階が移動する
モジュールについて次の5つすべてを更新して初めて完了します。

1. **`pyproject.toml` の `[tool.importlinter]` 契約**（`pyproject.toml:300-419`）。段階が移動する
   モジュールをすべて新しい dotted path へ向け直します。あるクラスタの列挙が空になる段階では、
   BE-0257 と同じように、その列挙を新しいパッケージ名1つへ集約します。
2. **`pyproject.toml` の `[tool.ruff.lint.per-file-ignores]`**（`pyproject.toml:206`）。厳密パスで
   列挙されている `bajutsu/_yaml.py`、`bajutsu/backends.py`、`bajutsu/runner/launch_server.py` の
   3つは、段階7と段階8で移動します。
3. **`Makefile` の `DOCSTRING_PATHS`**（`Makefile:105`）。`make lint-docstrings` が読む一覧です。
   すでに Google スタイルの docstring を備えて移動するモジュールは、新しいパスへ向け直したうえで
   一覧に残します。
4. **`docs/architecture.md` の「Module list and roles」表**。`scripts/lint_module_map.py`
   （`make lint-module-map`）が、モジュールを移動したのに対応する行を更新していない PR を落とし
   ます。
5. **`scripts/e2e_changes.py` の `_PERIPHERY_EXCLUSIONS`**。変更がオンデバイス E2E ワークフローを
   発火させるべきかを判定する厳密パスの表です。`tests/test_e2e_changes.py` がこれを検証するため、
   対象パスを移動する段階は同じ PR の中で表を更新します。

`coverage-floors.json` はどの段階でも手編集が不要です。移動のあとに `make test && make
coverage-floors` を実行すれば再生成され、想定される差分は移動したファイルそれぞれのキーが同じ
カバレッジ数値のまま新しいキーへ入れ替わることだけです。それ以外の低下が出た場合は、リネームの
副作用ではなく実際の後退なので、原因を突き止めます。

1. **`common/analytics/`、`common/cloud/`、`common/github/`**
   （`claude/reorg-common-analytics-cloud-github`）。既存の `analytics/`、`cloud/`、`github/`
   パッケージを、内部は変えずに `common/` 配下へ移し、import パスだけを書き換えます。最小で
   結合度ももっとも低いクラスタであり、より大きな段階が頼りにする前に、機械的な移動と書き換えの手順を
   低リスクで検証する狙いで最初に選びます。
2. **`common/ai/`、`common/agents/`**（`claude/reorg-common-ai-agents`）。`ai/` と `agents/` を
   `common/` 配下へ移します。段階1とは無関係な、次に小さいクラスタです。
3. **`common/evidence/`、`common/report/`、`analysis/` の確定**
   （`claude/reorg-common-evidence-report-analysis`）。`evidence/` と `report/`（および
   `report/rows.py` 用のグルーピング補助である単独ファイル `from_grouping.py`）を `common/` 配下へ
   移します。あわせて、BE-0257 がすでに切り出していた `analysis/` を `common/` のサブパッケージに
   せず、独立した機能ディレクトリとして確定させます。実行結果を消費する側であり、verdict を導く側
   ではないためです。BE-0257 が `evidence/` と `analysis/` のあいだに引いたのと同じ、コアと消費者の
   区分です。BE-0257 の段階5と同じ組み合わせです。
4. **`common/assertions/`、`common/scenario/`**
   （`claude/reorg-common-assertions-scenario`）。`assertions/` と `scenario/`（および `scenario/`
   が使う `${ns.key}` 展開の補助 `interp.py`）を、シナリオスキーマの契約層としてまとめて `common/`
   配下へ移します。
5. **`common/config/`、`common/capability/`、`common/provisioning/`**
   （`claude/reorg-common-config-capability-provisioning`）。`config/`（および隣接する
   `config_source.py`。役割が異なる、プロジェクトの config バインディングを取得する側であり
   スキーマを定義する側ではないため、`config/` の中に入れず隣に残します）を `common/` 配下へ
   移します。`preflight.py`、`capability_preflight.py`、`capabilities.py` は新設の
   `common/capability/` へ、`requirements.py`、`provision.py` は新設の `common/provisioning/` へ
   まとめます。`provision.py` は CLI コマンドではなく、`scripts/install.sh` から
   `python -m bajutsu.provision` として直接起動される入口なので、この段階では
   `scripts/install.sh` と、モジュール自身が持つ `prog=` の文字列も
   `python -m bajutsu.common.provisioning.provision` へ追随更新します。
6. **`common/drivers/`**（`claude/reorg-common-drivers`）。`drivers/` を `common/` 配下へ移し、
   ドライバに隣接する5つのフラットファイル `elements.py`、`dom.py`、`web_network.py`、
   `webview.py`、`zorder.py` も同梱します。ツリーの中でもっともファンインが広いパッケージのため、
   先に5つの小さいクラスタで移動と書き換えの手順を確立してから着手する狙いで、6番目に回します。
7. **`common/orchestrator/`、`common/runner/`**
   （`claude/reorg-common-orchestrator-runner`）。`orchestrator/` と `runner/` を、フラットファイル
   `cancellation.py`、`backends.py`、`mailbox.py`、`totp.py` とともに `common/` 配下へ移します。
   この段階で、本項目の動機が挙げる `bajutsu/mailbox.py` と `bajutsu/runner/mailbox.py`、
   `bajutsu/totp.py` と `bajutsu/orchestrator/actions/handlers/totp.py` の衝突が解消します。
   最大かつもっとも中心的なクラスタであり、`common/` 側の段階の中では最後に置きます。依存する
   モジュールがすべて最終的な移動先を確定させたあとに着手するためです。
8. **`run/`、`record/`、`triage/`、および `common/` に残るファイル**
   （`claude/reorg-run-record-triage`）。`notify.py` を `run/notify.py`（`run` 完了後に送る
   Slack 通知で、他の機能からは使われません）へ、`record.py` を `record/loop.py`（観察、提案、実行、
   出力のループ）へ、`record_capture.py` を `record/capture.py` へ、`triage.py` を
   `triage/heuristic.py`（M4 自己修復エンジン）へ移します。残る `common/run_meta/`
   （`run_files.py`、`run_id.py`、`run_root.py`、`artifact_perms.py`、`object_store.py`）、
   `common/devices/`（`device_os.py`、`device_id.py`、`device_errors.py`）、`common/backend_cli/`
   （`adb.py`、`adb_resident.py`、`simctl.py`）の各パッケージと、どのクラスタにも属さない残りの
   単独ファイル（`doctor.py`、`lint.py`、`screenshots.py`、`handoff.py`、`deprecations.py`、
   `diagnostics.py`、`stall_diagnostics.py`、`_yaml.py`）を `common/` 直下にまとめます。
   `doctor.py` と `lint.py` は、機能ディレクトリではなく `common/` 直下にフラットなまま残します。
   どちらも決定的コアの import-linter 契約に直接名前が載っており、`analysis/`、`mcp/`、
   `common/runner/` からも広く参照されているためです。薄い Typer ラッパーだけが
   `cli/commands/` へ移ります。この段階で、本項目の動機が挙げる `bajutsu/handoff.py` と
   `bajutsu/cli/handoff.py` の衝突も解消し、`common/backend_cli/adb.py`（`adb`/`simctl` を叩く
   シェルラッパー）と、段階6で移動した `common/drivers/adb.py`（`Driver` の実装）とを、どちらの
   import パスがどちらへ解決するかを覚えていなくても、ディレクトリだけで見分けられるようにします。
   上記のすべての `common/` 段階のあとに置くのは、あらゆるモジュールの移動先パッケージがすでに
   確定していることが前提だからです。
9. **CLI の機能への同居**（`claude/reorg-cli-feature-colocation`）。単一コマンドの機能については、
   `cli/commands/<feature>.py` をその機能自身の `cli.py`（`run`、`crawl`、`record`、`triage`、
   `mcp`、`codegen`）へ移します。`cli/commands/{serve,worker,approve}.py` は
   `serve/cli/{serve,worker,approve}.py` へ移しますが、1ファイルに統合はしません。統合すると
   探しにくくなり、`serve` にサブコマンドが増えるたびにファイルが際限なく膨らむためです。
   `cli/commands/{audit,coverage,impact,stats,flakiness,export,trace}.py` も同じ理由で
   `analysis/cli/{audit,coverage,impact,stats,flakiness,export,trace}.py` へ個別のまま移します。
   `serve/flakiness.py` は `analysis/flakiness.py` へ、`trace.py` は `analysis/trace.py` へ移し
   ます。どちらも `docs/architecture.md` がすでに「読み取り専用でCIを絶対にゲートしない」分析群
   として `audit`/`coverage`/`impact`/`stats` と並べて説明している仲間だからです。`dotenv.py` は
   `cli/dotenv.py` へ移します。唯一の利用者が `cli/__init__.py` であり、この段階を通じて適用して
   いる規則、すなわち利用者が1箇所だけのフラットファイルは `common/` へ投げ込まずその利用者に
   隣接させる規則をここでも適用するためです。移動を伴う段階の中で最後に置くのは、段階1から8まで
   ですべてのモジュールの移動先がすでに確定しており、この段階の import 書き換えが、行き先を
   何も決めずに済む1回きりの機械的な作業になるからです。
10. **ドキュメント仕上げ**（`claude/reorg-docs-sweep`）。ファイルの移動はありません。
    `docs/architecture.md` の「Dependencies (layers)」の記述と図、`.github/workflows/{android,ios}
    -e2e.yml` のパスに関するコメント（`paths:` トリガーではないためどのゲートも発火させず、
    すべてのパスが確定するまで後回しにできます）、その他プローズに残る陳腐化したパス表記を
    更新します。リポジトリ全体を `grep` し、移動前のパスが残っていないことを確認して、この段階を
    締めくくります。

各段階は自分が移動するモジュールへの import をリポジトリ全体にわたって書き換えるため、どの段階も
他の段階が先に着地することに依存しません。上記の順序は、依存関係ではなく、リスクの大きさと移動先
パスがどれだけ確定しているかによって並べたものです。互いに独立した2つのクラスタ（たとえば段階1と
2、あるいは段階3から5)は別々のブランチで並行に組み立てられますが、`main` へマージできるのは常に
1本ずつです。どの段階も同じ5つの設定面に触れるため、2本目のブランチは1本目の段階のマージを
取り込んでからでないと着地できません。コンフリクトの多くは機械的な性質（別々の行への追加同士）に
なるはずですが、それでも1本ずつ目視での確認が要ります。

段階10のマージ後に `bajutsu/` の直下を一覧すれば、本項目が全体として着地したことを確認できます。
そこには `run/`、`crawl/`、`record/`、`triage/`、`serve/`、`mcp/`、`codegen/`、`analysis/`、
`common/`、そしてどの機能にも属さないコマンドのための薄い `cli/` だけが並び、`__init__.py` と
`__main__.py` を除くフラットな `.py` モジュールは残っていません。

## 検討した代替案

- **すべてを1つの PR でまとめて移動する。** 却下します。BE-0257 がこれを却下したのと同じ理由です。
  9つのクラスタは互いに独立しており、いくつかはそれ単体でも大きく（`common/orchestrator/` と
  `common/runner/` だけでも数千行に及びます）、1つに統合した PR ではレビューも、あるクラスタだけの
  取り消しも難しくなります。
- **`run`、`record`、`triage` はフラットなまま残し、決定的コアの役割別ディレクトリだけを
  パッケージ化する。** 却下します。BE-0257 がもともと持っていた対象範囲は完了しますが、本項目の動機が
  冒頭で挙げた不整合、`crawl/` や `serve/` は機能として読み取れるのに `run/` や `record/` は
  読み取れないという不整合が、ユーザーがもっとも頻繁に使う3つのコマンドについて手つかずのまま残り
  ます。
- **`common/` を新設せず、決定的コアのパッケージ群を `bajutsu/` の直下に残す。** 却下します。
  共有される層に名前がないままでは、読み手はトップレベルの一覧だけを見ても、どのディレクトリが
  ユーザーの呼び出す機能で、どれがすべての機能が共有するインフラなのかを区別できません。
  BE-0257 の動機が冒頭で挙げた見えにくさが、1段上の階層でそのまま繰り返される形になります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `bajutsu/common/analytics/`、`common/cloud/`、`common/github/`。
- [ ] `bajutsu/common/ai/`、`common/agents/`。
- [ ] `bajutsu/common/evidence/`、`common/report/`。`bajutsu/analysis/` を機能ディレクトリとして
  確定。
- [ ] `bajutsu/common/assertions/`、`common/scenario/`。
- [ ] `bajutsu/common/config/`、`common/capability/`、`common/provisioning/`。
- [ ] `bajutsu/common/drivers/`。
- [ ] `bajutsu/common/orchestrator/`、`common/runner/`。
- [ ] `bajutsu/run/`、`record/`、`triage/`、および残る `common/run_meta/`、`devices/`、
  `backend_cli/` パッケージと残余の単独ファイル。
- [ ] CLI の機能への同居（`<feature>/cli.py`、`serve/cli/`、`analysis/cli/`、`cli/dotenv.py`）。
- [ ] ドキュメント仕上げ（`docs/architecture.md` の依存関係図、ワークフローのパスコメント、
  移動前のパスが残っていないかの最終 `grep`）。

## 参考

- [BE-0257](../BE-0257-layer-package-topology/BE-0257-layer-package-topology-ja.md) — 本提案が
  引き継ぐ前例です。6つのフラットなクラスタをアーキテクチャレイヤ単位でパッケージ化しており、
  本項目は同じ段階分割、MECE、`make lint-imports` による独立検証という手法を、BE-0257 がフラット
  なまま残したクラスタへ適用します。
- [BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement-ja.md) —
  本提案と BE-0257 がともにディレクトリツリー上で可視化する、レイヤモデルと import-linter の
  ゲートです。
- [BE-0135](../BE-0135-module-naming-debt/BE-0135-module-naming-debt-ja.md) — BE-0257 がパッケージの
  水準で引き継いだ、先行するトップレベルモジュールの命名整理です。本提案はそれを機能の水準で
  引き継ぎます。
- `pyproject.toml:300-419` — 各段階が向け直す `[tool.importlinter.contracts]` ブロックです。
- `Makefile:105` — `make lint-docstrings` が読む `DOCSTRING_PATHS` の一覧です。
- `scripts/e2e_changes.py` — 段階8と段階9が更新する `_PERIPHERY_EXCLUSIONS` の表です。
  `tests/test_e2e_changes.py` がこれを検証します。
- `docs/architecture.md` — `scripts/lint_module_map.py` がツリーと突き合わせる「Module list and
  roles」表と、段階10が整合させ直す「Dependencies (layers)」の記述です。
- `bajutsu/mailbox.py`、`bajutsu/runner/mailbox.py`、`bajutsu/handoff.py`、
  `bajutsu/cli/handoff.py`、`bajutsu/totp.py`、`bajutsu/orchestrator/actions/handlers/totp.py` —
  本提案がディレクトリによって解消する名前の衝突で、BE-0257 の対象範囲が狭かったために手つかずの
  ままでした。
