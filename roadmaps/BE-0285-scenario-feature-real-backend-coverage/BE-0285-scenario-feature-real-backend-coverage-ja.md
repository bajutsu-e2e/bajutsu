[English](BE-0285-scenario-feature-real-backend-coverage.md) · **日本語**

# BE-0285 — シナリオ作成機能の実バックエンドカバレッジを検証する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0285](BE-0285-scenario-feature-real-backend-coverage-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0285") |
| 実装 PR | [#1184](https://github.com/bajutsu-e2e/bajutsu/pull/1184), [#1214](https://github.com/bajutsu-e2e/bajutsu/pull/1214) |
| トピック | 検証とカバレッジ |
| 関連 | [BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios-ja.md), [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md), [BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md), [BE-0281](../BE-0281-ios-on-device-actuation-coverage/BE-0281-ios-on-device-actuation-coverage-ja.md) |
<!-- /BE-METADATA -->

## はじめに

シナリオ作成機能には、実バックエンドでは Android でしか動かないものと、どのバックエンドでも動いていないものがあります。`extract` と `forEach`（[BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)）は、どのデモシナリオにも使われておらず、どのレーンでもアクチュエートされていません。data-driven の行（[BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios-ja.md)）と `relaunch` は、adb でしか動いていません。本項目では、これらの機能を adb と web で動かす実バックエンドのシナリオを用意し、BE-0281 が着地すれば iOS にも広げます。あわせて、`FakeDriver` では表現できない 2 つのタイミングの前提を実際に動かすため、動的に変化する画面の上でそれらのシナリオを走らせます。

## 動機

`extract`（値を `${vars.*}` に取り込む）と `forEach`（一致した要素を反復する）は、`query()` のスナップショットに対するオーケストレータの純粋なロジックです。ユニットテストでは十分に検証済みです。しかし、要素ツリーが実際に変化する状況でも期待どおりに動くかどうかは、fake では示せません。`FakeDriver` の画面は、テストが変化をスクリプトしない限り凍結しています。そのため、反復の途中で行を並べ替えたり再利用したりするリストに対する `forEach` や、実アクセシビリティツリーが異なる形で報告するフィールド値に対する `extract` は、一度も観測されません。

実装済みの最適化のうち 2 つも、どの fake も動かせない前提の上に成り立っています。読み取り回数を減らす最適化（[BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md)）は、アクチュエーションを挟まずに取った隣接する 2 つのスナップショットが同じデバイス状態を指す、と仮定します。動いている時計、アニメーション、バックグラウンドのタイマーは、アクチュエーションを挟まない実デバイス上でこの前提を崩すことがあります。待機の下限（[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)）は、Compose の再コンポーズにかかる時間へポーリングの間隔を合わせた、と仮定します。どちらの前提も、凍結した fake では構造的に表現できません。実際に変化し続ける UI の上で動くシナリオだけが、それらの前提を検証できます。

純粋なロジックをデバイス上で再テストしても、価値は加わりません。要点は、実ハードウェアにしか存在しない 2 つの性質（反復中に変化するツリーと、スナップショットのあいだで生じる UI のドリフト）を伴う状況で、これらの機能を実際に動かすことです。

プラットフォームは、1 つのインタフェースの背後にある単なるバックエンドなので、これらの性質は adb と web だけでなく、アクチュエートするすべてのバックエンドで確かめる必要があります。idb は `tap` / `type` / `swipe` をアクチュエートできますが、iOS ではまだどの CI レーンも実シナリオでこれらを動かしていません。[BE-0281](../BE-0281-ios-on-device-actuation-coverage/BE-0281-ios-on-device-actuation-coverage-ja.md) がそれを CI に配線することを提案しており、それが着地すれば、本項目は adb や web と並んで iOS も対象にします。iOS のレーンは課金対象の macOS ランナーで動くため、まずゲート対象外のシグナルとして着地させます。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **`extract` の再利用シナリオ。** adb と web で、実フィールドの値を `extract` で取り込み、後続のステップに渡し、取り込んだ値が実ツリーの報告と一致することをアサートします。
- **実リストに対する `forEach`。** adb と web で、複数要素のリストを反復し、要素ごとに操作し、結果をアサートします。凍結した fake では再現できない場面です。
- **data-driven と `relaunch` の複数バックエンド展開。** data-driven の行と `relaunch` を web でも走らせます。どちらの機能も adb だけでしか実証されていない状態を解消します。
- **BE-0281 の着地後に iOS へ拡張。** BE-0281 が iOS の実アクチュエーションを CI に配線したのち、同じ `extract` / `forEach` / data-driven / `relaunch` のシナリオを iOS でも走らせます。ゲート対象外の macOS レーンとして動かします。
- **動的 UI シナリオ。** 経過時間やカウンタ表示のように変化し続ける要素を持つショーケース画面を動かし、読み取り回数削減のスナップショット同一性の仮定と、待機の下限の仮定を実際に検証します。まず既存のレーンにシグナルとして組み込みます。

## 検討した代替案

- **これらの機能をユニットテストのレベルにとどめる。** 純粋なロジックはユニットテストで検証済みです。しかし、`forEach` の変化するツリーでの挙動と、スナップショットのドリフトや再コンポーズのタイミングという前提は、fake では表現できません。凍結した画面では、これらを一度も観測できません。
- **タイミングの前提のために合成のストレスハーネスを作る。** 専用のハーネスは実ショーケース画面より忠実さに欠けるうえ、既存の実機インフラを再利用できません。実際に変化するショーケースの要素の方が現実に近く、しかも低コストです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] adb + web での `extract` 再利用シナリオ（実フィールド値を後続ステップに渡す）。
- [x] adb + web での実複数要素リストに対する `forEach`。
- [x] web での data-driven と `relaunch` の検証（adb に加えて）。
- [ ] BE-0281 の着地後、extract / forEach / data-driven / relaunch のシナリオを iOS へ拡張（ゲート対象外の macOS レーン）。
- [x] 読み取り回数削減のスナップショット同一性と待機の下限の前提を検証する動的 UI シナリオ（web）。待機の下限の前提である BE-0245 は adb / Android 固有であり、web に対応する仕組みはありません。

