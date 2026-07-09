[English](BE-XXXX-daily-doc-freshness-pr.md) · **日本語**

# BE-XXXX — ロードマップとドキュメントを毎日更新してレビュー用 PR を開く定期ワークフロー

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-daily-doc-freshness-pr-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | 開発基盤（コントリビュータ体験） |
<!-- /BE-METADATA -->

## はじめに

ロードマップとドキュメントのうち人手で維持している部分を、実際に出荷された内容と毎日照合する
GitHub Actions ワークフローを追加します。差分（ドリフト）が見つかったときは、更新案をまとめた
**ドラフト**プルリクエストを 1 本開き、人間がレビューしてマージします。更新の起草には、既存の
自動レビューと同じ AI プロバイダ（[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md)）
経由で Claude Code アクションを使います。マージするのは人間だけです。LLM は `run`/CI の合否には
一切関与せず、生成された PR もほかの PR と同じく決定論的な `make check` で検証されます。

## 動機

このリポジトリは、文書のうち*機械的に導ける*部分についてはすでに正しさを保っています。`make
roadmap-index` が索引を再生成し、コミット済みの表がずれれば `make test` が落ちます。
[BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity-ja.md) は
`docs/` からロードマップへのリンク切れをゲートの失敗にします。`roadmap-drift-check`
（[BE-0149](../BE-0149-roadmap-placeholder-format-guardrail/BE-0149-roadmap-placeholder-format-guardrail-ja.md)）
はオープン中の PR をテンプレートと再照合します。しかし、これらのいずれもカバーできないのが、
最新に保つのに*意味的な判断*を要し、そのために静かに陳腐化していく内容です。

- **BE 項目の `Status` / `Progress` / `Implementing PR`。** 作業合意では、項目を前進させた PR は
  同じ変更のなかで `Progress` のチェックを付け、`Implementing PR` を埋め、作業の開始や出荷に
  合わせて `Status` を切り替えることになっています。しかし実際には、コードだけがマージされて項目が
  更新されないことがあり、コードがマージ済みなのに項目が `Proposal` のまま残ったり、`Progress` の
  チェックリストが実態に遅れたりします。これはフォーマット検査ではなく文章と状態の照合なので、
  何も検知できません。
- **`docs/architecture.md#implementation-status`。** 「すでに何が存在するか」の拠り所とされている
  節ですが、機能が出荷されたときにコントリビュータが更新を忘れなければ、という前提でしか正確さを
  保てません。
- **`DESIGN.md` / `docs/architecture.md` を挙動と揃えること
  （[BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md)）。** この規則は
  あえて CI ゲートではなくレビュー時の規範にとどめています。文章の一段落がコードと合っているかを
  確かめるには意味的な判断が要り、それをゲートにすれば LLM を `run`/CI の合否経路に載せてしまう
  （プライムディレクティブ 1）からです。そのため注意で担保されており、注意は途切れます。

共通するのは、これらがまさに、決定論的なゲートではプライムディレクティブ 1 を破らずには塞げない
穴だという点です。だからこそ、人間が気づく瞬間と瞬間のあいだに溜まっていきます。照合の下書きを
定期的に作り、マージの判断は人間に委ねるエージェントは、その一線を越えずに遅延を縮めます。これは
自動レビューに対する起草側の対応物です。BE-0203 がマージを止めない AI *レビュアー*を足したのに
対し、本項目はマージしない AI *起草者*を足します。

## 詳細設計

`.github/workflows/daily-doc-refresh.yml` という定期ワークフロー 1 本と、その契約となる指示ファイル
1 つで構成します。AI が起草した変更はすべて、人間がレビューしゲートで検証される PR を通ります。
`main` に直接載せることはありません。

### 1. 定期ワークフローと 2 つの資格情報

`schedule`（毎日の cron）と `workflow_dispatch`（オンデマンド）で起動します。ジョブには独立した
資格情報が **2 つ**必要で、その両方が揃っていない限り緑の no-op でなければなりません（`roadmap-id.yml`
/ `claude-review.yml` と同じ「全部揃うか、さもなくば休止」の方針で、設定が中途半端なリポジトリを
決して赤にしないためです）。

