[English](BE-0221-android-scenario-portability-guarantee.md) · **日本語**

# BE-0221 — Android showcase の共有シナリオが無改修で動くことを保証する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0221](BE-0221-android-scenario-portability-guarantee-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0221") |
| 実装 PR | _PR 作成時に記入_ |
| トピック | Platform expansion (Android / Web / Flutter) |
| 関連 | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)、[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)、[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)、[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) |
<!-- /BE-METADATA -->

## はじめに

showcase の共有シナリオ一式（[`demos/showcase/scenarios/`](../../demos/showcase/scenarios)）は、
iOS 向け（`showcase-swiftui`、`showcase-uikit`）に id 中心で書かれており、Android の2つの UI
ツールキットのうち Compose（`showcase-compose`）にはすでに無改修のまま流用できています。
`Modifier.aid(...)` が `testTagsAsResourceId` 経由でドット区切りの SPEC id をそのまま
`resource-id` として露出するためです。もう一方の Android ツールキットである
Views（`showcase-views`）は、まだ同じシナリオ一式を実行できません。`android:id` は `.` と `-`
を許さないため、Views の id は機械的に `_` へ変換されており（`stable.refresh` →
`stable_refresh`）、この変換をどう扱うかという判断は
[`demos/showcase/android/README.md`](../../demos/showcase/android/README.md) と
[`showcase.config.yaml`](../../demos/showcase/showcase.config.yaml) の双方に名指しで
「adb ドライバが `.`⇄`_` を正規化してマッチさせるのか、Views 用に別のシナリオバリアントを
用意するのかは BE-0007 の設計判断待ち」と書かれたまま残っています。本項目はこの判断を確定させ、
「iOS 由来のシナリオが Android showcase 上でも無改修で動く」という主張を、片方のツールキットだけ
成り立つ主張から、両方のツールキットで CI により継続的にチェックされる保証へと引き上げます。

## 動機

[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) は
可搬性のモデルを明確に述べています。シナリオがプラットフォーム間で共有できるのは、セレクタが
**id** による場合に限られ、その id をどのアプリ側属性で満たすかは常にドライバの内部に閉じ、
シナリオ側には現れません。Compose はすでにこのモデルを裏づけています。`testTag` がドット区切りの
SPEC id をそのまま受け付けるため、シナリオ側を一切変更せずに共有シナリオ一式が Compose を動かせて
います。一方 Views は、このモデルがまだ実証されていない側です。プラットフォーム自身の `android:id`
命名規則が `.`/`-` → `_` という機械的な変換を強いており、`{ id: stable.refresh }` という
セレクタが `resource-id` の `stable_refresh` をどう見つけるべきかを決める仕組みが、コードベース
のどこにもまだ存在しません。

これは仮説上のギャップではありません。
[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) の Android e2e
CI レーン（`.github/workflows/android-e2e.yml`）は「共有シナリオが Android のビルドに対して
動き続けているか」を確認する唯一の自動化された手段ですが、実際に走らせているのは
[`--target showcase-compose`](../../demos/showcase/android/Makefile) だけです。
`demos/showcase/android/Makefile` の `e2e` ターゲットは `compose-build` にのみ依存しており、
`views-build` には依存していません。つまり現時点で「共有シナリオが Android 上で無改修で動く」が
真であるのは Android の2つの UI ツールキットのうち一方だけであり、もう一方は CI でも手動でも
検証されていません。Views は iOS 側でアクセシビリティ有効の基準ターゲットである UIKit
（`showcase-uikit`）の対をなすツールキットであり、その可搬性を未解決のまま放置することは、
Android showcase 自身のアクセシビリティ有効面の半分が、他のすべての showcase ターゲットが
共有している同じシナリオ資産でまだドッグフーディングできない状態を意味します。これは
「showcase がこの抽象化の成立を証明する」という BE-0009 と BE-0007 双方の前提を掘り崩します。

## 詳細設計

id マッチングの判断は、**ドライバではなくシナリオ側**で解決します。セレクタの `id` / `idMatches`
に複数の候補 id をリストとして書けるようにし、それらを OR で照合します。こうすると、1つの共有
シナリオが各プラットフォームの id 表記を同時に持てます。`stable.refresh`（iOS / Compose）と
`stable_refresh`（Views）の違いを、ドライバ側の `.`⇄`_` 書き換えに隠すのではなく、シナリオを
書く場所に**明示的に見える形**で残せます。ドライバ側の書き換えは暗黙的で、別々の id を取り違える
おそれがあり、しかも全解決経路に通さなければ機能しません。これは
[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) の意図的で
限定的な精緻化です。`id` をどのアプリ側属性（`resource-id`）で満たすかは引き続きドライバが持ち、
プラットフォームのネイティブな id 構文が SPEC の id をそのまま再現できないときに現れる**表記の
違い**だけをシナリオ側で表します。作業は MECE に4つへ分解できます。

