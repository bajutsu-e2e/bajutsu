[English](BE-0054-web-backend-completion.md) · **日本語**

# BE-0054 — Web backend の完成（リッチな capability と並列実行）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0054](BE-0054-web-backend-completion-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| 実装 PR | [#187](https://github.com/bajutsu-e2e/bajutsu/pull/187)（native network スライス）、[#297](https://github.com/bajutsu-e2e/bajutsu/pull/297)（並列レーン）、[#298](https://github.com/bajutsu-e2e/bajutsu/pull/298)（console / page-error 証跡）、[#299](https://github.com/bajutsu-e2e/bajutsu/pull/299)（動画証跡）、[#300](https://github.com/bajutsu-e2e/bajutsu/pull/300)（擬似 multiTouch） |
| トピック | プラットフォーム拡張（着手済みスライス） |
<!-- /BE-METADATA -->

## はじめに

Web（Playwright）backend の最初のスライス（[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）は、
意図的に絞った capability セット `{query, elements, screenshot, semanticTap, conditionWait}` で
決定論的な `run` パスを出荷し、Linux 上で小さなデモ Web アプリを駆動できるようにしました。本項目は、
BE-0041 が「Web を最初に選ぶ理由」とした capability モデルの**リッチ端**まで backend を引き上げます。
すなわち、ネイティブ network、video / console の evidence、擬似 multi-touch、そして複数の
BrowserContext による並列実行です。

## 動機

BE-0041 は、Web が抽象のプラットフォーム非依存性を最も低コストで実証できる場所だと論じました。理由は
Playwright が `capabilities()` のリッチ端（`semanticTap`、ネイティブ `conditionWait`、ネイティブ
`network`、video、擬似 `multiTouch`）に届くからです。v1 のスライスは、ゲートが緑のまま動く `run`
を素早く出荷するために、あえて lean なセットで止めました。そこで先送りした capability は、まさに Web を
リッチ端の証明たらしめるものであり、この差を埋めることが「Web が動く」を「Web が capability の全勾配を
行使する」へと変えます。

1. **ネイティブ `network`**：Playwright の route interception は、リクエストの観測とスタブを単一の
   API で行います。Web はネイティブ network を持つ**最初の** backend になります（idb は BajutsuKit を介して
   アプリ層でモックします）。アプリ側の協力なしに `request` アサーションと HTTP モックが動きます。
2. **video / console の evidence**：`BrowserContext` の録画と `console` / `pageerror` の取得は、
   simctl の video / `deviceLog` という interval provider の Web 版にあたります。`capture` ポリシーが
   Web でも同じ evidence 種別を運べるようになります。
3. **擬似 `multiTouch`**：Playwright はピンチ / 回転を合成できるので、`run` が現在 Web で拒否している
   （`UnsupportedAction`）ジェスチャ step が動くようになります。
4. **並列実行**：`BrowserContext` はほぼ無償の「デバイス」なので、N 個の context が N レーンになります。
   v1 は単一レーン（ダミー udid 1 本、`workers = 1`）であり、本項目で device pool の Web 分岐を複数
   レーンへ一般化し、iOS の並列実行と揃えます。

## 詳細設計

### v1 が残したシーム

| capability / 機能 | v1（BE-0041） | 本項目 |
|---|---|---|
| `network`（観測 + スタブ） | — | `page.route()` interception → `request` アサーション + HTTP モック |
| video evidence | — | `BrowserContext` の `record_video_dir` → `video` capture 種別、**出荷済み（#299）** |
| console / page-error ログ | — | `page.on("console")` / `on("pageerror")` → `deviceLog` 相当の種別、**出荷済み（#298）** |
| `multiTouch`（ピンチ / 回転） | `UnsupportedAction` | タッチ点の合成 → `MULTI_TOUCH` を広告、**出荷済み（#300）** |
| 並列レーン | 単一レーン（`workers = 1`） | pool の Web 分岐で N 個の `BrowserContext` レーン、**出荷済み（#297）**: `--workers N` が N 本の Web レーン |

いずれも既存のシームに対応します。capture provider は `FileSink` の interval 処理（現在は simctl の
`udid` で gate されている）を拡張し、network capability は iOS で BajutsuKit が供給するのと同じ
`request` アサーション経路に差し込み、並列レーンは `bajutsu/runner/pool.py` に追加した `is_web` 分岐を
一般化します。決定論コア（セレクタ解決、オーケストレータ、シナリオ DSL）は v1 と同じく不変です。

### Web の record デモ

driver がリッチな capability を広告すれば、AI の `record` による Web シナリオの作成はすでに可能です
（driver は `query`/`tap`/`type` を実装しています）。Web の `record` デモは**新規項目ではありません**。任意の
backend に適用される既存の record 体験の提案
（[BE-0012](../../in-progress/BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md) アクション捕捉 record、
[BE-0014](../../proposals/BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md)）
の下で扱います。

## 検討した代替案

- **BE-0041 に差し戻す。** 却下します。BE-0041 はスライスを出荷済みで、現在は *実装中* です。残った
  リッチ端の作業を独立項目として扱う方が、着手済みのスコープと先送りのスコープを明瞭に保てます。閉じた
  スライスを開け直さずに済みます。
- **capability ごとに別項目（network / video / multi-touch / 並列）。** 過剰な分割として却下します。これらは
  「Web backend を capability モデルのリッチ端まで引き上げる」という一つのまとまった取り組みであり、同じ
  シーム（pool の Web 分岐、`FileSink` の interval）を共有します。

## 参考

[BE-0041 — Web（Playwright）backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)、
[BE-0009 — クロスプラットフォーム抽象](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)、
[multi-platform.md](../../../docs/multi-platform.md)、`bajutsu/drivers/playwright.py`、
`bajutsu/runner/pool.py`、`bajutsu/evidence.py`