- **AI プロバイダ。** `claude-review.yml` とまったく同じ方式で選びます（BE-0203、
  [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)）。Claude Code
  サブスクリプション（`CLAUDE_CODE_OAUTH_TOKEN`）があればそれを、なければ OIDC 経由の Amazon
  Bedrock（`AWS_BEDROCK_ROLE_ARN` + `BEDROCK_MODEL_ID`）を使い、どちらもなければ休止します。これが
  更新を起草します。
- **自動化 App トークン**（`AUTOMATION_BOT_APP_ID` / `AUTOMATION_BOT_PRIVATE_KEY`、
  `actions/create-github-app-token` 経由）。`roadmap-id.yml` や `roadmap-drift-check.yml` と同じ使い方
  です。既定の `GITHUB_TOKEN` で開いた PR はほかのワークフローを起動しないため、更新 PR 自身の
  `check` CI が走らないのに対し、App のインストールトークンにはその制約がありません。App の身元で PR
  を開くことこそが、AI の出力に決定論的なゲートを実際に走らせる鍵です。

`concurrency` グループ（`cancel-in-progress: false`）で直列化し、手動起動が夜間実行と競合しないように
します。また `claude-review.yml` と同様に `timeout-minutes` の早期打ち切り上限を設けます。

### 2. 更新の契約（追跡されるプロンプトファイル）

`.github/daily-doc-refresh-prompt.md`（`.github/claude-review-prompt.md` に倣ったもの）を追跡下に置き、
アクションが実行する正典の指示とします。作業の範囲を厳密に定めます。

- **何を照合するか。** 前回実行以降にマージされた PR に対する BE 項目の `Status` / `Progress` /
  `Implementing PR`、`docs/architecture.md#implementation-status`、そして現在の挙動に対する
  `DESIGN.md` / `docs/architecture.md` の文章（BE-0113）です。`Status` を切り替えたり索引に影響する
  項目を編集したりしたときは、同じ変更のなかで `make roadmap-index` を実行し、決定論的な索引を
  再生成します。
- **バイリンガルの規則。** 書き起こす日本語はすべて `japanese-tech-writing` スキルと敬体
  （ですます調）に従い、英日のドキュメントは同時に更新します（作業合意の規則）。
- **越えてはならない範囲。** 編集するのは `roadmaps/**`、`docs/**`、`DESIGN.md`、トップレベルの
  `README*` / `CLAUDE.md` **のみ**で、プロダクトコード（`bajutsu/`、`BajutsuKit/`、テスト、設定、
  デモ）には**一切触れません**。PR を開く前に差分へパス許可リストを適用して強制します。これは
  ideation スキルの「起草のみ、実装はしない」という境界に倣ったものです。
- **保守的であること。** ドリフトの具体的な証拠（マージ済みの PR、出荷された機能）がある箇所だけを
  更新案とします。確信が持てないときは文書を変えずに残し、推測するのではなく PR 本文に不確かさを
  記します。判断するのはエージェントではなくレビュアーです。

### 3. PR を開く前に決定論的なゲートで検証する

エージェントがファイルを編集したあと、ワークフローはジョブ内で `make check` を実行します。これに
より、AI の出力を人間の変更と同じ基準（フォーマット、lint、roadmap-format、索引ドリフト、typecheck、
テスト）に保ちます。ゲートが赤でも PR を開くこと自体は止めません（いずれにせよ**ドラフト**で開き
ます）。ただし結果を PR 本文に載せ、そのまま取り込める状態かどうかを人間がすぐ判断できるようにし
ます。この PR は通常の `check` CI の対象にもなります（だからこそ App トークンが必要です。項目 1）。

### 4. 1 本の更新用ドラフト PR、人間がマージ、冪等

- **冪等。** 照合しても差分が出なければ、ジョブは PR を開いたり触ったりせずに終了します。静かな週に
  毎日ノイズを出しません。
- **1 本の更新用 PR。** 固定ブランチ（例: `chore/daily-doc-refresh`）に push し、オープン中の PR が
  あればそれを再利用します（ブランチを force-update）。毎日新しい PR を開くのではなく、生きた PR を
  1 本に保つほうがレビューしやすいからです。
