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

[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md) は Claude Code を
自動 PR レビュアーにし、このリポジトリ独自の契約
[`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) を与えました。この契約は
三つの prime directive、`implement-be` がすでに信頼しているレビューの観点（握りつぶしの検出、型設計、
テスト網羅）、そしていくつかの house convention（ドキュメントの二言語ルール、docstring 規約、ロードマップの
リンク）をカバーしています。実際の PR で効果を示してきましたが、その観点のリストはこのプロジェクト自身の
慣習から書き起こされたものであり、レビュアーが実際に何を検出すべきかの調査から導かれたものではありません。

この項目は、同じ契約を二種類の根拠で強化します。一つは、BE-0203 の移行期間中に並行稼働している
**GitHub Copilot** がこのリポジトリ自身の最近のプルリクエストで実際に何を指摘してきたかという実績、もう一つは
確立された参照可能なコードレビューの標準（Google の Engineering Practices、Python の各 PEP、Bandit のルール
一覧、OWASP のセキュアコードレビューの指針、Conventional Comments 仕様）です。変更するのはレビューの
**契約**（`.github/claude-review-prompt.md`）と、書式が変わる分だけワークフローの識別プレフィックスにとどまり、
BE-0203 が確立した助言専用・非ブロッキングの保証には手を加えません。この項目も同じ prime directive 1 の
境界に従います。

## 動機

- **このリポジトリ自身のレビュー履歴が、すでに欠けている観点を示しています。** 最近マージされた PR に
  対する Copilot のインラインコメントを読むと、現在のプロンプトが名指ししていないパターンが繰り返し
  現れています。
  - **コメントや docstring と実装のずれ（コメント腐敗）。**
    [#825](https://github.com/bajutsu-e2e/bajutsu/pull/825) では `state.py` の docstring が
    ストレージの seam が 4 つに増えたあとも古い数のままでした。
    [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816) では `platform_lifecycle.py` の
    docstring が、すでに他所で参照済みのプラットフォームを「将来対応」と書き、run predicate の数も
    実際と合っていませんでした。
    [#813](https://github.com/bajutsu-e2e/bajutsu/pull/813) では `record.py` の docstring が、
    実装の実際の戻り値の挙動と食い違っていました。
  - **docstring だけを本体に持つ `Protocol` メソッド。**
    [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816) では、`RunEnvironment` や
    `CrawlEnvironment` の複数のメソッドが本体に docstring しか持たず、`...` や
    `raise NotImplementedError` を欠いていました。これは戻り値の型注釈が `None` でないにもかかわらず、
    暗黙に `None` を返す具象メソッドになってしまう不具合で、`mypy --strict` では検出できません。
  - **構造化データを組み立てる際の未エスケープな文字列補間。**
    [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811) では、`bajutsu/templates/serve.author.js`
    内の三つの関数が、セレクター ID を YAML の flow mapping にエスケープなしで埋め込んでおり、`:` を
    含む ID を与えると不正な YAML になっていました。
  - **脆いテストとフレーキーなテスト。**
    同じく [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811) では、あるテストが引用符のスタイルを
    厳密な部分文字列一致で検証しており、挙動に関係のないフォーマット変更だけで壊れる状態でした。
    [#808](https://github.com/bajutsu-e2e/bajutsu/pull/808) では、テストのアサーションが壁時計時刻に
    依存しており、CI が遅いときにフレーキーになりえました。
  - **PR やロードマップの文章に残る陳腐化した記述。**
    [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811) 自身のロードマップファイルは、PR が
    すでに `Implementing PR` をリンクしたあとも、その欄を「pending」のままにしていました。

  これらはいずれも、現在のプロンプトが持つ三つの観点区分（prime directive、`implement-be` の観点、
  house convention）がレビュアーに名指しで注意を促していないパターンです。レビュアーがこれらを拾えるのは
  たまたま気づいた場合に限られ、契約がそう指示しているからではありません。
- **汎用のセキュリティ/品質チェックリストを丸ごと持ち込むと、ゲートとの重複が大半を占めてしまいます。
  網羅性より精度が重要です。** Bandit や OWASP 風のチェックリストを丸ごと足したくなりますが、このリポジトリの
  `ruff` 設定（[`pyproject.toml`](../../pyproject.toml)）は `make lint` の一部としてすでに `S`
  （Bandit 相当）のルール群を選択しています。ハードコードされた秘密情報、安全でない `yaml.load`、
  `subprocess(shell=True)`、弱い乱数、リクエストのタイムアウト欠落、証明書検証の無効化は、すでに
  Python 側のゲートで検出されます。これらをプロンプトで再び指摘するのは、すでにブロックしているチェックの
  重複です。さらに `assert` の使用や `subprocess` への生の argv 呼び出しを指摘してしまうと、このリポジトリが
  `S101` と `S603` に対して理由付きで下した ignore の判断と矛盾します。レビュアー独自の価値は、ゲートが
  構造的に見えない領域にあります。ファイルをまたぐ意味的なずれ、型検査は通るのに誤っている `Protocol`
  の本体、そして上記 `#811` の例のとおり `bajutsu/templates/serve.*.js` のようなインジェクション性の
  不具合です。`make lint-js` はこのファイル群を `node --check` による構文チェックにしかかけておらず、
  セキュリティやエスケープの観点の lint は一切ありません。この項目の設計は、汎用チェックリストの輸入では
  なく、この区別を先頭に置きます。
- **プロンプトには設計・アーキテクチャの観点がありません。**
  [Google の Engineering Practices レビュアーガイド](https://google.github.io/eng-practices/review/reviewer/looking-for.html)
  は、レビューの優先順位で設計（design）を機能性、複雑さ、テスト、命名、コメント、スタイルより上に
  置いています。変更が置かれるべき場所に置かれているか、問題に対して過剰設計または過小設計になっていないか、
  周囲のアーキテクチャに合っているかという観点です。現在のプロンプトはルールとチェックリストの集まり
  （prime directive、観点、house convention）に終始しており、変更そのものの形について意見を述べる
  観点がありません。これは Google 自身の指針と、この項目の依頼者の双方が最も価値が高いと考えるレビューの
  軸です。
- **コメントの重大度が、今はラベルのない地の文になっています。**
  現在、すべての指摘は `🤖 **Claude Code** —` というプレフィックスのあとにラベルのない文章として
  現れます。人間はコメントを一つずつ読まないと、それが些末な指摘なのか本当の問題なのか判断できません。
  [Conventional Comments](https://conventionalcomments.org/) 仕様は、コミュニティで広く採用されている
  簡潔な書式（`ラベル [装飾]: 主文`）で、`issue`、`suggestion`、`nitpick`、`question`、`praise`
  といったラベルと `(non-blocking)` のような装飾を持ちます。これを採用すれば、指摘の重大度を一目で
  把握できるようになるだけでなく、この項目が投稿するものは何一つマージを妨げないという保証を、周囲の
  説明文だけでなく成果物自体でも繰り返し示すことになり、prime directive 1 を補強します。
- **文言の一貫性を照らし合わせる、名前のついた標準がありません。** 現在の house convention の節は
  二言語ドキュメントの欠落や docstring 規約は指摘しますが、用語のドリフト（同じ概念が場所によって
  別々の言い方をされること）や、diff によってすでに否定された主張をドキュメントやコメントが
  していることは指摘しません。これはまさに上記のコメント腐敗のパターンです。
  [Google の開発者向けドキュメントスタイルガイド](https://developers.google.com/style) と、その
  [ドキュメントのベストプラクティス](https://google.github.io/styleguide/docguide/best_practices.html) は、
  このクラスの不備を直接名指ししており、参照可能な典拠になります。

## 詳細設計

作業はすべて既存のレビュー契約とその識別プレフィックスの内側にとどまります。BE-0203 のワークフローの
権限、トリガー、ゲートの意味論には一切変更を加えません。

1. **「ゲートが見えないものをレビューする」という先頭原則を追加します。** `.github/claude-review-prompt.md`
   の観点の節より前に短い段落を追加し、`make check` がすでにゲートしているもの（`ruff` が選択している
   `S`（Bandit）系を含むルール、`mypy --strict`、docstring linter、カバレッジの下限）を再指摘しないこと、
   そして `pyproject.toml` に記録された理由のあるファイル単位の ruff ignore（`S101`、`S603` など）を
   蒸し返さないことを明記します。これはこの項目が追加する観点すべてに共通する組み立ての原則であり、
   以下の各観点は、決定論的なゲートが構造的に見えないものだけを狙います。
2. **設計・アーキテクチャの観点を追加します。** 変更が置かれるべき場所に置かれているか、解こうとしている
   問題に対して過剰設計または過小設計になっていないか、既存のモジュール境界や seam に合っているかを
   レビュアーに述べさせる新しい観点です。
   [Google のレビュアーガイド](https://google.github.io/eng-practices/review/reviewer/looking-for.html)
   が示す「設計優先」の優先順位と同じです。prime directive 1 に沿って、あくまで批評と提案であり、
   裁定にはしません。
3. **コメントや docstring のずれ（コメント腐敗）を検出する観点を追加します。** docstring やコメント、
   地の文の主張が、それが説明しているコードと矛盾している場合、同じファイルや PR の別の箇所と矛盾している
   場合、diff 自身がすでに解決したことを保留・将来対応として書いている場合を指摘します。上記の `#825`、
   `#816`、`#813`、`#811` の背後にあるパターンです。
4. **`Protocol` や抽象メソッドの本体を検出する観点を追加します。** `Protocol` や `abc` のメソッドが、
   戻り値の型注釈が `None` でないにもかかわらず、本体が docstring だけ（`...` や
   `raise NotImplementedError`、実装のいずれも持たない）である場合を指摘します。`mypy --strict` では
   見えない不具合の型で、このリポジトリ自身の `#816` のレビューが人手で捉えたものです。
5. **未エスケープな構造化データ補間の観点を、ゲートの及ばない範囲に絞って追加します。** 変数から
   エスケープなしで YAML、JSON、HTML、シェル用の文字列を組み立てている箇所を指摘します。
   `bajutsu/templates/serve.*.js`（[`Makefile`](../../Makefile) の `make lint-js` では `node --check`
   のみで検査されている）がまさにこの種のゲートの及ばない領域であることを、`#811` の実例とともに
   明記します。
6. **テスト品質の観点（脆さとフレーキーさ）を追加します。** 挙動ではなく偶発的なフォーマット
   （厳密な引用符やホワイトスペース）に固定されたアサーションと、壁時計時刻など非決定的な入力に
   pass/fail が依存するテストを指摘します。これは、`run` に限らずテストスイート自体にも、このプロジェクト
   自身の「決定性優先」という姿勢（prime directive 2）を広げる形です。
7. **Conventional Comments のラベルを採用します。** すべてのインラインコメントに、既存の
   `🤖 **Claude Code** —` という識別プレフィックスの前に、
   [Conventional Comments](https://conventionalcomments.org/) のラベル（`issue`、`suggestion`、
   `nitpick`、`question`、`praise` のいずれか）と `(non-blocking)` という装飾を付けます
   （例：`🤖 **Claude Code** — issue (non-blocking): ...`）。プロンプトには、`(non-blocking)` が
   単なる飾りではなく、このレビュアーが投稿するラベルすべてに必ず付くものであることを明記します。
   レビューは設計上助言にとどまり（prime directive 1）、どのラベルもマージを妨げるものにはなりません。
8. **文言・用語の一貫性を見る観点を追加します。** 既存の house convention の節を拡張し、diff が触れた
   ファイルをまたいで同じ概念が別々の言い方をされている場合、頭字語が初出で展開されていない場合、
   PR 本文やロードマップの `Progress` の記述が実際の diff と食い違っている場合を指摘します。
   [Google の開発者向けドキュメントスタイルガイド](https://developers.google.com/style) と
   [ドキュメントのベストプラクティス](https://google.github.io/styleguide/docguide/best_practices.html)
   を典拠とし、`#811` 自身のレビューが捉えた `Implementing PR` 欄の陳腐化を踏まえています。
9. **検証。** BE-0203 の項目 9 と同じ形をとります。この項目はプロンプトの Markdown だけを変更し
   ワークフローの YAML には変更を加えないため、`actionlint`（すでに `make check` に含まれる）は
   関与しません。レビューの挙動そのものは実際の PR がないと確認できないため、検証は手動で行います。
   新しい観点それぞれの実例（陳腐化した docstring の主張、docstring だけの `Protocol` メソッド、
   `serve.*.js` テンプレート内の未エスケープな YAML 補間、壁時計時刻に依存するテストのアサーション）を
   一つずつ含むテスト用 PR を作成し、レビュアーのインラインコメントがそれぞれを新しい Conventional
   Comments のラベル付きで捉えること、そして要約とすべてのインラインコメントが非ブロッキングのまま
   であることを確認します。

## 検討した代替案

- **Bandit や OWASP のセキュリティチェックリストを丸ごと輸入する。** 却下します。このリポジトリの
  `ruff` 設定はすでに `S`（Bandit 相当）のルール群を `make lint` の一部として選択しており、その
  チェックリストの大半はすでにブロッキングなチェックと重複します。重複しない部分（`S101`、`S603`）は
  理由付きで意図的に ignore されており、これを再指摘するとこのリポジトリ自身が記録した判断と矛盾します。
  項目 1 の「ゲートが見えないものをレビューする」原則が、チェックリストの輸入に代わる絞り込みの規則です。
- **指摘に重大度で重みを付け、`issue` ラベルの指摘をブロッキング扱いにする。** これは明確に却下します。
  LLM の判断をマージ経路に乗せることになり、BE-0203 が必須ステータスチェック化をすでに却下したのと
  同じ理由で prime directive 1 に違反します。この項目が追加する Conventional Comments のラベルは
  すべて `(non-blocking)` という装飾を伴います。ラベルは人間が一目で判断するためのものであり、
  ゲートのためのものではありません。
- **コメントを自由記述の地の文のまま変えない（Conventional Comments のラベルを採用しない）。**
  手間の少ない選択として検討しましたが、却下します。ラベルのない指摘は、重大度を知るために人間へ
  コメント全文を読ませてしまいます。またラベル書式の `(non-blocking)` という装飾は、助言にとどまる
  という保証をもう一段、目に見える形で補強します。この書式は独自の語彙を一から考案して文書化する
  必要のない、既存の参照可能な標準であるため、追加コストは小さいと判断しました。
- **この項目を BE-0203 に統合する。** 検討しました。`.github/claude-review-prompt.md` へのこれまでの
  変更（ワークフローの堅牢化、仕組みの修正、文言の訂正）は、`状態` が「実装済み」になったあとも
  すべて `(BE-0203)` というタグ付きのコミットとして、その項目自身の `進捗` のログに積み重ねられてきました。
  ただしこの項目は性質が異なります。BE-0203 が出荷したワークフローへの運用上の修正ではなく、外部の
  調査に基づいてレビューの観点そのものを書き直す、実質のある政策変更です。そのため BE-0203 のログに
  畳み込むのではなく、`関連` で相互に参照した独立の項目として提案します。

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

- 提案を執筆しました。このリポジトリ自身の最近の Copilot レビューコメント
  （[#808](https://github.com/bajutsu-e2e/bajutsu/pull/808)、
  [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811)、
  [#813](https://github.com/bajutsu-e2e/bajutsu/pull/813)、
  [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816)、
  [#825](https://github.com/bajutsu-e2e/bajutsu/pull/825)）を読んだ実績と、外部標準の調査
  （Google Engineering Practices、Python の各 PEP、Bandit のルール一覧、OWASP のセキュアコードレビューの
  指針、Conventional Comments 仕様、Google の開発者向けドキュメントスタイルガイド）の双方に基づいています。
- `.github/claude-review-prompt.md` を書き換え、項目 1〜8 を取り込みました。「ゲートが見えないものを
  レビューする」という先頭原則、設計・アーキテクチャの観点、コメント腐敗・docstring だけの `Protocol`
  本体・未エスケープな補間という意味的観点、テストの脆さ・フレーキーさの観点、すべてのインライン指摘への
  Conventional Comments ラベル、ハウスルールへの用語一貫性の項目を追加しています。ワークフローの YAML は
  変更していません（識別用の接頭辞はプロンプト内にあるため）。項目 9 は実際の PR でレビュアーを動かす必要が
  あるため未着手のままです。

## 参考

- [BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md) — Claude Code を
  自動 PR レビュアーにした項目で、この項目が強化する契約の出所です。
- [`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) — この項目が変更する
  ファイルです。
- [`pyproject.toml`](../../pyproject.toml) — 項目 1 の「ゲートが見えないものをレビューする」原則が
  基準にする `ruff` の `select`/`ignore` 設定です。
- このリポジトリ自身のレビューコメントの根拠として読んだプルリクエスト：
  [#808](https://github.com/bajutsu-e2e/bajutsu/pull/808)、
  [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811)、
  [#813](https://github.com/bajutsu-e2e/bajutsu/pull/813)、
  [#816](https://github.com/bajutsu-e2e/bajutsu/pull/816)、
  [#825](https://github.com/bajutsu-e2e/bajutsu/pull/825)。
- [Google Engineering Practices — What to look for in a code
  review](https://google.github.io/eng-practices/review/reviewer/looking-for.html) と
  [The Standard of Code Review](https://google.github.io/eng-practices/review/reviewer/standard.html) —
  項目 2 の背後にある設計優先の優先順位です。
- [PEP 8](https://peps.python.org/pep-0008/)、[PEP 257](https://peps.python.org/pep-0257/)、
  [PEP 20](https://peps.python.org/pep-0020/) — Python のスタイル、docstring、設計思想に関する典拠です。
- [Bandit のルール一覧](https://bandit.readthedocs.io/en/latest/plugins/index.html) — このリポジトリの
  `ruff` の `S` セレクトがすでにカバーしているルール群で、動機の節で参照しています。
- [OWASP Secure Code Review Cheat
  Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secure_Code_Review_Cheat_Sheet.html) —
  項目 5 の絞り込みの一般的な典拠です。
- [Conventional Comments](https://conventionalcomments.org/) — 項目 7 が採用するラベル仕様です。
- [Google 開発者向けドキュメントスタイルガイド](https://developers.google.com/style) と
  [Documentation Best Practices](https://google.github.io/styleguide/docguide/best_practices.html) —
  項目 8 の背後にある文言・用語の一貫性の典拠です。
