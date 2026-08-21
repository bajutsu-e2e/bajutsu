[English](BE-XXXX-be-progress-tracker.md) · **日本語**

# BE-XXXX — 専用の低コストスキルでBE作業の進捗をArtifactに記録する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-be-progress-tracker-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | TBD |
| トピック | Contributor workflow |
<!-- /BE-METADATA -->

## はじめに

**`be-progress-tracker`** という小さな専用スキルを追加します。
[`ideation`](../../.agent-workflows/ideation/workflow.md)、
[`implement-be`](../../.agent-workflows/implement-be/workflow.md)、
[`propose-and-build`](../../.agent-workflows/propose-and-build/workflow.md)
は、それぞれ自分のステップの区切りでこのスキルを呼び出し、BE項目1件につき1枚の進捗ページ
（概要、実装の進捗、作業ログ）を最新の状態に保ちます。長いセッションを見守る人が、
会話全体を読まなくても項目の現状を把握できるようにするためです。この機能を3つの呼び出し元
それぞれに実装として埋め込むのではなく、別個のスキルとして切り出し、トークン消費量の低い
モデル（`haiku`）をデフォルトにします。呼び出し元がどのモデルで動いていても、チェックポイントの
呼び出し自体は低コストに保たれます。

## 動機

`implement-be` や `propose-and-build` のセッションは長くなりがちです。ブランチを切り、計画を
承認し、コードを書きます。そのうえでレビューを1回以上回し、ゲートを通してPRを開き、
フォローアップのループを回します。今のところ、この進捗を知る手段はセッションの会話そのものと、最終的な
ロードマップの`Status`の変更、そしてPRしかありません。会話全体を読まずに「この項目は今どこまで
進んでいるか」を確認できる、単一の共有できる場所がありません。

設計を絞り込む観点は2つあります。

1. **呼び出し元自身のモデルを使い回すと、チェックポイントごとに割高なコストがかかります。**
   `implement-be`のデフォルトモデルは`opus`です。進捗ページの更新は、呼び出し元がすでに下した判断を
   書式に整えるだけの作業であり、新たな判断を必要としません。チェックポイントのたびに`opus`の
   単価を払うのは無駄です。
   [BE-0103](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering.md)は、
   機械的なスキルに軽量なモデルをデフォルトとして与える方式をすでに確立しています
   （[`roadmap-filter`](../../.claude/skills/roadmap-filter/SKILL.md)の`haiku`を参照）。
   本項目は、この方式をより狭い用途に転用するものです。
2. **ロードマップを執筆・実装する3つのスキルは、すでに同じ形のチェックポイントを共有しています。**
   計画の承認、レビューの合格、ゲートの成功、PRの作成です。単一の進捗トラッカースキルを3つ
   から同じ形で呼び出すことで、このロジックを1か所にまとめられます。各スキルが独自の進捗
   整形処理を抱える事態を避けられます。

## 詳細設計

作業は、互いに独立した5つの単位に分解できます。

1. **共有ワークフロー**
   （[`.agent-workflows/be-progress-tracker/workflow.md`](../../.agent-workflows/be-progress-tracker/workflow.md)）は、
   ドキュメントの3つの固定セクション（概要、進捗、作業ログ）を定めます。呼び出し元が渡すべき
   チェックポイントの内容も、BEのID、呼び出し元ワークフローの名前とステップ、作業ログ1文として
   ここで定めます。担う範囲も明確にし、唯一の正とはならないこと、処理が失敗しても呼び出し元を
   止めないこと、自分の進捗ページ以外は変更しないことを述べます。
2. **Claudeアダプター**
   （[`.claude/skills/be-progress-tracker/SKILL.md`](../../.claude/skills/be-progress-tracker/SKILL.md)）は、
   frontmatterに`model: haiku`を持ち、進捗ページをMarkdownのArtifactとして公開します。
   BE項目1件につきArtifact1枚とし、以後のチェックポイントでは同じURLに再デプロイします。
3. **Codexアダプター**
   （[`.agent-hosts/codex/skills/be-progress-tracker/SKILL.md`](../../.agent-hosts/codex/skills/be-progress-tracker/SKILL.md)）には、
   ホスティングされたArtifactに相当するものがありません。そこで同じ3セクション構成の
   ページを、Gitの管理対象外にした`tmp/be-status/`配下のローカルファイルへ書き出し、
   最初の1回だけそのパスをユーザーに伝えます。
