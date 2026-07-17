[English](BE-XXXX-conformance-unactuated-verbs.md) · **日本語**

# BE-XXXX — 全バックエンド共通のテストを、アクチュエートされていない Driver 操作へ広げる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-conformance-unactuated-verbs-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | ドライバとバックエンドのアーキテクチャ |
| 関連 | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md), [BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance-ja.md), [BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md), [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md) |
<!-- /BE-METADATA -->

## はじめに

すべてのバックエンド（idb、XCUITest、adb、Playwright、そして Linux ゲート上の `FakeDriver`）を、同じ 1 本の
テスト（`tests/driver_conformance.py`）にかけて、どれも同じように振る舞うことを確かめる仕組みが、
[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) にあります（adb レーンは
[BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance-ja.md)
が追加しました）。バックエンドごとに別々のテストを書くのではなく、この 1 本を各バックエンドに対して流します。
こうして「どのバックエンドも同じように振る舞う」という前提が、そう期待するだけの状態から、実際の検査で
裏づけられた状態に変わります。この共通テストが現在カバーして
いるのは、tap、label と trait による解決、multi-touch と `selectOption` の capability の約束、そして条件待機の
セマンティクスです。`Driver` プロトコルの操作のうち、テキスト編集の一群（`delete_text`、`select_all`、
`copy_selection`）と `tap_point` は、この共通テストの範囲外にあり、どのレーンでも実機で一度もアクチュエート
されていません。本項目では、これらの操作を共通テストに取り込み、1 本のテストがすべてのバックエンドでそれらを
一度に検証するようにします。

## 動機

テキスト編集ステップ（[BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md)）は
すでに実装され、各バックエンドのコマンド構築はサブプロセスをモックしたユニットテストで検証されています。
どのレーンも行っていないのは、`delete_text`、`select_all`、`copy_selection` を実デバイスや実ブラウザに
対してアクチュエートすることです。ステップから観測可能なフィールドの変化までの往復は一度も確認されて
いません。`tap_point` も同じ状況にあり、実ドライバのコマンドテストは XCUITest と Playwright にしかなく、これを
アクチュエートするレーンはありません。`tap_point` はアラート解消の経路（
[BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)
が依拠する、視覚で位置を特定した座標タップ）の土台なので、実機で未確認のまま残すのは実際のリスクです。

このギャップを塞ぐのにもっとも安価な場所が、この共通テストです。1 つの仕様が 5 つのバックエンドに対して走るので、
バックエンドごとにショーケースのシナリオを 1 本ずつ書くのではなく、1 つのテスト本体を足せば全バックエンドに
カバレッジが広がります。capability モデルは、この共通テストの形をすでに定めています。capability を宣言した
バックエンドはアクチュエートしなければならず、宣言していないバックエンドは黙って何もしないのではなく
`UnsupportedAction` を明示的に送出しなければなりません。テキスト編集の操作と `tap_point` は、この同じ
パターンを拡張したものです。

作業を形づくる制約が 1 つあります。実機の適合性ハーネスは、要求された画面を識別子つきボタンの並びとして
実体化します。これは tap や解決のテストには十分ですが、テキスト編集には足りません。`delete_text` や
`select_all` を試すには、画面上に編集可能な実テキストフィールドが必要です。そのため共通テストへの追加とあわせて、
各プラットフォームの適合性画面（iOS の `ConformanceView`、Compose の `ConformanceScreen`、web ハーネスが
描画する document）に、編集可能なフィールドと既知フレームの要素を出すための小さな拡張を加えます。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **各操作の不変条件を列挙する。** `Driver` プロトコルに基づき、新しい操作が満たすべき不変条件を明文化します。
  テキスト編集では、その操作をアクチュエートするバックエンドで `type_text` に続けて `select_all`、
  `copy_selection` が `UnsupportedAction` なしに完了し、`delete_text` がフィールドの報告長を減らすこと。
  アクチュエートしないバックエンド、すなわち現状 `select_all` / `copy_selection` を無条件に送出する idb では、
  `UnsupportedAction` を送出すること。`MULTI_TOUCH` や `SELECT_OPTION` と違い、テキスト編集の select/copy 用の
  `Capability` トークンはまだ存在しないため、共通テストがアクチュエートと送出の挙動を直接表明するか、それとも同じように
  ゲートするための新しい capability を導入する（各バックエンドの `CAPABILITIES` に反映する）かは、最初に決めるべき
  検討事項です。現状では adb、Playwright、XCUITest が select/copy をアクチュエートし、idb は送出します。`tap_point`
  では、既知フレームの要素への座標タップが、その要素への意味的な tap と同じ観測可能な効果を持つこと。
- **3 つの適合性画面を拡張する。** iOS の `ConformanceView`、Compose の `ConformanceScreen`、web ハーネスの
  document に、編集可能なテキストフィールドと既知フレームの要素を加えます。準備完了マーカーの扱いはそのまま
  保ちます。
- **共通テストの本体を追加する。** 新しい不変条件を `tests/driver_conformance.py` に一度だけ書き、pytest が
  各バックエンドのサブクラスに対して収集するようにします。
- **実機ハーネスへ実体化を配線する。** 各ハーネスの画面実体化の経路（iOS のスペックファイル書き込み、Android の
  インテント再シード、web の `set_content`）を拡張し、テスト本体が走る前にフィールドと既知フレームの要素が
  存在するようにします。
- **capability の宣言が挙動と一致することを確認する。** 各バックエンドが宣言する capability が、どの操作を
  アクチュエートし、どの操作を拒むかと一致していることを確かめ、共通テストの「約束と挙動の一致」チェックを正しく
  保ちます。

## 検討した代替案

- **共通テストではなくショーケースにバックエンドごとのアクチュエーションシナリオを置く。** テキスト編集や
  `tap_point` のシナリオをバックエンドごとに書くと、同じ意図が 4 回重複し、バックエンドごとのずれを招きます。
  この共通テストは、まさにそのずれを防ぐために存在します。1 つの仕様をすべてのバックエンドに適用することが、
  この設計の要点です。
- **テキスト編集の操作をコマンド構築のユニットテストにとどめる。** サブプロセスをモックしたテストは、バックエンドが
  組み立てる argv、HTTP、キーの組み合わせを検証しますが、実デバイスが編集を実行することは一度も確認しません。
  ツールがうたっていてどのレーンも観測しない capability は、チェックのない約束です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 各操作の不変条件を列挙する（テキスト編集の往復、`tap_point`、バックエンドごとのアクチュエート／送出、テキスト編集 capability を追加するかの判断）。
- [ ] iOS / Compose / web の適合性画面に編集可能フィールドと既知フレーム要素を加える。
- [ ] 新しいテスト本体を `tests/driver_conformance.py` に追加する。
- [ ] 実機ハーネスへ画面実体化を配線する。
- [ ] capability の宣言がバックエンドごとのアクチュエート／送出の挙動と一致することを確認する。

## 参考

- [BE-0114 — バックエンド非依存の挙動を保証するドライバ適合性スイート](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)
- [BE-0270 — adb バックエンドの実機ドライバ適合性](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance-ja.md)
- [BE-0265 — テキスト編集ステップ: select、clear、delete、copy](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md)
- [BE-0269 — wait ステップ中のシステムアラートガードの介入を早める](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)
- `tests/driver_conformance.py`、`bajutsu/drivers/fake.py`
