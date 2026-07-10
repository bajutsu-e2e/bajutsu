[English](BE-0221-android-scenario-portability-guarantee.md) · **日本語**

# BE-0221 — Android showcase の共有シナリオが無改修で動くことを保証する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0221](BE-0221-android-scenario-portability-guarantee-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0221") |
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
セレクタが `resource-id` の `stable_refresh` をどう見つけるべきか、ドライバ側の決定がコードベース
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

作業は MECE に4つへ分解できます。

1. **id マッチングの判断を、ドライバ側で確定させる。** シナリオを分岐させるのではなく、ドライバ側で
   正規化する方式を推奨します。`AdbDriver` の `id` 検索が `resource-id` の完全一致を見つけられない
   場合、セレクタの id 中の `.` と `-` を `_` に変換したうえで再検索します。これは
   `demos/showcase/android/README.md` がすでに文書化している、Views ビルド側の変換を逆向きに
   なぞるだけの機械的な処理です。この方式なら「id 規約の違いは常にドライバの内部に閉じる」
   （BE-0009）という原則を保ったまま、`demos/showcase/ios/scenarios-xcuitest` が iOS の `-noax`
   ターゲット向けにすでに行っているようなシナリオの分岐を、`demos/showcase/scenarios/` に持ち込まず
   に済みます。BE-0009 は「1つの YAML を3回走らせる」を可搬性のモデルとしてすでに退けており、
   Views 専用のシナリオ分岐はその退けられた形を、プラットフォーム間ではなく1つのプラットフォームの
   内部でもう一段繰り返すことになるため、この項目ではあえて選びません。ドライバ側の変更そのものの
   実装は引き続き [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) が担い、本項目は
   その判断と理由づけを定めます。
2. **正規化の再検索を経ても、曖昧セレクタの即時失敗を保つ。** 正規化のための再検索が、本物の
   曖昧セレクタ失敗を覆い隠してはなりません（プライムディレクティブ2）。完全一致がすでに一意に
   解決していれば再検索は行わず、完全一致が曖昧であれば、再検索がその曖昧な候補のどれかを
   選んで「助ける」ことがあってはなりません。再検索経路（`.`/`-` → `_` の変換後にのみ一致する
   セレクタ）と、この失敗保持のケース（完全一致がすでに曖昧なら曖昧なままであること、
   `_` 正規化後の一致自体が曖昧ならそれも失敗すること）の両方をドライバレベルのテストで担保します。
3. **Android e2e の CI マトリクスを広げる。**
   [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) の
   `android-e2e.yml` と `demos/showcase/android/Makefile` の `e2e` ターゲットを拡張し、
   すでに `showcase-compose` に対して走らせている同じシナリオ一式を `showcase-views` に対しても
   ビルド・実行します。これにより、この保証は一度手元で確認して終わりではなく、関連する push /
   PR のたびにチェックされ続けます。
4. **ドキュメントの未解決の注記を解消する。** 判断が固まり次第、
   `demos/showcase/android/README.md` と `showcase.config.yaml` にある「BE-0007 の設計判断待ち」
   という注記を確定済みのルールに置き換え、`docs/architecture.md`（と日本語版）の既存の
   プラットフォーム別セレクタ対応表の隣に id マッチングの契約を記録します。これにより、将来
   新しいバックエンドを実装する人が、あるプラットフォームのネイティブな id 構文が SPEC の id を
   そのまま再現できない場合に id マッチングがどう振る舞うべきかを、1か所を見れば把握できるように
   します。

### 非目標

本項目は新しいバックエンドやアクチュエーション方式を追加せず、シナリオ DSL も変更しません。
BE-0009 がすでに確立した id 名前空間による opt-in の可搬性モデルを越えて可搬性を広げることも
目指しません。あくまで、そのモデルの Android における一点、すでに名指しされているギャップを
閉じるだけにスコープを絞ります。

## 検討した代替案

- **`ios/scenarios-xcuitest` を手本にした Views 専用のシナリオ分岐。** 目の前の障害は解消します
  が、本項目が掲げる目的そのものを損ないます。BE-0009 が iOS の `-noax` ケースだけに限定した
  「プラットフォームごとのシナリオバリアント」という形を、プラットフォーム間ではなく1つの
  プラットフォームの2つのツールキットの間で繰り返すことになるため、退けます。
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

- [ ] id マッチングの判断を確定させる — `AdbDriver` における `.`/`-` → `_` 正規化の再検索（BE-0007 が担当）。
- [ ] 正規化の再検索経路に対する、曖昧セレクタ即時失敗のテストカバレッジ。
- [ ] `android-e2e.yml` / Android showcase の `Makefile` の `e2e` ターゲットを広げ、`showcase-views` も実行する。
- [ ] README / config にある「BE-0007 の設計判断待ち」という注記を確定済みのルールに置き換え、`docs/architecture.md`（+ 日本語版）に契約を記載する。

## 参考

- [`demos/showcase/android/README.md`](../../demos/showcase/android/README.md) — id 規約の説明と、
  本項目が解決する未解決の注記。
- [`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml) —
  `showcase-compose` / `showcase-views` ターゲットの定義と、同じ注記。
- [`.github/workflows/android-e2e.yml`](../../.github/workflows/android-e2e.yml)、
  [`demos/showcase/android/Makefile`](../../demos/showcase/android/Makefile) — 現状
  `showcase-compose` のみに絞られている CI レーン。
- [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) — 本項目の正規化の判断が
  拡張する Android バックエンドとドライバ。
- [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) —
  本項目が支持する可搬性モデル（id 名前空間による opt-in、id 規約はドライバの内部に閉じる）。
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) —
  「一度確認して終わりではなく継続的にチェックする」という考え方の拠り所。
- [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) — 本項目が
  広げる Android e2e CI レーン。
- 2026-07-10 のアイデア出しセッションに由来する。