4. **呼び出し元3つの共有ワークフローへのチェックポイント配線**
   `implement-be/workflow.md`と`ideation/workflow.md`には、それぞれ自身のどの番号付き
   ステップでチェックポイントを呼ぶべきかを述べる短い段落を追加します。
   `propose-and-build/workflow.md`は独自のチェックポイントを定めません。代わりに、委任先の
   `ideation`と`implement-be`の各フェーズが持つチェックポイントをそのまま引き継ぐこと、
   両フェーズを通じてすでに使っている`BE-XXXX`の仮番号をキーに使うことを述べるにとどめます。
5. **Claudeアダプターの呼び出し指示**
   `implement-be`と`ideation`の各`SKILL.md`には、Agentツールを通じてチェックポイントを
   呼び出す際に`model: "haiku"`を明示的に渡す、という1行を追加します。サブエージェントの
   呼び出しは、呼び出されるスキル自身のfrontmatterが指定するモデルを自動的には継承しない
   ためです。`propose-and-build`のアダプターは、委任先の2スキルからチェックポイントと
   その呼び出し方法をそのまま引き継ぐため、変更を必要としません。

製品コードへの変更はありません。本項目は`.agent-workflows/`、`.claude/skills/`、
`.agent-hosts/codex/skills/`配下だけに閉じたコントリビューターワークフロー向けの整備であり、
`bajutsu/`、`BajutsuKit/`、`run`/CIの経路には触れません。第1の指針もそのまま保たれます。
このトラッカーが決定的なゲートの近くに現れることは一切なく、人間が判断を下すスキルがすでに
下した決定を書式に整えるだけです。

## 検討した代替案

- **専用スキルに切り出さず、各呼び出し元スキルの手順に直接組み込む案。** 却下しました。
  「進捗ページを整形する」というロジックを3か所に重複させてしまい、各チェックポイントの
  コストを呼び出し元が動いているモデル（`implement-be`なら`opus`）に紐付けてしまいます。
  これでは、チェックポイントを低コストに保つという目的が成り立ちません。
- **Artifactの代わりに、リポジトリにコミットするファイルへ状態を書き出す案。** Claudeアダプター
  については却下しました。頻繁に書き換えるその場限りのドキュメントは、チェックポイントの
  たびにコミットするなら履歴が煩雑になり、実装そのもののコミットと競合します。コミットしない
  なら、セッションの外にいる誰の目にも入りません。Artifactであれば、Gitを介さず人間が
  開けるホスティングされたページになります。Artifactと同等のものを持たないCodexアダプター
  だけは、ローカルファイルへのフォールバックを採用しますが、コミットせずgitの管理対象外である
  `tmp/`配下だけに限ります。
- **別ページを設けず、ロードマップ項目自身の`Progress`セクションを各ステップで更新する案。**
  却下しました。`Progress`は、実際に取り込まれた内容についてのPR単位のログであり
  （[`docs/ai-development.md`](../../docs/ai-development.md)）、セッション内のステップごとの
  経過を追う場所ではありません。両者を混ぜると、ロードマップ項目が煩雑になり、何かが実際に
  取り込まれたときだけでなく、些細なステップのたびに製品として管理されているファイルへ
  手を入れることになります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 共有ワークフロー（`.agent-workflows/be-progress-tracker/workflow.md`）
- [x] `model: haiku`を持つClaudeアダプター（`.claude/skills/be-progress-tracker/SKILL.md`）
- [x] ローカルファイルへのフォールバックを持つCodexアダプター（`.agent-hosts/codex/skills/be-progress-tracker/SKILL.md`）
- [x] `implement-be`、`ideation`、`propose-and-build`の共有ワークフローへのチェックポイント配線
- [x] `implement-be`と`ideation`向けのClaudeアダプター呼び出し指示（Agentツール経由で`model: "haiku"`を渡す）

提案と実装を1つの変更としてまとめて行いました。

## 参考

- [BE-0103](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering.md) — 本項目が
  より狭い用途へ転用した、タスクとモデル・エフォートを対応づける規約。
- [BE-0347](../BE-0347-bounded-ci-review-cycle/BE-0347-bounded-ci-review-cycle.md) — Agentツール
  を通じて役割ごとに異なるモデルを割り当てる先例。
- [`roadmap-filter`](../../.claude/skills/roadmap-filter/SKILL.md) — 本項目のClaudeアダプターが
  踏襲した、既存の`model: haiku`スキル。
- [`implement-be`](../../.agent-workflows/implement-be/workflow.md)、
  [`ideation`](../../.agent-workflows/ideation/workflow.md)、
  [`propose-and-build`](../../.agent-workflows/propose-and-build/workflow.md) — 本スキルを
  呼び出す3つのワークフロー。
