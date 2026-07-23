[English](BE-0113-design-doc-realignment.md) · **日本語**

# BE-0113 — DESIGN.md を現状の実装に合わせる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0113](BE-0113-design-doc-realignment-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0113") |
| 実装 PR | [#565](https://github.com/bajutsu-e2e/bajutsu/pull/565) |
| トピック | コントリビューターワークフロー |
| 関連 | [BE-0010](../BE-0010-update-scope-statement/BE-0010-update-scope-statement-ja.md), [BE-0027](../BE-0027-mock-server-external/BE-0027-mock-server-external-ja.md), [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`DESIGN.md` は冒頭で自らを「設計確定版」と名乗りますが、その記述のいくつかは実装に遅れています。冒頭の
位置づけを**設計判断とその理由の記録**に改め、現状の実装状態については `docs/ja/architecture.md` を見るよう
読者を導いたうえで、後述の 3 点の乖離を修正または注記し、同じ種類のずれが黙って再発しないよう運用規範を
加えます。これは文書の変更です。追跡する項目に値するのは、`DESIGN.md` が基盤となる文書であり（さらに
`pyproject.toml` の `readme = "DESIGN.md"` を通じて、パッケージが公開する説明文でもあり）、その正確さの
及ぶ範囲がリポジトリの外にも広がるからです。

## 動機

`docs/ja/architecture.md` が実装状態の source of truth であり、この役割分担は意図されたものです。しかし
`DESIGN.md` 自身がそう述べていません。現状について architecture.md を見よと導かないまま設計確定版として
振る舞うため、両者が食い違ったとき、読者はどちらを信じるべきかを文書からは判断できません。`main` に対して
確認できた乖離は 3 点です。

1. **ネットワーク証跡の取得元（§3.2、§9）。** DESIGN.md は、単一の外部**モックサーバ**を、ネットワークを
   モックする機構と `network` 証跡源の両方として説明します。この外部モックサーバは棚上げされ
   （[BE-0027](../BE-0027-mock-server-external/BE-0027-mock-server-external-ja.md)）、実装は
   シナリオ内の `mocks`（in-protocol stubs）へ移りました。
2. **モジュール構成（§4）。** 構成図はフラットなファイル配置（`orchestrator.py`、`scenario.py` など）の
   ままで、現行のパッケージ構造（`serve/`、`crawl`、`mcp/` ほか、約 30,000 行）とは一致しません。
3. **backend の状態（§3）。** 図は XCUITest backend を「(将来)」と記していますが、
   [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) は進行中です。

いずれも設計の誤りではなく、古びです。直しは、DESIGN.md に自らの役割を正直に述べさせ、3 点を整合させる
ことであって、何かを設計し直すことではありません。ただし、挙動を変えるコントリビュータに DESIGN.md や
`docs/ja/architecture.md` も合わせて直すよう促すものは今のところなく、この一巡を終えても同じ 3 種類の
ずれは再発しやすいままです。直しには、一度きりの修正だけでなく、再発を防ぐ規範も含めるべきです。

## 詳細設計

作業は次の 6 つの作業項目に MECE に分解できます。後半の「機械的に検査できる成果」と
「プライムディレクティブとの整合」は成果条件の整理であり、追加の作業項目ではありません。
`DESIGN.md` は日本語で書かれているため、すべての編集は [`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/SKILL.md) スキルに従います。

### 1. 冒頭の位置づけを改める

冒頭のステータス行を変え、DESIGN.md が設計判断とその理由を記録するものであること、現状の実装状態に
ついては `docs/ja/architecture.md` が source of truth であることを、はっきり述べます。「どちらの文書を
信じればよいか」という曖昧さを、冒頭で一度に解消します。

### 2. ネットワーク証跡の取得元を整合させる（§3.2、§9）

外部モックサーバの説明を、実際に入ったシナリオ内 `mocks`（in-protocol stubs）を反映するよう修正または
注記し、外部サーバが棚上げされた理由として BE-0027 を挙げます。

### 3. モジュール構成を整合させる（§4）

構成図を現行のパッケージ配置に更新するか、配置は例示であって権威ある現行の構造は
`docs/ja/architecture.md` が持つ、という注記に置き換えます。DESIGN.md を、また古びる構造のスナップショット
ではなく、判断の記録として保つほうを選びます。

### 4. backend の状態を整合させる（§3）

XCUITest backend の「(将来)」という表示を、BE-0019 が進行中であることを反映するよう更新し、それを
挙げます。

### 5. さらなる乖離を掃く

上記の編集のついでに、ほかに遅れている記述がないかを範囲を限って一巡し、同じ変更のなかで修正または
注記します。全面的な書き直しには広げません。

### 6. 再発を防ぐ規範を加える

[`CLAUDE.md`](../../CLAUDE.md) の Conventions に、`docs/` と `docs/ja/` を挙動変更時に両方更新すると
いう既存の規範と並べて、DESIGN.md または `docs/ja/architecture.md` が述べる挙動を変える PR は、同じ変更の
なかで該当する文書も更新しなければならない、という一文を加えます。これは CI のゲートにはできません。
ある差分が、ある段落の記述する挙動を変えているかどうかの判定にはコードと散文を突き合わせる意味理解が要り、
それは `run` や CI の verdict 経路に LLM を置くことになってしまうからです（プライムディレクティブ 1）。
ほかの既存の規範と同様、レビュー時の規範として置きます。

### 機械的に検査できる成果

本項目は文書であり、挙動の表明は持ちません。そのゲートは、`make check` にすでにあるドキュメントと
ロードマップのリンク整合と書式の検査
（[BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity-ja.md)）です。
編集後も `make check` は緑のままで、両言語のリンクは保たれます。文章の正しさはレビューの判断であって、
機械の verdict ではありません。テストに見せかけず、そのまま述べます。

### プライムディレクティブとの整合

文書のみです。コードも LLM もなく、`run` や CI の verdict 経路には何も置きません。ディレクティブに
ついての古い説明（棚上げされた外部モックサーバ）を取り除くことで、決定性と証跡が今どう働くかについて
読者を誤らせる余地を減らし、間接的にディレクティブを強めます。

## 検討した代替案

- **DESIGN.md をそのままにし、architecture.md に頼る。** 却下します。無条件の「設計確定版」という主張は
  実際に読者を誤らせます。ネットワーク / 構成 / backend の記述が古いという合図を、文書からは受け取れない
  からです。乖離の代償は、新しい読者のたびに支払われます。
- **DESIGN.md を削り、すべてを architecture.md に畳み込む。** 却下します。DESIGN.md の価値は**理由**、
  すなわち設計判断とその根拠にあり、状態を記す architecture.md はそれを持ちません。両方を残し、DESIGN.md
  の役割を明示します。
- **DESIGN.md を全面的に書き直す。** 範囲過大として却下します。冒頭の位置づけの改めと 3 点の整合で足り、
  書き直しは、文書の本当の値打ちである判断の履歴を捨てる危険があります。
- **規範ではなく、CI で古びを自動検出する。** 却下します。DESIGN.md の記述が実装と一致し続けているかの
  確認にはアサーションでは表せない意味理解が要り、それは `run` や CI の verdict 経路に LLM を置くことに
  なります。これはまさにプライムディレクティブ 1 が禁じることです。レビュー時の規範が、この安全策として
  実現可能な形です。
- **規範を加えず、3 点の乖離だけを直す。** 却下します。挙動を変える将来のコントリビュータに DESIGN.md や
  architecture.md も合わせて直すよう促すものがなければ、同じ種類のずれは再発します。それでは本 PR は、
  くり返される一度きりの修正の 1 つで終わってしまいます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 冒頭の位置づけを改める（設計判断の記録とし、状態は architecture.md を指す）
- [x] ネットワーク証跡の取得元（§3.2、§9）をシナリオ内 `mocks` に整合させ、BE-0027 を挙げる
- [x] モジュール構成（§4）を現行のパッケージ構造に整合させる（または architecture.md が権威と注記する）
- [x] backend の状態（§3）を整合させる。XCUITest は進行中（BE-0019）
- [x] さらなる乖離を範囲を限って掃き、同じ変更のなかで修正 / 注記する
- [x] 挙動変更時に DESIGN.md / architecture.md の更新を求める規範を CLAUDE.md に加える

## 参考

- [DESIGN.md](../../DESIGN.md)（本項目が合わせ直す文書。`pyproject.toml` の `readme = "DESIGN.md"` を通じて、パッケージが公開する説明文でもあります）
- [architecture.md](../../docs/ja/architecture.md)（DESIGN.md が委ねるべき、実装状態の source of truth）
- [CLAUDE.md](../../CLAUDE.md)（既存の bilingual ドキュメント規範と並べて、再発防止の規範を加える先）
- [BE-0027](../BE-0027-mock-server-external/BE-0027-mock-server-external-ja.md)（外部モックサーバが棚上げされ、シナリオ内 `mocks` に置き換わった理由）
- [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)（「将来」ではなくいま進行中の XCUITest backend）
- [BE-0010](../BE-0010-update-scope-statement/BE-0010-update-scope-statement-ja.md)（前例：スコープ記述を現実に合わせ直した文書項目）
- [BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity-ja.md)（本変更が緑に保つべき、ドキュメントとリンク整合のゲート）
