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

ある規範をどこに書くかを1つのファイルに決め、そのファイルを唯一の拠り所（single source of
truth）とします。以下ではこのファイルを「基準ファイル」と呼びます。本項目は、Bajutsu の
ドキュメントで複数箇所に独立して再記述されている横断的な規範ごとに基準ファイルを1つ定め、それ
以外の記述をそのファイルへの短いリンクに置き換えます。規範を変更するたびにすべての再記述を手作業で
探して直す現状を、基準ファイル1箇所の修正で済むようにします。

## 動機

`CLAUDE.md`、`docs/ai-development.md`、`CONTRIBUTING.md`、`AGENTS.md` を調査しました。あわせて
`roadmaps/README.md`、`.github/PULL_REQUEST_TEMPLATE.md` も調べました。
`.github/claude-review-prompt.md` も対象です。同じ規範が複数ファイルに再記述されています。
逐語的な場合もパラフレーズの場合もあります。基準ファイルへのリンクを
持たない箇所も見つかりました。そのうち2つはすでに矛盾へと乖離しています。この構造が抱える具体的な
コストを示す例です。

- `CONTRIBUTING.md`、`AGENTS.md`、`.github/PULL_REQUEST_TEMPLATE.md` は、ロードマップ項目の現在の
  置き場所を、古い記述のままにしています。いまも
  `roadmaps/proposals/` / `implemented/` / `in-progress/` / `deferred/` に振り分けられるものとして
  記述されています。
  [BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders-ja.md)
  がこのフォルダ分割を廃止しました。現行の正しい構成——項目ごとに1つのフラットな
  `roadmaps/BE-NNNN-<slug>/`、`Status` はインデックスの分類にのみ使う——は `docs/ai-development.md`
  と `CLAUDE.md` にあります。古い記述を先に読んだコントリビューターは、CI が拒否するディレクトリ
  構成を作ってしまいます。
- `CLAUDE.md`、`docs/ai-development.md`、`CONTRIBUTING.md`、`AGENTS.md`、
  `docs/contributor-workflow-tutorial.md` は、それぞれ異なる部分集合で `make check` の実行内容を
  列挙しています。どれも実際の11ステップと一致しません。短い列挙を信じた読み手は、ゲートが
  検証している範囲を過小評価します。

この2つの矛盾以外にも、同じ構造がまだ乖離せずに繰り返されています。ロードマップの BE-ID 規約には、
基準となる記述が `docs/ai-development.md` にあります。プレースホルダー `BE-0284`、マージ時の
割り当て、両言語ファイル、インデックスの再生成という規約です。しかし `roadmaps/README.md` は、
そこへリンクせず全文を再記述しています。PR タイトル・本文・Draft の規約も同様です。
`docs/ai-development.md` を指す代わりに、`AGENTS.md` でまるごと再説明されています。そして
`.github/claude-review-prompt.md` は、6つの規範をリンクなしで凝縮再記述しています。prime
directives、docstring のスタイル
（[BE-0065](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference-ja.md)）、
バイリンガルドキュメント、日本語の文章スタイル、ロードマップへのリンク規約という規約です。
コメントは「なぜ」を書く規約も含みます。これが、この項目のきっかけとなった事例です。再記述は
それぞれ、将来の修正が見落としうるもう1箇所になります。

これはプロダクトの振る舞いではなく、プロジェクトの運用上のリスクです。ただし、このロードマップが
すでに扱ってきたコントリビューター向け基盤整備の範囲そのものでもあります。手順書をコマンドに変換
した [BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md)、
両言語に単一のプローズ・スタイル権威を置いた
[BE-0278](../BE-0278-tech-writing-skill/BE-0278-tech-writing-skill-ja.md) と同じ系譜にあります。
本項目は、その系譜を「文体」から「規範をどのファイルに置くか」へ広げるものです。

## 詳細設計

規範のクラスタごとに、現時点でもっとも詳しく最新の内容を保っているファイルを基準ファイルと定め、
それ以外の記述は、乖離しうる独自の詳細を持たない短いリンクまたは要約に置き換えます。

| 規範 | 基準ファイル | ポインタに縮小するファイル |
|---|---|---|
| ロードマップのディレクトリ構成と BE-ID の割り当て | `docs/ai-development.md` | `roadmaps/README.md`、`CONTRIBUTING.md`、`AGENTS.md`、`.github/PULL_REQUEST_TEMPLATE.md` |
| PR タイトル・本文・Draft の規約 | `docs/ai-development.md` | `AGENTS.md`、`CONTRIBUTING.md` |
| `make check` の実行内容一覧 | `CLAUDE.md`（すでにもっとも詳しい） | `docs/ai-development.md`、`CONTRIBUTING.md`、`AGENTS.md`、`docs/contributor-workflow-tutorial.md` |
| docstring のスタイル（BE-0065） | `docs/ai-development.md` | `.github/claude-review-prompt.md` |
| バイリンガルドキュメントの運用（どのファイルをいつ） | `docs/ai-development.md` | `.github/claude-review-prompt.md`、`AGENTS.md` |
| 日本語の文章スタイル（敬体、造語を使わない） | `japanese-document-writing` スキル（BE-0278 のとおり） | `.github/claude-review-prompt.md`、`AGENTS.md` |
| Prime directives | `CLAUDE.md`（維持。後述） | — |

