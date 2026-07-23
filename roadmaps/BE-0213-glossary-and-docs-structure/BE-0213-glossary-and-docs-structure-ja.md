[English](BE-0213-glossary-and-docs-structure.md) · **日本語**

# BE-0213 — 用語集とドキュメント構成の見直し

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0213](BE-0213-glossary-and-docs-structure-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0213") |
| 実装 PR | [#853](https://github.com/bajutsu-e2e/bajutsu/pull/853) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

Bajutsu のドキュメントには、互いに近い意味を持つ用語がいくつも登場します。`scenario`、`goal`、
`step`、`driver`・`backend`・`actuator`・`platform`、`target`・`app`・`device`、`trace`・
`triage`、そして `capturePolicy` の「ルール」です。これらの語を一つずつ定義し、隣接する語との
関係を示す場所は、現状のドキュメントにはありません。この提案では、専用の用語集ページを追加し、
あわせて周辺のドキュメント構成（[`docs/overview.md`](../../docs/overview.md) の読む順序や、読者
が各用語に最初に出会う場所）も見直します。初めて読む人が、似た響きの二つの語のどちらを指してい
るのか迷わずに済むようにするためです。

## 動機

用語の定義を集めたページは、現状ひとつもありません。リポジトリ全体を「glossary」「用語」
「terminology」で検索しても、周辺的な言及以外は見つかりませんでした。[`docs/concepts.md`](../../docs/concepts.md)
が説明しているのは設計の理由（なぜ2層構成なのか、なぜセレクタは id を優先するのか）であり、語を
引く先としての用語リファレンスではありません。

この提案のために各ページを洗い出す過程で、具体的な不整合が一件見つかりました。
[`docs/getting-started.md:81`](../../docs/getting-started.md) はシナリオファイルを「a list of
named **tests**」と表現していますが、[`docs/scenarios.md`](../../docs/scenarios.md) とスキーマ
本体（`bajutsu/scenario/models/scenario.py` の `SCHEMA_VERSION`）は各エントリを「a **scenario**」
と呼びます。両方のページを読んだ読者は、同じ対象を指す二つの呼び名に出会うことになり、それが同じ
ものだと示す手がかりはどこにもありません。

この一件を除いても、いくつかの用語のかたまりは実際には一筋縄ではいかない内容を持ち、読者がページ
をまたいで自分でつなぎ合わせる形になっています。

- `driver`（抽象的な `Driver` インターフェース）・`backend`（idb・xcuitest・adb・playwright・fake
  というその実装であり、config の安定順リストでもある語）・`actuator`（ある実行で実際に選ばれた
  1つの backend）・`platform`（`ios`・`android`・`web`・`fake` という、backend に展開されるトーク
  ン）:[`docs/drivers.md`](../../docs/drivers.md) と [`docs/concepts.md`](../../docs/concepts.md) の
  第5節がそれぞれ一部を説明していますが、この関係を一箇所でまとめて述べたページはありません。
  （ここでの列挙はドキュメントではなく `bajutsu/backends.py` の `IMPLEMENTED` と
  `PLATFORM_ACTUATORS` に従っています。`docs/multi-platform.md` と CLAUDE.md は Android をいまだ
  「予定」と書いていますが、`adb` はすでに actuator として組み込まれており、これ自体がこの棚卸し
  で拾うべきずれです。）
- `trace`（完了した実行をテキストのタイムラインとして確認する）と `triage`（失敗した実行の原因を
  調べ、修正を提案する）:綴りが一文字違いの別コマンドであり、
  [`docs/cli.md`](../../docs/cli.md) を最初に読むときに混同しやすい組み合わせです。
- `target`（config の `targets.<name>` エントリ）・`app`（検証対象のアプリそのもの）・`device`
  （target を駆動する Simulator の実体）:初めて読む人には同じ概念に見えますが、実際には BE-0057
  が config 上の呼び名を `app` から `target` へ改めたのは、アプリそのものと区別するためでした。
- `capturePolicy` の各エントリ:[`docs/evidence.md`](../../docs/evidence.md) 全体を通じて
  「ルール」と呼ばれています。スキーマ側にも `CaptureRule` という型があります
  （`bajutsu/scenario/models/evidence.py`）が、`capturePolicy`・`CaptureRule`・地の文の「ルール」
  という3つの呼び名は、まだ一箇所で整理されていません。

Bajutsu はまだプレアルファ段階であり、[公開ドキュメントサイト](../BE-0093-public-docs-site/BE-0093-public-docs-site.md)
はすでに公開済みです。これから先、読者もページ数も、今のままの用語選びの上に積み上がっていきま
す。CLAUDE.md 自体のドキュメント執筆規約は、一文ごとに「独自の造語を避ける」「省略しない」ことを
すでに求めています。この提案は、その基準を一文単位ではなく構造として満たすものであり、どのページ
からも指し示せる一箇所を用意します。

## 詳細設計

作業は、独立して順に進められる5つの単位に分かれます。

1. **用語の棚卸し**:シナリオの記述、自然言語によるゴールや指示、CLI・config の表層に現れるすべて
   の用語を洗い出します。`scenario`、`goal`、`step`、`precondition`、`expect`（アサーション）、
   セレクタ・識別子、`component`、`capturePolicy` のルール、証跡（evidence）、Tier 1・Tier 2、
   `driver`・`backend`・`actuator`・`platform`、`target`・`app`・`device`、CLI の各動詞
   （`record`・`crawl`・`run`・`trace`・`triage`・`codegen`・`doctor`）、そして `from`
   （provenance、由来）が対象です。正とするのは実装（`bajutsu/scenario/` 配下の pydantic モデルと
   DSL 文法、および `bajutsu/backends.py` の backend レジストリ）であり、あるページがたまたま採用
   している言い回しではありません。ドキュメント側はこの点ですでにコードから遅れています
   （Android・`adb`）。
2. **新規ページ `docs/glossary.md`（+ `docs/ja/glossary.md`）**:用語ごとに、一文の定義と、詳しく
   説明しているページ・モジュールへのポインタを置きます。上で挙げた各クラスタについては、明示的
   な切り分けも添えます。たとえば `driver`・`backend`・`actuator`・`platform` は簡単な対応表で、
   「シナリオファイルはシナリオのリストを持ち、各エントリ自体がシナリオである（テストではない）」
   は一文で、`trace` と `triage` の違いも一文で決着させます。既存のページは、用語が最初に登場する
   箇所を用語集の該当項目にリンクし、その場で説明を繰り返さないようにします。
3. **棚卸しで見つかった不整合の修正**:最低限、確認済みの一件（`docs/getting-started.md:81` の
   「a list of named tests」を、`docs/scenarios.md` 自身の定義に合わせて「a list of named
   scenarios」に直す）を修正します。棚卸しの勢いが残っているうちに、`getting-started.md` の残り、
   `docs/index.md`、`docs/overview.md` も同種のずれがないか点検します。
4. **読む順序と構成の見直し**:用語集を [`docs/overview.md`](../../docs/overview.md) の読む順序の
   どこに置くか決めます。候補の一つは `concepts.md` の直前か直後で、設計原則を読む前に語彙を手に
   している状態を作ります。あわせて、`concepts.md` の各原則の説明を、用語を再定義する代わりに用語
   集へのリンクで済ませられないかも検討します。決めた順序に合わせて `README.md` と
   `docs/index.md` の案内も更新します。
5. **日本語版の作成**:`docs/ja/glossary.md` は [`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/)
   スキルに従って自然な日本語で書き、英語の直訳にはしません。DESIGN.md が使う日本語の語彙は、英語
   版用語集の項目と必ずしも一対一に対応しません(たとえば「証跡」は evidence と trace の両方を指す
   場面で緩く使われています)。日本語版の項目では、対応が一対一でない箇所を明示します。

対象外とする範囲もあります。コード・CLI フラグ・config キーの水準での用語の改名です
(`capturePolicy`、`backend`、Tier 1・Tier 2 などはそのままにします)。この提案は、Bajutsu が現に
使っている用語を文書化し切り分けるものであり、どれかを改名すべきだと主張するものではありません。
棚卸しの過程で、ドキュメントだけでなく語そのものの改名を検討する価値がありそうな用語が見つかった
場合は、この提案の範囲を広げるのではなく、別の提案として独立させます。

この提案と並行して起票した姉妹提案「Web 版のみで完結する初学者向けチュートリアル」は、この提案
が先に着地することを前提にしています。新しいチュートリアルの導線が、この用語集で決着させた語彙を
そのまま使えるようにするためであり、並行して独自の言い回しを作らずに済ませます。両者は互いに参照
し合う姉妹項目であり、両者を結ぶ `関連` のメタデータリンクは、CI が実 ID を採番したあとに追加しま
す(プレースホルダの `BE-0213` は別の新規項目を相互参照できません。項目ごとの書き換えが、この項目
自身の番号に解決してしまうためです)。

## 検討した代替案

- **用語集を新規ページにせず `concepts.md` に統合する案**:見送りました。`concepts.md` が説明する
  のは Bajutsu がなぜ今の形をしているかという設計の理由であり、用語集が説明するのは語の意味その
  ものです。両者を混ぜると、用語を追加するたびに設計理由を説明するページを編集することになり、語
  を一つ引くだけのために設計理由を読み進める必要が生じます。
- **一つの正典的な用語集を持たず、各機能ページに「このページで使う用語」の小さな囲みを置く案**:
  見送りました。この方法では、この提案のきっかけになったページ間の不整合という問題そのものが解決
  しません。各ページが自分の定義を書き続ける限り、「test」と「scenario」がずれたのと同じ経緯を
  繰り返します。あるページから別の語を参照するにも、指し示す先が一箇所に定まっている必要がありま
  す。
- **今の用語と構成のまま、今後のドキュメント修正のたびに場当たり的にずれを直す案**:見送りまし
  た。全ページを横断的に洗い出す作業を意図的に行わない限り、`driver`・`backend`・`actuator`・
  `platform` のような近縁語のかたまりは、どの個別の修正にも問題として浮かび上がりません。今回も、
  すべてのページを並べて grep することで初めて見えてきました。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 1. 用語の棚卸し(scenario・goal・step・driver-backend-actuator-platform・target-app-device・
      trace-triage・capturePolicy ルール・Tier 1-2・CLI 動詞・provenance)
- [x] 2. 新規 `docs/glossary.md`(+ `docs/ja/glossary.md`)の作成、各クラスタの切り分けを含む
- [x] 3. 確認済みのずれの修正(`getting-started.md` の「named tests」および点検で見つかった同種の
      箇所)
- [x] 4. 読む順序・構成の見直し(`docs/overview.md`、`README.md`、`docs/index.md` の案内)
- [x] 5. `japanese-tech-writing` スキルに従った日本語版の作成

**ログ**

- 2026-07-09（[#853](https://github.com/bajutsu-e2e/bajutsu/pull/853)）：`docs/glossary.md` と `docs/ja/glossary.md` を追加しました。ドメイン
  用語を一語ずつ定義し、`driver` / `backend` / `actuator` / `platform`、`target` / `app` / `device`、
  scenario と test、`trace` と `triage` の各クラスタには切り分けの表を添えています。正とするのは
  `bajutsu/backends.py` とシナリオモデルであり、あるページの言い回しではありません。このページを
  `mkdocs.yml` の Concepts セクション（`用語集` のナビ訳つき）と `docs/overview.md` の読む順序の 2 番目に
  組み込み、`docs/index.md` と `docs/README.md` からの案内も足しました。確認済みの
  `getting-started.md` の「a list of named tests」→「scenarios」のずれを修正しました。`concepts.md` の
  §2・§5・§7 を、Tier 1-2、driver/backend/actuator/platform のクラスタ、`capturePolicy` をその場で
  再定義する代わりに用語集へリンクする形に簡約しました（§5 の簡約により、そのページから「Android は
  予定」というずれと不完全な backend の列挙も落ちます）。用語集の backend の表には `adb` が予定ではなく
  実装済みであることを記録し、`docs/multi-platform.md` と `CLAUDE.md` に残る「Android は予定」の記述は、
  範囲を絞ったフォローアップとして残しました。状態を実装済みに更新しました。

## 参考

- [`docs/concepts.md`](../../docs/concepts.md)：用語集がリンクすべき設計の理由であり、再掲はしない
- [`docs/overview.md`](../../docs/overview.md)：この提案が見直す読む順序
- [`docs/getting-started.md`](../../docs/getting-started.md)：確認済みの「named tests」のずれがある場所
- [`docs/scenarios.md`](../../docs/scenarios.md) ・ [`docs/dsl-grammar.md`](../../docs/dsl-grammar.md)：用語定義の正となるスキーマ側の情報源
- [`docs/drivers.md`](../../docs/drivers.md) ・ [`docs/cli.md`](../../docs/cli.md)：driver・backend・actuator と trace・triage のクラスタ
- [BE-0093](../BE-0093-public-docs-site/BE-0093-public-docs-site.md)：この用語集を公開する先の公開ドキュメントサイト
- [BE-0057](../BE-0057-rename-apps-to-targets/BE-0057-rename-apps-to-targets.md)：この用語集が文書化する、以前の `app` → `target` の config 改称
- CLAUDE.md のドキュメント執筆規約(「独自の造語を避ける」「省略しない」)：この提案が構造として満たす、一文単位の基準