**ログ**

- 2026-07-17（[#1184](https://github.com/bajutsu-e2e/bajutsu/pull/1184)）: web 側の作業が着地しました。
  `demos/web/app/index.html` に実複数要素リストとライブ更新するティッカーを追加しました（これまでどの
  デモにもなかった要素です）。`demos/web/scenarios/` に `extract.yaml`、`foreach.yaml`、
  `data_driven.yaml`、`relaunch.yaml`、`dynamic_ui.yaml` を追加し、すべて実際の Playwright バックエンド
  に対して実行しました。`extract` / `forEach` の adb 側の対応と iOS への拡張は未着手のまま残っています。
- 2026-07-21（[#1214](https://github.com/bajutsu-e2e/bajutsu/pull/1214)）: adb 側の作業が着地しました。
  `demos/showcase/scenarios/` に `extract.yaml`（Log タブのライブなカウンタ値を取り込んで再利用する）と
  `foreach.yaml`（Stable の 5 行を反復し、各行の詳細を開いて反復のあいだにツリーを変化させる）を追加しました。
  どちらも既存のショーケースの部品を再利用し（アプリの変更は不要です）、両方の id 形式（BE-0221）を持つため
  Compose と Views の双方で動きます。ショーケースの Android Makefile の `E2E_SCENARIOS` /
  `E2E_VIEWS_SCENARIOS` を通じて adb レーンに加わります。iOS への拡張は引き続き BE-0281 を待ちます。

## 参考

- [BE-0031 — データ駆動シナリオ](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios-ja.md)
- [BE-0033 — シナリオ変数と制御フロー](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)
- [BE-0030 — パラメータ化した共有ステップ](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md)
- [BE-0259 — assert / query のスナップショット再利用](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md)
- [BE-0245 — adb 常駐 UI Automator サーバ](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)
- [BE-0281 — iOS CI に実機で操作するテストを加える](../BE-0281-ios-on-device-actuation-coverage/BE-0281-ios-on-device-actuation-coverage-ja.md)
