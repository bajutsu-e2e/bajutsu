[English](BE-XXXX-fix-issue-skill.md) · **日本語**

# BE-XXXX — fix-issueスキルが、ロードマップ項目なしに軽微なGitHub Issueを実装まで運ぶ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-fix-issue-skill-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

本項目は、素のGitHub Issueを実装、レビュー、ゲート、Draft プルリクエスト(PR)まで一気通貫で仕上げる
新スキル、fix-issueを追加します。素のGitHub Issueとは、ロードマップ(Bajutsu Evolution、BE)項目に
するほどでもない小さな不具合、ちょっとした使い勝手の悪さ、範囲の定まった改善を指します。このスキル
はロードマップのファイルには一切触れず、新しいラベルも必要としません。
[`implement-be`](../../.agent-workflows/implement-be/workflow.md)が番号付きのBE項目に対して
担っている役割を、素のIssueに対して同じように担うスキルです。implement-beの実装、レビュー、
フォローアップの各手順は、そのまま再利用します。異なるのは、素のIssueがBE項目と本質的に違う2点
だけです。担当をどう確保するか、修正が取り込まれたときに何が完了を告げるか、です。

## 動機

BE提案を書かずに、軽い不具合や改善をIssueとして出すことはすでにできます。`bug`ラベルと`enhancement`
ラベルのIssueテンプレート([`bug_report.yml`](../../.github/ISSUE_TEMPLATE/bug_report.yml)、
[`feature_request.yml`](../../.github/ISSUE_TEMPLATE/feature_request.yml))がまさにその用途で
用意されています。両者の`config.yml`も、大きなアイデアのときだけロードマップに進むよう案内してい
ます。[`task-select`](../../.agent-workflows/task-select/workflow.md)スキルも、これらのオープンな
Issueをロードマップと並べて調査し、次に着手すべき候補としてすでに順位付けしています。

しかし、候補が選ばれたあとにそれを仕上げる仕組みはありません。`implement-be`は計画を統合済みの修正
に変えるスキルですが、その手順すべてがBEファイルの存在を前提にしています。`Status`フィール
ドを読み、ボットが管理するトラッキングIssue(BE-0109)を担当として確保し、`Status`を`Implemented`
に切り替え、PRタイトルに`[BE-NNNN]`を付けます。1行で済む不具合修正には、こうした足場がまったく
ありません。素のIssueを今日拾ったセッションは、2つの悪い選択肢しか持ちません。不要なBE提案に
仕立て上げるか、`implement-be`のブランチ、レビュー、ゲートの作法を記憶から再現するかです。後者を
選んだ2つのセッションが同じやり方をする保証はありません。

最初に思いつく対策は新しいラベルです。[BE-0109](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md)
がロードマップトラッキングIssueにすでに付けているのと同じように、担当者がBE項目を要しないと判断した
Issueにこのラベルを付ける、という形です。本項目はこのラベルを意図的に見送ります。「素の修正として
仕上げてよい」と「提案が必要」の境界は判断であり、その判断は起票時点で下せるとは限らず、修正作業の
途中で初めて明らかになることもあるため、ラベルではなくスキル自身が下します。
[`ideation`](../../.agent-workflows/ideation/workflow.md)はすでに逆方向の境界をこの形で判断してい
ます。提案ではなく実装を求めるユーザーを、ラベルに頼らずリダイレクトしているのです。

## 詳細設計