作業は MECE に次のとおり分解します。

1. **まず矛盾している箇所を直します。** `CONTRIBUTING.md`、`AGENTS.md`、
   `.github/PULL_REQUEST_TEMPLATE.md` を、BE-0159 を引用してフラットな `roadmaps/BE-NNNN-<slug>/`
   構成の記述に更新します。あわせて、5箇所で食い違っている `make check` の列挙を、`CLAUDE.md`
   の11ステップに合わせます。
2. **`.github/claude-review-prompt.md` の6つの再記述をリンクに畳み込みます。** それぞれを
   `CLAUDE.md` または `docs/ai-development.md` の基準となる記述、あるいは文章スタイルについては
   `japanese-document-writing` スキルへ向け、規約を再説明しないようにします。
3. **`roadmaps/README.md` の独自の BE-ID 規約再記述を、リンクに置き換えます。**
   `docs/ai-development.md` へ向け、インデックス表にたどり着くまでに読者が必要とする1、2文だけを
   残します。
4. **`AGENTS.md` と `CONTRIBUTING.md` の PR 規約・ロードマップ規約のフルの記述を縮小します。**
   ポインタと、その文書の読者だけに固有の詳細を残します。
5. **「再記述せずリンクする」という一文を、`docs/ai-development.md` 自体へ追記します。**
   既存のドキュメント規約と並べて置きます。そうすれば、新しい規範を追加するコントリビューター
   は、最初から一箇所にまとめられます。

**Prime directives は意図的な例外です。** 短く、行き渡ることを前提とした重要な規範です。
`CLAUDE.md` 自体が、PR 規約セクションでの再記述を、より詳しい規範の「短縮形」だと明記しています。
本項目は、短く正確な prime directives の再記述をすべて維持します。取り除くのは、乖離したものや、
その規範自体を超える詳細を再現しているものだけです。

この整理は CI のチェックではなく、レビュー時の規範のままとします。ある段落が「再記述」なのか、
「行き渡らせるべき規範を正当に繰り返している」だけなのかの判定には、意味論的な判断が要ります。
それは、バイリンガルドキュメントの規範や
[BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md) の DESIGN.md
整合性規範と同じ判断です。prime directive 1 は、この種の判断を決定的なゲートから締め出しています。

## 検討した代替案

- **プローズの断片をハッシュ化・差分比較して乖離を CI で落とすスクリプト。** 却下しました。
  言い回しの異なる2つの段落が同じ規範を述べていると判定するには意味論的な判断が必要で、それは
  `run`・CI の合否判定に LLM を持ち込むことになり、prime directive 1 が禁じています。LLM を
  使わなければ、意図的に短くした再記述（特に prime directives）のすべてを誤検知します。
- **すべての再記述を削除し、例外なくすべての文書にリンクさせる。** 短く行き渡らせるべき規範に
  ついては却下しました。初読で自己完結していなければならない文書もあります
  （`CLAUDE.md` がすでに述べている「省略なし」の規範）。そうした文書は、3文程度のために初読の
  読者を他の文書へ送るより、短く正確な prime directives の写しを正当に持ち続けるべきです。
- **すべての規範を統合する新しいトップレベルの `conventions.md` を1つ作る。** 却下しました。
  AI 開発プロセスの規範の基準ファイルはすでに `docs/ai-development.md` です。prime directives と
  クイックリファレンスとしてのゲートの基準ファイルは `CLAUDE.md` です。3つ目のトップレベル文書を
  作ることは、統合ではなくむしろ規範をさらに分散させます。

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

- 一括で集約しました（PR #1169）。`CONTRIBUTING.md` / `AGENTS.md` /
  `.github/PULL_REQUEST_TEMPLATE.md` のフラット構成への修正、`docs/ai-development.md`・
  `CONTRIBUTING.md`・`AGENTS.md`・`docs/contributor-workflow-tutorial.md` の `make check` 記述を
  `CLAUDE.md` へのリンクに統一、`.github/claude-review-prompt.md` の各規範を基準ファイルへリンク、
  `roadmaps/README.md` の BE-ID 再記述をポインタへ縮小、`docs/ai-development.md` へ「再記述せず
  リンクする」規範を追加、を行いました。日本語のミラーも同時に更新しました。

## 参考

- [BE-0159 — ロードマップディレクトリのフラット化（状態別フォルダの廃止）](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders-ja.md)
- [BE-0278 — tech-writing スキル](../BE-0278-tech-writing-skill/BE-0278-tech-writing-skill-ja.md)
- [BE-0113 — DESIGN.md の整合性再確立](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md)
- [BE-0069 — 実行可能なコントリビューター向けガードレール](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md)
- [BE-0065 — docstring 標準・API リファレンス](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference-ja.md)
