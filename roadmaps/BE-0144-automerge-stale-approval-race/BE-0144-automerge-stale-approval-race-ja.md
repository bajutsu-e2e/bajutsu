[English](BE-0144-automerge-stale-approval-race.md) · **日本語**

# BE-0144 — auto-merge の stale-approval レースを解消する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0144](BE-0144-automerge-stale-approval-race-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0144") |
| 実装 PR | [#691](https://github.com/bajutsu-e2e/bajutsu/pull/691) |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

コードベース分析レポートは、auto-merge における stale-approval レースの可能性を指摘しています。
レビューされていない新しいコミットが push されたあとでも、それより前に得た承認のまま PR が
マージされうる、という懸念です。このレースが起こりうるかどうかは、`main` ブランチの ruleset の
正確な設定に依存します。レポート自体は、その設定を確認していません。

この指摘は**未確認**です。最初のタスクは、修正が必要かどうかを判断する前に、ruleset を確認する
ことです。

## 動機

レポートが述べているレースは、次のとおりです。

1. レビュアが PR を承認します。
2. author がさらにコミットを push します。
3. プラットフォームが、以前の承認を dismiss しません。
4. auto-merge が、stale な承認の効力のまま、レビューされていないコミットを取り込みます。ここでの
   auto-merge は、BE-0061 のアトミックな claim-ref による自動化か、GitHub のネイティブ
   auto-merge です。

このレースがこのリポジトリで起こりうるかどうかは、`main` のブランチ保護 ruleset の
レビュー要件だけに依存します。bajutsu 固有のコードは関係しません。本提案は、この依存関係を
記録して答えを確定させるために存在します。レースが実在すると主張するものではありません。

リポジトリの「Require code review」ruleset（`pull_request` ルール）を読むと、
`dismiss_stale_reviews_on_push: true` と `require_last_push_approval: true` がすでに設定
されています。この ruleset が `main` を管理していて、これらのフィールドが GitHub の
ドキュメントどおりに動作するなら、レースはすでに解消されています。承認後の新しいコミットは
以前の承認をすべて無効化し、マージにはさらに、最後に push された正確なコミットへの承認が
必要になるからです。

本提案は、この読み取りを最終結論とは扱いません。実際の最初の作業は、次の 2 つです。

- 一時点の読み取りではなく、稼働中の ruleset に対して設定を確認します。
- BE-0061 の自動化とネイティブ auto-merge が、dismiss された承認とどう相互作用するかを確認します。

## 詳細設計

1. **`main` の ruleset の稼働中の設定を確認する。** `gh api repos/{owner}/{repo}/rulesets`
   （または Settings → Rules の UI）を使います。`pull_request` ルールの
   `dismiss_stale_reviews_on_push` と `require_last_push_approval` の両フィールドを確認します。
   確認の対象は、`main` へのマージに実際に適用されているブランチ保護の ruleset です。
2. **両方がすでに有効で、すべてのマージ経路をカバーしている場合は、既存の設定によってすでに
   緩和されているものとして、本提案をクローズする。** マージ経路には、BE-0061 の自動化による
   claim + push の再割り当てフローと、GitHub のネイティブ auto-merge が含まれます。コードや
   ワークフローの変更は不要で、確認結果を記録するだけです。
3. **いずれかが欠けている場合、またはいずれかのマージ経路がそれらを回避してしまう場合は、
   `main` を管理する ruleset で両フィールドを有効化して強制する。** 回避の例は、自動化による
   コミット push が、ruleset を再評価せずにマージを再トリガーする場合です。変更後は、その制約の
   下で BE-0061 の自動化が引き続き機能することを再確認します。claim-ref による push フローは
   PR ブランチへの再 push であり、stale-approval の dismiss ポリシーがまさに適用対象とする
   種類の push だからです。

## 検討した代替案

- **ruleset を確認せずに、レースが実在するものと仮定して修正を出荷する。** 却下しました。
  レポートはこの指摘を明示的に要確認としており、ruleset の読み取り結果も、関連する設定が
  すでに存在することを示唆しています。すでに緩和されている問題への修正は、無駄な作業になります。
  BE-0061 の既存の自動化と衝突するリスクもあります。
- **深刻度が不明確なので指摘自体を無視する。** 却下しました。stale な承認の下でレビューされて
  いないコミットがマージされる可能性がわずかでもあるなら、一度確認しておく価値があります。
  確認そのものは、リスクに比べて安価です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 稼働中の `main` ruleset の `dismiss_stale_reviews_on_push` と
      `require_last_push_approval` の設定を確認する。
- [x] これらの設定が、BE-0061 の自動化とネイティブ auto-merge を含むすべてのマージ経路に
      適用されることを確認する。
- [x] ~~不足が見つかった場合、該当する設定を有効化・強制し、BE-0061 の自動化を再検証する。~~
      — 該当なし（不足は見つかりませんでした）。
- [x] 不足が見つからなかった場合、確認結果を記録して本提案をクローズする（コード変更なし）。

### 確認ログ

**2026-07-05**：`gh api repos/bajutsu-e2e/bajutsu/rulesets/17735477` で稼働中の ruleset を
照会しました。

**Ruleset「Require code review」（ID 17735477）**

| 設定 | 値 |
|---|---|
| Enforcement | active |
| スコープ | `~DEFAULT_BRANCH`（main） |
| `dismiss_stale_reviews_on_push` | **true** |
| `require_last_push_approval` | **true** |
| `required_approving_review_count` | 1 |
| `required_review_thread_resolution` | true |

**マージ経路の分析**

1. **人手によるマージ**：ruleset が直接適用されます。承認後の push は承認を dismiss し、最後に
   push されたコミットへの承認が、マージの前提条件となります。レースは解消済みです。
2. **ネイティブ auto-merge**（`auto-merge.yml`）：`bajutsu-automation-bot`（Integration
   4178537）が有効化します。この App は、本 ruleset の bypass actor（`bypass_mode: always`）
   です。bypass ステータスにもかかわらず、PR #677 と #678（いずれも本 App による auto-merge で
   マージ）を実地観察すると、auto-merge はマージ前にレビュー承認を待っていました。
   - PR #677：承認 04:49:50、マージ 04:49:51。
   - PR #678：承認 04:20:53、マージ 04:21:04。

   `gh pr merge --auto` は、アクターの bypass 権限にかかわらず、すべてのブランチ保護要件を
   自発的に待機します。bypass を直接行使するパスは `--auto` なしの `gh pr merge` ですが、
   自動化のどこにも使われていません。レースは解消済みです。
3. **roadmap-id.yml**（BE-0089）：マージ後に BE ID を割り当てる、`main` への直接 push です。
   PR のマージ経路ではないので、stale-approval レースとは無関係です。

**結論**：コードベース分析レポートが指摘した stale-approval レースは、稼働中の設定によって
すでに緩和されています。コード、ワークフロー、ruleset のいずれの変更も不要です。

## 参考

- GitHub リポジトリの ruleset「Require code review」（`pull_request` ルール:
  `dismiss_stale_reviews_on_push`、`require_last_push_approval`）
- 関連: BE-0089（マージ時 BE-ID 割り当て）、BE-0061（BE-ID 割り当てのハードニング）、
  BE-0069（実行可能な contributor ガードレール）
- 2026-07-02 のコードベース分析レポート（セキュリティ）に基づきます。
