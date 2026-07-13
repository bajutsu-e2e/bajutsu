[English](BE-XXXX-layer-package-topology.md) · **日本語**

# BE-XXXX — 強制されているアーキテクチャレイヤをディレクトリとしてパッケージ化する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-layer-package-topology-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | コードベース品質・技術的負債 |
| 関連 | [BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement-ja.md)、[BE-0135](../BE-0135-module-naming-debt/BE-0135-module-naming-debt-ja.md)、[BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/` 直下には約71個のトップレベルモジュールがフラットな名前空間に並んでいます。しかしコード
ベースには、[BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement-ja.md)
によってすでにゲートで強制されているレイヤリング、決定的コア・契約・周辺の区分があります。レイヤの
所属はディレクトリツリーからは見えません。`pyproject.toml` の `[tool.importlinter]` 契約に手作業で
維持されたモジュール一覧と、`docs/architecture.md` の文章としてのみ存在しています。本提案は、すでに
一つのまとまりとして振る舞っているモジュール群を package へとグループ化し、モジュールのレイヤ（と
そのクラスタ）がパスから読み取れるようにします。import-linter の契約も、手作業での列挙ではなく
一つの package 名で済むようになります。

## 動機

モジュールのアーキテクチャレイヤは、それが置かれている場所から見えるべきです。しかし現状はそう
なっていません。読み手（および lint の設定）は、レイヤの対応関係を頭の中で持ち歩き、
`docs/architecture.md` とフラットなファイル一覧を突き合わせる必要があります。`pyproject.toml:216-316`
はその帰結をそのまま示しています。3つの `[tool.importlinter.contracts]` ブロックが、レイヤに
package の境界がなく名指しできないというただそれだけの理由で、40近いモジュール名を手作業で列挙して
います。

いくつかの密なクラスタは、ディレクトリ構造を除けばすでに package そのものとして振る舞っています。

- **codegen**：`codegen.py`（401行）に加えて `codegen_common.py`（99行）、`codegen_emit.py`
  （55行）、`codegen_playwright.py`（521行）、`codegen_uiautomator.py`（419行）です。バックエンド
  ごとの emitter を持つ一つの生成器であり、合計1,495行、すでに共通の `codegen_` という接頭辞で
  名付けられています。
- **crawl**：`crawl.py`（1,256行）に加えて `crawl_flows.py`、`crawl_guide.py`、`crawl_report.py`、
  `crawl_repro.py`、`crawl_tabs.py` です。周辺の中で最大の module であり、すでに `crawl_` という
  接頭辞で flow、guide、report、repro、tab の関心事に分かれています。
- **AI やエージェントの周辺**：`agent.py`、`agents.py`、`claude_agent.py`、`claude_enrich_agent.py`、
  `claude_triage.py`、`anthropic_client.py`、`ai_availability.py`、`enrich.py`、`alerts.py` の
  9個の module です。BE-0112 の契約上はいずれも周辺レイヤの AI やエージェント関連ですが、現状は
  共通の接頭辞を持たないままフラットな名前空間に散らばっています。

レイヤをまたいだ名前の衝突が3組あります。どちらが解決されるかは、どちらの import パスが解決される
かを知っていて初めてわかる状態です。`bajutsu/mailbox.py` と `bajutsu/runner/mailbox.py`、
`bajutsu/object_store.py` と `bajutsu/serve/server/object_store.py`、`bajutsu/handoff.py` と
`bajutsu/cli/handoff.py` です。トップレベルの module を package 化すれば、それぞれの組が自己文書化
された別々のパスに解決されます。

実在する import の循環も1つあります。`bajutsu/config_source.py` は `GitHubAccessError` を定義し
（`config_source.py:178`）、`github_app.py` との循環を避けるためだけに、`bajutsu.github_app` を
関数本体の中で遅延 import しています（`config_source.py:274`）。一方 `github_app.py` はモジュール
先頭で `config_source` から `GitHubAccessError` を import しています（`github_app.py:24`）。この
遅延 import はレイヤ構造の問題を回避しているだけで、解決してはいません。共有されているエラー型を
`github/` package へ移すことで、循環そのものを根本から解消できます。

## 詳細設計

作業はクラスタごとに MECE です。各段階は個別のフォローアップ PR として着地し、
`make lint-imports`（BE-0112 のゲート）で独立に検証できます。どの段階も、他の段階が先に着地する
ことに依存しません。どの段階でも、`__init__.py` による re-export を通じて公開の import パスを保ち
ます。これは `bajutsu/report/__init__.py` ですでに確立されているパターン（BE-0043）に倣うもので、
呼び出し側は `bajutsu.codegen.foo` などを変更なしに import し続けられ、変わるのは内部の module
配置だけです。すべての段階は挙動を変えません。変わるのは module の位置と import 文だけで、実行時の
ロジックには手を入れません。

1. **`bajutsu/codegen/` package**：`codegen/__init__.py`（現在の公開 API を re-export）に加えて
   `codegen/common.py`（旧 `codegen_common.py`）、`codegen/emit.py`（旧 `codegen_emit.py`）、
   `codegen/playwright.py`（旧 `codegen_playwright.py`）、`codegen/uiautomator.py`
   （旧 `codegen_uiautomator.py`）です。
2. **`bajutsu/crawl/` package**：`crawl/__init__.py` に加えて `crawl/flows.py`、`crawl/guide.py`、
   `crawl/report.py`、`crawl/repro.py`、`crawl/tabs.py` です。あわせて、`action_to_dict` /
   `action_from_dict` / `screenmap_from_dict` / `screenmap_dict`（現在は `crawl.py:1106-1218`）
   というシリアライズ関連のまとまり、約150行を `crawl/serialize.py` として切り出します。
   `Action` / `ScreenMap` を辞書に変換する処理とそこから復元する処理は、crawl の巡回処理そのものとは
   独立した関心事だからです。なお crawl の調整役はすでに
   [BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction-ja.md)
   によってクラスへ切り出し済みなので、この段階の対象には含みません。ここでの作業は、残りの
   module のファイル配置を変えるだけです。
3. **`bajutsu/github/` package**：`github/__init__.py`、`github/actions.py`（旧 `github.py`、
   `bajutsu run` の Actions annotation 連携）、`github/app.py`（旧 `github_app.py`、GitHub App
   のインストールトークン取得の経路）です。`GitHubAccessError` を `github/__init__.py`（または
   小さな `github/errors.py`）へ移し、`config_source.py` 側がそこから import するようにします。
   `github_app` が `config_source` から import する向きをやめることで、循環そのものを解消
   します。エラー型は、遅延 import を必要としていた側のモジュールにはもう存在しなくなります。
4. **`bajutsu/agents/` の周辺 package**：`agents/__init__.py` に加えて `agents/base.py`
   （旧 `agent.py`）、`agents/registry.py`（旧 `agents.py`）、`agents/claude.py`
   （旧 `claude_agent.py`）、`agents/claude_enrich.py`（旧 `claude_enrich_agent.py`）、
   `agents/claude_triage.py`（旧 `claude_triage.py`）、`agents/anthropic_client.py`
   （旧 `anthropic_client.py`）、`agents/availability.py`（旧 `ai_availability.py`）、
   `agents/enrich.py`（旧 `enrich.py`）、`agents/alerts.py`（旧 `alerts.py`）です。9個の module
   からなる最大のクラスタであり、BE-0112 の forbidden-module 一覧が今日いちばん長い箇所でも
   あります。
5. **`bajutsu/evidence/` と `bajutsu/analysis/` の package**：残る evidence 関連のフラットな
   module を役割ごとに2つの package へ分けます。`evidence/` には、現在の `bajutsu/evidence.py`
   を `evidence/core.py` へ移して `evidence/__init__.py` から re-export したものに加えて、
   `evidence/intervals.py`、`evidence/network.py`、`evidence/visual.py`、`evidence/golden.py`、
   `evidence/redaction.py` を置きます（evidence の取得と redaction であり、いずれも BE-0112 の
   決定的コアに属します）。`analysis/` には `analysis/__init__.py` に加えて `analysis/coverage.py`、
   `analysis/audit.py`、`analysis/stats.py` を置きます（実行結果に対する事後分析であり、report に
   近い性質を持ちます）。1つの大きな package にまとめず2つに分けるのは、BE-0112 のレイヤ区分に
   合わせるためです。`evidence` はコアであり、`coverage` / `audit` / `stats` は verdict を導く
   処理の一部ではなく、実行結果の消費者です。
6. **`bajutsu/analytics/` package**：`analytics/__init__.py` に加えて `analytics/usage.py`
   （旧 `usage.py`）、`analytics/ledger.py`（旧 `usage_ledger.py`）、`analytics/stats.py`
   （旧 `usage_stats.py`）です。トークンとコストの集計パイプラインであり、すでに共通の `usage_`
   という接頭辞を持つ、まとまりのよい3つの module です。

各段階が module 一覧の項目を1つ減らすたびに、対応する `pyproject.toml`（`pyproject.toml:216-316`）
の `[[tool.importlinter.contracts]]` は、かつてのトップレベル module を個別に列挙する代わりに、
1つの package 名（たとえば `bajutsu.agents`）だけを名指しできるようになります。package 化が段階
的に進むにつれて、手作業の一覧も段階的に縮んでいきます。

## 検討した代替案

- **フラットな配置のまま、import-linter の契約だけに頼る。** 却下します。契約が冗長なのは
  （3つのブロックにまたがって40近いモジュールを手作業で列挙している状態です）、まさに配置に
  package の境界がなく名指しできないからです。package 化はその契約を直接的に短くします。今後の
  すべてのコントリビュータに、どのフラットな module がどのレイヤに属するかという対応表を頭の中に
  持たせ続けるよりも合理的です。
- **6つのクラスタすべてを1つの PR でまとめて再編する。** 却下します。6つのクラスタは互いに独立
  しており、それぞれが十分な規模を持ちます（crawl のクラスタだけでも2,200行を超えます）。1つに
  まとめた PR ではレビューも、段階ごとの取り消しも難しくなります。段階に分けることで、それぞれの
  移動を小さく保ち、ゲートで独立に検証でき、独立に取り消せる状態を保てます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `bajutsu/codegen/` package（`__init__` / `common` / `emit` / `playwright` / `uiautomator`）。
- [ ] `bajutsu/crawl/` package（`__init__` / `flows` / `guide` / `report` / `repro` / `tabs` /
  `serialize`）。
- [ ] `bajutsu/github/` package（`__init__` / `actions` / `app`）。`config_source` と
  `github_app` の循環を解消します。
- [ ] `bajutsu/agents/` の周辺 package（9個の module）。
- [ ] `bajutsu/evidence/` と `bajutsu/analysis/` の package。
- [ ] `bajutsu/analytics/` package（`usage` / `ledger` / `stats`）。

## 参考

- `pyproject.toml:216-316` — 3つの `[tool.importlinter.contracts]` ブロック（BE-0112）。
  `source_modules` / `forbidden_modules` が40近い個別のモジュール名を手作業で列挙しています。
- `docs/architecture.md` — 決定的コア・契約・周辺のレイヤを文章で説明している箇所です。
  package 化された配置であれば、これをディレクトリツリーから直接読み取れるようになります。
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
  package 化された配置がディレクトリによって解消する、レイヤをまたいだ3組の名前の衝突です。
- `bajutsu/report/__init__.py` — 本提案の段階的な移動が公開の import パスを保つために倣っている、
  既存の re-export パターン（BE-0043）です。
- [BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement-ja.md) —
  本提案がディレクトリツリー上に可視化するレイヤモデルと import-linter のゲートです。
- [BE-0135](../BE-0135-module-naming-debt/BE-0135-module-naming-debt-ja.md) — トップレベル
  module の命名を整理した先行項目であり、本提案はそれを package の水準で引き継ぎます。
- [BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction-ja.md) —
  crawl の調整役はすでにクラスへ切り出し済みです。本提案の crawl package 化の段階は、この切り出し
  ではなく、残りのファイル配置だけを対象にします。
- AI やエージェントに関連する module の命名や分類を整理する姉妹提案が別途検討されていますが、ここでは
  番号を挙げずに言及するにとどめます。
