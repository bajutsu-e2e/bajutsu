[English](BE-XXXX-prose-refresh.md) · **日本語**

# BE-XXXX — Claude review の言い回しのみの指摘を、コード PR の CI サイクルを回さずに解消する定期ワークフローを追加する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-prose-refresh-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | コントリビューターワークフロー |
| 関連 | [BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md)、[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は、`roadmap-refresh` と `docs-refresh`（[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md)）
に並ぶ 3 本目の定期ワークフローとして、**prose-refresh** を追加します。`docs/` 配下と各ロードマップ項目
の本文を、英日両言語で `document-writing` / `english-document-writing` / `japanese-document-writing`
という各スキルの規範と照合し、直す価値のある言い回しが見つかったときだけ自分のドラフト PR を開くワーク
フローです。これに合わせて自動レビュー
（[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md)）は言い回しだけの指摘
に専用の印を付けます。`pr-followup` はコード側のファイルも変更している pull request 上で、その印が付いた
指摘を返信とスレッド解決だけで済ませ、修正コミットは作りません。prose-refresh が次回の実行でその
pull request のマージ後に拾い直すことを前提にした運用です。

## 動機

BE-0203 は pull request の全差分を push ごとに読み直し、インラインの指摘を投稿します。そのレンズの
1 つが日本語の文章品質です。バイリンガルなドキュメントという house convention のもとでは、英語の
`docs/*.md` とロードマップの英語本文も同じ言い回し品質の基準を課されるべきですが、今日のレビュアーに
対になるレンズはありません。本項目は、すでにある日本語のレンズに並べて、そのレンズを追加します。
どちらも純粋な言い回しの提案であって、
正しさの欠陥ではありません。しかし今日、この指摘を直すとなると同じ pull request にコミットを push
するしかなく、その push は挙動には何も影響しない文章の修正のために CI 一式（`ci.yml` と、変更ファイルが
選ぶ end-to-end のレーン）を丸ごと再実行させます。レビュアーは push ごとに全差分を読み直すため、
言い回しの指摘は一度にまとまって出るとは限らず、pull request の寿命のなかで時間差をおいて現れます。
そのため、貢献者はこのコストを 1 つの pull request につき何度も負うことになります。

このリポジトリはすでに、文章の品質を CI ゲートではなくレビュー時の規範として扱っています。`DESIGN.md`
の整合を扱う理由と同じです。言い回しの当否は意味的な判断を要するため、ゲートで判定させれば大規模言語
モデル（LLM）が `run`/CI の合否を左右してしまいます（プライムディレクティブ 1）。`document-writing` スキルの
規範は textlint で検証できますが、既存のロードマップの文章群に対してそれをゼロ件まで通すことは求めて
いません。[BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md) も同じ
根拠で、設計文書の整合を `make check` の外に置いています。つまり pull request 上の言い回しの指摘は、
もともとマージ前に直す義務のない助言です。足りていないのは、後回しにした指摘の行き先です。今は後回し
にすると、それを後で拾い直す仕組みが何もないため、指摘そのものが失われます。

[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) は、「最新さの維持は
意味的な判断を要し、目が届かなくなると静かに陳腐化していく」という隣接する問題を、すでに解決済みです。
対象は BE 項目の `Status` / `Progress` のマージ済み PR とのずれ、そして `docs/` / `DESIGN.md` の文章と
出荷済みの挙動とのずれという 2 種類のドリフトです。言い回しの品質も、同じ形をした 3 種目のドリフトです。
人間と同等の判断を要し、決定論的なゲートでは照合できず、貢献者が次の作業に移った瞬間から同じように
陳腐化していきます。BE-0222 の定期リフレッシュの型は、定期的に起草し `make check` でゲートしたうえで、
1 本のドラフト PR を開きます。マージするのは人間だけです。この型を再利用すれば、BE-0222 自身の穴を
塞いだのと同じやり方でこの穴も塞げます。新しい問題のために新しい仕組みを発明する必要はありません。

## 詳細設計

### 共有された `refresh.yml` を呼ぶ 3 本目の薄いワークフロー、prose-refresh

新しいワークフロー `.github/workflows/prose-refresh.yml` は、`roadmap-refresh.yml` や
`docs-refresh.yml` とまったく同じ形で、再利用可能な [`refresh.yml`](../../.github/workflows/refresh.yml)
を呼び出します。3 本が共有するのは次の要素です。

- 2 つの資格情報による休止ゲート（AI プロバイダと、`AUTOMATION_BOT_APP_ID` / `AUTOMATION_BOT_PRIVATE_KEY` の自動化 App）
- App トークンでの `main` の checkout
- パス許可リストで境界を定めた AI 起草ステップ
- ジョブ内の `make check`
- 冪等で上書き防止ガード付きの、常にドラフトの更新用 PR

`refresh.yml` 自体には何も変更を加えません。`prose-refresh.yml` は `docs-refresh.yml` がすでに示して
いる形のまま、自分の `label` / `branch`（`chore/prose-refresh`）/ `contract` / `title` / `allow` /
`allowed_tools` だけを渡します。

契約ファイル `.github/prose-refresh-prompt.md` は、AI 起草者に対して `docs/`（英語側と `docs/ja/` の
対訳）と各ロードマップ項目の本文を読み直すよう指示します。対象から除くのは `<!-- BE-METADATA -->`
ブロックと H1、`Progress` チェックリストで、これらは `roadmap-refresh` の担当のまま残します。読み直す
先は `document-writing` / `english-document-writing` / `japanese-document-writing` の各規範であり、
具体的な違反を見つけたときだけファイルを直すよう指示します。継承するのは、`docs-refresh-prompt.md` が
すでに定めている**保守性の規則**です。この規則は、規範への違反が具体的に見つかった箇所だけを更新案と
し、確信が持てなければ推測で書き換えず文章を残すよう求めます。パス許可リストは `docs/**`、
トップレベルの `DESIGN.md`、そして `roadmaps/**/*.md` / `roadmaps/**/*-ja.md` です。これは
`docs-refresh` の担当領域（すでに `DESIGN.md` を含みます）と `roadmap-refresh` の担当領域を、
本文だけに絞って合わせたものです。`docs-refresh-prompt.md` と同様、トップレベルの `README*` と
`CLAUDE.md` は対象外とします。これらは AI 起草者自身が縛られるプライムディレクティブを定めた契約面
であり、人間が書くものとして残します。

### なぜ既存の 2 つの契約を拡張せず、3 本目のワークフローにするか

[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) は、ドリフトの
種類ごとにワークフローを分ける理由を、対象ツリーの違いだけでなく作業の質の違いとしてすでに述べて
います。異なる種類のレビューを 1 本の PR に詰め込むと、レビュアーは速く機械的な変更と、遅く慎重な
変更とを一緒に判断させられ、両者の失敗モードと周期まで結合してしまいます。言い回しの品質審査は、
まさにその遅く慎重な種類の判断であり、しかも `docs/**` と `roadmaps/**` という既存の 2 つのツリー
の両方にまたがるため、どちらか一方のツリーだけを担当する既存の契約には収まりません。
`docs-refresh-prompt.md` に折り込めば、ロードマップの本文は照合されないまま残ります。
`roadmap-refresh-prompt.md` に折り込めば、`Status` / `Progress` の照合というほぼ機械的な現在の性質が、
毎回の実行で重い言い回しの判断によって薄められてしまいます。3 本目の、対象を絞った契約を用意すれば、
3 つそれぞれのリフレッシュを単独で評価できる状態を保てます。これは
[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) が
roadmap-refresh と docs-refresh を分けた理由と同じ論理です。

### 既存の 2 つのリフレッシュとの、対象ツリーの重なりをどう扱うか

prose-refresh の許可リストは、既存 2 つのリフレッシュの対象ツリーと本当に重なります。`docs/**` は
docs-refresh と、ロードマップの本文は roadmap-refresh と重なります。これは新しい事態です。
`roadmap-refresh.yml` と `docs-refresh.yml` はすでに、日次 cron の時刻をずらす（UTC 17:07 と
17:37）ことでランナーの競合を避けており、`prose-refresh.yml` も同じ発想で 3 つ目の時刻をずらして
走らせます。これで**ランナー**の競合は避けられますが、**内容**の競合は避けられません。docs-refresh
が挙動とのずれのために書き直す段落を、同じ日に prose-refresh が言い回しのために書き直すことはあり
えます。両者はそれぞれ自分のブランチから、それぞれ自分のドラフト PR を開きます。同じ行に両方が触れた
場合、2 本のドラフト PR をレビューする人間がどちらかを先にマージし、もう一方のドラフト PR は通常の
rebase でそのマージを取り込む必要が生じます。これは、人間がその段落を編集している最中にリフレッシュ
の PR がすでに開いていた場合に今日でも起きる競合と同じ種類のものであり、本項目が新しく持ち込む失敗
モードではありません。時々発生するこの rebase を受け入れることが、各リフレッシュの契約を狭く保ち、
それぞれを単独でレビューできる状態に保つ（前節で論じた性質）ための代償です。マージするのは人間が
それぞれのドラフト PR を個別にレビューしたうえでなので、誤ったマージが起きる危険はありません。

### 自動レビュー側で、言い回しだけの指摘に印を付ける

[`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) は、今日は日本語の文章品質
レンズだけを名指ししており、対になる英語のレンズはありません。あるのは別物の、バイリンガルなドキュメント
の同期を見るレンズと、用語の揺れを見るレンズだけで、どちらも英語の言い回し品質を審査するものではあり
ません。本項目は、この足りないレンズを追加します。既存の日本語のレンズが `japanese-document-writing`
に照らして日本語の文章を審査しているのと同じように、新しい house convention の項を立て、英語の
`docs/*.md` とロードマップ本文を `document-writing` / `english-document-writing` の各スキルに照らして
審査させます。[`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) はすでに、
すべてのインライン指摘に `(non-blocking)` という飾りを付けています（BE-0203）。本項目はここに、
もう 1 つの飾り、たとえば `(non-blocking, prose)` を追加します。付けるのは 2 つの言い回し品質レンズ
から出た指摘だけであり、コードに対する設計・セキュリティ・正しさの `suggestion` には付けません。
この印があることで、`pr-followup` は指摘の本文を読み直さずに、言い回しだけの指摘かどうかを機械的に
見分けられます。

### `pr-followup` での後回しの扱い

[`.agent-workflows/pr-followup/workflow.md`](../../.agent-workflows/pr-followup/workflow.md) の
ステップ 3 に、返信して解決するという通常のループの例外を 1 つ加えます。`docs/` やロードマップ本文
以外のファイルも変更している pull request では、`prose` の印が付いた指摘に返信し、スレッドを解決
しますが、修正コミットは作りません。返信では、次回の prose-refresh の実行が、この pull request の
マージ後にその指摘を拾い直すと述べます。レビュー対象の pull request 自体がドキュメントのみである
場合は、`CLAUDE.md` が定める軽量な Ready for review の経路をすでに通っており CI のコストも低いため、
そこでは今までと同じくその場で直します。返信の内容は「コミット X で直しました」から「次回の定期
リフレッシュに委ねます」に変わりますが、指摘に答える人間の義務が消えるわけではありません。消えるのは、
この特定の pull request の中で直す義務だけです。

後回しにするとは、指摘の記録を prose-refresh に引き渡すことではなく、同じ違反を prose-refresh が
自力で拾い直すことに委ねるという意味です。prose-refresh は、保存済みの一覧を再生するのではなく、
毎回の実行で指摘を独立に導き出します。これは docs-refresh が今日すでに採っている方式と同じです。
この記録管理こそ、後述する「検討した代替案」の常時同期の companion PR が必要とする追加の状態であり、
本項目がその代替案を却下する理由の 1 つです。これは見落としではなく、
意図した賭けです。`prose` の印が付いた指摘は、そもそも prose-refresh 自身が適用するのと同じ
`document-writing` / `english-document-writing` / `japanese-document-writing` の規範への具体的な
違反であり、pull request のマージから次回の prose-refresh の実行までの間に、その文章は変わりません。
つまり同じ判断を同じ入力に適用するのですから、同じ結論に達しやすいはずです。**保守性の規則**
（「確信が持てなければ推測で書き換えず文章を残す」）は指摘を作り出すことを戒めるものであって、
本物の指摘を見落とすことを促すものではありません。それでも、AI が起草するリフレッシュには決定論的な
保証がなく、この賭けが外れる場合もあります。後回しにした特定の言い回しの指摘を見落とすわけにはいかない
場合、元の pull request 内で直す道も残っています。後回しにする対象は、そもそも助言であり非ブロッキング
だった指摘に限られます。

### プライムディレクティブとの整合

LLM を使うのは起草の経路だけであり、これは
[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) がすでに確立した
形と同じです。作業ツリーへの編集を起草するだけで、その結果のドラフト PR は人間がレビューしてマージ
しなければなりません。`run` や必須のステータスチェックに LLM 呼び出しを一切足しません。決定論的な
`check` が唯一のマージ裁定者であり続け、起草された言い回しを判断するのは人間だけです。パス許可リスト
により、prose-refresh は `bajutsu/`、`BajutsuKit/`、テスト、設定、デモのいずれにも触れないため、
決定論やアプリ非依存の中核に影響を与えることはできません。

## 検討した代替案

- **元の pull request を force-push で追随させる、常時同期の companion PR。** 定期実行してマージ後に
  拾うのではなく、`claude-review.yml` と同じ `pull_request` イベントで動くジョブを用意する案です。
  push のたびに、そのジョブが元の pull request の最新の head から companion branch を再構築し、各指摘
  の提案を適用して force-push します。元の pull request がまだ開いている間もほぼリアルタイムで追随でき
  ますが、本項目の主たる設計としては却下します。理由は 3 つです。コントリビュータのブランチから派生
  したブランチへ push する、新しい資格情報付きのジョブが要ります（`main` か自分の固定ブランチにしか
  push しない今の自動化 App より信頼範囲が大きくなります）。push ごとに再構築と force-push のサイクル
  が走ります。そして、元の pull request が指摘の対象だった行そのものを編集したとき、指摘ごとの陳腐化
  検出も要ります。prose-refresh のマージ後方式にはこれらの失敗モードがどれもなく、代わりに修正が届く
  のは元の pull request が開いている間ではなく、マージしたあとになります。この遅れが実運用で長すぎる
  とわかった場合、この代替案が次に検討すべき自然な拡張になります。
- **`roadmap-refresh-prompt.md` と `docs-refresh-prompt.md` に言い回し品質のレンズを足す形で、3 本目
  のワークフローを作らない。** 上の「なぜ 3 本目のワークフローにするか」で述べた理由により却下します。
  言い回しの審査はどちらの契約の現在の仕事（機械的な照合、または挙動とのずれの照合）とも異なる、
  より重い判断であり、しかもどちらか一方の契約が担当するツリーだけには収まりません。
- **オンデマンドのトリガのみ（pull request への `@claude prose-pr` というコメント）。** 却下します。
  本項目がなくそうとしている、後回しにした指摘を人手で拾い直す手間をそのまま残してしまいます。人間が
  適用を頼むのを忘れないという前提が残るからです。
- **言い回しだけの指摘に印を付けず、その場で直し続ける。** 現状であり、本項目の動機そのものです。
  言い回しの修正のたびに、それを指摘した pull request の CI を丸ごと再実行するコストが残ります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `prose-refresh.yml` — 共有された `refresh.yml` を呼ぶ 3 本目の薄いワークフロー。専用の契約・
  ブランチ・更新用ドラフト PR を持つ
- [ ] `.github/prose-refresh-prompt.md` — `docs/`、`DESIGN.md`、ロードマップ本文だけにパス許可リストを
  絞った言い回し品質の契約。`README*` / `CLAUDE.md` と、各ロードマップ項目のメタデータ・H1・`Progress`
  ブロックは対象外
- [ ] `.github/claude-review-prompt.md` — 既存の日本語のレンズに並ぶ新しい英語の文章品質レンズと、
  両方に付ける `(non-blocking, prose)` の飾り
- [ ] `.agent-workflows/pr-followup/workflow.md` — コード側も変更している pull request での後回し
  例外をステップ 3 に追加
- [ ] `docs/ai-development.md` とその対訳 `docs/ja/ai-development.md` — 「すべてのレビュアーの指摘を
  同じように扱う」という原則にこの例外を追記
- [ ] この例外を指す短い `CLAUDE.md` の 1 行

### ログ

## 参考

[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md) は、本項目が後回しに
する、日本語の文章品質レンズとバイリンガルなドキュメントという house convention を持つ自動レビューです。
[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) は、本項目が 3 本目の
呼び出し側として再利用する、定期リフレッシュの設計と `refresh.yml` の仕組みです。
[BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md) は、本項目の動機が
根拠とする、文章の判断をレビュー時の規範に留めゲートとはしないという前例です。適用する言い回し品質の
規範は、`document-writing` / `english-document-writing` / `japanese-document-writing` の各スキルです。
