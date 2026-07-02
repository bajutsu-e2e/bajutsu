[English](BE-XXXX-automerge-stale-approval-race.md) · **日本語**

# BE-XXXX — auto-merge の stale-approval レースを解消する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-automerge-stale-approval-race-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

コードベース分析レポートは、auto-merge における stale-approval レースの可能性を指摘して
います。つまり、レビュー前の新しいコミットより前に得た承認のまま PR がマージされうるという
懸念です。しかしこれが実際に起こりうるかどうかは `main` ブランチの ruleset の正確な設定に
依存しており、レポート自体はその設定を確認していません。この指摘は**未確認**です。最初の
タスクは、そもそも修正が必要かどうかを判断する前に ruleset を確認することです。

## 動機

レポートが述べているレースとは次のようなものです。レビュアが PR を承認し、その後 author が
さらにコミットを push した場合、プラットフォームが以前の承認を dismiss しなければ、
auto-merge（BE-0061 のアトミックな claim-ref による自動化、あるいは GitHub のネイティブ
auto-merge）が、stale な承認の効力のままレビューされていないコミットを取り込んでしまう
可能性がある、というものです。これがこのリポジトリで実際に起こりうるかどうかは、bajutsu 固有の
コードではなく、`main` のブランチ保護 ruleset のレビュー要件だけに依存します。本提案は、
この依存関係を記録し答えを確定させるために存在するのであって、レースが実在すると主張する
ものではありません。

リポジトリの「Require code review」ruleset（`pull_request` ルール）を読むと、
`dismiss_stale_reviews_on_push: true` と `require_last_push_approval: true` がすでに設定
されていることがわかります。もしこれが実際に `main` を管理している ruleset であり、これらの
フィールドが GitHub のドキュメントどおりに動作するなら、このレースはすでに解消されている
ことになります。承認後の新しいコミットは以前の承認をすべて無効化し、マージにはさらに最後に
push された正確なコミットへの承認が必要になるからです。ただし本提案は、この一度きりの読み取りを
最終結論とは扱いません。実際に確認すべき最初の作業は、これを稼働中の ruleset に対して
（一時点の読み取りではなく）確認すること、そして BE-0061 の自動化やネイティブ auto-merge が
dismiss された承認とどう相互作用するかを確認することです。

## 詳細設計

1. **`main` の ruleset の稼働中の設定を確認する。** `gh api repos/<owner>/<repo>/rulesets`
   （あるいは Settings → Rules の UI）経由で、`main` へのマージに実際に適用されているブランチ
   保護の ruleset について、`pull_request` ルールの `dismiss_stale_reviews_on_push` と
   `require_last_push_approval` の両フィールドを確認します。
2. **両方がすでに有効で、すべてのマージ経路をカバーしている場合**（BE-0061 の自動化による
   claim + push の再割り当てフローや GitHub のネイティブ auto-merge を含む）、本提案は
   既存の設定によってすでに緩和されているものとしてクローズします。コードやワークフローの
   変更は不要で、確認結果を記録するだけです。
3. **いずれかが欠けている場合、またはいずれかのマージ経路がそれらを回避してしまう場合**
   （例えば、自動化によるコミット push が ruleset を再評価せずにマージを再トリガーする場合）、
   `main` を管理する ruleset で `dismiss_stale_reviews_on_push` と
   `require_last_push_approval` を有効化・強制し、その制約の下で BE-0061 の自動化が引き続き
   機能することを再確認します（その claim-ref による push フローは PR ブランチへの再 push で
   あり、stale-approval の dismiss ポリシーがまさに適用対象とするような push だからです）。

## 検討した代替案

- **ruleset を確認せずに、レースが実在するものと仮定して修正を出荷する。** 却下しました。
  レポートはこれを明示的に要確認としており、ruleset の読み取り結果はすでに関連する設定が
  存在することを示唆しています。すでに緩和されている問題に対する修正を作るのは無駄な作業に
  なり、BE-0061 の既存の自動化と衝突するリスクもあります。
- **深刻度が不明確なので指摘自体を無視する。** 却下しました。stale な承認の下でレビューされて
  いないコミットがマージされる可能性がわずかでもあるなら、確認そのものが安価である以上、
  一度確認しておく価値があります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 稼働中の `main` ruleset の `dismiss_stale_reviews_on_push` と
      `require_last_push_approval` の設定を確認する。
- [ ] これらの設定が、BE-0061 の自動化とネイティブ auto-merge を含むすべてのマージ経路に
      適用されることを確認する。
- [ ] 不足が見つかった場合、該当する設定を有効化・強制し、BE-0061 の自動化を再検証する。
- [ ] 不足が見つからなかった場合、確認結果を記録して本提案をクローズする（コード変更なし）。

まだ着手した PR はありません。

## 参考

GitHub リポジトリの ruleset「Require code review」（`pull_request` ルール:
`dismiss_stale_reviews_on_push`、`require_last_push_approval`）。関連: BE-0089
（マージ時 BE-ID 割り当て）、BE-0061（BE-ID 割り当てのハードニング）、BE-0069
（実行可能な contributor ガードレール）。2026-07-02 のコードベース分析レポート
（セキュリティ）に基づきます。
