[English](BE-0257-layer-package-topology.md) · **日本語**

# BE-0257 — 強制されているアーキテクチャレイヤをディレクトリとしてパッケージ化する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0257](BE-0257-layer-package-topology-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0257") |
| 実装 PR | [#1045](https://github.com/bajutsu-e2e/bajutsu/pull/1045)、[#1052](https://github.com/bajutsu-e2e/bajutsu/pull/1052)、[#1172](https://github.com/bajutsu-e2e/bajutsu/pull/1172)、[#1185](https://github.com/bajutsu-e2e/bajutsu/pull/1185) |
| トピック | コードベース品質・技術的負債 |
| 関連 | [BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement-ja.md)、[BE-0135](../BE-0135-module-naming-debt/BE-0135-module-naming-debt-ja.md)、[BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/` 直下には、約71個のトップレベルモジュールがフラットな名前空間に並んでいます。一方で、
コードベースには、[BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement-ja.md)
によってゲートで強制されているレイヤリングがすでにあります。決定的コア、契約、周辺という区分です。
レイヤの所属は、ディレクトリツリーからは見えません。`pyproject.toml` の `[tool.importlinter]` 契約に
手作業で維持されたモジュール一覧と、`docs/architecture.md` の文章としてのみ存在しています。

本提案は、すでに一つのまとまりとして振る舞っているモジュール群を、パッケージへグループ化します。
モジュールのレイヤとクラスタが、パスから読み取れるようになります。import-linter の契約も、手作業で
列挙せず、一つのパッケージ名で済みます。

## 動機

モジュールのアーキテクチャレイヤは、そのモジュールが置かれている場所から見えるべきです。現状は
そうなっていません。読み手と lint の設定は、レイヤの対応関係を頭の中に持ち歩き、
`docs/architecture.md` とフラットなファイル一覧を突き合わせる必要があります。

`pyproject.toml:216-316` が、その帰結を示しています。3つの `[tool.importlinter.contracts]` ブロックが、
40近いモジュール名を手作業で列挙しています。レイヤに、名指しできるパッケージの境界がないためです。

いくつかの密なクラスタは、ディレクトリ構造を除けば、すでにパッケージとして振る舞っています。

- **codegen**：`codegen.py`（401行）に加えて、`codegen_common.py`（99行）、`codegen_emit.py`
  （55行）、`codegen_playwright.py`（521行）、`codegen_uiautomator.py`（419行）です。バックエンドごとの
  emitter を持つ一つの生成器で、合計1,495行あります。ファイル名は、すでに共通の `codegen_` 接頭辞を
  持ちます。
- **crawl**：`crawl.py`（1,256行）に加えて、`crawl_flows.py`、`crawl_guide.py`、`crawl_report.py`、
  `crawl_repro.py`、`crawl_tabs.py` です。周辺で最大のモジュールです。`crawl_` 接頭辞により、flow、
  guide、report、repro、tab の関心事に、すでに分かれています。
- **AI やエージェントの周辺**：`agent.py`、`agents.py`、`claude_agent.py`、`claude_enrich_agent.py`、
  `claude_triage.py`、`anthropic_client.py`、`ai_availability.py`、`enrich.py`、`alerts.py` の9個の
  モジュールです。BE-0112 の契約上は、いずれも周辺レイヤの AI やエージェント関連です。現状は、まとまりを
  示す共通の接頭辞を持たないまま、フラットな名前空間に散らばっています。

レイヤをまたいだ名前の衝突が、3組あります。各組の2つのモジュールは、どの import パスがどちらに
解決されるかを知っている場合にだけ、区別できます。

- `bajutsu/mailbox.py` と `bajutsu/runner/mailbox.py`
- `bajutsu/object_store.py` と `bajutsu/serve/server/object_store.py`
- `bajutsu/handoff.py` と `bajutsu/cli/handoff.py`

トップレベルのモジュールをパッケージ化すれば、各組は、自己文書化された別々のパスに解決されます。

実在する import の循環も1つあります。`bajutsu/config_source.py` は `GitHubAccessError` を定義して
います（`config_source.py:178`）。`github_app.py` との循環を避けるために、`bajutsu.github_app` を関数
本体の中で遅延 import しています（`config_source.py:274`）。一方、`github_app.py` はモジュール先頭で、
`config_source` から `GitHubAccessError` を import しています（`github_app.py:24`）。

この遅延 import は、レイヤ構造の問題を回避しているだけで、解決していません。共有されているエラー型を
`github/` パッケージへ移せば、循環を回避せず、根本から解消できます。

## 詳細設計

作業はクラスタごとに MECE です。各段階は、個別のフォローアップ PR として着地します。各段階は
`make lint-imports`（BE-0112 のゲート）で独立に検証でき、他の段階が先に着地することには依存しません。

どの段階でも、`__init__.py` による re-export を通じて、公開の import パスを保ちます。これは
`bajutsu/report/__init__.py` が確立したパターン（BE-0043）に倣います。呼び出し側は、
`bajutsu.codegen.foo` などを変更なしに import し続けられます。変わるのは、内部のモジュール配置だけです。
すべての段階は挙動を変えません。変わるのはモジュールの位置と import 文だけで、実行時のロジックには
手を入れません。

1. **`bajutsu/codegen/` パッケージ。** 構成は次のとおりです。
   - `codegen/__init__.py`（現在の公開 API を re-export）
   - `codegen/common.py`（旧 `codegen_common.py`）
   - `codegen/emit.py`（旧 `codegen_emit.py`）
   - `codegen/playwright.py`（旧 `codegen_playwright.py`）
   - `codegen/uiautomator.py`（旧 `codegen_uiautomator.py`）
2. **`bajutsu/crawl/` パッケージ。** `crawl/__init__.py` に加えて、`crawl/flows.py`、`crawl/guide.py`、
   `crawl/report.py`、`crawl/repro.py`、`crawl/tabs.py` を置きます。
   - 約150行のシリアライズ関連のまとまりを、`crawl/serialize.py` として切り出します。対象は
     `action_to_dict`、`action_from_dict`、`screenmap_from_dict`、`screenmap_dict` です（現在は
     `crawl.py:1106-1218`）。`Action` と `ScreenMap` の辞書への変換と復元は、crawl の巡回処理とは
     独立した、自己完結した関心事です。
   - crawl の調整役は、すでに
     [BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction-ja.md)
     がクラスへ切り出し済みです。その切り出しは、この段階の対象外です。この段階は、残りのモジュールの
     ファイル配置を変えるだけです。
3. **`bajutsu/github/` パッケージ。** 構成は次のとおりです。
   - `github/__init__.py`
   - `github/actions.py`（旧 `github.py`。`bajutsu run` の Actions annotation 連携）
   - `github/app.py`（旧 `github_app.py`。GitHub App のインストールトークン取得の経路）

   `GitHubAccessError` を `github/__init__.py`（または小さな `github/errors.py`）へ移します。
   `config_source.py` は、そこから import します。`github_app` が `config_source` から import する
   向きをやめることで、循環を直接解消します。エラー型は、循環を避けるために App の経路を遅延 import
   していたモジュールには、もう存在しなくなります。
4. **`bajutsu/agents/` の周辺パッケージ。** 構成は次のとおりです。
   - `agents/__init__.py`
   - `agents/base.py`（旧 `agent.py`）
   - `agents/registry.py`（旧 `agents.py`）
   - `agents/claude.py`（旧 `claude_agent.py`）
   - `agents/claude_enrich.py`（旧 `claude_enrich_agent.py`）
   - `agents/claude_triage.py`（旧 `claude_triage.py`）
   - `agents/anthropic_client.py`（旧 `anthropic_client.py`）
   - `agents/availability.py`（旧 `ai_availability.py`）
   - `agents/enrich.py`（旧 `enrich.py`）
   - `agents/alerts.py`（旧 `alerts.py`）

   9個のモジュールからなる、最大のクラスタです。BE-0112 の forbidden-module 一覧が、今日いちばん長い
   箇所でもあります。
5. **`bajutsu/evidence/` と `bajutsu/analysis/` のパッケージ。** 残る evidence 関連のフラットな
   モジュールを、役割ごとに2つのパッケージへ分けます。
   - `evidence/` には、現在の `bajutsu/evidence.py` を `evidence/core.py` へ移して
     `evidence/__init__.py` から re-export したものを置きます。加えて、`evidence/intervals.py`、
     `evidence/network.py`、`evidence/visual.py`、`evidence/golden.py`、`evidence/redaction.py` を
     置きます。evidence の取得と redaction を扱い、いずれも BE-0112 の決定的コアに属します。
   - `analysis/` には、`analysis/__init__.py` に加えて、`analysis/coverage.py`、`analysis/audit.py`、
     `analysis/stats.py` を置きます。実行後の分析を扱い、report に近い性質を持ちます。

   1つの大きなパッケージにまとめず2つに分けるほうが、BE-0112 のレイヤ区分に合います。`evidence` は
   コアです。`coverage`、`audit`、`stats` は、実行結果の消費者であり、verdict を導く処理の一部では
   ありません。
6. **`bajutsu/analytics/` パッケージ。** `analytics/__init__.py` に加えて、`analytics/usage.py`
   （旧 `usage.py`）、`analytics/ledger.py`（旧 `usage_ledger.py`）、`analytics/stats.py`
   （旧 `usage_stats.py`）を置きます。トークンとコストの集計パイプラインで、現状すでに共通の
   `usage_` 接頭辞を持つ、まとまりのよい3つのモジュールです。

モジュール一覧の契約メンバーを減らす段階ごとに、対応する `pyproject.toml`（`pyproject.toml:216-316`）の
`[[tool.importlinter.contracts]]` は、1つのパッケージ名（たとえば `bajutsu.agents`）を名指しできる
ようになります。かつてのトップレベルモジュールを、個別に列挙する必要はなくなります。パッケージ化が
段階的に進むにつれて、手作業の一覧も段階的に縮みます。

## 検討した代替案

- **フラットな配置のまま、import-linter の契約だけに頼る。** 却下します。契約が冗長なのは（3つの
  ブロックにまたがって40近いモジュールを手作業で列挙しています）、配置に名指しできるパッケージの境界が
  ないからです。パッケージ化すれば、契約は直接的に短くなります。この代替案では、今後のすべての
  コントリビュータが、どのフラットなモジュールがどのレイヤに属するかという対応表を、頭の中に持ち続ける
  ことになります。
- **6つのクラスタすべてを1つの PR でまとめて再編する。** 却下します。6つのクラスタは互いに独立して
  います。それぞれが十分な規模を持ちます（crawl のクラスタだけでも2,200行を超えます）。1つにまとめた
  PR は、レビューが難しく、1つの段階だけをやり直す場合の取り消しも難しくなります。段階に分ければ、
  それぞれの移動を小さく保てます。ゲートで独立に検証でき、独立に取り消せます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `bajutsu/codegen/` パッケージ（`__init__` / `common` / `emit` / `playwright` / `uiautomator`）。
- [x] `bajutsu/crawl/` パッケージ（`__init__` / `core` / `flows` / `guide` / `report` / `repro` /
  `tabs` / `serialize`）。
- [x] `bajutsu/github/` パッケージ（`__init__` / `actions` / `app` / `errors`）。`config_source`
  と `github_app` の循環を解消します。
- [x] `bajutsu/agents/` の周辺パッケージ（11個のモジュール）。
- [x] `bajutsu/evidence/` と `bajutsu/analysis/` のパッケージ。
- [x] `bajutsu/analytics/` パッケージ（`usage` / `ledger` / `stats`）。

**ログ**

- 2026-07-14（[#1045](https://github.com/bajutsu-e2e/bajutsu/pull/1045)）: ステージ 1。フラットな
  `codegen*.py` を `bajutsu/codegen/` パッケージ（`xcuitest` / `common` / `emit` / `playwright` /
  `uiautomator` と re-export の `__init__`）へ移動しました。あわせて import-linter の codegen 3 件を
  `bajutsu.codegen` 1 件へ集約し、`Makefile` と `e2e_changes.py`、e2e ワークフローのパスフィルタ、
  そして日英のドキュメントをパッケージへ向け直しました。
- 2026-07-14（[#1052](https://github.com/bajutsu-e2e/bajutsu/pull/1052)）: ステージ 2。フラットな
  `crawl*.py` を `bajutsu/crawl/` パッケージ（`core` エンジンと `flows` / `guide` / `report` /
  `repro` / `tabs`、re-export の `__init__`）へ移動し、`Action` / `ScreenMap` のシリアライズ関連の
  まとまりを `crawl/serialize.py` として切り出しました。あわせて import-linter の
  `bajutsu.crawl_guide` を `bajutsu.crawl.guide` へ、`Makefile` と `e2e_changes.py`、e2e ワークフローの
  パスフィルタを `crawl/core.py` と `crawl/serialize.py`、`crawl/__init__.py`（オンデバイス実行が
  import するエンジンと re-export。周辺のモジュールは対象外のまま）へ向け直し、日英のドキュメントも
  更新しました。
- 2026-07-17（[#1172](https://github.com/bajutsu-e2e/bajutsu/pull/1172)）: ステージ 3。フラットな
  `github.py` / `github_app.py` を `bajutsu/github/` パッケージ（`actions` の CI エミッタと `app` の
  App トークン経路、`errors`、re-export の `__init__`）へ移動し、`GitHubAccessError` を独立した
  `github/errors.py` へ移しました。これで `github.app` と `config_source` は互いを import せずに共有
  でき、`config_source` ↔ `github_app` の循環を根本から解消します。あわせて import-linter の
  `bajutsu.github` を `bajutsu.github.actions`（周辺の部分。`app` と `errors` は決定的コアからも安全に
  参照できます）へ向け直し、日英のドキュメントも更新しました。
- 2026-07-17（[#1185](https://github.com/bajutsu-e2e/bajutsu/pull/1185)）: ステージ 4〜6。1 本の PR に
  ステージごと 1 コミットの形でまとめて着地しました。ステージ 4 は、AI / エージェントの周辺 11
  モジュール（`agent_protocols` / `agent_factory` / `claude_agent` / `claude_backed_agent` /
  `claude_enrich_agent` / `claude_triage` / `ai_config` / `anthropic_client` / `ai_availability` /
  `enrich` / `alerts`）を `bajutsu/agents/`（`protocols` / `factory` / `claude` / `claude_backed` /
  `claude_enrich` / `claude_triage` / `ai_config` / `anthropic_client` / `availability` / `enrich` /
  `alerts`）へ移動し、import-linter の該当 11 件を `bajutsu.agents` 1 件へ集約しました。提案本文が
  挙げていた元の 9 モジュールの一覧は、提案執筆時点ですでに古いものでした。`agent.py` / `agents.py`
  は提案が書かれる前に `agent_protocols.py` / `agent_factory.py` へ改名済みで、`claude_backed_agent`
  と `ai_config` はその後にクラスタへ加わっていたためです。ステージ 5 は `evidence.py` と
  `intervals` / `network` / `visual` / `golden` / `redaction` を `bajutsu/evidence/`（`core` と
  周辺モジュール、re-export の `__init__`）へ、`coverage` / `audit` / `stats` を
  `bajutsu/analysis/`（re-export なし）へ分割しました。`network.py` を `evidence/` の下に置いたことで、
  `drivers/base.py` がもともと持っていた `TYPE_CHECKING` 専用の `Collector` 参照が、「シナリオスキーマ
  と Driver Protocol の可搬なインナー契約」契約の `bajutsu.evidence` 全体を禁止するエントリに
  引っかかるようになりました。名前空間全体を開放するのではなく、その 1 本の import だけを許す
  `ignore_imports` の例外を 1 件加えて解決しています。ステージ 6 は `usage` / `usage_ledger` /
  `usage_stats` を `bajutsu/analytics/`（`usage` / `ledger` / `stats`）へ移動しました。3
  モジュールで完結する最小のクラスタで、import-linter や E2E トリガーの変更は不要でした。各ステージ
  とも、`Makefile` / `e2e_changes.py` / 日英ドキュメントを新しいパッケージへ向け直し、ステージ
  1〜3 と同じ扱いにしています。これで本項目の進捗チェックリストが完了し、`状態` を実装済みへ
  切り替えます。

## 参考

- `pyproject.toml:216-316` — 3つの `[tool.importlinter.contracts]` ブロック（BE-0112）。
  `source_modules` / `forbidden_modules` が40近い個別のモジュール名を手作業で列挙しています。
- `docs/architecture.md` — 決定的コア・契約・周辺のレイヤを文章で説明している箇所です。
  パッケージ化された配置であれば、これをディレクトリツリーから直接読み取れるようになります。
- `bajutsu/codegen.py`、`bajutsu/codegen_common.py`、`bajutsu/codegen_emit.py`、
  `bajutsu/codegen_playwright.py`、`bajutsu/codegen_uiautomator.py` — codegen のクラスタ
  （合計1,495行）。
- `bajutsu/crawl.py:1106-1218` — `crawl/serialize.py` への切り出しを提案している
  `action_to_dict` / `action_from_dict` / `screenmap_from_dict` / `screenmap_dict` のシリアライズ
  関連のまとまりです。
- `bajutsu/config_source.py:178`（`GitHubAccessError` の定義）、`bajutsu/config_source.py:274`
  （遅延 import している `from bajutsu.github_app import installation_token`）、
  `bajutsu/github_app.py:24`（`from bajutsu.config_source import GitHubAccessError`）— `config_source`
  と `github_app` のあいだに実在する循環であり、現在は遅延 import によって回避されているだけです。
- `bajutsu/mailbox.py` と `bajutsu/runner/mailbox.py`、`bajutsu/object_store.py` と
  `bajutsu/serve/server/object_store.py`、`bajutsu/handoff.py` と `bajutsu/cli/handoff.py` —
  パッケージ化された配置がディレクトリによって解消する、レイヤをまたいだ3組の名前の衝突です。
- `bajutsu/report/__init__.py` — 本提案の段階的な移動が公開の import パスを保つために倣っている、
  既存の re-export パターン（BE-0043）です。
- [BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement-ja.md) —
  本提案がディレクトリツリー上に可視化するレイヤモデルと import-linter のゲートです。
- [BE-0135](../BE-0135-module-naming-debt/BE-0135-module-naming-debt-ja.md) — トップレベル
  モジュールの命名を整理した先行項目であり、本提案はそれをパッケージの水準で引き継ぎます。
- [BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction-ja.md) —
  crawl の調整役はすでにクラスへ切り出し済みです。本提案の crawl パッケージ化の段階は、この切り出し
  ではなく、残りのファイル配置だけを対象にします。
- AI やエージェントに関連するモジュールの命名や分類を整理する姉妹提案が別途検討されていますが、ここでは
  番号を挙げずに言及するにとどめます。
