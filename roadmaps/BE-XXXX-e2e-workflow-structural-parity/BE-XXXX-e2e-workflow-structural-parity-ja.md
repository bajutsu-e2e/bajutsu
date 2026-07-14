[English](BE-XXXX-e2e-workflow-structural-parity.md) · **日本語**

# BE-XXXX — Structural parity across platform E2E workflows

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-e2e-workflow-structural-parity-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform support |
<!-- /BE-METADATA -->

## はじめに

Bajutsu には、プラットフォームごとの実機・実環境 E2E ワークフローが3つあります。
[`android-e2e.yml`](../../.github/workflows/android-e2e.yml)、
[`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml)、
[`web-e2e.yml`](../../.github/workflows/web-e2e.yml) です。対称性に向けた最初の一歩はすでに入って
います。iOS の機能ジョブと必須 `E2E` ゲートを抱えていた独立ワークフロー `e2e.yml` が `ios-e2e.yml` に
統合され、iOS レーンは `smoke (idb)`・`xcuitest (codegen)`・`xcuitest (multi-touch)`・
`conformance (idb + xcuitest)`・`visual (idb)`・必須の `E2E` 集約ジョブを備えた、単体で完結する1
ファイルになりました。この3ファイルの間には、構造上の非対称がまだ2つ残っています。

- `android-e2e.yml` はいまだに **1つのジョブ** `smoke + golden + visual + fallback (adb)` に、機能
  シナリオの実行・element ツリーの golden 比較・ピクセル単位の visual regression test（VRT）・
  resident/fallback チャネルの確認をまとめて詰め込んでいます。`ios-e2e.yml` と `web-e2e.yml` が作業を
  名前付きジョブに分けているのに対して、Android だけが束ねたままです。
- iOS には **PR ごとの element ツリー golden がありません**。`ios-e2e.yml` は
  smoke/codegen/gestures/conformance/visual を走らせますが、idb の golden は
  [`idb-monitor.yml`](../../.github/workflows/idb-monitor.yml) で週次実行されるのみです。一方
  `android-e2e.yml` は golden を関連する PR ごとに走らせています。

3つのレーンが共有する機能面の核 ── 「bajutsu は実際にそのプラットフォームを操作できるのか」 ── は
**smoke** ジョブです。実バックエンド上で showcase のシナリオを `bajutsu run` で回し、決定的に合否を
判定します。この項目は、その smoke ジョブとその周辺のチェックを、3プラットフォームで構造的に対称に
します。

## 動機

- **プラットフォームの E2E ワークフローは、ジョブ単位でどの機能が壊れたかを示すべきです。**
  `android-e2e.yml` の単一ジョブは、機能スモーク・golden・visual・fallback という4つの異なる確認を1つの
  合否として報告します。チェックが赤くなっても、実行ログを開かない限り4つのうちどれが壊れたのかレビュ
  アーにはわかりません。チェック一覧こそが最初に読まれ、YAML 本体はそうではないという点は
  [BE-0122](../BE-0122-workflow-name-legibility/BE-0122-workflow-name-legibility.md) がすでに確立して
  います。同じ論拠は、ワークフロー名だけでなく、ワークフロー内部のジョブ粒度にも一段そのまま当てはまり
  ます。`ios-e2e.yml` と `web-e2e.yml` の複数ジョブ分割はその目指すべき形をすでに示しており、束ねた
  ままなのは Android レーンだけです。
- **機能面のカバレッジは、プラットフォームが実際に対応している範囲では対称であるべきです。** iOS と
  Android はどちらも、正規化したアクセシビリティツリーを固定する element ツリー golden（BE-0006）を
  持っています。Android はこれを PR ごとに、iOS は週次のみ走らせています。iOS が週次のみだったことには
  正当な理由 ── `idb_companion` の upstream ドリフト監視を PR の活動から切り離すこと（`idb-monitor.yml`
  の冒頭コメント）── がありました。ただしその論拠は*監視*にかかるものであって、*回帰チェック*にかかる
  ものではありません。決定的でホスト非依存なツリー golden が、Android と同じように各 PR も守ることを
  妨げるものは何もありません。
- **文書化されていない非対称性は、不具合に見えてしまいます。** iOS の golden が週次のみだったことは
  `ios-e2e.yml` や `android-e2e.yml` からは見えませんでした。この項目を起こす調査自体が、それを意図した
  選択ではなく説明のつかない不整合として最初に読んでしまいました。プラットフォームごとのジョブ構成を
  書き出すこと（単位3）で、次の読み手が同じ推測をやり直さずに済むようにします。

## 詳細設計

作業は CI ワークフローの再構成にとどまり、プロダクトコードの変更は含みません。ここでの変更は
`run`・CI の合否判定経路に LLM を持ち込むものでもないため、第一原則（AI は著者・調査役であり判定役
ではない）には触れません。既存の決定的なチェックを整理し直すだけです。*はじめに*で述べた iOS
ファイルの統合はすでに別途入っており、残るのは以下の3つの単位です。

1. **`android-e2e.yml` の単一ジョブを、観点ごとのジョブに分割します。** `smoke` / `golden` /
   `visual` として、`ios-e2e.yml` と `web-e2e.yml` のジョブ分割に合わせます。それぞれ、すでに通って
   いる Android の `make` ターゲットに対応します。
   - `smoke (adb)` → `make -C demos/showcase/android e2e`（Compose を全シナリオ、Views を id 検証済み
     サブセットで回す ── 機能面の核）。
   - `golden (adb)` → `make e2e-golden && make e2e-fallback`。resident チャネル上で element ツリー
     golden を実行し、続いて `BAJUTSU_ADB_RESIDENT=0` で再実行して `uiautomator dump` フォールバックを
     動かし、両チャネルが同じツリーを返すことを確認します（BE-0245）。`fallback` を4つ目のジョブに
     せずこのジョブ内のステップに留めるのは、同じ golden をもう一方のチャネルで再実行するだけだから
     です。切り出しても同じセットアップを重複させるだけで、特定しやすさは増えません。
   - `visual (adb)` → `make e2e-visual`（ピクセル VRT。非必須、ホスト依存のベースライン）。

   各ジョブは自前で AVD を起動します。iOS の従量制 macOS ランナー（10倍課金）と違い、Android は標準
   価格の Linux 上で KVM を使って走るため、ジョブごとの emulator 起動は特定しやすさと引き換えに許容
   できるコストです。セットアップ（uv、JDK、Gradle、KVM、AVD キャッシュ）はジョブごとに重複させます。
   これは `ios-e2e.yml` がすでにジョブごとのセットアップを重複させているのに合わせたもので、composite
   action を新たに導入しません（この項目は新しい CI の仕組みを追加しません）。Android は非必須です
   （ブランチ保護の ruleset に含まれていません）から、分割にあたって集約ジョブは不要です。
   `web-e2e.yml` と同じく、独立した3ジョブになります。

2. **`ios-e2e.yml` に、PR ごとの `golden (idb)` ジョブを追加します。** `bajutsu-e2e` アクション経由で
   `golden.yaml` を idb 上で実行します（`idb-monitor.yml` が週次で回しているのと同じシナリオ・
   ベースライン）。既存の `changes` ジョブでゲートしますが、`visual` とまったく同じように、**`E2E`
   ゲートの `needs:` からは意図的に除外します**。ツリー golden は決定的でホスト非依存ですが、
   `idb_companion` は upstream の依存であり、そのドリフトが Bajutsu 側の変更と無関係に golden を
   赤くしうるため、golden のドリフトはマージを止めずに PR ごとのシグナルとして表れるべきだからです。
   これにより iOS の golden は Android のもの（非必須レーン上で PR ごとに走る）と実効的に同じ地位に
   なります。週次の `idb-monitor.yml` の golden は残します。その目的は*最新の* `idb_companion` に対して
   走らせるという前方視点のものであり、PR ごとのジョブが置き換えるものではないからです。

3. **プラットフォームごとの E2E ジョブ構成の規約を書き出します。** smoke・golden・visual・
   conformance・codegen・gestures・fallback のうち、各プラットフォームの実機・実環境ワークフローが
   どれを備えるか、そして備えない場合はなぜか（例: Web には golden/visual がない、Android にはまだ
   conformance がない ── 単位4）を、[`docs/ai-development.md`](../../docs/ai-development.md)（英語版と
   `docs/ja/` の対訳）に記載します。将来のバックエンド（`CLAUDE.md` にある通り、次に予定されている
   4つ目は Flutter です）は、3つのファイルから逆算する代わりに、書き出された形に合わせられるように
   なります。

4. **スコープ外とし、フォローアップ項目として切り出します**: Android には、実機上でのドライバ
   conformance ジョブがありません（`test_driver_conformance_ondevice.py` や
   `test_driver_conformance_web.py` に対応する adb 版が存在しません）。これは実在するカバレッジの
   欠落ですが、新しい conformance スイートを書くことはテスト作成の作業であり、ワークフローの再構成
   ではないため、この項目のスコープには含めません。

## 検討した代替案

- **Android の golden と fallback を、2つの別々のジョブに分割する案。** 見送りました。`fallback` は
  同じ `golden.yaml` を resident チャネルを切って再実行するだけなので、別ジョブにすると APK ビルドと
  AVD 起動を重複させて同じツリーを別の読み取り経路で確認することになります。`golden` ジョブ内の2つ目
  のステップに留めれば、冗長なセットアップなしにチャネル対称性のカバレッジが得られます。
- **iOS の PR ごと golden を必須ゲートの一員にする（`E2E` の `needs:` に加える）案。** 見送りました。
  そうすると `idb_companion` の upstream リリースが golden を赤くし、ベースラインを録り直すまで*すべて
  の*マージを止めうるからです。まさにこの結合を PR の経路から切り離すために、週次の `idb-monitor.yml`
  が作られたのでした。`visual` と同じく `needs:` から除外することで、マージを止めるリスクなしに PR
  ごとのシグナルを保ちます。
- **3プラットフォームの E2E ワークフローを、1つのパラメータ化・再利用可能なワークフローへ統合する案。**
  構造的な対称性を一度に最大化できますが、今後の変更の影響範囲が1プラットフォームから3プラット
  フォームへと広がってしまいます。また、必須チェックのブランチ保護設定も、統合後のワークフローが
  生成するジョブ名に合わせて移行する必要があります。ここで得られる利点に対してリスクが見合わない
  ため、見送りました。単位3で述べるファイルごとの規約の文書化で、この案が持つ可読性向上の効果の
  大部分は得られます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `android-e2e.yml` の単一ジョブを `smoke` / `golden`（`fallback` ステップを含む）/ `visual` の
      各ジョブに分割する。
- [ ] `ios-e2e.yml` に PR ごとの `golden (idb)` ジョブを追加し、`E2E` ゲートの `needs:` からは除外する。
- [ ] プラットフォームごとの E2E ジョブ構成の規約を `docs/ai-development.md`（+ 対訳）に記載する。

## 参考

- [`android-e2e.yml`](../../.github/workflows/android-e2e.yml)、
  [`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml)、
  [`web-e2e.yml`](../../.github/workflows/web-e2e.yml)、
  [`idb-monitor.yml`](../../.github/workflows/idb-monitor.yml)
- [BE-0122](../BE-0122-workflow-name-legibility/BE-0122-workflow-name-legibility.md) — ワークフロー・
  ジョブ名の可読性向上。この項目は、その論拠をジョブ粒度・中身の水準まで一段掘り下げて引き継ぎます。
- [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)、
  [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md)、
  [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)
  — Android E2E レーンの経緯。この項目が今回ジョブに分割する中身そのものです。
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — 単位5の
  スコープ外の注記で触れているドライバ conformance スイート。
