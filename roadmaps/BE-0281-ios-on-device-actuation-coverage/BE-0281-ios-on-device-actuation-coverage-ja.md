[English](BE-0281-ios-on-device-actuation-coverage.md) · **日本語**

# BE-0281 — iOS CI に実機で操作するテストを加える

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0281](BE-0281-ios-on-device-actuation-coverage-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0281") |
| 実装 PR | [#1181](https://github.com/bajutsu-e2e/bajutsu/pull/1181) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md), [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee-ja.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md), [BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md) |
<!-- /BE-METADATA -->

## はじめに

iOS E2E レーンの idb シナリオ（smoke、golden、visual）は、画面の表示が正しいかを検査します。
`wait` と `expect` を使い、`tap`、`type`、`swipe`、`back`、ジェスチャのステップは含みません。

idb が CI で唯一実際にアクチュエートする操作は、conformance ジョブ経由の `tap` です。それ以外の
[アクチュエータ](../../docs/ja/glossary.md#driver-backend-actuator-platform)は、どの iOS レーンでも
アクチュエートされていません。`type_text`、`swipe`、`scroll`、`back`、`long_press`、`double_tap`、
`tap_point` です。デバイスコントロールの一群も同じです。

Android はすでにこれらの操作を実デバイスで動かしており
（[BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)）、iOS も同じ
水準に達するべきです。ショーケースにはシナリオ（`gestures.yaml`、`device.yaml`、`push.yaml`）がすでに
あります。これらは CI ではなく、ローカル専用の Makefile ターゲットでのみ走っています。本項目では、
実機の iOS アクチュエーションを既存の `ios-e2e.yml` に配線します。

## 動機

Android は圧倒的によく動かされているバックエンドです。次の操作を実デバイスで実証している唯一の
バックエンドでもあります。`swipe` / `scroll` / `back` / `longPress` / `doubleTap` / `relaunch` /
`setLocation` とクリップボードです
（[BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)、
[BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee-ja.md)）。

一方、iOS のアクチュエータは、コマンド構築のレベル（サブプロセスをモックしたユニットテスト）でしか
実証されていません。この非対称は、必須の `E2E` ゲートを備えるプラットフォームで、バックエンドに
依存しないという約束を損ないます。iOS のアクチュエータは、XCUITest のランナーチャネルと idb
コンパニオンにある、共有実装の外のコードを通ります。そこにバックエンド固有の退行があっても、いまは
どの iOS レーンも落ちません。

ギャップはシナリオの不足ではなく、CI 配線の不足です。シナリオは存在します。次の 2 つを足せば、
ジェスチャとテキストの側は塞がります。

- `ios-e2e` のインタラクションジョブ（tap / type / swipe / scroll / back に加えて `gestures.yaml` の
  doubleTap と longPress）
- ランナーチャネルで `/type`、`/swipe`、`/back` を動かす XCUITest シナリオ 1 本

デバイスコントロールは、push、ステータスバーの上書きと解除、keychain、フォアグラウンドとバック
グラウンドを指します。idb と XCUITest が `DEVICE_CONTROL_ALL` で広告し、simctl のビルダーもユニット
テストされています。しかし `device.yaml` と `push.yaml` はどの iOS レーンでも走りません。実デバイスで
デバイスコントロールを実証しているのは Android だけで、対象は `setLocation` とクリップボードのみです。

macOS ランナーは Linux の 10 倍で課金されます。そのため、どの新しいジョブをゲートにし、どれを
シグナルにするかを、設計として意図的に決めます。既存の `ios-e2e.yml` を再利用し、新しいワークフローは
作りません。
[BE-0271](../BE-0271-e2e-workflow-structural-parity/BE-0271-e2e-workflow-structural-parity-ja.md) が
確立した構造的な対称性の形に従います。
[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
の準備完了の安定化作業の精神にならい、新しいジョブはゲート対象外のシグナルとして始めます。

## 詳細設計

この節は提案の粒度で書きます。作業は、相互に排他的で全体を網羅する（MECE）以下の単位に分かれます。

- **iOS idb インタラクションジョブ。** インタラクションシナリオを、課金対象の `ios-e2e` ジョブに
  昇格させます。シナリオの範囲は、tap / type / swipe / scroll / back に `gestures.yaml` の doubleTap と
  longPress を加えたものです。idb のアクチュエータが、conformance 契約の中だけでなく実 Simulator に
  対して走ります。
- **XCUITest アクチュエーション。** `XcuitestDriver` のランナーチャネルで `/type`、`/swipe`、`/back` を
  動かすシナリオを追加します。このチャネルが実機 CI に届いているのは、いまは tap、pinch、rotate
  だけです。
- **iOS デバイスコントロール。** `device.yaml` と `push.yaml` を、ゲート対象外のジョブとしてつなぎます。
  このジョブは、次の操作を実 Simulator に対して動かします。
  - `setLocation`
  - クリップボード
  - ステータスバーの上書きと解除
  - keychain のリセット
  - フォアグラウンドとバックグラウンド
- **コストの規律。** macOS の課金を踏まえ、必須ジョブとシグナルジョブの分け方を適切な大きさに
  調整します。新しいワークフローは作らず、単一の `ios-e2e.yml` とジョブ分割の形を再利用します。

## 検討した代替案

- **バックエンド横断のアクチュエーションの確信を Android に頼る。** 却下します。Android の広さは
  iOS に転用できません。iOS のアクチュエータは XCUITest のランナーチャネルと idb コンパニオンを通り、
  Android レーンはそのコードに一度も触れないからです。iOS の挙動を Android で信用することは、
  まさに conformance 作業が確かめるために存在する、バックエンドに依存しないという前提であり、
  そのまま信じてよいものではありません。
- **新しいジョブをすべて必須ゲートにする。** 却下します。macOS の 10 倍課金がそれを高価にします。
  Simulator レーンのフレーキー履歴
  （[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)）
  は、新しいアクチュエーションジョブをまずシグナルとして着地させることを支持します。必須への昇格は、
  安定を確認してからにします。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] iOS idb インタラクションジョブ（`back`）。`type`/`swipe`/`scroll`/`doubleTap`/
      `longPress` は XCUITest 側に残ります。idb はネイティブのタブバーを 1 つの不透明なグループに
      畳んでしまい、どのタブにも届きません。提案の想定にはなかった制約で、実装前に実機で確認済み
      です。詳細はログを参照してください。
- [x] XCUITest アクチュエーションシナリオ（`/type` は `search.yaml`、`/swipe` + `/back` は `notices.yaml` のランナーチャネルで実行）。
      新規ファイルではなく既存の共有シナリオを再利用します。
- [x] iOS デバイスコントロールジョブ（`device.yaml` / `push.yaml`）、ゲート対象外。
- [x] macOS 課金のジョブについてゲートとシグナルの割り当てを適切な大きさに調整し、`ios-e2e.yml` を再利用する。

### ログ

- [#1181](https://github.com/bajutsu-e2e/bajutsu/pull/1181)：ゲート対象外の新規ジョブ `actuation (idb)` を 1 つ着地させました。
  `back` は `navigation.yaml`、デバイスコントロールは `device.yaml` と `push.yaml` で実行します。
  それに加えて、既存の `xcuitest (multi-touch)` ジョブに実行を 2 つ追加で配線しました。
  `/type` は `search.yaml`、`/swipe` + `/back` は `notices.yaml` で実行します。
  idb と XCUITest のカバレッジは、共有できる箇所でビルドを共有しました。
  そのため、課金対象の新規ジョブは 2 つではなく 1 つで済んでいます。
  もとの設計からは範囲を狭めました。idb はネイティブのタブバーをまったくタップできないためです（SPEC.md §3、BE-0107）。
  提案が想定していたようにタブをまたぐ `gestures.yaml` / `controls.yaml` のシナリオを、idb 上のインタラクションジョブで走らせることはできませんでした。
  この点は実装に先立つ実機での実行で確かめています（`Log` タブのタップで `一致なし`）。
  `type` / `swipe` / `scroll` / `longPress` / `doubleTap` は、idb 上ではモックしたユニットテストでしか実証されないままです。
  このギャップを塞ぐには新しいタブなしの showcase 画面が要ります。後続の項目に残します。

## 参考

- [BE-0210：Android 実機のアクチュエーション忠実度](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)
- [BE-0221：共有ショーケースシナリオが Android で無改変に走ることの保証](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee-ja.md)
- [BE-0218：E2E Simulator ゲートの安定化](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
- [BE-0240：iOS の capability を踏まえた自動アクチュエータ選択](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)
- `.github/workflows/ios-e2e.yml`、`demos/showcase/scenarios/navigation.yaml`、`demos/showcase/scenarios/device.yaml`、`demos/showcase/scenarios/push.yaml`、`demos/showcase/scenarios/search.yaml`、`demos/showcase/scenarios/notices.yaml`
