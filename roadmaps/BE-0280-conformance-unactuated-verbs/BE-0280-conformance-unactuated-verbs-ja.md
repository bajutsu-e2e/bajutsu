[English](BE-0280-conformance-unactuated-verbs.md) · **日本語**

# BE-0280 — 全バックエンド共通のテストを、アクチュエートされていない Driver 操作へ広げる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0280](BE-0280-conformance-unactuated-verbs-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0280") |
| 実装 PR | [#1204](https://github.com/bajutsu-e2e/bajutsu/pull/1204) |
| トピック | ドライバとバックエンドのアーキテクチャ |
| 関連 | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md), [BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance-ja.md), [BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md), [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) には、すべての
[バックエンド](../../docs/ja/glossary.md#driver-backend-actuator-platform)を同じ 1 本のテスト
（`tests/driver_conformance.py`）にかけ、どれも同じように振る舞うことを確かめる仕組みがあります。
バックエンドごとに別々のテストを書くのではなく、この 1 本を各バックエンドに対して流します。
こうして「どのバックエンドも同じように振る舞う」という前提が、そう期待するだけの状態から、実際の
検査で裏づけられた状態に変わります。

対象は、Linux ゲート上の `FakeDriver` と、実機の idb、XCUITest、adb、Playwright です。adb レーンは
[BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance-ja.md)
が追加しました。

この共通テストが現在カバーしているのは、次の挙動です。

- tap
- label と trait による解決
- multi-touch と `selectOption` の capability の約束
- 条件待機のセマンティクス

`Driver` プロトコルの操作のうち、共通テストの範囲外にあり、どのレーンでも実機で一度もアクチュエート
されていないものがあります。テキスト編集の一群（`delete_text`、`select_all`、`copy_selection`）と
`tap_point` です。本項目では、これらの操作を共通テストに取り込みます。1 本のテストが、すべての
バックエンドでそれらを一度に検証します。

## 動機

テキスト編集ステップ（[BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md)）は
すでに実装されています。各バックエンドのコマンド構築は、サブプロセスをモックしたユニットテストで
検証されています。ところが、どのレーンも `delete_text`、`select_all`、`copy_selection` を実デバイスや
実ブラウザに対してアクチュエートしていません。ステップから観測可能なフィールドの変化までの往復は、
一度も確認されていません。

`tap_point` も同じ状況にあります。実ドライバのコマンドテストは XCUITest と Playwright にしかなく、
この操作をアクチュエートするレーンはありません。`tap_point` は、アラート解消の経路の土台でもあります。
[BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)
が依拠する、視覚で位置を特定した座標タップです。実機で未確認のまま残すのは、実際のリスクです。

このギャップを塞ぐのにもっとも安価な場所が、共通テストです。1 つの仕様が 5 つのバックエンドに対して
走ります。バックエンドごとにショーケースのシナリオを 1 本ずつ書く代わりに、1 つのテスト本体を足せば
全バックエンドにカバレッジが広がります。

capability モデルは、共通テストの形をすでに定めています。capability を宣言したバックエンドは
アクチュエートしなければなりません。宣言していないバックエンドは、黙って何もしないのではなく、
`UnsupportedAction` を明示的に送出しなければなりません。テキスト編集の操作と `tap_point` は、この同じ
パターンを拡張したものです。

作業を形づくる制約が 1 つあります。実機の適合性ハーネスは、要求された画面を識別子つきボタンの並びとして
実体化します。この形は tap や解決のテストには十分ですが、テキスト編集には足りません。`delete_text` や
`select_all` を試すには、画面上に編集可能な実テキストフィールドが必要だからです。

そのため、共通テストへの追加とあわせて、各プラットフォームの適合性画面に小さな拡張を加えます。
対象は、iOS の `ConformanceView`、Compose の `ConformanceScreen`、web ハーネスが描画する document です。
各画面に、編集可能なフィールドと既知フレームの要素を出します。

## 詳細設計

この節は提案の粒度で書きます。作業は、相互に排他的で全体を網羅する（MECE）以下の単位に分かれます。

- **各操作の不変条件を列挙する。** `Driver` プロトコルに基づき、新しい操作が満たすべき不変条件を
  明文化します。
  - テキスト編集：その操作をアクチュエートするバックエンドでは、`type_text` に続けて `select_all`、
    `copy_selection` が `UnsupportedAction` なしに完了します。`delete_text` はフィールドの報告長を
    減らします。アクチュエートしないバックエンドは `UnsupportedAction` を送出します。現状でこれに
    当たるのは idb で、`select_all` / `copy_selection` を無条件に送出します。
  - 最初に決める検討事項：`MULTI_TOUCH` や `SELECT_OPTION` と違い、テキスト編集の select/copy 用の
    `Capability` トークンはまだ存在しません。選択肢は 2 つあります。1 つ目は、共通テストが
    アクチュエートと送出の挙動を直接表明することです。2 つ目は、新しい capability を導入して同じ
    ようにゲートすることです。その場合は各バックエンドの `CAPABILITIES` に反映します。現状では
    adb、Playwright、XCUITest が select/copy をアクチュエートし、idb は送出します。
  - `tap_point`：既知フレームの要素への座標タップは、その要素への意味的な tap と同じ観測可能な効果を
    持ちます。
- **3 つの適合性画面を拡張する。** 対象は、iOS の `ConformanceView`、Compose の `ConformanceScreen`、
  web ハーネスの document です。各画面に、編集可能なテキストフィールドと既知フレームの要素を
  加えます。準備完了マーカーの扱いはそのまま保ちます。
- **共通テストの本体を追加する。** 新しい不変条件を `tests/driver_conformance.py` に一度だけ書きます。
  pytest が、各バックエンドのサブクラスに対して収集します。
- **実機ハーネスへ実体化を配線する。** 各ハーネスの画面実体化の経路を拡張します。経路は、iOS の
  スペックファイル書き込み、Android のインテント再シード、web の `set_content` です。テスト本体が
  走る前に、フィールドと既知フレームの要素が存在するようになります。
- **capability の宣言が挙動と一致することを確認する。** 各バックエンドが宣言する capability を、
  アクチュエートする操作と拒む操作に照らして確かめます。共通テストの「約束と挙動の一致」チェックが
  正しく保たれます。

## 検討した代替案

- **共通テストではなく、ショーケースにバックエンドごとのアクチュエーションシナリオを置く。**
  テキスト編集や `tap_point` のシナリオをバックエンドごとに書くと、同じ意図が 4 回重複します。
  バックエンドごとのずれも招きます。共通テストは、まさにそのずれを防ぐために存在します。1 つの仕様を
  すべてのバックエンドに適用することが、この設計の要点です。
- **テキスト編集の操作をコマンド構築のユニットテストにとどめる。** サブプロセスをモックしたテストは、
  バックエンドが組み立てる argv、HTTP、キーの組み合わせを検証します。実デバイスが編集を実行する
  ことは、一度も確認しません。ツールがうたっていてどのレーンも観測しない capability は、チェックの
  ない約束です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 各操作の不変条件を列挙する（テキスト編集の往復、`tap_point`、バックエンドごとのアクチュエート／送出、テキスト編集 capability を追加するかの判断）。
- [x] iOS / Compose / web の適合性画面に編集可能フィールドと既知フレーム要素を加える。
- [x] 新しいテスト本体を `tests/driver_conformance.py` に追加する。
- [x] 実機ハーネスへ画面実体化を配線する。
- [x] capability の宣言がバックエンドごとのアクチュエート／送出の挙動と一致することを確認する。

**ログ**

- 2026-07-19：1 つの PR（[#1204](https://github.com/bajutsu-e2e/bajutsu/pull/1204)）で実装しました。新しい `Capability.TEXT_SELECTION` トークンが
  `select` / `copy`（全選択とクリップボードへのコピー）を門にします。XCUITest / adb / Playwright /
  `fake` はこれを宣言し、idb は宣言せず `UnsupportedAction` を送出します。preflight
  （`capability_preflight.py`）は idb 上の `select` / `copy` シナリオを `selectOption` と同様に事前に
  弾きます。`delete` / `clear` は門にしません（`delete_text` はどのバックエンドも実現できます）。契約
  （`tests/driver_conformance.py`）にはテスト本体を 3 つ追加しました。テキスト選択のアクチュエート／送出、
  削除で文字数が減る往復、そして `tap_point` が semantic tap と同じくフィールドをフォーカスすることです。
  各適合性画面（iOS `ConformanceView`、Compose `ConformanceScreen`、web `_render`、ゲート上では反応する
  `FakeConformanceHarness`）に、常時存在する編集可能フィールドを 1 つ加えました。このフィールドは既知で
  問い合わせ可能なフレームを持つため、座標タップが狙う既知フレーム要素を兼ねます。したがって別個のフレーム
  要素は不要です。検証: `make check` は緑（FakeDriver レーン）、web レーンは実際の Chromium に対して緑
  です。idb / XCUITest / adb の各レーンは E2E ワークフローで検証されます。

## 参考

- [BE-0114：バックエンド非依存の挙動を保証するドライバ適合性スイート](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)
- [BE-0270：adb バックエンドの実機ドライバ適合性](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance-ja.md)
- [BE-0265：テキスト編集ステップ: select、clear、delete、copy](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md)
- [BE-0269：wait ステップ中のシステムアラートガードの介入を早める](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)
- `tests/driver_conformance.py`、`bajutsu/drivers/fake.py`
