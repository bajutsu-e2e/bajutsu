[English](BE-XXXX-scenario-feature-real-backend-coverage.md) · **日本語**

# BE-XXXX — シナリオ作成機能の実バックエンドカバレッジ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-scenario-feature-real-backend-coverage-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | 検証とカバレッジ |
| 関連 | [BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios-ja.md), [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md), [BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md) |
<!-- /BE-METADATA -->

## はじめに

シナリオ作成機能のいくつかは、実バックエンドに対して Android でしか走らないか、どこでも走りません。
`extract` と `forEach`（[BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)）は
どのデモシナリオにも現れず、どのレーンでもアクチュエートされていません。data-driven の行
（[BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios-ja.md)）と `relaunch` は adb でしか
走りません。本項目では、これらの機能を複数のバックエンドで動かす実バックエンドのシナリオを用意し、さらに
`FakeDriver` が表現できない 2 つのタイミングの前提を実際に動かすため、動的に変化する画面の上でそれらを走らせます。

## 動機

`extract`（値を `${vars.*}` に取り込む）と `forEach`（一致した要素を反復する）は、`query()` のスナップショットに
対する純粋なオーケストレータのロジックであり、十分に単体テストされています。実際に変化する要素ツリーの下での
正しさこそ、fake が示せないものです。`FakeDriver` の画面はテストが変化をスクリプトしない限り凍結しているため、
反復の途中で行を並べ替えたり再利用したりするリストに対する `forEach` や、実アクセシビリティツリーが異なる形で
報告するフィールド値に対する `extract` は、一度も観測されません。

出荷済みの最適化のうち 2 つは、どの fake も動かせない前提の上に成り立っています。読み取り回数の削減
（[BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md)）は、あいだに
アクチュエーションを挟まずに取った 2 つの隣接スナップショットが同じデバイス状態であると仮定します。これは、
生きた時計、アニメーション、バックグラウンドのタイマーが、アクチュエーションなしに実デバイスで破りうる仮定です。
待機のフロア（[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)）は、
実際の Compose の再コンポーズのタイミングに合わせて調整したポーリング予算を仮定します。どちらも凍結した fake では
構造的に表現できません。生きて漂う UI の上のシナリオが、それらを観測できる唯一の場所です。

要点は、純粋なロジックをデバイスで再テストすることではありません。それは何も足しません。要点は、実ハードウェア
にしか存在しない 2 つの性質、すなわち反復中に変化するツリーと、スナップショットのあいだの UI の漂いを動かすこと
です。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **`extract` の再利用シナリオ。** adb と web で、実フィールドの値を `extract` で取り込み、後続のステップに渡し、
  取り込んだ値が実ツリーの報告どおりであることをアサートします。
- **実リストに対する `forEach`。** adb と web で、複数要素のリストを反復し、要素ごとに操作し、結果をアサートします。
  凍結した fake が現実にできない場合です。
- **data-driven と `relaunch` の対称性。** data-driven の行と `relaunch` を adb 以外の少なくとも 1 つの
  バックエンド（web か iOS）で走らせ、どちらの機能も単一プラットフォームだけで実証される状態を解消します。
- **動的 UI シナリオ。** 生きた要素（経過時間やカウンタの表示）を持つショーケース画面を動かし、読み取り回数の
  スナップショット同一性の仮定と待機のフロアを実際に動かします。既存のレーンにシグナルとしてつなぎます。

## 検討した代替案

* **これらの機能を単体テストのレベルにとどめる。** 純粋なロジックは検証されていますが、`forEach` の
  変化するツリーでの挙動と、スナップショットの漂い・再コンポーズのタイミングの前提は fake では表現できません。
  凍結した画面はそれらを決して表に出せません。
* **タイミングの前提のために合成のストレスハーネスを作る。** 専用のハーネスは実ショーケース画面より忠実さに
  欠け、既存の実機インフラを再利用しません。生きたショーケースの要素のほうが現実に近く、しかも安価です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] adb + web での `extract` 再利用シナリオ（実フィールド値を後続ステップに渡す）。
- [ ] adb + web での実複数要素リストに対する `forEach`。
- [ ] adb 以外のバックエンドでの data-driven と `relaunch` の対称性。
- [ ] 読み取り回数のスナップショット同一性と待機フロアの前提を動かす動的 UI シナリオ。

## 参考

- [BE-0031 — データ駆動シナリオ](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios-ja.md)
- [BE-0033 — シナリオ変数と制御フロー](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)
- [BE-0030 — パラメータ化した共有ステップ](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md)
- [BE-0259 — assert / query のスナップショット再利用](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md)
- [BE-0245 — adb 常駐 UI Automator サーバ](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)