1. **新しいラベルは置かず、スキル自身がスコープの適合を判断し、適合しないときは委ねます。** 修正を
   計画する前に、`fix-issue`はIssueのスコープを短い基準に当てます。原因が1つに絞れること、変更が
   局所的であること、新しいユーザー向けの振る舞いや設定面を追加しないこと、そして
   [3つの基本原則](../../CLAUDE.md#prime-directives-do-not-violate)と衝突しないことです。修正が
   設計判断や横断的な変更、あるいは基本原則に触れるアイデアの再構成を要すると判明した場合、スキル
   は止まります。理由をユーザーに伝え、続行せずに
   [`ideation`](../../.agent-workflows/ideation/workflow.md)や
   [`propose-and-build`](../../.agent-workflows/propose-and-build/workflow.md)を指し示します。
   修正の本当の形はIssue本文だけでは見えないことがあるため、このエスカレーションは、手順3の計画段階までのどの時点でも起こりえます。
2. **担当の確保は、ボットが管理するトラッキングIssueではなく、Issue自身のAssignee欄で行います。**
   BE-0109は素のIssueを同期しないため、`fix-issue`はIssue自身で担当を確認し確保します。まず
   `gh issue view <N> --json assignees`で確認し、Issueに担当者がいないか、すでに自分が担当になって
   いる場合に限り`gh issue edit <N> --add-assignee @me`を実行します。すでに他の人が担当になって
   いるIssueでは、スキルはそこで止まります。`implement-be`もBEトラッキングIssueに同じ規則を適用
   しています。
3. **コードを仕上げる手順は、`implement-be`をほぼそのまま再利用します。** スキルはリンクされた
   コードとテストを読んで足場を固め、1トピック1ブランチの原則に沿ったブランチ
   (`claude/fix-issue-<N>-<slug>`)を切り、触れるファイル、機械的に検証できる結果、追加するテスト
   をまとめた短い計画を立てます。この計画はコードを書く前にユーザーと確認します。実装はコードベース
   の書き方に合わせ、続いて2つの役割による自己レビューが
   [`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md)の契約に照らして差分
   を点検します
   ([BE-0347](../BE-0347-bounded-ci-review-cycle/BE-0347-bounded-ci-review-cycle.md))。
   [Tier 2 ゲート](../../docs/glossary.md#the-two-tiers)における唯一の判定者は、AIの呼び出しを一切
   経ない`make check`です。ここで手順を書き直す必要はありません。`fix-issue`は
   `implement-be`の手順をそのまま指し示します。
4. **BE項目のPRと異なる点です。** 切り替えるべき`Status`フィールドがないため、`implement-be`の
   ロードマップ昇格の手順にはここでは対応するものがありません。PRタイトルは`[BE-NNNN]`の接頭辞を
   付けず、スコープ付きの通常のタイトルのままにします。これは
   [`docs/ai-development.md`](../../docs/ai-development.md)がロードマップ項目を持たないPRについて
   すでに定めている形と同じです。本文には`Closes #<N>`を加えるため、PRがマージされれば元のIssueは
   自動で閉じます。自己レビューとゲートの両方が問題なしとなった時点で、Draft PRはこれまでと同様に
   自動で開きます
   ([BE-0230](../BE-0230-hands-free-implement-review-loop/BE-0230-hands-free-implement-review-loop.md))。
   その後は同じ停止条件、エスカレーション規則、繰り返し回数の上限
   を使う、`implement-be`と共通の`pr-followup`ループが、静かで緑の状態までPRを進めます。
5. **新設するスキルのファイルです。** ホストに依存しない
   `.agent-workflows/fix-issue/workflow.md`が上記の手順を記述します。Claude Codeアダプター
   `.claude/skills/fix-issue/SKILL.md`は、フロントマターで`model: opus`を宣言します。これは
   `implement-be`と同じHeavyティアであり、両者ともプロダクトコードを出荷するためです。Codex
   アダプター`.agent-hosts/codex/skills/fix-issue/SKILL.md`は、既存のすべてのスキルが今日備えて
   いるホスト間の対応関係を保ちます
   ([`CLAUDE.md`](../../CLAUDE.md#agent-skill-layout))。
6. **ドキュメントと引き渡しの整備です。**
   - [`docs/ai-development.md`](../../docs/ai-development.md)(および`docs/ja/`側の対訳)に、
     `propose-and-build`の項目と同じ理由による`fix-issue` → `opus`(Heavy)の1行をスキル別モデル
     一覧へ加えます。また「ロードマップ項目を起草し出荷する3つのスキル」の節にも、BE番号を
     一切受け取らない作業の対になる道として`fix-issue`を指す短い注記を加えます。
   - [`CLAUDE.md`](../../CLAUDE.md)の「PRを開く担当は作業内容で決まる(BE-0230)」の箇条書きが、
     「実装作業」の項で`implement-be`と並べて`fix-issue`を挙げます。`fix-issue`が生むPRは、その
     箇条書きがすでにDraftとして自動で開き、ペースを保った追跡ループで進めている、まとまった状態で
     ゲートを通る変更そのものだからです。
   - [`task-select`](../../.agent-workflows/task-select/workflow.md)手順5の推奨コマンドは、選ば
     れた候補がBE番号を持たない素のGitHub Issueのとき`fix-issue #<N>`になります。ロードマップ項目
     に対する既存の`implement-be BE-NNNN`という推奨は変わりません。

## 検討した代替案

- **新しい`quick-fix`ラベルを設け、担当者がBE項目を要しないと判断したIssueに付ける案。** 見送り
  ました。誰が付けるか、BE-0109の`roadmap-tracking`ラベルのような同期ワークフローが必要か、誤って
  付けたときどうするかといった、独自に維持すべき分類体系が必要になります。この区別はスキル自身が
  Issueを読むだけですでに下せますし、起票時点で固定された静的なラベルには、調査の途中で修正が
  設計判断を要すると判明する事態は見えません。詳細設計の1の判断ならそれを捉えられます。
- **既存の`good first issue`ラベルを流用する案。** 見送りました。このラベルにはすでに、初心者にも
  取り組みやすいという、人間向けの確立した意味があります。そこに「AI向けの即応可否」という意味を
  重ねると、見た目だけでは2つの意味を区別できなくなります。
- **`implement-be`を拡張し、素のIssue番号も受け取れるようにする案。** 見送りました。あのスキルの
  すべての手順は、`Status`メタデータ、`Implementing PR`の行、`[BE-NNNN]`の接頭辞といった、BEファ
  イルの存在を前提にしています。Issueを受け取れるようにするには、それぞれの手順を再利用するのでは
  なく分岐させる必要があり、本来は無条件であるべきスキルの契約に条件分岐が入り込みます。小さな姉妹
  スキルを別に置くほうが、双方の契約を単純に保てます。
- **これまでどおり、その場のセッションに委ねる案。** 見送りました。セッションは`implement-be`の
  ブランチ、レビュー、ゲートの作法を毎回記憶から再現することになり、一貫性は保証されません。また、
  素のIssueを候補としてすでに洗い出している`task-select`とも噛み合いません。選ばれた候補を引き渡す
  先がないままだからです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 1. ラベルの代わりとなるスコープ適合の判断、および`ideation`/`propose-and-build`へのエスカレー
  ション。
- [ ] 2. Issue自身のAssignee欄による担当の確認と確保。
- [ ] 3. `implement-be`から再利用するコード仕上げの手順(ブランチ、計画の確認、実装、2役割による
  自己レビュー、ゲート)。
- [ ] 4. BE項目のPRとの違い:`Status`を切り替えない、`[BE-NNNN]`接頭辞を付けない、本文に
  `Closes #<N>`を書く、同じDraft PRの自動オープンと`pr-followup`ループ。
- [ ] 5. 新設するスキルのファイル:ホストに依存しないワークフロー、Claude Codeアダプター、Codex
  アダプター。
- [ ] 6. ドキュメントの整備:`docs/ai-development.md`(および対訳)、`CLAUDE.md`、`task-select`。

## 参考

- [`implement-be`](../../.agent-workflows/implement-be/workflow.md)：本スキルが素のIssueに対して
  写し取っているBE項目側の対であり、そのまま再利用する手順の出どころです。
- [`ideation`](../../.agent-workflows/ideation/workflow.md)：本スキルがエスカレーション先として指す
  スキルであり、スコープの境界をラベルではなくスキルの中で判断する先例です。
- [`propose-and-build`](../../.agent-workflows/propose-and-build/workflow.md)：もう1つのエスカ
  レーション先で、設計がすでに固まっており1つのPRで提案と実装を済ませられる小さなアイデア向けです。
- [`task-select`](../../.agent-workflows/task-select/workflow.md)：素のGitHub Issueを候補として
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
