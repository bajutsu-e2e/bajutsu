[English](BE-0343-prose-companion-pr.md) · **日本語**

# BE-0343 — Claude review の言い回しのみの指摘を、コード PR の CI サイクルを回さずに解消する companion PR ワークフローを追加する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0343](BE-0343-prose-companion-pr-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0343") |
| 実装 PR | [#1498](https://github.com/bajutsu-e2e/bajutsu/pull/1498), [#1565](https://github.com/bajutsu-e2e/bajutsu/pull/1565) |
| トピック | コントリビューターワークフロー |
| 関連 | [BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md)、[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) |
| 無効化 | [BE-0419](../BE-0419-retire-prose-companion-pr/BE-0419-retire-prose-companion-pr-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は、自動レビュー（[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md)）
に、**companion PR** という仕組みを追加します。対象は、言い回しだけの指摘です。日本語の文章品質の指
摘と、その対になる英語の `docs/*.md` / ロードマップ本文レンズの指摘がこれにあたります。英語のレンズ
は、本項目が新たに追加します。

自動レビューが、コード側のファイルも変更している pull request に、この種の指摘を投稿したとします。す
ると、新しいワークフローが、指摘自身の提案を、pull request の現在の head を基点にした companion
branch へ機械的に適用します。そして、そのブランチを対象とする companion pull request を開くか、更新
します。

元の pull request のブランチと CI サイクルは、人間がこの小さな companion pull request をレビューして
マージするまで、触れられません。マージされると、言い回しの修正は、元のブランチへの通常の push として
反映されます。修正自体は、安価な条件でレビューされます。元の pull request がまだ開いている間に使える
ので、マージ後を待つ必要はありません。

この仕組みは、自前の LLM 呼び出しを持ちません。修正は、レビュー時点で BE-0203 がすでに起草しています。
本項目が自動化するのは、それを適用して届けることだけです。

## 動機

BE-0203 は、pull request の全差分を push ごとに読み直し、インラインの指摘を投稿します。レンズの 1 つ
が、日本語の文章品質です。バイリンガルなドキュメントという house convention のもとでは、英語の
`docs/*.md` とロードマップの英語本文にも、同じ言い回し品質の基準が課されるべきです。しかし、現在のレ
ビュアーには、対になるレンズがありません。本項目は、すでにある日本語のレンズに並べて、英語のレンズを
追加します。

どちらの指摘も、純粋な言い回しの提案であって、正しさの欠陥ではありません。しかし現状では、この指摘を
直すとなると、同じ pull request にコミットを push するしかありません。その push は、挙動に何も影響し
ない文章の修正のために、CI 一式を丸ごと再実行させます。CI 一式とは、`ci.yml` と、変更ファイルが選ぶ
end-to-end のレーンです。

レビュアーは push ごとに全差分を読み直します。そのため、言い回しの指摘は、一度にまとまって出るとは限
りません。pull request の寿命のなかで、時間差をおいて現れます。貢献者は、このコストを、1 つの pull
request につき何度も負います。

このリポジトリは、文章の品質を、CI ゲートではなくレビュー時の規範として扱っています。`DESIGN.md` の
整合を扱う理由と同じです。言い回しの当否は意味的な判断を要します。ゲートで判定させれば、大規模言語モ
デル（LLM）が `run`/CI の合否を左右してしまいます（プライムディレクティブ 1）。

`document-writing` スキルの規範は、textlint で検証できます。ただし、既存のロードマップの文章群に対し
て、指摘をゼロ件まで通すことは求めていません。
[BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md) も、同じ根拠で、設
計文書の整合を `make check` の外に置いています。pull request 上の言い回しの指摘は、もともと、マージ
前に直す義務のない助言です。

この助言という位置づけこそ、修正をマージまで待たせてはいけない理由です。言い回しの指摘を、レビューが
終わるまで直さずにおくと、指摘は差分に残ります。その差分は、人間がまさに読み、承認しようとしているも
のです。レビュアーは、pull request がマージされる前に、修正済みの文章を目にしません。

[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) の定期リフレッシュ
は、他の種類のドリフトを、マージ**後**に解消します。この方式を採ると、CI コストの問題が、可視性の問
題に置き換わります。元の問題は解決しません。

必要なのは、元の pull request がまだ開いている間に、修正を即座に適用する方法です。その pull request
自身のブランチと CI サイクルは、経由しません。小さく安価な条件でレビューできる companion pull
request が、これを実現します。

## 詳細設計

### 自動レビュー側で、言い回しだけの指摘に印を付ける

[`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) が名指しするのは、現在、
日本語の文章品質レンズだけです。対になる英語のレンズはありません。ほかには、バイリンガルなドキュメン
トの同期を見るレンズと、用語の揺れを見るレンズがあります。どちらも、英語の言い回し品質は審査しません。

本項目は、この足りないレンズを追加します。新しい house convention の項を立て、英語の `docs/*.md` と
ロードマップ本文を、`document-writing` / `english-document-writing` の各スキルに照らして審査させます。
既存の項は、日本語の文章を `japanese-document-writing` に照らして審査しています。

レビュアーは、すべてのインライン指摘に、`(non-blocking)` という飾りを付けています（BE-0203）。本項目
は、もう 1 つの飾り、たとえば `(non-blocking, prose)` を追加します。付けるのは、2 つの言い回し品質レ
ンズから出た指摘だけです。コードに対する設計、セキュリティ、正しさの `suggestion` には、付けません。
この印があることで、後述の companion PR ジョブは、指摘の内容を判断せずに、言い回しだけの指摘かどうか
を見分けられます。

### レビュー時点で機械的に適用される companion PR

新しいジョブを用意します。専用のワークフローでも、`claude-review.yml` 自身への追加のジョブでもかまい
ません。`needs: review` で順序付けるのは、その push の指摘が投稿されたあとにだけ、このジョブを走らせ
るためです。

このジョブは、`claude-review.yml` が重複防止にすでに使っている呼び出し
`gh api repos/{owner}/{repo}/pulls/{pr}/comments` で、投稿済みのインラインコメントを読みます。読んだ
コメントは、`(non-blocking, prose)` の印が付き、かつ GitHub の `suggestion` ブロックを伴うものに絞り
ます。1 件も見つからなければ、何もしません。1 件以上見つかれば、次の 4 つを行います。

1. `roadmap-id.yml` と `refresh.yml` がすでに使っている自動化 App トークンで、元の pull request の現
   在の head を読みます。素の `GITHUB_TOKEN` による push では、自身の `check` CI が走りません。push
   して PR を開いたときに CI が実際に走るには、この App トークンが要ります。head は、解決済みのコミ
   ットからデータとして読むだけで、チェックアウトはしません。ジョブが作業ツリーを持たない理由は、後
   述のログに書きます。

   このジョブは、`claude-review.yml` 自身のレビューステップがすでに引いている信頼境界を、そのまま引
   き継ぎます。同一リポジトリの `claude/<topic>` / `<user>/<topic>` ブランチからの `pull_request` イ
   ベントだけを対象とし、フォークは対象にしません。任意のブランチに、権限のある App トークンで push
   することは、質の異なるリスクだからです。`roadmap-id.yml` や `refresh.yml` は、すでに信頼済みの
   `main` にしか触れません。フォークの pull request の言い回しの指摘は、自動化されません。既存の自動
   レビューも、フォークに対して、オンデマンドのみという同じ制約をすでに認めています。
2. companion branch を、実行のたびに、**元の pull request の現在の head から作り直し**ます。前回の版
   に差分を積み重ねることは、しません。現在開いているすべての `(non-blocking, prose)` の指摘について、
   `suggestion` ブロックを、そのファイルと行に対する機械的なパッチとして再適用します。前回の実行より
   後に投稿された指摘だけではありません。

   毎回作り直すことが、元の pull request 上の rebase を吸収する仕組みです。`CLAUDE.md` の「早めに
   rebase し、小さな衝突のうちに統合する」という規範のもとで、貢献者はマージ前に自分のブランチの履歴
   を書き換えるのが日常です。前回のコミットの上に積み重ねる companion branch は、書き換え後の履歴と
   の親子関係を、静かに失います。現在の head から作り直せば、引き継ぐものがありません。再同期する必
   要自体が生じません。

   指摘の対象行が、投稿後に元の pull request 自身の編集によって、一致しなくなることがあります。この
   場合は、companion pull request の本文にその旨を記録して、スキップします。推測で適用することはあり
   ません。`docs-refresh-prompt.md` など、このリポジトリがすでに他所で保っている慎重さと同じです。
3. 作り直した companion branch を、force-push します。ブランチ名は、元の pull request の番号から決定
   的に付けます（たとえば `prose-fix/pr-<N>`）。ガードは、
   [BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) の rolling
   branch と同じです。そのブランチ上の bot 自身の直前のコミットの上にだけ force-update し、companion
   branch 自身への人間の編集の上には、決して force-update しません。
4. companion pull request がまだ開いていなければ、開きます。force-push が既存の companion pull
   request を更新しただけなら、そのままにします。そのうえで、適用した各指摘のスレッドに、companion
   pull request の番号を記して返信し、そのスレッドを解決します。指摘自身の提案が、すでに修正を決めて
   います。判断は要らないので、これは機械的に行えます。

### companion PR の基点を main ではなく元のブランチにする

companion pull request の基点は、main ではなく、**元の pull request 自身のブランチ**です。元の pull
request が持ち込む文章、たとえば新規ファイルや新しい段落は、まだ main に存在しません。main を基点に
した companion pull request は、元の pull request がマージされるまで、その修正を運べません。元のブラ
ンチを基点にすれば、元の pull request がまだ開いている間に、修正を届けられます。

このリポジトリは、**delete head branches on merge** をすでに有効にしています。
`gh api repos/bajutsu-e2e/bajutsu --jq '.delete_branch_on_merge'` が `true` を返すことで、確認済みで
す。GitHub は、基点のブランチが削除された、開いている pull request を、そのブランチの基点へ自動的に
retarget します。元の pull request がマージされ、そのブランチが削除されると、まだ開いている
companion pull request は、main へ自動的に retarget されます。このワークフローは、何もしません。その
後の companion pull request のマージは、main への通常の、独立したマージです。

### プライムディレクティブとの整合

この仕組みは、自前の LLM 呼び出しを持ちません。言い回しの修正は、レビュー時点で BE-0203 が、
`suggestion` ブロックとして起草済みです。本項目のジョブは、その文章がまだ一致するときに、そのまま適
用します。一致しなければ、推測せずにスキップします。投稿済みのコメントを読み、ブランチを書き、pull
request を開くことは、判断ではありません。

そのため、この仕組みは `run`/CI の合否経路から、BE-0203 自身のレビューより遠い位置にあります。
BE-0203 のレビューは、指摘を見つけるために LLM を呼びます。AI が起草する
[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) のリフレッシュ群と
比べても、遠い位置にあります。`run` や必須のステータスチェックに、LLM 呼び出しは一切足しません。元の
pull request と companion pull request の、どちらも、レビューとマージを行うのは人間だけです。

### 仕組みを文書化する

修正はすでに適用済みで、スレッドもすでに自動で解決済みです。他の指摘のために `pr-followup` が持つ、
返信して解決するという例外は、本項目には要りません。レビューコメントに答えている貢献者は、自分の
pull request のタイムラインに companion pull request が説明なく現れるまで、この仕組みの存在を知る必
要がありません。

`docs/ai-development.md` の「PR レビューコメントへの対応」の節（と対訳の `docs/ja/ai-development.md`）
に、自動レビュー自身の説明と並べて、短い段落を追加します。この段落は、companion PR の仕組みと、貢献
者がそれをどう扱うべきかを説明します。扱い方は、他の小さな pull request と同じです。自分の都合でレビ
ューして、マージします。`CLAUDE.md` の 1 行が、自動レビュー自身の項目と同じように、その段落を指しま
す。

## 検討した代替案

-
  **[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md) の定期リフレッシュの型を再利用し、元の pull request がマージされたあとに修正を回す。**
  本項目のもとの形です。3 本目の `refresh.yml` 呼び出し側が、`docs/**` とロードマップ本文を毎日の
  cron で読み直し、main に対して 1 本のドラフト PR を開きます。却下します。理由は 3 つあります。
  - 元の pull request がマージされるまで待つと、言い回しの問題は、そのレビューが終わるまで直らないま
    ま残ります。レビュアーは、承認する前に修正済みの文章を目にしません。本項目が提供しようとしている
    即時性の、正反対です。
  - この方式は、すでに起草済みの正確な提案を適用せず、毎回独立して各指摘を導き出します。特定の修正が
    届くのは、確実ではなく、可能性が高いというだけです。
  - companion pull request 自身の CI コストや、ブランチをまたぐ信頼範囲の広がりが、実運用でこのトレー
    ドオフより悪いとわかった場合は、この代替案を見直してください。
- **companion PR の基点を main にする。** 却下します。元の pull request が持ち込む文章は、その pull
  request がマージされるまで、main に存在しません。main を基点にした companion pull request は、元の
  pull request が開いている間、そのような修正をまったく運べません。上の、マージ後方式の代替案に帰着
  してしまいます。
- **オンデマンドのトリガのみ（pull request への `@claude prose-pr` というコメント）。** 却下します。
  本項目がなくそうとしている手作業の手順が、そのまま残ります。人間が companion pull request を頼むの
  を忘れない、という前提が残るからです。
- **言い回しだけの指摘に印を付けず、その場で直し続ける。** 現状であり、本項目の動機そのものです。言
  い回しの修正のたびに、それを指摘した pull request の CI を丸ごと再実行するコストが残ります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `prose-companion` ジョブ — 投稿済みの `(non-blocking, prose)` の指摘を読み、各提案を機械的に
  適用し、companion branch へ force-push し、元のブランチを基点にした companion pull request を
  開くか更新する
- [x] `.github/claude-review-prompt.md` — 既存の日本語のレンズに並ぶ新しい英語の文章品質レンズと、
  両方に付ける `(non-blocking, prose)` の飾り
- [x] 元の pull request の指摘スレッドへの自動返信と自動解決。companion pull request の番号を記す
- [x] `docs/ai-development.md` とその対訳 `docs/ja/ai-development.md` — 「PR レビューコメントへの
  対応」の下に companion PR の仕組みを記載
- [x] この仕組みを指す短い `CLAUDE.md` の 1 行
- [x] [BE-0419](../BE-0419-retire-prose-companion-pr/BE-0419-retire-prose-companion-pr-ja.md)により
  無効化：companion PRのジョブとスクリプト、`(non-blocking, prose)`という印を廃止しました。
  言い回しだけの指摘も、ほかの指摘と同じく、元のpull requestへの通常のpushで直します。

### ログ

- 一度にまとめて実装しました。判断のロジックはインラインの YAML ではなく
  `scripts/prose_companion_pr.py` に置き、ユニットテスト（`tests/test_prose_companion_pr.py`）で
  固定しています。どの指摘を対象にするか、各指摘が対象行に何を期待しているか、そしてそれが現在の
  head と一致するか、の 3 点です。ジョブは
  [`claude-review.yml`](../../.github/workflows/claude-review.yml) の 2 つ目のジョブとして
  `needs: review` で走ります。スクリプトは**デフォルトブランチ**のチェックアウトから実行します。
  こうすると、同一リポジトリの
  ブランチであっても、自動化 App のトークンの下で動くコードを書き換えられません。スクリプトは標準
  ライブラリだけで動き、ランナーの `python3` で実行するので、pull request 側の `pyproject.toml` は
  この特権ジョブに一切関わりません。
- ジョブが当初その場で編集していた 2 つ目のチェックアウト、すなわち pull request の head の
  チェックアウトをやめました。CodeQL の `actions/untrusted-checkout-toctou` が指摘したためです。
  このジョブは `issue_comment` から到達するので、リポジトリのシークレットを持って走ります。貢献者が
  書いたコードを自動化 App のトークンの傍らでチェックアウトすることは、その検査が捕まえようとして
  いる型そのものでした。実行を許可する関門が判定するのは**コメント**です。そのあと実行が実体化させる
  コードについては、何も固定しません。いまは head をデータとして受け取ります。指摘が名指しした
  ファイルは、解決済みの head コミットから contents エンドポイントで読みます。companion のコミットは
  Git Data エンドポイントで組み立てます。blob、tree、commit を作り、最後にブランチの参照を強制的に
  更新します。記録しておく価値のある帰結が 2 つあります。1 つは、読み取りをブランチではなく解決済みの
  コミットに固定した点です。実行中に届いた push は、指摘の照合先となる文字列をずらせなくなりました。
  作業ツリー版にこの保証はありません。もう 1 つは引き換えです。これらのエンドポイントには
  `git push --force-with-lease` の代わりになる compare-and-swap がありません。そのため上書き防止
  ガードは、companion ブランチの先端を上書きの往復 1 回前に読みます。lease に隙はありませんでした。
  ガード自体は変わっておらず、人間がコミットした先端があれば実行は止まります。

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