1. **セレクタに OR の候補リストを追加する。** シナリオモデルと共有の `base.Selector` の両方で
   `Selector.id` / `Selector.idMatches` が `str | list[str]` を受け付けるようにし、`matches` /
   `find_all` はリストを OR として扱います。要素の identifier が候補の*いずれか*と一致（または
   グロブ一致）すれば、そのセレクタを満たします。単一値のセレクタは従来どおりなので、既存の
   シナリオもバックエンドも挙動は完全に変わりません。showcase の共有シナリオは id を両方の形で
   列挙し（`id: [stable.refresh, stable_refresh]`、`idMatches: [stable.row.*, stable_row_*]`）、
   `showcase-swiftui` / `showcase-compose` / `showcase-views` が同じファイルをそのまま実行します。
2. **曖昧セレクタの即時失敗を保つ。** OR は共有の決定論的コアが解決し、そのコアは 2 件以上の
   一致をすでに失敗させます。あるアプリの画面に現れる id の形は常に一方だけなので、候補リストは
   一意に解決します。仮に両方の形が同時に画面にあれば、`resolve_unique` はどちらかを選ばず
   `AmbiguousSelector` を送出します。OR が即時失敗すべき曖昧さを暗黙の選択に変えることはありません
   （プライムディレクティブ2）。テストでは、いずれの形でも解決すること、両方が存在すれば曖昧に
   なること、`find_all` が `elements` の順序で一致を返すこと、`idMatches` の OR が両ツールキットで
   `count` を駆動することを担保します。
3. **Android e2e の CI マトリクスを広げる。**
   [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) の
   `android-e2e.yml` と `demos/showcase/android/Makefile` の `e2e` ターゲットを拡張し、
   すでに `showcase-compose` に対して走らせている Stable タブのシナリオ一式を `showcase-views`
   にもビルド・実行します。これにより、この保証は一度手元で確認して終わりではなく、関連する
   push / PR のたびにチェックされ続けます。`showcase-views` はアンダースコアの id が
   `idNamespaces` による起動判定を効かなくする（`namespace_of` は `.` で分割し、Views には `.`
   がない）ため、`readyWhen` を両方の id 形で宣言します。
4. **未解決の注記を解消し、契約を記録する。** `demos/showcase/android/README.md`（と日本語版）と
   `showcase.config.yaml` にある「BE-0007 の設計判断待ち」という注記を確定済みのルールに置き換え、
   候補リストの DSL を、セレクタとプラットフォーム別 id 対応がすでに書かれている場所
   （`docs/scenarios.md`、`docs/drivers.md`、`docs/multi-platform.md`、`docs/architecture.md`。
   いずれも日本語版も）に記載します。これにより、将来新しいバックエンドを実装する人が、ある
   プラットフォームのネイティブな id 構文が SPEC の id をそのまま再現できない場合に id マッチングが
   どう振る舞うかを、定められた1つのルールとして把握できます。

### 非目標

本項目は新しいバックエンドやアクチュエーション方式を追加しません。セレクタに後方互換の候補リストを
加えますが、それ以外の DSL 面は変更せず、BE-0009 がすでに確立した id 名前空間による opt-in の
可搬性モデルを越えて可搬性を広げることも目指しません。あくまで、そのモデルの Android における
一点、すでに名指しされているギャップを閉じるだけにスコープを絞ります。

## 検討した代替案

- **ドライバ側の `.`⇄`_` 正規化（本提案の当初の推奨案）。** 当初の設計では、完全一致で id が
  見つからないとき `AdbDriver` が `.`/`-` → `_` に変換して再検索する方式（`id_fallback`）でした。
  レビューで退けました。これはドライバに隠れた*暗黙*の変換であり、しかも一致しなかったときにだけ
  効くフォールバックであるため、セレクタが実際にどの id へ解決するか書き手には分からず、曖昧な id
  指定を招きます。`.`/`-` → `_` の対応づけ自体も別々の id を取り違えます（`a.b` と `a-b` はどちらも
  `a_b` になります）。加えて、アサーション / `wait` / `forEach` は `query()` の出力をドライバの*外*で
  解決するため、共有リゾルバに正規化を通さなければこれらの経路には届きません。シナリオ側の候補
  リストは明示的で、すべての解決経路を一様に覆い、共有の決定論的コアには手を触れません。
- **`ios/scenarios-xcuitest` を手本にした Views 専用のシナリオ分岐。** 目の前の障害は解消します
  が、本項目が掲げる目的（共有シナリオ一式を1つに保つこと）を損ないます。「プラットフォームごとの
  シナリオバリアント」という形を、プラットフォーム間ではなく1つのプラットフォームの2つの
  ツールキットの間で繰り返すことになるため、退けます。候補リストなら1つのファイルで済みます。
- **Android e2e レーンを Compose のみのまま放置し、Views が壊れたときにだけ手を入れる。**
  検証されていない経路は、まさに
  [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) の
  コンフォーマンスの考え方が警告する、バックエンドごとの潜在的な乖離そのものです。Views の
  id マッチングは、作ったうえで継続的にチェックすべきものであり、文書化されたギャップのまま
  据え置くべきではないため、退けます。
- **BE-0007 と BE-0208 それぞれの作業分解に、この作業を直接組み込む。** アイデア出しの段階で
  検討しましたが、1つの end-to-end な保証を2つの別項目の作業分解に分割するのではなく、
  独自の進捗チェックリストを持ち、両方の前提項目へ「関連」リンクで結びついた、単独で追跡できる
  項目として残す判断をしました。

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
