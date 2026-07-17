[English](BE-0279-crossbackend-e2e-required-gate.md) · **日本語**

# BE-0279 — E2E の必須チェックを全 backend に揃える

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0279](BE-0279-crossbackend-e2e-required-gate-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0279") |
| 実装 PR | [#1177](https://github.com/bajutsu-e2e/bajutsu/pull/1177) |
| トピック | プラットフォーム対応 |
<!-- /BE-METADATA -->

## はじめに

このBEの目的は、実機の E2E チェックのうちどれをマージの必須条件とするかを定める判断基準を明文化し、その
基準を各 backend のレーンに実装することです。CI の各チェックには二つの種類があります。失敗するとマージを
阻む**必須チェック**と、実行はされ結果も表示されるものの合否を左右しない**参考チェック**です。現状、実機の
検証で必須チェックになっているのは iOS の集約ジョブ `E2E` だけで、Android と web のレーンはすべて参考
チェックに留まっています。この不均衡を正します。

## 動機

課題は、機能検証の範囲が最も広いレーンでも、その失敗がマージを止められないことです。Android レーン
（[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)）は毎回の PR で共有
シナリオを 14 本走らせ、web レーン（[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）は
driver conformance の契約を実機の Chromium で確かめます。ところが、その Android の 14 本や web の契約が
壊れても、PR はそのままマージできます。

止められない理由は、両レーンに必須チェックとしての受け皿がないことにあります。iOS レーンには個別の
ジョブの結果を一つにまとめる集約ジョブ `E2E` があり、これがブランチ保護（branch protection）の規則に
必須チェックとして登録されています。Android と web にはこの集約ジョブがなく、規則にも登録されていません。
個別のジョブは実行されても、その成否をマージ条件に結び付ける経路がないのです。

未解決のまま残ってきたのは、必須化の判断基準が言葉になっていないからです。iOS の必須化が先に決まり、
Android と web のレーンは後から段階的に加わったため、両者を必須にするかどうかが宙に浮いたままでした。
さらにこのバッチの兄弟項目が、ネットワーク、iOS の機能パリティ、push、WebView、テキスト編集のジョブを
三つのレーンに追加します。基準がなければ、新しいジョブが着地するたびに必須か参考かを場当たりに決めること
になり、判断が一貫しません。

解決の指針となる基準は単純です。決定論的で実行環境に依存しない検証は必須にし、実行環境ごとに結果が変わる
検証や外部依存の変化に左右される検証は参考に留めます。この基準は新しく発明するものではなく、既存の運用を
書き下したものです。iOS レーンはすでに、ピクセル VRT（Simulator の描画結果は Xcode や端末や OS で変わり
ます）と要素ツリーの golden（上流の `idb_companion` が Bajutsu の変更と無関係に変化しえます）を集約ジョブ
の必須依存から外しています。この項目は、その境界を Android と web にも広げ、新しいジョブへ適用します。

## 詳細設計

提案の粒度です。作業は集約ジョブとブランチ保護の規則を中心に MECE に分けます。

- **Android の集約ジョブ `E2E (android)`**：`android-e2e.yml` に、iOS の `E2E` を写した集約ジョブを足します。
  決定論的で実行環境に依存しないジョブ（`smoke`、`conformance`）を必須依存にし、`golden` と `visual` は
  除きます。`if: always()` で走らせ、path による省略は合格として報告させます。
- **web の集約ジョブ `E2E (web)`**：web 版として `smoke`、serve-UI の dogfood、`conformance` を必須依存にし、
  ネットワークのジョブが着地したらそれも加えます。
- **ブランチ保護の規則**：新しい集約チェックの名前を `main` の規則に必須チェックとして登録します。これは
  リポジトリの外にある管理設定なので、リポジトリの変更ではなく、正確なチェック名をここに記録したうえでの
  人手の作業になります。
- **判断基準の明文化**：必須と参考の境界を workflow のヘッダと貢献者向けドキュメントに記します。決定論的で
  実行環境に依存しない検証は必須、実行環境ごとに異なる検証（ピクセル VRT）と外部依存の変化による検証
  （`idb_companion` の golden）は参考に留める、という基準です。
- **兄弟ジョブの取り込み**：ネットワーク、機能パリティ、push、WebView、テキスト編集の各項目が足す新しい
  ジョブを、正しい集約ジョブの必須依存に配線します。

## 検討した代替案

* **iOS だけを必須に留める（現状）**：却下します。そうすると必須のレーンが最も薄い機能カバレッジを担い、
  Android の広い範囲の不具合が必須チェックから見えないままになるからです。
* **VRT と golden も含め、すべてのジョブを必須にする**：却下します。ピクセルの baseline は実行環境ごとに
  異なり、`idb_companion` の変化は上流の出来事なので、それらを必須にすると、変更と無関係な理由で PR が
  赤くなります。これは iOS レーンがすでに文書化している理由そのものです。
* **全 backend を一つの集約ジョブにまとめる**：却下します。backend ごとに集約ジョブを分けると切り分けが
  保たれ、赤いチェックが壊れた backend を名指しします。これはリポジトリの「1 ジョブ 1 関心」の構造に
  合致します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `android-e2e.yml` の集約ジョブ `E2E (android)`。`needs: [smoke, conformance]`、`if: always()` で、
  `golden` と `visual` は除外。レーンを iOS 型のトリガに変換（全 PR + `merge_group`、`changes` ジョブが
  `scripts/e2e_changes.py` を `E2E_LANE=android` で走らせて KVM ジョブをパスゲート）。
- [x] `web-e2e.yml` の集約ジョブ `E2E (web)`。`needs: [smoke, dogfood, conformance]`、同じトリガ変換
  （`E2E_LANE=web`）。
- [ ] 新しい必須チェック名 `E2E (android)` と `E2E (web)` を `main` のブランチ保護の規則に登録する
  （人手の作業。リポジトリ外の管理設定であり、リポジトリの変更にはできません）。
- [x] 必須と参考の境界を明文化する（決定論的で実行環境に依存しない検証を必須にする）。`docs/ci.md` と
  その `docs/ja/ci.md` ミラー、および各 workflow のヘッダに記載。
- [ ] 兄弟項目の新しいジョブを正しい集約ジョブの必須依存に配線する。先送り分です。ネットワーク / push /
  WebView / テキスト編集のジョブはまだ workflow に存在しないため、各兄弟 PR が着地時に自分を正しい集約
  ジョブへ加えます（集約ジョブは現時点のジョブを列挙します）。

## 参考

`.github/workflows/ios-e2e.yml`（この項目が一般化する集約ジョブ `E2E`）、
`.github/workflows/android-e2e.yml`、`.github/workflows/web-e2e.yml`、
[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)、
[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)、
[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)。