- **常にドラフト、自動マージしない。** PR は `--draft` で開きます。人間がレビューし、ready にして
  マージするのは人間**だけ**です。このブランチに自動マージはありません。PR 本文には、何が変わり、
  なぜ変わったか（どのマージ済み PR や機能が各編集の根拠か）、そして `make check` の結果をまとめ
  ます。

### プライムディレクティブとの整合

LLM は純粋に*起草*の経路でのみ使います。`record` がシナリオを起草するのとまったく同様に文書の更新を
下書きし、その出力は人間がレビューしてマージしなければならないドラフト PR です。`run` や
`required_status_checks` に LLM 呼び出しは一切足しません。決定論的な `check` が唯一のマージ裁定者で
あり続け、判断する人間が唯一の裁定者です。ワークフローのパス許可リストにより、ドライバ・ランナー・
テストには触れないため、決定論やアプリ非依存の中核に影響を与えることはできません。

## 検討した代替案

- **決定論のみの更新（LLM なし）。** 索引の再生成、リンクの再検査、トラッキング Issue の URL 補正を
  定期実行する案です。*全体の*答えとしては却下します。これらの部分はすでに `make check` /
  `make roadmap-index` / BE-0096 でカバーされており、そもそも陳腐化する箇所ではありません。本項目が
  対象とするドリフト（`Status`/`Progress` の遅れ、文章と挙動のずれ）は本質的に意味的で、判断なしには
  照合できません。
- **更新を `main` に直接コミットする。** 却下します。人間をループから外し（プライムディレクティブ 1
  ——ここでは人間が裁定者）、AI が起草した文章のレビューを飛ばします。ドラフト PR にすることこそが
  眼目です。
- **新規ワークフローではなく自動レビュー（BE-0203）を拡張する。** 形が違います。BE-0203 は既存の PR
  を*レビュー*してコメントするのに対し、本項目は定期的に PR をゼロから*起草*します。プロバイダ選択の
  ロジック（項目 1）を再利用するのは適切な共有ですが、2 つを 1 つのジョブに統合すると、無関係な
  トリガと範囲を 1 本のワークフローに詰め込みすぎます。
- **オンデマンドのみ（cron なしの `workflow_dispatch`）。** 追加のトリガとしては残しますが、価値は
  無人の毎日の周期——「常に最新」——にあり、オンデマンドのボタンだけではそれを実現できません。
- **毎日新しい PR を開く。** 却下します。レビューの滞留と古い重複を生みます。force-update する
  ドラフト PR を 1 本に保つほうが追いやすくなります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 2 つの資格情報による休止ゲート・concurrency・timeout を備えた定期ワークフロー `daily-doc-refresh.yml`（項目 1）
- [ ] 範囲・バイリンガル・保守性の規則を定めた更新契約 `.github/daily-doc-refresh-prompt.md`（項目 2）
- [ ] ジョブ内 `make check` 検証と、その結果の PR 本文への反映（項目 3）
- [ ] App トークン経由の、1 本・冪等・常にドラフト・人間マージの PR（項目 4）

## 参考

`.github/workflows/claude-review.yml`（BE-0203 —— AI プロバイダ選択と Claude Code アクションの使い方。
項目 1 が再利用するパターン）、`.github/workflows/roadmap-id.yml`（BE-0089）と
`.github/workflows/roadmap-drift-check.yml`
（[BE-0149](../BE-0149-roadmap-placeholder-format-guardrail/BE-0149-roadmap-placeholder-format-guardrail-ja.md)）
（bot が開いた PR に自身の `check` CI を走らせる自動化 App トークン）、
[BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity-ja.md)
（本項目が補完する決定論的なリンク整合ガード）、
[BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md)
（DESIGN.md / architecture.md を挙動と揃える —— 本項目が自動で促すレビュー時の規範）、
[BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)
（ベンダー非依存の AI プロバイダ）、`japanese-tech-writing` スキル（日本語出力が従う敬体の規範）。
