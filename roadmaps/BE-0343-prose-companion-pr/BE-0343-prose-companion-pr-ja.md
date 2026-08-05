[English](BE-0343-prose-companion-pr.md) · **日本語**

# BE-0343 — Claude review の言い回しのみの指摘を、コード PR の CI サイクルを回さずに解消する companion PR ワークフローを追加する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0343](BE-0343-prose-companion-pr-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0343") |
| トピック | コントリビューターワークフロー |
| 関連 | [BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md)、[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は、自動レビュー（[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md)）
に **companion PR** という仕組みを追加します。対象は言い回しだけの指摘です。日本語の文章品質、
または本項目が新たに追加するその対になる英語の `docs/*.md` / ロードマップ本文レンズです。自動レビュー
がこの種の指摘を、コード側のファイルも変更している pull request 上に投稿すると、新しいワークフローが
その指摘自身の提案を pull request の現在の head を基点にした companion branch へ機械的に適用します。
そして、そのブランチを対象とする companion pull request を開くか更新します。元の pull request の
ブランチと CI サイクルは、
人間がこの小さな companion pull request をレビューしてマージするまで触れられません。マージされる
と、言い回しの修正は元のブランチへの通常の push として反映され、それ自体の安価な条件でレビューされ
ます。しかも、元の pull request がまだ開いている間に使えるので、マージ後を待つ必要はありません。
この仕組みは自前の LLM 呼び出しを持ちません。修正はレビュー時点で BE-0203 がすでに起草しているの
で、本項目はそれを適用して届けることだけを自動化します。

## 動機

BE-0203 は pull request の全差分を push ごとに読み直し、インラインの指摘を投稿します。そのレンズの
1 つが日本語の文章品質です。バイリンガルなドキュメントという house convention のもとでは、英語の
`docs/*.md` とロードマップの英語本文も同じ言い回し品質の基準を課されるべきですが、今日のレビュアーに
対になるレンズはありません。本項目は、すでにある日本語のレンズに並べて、そのレンズを追加します。
どちらも純粋な言い回しの提案であって、正しさの欠陥ではありません。しかし今日、この指摘を直すとなると
同じ pull request にコミットを push するしかなく、その push は挙動には何も影響しない文章の修正のため
に CI 一式（`ci.yml` と、変更ファイルが選ぶ end-to-end のレーン）を丸ごと再実行させます。レビュアーは
push ごとに全差分を読み直すため、言い回しの指摘は一度にまとまって出るとは限らず、pull request の寿命
のなかで時間差をおいて現れます。そのため、貢献者はこのコストを 1 つの pull request につき何度も負う
ことになります。

このリポジトリはすでに、文章の品質を CI ゲートではなくレビュー時の規範として扱っています。`DESIGN.md`
の整合を扱う理由と同じです。言い回しの当否は意味的な判断を要するため、ゲートで判定させれば大規模言語
モデル（LLM）が `run`/CI の合否を左右してしまいます（プライムディレクティブ 1）。`document-writing`
スキルの規範は textlint で検証できますが、既存のロードマップの文章群に対してそれをゼロ件まで通すこと
は求めていません。[BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md)
も同じ根拠で、設計文書の整合を `make check` の外に置いています。つまり pull request 上の言い回しの
指摘は、もともとマージ前に直す義務のない助言です。

この助言という位置づけこそ、修正を単純に待たせてはいけない理由です。言い回しの指摘を pull request の
レビューが終わるまで直さずに残すと、人間がまさに読んでレビューしている、そして承認しようとしている
その差分に、指摘はそのまま残ります。レビュアーは、pull request がマージされる前に修正済みの文章を
実際には目にしません。[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md)
の定期リフレッシュが他の種類のドリフトを解消しているやり方、つまりマージ**後**に修正を回すやり方を
採用すると、CI コストの問題を可視性の問題に置き換えるだけで、元の問題は解決しません。必要なのは、
元の pull request がまだ開いている間に、その pull request 自身のブランチと CI サイクルを経由せずに
修正を即座に適用する方法です。それ自体は小さく安価な条件でレビューできる companion pull request が、
まさにそれを実現します。

## 詳細設計

### 自動レビュー側で、言い回しだけの指摘に印を付ける

[`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) は、今日は日本語の文章品質
レンズだけを名指ししており、対になる英語のレンズはありません。あるのは別物の、バイリンガルなドキュメント
の同期を見るレンズと、用語の揺れを見るレンズだけで、どちらも英語の言い回し品質を審査するものではあり
ません。本項目は、この足りないレンズを追加します。既存の日本語のレンズが `japanese-document-writing`
に照らして日本語の文章を審査しているのと同じように、新しいハウスコンベンションの項を立て、英語の
`docs/*.md` とロードマップ本文を `document-writing` / `english-document-writing` の各スキルに照らして
審査させます。レビュアーはすでに、すべてのインライン指摘に `(non-blocking)` という飾りを付けています
（BE-0203）。本項目はここに、もう 1 つの飾り、たとえば `(non-blocking, prose)` を追加します。付ける
のは 2 つの言い回し品質レンズから出た指摘だけであり、コードに対する設計・セキュリティ・正しさの
`suggestion` には付けません。この印があることで、後述の companion PR ジョブは、指摘の内容を判断せず
に、言い回しだけの指摘かどうかを機械的に見分けられます。

### レビュー時点で機械的に適用される companion PR

新しいジョブを用意します。専用のワークフローでも、`claude-review.yml` 自身に `needs: review` で
順序付けた追加のジョブでもかまいません。`needs: review` で順序付けるのは、その push の指摘が投稿
された後にだけこのジョブが走るようにするためです。このジョブは、`claude-review.yml` 自身がすでに
重複防止に使っている `gh api repos/{owner}/{repo}/pulls/{pr}/comments` の呼び出しで、pull request
に投稿済みのインラインコメントを読みます。読んだコメントを、`(non-blocking, prose)` の印が付き、
かつ GitHub の `suggestion` ブロックを伴うものだけに絞り込みます。1 件も見つからなければ何もしません。
1 件以上見つかれば、次を行います。

1. `roadmap-id.yml` と `refresh.yml` がすでに使っている同じ自動化 App トークンで、元の pull request
   の現在の head をチェックアウトします。素の `GITHUB_TOKEN` による push では自身の `check` CI が
   走らないため、push して PR を開いたときに実際に CI が走るには、この App トークンが要ります。この
   ジョブは、`claude-review.yml` 自身のレビューステップがすでに引いている信頼境界をそのまま引き継ぎ
   ます。同一リポジトリの `claude/<topic>` / `<user>/<topic>` ブランチからの `pull_request` イベント
   だけを対象とし、フォークは対象にしません。任意のブランチをチェックアウトして権限のある App トーク
   ンで push することは、`main`（すでに信頼済み）にしか触れない `roadmap-id.yml` や `refresh.yml` 自身
   の使い方とは、質の異なるリスクだからです。フォークの pull request の言い回しの指摘は自動化され
   ず、既存の自動レビュー自身がフォークに対してすでに認めているオンデマンドのみという同じ制約が残り
   ます。
2. companion branch を、毎回の実行ごとに**元の pull request の現在の head から作り直し**ます。前回
   の版に差分を積み重ねるのではありません。そのうえで、現在開いているすべての `(non-blocking, prose)`
   の指摘の `suggestion` ブロックを、そのファイルと行に対する機械的なパッチとして再適用します。前回の
   実行より後に新しく投稿された指摘だけではありません。前回に積み重ねず毎回作り直すことが、元の
   pull request 上のrebaseを吸収する仕組みです。`CLAUDE.md` の「早めに rebase し、小さな衝突のうちに
   統合する」という規範のもとで、貢献者はマージ前に自分のブランチの履歴を書き換えるのが日常です。
   前回のコミットの上に積み重ねる companion branch では、書き換え後の履歴との親子関係が静かに失われ
   ます。毎回、現在の head から作り直せば、そもそも引き継ぐものがないため、再同期する必要自体が生じ
   ません。指摘の対象行が、投稿後に元の pull request 自身の編集によってもう一致しない場合は、
   companion pull request の本文にその旨を記録してスキップします。推測で適用することはありません。
   `docs-refresh-prompt.md` など、このリポジトリがすでに他所で持っている保守性と同じです。
3. 作り直した companion branch を、元の pull request の番号から決定的に名付けたブランチ名（たとえば `prose-fix/pr-<N>`）へ force-push します。
   [BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md)
   の rolling branch と同じガードを使い、そのブランチ上の bot 自身の直前のコミットの上にだけ
   force-update し、companion branch 自身への人間の編集の上には決して force-update しません。
4. companion pull request がまだ開いていなければ開き、force-push が既存の companion pull request を
   更新しただけならそのままにします。そして、適用した各指摘のスレッドに companion pull request の
   番号を記して返信し、そのスレッドを解決します。指摘自身の提案がすでに修正を決めているので判断は
   不要であり、これは機械的に行えます。

### companion PR の基点を main ではなく元のブランチにする

companion pull request の基点は main ではなく、**元の pull request 自身のブランチ**です。元の
pull request 自身が持ち込む文章、新規ファイルや新しい段落は、まだ main に存在しないため、main を
基点にした companion pull request では、元の pull request がマージされるまでその修正を運べません。
元のブランチを基点にすることが、元の pull request がまだ開いている間に修正を届けられる理由です。

このリポジトリはすでに **delete head branches on merge** を有効にしています
（`gh api repos/bajutsu-e2e/bajutsu --jq '.delete_branch_on_merge'` を実行すると `true` が返ること
で確認済みです）。GitHub は、基点のブランチが削除されたオープン中の pull request を、そのブランチ
自身の基点へ自動的に retarget します。したがって、元の pull request がマージされてそのブランチが
削除されると、まだ開いている companion pull request は、このワークフローが何もしなくても main へ
自動的に retarget されます。その後の companion pull request のマージは、main への普通の、独立した
マージです。

### プライムディレクティブとの整合

この仕組みは自前の LLM 呼び出しを持ちません。言い回しの修正は、レビュー時点で BE-0203 がすでに
`suggestion` ブロックとして起草済みです。本項目のジョブは、その正確な文章がまだ一致するときだけ
適用し、一致しなければ推測せずスキップするだけです。投稿済みのコメントを読み、ブランチを書き、
pull request を開くことは判断ではありません。そもそも指摘を見つけるために LLM を呼ぶ BE-0203 自身の
レビューや、AI が起草する
[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) のリフレッシュ群
と比べても、この仕組みは `run`/CI の合否経路からさらに遠い位置にあります。`run` や必須のステータス
チェックに LLM 呼び出しは一切足しません。元の pull request と companion pull request、どちらの
レビューとマージも人間だけが行います。

### 仕組みを文書化する

修正はすでに適用済みで、スレッドもすでに自動で解決済みなので、他の指摘のために `pr-followup` が持つ
ような、返信して解決するという例外は本項目には要りません。レビューコメントに答えている貢献者は、
自分の pull request のタイムラインに companion pull request が説明もなく現れるまで、この仕組みの
存在を知る必要がないままです。`docs/ai-development.md` の「PR レビューコメントへの対応」の節
（および対訳の `docs/ja/ai-development.md`）に、自動レビュー自身の説明と並べて短い段落を追加します。
この段落は companion PR の仕組みと、貢献者がそれをどう扱うべきかを説明します。扱い方は、他の小さな
pull request と同じように、自分の都合でレビューしてマージすることです。`CLAUDE.md` の 1 行が、
自動レビュー自身の項目と同じように、その段落を指します。

## 検討した代替案

- **[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) の定期リフレッシュ
  の型を再利用し、元の pull request がマージされたあとに修正を回す。** 本項目のもとの形です。
  `docs/**` とロードマップ本文を毎日の cron で読み直し、main に対して 1 本のドラフト PR を開く、
  3 本目の `refresh.yml` 呼び出し側でした。却下します。元の pull request がマージされるまで待つと、
  言い回しの問題はそのレビューが終わるまで直らないまま残り、レビュアーは承認する前に修正済みの文章を
  目にしません。これは、本項目が提供しようとしている即時性とは正反対です。この方式はまた、すでに
  起草済みの正確な提案を適用するのではなく、毎回独立して各指摘を導き出すため、特定の修正が届くのは
  確実ではなく、可能性が高いというだけになります。companion pull request 自身の CI コストやブランチ
  をまたぐ信頼範囲の広がりが、実運用でこのトレードオフより悪いとわかった場合は、この代替案を見直し
  てください。
- **companion PR の基点を main にする。** 却下します。元の pull request 自身が持ち込む文章は、その
  pull request がマージされるまで main に存在しないため、main を基点にした companion pull request
  は、元の pull request がまだ開いている間はそのような修正をまったく運べません。これは上の、マージ
  後方式の代替案に帰着してしまいます。
- **オンデマンドのトリガのみ（pull request への `@claude prose-pr` というコメント）。** 却下します。
  本項目がなくそうとしている手作業の手順をそのまま残してしまいます。人間が companion pull request
  を頼むのを忘れないという前提が残るからです。
- **言い回しだけの指摘に印を付けず、その場で直し続ける。** 現状であり、本項目の動機そのものです。
  言い回しの修正のたびに、それを指摘した pull request の CI を丸ごと再実行するコストが残ります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `prose-companion` ジョブ — 投稿済みの `(non-blocking, prose)` の指摘を読み、各提案を機械的に
  適用し、companion branch へ force-push し、元のブランチを基点にした companion pull request を
  開くか更新する
- [ ] `.github/claude-review-prompt.md` — 既存の日本語のレンズに並ぶ新しい英語の文章品質レンズと、
  両方に付ける `(non-blocking, prose)` の飾り
- [ ] 元の pull request の指摘スレッドへの自動返信と自動解決。companion pull request の番号を記す
- [ ] `docs/ai-development.md` とその対訳 `docs/ja/ai-development.md` — 「PR レビューコメントへの
  対応」の下に companion PR の仕組みを記載
- [ ] この仕組みを指す短い `CLAUDE.md` の 1 行

### ログ

## 参考

[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md) は、本項目が修正を
適用する自動レビューです。
[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) は、本項目の
companion branch が再利用する rolling branch の上書き防止ガードであり、本項目が却下する定期
リフレッシュの代替案でもあります。[`roadmap-id.yml`](../../.github/workflows/roadmap-id.yml) と
[`refresh.yml`](../../.github/workflows/refresh.yml) は、本項目のジョブが再利用する、自動化 App に
よる push-and-open-a-pull-request のパターンです。
[BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md) は、本項目の動機が
根拠とする、文章の判断をレビュー時の規範に留めゲートとはしないという前例です。適用する言い回し品質の
規範は、`document-writing` / `english-document-writing` / `japanese-document-writing` の各スキルで
す（新しい英語レンズと既存の日本語レンズの両方が適用します）。
