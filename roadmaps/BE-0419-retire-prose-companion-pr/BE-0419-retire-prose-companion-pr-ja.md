[English](BE-0419-retire-prose-companion-pr.md) · **日本語**

# BE-0419 — companion PR による文言修正の仕組みを廃止し、元の pull request で対処する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0419](BE-0419-retire-prose-companion-pr-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0419") |
| 実装 PR | [#1983](https://github.com/bajutsu-e2e/bajutsu/pull/1983) |
| トピック | コントリビューターワークフロー |
| 関連 | [BE-0343](../BE-0343-prose-companion-pr/BE-0343-prose-companion-pr-ja.md)、[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の自動レビュー（BE-0203）は、言い回しだけを扱うレンズを 2 つ持っています。1 つは日本語の文章
品質を判定します。もう 1 つは、英語の `docs/*.md` とロードマップ本文の言い回し品質を判定します。

BE-0343 は、この 2 つのレンズが出す指摘に `(non-blocking, prose)` という印を付けました。専用の
`prose-companion` ジョブも追加しました。このジョブは、指摘自身が持つ `suggestion` ブロックを
`prose-fix/pr-<N>` ブランチへ機械的に適用し、貢献者自身のブランチに対する小さな pull request を
開きます。これにより、言い回しの修正が、貢献者自身の pull request の継続的インテグレーション（CI）一式を
再実行させることはありませんでした。

本項目は、このジョブと、その裏にあるスクリプト（`scripts/prose_companion_pr.py`）、両者のテストを
廃止します。変わらないことが 2 つあります。2 つのレンズは、これまでどおり本物の違反を指摘し続けます。
修正が機械的に決まる場合は、これまでどおり `suggestion` ブロックも添えます。

変わることも 2 つあります。指摘の印は、ほかのすべての指摘と同じ、素の `(non-blocking)` になります。
修正は、その指摘を出した pull request 自体への通常の push として届きます。貢献者がほかのレビュー
コメントに答えるときと同じやり方です。

## 動機

BE-0343 が解決しようとした問題は 1 つでした。言い回しだけの指摘を直そうとして同じ pull request へ
push すると、挙動に影響のない変更のために、pull request 全体の CI 一式が再実行されてしまいます。
companion pull request は、この修正を別の小さなブランチで届けることで、そのコストを避けました。
元の pull request が開いたままの間も、レビューできる形でした。

この利点には、継続的なコストが伴っていました。当時の提案は、このコストを CI 一式の節約と
引き換えに認めていました。コストは次の 4 つです。

- 貢献者自身のブランチへの書き込み権限を持つ、権限の強い自動化アカウント
- 独自の信頼境界と no-op 経路を持つジョブ
- 言い回しの指摘が現れたときだけ実行されるスクリプト
- 貢献者が取り組んでいる pull request とは別に、気づいてレビューしてマージしなければならない、
  もう 1 つの pull request

companion pull request のレビューが無償で済むことはありません。提案されたテキストの適用そのものは
ワンクリックで済んでも、レビューという作業は残ります。

2 つのコストを比べると、常設の仕組みのほうが重くなります。常設の仕組みとは、権限の強いアカウント、
追加のジョブ、スクリプト、そして貢献者が別途追い続けなければならない pull request です。この仕組みが
避けようとしていた、ときどき生じる CI 一式のコストのほうが軽くなります。

本項目はこの仕組みを取り除き、より単純な原則に戻します。言い回しだけの指摘も、ほかのレビューコメントと
同じで、通常の push で直すという原則です。

この原則が定着したかどうかは、次のように確かめられます。言い回しの指摘のあとに、新しい
`prose-fix/pr-<N>` ブランチや pull request が現れなくなります。代わりに、貢献者自身の pull request への
push として、修正が届きます。

## 詳細設計

### 変更点

- [`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) から、「言い回しだけの
  指摘には `(non-blocking, prose)` という印を付ける」という箇条書きと、`(non-blocking, prose)` という
  印そのものを削除します。
  - どちらのレンズの指摘にも、素の `(non-blocking)` という印を付けます。修正が機械的に決まる場合は、
    `suggestion` ブロックも添えます。ほかのすべての指摘と同じ扱いです。
  - 「文章表現にまつわる規範」の節は、2 つのレンズの基準を、そのまま残します。基準とは、明確で名指し
    できる違反と、具体的な書き換えです。ただし、その根拠から「修正が無償である」という理由は外れます。
  - 基準が残る理由は 2 つあります。`suggestion` ブロックが、書き換えをすでに一行の適用に変えている
    こと。そして、この 2 つのレンズが、[`CLAUDE.md`](../../CLAUDE.md) がすでに求めている
    `document-writing` の文章表現の規範に対する、レビューの唯一の自動チェックであることです。
- [`.github/workflows/claude-review.yml`](../../.github/workflows/claude-review.yml) から、
  `prose-companion` ジョブをまるごと削除します。`review` ジョブには手を入れません。このジョブは
  BE-0203 が導入し、[BE-0347](../BE-0347-bounded-ci-review-cycle/BE-0347-bounded-ci-review-cycle-ja.md)
  が open、reopen、`@claude review` に絞りました。
- `scripts/prose_companion_pr.py` と `tests/test_prose_companion_pr.py` を削除します。
- `tests/test_claude_review_workflow.py` から、`prose-companion` ジョブに関するアサーションと、
  モジュールの docstring にあるその言及を削除します。残すのは、`review` ジョブ自身のトリガー配線を
  説明する部分です。
- [`.apm/skills/claude-review/SKILL.md`](../../.apm/skills/claude-review/SKILL.md) から、印を付けて
  投稿する手順を削除します。対象は、手順 3 の適用範囲の注記と、手順 5 の投稿フォーマットです。
  そのうえで `make skills` を実行し、デプロイ先の `.claude/skills/claude-review/SKILL.md` を同期します。
- [`CLAUDE.md`](../../CLAUDE.md) から、「言い回しだけの指摘は companion PR として届く」という箇条書きを
  削除します。
- [`docs/ai-development.md`](../../docs/ai-development.md) と、その対訳である
  [`docs/ja/ai-development.md`](../../docs/ja/ai-development.md) からは、「文章表現だけの指摘に対する
  companion PR（BE-0343）」の節を削除します。代わりに、自動レビューの説明のすぐそばに短い段落を
  置きます。段落の内容は、言い回しだけの指摘も、ほかの指摘と同じく、同じ pull request への通常の push で
  直す、というものです。

### 変わらない点

- `.github/claude-review-prompt.md` にある、2 つのレンズの判定範囲と基準です。
- `review` ジョブ自体と、BE-0347 が定めるその実行タイミングです。
- `roadmaps/BE-0343-prose-companion-pr/` の `実装済み` という状態です。この状態は、実際に実装され、
  出荷された内容の正確な記録であり、これからもそうです。この項目は、いまは本項目への
  `Superseded by` のリンクも持っています。

### すでに開いている 3 件の companion pull request

執筆時点で、companion pull request が 3 件開いたままです。#1978、#1980、#1982 です。それぞれ、元に
なった pull request がまだ開いています。

ジョブを削除しても、これらには影響しません。それぞれ、ほかの小さな pull request と同じようにレビュー
してマージできます。元の pull request がもう必要としていなければ、閉じることもできます。本項目が
マージされたあとは、新しい companion pull request は開かれません。

## 検討した代替案

- **仕組みは残し、発動条件だけをさらに絞る案（たとえば `@claude prose-pr` というコメントでの
  オンデマンド実行）。** BE-0343 自身の提案は、オンデマンドの発動条件をすでに退けています。手作業の
  手順をまた持ち込んでしまうからです。自動発動の条件をここで同じように絞っても、常設の仕組みは
  そのまま残ります。本項目が取り除こうとしているコストは、何も減りません。
- **`(non-blocking, prose)` という印だけを外し、ジョブ自体は休眠させたまま残す案。** どの指摘も
  たどり着かないジョブは、死んだコードです。生きた GitHub App のアカウントと、通し続けなければならない
  独自のテスト一式を、抱えたまま残ります。削除する手間は、残す手間と変わりません。削除すれば、あとの
  読み手に説明するものも残りません。
- **companion PR による届け方だけでなく、2 つのレンズ自体を丸ごと削除する案。** 2 つのレンズが下す
  判定そのものの価値が、下がったわけではありません。修正を 2 つ目の pull request で届ける方法が、
  割に合わなくなっただけです。レンズまで削除すると、[`CLAUDE.md`](../../CLAUDE.md) がいまも求めている
  `document-writing` の house convention に対するレビューのカバレッジが、気づかないうちに下がって
  しまいます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `.github/claude-review-prompt.md`から`(non-blocking, prose)`という印を削除し、「文章表現
  にまつわる規範」の根拠を書き直す
- [x] `.github/workflows/claude-review.yml`から`prose-companion`ジョブを削除する
- [x] `scripts/prose_companion_pr.py`と`tests/test_prose_companion_pr.py`を削除し、
  `tests/test_claude_review_workflow.py`を整理する
- [x] `.apm/skills/claude-review/SKILL.md`から印を付けて投稿する手順を削除し、`make skills`で
  `.claude/skills/claude-review/SKILL.md`を同期する
- [x] `CLAUDE.md`の箇条書きと、`docs/ai-development.md` / `docs/ja/ai-development.md`の該当節を
  更新する
- [x] 本項目のidが割り当てられた時点で、`roadmaps/BE-0343-prose-companion-pr/`に相互リンクとなる
  `Superseded by`を追加する

### ログ

- 提案と同じpull requestで、まるごと出荷しました
  （[`propose-and-build`](../../.apm/skills/propose-and-build/SKILL.md)）。当時すでに開いていた
  3件のcompanion pull request（#1978、#1980、#1982）は、閉じずに、独立してマージできる通常の
  pull requestとして残しました。
- 本項目のid（`BE-0419`）の割り当てを受けて、`roadmaps/BE-0343-prose-companion-pr/`に相互リンクと
  なる`Superseded by`を追加しました。

## 参考

[BE-0343](../BE-0343-prose-companion-pr/BE-0343-prose-companion-pr-ja.md)（本項目が廃止する仕組
み）、[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md)（2つの文章
表現レンズがどちらも属する自動レビュー）、
[BE-0347](../BE-0347-bounded-ci-review-cycle/BE-0347-bounded-ci-review-cycle-ja.md)（`review`
ジョブが保つ発動条件の絞り込み）、`document-writing`・`english-document-writing`・
`japanese-document-writing`の各スキル（2つの文章表現レンズが従う規範）。
