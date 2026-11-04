[English](BE-0271-e2e-workflow-structural-parity.md) · **日本語**

# BE-0271 — Structural parity across platform E2E workflows

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0271](BE-0271-e2e-workflow-structural-parity-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0271") |
| トピック | Platform support |
<!-- /BE-METADATA -->

## はじめに

Bajutsu には、プラットフォームごとの実機または実環境の E2E ワークフローが 3 つあります。[`android-e2e.yml`](../../.github/workflows/android-e2e.yml)、[`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml)、[`web-e2e.yml`](../../.github/workflows/web-e2e.yml) です。

対称性に向けた最初の一歩は、すでに入っています。iOS の機能ジョブと必須 `E2E` ゲートを抱えていた独立ワークフロー `e2e.yml` が、`ios-e2e.yml` に統合されました。iOS レーンは、単体で完結する 1 ファイルになりました。`smoke (idb)`、`xcuitest (codegen)`、`xcuitest (multi-touch)`、`conformance (idb + xcuitest)`、`visual (idb)`、必須の `E2E` 集約ジョブを備えています。この 3 ファイルの間には、構造上の非対称がまだ 2 つ残っています。

- `android-e2e.yml` は、いまだに **1 つのジョブ** `smoke + golden + visual + fallback (adb)` を走らせています。このジョブに、機能シナリオの実行、element ツリーの golden 比較、ピクセル単位の visual regression test（VRT）、resident/fallback チャネルの確認が詰め込まれています。`ios-e2e.yml` と `web-e2e.yml` は、作業を名前付きジョブに分けています。
- iOS には、**PR ごとの element ツリー golden がありません**。`ios-e2e.yml` は smoke、codegen、gestures、conformance、visual を走らせます。idb の golden は [`idb-monitor.yml`](../../.github/workflows/idb-monitor.yml) で週次に実行されるだけです。一方 `android-e2e.yml` は、golden を関連する PR ごとに走らせています。

3 つのレーンは、1 つの機能面の核を共有しています。「bajutsu は実際にそのプラットフォームを操作できるのか」という問いに答える **smoke** ジョブです。実バックエンド上で showcase のシナリオを `bajutsu run` で回し、決定的に合否を判定します。この項目は、その smoke ジョブとその周辺のチェックを、3 プラットフォームで構造的に対称にします。

## 動機

- **プラットフォームの E2E ワークフローは、ジョブ単位でどの機能が壊れたかを示すべきです。** `android-e2e.yml` の単一ジョブは、機能スモーク、golden、visual、fallback という 4 つの異なる確認を、1 つの合否として報告します。チェックが赤くなっても、レビューアは実行ログを開くまで、4 つのうちどれが壊れたのかわかりません。最初に読まれるのはチェック一覧であり、YAML 本体ではないという点は、[BE-0122](../BE-0122-workflow-name-legibility/BE-0122-workflow-name-legibility.md) がすでに確立しています。同じ論拠は、ワークフロー名だけでなく、ワークフロー内部のジョブ粒度にも、一段下でそのまま当てはまります。`ios-e2e.yml` と `web-e2e.yml` の複数ジョブ分割は、目指すべき形をすでに示しています。束ねたままなのは、Android レーンだけです。
- **機能面のカバレッジは、プラットフォームが実際に対応している範囲では対称であるべきです。** iOS と Android はどちらも、正規化したアクセシビリティツリーを固定する element ツリー golden（BE-0006）を持っています。Android はこれを PR ごとに、iOS は週次で走らせています。iOS が週次だったことには、正当な理由がありました。`idb_companion` の upstream ドリフト監視を、PR の活動から切り離すことです（`idb-monitor.yml` の冒頭コメント）。この理由が当てはまるのは*監視*であって、*回帰チェック*ではありません。決定的でホスト非依存なツリー golden が、Android と同じように各 PR も守ることを、妨げるものは何もありません。
- **文書化されていない非対称性は、不具合に見えてしまいます。** iOS の golden が週次だけだったことは、`ios-e2e.yml` からも `android-e2e.yml` からも見えませんでした。この項目を起こした調査自体が、その事実を、意図した選択ではなく説明のつかない不整合として最初に読みました。プラットフォームごとのジョブ構成を書き出す（単位 3）ことで、次の読み手が同じ推測をやり直さずに済みます。

## 詳細設計

作業は CI ワークフローの再構成だけで、プロダクトコードの変更は含みません。`run` と CI の合否判定経路に LLM を持ち込むものでもないため、プライムディレクティブ 1（AI は著者と調査役であり、判定役ではない）には触れません。既存の決定的なチェックを整理し直すだけです。「はじめに」で述べた iOS ファイルの統合はすでに別途入っているので、残る単位は 3 つです。

1. **`android-e2e.yml` の単一ジョブを、観点ごとのジョブに分割します。** ジョブは `smoke`、`golden`、`visual` で、`ios-e2e.yml` と `web-e2e.yml` のジョブ分割に合わせます。各ジョブは、すでに通っている Android の `make` ターゲットに対応します。
   - `smoke (adb)` は `make -C demos/showcase/android e2e` を走らせます。Compose は全シナリオを、Views は id 検証済みのサブセットを回します。機能面の核です。
   - `golden (adb)` は `make e2e-golden && make e2e-fallback` を走らせます。まず resident チャネル上で element ツリー golden を実行します。続いて `BAJUTSU_ADB_RESIDENT=0` で再実行して `uiautomator dump` フォールバックを動かし、両チャネルが同じツリーを返すことを確認します（BE-0245）。`fallback` は、4 つ目のジョブにせず、このジョブ内のステップに留めます。同じ golden を、もう一方のチャネルで再実行するだけだからです。切り出しても同じセットアップを重複させるだけで、原因の特定しやすさは増えません。
   - `visual (adb)` は `make e2e-visual`（ピクセル VRT）を走らせます。非必須で、ベースラインはホスト依存です。

   各ジョブは、自前で Android Virtual Device（AVD）を起動します。iOS は従量制の macOS ランナー（10 倍課金）で動きますが、Android は標準価格の Linux 上で KVM を使って動きます。ジョブごとの emulator 起動は、特定しやすさと引き換えに許容できるコストです。

   セットアップ（uv、JDK、Gradle、KVM、AVD キャッシュ）は、ジョブごとに重複させます。これは `ios-e2e.yml` が、すでにジョブごとのセットアップを重複させているのに合わせたものです。composite action のような新しい CI の仕組みは、この項目では追加しません。Android は非必須で、ブランチ保護の ruleset に含まれていません。そのため分割に集約ジョブは不要で、`web-e2e.yml` と同じく独立した 3 ジョブになります。

2. **`ios-e2e.yml` に、PR ごとの `golden (idb)` ジョブを追加します。** このジョブは、`bajutsu-e2e` アクション経由で `golden.yaml` を idb 上で実行します。`idb-monitor.yml` が週次で回しているのと同じシナリオとベースラインを使います。既存の `changes` ジョブでゲートします。`visual` とまったく同じように、**`E2E` ゲートの `needs:` からは意図的に除外します**。

   ツリー golden は決定的でホスト非依存です。ただし `idb_companion` は upstream の依存であり、そのドリフトが Bajutsu 側の変更と無関係に golden を赤くしうるため、golden のドリフトはマージを止めず、PR ごとのシグナルとして表れるべきです。これにより、iOS の golden は、Android のもの（非必須レーン上で PR ごとに走る）と実効的に同じ地位になります。

   週次の `idb-monitor.yml` の golden は残します。その目的は、*最新の* `idb_companion` に対して走らせるという前方視点のものであり、PR ごとのジョブは、その目的を置き換えないからです。

3. **プラットフォームごとの E2E ジョブ構成の規約を書き出します。** この規約は、smoke、golden、visual、conformance、codegen、gestures、fallback のうち、各プラットフォームの実機または実環境ワークフローが、どれを備えるかを記録します。備えない場合は、その理由も記録します。たとえば Web には golden と visual のジョブがなく、Android にはまだ conformance のジョブがありません（単位 4 を参照）。規約は、[`docs/ai-development.md`](../../docs/ai-development.md)（英語版と `docs/ja/` の対訳）に記載します。将来のバックエンド（`CLAUDE.md` にあるとおり、次に予定されている 4 つ目は Flutter です）は、3 つのファイルから逆算する代わりに、書き出された形に合わせられます。

4. **スコープ外とし、フォローアップ項目として切り出します。** Android には、実機上でのドライバ conformance ジョブがありません。`test_driver_conformance_ondevice.py` や `test_driver_conformance_web.py` に対応する adb 版が存在しません。これは実在するカバレッジの欠落です。ただし、新しい conformance スイートを書くことは、ワークフローの再構成ではなくテスト作成の作業なので、この項目のスコープには含めません。

## 検討した代替案

- **Android の golden と fallback を、2 つの別々のジョブに分割する案。** 見送りました。`fallback` は、同じ `golden.yaml` を resident チャネルを切って再実行するだけです。別ジョブにすると、APK ビルドと AVD 起動を重複させて、同じツリーを別の読み取り経路で確認することになります。`golden` ジョブ内の 2 つ目のステップに留めれば、冗長なセットアップなしに、チャネル対称性のカバレッジが得られます。
- **iOS の PR ごと golden を必須ゲートの一員にする（`E2E` の `needs:` に加える）案。** 見送りました。そうすると `idb_companion` の upstream リリースが golden を赤くし、ベースラインを録り直すまで*すべての*マージを止めうるからです。週次の `idb-monitor.yml` は、まさにこの結合を PR の経路から切り離すために作られました。`visual` と同じく `needs:` から除外すれば、マージを止めるリスクなしに、PR ごとのシグナルを保てます。
- **3 プラットフォームの E2E ワークフローを、1 つのパラメータ化された再利用可能なワークフローへ統合する案。** 構造的な対称性を一度に最大化できます。ただし、今後の変更の影響範囲が、1 プラットフォームから 3 プラットフォームへ広がります。必須チェックのブランチ保護設定も、統合後のワークフローが生成するジョブ名に合わせて移行する必要があります。得られる利点に対してリスクが見合わないため、見送りました。単位 3 のファイルごとの規約の文書化で、この案が持つ可読性向上の効果の大部分は得られます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `android-e2e.yml` の単一ジョブを `smoke` / `golden`（`fallback` ステップを含む）/ `visual` の
      各ジョブに分割する。
- [x] `ios-e2e.yml` に PR ごとの `golden (idb)` ジョブを追加し、`E2E` ゲートの `needs:` からは除外する。
- [x] プラットフォームごとの E2E ジョブ構成の規約を `docs/ai-development.md`（+ 対訳）に記載する。

### ログ

- 提案と実装をまとめて起草し、1 つの PR で着地させました（BE 作成を伴うため PR は人間が開きます。
  `roadmap-id` がマージ時に本番の BE id を採番し、`Implementing PR` 行はそのときに補完します）。Android
  レーンを `smoke`/`golden`/`visual` のジョブに分割し、iOS に PR ごとの `golden (idb)` ジョブ（必須
  `E2E` ゲートからは除外）を追加し、プラットフォームごとの E2E ジョブ構成を `docs/ai-development.md`
  と `docs/ja/` の対訳に記載しました。

## 参考

- [`android-e2e.yml`](../../.github/workflows/android-e2e.yml)、
  [`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml)、
  [`web-e2e.yml`](../../.github/workflows/web-e2e.yml)、
  [`idb-monitor.yml`](../../.github/workflows/idb-monitor.yml)
- [BE-0122](../BE-0122-workflow-name-legibility/BE-0122-workflow-name-legibility.md)：ワークフローとジョブ名の可読性向上。この項目は、その論拠をジョブの粒度と中身の水準まで一段掘り下げて引き継ぎます。
- [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)、
  [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md)、
  [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)：
  Android E2E レーンの経緯。この項目が今回ジョブに分割する中身そのものです。
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)：単位5の
  スコープ外の注記で触れているドライバ conformance スイート。
