[English](BE-0217-harden-review-prompt.md) · **日本語**

# BE-0217 — 調査に基づく方針で自動 PR レビュープロンプトを強化する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0217](BE-0217-harden-review-prompt-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0217") |
| 実装 PR | [#865](https://github.com/bajutsu-e2e/bajutsu/pull/865) |
| トピック | コントリビューターワークフロー |
| 関連 | [BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md) は、Claude Code を
自動 PR レビュアーにしました。あわせて、このリポジトリ独自の契約
[`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) をレビュアーに与えています。
この契約がカバーするのは、次の三つです。

- 三つの prime directive
- `implement-be` がすでに信頼しているレビューの観点（握りつぶしの検出、型設計、テスト網羅）
- いくつかの house convention（ドキュメントの二言語ルール、docstring 規約、ロードマップのリンク）

この契約は、実際の PR で効果を示してきました。ただし、観点のリストは、このプロジェクト自身の慣習から
書き起こしたものです。レビュアーが実際に何を検出すべきかを調べて導いたものではありません。

この項目は、同じ契約を二種類の根拠で強化します。どちらも、推測ではなく直接集めた根拠です。

1. **GitHub Copilot** が、このリポジトリ自身の最近のプルリクエストで実際に何を指摘してきたかという実績。
   このプロジェクトは、BE-0203 の移行期間中、Copilot を Claude Code と並行して稼働させています。
2. 確立された、参照可能なコードレビューの標準。Google の Engineering Practices、Python の各 PEP、
   Bandit のルール一覧、OWASP のセキュアコードレビューの指針、Conventional Comments 仕様です。

変更するのは、レビューの**契約**（`.github/claude-review-prompt.md`）だけです。コメントの書式が変わる
場合は、ワークフローの識別プレフィックスも変えます。BE-0203 が確立した、助言専用で非ブロッキングという
保証には、手を加えません。この項目にも、同じ prime directive 1 の境界が及びます。

## 動機

- **このリポジトリ自身のレビュー履歴が、すでに欠けている観点を示しています。** 最近マージされた PR に
  対する Copilot のインラインコメントを読むと、同じパターンが繰り返し現れます。現在のプロンプトは、
  そのどれも名指ししていません。
  **コメントや docstring と実装のずれ（コメント腐敗）。**
    [#825](https://github.com/bajutsu-e2e/bajutsu/pull/825) では、`state.py` の docstring が、
      ストレージの seam が 4 つに増えたあとも古い数のままでした。
    [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816) では、`platform_lifecycle.py` の
      docstring が、すでに参照済みのプラットフォームを「将来対応」と書いていました。run predicate の
      数も、実際と合っていませんでした。
    [#813](https://github.com/bajutsu-e2e/bajutsu/pull/813) では、`record.py` の docstring が、
      実装の実際の戻り値の挙動と食い違っていました。
  **docstring だけを本体に持つ `Protocol` メソッド。**
    [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816) では、`RunEnvironment` や
    `CrawlEnvironment` の複数のメソッドが、本体に docstring しか持たず、`...` や
    `raise NotImplementedError` を欠いていました。このようなメソッドは、戻り値の型注釈が `None` で
    ないにもかかわらず、暗黙に `None` を返す具象メソッドになります。`mypy --strict` では検出できません。
  **構造化データを組み立てる際の未エスケープな文字列補間。**
    [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811) では、`bajutsu/templates/serve.author.js`
    内の三つの関数が、セレクター ID を YAML の flow mapping にエスケープなしで埋め込んでいました。
    `:` を含む ID を与えると、不正な YAML になります。
  **脆いテストとフレーキーなテスト。** 同じく [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811)
    では、あるテストが引用符のスタイルを厳密な部分文字列一致で検証していました。挙動に関係のない
    フォーマット変更だけで壊れます。[#808](https://github.com/bajutsu-e2e/bajutsu/pull/808) では、
    テストのアサーションが壁時計時刻に依存しており、CI が遅いときにフレーキーになりえました。
  **PR やロードマップの文章に残る陳腐化した記述。**
    [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811) のロードマップファイルは、PR がすでに
    `Implementing PR` をリンクしたあとも、その欄を「pending」のままにしていました。

  現在のプロンプトには、三つの観点区分があります。prime directive、`implement-be` の観点、
  house convention です。このどれも、これらのパターンをレビュアーに名指しで示していません。レビュアーが
  拾えるのは、たまたま気づいた場合に限られます。契約が見るよう指示しているからではありません。
- **汎用のセキュリティ/品質チェックリストは、ゲートとの重複が大半を占めます。網羅性より精度が
  重要です。** Bandit や OWASP 風の広いチェックリストを足したくなります。しかし、このリポジトリの
  `ruff` 設定（[`pyproject.toml`](../../pyproject.toml)）は、`make lint` の一部として、すでに `S`
  （Bandit 相当）のルール群を選択しています。Python 側のゲートは、ハードコードされた秘密情報、安全でない
  `yaml.load`、`subprocess(shell=True)`、弱い乱数、リクエストのタイムアウト欠落、証明書検証の無効化を、
  すでにブロックしています。
  これらをプロンプトで再び指摘すると、すでに PR をブロックしているチェックと重複します。
  `assert` の使用や、`subprocess` への生の argv 呼び出しを指摘すると、このリポジトリの `S101` と
    `S603` の ignore と矛盾します。この ignore には、`pyproject.toml` に理由が記録されています。
  レビュアー独自の価値は、ゲートが構造的に見えない領域にあります。ファイルをまたぐ意味的なずれと、
    型検査は通るのに誤っている `Protocol` の本体です。上記 `#811` の例のとおり、
    `bajutsu/templates/serve.*.js` のようなインジェクション性の不具合も、具体的な例です。
    `make lint-js` は、このファイル群を `node --check` による構文チェックにしかかけていません。
    セキュリティやエスケープの観点の lint は、一切ありません。

  この項目の設計は、汎用チェックリストを丸ごと輸入するのではなく、この区別を先頭に置きます。
- **プロンプトには、設計とアーキテクチャの観点がありません。**
  [Google の Engineering Practices のレビュアーガイド](https://google.github.io/eng-practices/review/reviewer/looking-for.html)
  は、レビューの優先順位で、設計（design）を機能性、複雑さ、テスト、命名、コメント、スタイルより
  上に置いています。設計とは、三つの問いです。変更が置かれるべき場所に置かれているか。問題に対して
  過剰設計または過小設計になっていないか。周囲のアーキテクチャに合っているか。現在のプロンプトは、
  ルールとチェックリスト（prime directive、観点、house convention）だけで構成されています。変更そのものの
  形について意見を述べるよう求める箇所は、ありません。この軸は、Google 自身の指針と、この項目の依頼者の
  双方が、最も価値が高いと考えるレビューの軸です。
- **コメントの重大度が、今はラベルのない地の文になっています。** 現在、すべての指摘は
  `🤖 **Claude Code** —` というプレフィックスのあとに、ラベルのない文章として現れます。人間は、指摘を
  一つずつ読まないと、それが些末な指摘なのか本当の問題なのか判断できません。
  [Conventional Comments](https://conventionalcomments.org/) 仕様は、コミュニティで広く採用されている
  簡潔な書式で、`ラベル [装飾]: 主文` の形をとります。ラベルには `issue`、`suggestion`、`nitpick`、
  `question`、`praise` があり、装飾には `(non-blocking)` があります。この書式を使えば、指摘の重大度を
  一目で把握できます。さらに、レビュアーが投稿するものは何一つマージを妨げないことを、常に目に見える形で
  思い出させます。これは、周囲の説明文だけでなく成果物自体でも、prime directive 1 を補強します。
- **文言の一貫性を照らし合わせる、名前のついた標準がありません。** 現在の house convention の節は、
  二言語ドキュメントの欠落と docstring 規約を指摘します。用語のドリフト（同じ概念が場所によって別々の
  言い方をされること）は、指摘しません。diff によってすでに否定された主張を、ドキュメントやコメントが
  していることも、指摘しません。後者は、まさに上記のコメント腐敗のパターンです。
  [Google の開発者向けドキュメントスタイルガイド](https://developers.google.com/style) と、その
  [ドキュメントのベストプラクティス](https://google.github.io/styleguide/docguide/best_practices.html) は、
  このクラスの不備を直接名指ししています。どちらも、参照可能で確立した典拠です。

## 詳細設計

作業は、すべて既存のレビュー契約とその識別プレフィックスの内側にとどまります。BE-0203 のワークフローの
権限、トリガー、ゲートの意味論には、一切変更を加えません。

1. **「ゲートが見えないものをレビューする」という先頭原則を追加します。**
   `.github/claude-review-prompt.md` の観点の節より前に、短い段落を追加します。段落は二つの規則を
   述べます。
   `make check` がすでにゲートしているものを、レビュアーは再指摘しません。対象は、`ruff` が選択して
     いる `S`（Bandit）系を含むルール、`mypy --strict`、docstring linter、カバレッジの下限です。
   `pyproject.toml` に記録された、理由のあるファイル単位の ruff ignore（`S101`、`S603` など）を、
     レビュアーは蒸し返しません。段落は、そこにすでに記録された理由を引用します。

   これは、この項目が追加する観点すべてに共通する、組み立ての原則です。以下の各観点は、決定論的な
   ゲートが構造的に見えないものだけを狙います。
2. **設計とアーキテクチャの観点を追加します。** この新しい観点は、レビュアーに三つの問いについて述べ
   させます。変更が置かれるべき場所に置かれているか。解こうとしている問題に対して、過剰設計または
   過小設計になっていないか。既存のモジュール境界や seam に合っているか。
   [Google のレビュアーガイド](https://google.github.io/eng-practices/review/reviewer/looking-for.html)
   が示す、「設計優先」の優先順位と同じです。観点は、prime directive 1 に沿って、あくまで批評と提案と
   して書き、裁定にはしません。
3. **コメントや docstring のずれ（コメント腐敗）を検出する観点を追加します。** docstring、コメント、
   地の文の主張が、次の三つのいずれかに当たる場合を指摘します。説明しているコードと矛盾している。
   同じファイルや PR の別の箇所と矛盾している。diff 自身がすでに解決したことを、保留や将来対応として
   書いている。上記の `#825`、`#816`、`#813`、`#811` の背後にあるパターンです。
4. **`Protocol` や抽象メソッドの本体を検出する観点を追加します。** `Protocol` や `abc` のメソッドで、
   戻り値の型注釈が `None` でないにもかかわらず、本体が docstring だけの場合を指摘します。ここでの
   「docstring だけ」とは、`...`、`raise NotImplementedError`、実際の実装のいずれも持たない状態です。
   この種類の不具合は、`mypy --strict` では見えません。このリポジトリ自身の `#816` のレビューが、
   人手で捉えました。
5. **未エスケープな構造化データ補間の観点を、ゲートの及ばない範囲に絞って追加します。** 変数から
   エスケープなしで YAML、JSON、HTML、シェル用の文字列を組み立てている、文字列の連結や補間を指摘
   します。この観点は、明示的な注記を持ちます。`bajutsu/templates/serve.*.js` は、まさにこの種の
   ゲートの及ばない領域です。`make lint-js` が、このファイルを `node --check` でしか検査しないからです
   （[`Makefile`](../../Makefile) を参照）。`#811` の実例が、そのリスクを示しています。
6. **テスト品質の観点（脆さとフレーキーさ）を追加します。** 挙動ではなく、偶発的なフォーマット
   （厳密な引用符やホワイトスペース）に固定されたアサーションを指摘します。壁時計時刻など、非決定的な
   入力に pass/fail が依存するテストも指摘します。この観点は、このプロジェクト自身の「決定性優先」という
   姿勢（prime directive 2）を、`run` だけでなくテストスイート自体にも広げる形をとります。
7. **Conventional Comments のラベルを採用します。** すべてのインラインコメントに、
   [Conventional Comments](https://conventionalcomments.org/) のラベル（`issue`、`suggestion`、
   `nitpick`、`question`、`praise` のいずれか）と、`(non-blocking)` という装飾を付けます。付ける位置は、
   既存の `🤖 **Claude Code** —` という識別プレフィックスの前です。例は
   `🤖 **Claude Code** — issue (non-blocking): ...` です。プロンプトには、`(non-blocking)` が単なる
   飾りではないことを明記します。このレビュアーが投稿するラベルには、すべて必ず付きます。レビューは、
   設計上、助言にとどまるからです（prime directive 1）。どのラベルも、マージを妨げません。
8. **文言と用語の一貫性を見る観点を追加します。** 既存の house convention の節を拡張し、diff が触れた
   ファイルについて、次の三つを指摘します。
   同じ概念が、別々の言い方をされている。
   頭字語が、初出で展開されていない。
   PR 本文やロードマップの `Progress` の記述が、説明している diff と食い違っている。

   この観点は、[Google の開発者向けドキュメントスタイルガイド](https://developers.google.com/style) と
   [ドキュメントのベストプラクティス](https://google.github.io/styleguide/docguide/best_practices.html)
   を典拠とします。`#811` のレビューが捉えた、`Implementing PR` 欄の陳腐化も根拠にしています。
9. **検証。** 形は、BE-0203 の項目 9 と同じです。この項目が変更するのは、プロンプトの Markdown
   だけです。そのため、ワークフローの YAML に変更が不要なことを、`actionlint`（すでに `make check`
   に含まれる）が検証します。レビューの挙動そのものは、実際の PR がないと確認できません。したがって、
   検証は手動で行います。新しい観点それぞれの実例を、一つずつ含むテスト用 PR を作成します。
   陳腐化した docstring の主張
   docstring だけの `Protocol` メソッド
   `serve.*.js` テンプレート内の、未エスケープな YAML 補間
   壁時計時刻に依存する、テストのアサーション

   そのうえで、二つを確認します。レビュアーのインラインコメントが、それぞれを新しい Conventional
   Comments のラベル付きで捉えること。要約とすべてのインラインコメントが、非ブロッキングのままで
   あることです。

## 検討した代替案

- **Bandit や OWASP のセキュリティチェックリストを丸ごと輸入する案。** 却下します。このリポジトリの
  `ruff` 設定は、すでに `S`（Bandit 相当）のルール群をゲートの一部として選択しています。そのため、
  チェックリストの大半は、すでにブロッキングなチェックと重複します。重複しない部分（`S101`、`S603`）は、
  理由付きで意図的に ignore されています。これを再指摘すると、このリポジトリ自身が記録した判断と矛盾
  します。項目 1 の「ゲートが見えないものをレビューする」原則が、チェックリストの輸入に代わる、
  絞り込みの規則です。
- **指摘に重大度で重みを付け、`issue` ラベルの指摘をブロッキング扱いにする案。** 明確に却下します。
  LLM の判断をマージ経路に乗せることになり、prime directive 1 に違反します。BE-0203 が必須ステータス
  チェック化を却下したのと、同じ理由です。この項目が追加する Conventional Comments のラベルは、すべて
  `(non-blocking)` という装飾を伴います。ラベルは人間が一目で判断するためのもので、ゲートのためのもの
  ではありません。
- **コメントを自由記述の地の文のまま変えない案（Conventional Comments のラベルを採用しない）。**
  手間の少ない選択として検討し、却下します。ラベルのない指摘は、重大度を知るために、人間にコメント全文を
  読ませます。ラベル書式の `(non-blocking)` という装飾は、助言にとどまるという保証を、もう一段、
  目に見える形で補強します。この書式は、既存の参照可能な標準です。独自の語彙を一から考案して文書化する
  必要がないので、追加のコストは小さくなります。
- **この項目を BE-0203 に統合する案。** 検討しました。`.github/claude-review-prompt.md` へのこれまでの
  変更（ワークフローの堅牢化、仕組みの修正、文言の訂正）は、`状態` が「実装済み」になったあとも、
  すべて `(BE-0203)` というタグ付きのコミットとして、その項目自身の `進捗` のログに積み重ねられて
  きました。ただし、この項目は性質が異なります。BE-0203 が出荷したワークフローへの運用上の修正では
  ありません。外部の調査に基づいて、レビューの観点そのものを書き直す、実質のある方針の変更です。
  そのため、BE-0203 のログに畳み込まず、`関連` で相互に参照した独立の項目として提案します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 「ゲートが見えないものをレビューする」という先頭原則を追加する（項目 1）
- [x] 設計・アーキテクチャの観点を追加する（項目 2）
- [x] コメント/docstring のずれ（コメント腐敗）の観点を追加する（項目 3）
- [x] `Protocol`/抽象メソッドの docstring だけの本体を検出する観点を追加する（項目 4）
- [x] 未エスケープな構造化データ補間の観点を、ゲートの及ばない JS テンプレートに絞って追加する（項目 5）
- [x] テストの脆さ・フレーキーさの観点を追加する（項目 6）
- [x] インラインの指摘に Conventional Comments のラベルを採用する（項目 7）
- [x] 文言・用語の一貫性の観点を追加する（項目 8）
- [ ] 新しい観点それぞれを実際の PR で検証する（項目 9）— レビュアーを動かす実際の PR が必要なため手動

ログ：

- 提案を執筆しました。根拠は二つあります。一つは、このリポジトリ自身の最近の Copilot レビューコメント
  （[#808](https://github.com/bajutsu-e2e/bajutsu/pull/808)、
  [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811)、
  [#813](https://github.com/bajutsu-e2e/bajutsu/pull/813)、
  [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816)、
  [#825](https://github.com/bajutsu-e2e/bajutsu/pull/825)）を読んだ実績です。もう一つは、外部標準の調査
  （Google Engineering Practices、Python の各 PEP、Bandit のルール一覧、OWASP のセキュアコードレビューの
  指針、Conventional Comments 仕様、Google の開発者向けドキュメントスタイルガイド）です。
- `.github/claude-review-prompt.md` を書き換え、項目 1〜8 を取り込みました。追加したのは、
  「ゲートが見えないものをレビューする」という先頭原則、設計とアーキテクチャの観点、コメント腐敗、
  docstring だけの `Protocol` 本体、未エスケープな補間という意味的観点、テストの脆さとフレーキーさの観点、
  すべてのインライン指摘への Conventional Comments ラベル、house convention への用語一貫性の項目です。
  ワークフローの YAML は変更していません（識別用の接頭辞はプロンプト内にあるため）。項目 9 は、実際の
  PR でレビュアーを動かす必要があるため、未着手のままです。

## 参考

- [BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md)：Claude Code を
  自動 PR レビュアーにした項目で、この項目が強化する契約の出所
- [`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md)：この項目が変更するファイル
- [`pyproject.toml`](../../pyproject.toml)：項目 1 の「ゲートが見えないものをレビューする」原則が
  基準にする `ruff` の `select`/`ignore` 設定
- このリポジトリ自身のレビューコメントの根拠として読んだプルリクエスト：
  [#808](https://github.com/bajutsu-e2e/bajutsu/pull/808)、
  [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811)、
  [#813](https://github.com/bajutsu-e2e/bajutsu/pull/813)、
  [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816)、
  [#825](https://github.com/bajutsu-e2e/bajutsu/pull/825)
- [Google Engineering Practices — What to look for in a code
  review](https://google.github.io/eng-practices/review/reviewer/looking-for.html) と
  [The Standard of Code Review](https://google.github.io/eng-practices/review/reviewer/standard.html)：
  項目 2 の背後にある設計優先の優先順位
- [PEP 8](https://peps.python.org/pep-0008/)、[PEP 257](https://peps.python.org/pep-0257/)、
  [PEP 20](https://peps.python.org/pep-0020/)：Python のスタイル、docstring、設計思想に関する典拠
- [Bandit のルール一覧](https://bandit.readthedocs.io/en/latest/plugins/index.html)：このリポジトリの
  `ruff` の `S` セレクトがすでにカバーしているルール群で、動機の節で参照している
- [OWASP Secure Code Review Cheat
  Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secure_Code_Review_Cheat_Sheet.html)：
  項目 5 の絞り込みの一般的な典拠
- [Conventional Comments](https://conventionalcomments.org/)：項目 7 が採用するラベル仕様
- [Google 開発者向けドキュメントスタイルガイド](https://developers.google.com/style) と
  [Documentation Best Practices](https://google.github.io/styleguide/docguide/best_practices.html)：
  項目 8 の背後にある文言と用語の一貫性の典拠
