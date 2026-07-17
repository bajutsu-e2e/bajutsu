[English](BE-XXXX-scenario-feature-real-backend-coverage.md) · **日本語**

# BE-XXXX — シナリオ作成機能の実バックエンドカバレッジを検証する

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

シナリオ作成機能には、実バックエンドでは Android でしか動かないものと、どのバックエンドでも動いていないものがあります。`extract` と `forEach`（[BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)）は、どのデモシナリオにも使われておらず、どのレーンでもアクチュエートされていません。data-driven の行（[BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios-ja.md)）と `relaunch` は、adb でしか動いていません。本項目では、これらの機能を複数のバックエンドで動かす実バックエンドのシナリオを用意します。あわせて、`FakeDriver` では表現できない 2 つのタイミングの前提を実際に動かすため、動的に変化する画面の上でそれらのシナリオを走らせます。

## 動機

`extract`（値を `${vars.*}` に取り込む）と `forEach`（一致した要素を反復する）は、`query()` のスナップショットに対するオーケストレータの純粋なロジックです。ユニットテストでは十分に検証済みです。しかし、要素ツリーが実際に変化する状況でも期待どおりに動くかどうかは、fake では示せません。`FakeDriver` の画面は、テストが変化をスクリプトしない限り凍結しています。そのため、反復の途中で行を並べ替えたり再利用したりするリストに対する `forEach` や、実アクセシビリティツリーが異なる形で報告するフィールド値に対する `extract` は、一度も観測されません。

実装済みの最適化のうち 2 つも、どの fake も動かせない前提の上に成り立っています。読み取り回数を減らす最適化（[BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md)）は、アクチュエーションを挟まずに取った隣接する 2 つのスナップショットが同じデバイス状態を指す、と仮定します。バックグラウンドのタイマーは、アクチュエーションを挟まない実デバイス上でこの前提を崩すことがあります。待機の下限（[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)）は、Compose の再コンポーズにかかる時間へポーリングの間隔を合わせた、と仮定します。どちらの前提も、凍結した fake では構造的に表現できません。実際に変化し続ける UI の上で動くシナリオだけが、それらの前提を検証できます。

純粋なロジックをデバイス上で再テストしても、価値は加わりません。要点は、実ハードウェアにしか存在しない 2 つの性質（反復中に変化するツリーと、スナップショットのあいだで生じる UI のドリフト）を伴う状況で、これらの機能を実際に動かすことです。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **`extract` の再利用シナリオ。** adb と web で、実フィールドの値を `extract` で取り込み、後続のステップに渡し、取り込んだ値が実ツリーの報告と一致することをアサートします。
- **実リストに対する `forEach`。** adb と web で、複数要素のリストを反復し、要素ごとに操作し、結果をアサートします。凍結した fake では再現できない場面です。
- **data-driven と `relaunch` の複数バックエンド展開。** data-driven の行と `relaunch` を、adb 以外のバックエンド（web か iOS）でも走らせます。どちらの機能も単一プラットフォームでしか実証されていない状態を解消します。
- **動的 UI シナリオ。** 経過時間やカウンタ表示のように変化し続ける要素を持つショーケース画面を動かし、読み取り回数削減のスナップショット同一性の仮定と、待機の下限の仮定を実際に検証します。まず既存のレーンにシグナルとして組み込みます。

## 検討した代替案

- **これらの機能をユニットテストのレベルにとどめる。** 純粋なロジックはユニットテストで検証済みです。しかし、`forEach` の変化するツリーでの挙動と、スナップショットのドリフトや再コンポーズのタイミングという前提は、fake では表現できません。凍結した画面では、これらを一度も観測できません。
- **タイミングの前提のために合成のストレスハーネスを作る。** 専用のハーネスは実ショーケース画面より忠実さに欠けるうえ、既存の実機インフラを再利用できません。実際に変化するショーケースの要素の方が現実に近く、しかも低コストです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] adb + web での `extract` 再利用シナリオ（実フィールド値を後続ステップに渡す）。
- [ ] adb + web での実複数要素リストに対する `forEach`。
- [ ] adb 以外の少なくとも 1 つのバックエンドでの data-driven と `relaunch` の検証。
- [ ] 読み取り回数削減のスナップショット同一性と待機の下限の前提を検証する動的 UI シナリオ。

## 参考

- [BE-0031 — データ駆動シナリオ](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios-ja.md)
- [BE-0033 — シナリオ変数と制御フロー](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)
- [BE-0030 — パラメータ化した共有ステップ](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md)
- [BE-0259 — assert / query のスナップショット再利用](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md)
- [BE-0245 — adb 常駐 UI Automator サーバ](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)
