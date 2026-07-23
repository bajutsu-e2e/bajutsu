[English](BE-0203-claude-code-pr-review.md) · **日本語**

# BE-0203 — プルリクエストの自動コードレビューを Claude Code で行う

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0203](BE-0203-claude-code-pr-review-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0203") |
| 実装 PR | [#807](https://github.com/bajutsu-e2e/bajutsu/pull/807), [#915](https://github.com/bajutsu-e2e/bajutsu/pull/915), [#916](https://github.com/bajutsu-e2e/bajutsu/pull/916), [#1160](https://github.com/bajutsu-e2e/bajutsu/pull/1160) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

現在、本リポジトリへのプルリクエストは **GitHub Copilot** が自動でレビューしています。プルリクエストが
開かれるとインラインコメントを付け、プッシュのたびに再レビューします。この項目は、そのレビュアーを
**Claude Code** に置き換えます。GitHub Actions のワークフローをすべてのプルリクエストに対して走らせ、
オープン時と各プッシュ時に自動で起動して、アクションのネイティブなインラインコメントツールで GitHub の
提案変更（suggested change）ブロックを含む**行単位のインラインコメント**を投稿します。（当初はトップレベルの
要約も投稿していましたが、のちに取りやめました。詳細は進捗ログを参照してください。再実行のたびに新しい要約を
出すと、古い要約が残って PR 上で矛盾するためです。）Copilot に対する利点は、Claude Code が
**このリポジトリ自身の契約** に照らしてレビューできる点にあります。三つの
[prime directive](../../CLAUDE.md#prime-directives-do-not-violate)、docstring 規約、ドキュメントの二言語ルール、
BE ID のライフサイクルといった、汎用のレビュアーには知りようのない事項です。

この項目が決して越えない一線は **prime directive 1** です。自動レビューは**助言**にとどまります。ほかの
どのボットのコメントとも同じく、人間が判断材料として重み付けするコメントを投稿するだけで、**必須の
ステータスチェックには決してならず**、`run` / CI の**判定**の上に乗ることもありません。プルリクエストを
マージできるかどうかを決める唯一の裁定者は、これまでどおり決定論的な `check` と `E2E` のゲートです。この項目が
加えるのはレビュアーであって、審判ではありません。

## 動機

- **ユーザーは PR レビュアーとして Copilot ではなく Claude Code を使いたい。** これが直接の要望です。
  コントリビューターがすでに頼りにしている体験（オープン時の自動レビュー、プッシュ時の再レビュー、
  インラインコメント）を保ったまま、自動レビューの表面を Copilot から Claude Code へ移します。
- **リポジトリを理解したレビュアーは、汎用のレビュアーが拾えないものを拾える。** Copilot は一般的な
  コード品質のヒューリスティックに照らしてレビューするので、Bajutsu の prime directive を知りません。
  したがって、このプロジェクトが最も気にする種類の誤りを検出できません。`run` / CI の判定経路に LLM 呼び出しが
  忍び込むこと、条件待ちにすべきところの固定 `sleep`、「最初にマッチしたものをタップする」曖昧なセレクター、
  `targets.<name>` の設定に置くべきアプリごとの差異のハードコード、片方の言語だけを変えたドキュメント化済みの
  挙動変更、BE 項目へのリンクを欠いたロードマップの PR などです。リポジトリ独自のプロンプトでレビューする
  Claude Code は、ランナーと同じ方向に力をかけます。
- **部品はすでに揃っており、この項目はそれを PR に配線するだけです。**
  Claude Code 組み込みの `code-review` スキルはすでに指摘を生成し、`--comment` を付ければ**それらを
  インラインの PR コメントとして投稿**します。[`implement-be`](../../.claude/skills/implement-be/SKILL.md) は、
  このリポジトリが信頼するレビューの観点（握りつぶしの検出は「大きく失敗する」原則、strict な `mypy` のもとでの
  型設計、新しいロジックのテスト網羅）をすでに定式化しています。ただし現状では、これらは PR が存在する前の
  **セッション内、著者側**でしか走りません。人間が書いた PR にも、外部から寄せられた PR にも、再プッシュにも
  届かないのです。この項目は、同じレビューを、レビュアーが期待される場所である PR そのものの上で走らせます。
- **AI プロバイダーはすでにベンダー中立です。**
  [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) が Tier-1 の AI 経路に
  ベンダー中立なバックエンドを与え、[BE-0053](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md)
  （Amazon Bedrock）と [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md)（OAuth）が
  Anthropic のアプリケーションプログラミングインターフェース（API）直叩きの代替として用意されています。レビューの
  ワークフローは、一つのベンダーを固定するのではなく、この同じプロバイダー選択を通じて認証するので、新しい
  資格情報の仕組みを持ち込みません。

## 詳細設計

作業は、新しい助言用の Actions ワークフローとそのレビュープロンプト、Copilot からの移行のドキュメント化、
そして二言語のドキュメントです。決定論的なゲート、ドライバー、ランナーには変更を加えません。

1. **レビューのワークフロー。** 新規の `.github/workflows/claude-review.yml` を追加します。
   `pull_request` の `types: [opened, synchronize, reopened]` で起動し（オープン時の自動レビュー、各プッシュ時の再レビュー。
   Copilot と同じ）、Anthropic 公式の `claude-code-action`（本リポジトリの他のすべてのアクションと同様、
   コミットの完全な SHA に固定する）を実行して、組み込みの **`/code-review --comment`** を呼び出し、指摘を
   **インラインの PR コメント**として着地させます。権限はこの表面が必要とする最小限にとどめます。
   `pull-requests: write`（レビューコメントの投稿）と `contents: read`（差分の読み取り）だけで、それ以外は
   与えません。`ci.yml` にならって `concurrency` ブロック（`group: claude-review-${{ github.ref }}`、
   `cancel-in-progress: true`）を持たせ、短時間に連続するプッシュでは古いレビューを積み重ねずに打ち切って置き換えます。

2. **助言であってゲートではない、という prime directive のガードレール。** レビューを判定経路から外すために、
   次の二つの性質をどちらも明示します。
   - **必須のステータスチェックにしない。** そのジョブの `name:` を、`main` のブランチ保護の
     `required_status_checks`（`check` / `E2E` / `require two approvals for BE proposals` を固定しているルールセット）
     に**加えません**。PR は決定論的なゲートだけでマージでき、レビューがそれを妨げることはありません。
   - **ワークフロー自身の結果を、指摘の有無から切り離す。** 指摘を*見つけた*レビューは、失敗したチェックではなく
     成功したレビューです。コメントを投稿したかどうかにかかわらず、そのステップは `0` で終了します。ジョブが
     赤くなるのはインフラの失敗（アクション自体のエラー）のときだけで、「レビュアーがコードを気に入らなかった」
     ときではありません。これが、レビューをコメントの表面にとどめ、LLM の判定をこっそり紛れ込ませないための
     仕組みです。

3. **Copilot との機能同等性。** Copilot のレビュー機能のそれぞれが、ここでは具体的な部品に対応します。

   | Copilot の機能 | この項目 |
   |---|---|
   | PR オープン時の自動レビュー | `pull_request` の `types: [opened]` |
   | 各プッシュ時の再レビュー | `pull_request` の `types: [synchronize]` |
   | 行単位のインラインコメント | `/code-review --comment`（インラインの PR コメントを投稿） |
   | 提案変更（ワンクリック適用） | 具体的で機械的な修正が当てはまる箇所で、レビュープロンプトが GitHub の ```` ```suggestion ```` ブロックを求める |
   | レビューの要約 | 当初はインラインの指摘と並べてトップレベルの要約コメントを投稿していたが、のちに取りやめた（進捗ログを参照）。再実行のたびに新しい要約を出すと、古い要約が残って矛盾するため |
   | 手動での再レビュー | 自動起動に**加えて**、任意で使える `@claude review` メンションの経路（下記） |

4. **手動での再レビュー（任意・追加のみ）。** 自動起動とは別に、`issue_comment` /
   `pull_request_review_comment` の起動を用意し、コントリビューターが `@claude review` と書く（あるいはスレッドに
   返信する）ことで、新しいレビューや特定コメントへの追随を依頼できるようにします。これは
   [PR レビューコメントへの返信ルール](../../docs/ai-development.md#responding-to-pr-review-comments)が AI レビュアーに
   すでに想定している作法と同じものです。項目 1 に純粋に足すだけで、既定は自動レビューであり、メンションは
   要りません。

5. **リポジトリ仕様のレビュープロンプト。** コミット済みのプロンプト（たとえば
   `.github/claude-review-prompt.md`、またはアクションが `/code-review` に渡す `claude_args`）で、レビューを
   *このリポジトリの*契約に向けます。これにより、汎用のレビュアーが見逃すものを拾えます。
   - 三つの **prime directive**。`run` / CI の判定に届く LLM 呼び出し、条件待ちにすべき箇所の固定 `sleep`、
     曖昧なセレクターの「最初のマッチをタップ」、`targets.<name>` の設定の外にハードコードされたアプリごとの
     つまみを検出します。
   - `implement-be` がすでに信頼している**レビューの観点**。握りつぶしたエラーや弱いフォールバック（決定論とは
     大きく失敗すること）、strict な `mypy` のもとでの型不変条件、新しいロジックが実際にテストで覆われているか、
     です。
   - ゲートが判定できない**プロジェクトの慣行**。二言語のドキュメントが両方の言語で更新されているか、
     [docstring 規約](../../docs/ai-development.md#code-documentation-comments-docstrings--be-0065)、ロードマップの PR が
     BE 項目を双方向にリンクしているか、`## Progress` が最新に保たれているか、です。

   このプロンプトはレビューの*補助*にとどまります。レビュアーが何を見るかを形づくるだけで、何がマージされるかは
   決して左右しません。

6. **資格情報のスコープとフォークの安全性。** ワークフローは、
   [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) がすでに選ぶプロバイダー
   （`ANTHROPIC_API_KEY`、OAuth トークン、または Bedrock 向けの Amazon Web Services（AWS）資格情報）を通じて認証し、
   その資格情報は Environment でスコープした Actions のシークレットとして保管して、無関係なジョブから読めないように
   します。フォークからの素の `pull_request` イベントは（GitHub の設計上）シークレットを露出しないため、自動レビューは
   [CLAUDE.md](../../CLAUDE.md) がすでに前提とする同一リポジトリの `claude/<topic>` / `<user>/<topic>` ブランチを
   対象とし、フォークの PR は代わりにメンテナーがオンデマンドでレビューします。この項目はあえて
   `pull_request_target`（信頼できないフォークのコードに対してシークレット付きで実行する）を**使いません**。この
   トレードオフは*代替案*に記録します。

7. **Copilot からの移行。並行運用してから切り替える（ドキュメント化された手動の切り替え）。** 切り替えは、
   Copilot を退役させる前に品質を確かめられるよう段階的に進めます。
   - **フェーズ A — 並行運用。** Copilot のレビューを有効にしたまま、このワークフローを投入します。両方の
     レビュアーがすべての PR にコメントし、どちらもゲートにはなりません。
   - **フェーズ B — 比較。** 数件の PR にわたって、Claude Code のレビューを Copilot のそれと比べます。信号、
     ノイズ、偽陽性、そしてリポジトリを理解したチェックが割に合うかどうかを見ます。
   - **フェーズ C — 切り替え。** メンテナーが、リポジトリまたは組織の設定で **Copilot の自動レビューを無効に
     します**。これは**通常の PR では持ち運べないリポジトリ外の管理状態**であり、BE-0122 や BE-0089 がすでに
     指摘しているブランチ保護ルールセットの編集と同じ形をしています。したがって、この項目の差分が実行できるもの
     ではなく、明示的にドキュメント化された手動の手順です。切り替えが中途半端に終わらない（両方のレビュアーが
     いつまでも有効なまま残る）よう、ドキュメント（項目 8）に記録します。

8. **レビュアーをドキュメント化する（二言語）。** [`docs/ai-development.md`](../../docs/ai-development.md) の
   *Responding to PR review comments* 節（すでに「GitHub Copilot and other AI reviewers」に言及している）を更新し、
   **Claude Code** を*その*自動レビュアーとして名指しします。あわせて、自動レビューのワークフローを説明する短い
   小節を追加します。助言であること（必須チェックには決してならない）、何にコメントするか、`@claude review` の
   オンデマンド経路、フォークの制約、そして項目 7 の手動での Copilot 無効化手順です。
   [`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/) スキルに従って、
   [`docs/ja/ai-development.md`](../../docs/ja/ai-development.md) にも反映します。

9. **検証。** `actionlint`（すでに `make check` に入っている）が新しいワークフローの YAML を検証するので、
   追加の自動テストなしにゲートは緑のままです。レビューの*挙動*はライブの PR とプロバイダー呼び出しを要するため、
   ユニットテストにはできません。したがって検証は BE-0122 と同じ手動の形になります。テスト用の PR を開き、オープン
   時にインラインコメントと要約が自動で投稿されること、続くプッシュで再投稿されること、```` ```suggestion ```` ブロックが
   ワンクリック適用として描画されること、そしてそのレビューのチェックが必須チェックの一覧に**入っていない**
   （マージを妨げられない）ことを確認します。

## 検討した代替案

- **Copilot を維持する（何もしない）。** 却下します。Claude Code へ移すことが明示の要望であり、そもそも Copilot は
  prime directive に照らしたレビューが構造的にできません。このリポジトリが最も検出したい誤り（判定経路の LLM、
  固定 `sleep`、片方の言語だけのドキュメント変更）は、契約を知らないレビュアーには見えないからです。
- **Claude のレビューを必須のステータスチェックにする。** きっぱり却下します。LLM をマージ判定に載せることに
  なり、prime directive 1 に反します。レビューは助言にとどめ、裁定者は決定論的な `check` / `E2E` のゲートだけと
  します。これは、この項目全体を形づくる唯一の一線です。
- **著者側のレビューだけにとどめる（既存の `implement-be` 手順 7）。** 不十分として却下します。セッション内の
  `simplify` / `code-review` / pr-review-toolkit のパスは PR が存在する*前*に、しかもエージェント主導の変更に対して
  だけ走ります。人間が書いた PR にも、外部の PR にも、再プッシュにも届きません。まさにこれらは PR レビュアーが
  存在する理由そのものです。この項目は、その著者側のパスを置き換えるのではなく補います。
- **フォークの PR も自動レビューするために `pull_request_target` を使う。** 却下（保留）します。信頼できない
  フォークのコードの文脈でリポジトリのシークレット付きでワークフローを実行することになり、よく知られたトークン
  流出のリスクです。コントリビューションの形が同一リポジトリのトピックブランチであるこのリポジトリには見合いません。
  フォークの PR はメンテナーがオンデマンドで起動するレビューで扱い、外部フォークからの寄与が増えたときにだけ
  見直します。
- **レビュアーを自作する（専用スクリプトからプロバイダーの API を叩く）。** 却下します。公式の
  `claude-code-action` とリポジトリ自身の `/code-review --comment` スキルがすでにこれを行っており、専用スクリプトは
  スキルを二重化してそこから乖離していきます。スキルを再利用して、CI のレビュアーと著者側のレビュアーを一つの
  実装に保ちます。
- **インラインコメントの代わりに要約コメント一つだけにする。** Copilot からの後退として却下します。行単位の
  インラインコメント（と提案ブロック）はコントリビューターが頼りにする機能であり、`code-review --comment` が
  すでにそれをサポートしている以上、あえて減らして出す理由はありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 助言用のレビューワークフロー `.github/workflows/claude-review.yml` を追加する（自動起動、リポジトリの
      契約に照らして action ネイティブのツールでインラインコメント投稿、最小権限、concurrency）（項目 1）
- [x] 助言のガードレールを担保する。必須チェックにしない、結果を指摘から切り離す（項目 2）
- [x] Copilot 同等に到達する。インラインコメント、提案ブロック、要約（項目 3）。要約はのちに取りやめ
      （下記ログを参照）、インラインコメントと提案ブロックを残しました
- [x] 任意の `@claude review` オンデマンド経路を追加する（項目 4）
- [x] リポジトリ仕様のレビュープロンプト（prime directive、観点、プロジェクトの慣行）をコミットする（項目 5）
- [x] 資格情報を Environment でスコープし、フォークの制約をドキュメント化する（項目 6）
- [x] 手動の Copilot 無効化手順をドキュメント化する（項目 7、ドキュメント）。移行の実施（並行運用 → 比較 →
      切り替え）はマージ後の運用作業です
- [x] `docs/ai-development.md`（EN + JA）にレビュアーをドキュメント化する（項目 8）
- [x] ライブの PR で検証する（項目 9）。#807 でサブスクリプション（OAuth）プロバイダーを使って実施済みです。
      プッシュ時に自動レビューがインラインコメントを投稿し、`claude review` チェックは助言（非必須）です。
      レビューの挙動はライブの PR とプロバイダー呼び出しを要するため、決定論的なゲートには置けません
      （BE-0122 と同じ形）

ログ：

- 提案を起草。
- 助言用のレビューワークフロー、リポジトリ仕様のレビュープロンプト、二言語のドキュメントを出荷しました
  （項目 1〜8）。Claude Code サブスクリプション（OAuth）または GitHub OIDC 経由の Amazon Bedrock で認証し、
  `CLAUDE_CODE_OAUTH_TOKEN` あるいは `AWS_BEDROCK_ROLE_ARN` + `BEDROCK_MODEL_ID` が用意されるまでは
  green no-op に固定します。項目 7 の移行実施（リポジトリ／組織設定での Copilot 無効化）はマージ後の運用作業です。
  実装 PR: [#807](https://github.com/bajutsu-e2e/bajutsu/pull/807)。
- Bedrock に加えて Claude Code サブスクリプション（OAuth）のプロバイダーを追加しました（項目 6）。
  ワークフローは `CLAUDE_CODE_OAUTH_TOKEN` シークレットがあればそれを優先し、なければ OIDC 経由の
  Bedrock を使い、どちらもなければ green no-op のままにします。1 回の実行で有効なプロバイダーは一つだけです。
- レビュー（#807）を受けて堅牢化しました。オンデマンドのコメント経路は、コメントに `@claude review` を含むことと
  信頼された actor（OWNER/MEMBER/COLLABORATOR）であることの両方を要求するようにし、フォーク PR のシークレット露出の
  隙をふさぎました。Bedrock のゲートは `AWS_BEDROCK_ROLE_ARN` と `BEDROCK_MODEL_ID` の両方を要求するようにし
  （設定が半端な Environment は赤く失敗せず no-op のままにします）、ドキュメントには資格情報が設定されるまでは
  休眠する挙動を明記しました。
- ライブ検証（項目 9）で投稿機構の不一致が見つかり、修正しました。組み込みの `/code-review --comment` スキルは
  制限のない `gh`（Bash）で投稿しますが、`claude-code-action` はこれをサンドボックスで拒否します（最初のライブ実行は
  認証もレビューも成功したのに、21 件の権限拒否と "No buffered inline comments" で何も投稿されませんでした）。
  そこでインラインの指摘は action ネイティブの `mcp__github_inline_comment__create_inline_comment` ツールで、
  要約は狭くスコープした `gh pr comment` で投稿するようにし、対応する `claude_args --allowedTools` の許可リストを
  付けました（action 自身のレビューワークフローと同じ形です）。リポジトリ仕様の*契約*
  （`.github/claude-review-prompt.md`）は変えておらず、そのまま再利用します。移したのは投稿機構だけです。
  ライブ実行で OAuth（サブスクリプション）認証の成功とレビュー投稿を確認しました。
- ライブ実行で露見した concurrency の不備を修正しました。グループが一つで `cancel-in-progress: true` だったため、
  レビュー中に投稿されたコメント（ボットの返信やレビュアーのメモ）が実行中の自動レビューを打ち切り、赤い
  「cancelled」チェックを出していました。グループをイベント種別で分け、`pull_request` のときだけ
  cancel-in-progress するようにしたので、プッシュ同士は引き続き上書きしつつ、コメント起因の実行は独立します。
- レビューをさらに堅牢化しました。オンデマンドの checkout `ref` を修正し（`refs/pull/N/head` が要るのは
  `issue_comment` だけで、他のイベントはトップレベルの `pull_request.head.sha` を持ちます）、ボット名義
  （`github-actions[bot]`）で投稿されても Claude Code 由来だと分かるよう、レビューに自己識別を付けました。
- CodeQL の「特権コンテキストでの untrusted checkout」／TOCTOU アラートを消すため checkout を再設計しました。
  特権ジョブは untrusted な PR head ではなく、レビュー契約の正典があり信頼できる**デフォルトブランチ**を
  `persist-credentials: false` で checkout し、変更内容は `gh pr diff` で読みます。これによりレビュー契約
  （`.github/claude-review-prompt.md`）は常にデフォルトブランチから読まれ（どの PR でも同一で、コメント
  イベントでも解決できます）、PR が自身のレビュー規則を書き換えることはできません。
- 自動化ボットの PR で露見した抜けを修正しました。`claude-code-action` は許可リストにないボットやアプリを
  非人間の actor として拒否するため、リポジトリ自身の `bajutsu-automation-bot`（roadmap-refresh や
  docs-refresh など）が開いた PR は「Workflow initiated by non-human actor」でレビューが失敗し、
  レビューされていませんでした。ワークフローに `allowed_bots: "bajutsu-automation-bot"` を設定し、
  `'*'`（action は public リポジトリでは外部アプリに攻撃者制御のプロンプトで起動されうると警告します）ではなく、
  その信頼できる内部ボットだけを許可します。照合は大文字小文字を無視し末尾の `[bot]` を除くので、
  どちらの表記でも一致します。実装 PR: [#915](https://github.com/bajutsu-e2e/bajutsu/pull/915)。
- レビュープロンプト自体の観点を広げました（項目 5）。ゲートのパターン照合ベースの `ruff` チェックでは
  追えない意味論的・データフロー由来の脆弱性を見るセキュリティの観点、設計・技術的負債の観点の強化、
  ディスカッション認識の観点（まず `gh pr view --comments` を読み、他のレビュアーが既に指摘した点を
  繰り返さない）、実行可能な指摘には必ず具体的な変更を示すという要件、そして
  `japanese-tech-writing` スキルに沿った日本語の文章品質の観点を追加しました。実装 PR:
  [#916](https://github.com/bajutsu-e2e/bajutsu/pull/916)。
- トップレベルの要約コメントを取りやめ、インラインの指摘だけを投稿するようにしました。ジョブはプッシュの
  たびに再実行されるため、その都度新しい要約を出すと古い要約が残って PR 上で矛盾していました。ワークフローの
  プロンプトと `.github/claude-review-prompt.md` の契約から要約の指示を外し、`Bash(gh pr comment:*)` を
  action の `--allowedTools` から削除して、ツールを与えないことで規則を担保します。同じ変更で、指摘を再実行に
  分散させず一度のパスですべて挙げるよう（かつ変更していない既存行は omission として扱うよう）レビュアーを
  誘導し、作者が支払う修正と待機の往復を減らします。実装 PR:
  [#1160](https://github.com/bajutsu-e2e/bajutsu/pull/1160)。

## 参考

- Claude Code 組み込みの `code-review` スキル — この項目が補う著者側のレビューパス。組み込みスキルであり、
  [`.claude/skills`](../../.claude/skills)（リポジトリ側で定義したスキル群）の配下にはありません。リポジトリの
  [`implement-be`](../../.claude/skills/implement-be/SKILL.md) が著者側ですでに使っています（観点や
  pr-review-toolkit を伴うレビュー手順）。CI のワークフローはこのスキルを直接**呼び出しません**。同じリポジトリの
  契約に照らし、action ネイティブの `mcp__github_inline_comment__create_inline_comment` ツールでインライン指摘を投稿します。
- [`docs/ai-development.md`](../../docs/ai-development.md) — この項目が更新する *Responding to PR review comments*
  のルール（すでに AI レビュアーに言及）と、そこがならう必須ステータスチェック／管理状態の制約。
- [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) — 唯一のマージ裁定者であり続ける決定論的なゲート。
  このワークフローがならう `concurrency` の形。
- [`CLAUDE.md`](../../CLAUDE.md) — レビュープロンプト（項目 5）が符号化する三つの prime directive。
- [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)、
  [BE-0053](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md)、
  [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md) — ワークフローが認証を通す、
  ベンダー中立な AI プロバイダー。
- [BE-0122](../BE-0122-workflow-name-legibility/BE-0122-workflow-name-legibility.md)、
  [BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md) — Copilot 無効化手順
  （項目 7）がならう、リポジトリ外の管理状態のパターン（PR では持ち運べないルールセット／設定の編集）を持つ先行項目。
