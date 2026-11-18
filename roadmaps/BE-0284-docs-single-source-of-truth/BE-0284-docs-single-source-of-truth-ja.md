[English](BE-0284-docs-single-source-of-truth.md) · **日本語**

# BE-0284 — 重複するドキュメント規範を1つの基準ファイルへ集約する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0284](BE-0284-docs-single-source-of-truth-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0284") |
| 実装 PR | [#1169](https://github.com/bajutsu-e2e/bajutsu/pull/1169) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

Bajutsu のドキュメントでは、複数のファイルが同じ横断的な規範を独立に再記述しています。本項目は、規範ごとに基準ファイル（single source of truth）を1つ定め、それ以外の記述を基準ファイルへの短いリンクに置き換えます。現状では、規範を変更するたびに、すべての再記述を手作業で探して直さなければなりません。本項目の後は、基準ファイルを1箇所直せば済みます。

## 動機

調査の対象は、`CLAUDE.md`、`docs/ai-development.md`、`CONTRIBUTING.md`、`AGENTS.md`、`roadmaps/README.md`、`.github/PULL_REQUEST_TEMPLATE.md`、`.github/claude-review-prompt.md` です。同じ規範が複数のファイルに、逐語またはパラフレーズで再記述されており、基準ファイルへのリンクはありませんでした。そのうち2つはすでに矛盾するまで乖離しており、この構造のコストを具体的に示しています。

- `CONTRIBUTING.md`、`AGENTS.md`、`.github/PULL_REQUEST_TEMPLATE.md` は、ロードマップ項目が `roadmaps/proposals/`、`implemented/`、`in-progress/`、`deferred/` に振り分けられると、いまも記述しています。[BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders-ja.md) はこのフォルダ分割を廃止しました。現行の構成は、項目ごとに1つのフラットな `roadmaps/BE-NNNN-<slug>/` を置き、`Status` はインデックスの分類にだけ使うものです。`docs/ai-development.md` と `CLAUDE.md` は、この構成を正しく記述しています。古い記述を先に読んだコントリビューターは、CI が拒否するディレクトリ構成を作ってしまいます。
- `CLAUDE.md`、`docs/ai-development.md`、`CONTRIBUTING.md`、`AGENTS.md`、`docs/contributor-workflow-tutorial.md` は、`make check` が実行するステップを、それぞれ異なる部分集合で列挙しています。どの列挙も、実際の11ステップと一致しません。短い列挙を信じた読み手は、ゲートの検証範囲を過小評価します。

同じ構造は、まだ乖離していない箇所にも繰り返されています。

- `roadmaps/README.md` は、ロードマップの BE-ID 規約（プレースホルダー `BE-0284`、マージ時の割り当て、両言語ファイル、インデックスの再生成）を、`docs/ai-development.md` の基準となる節へリンクせず、全文で再記述しています。
- `AGENTS.md` は、PR のタイトル、本文、Draft の規約を、`docs/ai-development.md` へ向けず、まるごと再説明しています。
- `.github/claude-review-prompt.md` は、6つの規範をリンクなしで凝縮して再記述しています。prime directives、docstring のスタイル（[BE-0065](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference-ja.md)）、バイリンガルドキュメント、日本語の文章スタイル、ロードマップへのリンク規約、コメントは「なぜ」を書く規約です。このファイルが、本項目のきっかけとなった事例です。

再記述はどれも、将来の修正が見落としうるもう1箇所になります。

このリスクはプロダクトの振る舞いではなく、プロジェクトの運用にあります。それでも、本ロードマップがすでに扱っているコントリビューター向け基盤整備の範囲に収まります。[BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md) は、手順書をコマンドに変換しました。[BE-0278](../BE-0278-tech-writing-skill/BE-0278-tech-writing-skill-ja.md) は、両言語に単一の文章スタイルの権威を置きました。本項目は、この系譜を「文体」から「規範をどのファイルに置くか」へ広げます。

## 詳細設計

規範のクラスタごとに、もっとも詳しく最新の内容を保っているファイルを基準ファイルと定めます。それ以外の記述は、短いリンクか、乖離しうる独自の詳細を持たない要約に置き換えます。

| 規範 | 基準ファイル | ポインタに縮小するファイル |
|---|---|---|
| ロードマップのディレクトリ構成と BE-ID の割り当て | `docs/ai-development.md` | `roadmaps/README.md`、`CONTRIBUTING.md`、`AGENTS.md`、`.github/PULL_REQUEST_TEMPLATE.md` |
| PR タイトル・本文・Draft の規約 | `docs/ai-development.md` | `AGENTS.md`、`CONTRIBUTING.md` |
| `make check` の実行内容一覧 | `CLAUDE.md`（すでにもっとも詳しい） | `docs/ai-development.md`、`CONTRIBUTING.md`、`AGENTS.md`、`docs/contributor-workflow-tutorial.md` |
| docstring のスタイル（BE-0065） | `docs/ai-development.md` | `.github/claude-review-prompt.md` |
| バイリンガルドキュメントの運用（どのファイルをいつ） | `docs/ai-development.md` | `.github/claude-review-prompt.md`、`AGENTS.md` |
| 日本語の文章スタイル（敬体、造語を使わない） | `japanese-document-writing` スキル（BE-0278 のとおり） | `.github/claude-review-prompt.md`、`AGENTS.md` |
| Prime directives | `CLAUDE.md`（維持。後述） | — |

作業は MECE（互いに重複せず、漏れもない分割）に、次のとおり分解します。

1. **まず矛盾している箇所を直します。** `CONTRIBUTING.md`、`AGENTS.md`、`.github/PULL_REQUEST_TEMPLATE.md` を、BE-0159 を引用してフラットな `roadmaps/BE-NNNN-<slug>/` 構成の記述に更新します。続いて、食い違う5箇所の `make check` の列挙を、`CLAUDE.md` が挙げる11ステップに合わせます。
2. **`.github/claude-review-prompt.md` の6つの再記述をリンクに畳み込みます。** それぞれの規範を、`CLAUDE.md` または `docs/ai-development.md` の基準となる節へ向けます。文章スタイルは `japanese-document-writing` スキルへ向けます。規約そのものは再説明しません。
3. **`roadmaps/README.md` の独自の BE-ID 規約の再記述を、リンクに置き換えます。** リンク先は `docs/ai-development.md` の基準となる節です。インデックス表にたどり着くまでに読者が必要とする1、2文だけを残します。
4. **`AGENTS.md` と `CONTRIBUTING.md` にある PR 規約とロードマップ規約のフルの記述を縮小します。** ポインタに加えて、それぞれの文書の読者に固有の詳細だけを残します。
5. **「再記述せずリンクする」という一文を、`docs/ai-development.md` 自体へ追記します。** 既存のドキュメント規約と並べて置きます。新しい規範を後から追加するコントリビューターは、最初から1箇所にまとめるようになります。

**Prime directives は意図的な例外です。** 短く、行き渡ることを前提とした重要な規範です。`CLAUDE.md` 自体が、PR 規約の節に置いた再記述を、より詳しい規範の「短縮形」だと明記しています。本項目は、短く正確な prime directives の再記述をすべて維持します。取り除くのは、乖離したものと、規範そのものを超える詳細を再現しているものだけです。

この整理は CI のチェックではなく、レビュー時の規範のままとします。ある段落が規範を「再記述」しているのか、行き渡らせるべき規範を「正当に繰り返している」のかを判定するには、意味論的な判断が要ります。バイリンガルドキュメントの規範や、[BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md) の DESIGN.md 整合性規範にも、同じ判断が要ります。prime directive 1 は、この種の判断を決定的なゲートから締め出しています。

## 検討した代替案

- **プローズの断片をハッシュ化または差分比較し、乖離を CI で落とすスクリプト。** 却下しました。言い回しの異なる2つの段落が同じ規範を述べていると判定するには、意味論的な判断が必要です。その判断は `run` と CI の合否判定に大規模言語モデル（LLM）を持ち込むことになり、prime directive 1 が禁じています。LLM を使わなければ、意図的に短くした再記述、特に prime directives のすべてを誤検知します。
- **すべての再記述を削除し、例外なくすべての文書にリンクさせる。** 短く行き渡らせるべき規範については却下しました。`CLAUDE.md` はすでに「省略なし」の規範を述べており、文書は初読で自己完結していなければなりません。そうした文書は、prime directives の短く正確な写しを正当に持ち続けます。3文程度のために、初読の読者を他の文書へ送るべきではありません。
- **すべての規範を統合する新しいトップレベルの `conventions.md` を1つ作る。** 却下しました。AI 開発プロセスの規範の基準ファイルは、すでに `docs/ai-development.md` です。prime directives とクイックリファレンスとしてのゲートの基準ファイルは、`CLAUDE.md` です。3つ目のトップレベル文書を作ると、規範は統合されず、さらに分散します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `CONTRIBUTING.md`、`AGENTS.md`、`.github/PULL_REQUEST_TEMPLATE.md` の古いロードマップ
      ディレクトリ構成の記述を修正する。BE-0159 のフラット構成に合わせる。
- [x] 5箇所で食い違っている `make check` の実行内容一覧を1つに統一し、それ以外はリンクにする。
- [x] `.github/claude-review-prompt.md` の6つの再記述を、それぞれの基準ファイルへのリンクに畳み込む。
- [x] `roadmaps/README.md` の独自の BE-ID 規約再記述を `docs/ai-development.md` へのリンクに
      置き換える。
- [x] `AGENTS.md` と `CONTRIBUTING.md` の PR 規約・ロードマップ規約のフルの記述をポインタに
      縮小する。
- [x] `docs/ai-development.md` のドキュメント規約に「再記述せずリンクする」という一文を追加する。

### ログ

- 規範を一括で集約しました（PR #1169）。
  - `CONTRIBUTING.md`、`AGENTS.md`、`.github/PULL_REQUEST_TEMPLATE.md` の記述を、フラット構成へ修正しました。
  - `docs/ai-development.md`、`CONTRIBUTING.md`、`AGENTS.md`、`docs/contributor-workflow-tutorial.md` にある `make check` の記述を、`CLAUDE.md` へのリンクに統一しました。
  - `.github/claude-review-prompt.md` の各規範を、基準ファイルへリンクしました。
  - `roadmaps/README.md` の BE-ID 再記述を、ポインタへ縮小しました。
  - 「再記述せずリンクする」規範を、`docs/ai-development.md` へ追加しました。
  - 日本語のミラーも同時に更新しました。

## 参考

- [BE-0159 — ロードマップディレクトリのフラット化（状態別フォルダの廃止）](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders-ja.md)
- [BE-0278 — tech-writing スキル](../BE-0278-tech-writing-skill/BE-0278-tech-writing-skill-ja.md)
- [BE-0113 — DESIGN.md の整合性再確立](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md)
- [BE-0069 — 実行可能なコントリビューター向けガードレール](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md)
- [BE-0065 — docstring 標準・API リファレンス](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference-ja.md)
