[English](BE-0221-android-scenario-portability-guarantee.md) · **日本語**

# BE-0221 — Android showcase の共有シナリオが無改修で動くことを保証する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0221](BE-0221-android-scenario-portability-guarantee-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0221") |
| 実装 PR | [#889](https://github.com/bajutsu-e2e/bajutsu/pull/889) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)、[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)、[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)、[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) |
<!-- /BE-METADATA -->

## はじめに

showcase には共有シナリオ一式（[`demos/showcase/scenarios/`](../../demos/showcase/scenarios)）があります。
このシナリオは、iOS 向け（`showcase-swiftui`、`showcase-uikit`）に id 中心で書かれています。Android の 2 つの
UI ツールキットのうち Compose（`showcase-compose`）は、すでにこの一式を無改修のまま流用できています。
`Modifier.aid(...)` が `testTagsAsResourceId` 経由で、ドット区切りの SPEC id をそのまま露出するためです。

もう一方の Android ツールキットである Views（`showcase-views`）は、まだ同じ一式を実行できません。`android:id`
は `.` と `-` を許さないため、Views の id は機械的に `_` へ変換されています（`stable.refresh` →
`stable_refresh`）。

この変換をどう扱うかという id マッチングの問いは、
[`demos/showcase/android/README.md`](../../demos/showcase/android/README.md) と
[`showcase.config.yaml`](../../demos/showcase/showcase.config.yaml) の双方に名指しで残っています。

> adb ドライバが `.`⇄`_` を正規化してマッチさせるのか、Views 用に別のシナリオバリアントを用意するのかは
> BE-0007 の設計判断待ち。

この項目は、この判断を確定させます。現状、「iOS 由来のシナリオが Android showcase 上でも無改修で動く」という
主張が成り立つのは、片方のツールキットだけです。項目はこの主張を、両方のツールキットで成り立つ保証へ引き上げ、
CI で継続的にチェックします。

## 動機

[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) は、可搬性のモデルを
明確に述べています。シナリオをプラットフォーム間で共有できるのは、セレクタが **id** による場合です。その id を
どのアプリ側属性で満たすかは、ドライバだけが決め、シナリオには現れません。

Compose はすでにこのモデルを裏づけています。`testTag` がドット区切りの SPEC id をそのまま受け付けるので、
共有シナリオ一式は、シナリオ側を一切変更せずに Compose を動かせます。

一方 Views は、このモデルがまだ実証されていない側です。プラットフォーム自身の `android:id` 命名規則が、
`.`/`-` → `_` という機械的な変換を強いています。`{ id: stable.refresh }` というセレクタが `resource-id` の
`stable_refresh` をどう見つけるかを決める仕組みは、今のコードベースのどこにもありません。

これは仮説上のギャップではありません。[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)
の Android e2e CI レーン（`.github/workflows/android-e2e.yml`）は、共有シナリオに関する唯一の自動化された手段です。
この手段が確認するのは、共有シナリオが Android のビルドに対して動き続けているかどうかです。

このレーンが走らせているのは、[`--target showcase-compose`](../../demos/showcase/android/Makefile) だけです。
`demos/showcase/android/Makefile` の `e2e` ターゲットは `compose-build` に依存し、`views-build` には依存して
いません。

つまり「共有シナリオが Android 上で無改修で動く」が真であるのは、2 つの UI ツールキットのうち一方だけです。
もう一方については、CI でも手動でも検証していません。

Views は、iOS 側でアクセシビリティ有効の基準ターゲットである UIKit（`showcase-uikit`）の対をなすツールキット
です。Views の可搬性を未解決のまま放置すると、Android showcase 自身のアクセシビリティ有効面の半分が、他のすべての
showcase ターゲットが共有するシナリオ資産を使えません。これは、BE-0009 と BE-0007 の双方が前提とする「showcase が
この抽象化の成立を証明する」を掘り崩します。

## 詳細設計

この項目は、id マッチングの判断を**ドライバではなくシナリオ側**で解決します。セレクタの `id` / `idMatches` に
複数の候補 id をリストとして書けるようにし、それらを OR で照合します。1 つの共有シナリオが、各プラットフォームの
id 表記を同時に持てます。

`stable.refresh`（iOS / Compose）と `stable_refresh`（Views）の違いは、シナリオを書く場所に**明示的に見える形**で
残ります。ドライバ側の `.`⇄`_` 書き換えなら、この違いを隠してしまいます。その書き換えは暗黙的で、別々の id を
取り違えるおそれがあり、全解決経路に通す必要もあります。

この判断は、[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) の意図的で
限定的な精緻化です。`id` をどのアプリ側属性（`resource-id`）で満たすかは、引き続きドライバが持ちます。
プラットフォームのネイティブな id 構文が SPEC の id をそのまま再現できないときの**表記の違い**だけを、
シナリオ側で表します。

作業は MECE に 4 つへ分解できます。

1. **セレクタに OR の候補リストを追加する。** シナリオモデルと共有の `base.Selector` の両方で、
   `Selector.id` と `Selector.idMatches` が `str | list[str]` を受け付けます。`matches` と `find_all` は、
   リストを OR として扱います。要素の identifier が候補の*いずれか*と一致（またはグロブ一致）すれば、そのセレクタを
   満たします。単一値のセレクタは従来どおりなので、既存のシナリオもバックエンドも挙動は変わりません。
   showcase の共有シナリオは id を両方の形で列挙します（`id: [stable.refresh, stable_refresh]`、
   `idMatches: [stable.row.*, stable_row_*]`）。`showcase-swiftui`、`showcase-compose`、`showcase-views` が、
   同じファイルをそのまま実行します。
2. **曖昧セレクタの即時失敗を保つ。** OR は共有の決定論的コアが解決し、そのコアは 2 件以上の一致をすでに失敗
   させます。あるアプリの画面に現れる id の形は一方だけなので、候補リストは一意に解決します。仮に両方の形が同時に
   画面にあっても、`resolve_unique` はどちらかを選ばず `AmbiguousSelector` を送出します。OR が、即時失敗すべき
   曖昧さを暗黙の選択に変えることはありません（プライムディレクティブ 2）。テストは次の 4 点を担保します。いずれの形
   でも解決すること、両方が存在すれば曖昧になること、`find_all` が `elements` の順序で一致を返すこと、`idMatches`
   の OR が両ツールキットで `count` を駆動することです。
3. **Android e2e の CI マトリクスを広げる。**
   [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) の `android-e2e.yml` と
   `demos/showcase/android/Makefile` の `e2e` ターゲットを拡張します。すでに `showcase-compose` に対して走らせて
   いる Stable タブのシナリオ一式を、`showcase-views` にもビルドして実行します。これにより、保証は一度手元で確認
   して終わりではなく、関連する push / PR のたびに CI がチェックし続けます。`showcase-views` は、`readyWhen` を
   両方の id 形で宣言します。アンダースコアの id が `idNamespaces` による起動判定を効かなくするためです
   （`namespace_of` は `.` で分割しますが、Views には `.` がありません）。
4. **未解決の注記を解消し、契約を記録する。** `demos/showcase/android/README.md`（と日本語版）と
   `showcase.config.yaml` にある「BE-0007 の設計判断待ち」という注記を、確定済みのルールに置き換えます。
   候補リストの DSL は、セレクタとプラットフォーム別 id 対応がすでに書かれている場所に記載します
   （`docs/scenarios.md`、`docs/drivers.md`、`docs/multi-platform.md`、`docs/architecture.md`。いずれも日本語版も）。
   将来新しいバックエンドを実装する人は、定められた 1 つのルールを見つけられます。ルールは、プラットフォームの
   ネイティブな id 構文が SPEC の id をそのまま再現できない場合に、id マッチングがどう振る舞うかを述べます。

### 非目標

この項目は、新しいバックエンドやアクチュエーション方式を追加しません。セレクタに後方互換の候補リストを加えますが、
それ以外の DSL 面は変更しません。BE-0009 がすでに確立した id 名前空間による opt-in の可搬性モデルを越えて、
可搬性を広げることも目指しません。そのモデルの Android における一点、すでに名指しされているギャップを閉じるだけに
スコープを絞ります。

### 2 つの id 形を越えた拡張

現在のロードマップが必要とするのは、2 つの形（dotted / underscore）です。iOS と Android Compose は SPEC の id を
そのまま表出し、Android Views はアンダースコアへ写します。候補リストは、ネイティブ id 構文が異なるプラット
フォームが増えるごとに 1 つずつ項目が増えます。リストが伸びても「canonical 形を先頭に」の規則は保たれます。

この手作業を今ここで一般化しないのは意図的で、理由は 2 つあります。1 つ目は、第 3 の id 表記がまだ具体化して
いないことです（Flutter バックエンド [BE-0008](../BE-0008-flutter-support/BE-0008-flutter-support.md) は、まだ
Proposal です）。2 つ目は、壊れ方が target 単位で目立つことです。候補が欠けたセレクタはその target の実行で失敗
し、どこかで静かに失敗するわけではありません。取りこぼしは、新しい target を動かした瞬間に表面化します。

第 3 のプラットフォームが実際に来たときは、`audit` 側のカバレッジ検査が自然な形です。この検査は、各 target の
id 規約と各セレクタ候補を突き合わせます。候補をすでに列挙している `referenced_ids` の拡張です。検査は、検証すべき
具体的な規約を持つそのプラットフォームと**一緒に**入れます。投機的な N 形の仕組みを今作るのは避けます。

## 検討した代替案

- **ドライバ側の `.`⇄`_` 正規化（本提案の当初の推奨案）。** 当初の設計では、完全一致で id が見つからないとき、
  `AdbDriver` が `.`/`-` → `_` に変換して再検索する方式（`id_fallback`）でした。レビューで退けました。
  この変換はドライバに隠れた*暗黙*のものです。しかも一致しなかったときにだけ効くフォールバックなので、セレクタが
  実際にどの id へ解決するか書き手には分からず、曖昧な id 指定を招きます。`.`/`-` → `_` の対応づけ自体も、別々の
  id を取り違えます（`a.b` と `a-b` はどちらも `a_b` になります）。加えて、アサーション / `wait` / `forEach` は
  `query()` の出力をドライバの*外*で解決します。共有リゾルバに正規化を通さなければ、これらの経路には届きません。
  シナリオ側の候補リストは明示的で、すべての解決経路を一様に覆い、共有の決定論的コアには手を触れません。
- **`ios/scenarios-xcuitest` を手本にした Views 専用のシナリオ分岐。** 目の前の障害は解消しますが、この項目が
  掲げる目的（共有シナリオ一式を 1 つに保つこと）を損ないます。「プラットフォームごとのシナリオバリアント」という
  形を、プラットフォーム間ではなく 1 つのプラットフォームの 2 つのツールキットの間で繰り返すことになるため、
  退けます。候補リストなら 1 つのファイルで済みます。
- **Android e2e レーンを Compose のみのまま放置し、Views が壊れたときに手を入れる。** 検証されていない経路は、
  [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) のコンフォーマンスの考え方が
  警告する、バックエンドごとの潜在的な乖離そのものです。Views の id マッチングは、作ったうえで継続的にチェック
  すべきものです。文書化されたギャップのまま据え置くべきではないため、退けます。
- **BE-0007 と BE-0208 それぞれの作業分解に、この作業を直接組み込む。** アイデア出しの段階で検討しました。
  1 つの end-to-end な保証を 2 つの別項目の作業分解に分割するのではなく、単独で追跡できる項目として残す判断を
  しました。この項目は独自の進捗チェックリストを持ち、両方の前提項目へ「関連」リンクで結びつきます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] セレクタを拡張する — シナリオモデルと `base.Selector` で `id` / `idMatches` が OR の候補リスト（`str | list[str]`）を受け付け、`matches` / `find_all` が候補のいずれにも一致し、共有 showcase シナリオが id を両方の形で列挙する。
