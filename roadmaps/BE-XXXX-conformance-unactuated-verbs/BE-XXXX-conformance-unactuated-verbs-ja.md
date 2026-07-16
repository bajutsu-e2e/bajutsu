[English](BE-XXXX-conformance-unactuated-verbs.md) · **日本語**

# BE-XXXX — ドライバ適合性契約を未アクチュエーションの Driver 動詞へ拡張する

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

ドライバ適合性契約（[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）は、
バックエンドに依存しない 1 つの仕様をすべてのバックエンドに対して実行します。Linux ゲートでは
`FakeDriver`、実機では idb、XCUITest、adb、Playwright に対して走り、adb レーンは
[BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance-ja.md)
が追加しました。現在の契約が固定しているのは、tap、label と trait による解決、multi-touch と
`selectOption` の capability の約束、そして条件待機のセマンティクスです。`Driver` プロトコルの動詞の
いくつかはこの契約の外にあり、どのレーンでも実機で一度もアクチュエートされていません。テキスト編集の
一群（`delete_text`、`select_all`、`copy_selection`）と `tap_point` です。本項目では、これらの動詞を
契約に取り込み、1 つの仕様がすべてのバックエンドで同時に検証するようにします。

## 動機

テキスト編集ステップ（[BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md)）は
すでに実装され、各バックエンドのコマンド構築はサブプロセスをモックした単体テストで検証されています。
どのレーンも行っていないのは、`delete_text`、`select_all`、`copy_selection` を実デバイスや実ブラウザに
対してアクチュエートすることです。ステップから観測可能なフィールドの変化までの往復は一度も確認されて
いません。`tap_point` も同じ状況にあり、実ドライバのコマンドテストは XCUITest にしかなく、これを
アクチュエートするレーンはありません。`tap_point` はアラート解消の経路（
[BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)
が依拠する、視覚で位置を特定した座標タップ）の土台なので、実機で未確認のまま残すのは実際のリスクです。

このギャップを塞ぐのに最も安価な場所が契約です。1 つの仕様が 5 つのバックエンドに対して走るので、
バックエンドごとにショーケースのシナリオを 1 本ずつ書くのではなく、1 つのテスト本体を足せば全バックエンドに
カバレッジが広がります。capability モデルはすでに契約の形を与えています。capability を宣言した
バックエンドはアクチュエートしなければならず、宣言していないバックエンドは黙って何もしないのではなく
`UnsupportedAction` を明示的に送出しなければなりません。テキスト編集の動詞と `tap_point` は、この同じ
パターンを拡張したものです。

作業を形づくる制約が 1 つあります。実機の適合性ハーネスは、要求された画面を識別子つきボタンの並びとして
実体化します。これは tap や解決のテストには十分ですが、テキスト編集には足りません。`delete_text` や
`select_all` を試すには、画面上に編集可能な実テキストフィールドが必要です。そのため契約への追加とあわせて、
各プラットフォームの適合性画面（iOS の `ConformanceView`、Compose の `ConformanceScreen`、web ハーネスが
描画する document）に、編集可能なフィールドと既知フレームの要素を出すための小さな拡張を加えます。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **動詞の契約を列挙する。** `Driver` プロトコルに基づき、新しい動詞が満たすべき不変条件を明文化します。
  テキスト編集では、capability を宣言したバックエンドで `type_text` に続けて `select_all`、`copy_selection`
  が `UnsupportedAction` なしに完了し、`delete_text` がフィールドの報告長を減らすこと。宣言していない
  バックエンドではそれぞれが `UnsupportedAction` を送出すること（select と copy をめぐる idb / XCUITest /
  adb の差は、既存の `MULTI_TOUCH` や `SELECT_OPTION` の契約テストと同じく、送出を確認する形で表明します）。
  `tap_point` では、既知フレームの要素への座標タップが、その要素への意味的な tap と同じ観測可能な効果を
  持つこと。
- **3 つの適合性画面を拡張する。** iOS の `ConformanceView`、Compose の `ConformanceScreen`、web ハーネスの
  document に、編集可能なテキストフィールドと既知フレームの要素を加えます。準備完了マーカーの契約はそのまま
  保ちます。
- **契約のテスト本体を追加する。** 新しい不変条件を `tests/driver_conformance.py` に一度だけ書き、pytest が
  各バックエンドのサブクラスに対して収集するようにします。
- **実機ハーネスへ実体化を配線する。** 各ハーネスの画面実体化の経路（iOS のスペックファイル書き込み、Android の
  インテント再シード、web の `set_content`）を拡張し、契約本体が走る前にフィールドと既知フレームの要素が
  存在するようにします。
- **capability の宣言が挙動と一致することを確認する。** 各バックエンドが宣言する capability が、どの動詞を
  アクチュエートし、どの動詞を拒むかと一致していることを確かめ、契約の「約束と挙動の一致」チェックを正しく
  保ちます。

## 検討した代替案

* **契約ではなくショーケースにバックエンドごとのアクチュエーションシナリオを置く。** テキスト編集や
  `tap_point` のシナリオをバックエンドごとに書くと、同じ意図が 4 回重複し、バックエンドごとのずれを招きます。
  これは適合性スイートが防ぐために存在するものそのものです。1 つの仕様を全バックエンドにという点こそが要点です。
* **テキスト編集の動詞をコマンド構築の単体テストにとどめる。** サブプロセスをモックしたテストは、バックエンドが
  組み立てる argv、HTTP、キーの組み合わせを検証しますが、実デバイスが編集を実行することは一度も確認しません。
  ツールが広告していてどのレーンも観測しない capability は、チェックのない約束です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 動詞の契約を列挙する（テキスト編集の往復、`tap_point`、capability で分岐する送出）。
- [ ] iOS / Compose / web の適合性画面に編集可能フィールドと既知フレーム要素を加える。
- [ ] 新しい契約テスト本体を `tests/driver_conformance.py` に追加する。
- [ ] 実機ハーネスへ画面実体化を配線する。
- [ ] capability の宣言がバックエンドごとのアクチュエート／送出の挙動と一致することを確認する。

## 参考

- [BE-0114 — バックエンド非依存の挙動を保証するドライバ適合性スイート](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)
- [BE-0270 — adb バックエンドの実機ドライバ適合性](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance-ja.md)
- [BE-0265 — テキスト編集ステップ: select、clear、delete、copy](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md)
- [BE-0269 — wait ステップ中のシステムアラートガードの介入を早める](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)
- `tests/driver_conformance.py`、`bajutsu/drivers/fake.py`
