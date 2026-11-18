[English](BE-0380-fix-issue-skill.md) · **日本語**

# BE-0380 — fix-issueスキルが、ロードマップ項目なしに軽微なGitHub Issueを実装まで運ぶ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0380](BE-0380-fix-issue-skill-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0380") |
| 実装 PR | [#1679](https://github.com/bajutsu-e2e/bajutsu/pull/1679) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

本項目は、素のGitHub Issueを一気通貫で仕上げる新スキル、`fix-issue`を追加します。
スキルが扱う範囲は、実装、レビュー、ゲート、Draftのプルリクエスト（PR）です。
素のGitHub Issueとは、ロードマップ（Bajutsu Evolution、BE）項目にするほどでもない小さな不具合、ちょっとした使い勝手の悪さ、範囲の定まった改善を指します。
このスキルは、ロードマップのファイルには一切触れず、新しいラベルも必要としません。

`fix-issue`は、[`implement-be`](../../.apm/skills/implement-be/SKILL.md)が番号付きのBE項目に対して担っている役割を、素のIssueに対して担います。
`implement-be`の実装、レビュー、フォローアップの各手順は、そのまま再利用します。
異なるのは、素のIssueがBE項目と違う2点だけです。
1つは担当をどう確保するか、もう1つは修正が取り込まれたときに何が完了を告げるかです。

## 動機

BE提案を書かずに、軽い不具合や改善をIssueとして出すことは、すでにできます。
`bug`ラベルと`enhancement`ラベルのIssueテンプレート（[`bug_report.yml`](../../.github/ISSUE_TEMPLATE/bug_report.yml)、
[`feature_request.yml`](../../.github/ISSUE_TEMPLATE/feature_request.yml)）が、その用途で用意されています。
両者の`config.yml`も、大きなアイデアのときだけロードマップに進むよう案内しています。
[`task-select`](../../.apm/skills/task-select/SKILL.md)スキルも、これらのオープンなIssueをロードマップと並べて調査し、次に着手すべき候補として順位付けしています。

しかし、候補が選ばれたあとにそれを仕上げる仕組みはありません。
`implement-be`は計画をマージ済みの修正に変えるスキルですが、その手順すべてがBEファイルの存在を前提にしています。
`Status`フィールドを読み、ボットが管理するトラッキングIssue（BE-0109）の担当を確保します。
`Status`を`Implemented`に切り替え、PRタイトルに`[BE-NNNN]`を付けます。
1行で済む不具合修正には、こうした足場がまったくありません。

素のIssueを今日拾ったセッションには、悪い選択肢が2つあります。

- 不要なBE提案に仕立て上げる。
- `implement-be`のブランチ、レビュー、ゲートの作法を、記憶から再現する。この方法を採った2つのセッションが、同じやり方になる保証はありません。

最初に思いつく対策は、新しいラベルです。
[BE-0109](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md)がロードマップトラッキングIssueにラベルを付けているのと同じように、担当者がBE項目を要しないと判断したIssueにラベルを付ける形です。
本項目は、このラベルを意図的に見送ります。
「素の修正として仕上げてよい」と「提案が必要」の境界は、判断の問題です。
その判断は、起票時点で下せるとは限りません。修正作業の途中で初めて明らかになることもあります。
そのため、ラベルではなくスキル自身が判断を下します。
[`ideation`](../../.apm/skills/ideation/SKILL.md)は、逆方向の境界をすでにこの形で判断しています。
提案ではなく実装を求めるユーザーを、ラベルに頼らずリダイレクトしているのです。

## 詳細設計

1. **新しいラベルは置かず、スキル自身がスコープの適合を判断し、適合しないときは委ねます。**
   修正を計画する前に、`fix-issue`はIssueのスコープを短い基準に当てます。基準は次の4つです。
   - 原因が1つに絞れること。
   - 変更が局所的であること。
   - Issue自身が示している範囲を超えて、ユーザー向けの振る舞いや設定面を広げないこと。
   - [3つの基本原則](../../CLAUDE.md#prime-directives-do-not-violate)と衝突しないこと。

   修正が、設計判断、横断的な変更、基本原則に触れるアイデアの再構成のいずれかを要すると判明した場合、スキルは止まります。
   理由をユーザーに伝え、続行せずに[`ideation`](../../.apm/skills/ideation/SKILL.md)や
   [`propose-and-build`](../../.apm/skills/propose-and-build/SKILL.md)を指し示します。
   このとき、詳細設計の2で確保した担当も解除します（`gh issue edit <N> --remove-assignee @me`）。
   委ねたIssueが作業中のまま残らないようにするためです。
   修正の本当の形は、Issue本文だけでは見えないことがあります。
   そのためこのエスカレーションは、手順3の計画段階までのどの時点でも起こりえます。
2. **担当の確保には、トラッキングIssueではなく、Issue自身のAssignee欄を使います。**
   ボットが管理するトラッキングIssueは使いません。BE-0109は素のIssueを同期しないため、`fix-issue`はIssue自身で担当を確認し、確保します。
   まず`gh issue view <N> --json state,assignees`で確認します。
   Issueがオープンで、担当者がいないか、すでに自分が担当になっている場合に限り、`gh issue edit <N> --add-assignee @me`を実行します。
   閉じたIssueや、すでに他の人が担当になっているIssueでは、スキルはそこで止まります。
   `implement-be`も、BEトラッキングIssueの担当について同じ規則を適用しています。
   その手順1の`Status`の読み取りが、ここでは状態の確認にあたります。
3. **コードを仕上げる手順は、`implement-be`をほぼそのまま再利用します。**
   スキルは、リンクされたコードとテストを読んで足場を固めます。
   1トピック1ブランチの原則に沿ったブランチ（`claude/fix-issue-<N>-<slug>`）を切り、短い計画を立てます。
   計画には、触れるファイル、機械的に検証できる結果、追加するテストをまとめます。
   計画は、コードを書く前にユーザーと確認します。
   実装はコードベースの書き方に合わせます。
   続いて、2つの役割による自己レビューが、[`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md)の契約に照らして差分を点検します
   （[BE-0347](../BE-0347-bounded-ci-review-cycle/BE-0347-bounded-ci-review-cycle.md)）。
   [Tier 2 ゲート](../../docs/glossary.md#the-two-tiers)における唯一の判定者は、AIの呼び出しを一切経ない`make check`です。
   ここで手順を書き直す必要はありません。`fix-issue`は`implement-be`の手順をそのまま指し示します。
4. **BE項目のPRと異なる点です。**
   - 切り替えるべき`Status`フィールドがないため、`implement-be`のロードマップ昇格の手順に対応するものはありません。
   - PRタイトルは、`[BE-NNNN]`の接頭辞を付けず、スコープ付きの通常のタイトルのままにします。[`docs/ai-development.md`](../../docs/ai-development.md)が、ロードマップ項目を持たないPRについて定めている形と同じです。
   - PR本文には`Closes #<N>`を加えます。PRがマージされれば、元のIssueは自動で閉じます。
   - 自己レビューとゲートの両方が問題なしとなった時点で、Draft PRはこれまでと同様に自動で開きます
     （[BE-0230](../BE-0230-hands-free-implement-review-loop/BE-0230-hands-free-implement-review-loop.md)）。
   - `implement-be`の手順10にある例外も、そのまま引き継ぎます。差分がドキュメントのみである修正は、Draftではなく、`--reviewer bajutsu-e2e/steering-committee`を付けてReady for reviewで開きます。
   - その後は、`implement-be`と共通の`pr-followup`ループが、静かで緑の状態までPRを進めます。停止条件、エスカレーション規則、繰り返し回数の上限も、`implement-be`と同じです。
5. **新設するスキルのファイルです。**
   - ホストに依存しない`.agent-workflows/fix-issue/workflow.md`が、上記の手順を記述します。
   - Claude Codeアダプター`.claude/skills/fix-issue/SKILL.md`は、フロントマターで`model: opus`を宣言します。両者ともプロダクトコードを出荷するため、`implement-be`と同じHeavyティアです。
   - Codexアダプター`.agent-hosts/codex/skills/fix-issue/SKILL.md`は、既存のすべてのスキルが今日備えているホスト間の対応関係を保ちます（[`CLAUDE.md`](../../CLAUDE.md#agent-skill-layout)）。
   - BE-0390が、のちにこの3つのファイルを単一ソース`.apm/skills/fix-issue/SKILL.md`へ畳み込みました。
6. **ドキュメントと引き渡しの整備です。**
   - [`docs/ai-development.md`](../../docs/ai-development.md)（および`docs/ja/`側の対訳）のスキル別モデル一覧に、`fix-issue` → `opus`（Heavy）の1行を加えます。`propose-and-build`の項目の隣に、同じ理由で置きます。「ロードマップ項目を起草し出荷する3つのスキル」の節にも、短い注記を加えます。注記は、BE番号を一切受け取らない作業の対になる道として、`fix-issue`を指します。
   - [`CLAUDE.md`](../../CLAUDE.md)の「PRを開く担当は作業内容で決まる（BE-0230）」の箇条書きが、「実装作業」の項で`implement-be`と並べて`fix-issue`を挙げます。`fix-issue`が生むPRは、その箇条書きがすでにDraftとして自動で開き、ペースを保った追跡ループで進めている、まとまった状態でゲートを通る変更そのものだからです。
   - [`task-select`](../../.apm/skills/task-select/SKILL.md)の手順5の推奨コマンドは、選ばれた候補がBE番号を持たない素のGitHub Issueのとき、`fix-issue #<N>`になります。ロードマップ項目に対する既存の`implement-be BE-NNNN`という推奨は変わりません。

## 検討した代替案

- **新しい`quick-fix`ラベルを設け、担当者がBE項目を要しないと判断したIssueに付ける案。** 見送りました。
  - 独自に維持すべき分類体系が必要になります。誰が付けるか、BE-0109の`roadmap-tracking`ラベルのような同期ワークフローが必要か、誤って付けたときどうするかを、決めなければなりません。
  - この区別は、スキル自身がIssueを読むだけですでに下せます。
  - 起票時点で固定された静的なラベルには、調査の途中で修正が設計判断を要すると判明する事態は見えません。詳細設計の1の判断なら、それを捉えられます。
- **既存の`good first issue`ラベルを流用する案。** 見送りました。このラベルにはすでに、初心者にも取り組みやすいという、人間向けの確立した意味があります。そこに「AI向けの即応可否」という意味を重ねると、見た目だけでは2つの意味を区別できなくなります。
- **`implement-be`を拡張し、素のIssue番号も受け取れるようにする案。** 見送りました。あのスキルのすべての手順は、`Status`メタデータ、`Implementing PR`の行、`[BE-NNNN]`の接頭辞といった、BEファイルの存在を前提にしています。Issueを受け取れるようにするには、それぞれの手順を再利用せず分岐させる必要があります。本来は無条件であるべきスキルの契約に、条件分岐が入り込みます。小さな姉妹スキルを別に置くほうが、双方の契約を単純に保てます。
- **これまでどおり、その場のセッションに委ねる案。** 見送りました。
  - セッションは、`implement-be`のブランチ、レビュー、ゲートの作法を毎回記憶から再現することになり、一貫性は保証されません。
  - 素のIssueを候補としてすでに洗い出している`task-select`とも噛み合いません。選ばれた候補を引き渡す先がないままだからです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 1. ラベルの代わりとなるスコープ適合の判断、`ideation`/`propose-and-build`へのエスカレー
  ション、および委ねたIssueが作業中と見えないようにする担当の解除。
- [x] 2. Issue自身の`state`とAssignee欄によるオープン状態および担当の確認と、担当の確保。
- [x] 3. `implement-be`から再利用するコード仕上げの手順(ブランチ、計画の確認、実装、2役割による
  自己レビュー、ゲート)。
- [x] 4. BE項目のPRとの違い:`Status`を切り替えない、`[BE-NNNN]`接頭辞を付けない、本文に
  `Closes #<N>`を書く、同じDraft PRの自動オープン(`implement-be`のドキュメントのみの変更を
  Ready for reviewで開く例外を引き継ぐ)と`pr-followup`ループ。
- [x] 5. 新設するスキルのファイル:ホストに依存しないワークフロー、Claude Codeアダプター、Codex
  アダプター。
- [x] 6. ドキュメントの整備:`docs/ai-development.md`(および対訳)、`CLAUDE.md`、`task-select`。

## 参考

- [`implement-be`](../../.apm/skills/implement-be/SKILL.md)：本スキルが素のIssueに対して
  写し取っているBE項目側の対であり、そのまま再利用する手順の出どころです。
- [`ideation`](../../.apm/skills/ideation/SKILL.md)：本スキルがエスカレーション先として指す
  スキルであり、スコープの境界をラベルではなくスキルの中で判断する先例です。
- [`propose-and-build`](../../.apm/skills/propose-and-build/SKILL.md)：もう1つのエスカ
  レーション先で、設計がすでに固まっており1つのPRで提案と実装を済ませられる小さなアイデア向けです。
- [`task-select`](../../.apm/skills/task-select/SKILL.md)：素のGitHub Issueを候補として
  すでに順位付けしている読み取り専用のスキルで、詳細設計の6で`fix-issue`への引き渡しを得ます。
- [BE-0109](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md)：オープンな
  ロードマップ項目の担当を追うGitHub Issueの仕組みであり、本項目が扱うIssueはBE番号に結び付かず、
  ボットによる管理の外にとどまる対比先です。
- [BE-0230](../BE-0230-hands-free-implement-review-loop/BE-0230-hands-free-implement-review-loop.md)：
  素のIssue修正でも再利用する、自動で開くDraft PRと`pr-followup`の追跡ループです。
- [BE-0347](../BE-0347-bounded-ci-review-cycle/BE-0347-bounded-ci-review-cycle.md)：実装手順が
  PRを開く前に走らせる、2つの役割と2つのモデルによるローカルの自己レビューです。
- [`.github/ISSUE_TEMPLATE/bug_report.yml`](../../.github/ISSUE_TEMPLATE/bug_report.yml)と
  [`feature_request.yml`](../../.github/ISSUE_TEMPLATE/feature_request.yml)：本項目が変更なしに
  修正を届ける先である、既存の受付テンプレートです。
- [`docs/glossary.md`](../../docs/glossary.md#the-two-tiers)：本項目の実装手順が唯一の合否判定者
  として委ねるTier 2ゲートです。
