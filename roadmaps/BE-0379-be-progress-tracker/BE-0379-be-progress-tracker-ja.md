[English](BE-0379-be-progress-tracker.md) · **日本語**

# BE-0379 — 専用の低コストスキルでBE作業の進捗をArtifactに記録する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0379](BE-0379-be-progress-tracker-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0379") |
| 実装 PR | [#1673](https://github.com/bajutsu-e2e/bajutsu/pull/1673) |
| トピック | Contributor workflow |
<!-- /BE-METADATA -->

## はじめに

**`be-progress-tracker`** という小さな専用スキルを追加します。
[`ideation`](../../.apm/skills/ideation/SKILL.md)、
[`implement-be`](../../.apm/skills/implement-be/SKILL.md)、
[`propose-and-build`](../../.apm/skills/propose-and-build/SKILL.md)は、それぞれ自分のステップの区切りでこのスキルを呼び出します。
トラッカーは、BE項目1件につき1枚の進捗ページ（概要、実装の進捗、作業ログ）を最新の状態に保ちます。
長いセッションを見守る人が、会話全体を読まなくても項目の現状を把握できるようにするためです。

トラッカーは、3つの呼び出し元に埋め込む実装ではなく、別個のスキルとして切り出します。
デフォルトのモデルは、トークン消費量の低い`haiku`にします。
呼び出し元がどのモデルで動いていても、チェックポイントは低コストに保たれます。

## 動機

`implement-be`や`propose-and-build`のセッションは長くなりがちです。
ブランチを切り、計画を承認し、コードを書き、レビューを1回以上回します。
その後にゲートを通してPRを開き、フォローアップのループを回します。
今のところ、この進捗を知る手段は、セッションの会話、最終的なロードマップの`Status`の変更、PRの3つしかありません。
会話全体を読まずに「この項目は今どこまで進んでいるか」を確認できる、共有可能な場所が1つもありません。

設計を絞り込む観点は2つあります。

1. **呼び出し元のモデルを使い回すと、チェックポイントごとに割高なコストがかかります。**
   `implement-be`のデフォルトモデルは`opus`です。
   進捗ページの更新は、呼び出し元がすでに下した判断を書式に整えるだけで、新たな判断を必要としません。
   チェックポイントのたびに`opus`の単価を払うのは無駄です。
   [BE-0103](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering.md)は、
   機械的なスキルに軽量なモデルをデフォルトとして与える方式をすでに確立しています
   （[`roadmap-filter`](../../.claude/skills/roadmap-filter/SKILL.md)の`haiku`を参照）。
   本項目は、同じ方式をより狭い用途に適用します。
2. **ロードマップを執筆・実装する3つのスキルは、すでに同じ形のチェックポイントを共有しています。**
   チェックポイントは、計画の承認、レビューの合格、ゲートの成功、PRの作成です。
   単一のトラッカースキルを3つから同じ形で呼び出せば、このロジックを1か所にまとめられます。
   そうしなければ、3つのスキルがそれぞれ独自の進捗整形処理を抱えることになります。

## 詳細設計

作業は、互いに独立した5つの単位に分解できます。

1. **共有ワークフロー。**
   [`.apm/skills/be-progress-tracker/SKILL.md`](../../.apm/skills/be-progress-tracker/SKILL.md)が、
   ドキュメントの3つの固定セクション（概要、進捗、作業ログ）を定めます。
   呼び出し元が渡すべきチェックポイントの内容も、ここで定めます。内容は、BEのID、呼び出し元ワークフローの名前とステップ、作業ログ1文です。
   担わない範囲は次のとおりです。
   - 唯一の正にはなりません。ロードマップ項目とPRが正であり続けます。
   - 呼び出し元を止めません。チェックポイントが失敗または利用不可でも、その旨を記録して呼び出し元は先へ進みます。
   - 自分の進捗ページ以外は変更しません。
2. **Claudeアダプター。**
   [`.claude/skills/be-progress-tracker/SKILL.md`](../../.claude/skills/be-progress-tracker/SKILL.md)は、
   frontmatterに`model: haiku`を持ちます。
   進捗ページはMarkdownのArtifactとして公開し、BE項目1件につきArtifact1枚とします。
   以後のチェックポイントでは、公開し直さず同じURLに再デプロイします。
3. **Codexアダプター。**
   `.agent-hosts/codex/skills/be-progress-tracker/SKILL.md`には、ホスティングされたArtifactに相当するものがありません。
   そこで同じ3セクション構成のページを、Gitの管理対象外にした`tmp/be-status/`配下のローカルファイルへ書き出します。
   そのパスは、最初の1回だけユーザーに伝えます。
   なお[BE-0390](../BE-0390-apm-skill-management/BE-0390-apm-skill-management-ja.md)が、のちにCodex用のツリーを撤去し、
   Claude用アダプターをスキルごとの単一ソースへ畳み込みました。
4. **呼び出し元3つの共有ワークフローへのチェックポイント配線。**
   `implement-be/workflow.md`と`ideation/workflow.md`には、短い段落を1つずつ追加します。
   段落では、それぞれ自身のどの番号付きステップでチェックポイントを呼ぶべきかを述べます。
   `propose-and-build/workflow.md`は、独自のチェックポイントを定めません。
   委任先の`ideation`と`implement-be`の各フェーズが持つチェックポイントを引き継ぐこと、
   両フェーズを通じてすでに使っている`BE-0379`の仮番号をキーに使うことを述べるにとどめます。
5. **Claudeアダプターの呼び出し指示。**
   `implement-be`、`ideation`、`propose-and-build`の各`SKILL.md`には、1行ずつ追加します。
   その行は、Agentツールでチェックポイントを呼び出す際に`model: "haiku"`を明示的に渡すよう、ホストに指示します。
   サブエージェントの呼び出しは、スキル自身のfrontmatterが指定するモデルを自動的には継承しないためです。
   `propose-and-build`の2つの委任先フェーズは、チェックポイント自体を引き継げます（前項）が、その呼び出し方法は引き継げません。
   アダプターは自分自身の共有ワークフローだけを読み、他のスキルのアダプターは読まないためです。
   そのため`propose-and-build`のアダプターにも、同じ1行が必要です。

製品コードへの変更はありません。
本項目は、コントリビューターワークフロー向けの整備です。
対象は、当時のスキルツリー（`.agent-workflows/`、`.claude/skills/`、`.agent-hosts/codex/skills/`）配下に閉じています。
`bajutsu/`、`BajutsuKit/`、`run`/CIの経路には触れません。
第1の指針も保たれます。トラッカーが決定的なゲートの近くに現れることはなく、
人間が判断を下すスキルがすでに下した決定を、書式に整えるだけです。

## 検討した代替案

- **専用スキルに切り出さず、各呼び出し元スキルの手順に直接組み込む案。** 却下しました。
  - 「進捗ページを整形する」というロジックが3か所に重複します。
  - 各チェックポイントのコストが、呼び出し元の動くモデル（`implement-be`なら`opus`）に左右されます。チェックポイントを低コストに保つという目的が成り立ちません。
- **Artifactの代わりに、リポジトリにコミットするファイルへ状態を書き出す案。** Claudeアダプターについては却下しました。
  - 頻繁に書き換えるその場限りのドキュメントは、チェックポイントのたびにコミットが要ります。履歴が煩雑になり、実装そのもののコミットと競合します。
  - コミットしなければ、セッションの外にいる誰の目にも入りません。
  - Artifactは、Gitを介さず人間が開けるホスティングされたページです。
  - Artifactに相当するものを持たないCodexアダプターだけは、ローカルファイルへのフォールバックを採用します。ただし、コミットしないGit管理対象外の`tmp/`配下に限ります。
- **別ページを設けず、ロードマップ項目自身の`Progress`セクションを各ステップで更新する案。** 却下しました。
  `Progress`は、実際に取り込まれた内容についてのPR単位のログです
  （[`docs/ai-development.md`](../../docs/ai-development.md)）。
  セッション内のステップごとの経過を追う場所ではありません。
  両者を混ぜると、ロードマップ項目が煩雑になります。
  さらに、何かが実際に取り込まれたときだけでなく、些細なステップのたびに製品として管理されているファイルへ手を入れることになります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 共有ワークフロー（`.apm/skills/be-progress-tracker/SKILL.md`）
- [x] `model: haiku`を持つClaudeアダプター（`.claude/skills/be-progress-tracker/SKILL.md`）
- [x] ローカルファイルへのフォールバックを持つCodexアダプター（`.agent-hosts/codex/skills/be-progress-tracker/SKILL.md`）
- [x] `implement-be`、`ideation`、`propose-and-build`の共有ワークフローへのチェックポイント配線
- [x] `implement-be`、`ideation`、`propose-and-build`向けのClaudeアダプター呼び出し指示（Agentツール経由で`model: "haiku"`を渡す）

提案と実装を1つの変更としてまとめて行いました。

## 参考

- [BE-0103](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering.md) — 本項目が
  より狭い用途へ転用した、タスクとモデル・エフォートを対応づける規約。
- [BE-0347](../BE-0347-bounded-ci-review-cycle/BE-0347-bounded-ci-review-cycle.md) — Agentツール
  を通じて役割ごとに異なるモデルを割り当てる先例。
- [`roadmap-filter`](../../.claude/skills/roadmap-filter/SKILL.md) — 本項目のClaudeアダプターが
  踏襲した、既存の`model: haiku`スキル。
- [`implement-be`](../../.apm/skills/implement-be/SKILL.md)、
  [`ideation`](../../.apm/skills/ideation/SKILL.md)、
  [`propose-and-build`](../../.apm/skills/propose-and-build/SKILL.md) — 本スキルを
  呼び出す3つのワークフロー。