- [x] 曖昧セレクタ即時失敗のテストカバレッジ — いずれの形でも解決すること、両方存在すれば曖昧になること、`find_all` が `elements` の順序を保つこと、`idMatches` の OR が両ツールキットで `count` を駆動すること。
- [x] `android-e2e.yml` / Android showcase の `Makefile` の `e2e` ターゲットを広げ、`showcase-views` もビルド・実行する。`showcase-views` は `readyWhen` を両方の id 形で宣言する。
- [x] README（+ 日本語版）/ config にある「BE-0007 の設計判断待ち」という注記を確定済みのルールに置き換え、候補リストの DSL を `docs/scenarios.md`、`docs/drivers.md`、`docs/multi-platform.md`、`docs/architecture.md`（いずれも日本語版も）に記載する。

ログ：

- 2026-07-10 — 当初提案したドライバ側の正規化ではなく、シナリオ側の OR 候補リストとして実装しました
  （*検討した代替案* を参照）。id 規約をシナリオに明示的に残し、共有の決定論的コアには手を触れません。
  セレクタ DSL を拡張し、共有 showcase シナリオが id を両方の形で列挙し、Android e2e レーンが
  `showcase-compose` と `showcase-views` を同じセットで駆動し、ドキュメントと注記に契約を記録しました。

## 参考

- [`demos/showcase/android/README.md`](../../demos/showcase/android/README.md) — id 規約の説明と、
  本項目が解決する未解決の注記。
- [`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml) —
  `showcase-compose` / `showcase-views` ターゲットの定義と、同じ注記。
- [`.github/workflows/android-e2e.yml`](../../.github/workflows/android-e2e.yml)、
  [`demos/showcase/android/Makefile`](../../demos/showcase/android/Makefile) — 本項目が
  `showcase-views` も `showcase-compose` と並べて駆動するよう広げる CI レーン。
- [`docs/scenarios.md`](../../docs/scenarios.md) — 本項目が追加する候補リストのセレクタ DSL。
- [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) — 本項目が id の厳密一致を
  そのまま保つ Android バックエンドとドライバ。
- [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) —
  本項目が精緻化する可搬性モデル。`id` をどの属性で満たすかは引き続きドライバが持ち、
  プラットフォームによる id 表記の違いはシナリオ側の OR で表します。
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) —
  「一度確認して終わりではなく継続的にチェックする」という考え方の拠り所。
- [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) — 本項目が
  広げる Android e2e CI レーン。
- 2026-07-10 のアイデア出しセッションに由来する。
