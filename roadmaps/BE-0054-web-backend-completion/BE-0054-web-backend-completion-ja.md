[English](BE-0054-web-backend-completion.md) · **日本語**

# BE-0054 — Web backend の完成（リッチな capability と並列実行）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0054](BE-0054-web-backend-completion-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0054") |
| 実装 PR | [#187](https://github.com/bajutsu-e2e/bajutsu/pull/187)（native network スライス）、[#297](https://github.com/bajutsu-e2e/bajutsu/pull/297)（並列レーン）、[#298](https://github.com/bajutsu-e2e/bajutsu/pull/298)（console / page-error 証跡）、[#299](https://github.com/bajutsu-e2e/bajutsu/pull/299)（動画証跡）、[#300](https://github.com/bajutsu-e2e/bajutsu/pull/300)（擬似 multiTouch） |
| トピック | プラットフォーム対応 |
<!-- /BE-METADATA -->

## はじめに

Web（Playwright）backend の最初のスライス（[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）は、決定論的な `run` パスを出荷しました。capability のセットは `{query, elements, screenshot, semanticTap, conditionWait}` にあえて絞り、Linux 上で小さなデモ Web アプリを駆動しました。

BE-0041 が Web を最初に選んだのは、Web が capability モデルの**リッチ端**に届くからです。本項目は、backend をそのリッチ端まで引き上げて完成させます。追加するのは、ネイティブ network、video と console の evidence、擬似 multi-touch、複数の BrowserContext にわたる並列実行です。

## 動機

BE-0041 は、Web が抽象のプラットフォーム非依存性を最も低コストで実証できる場所だと論じました。Playwright が `capabilities()` のリッチ端に届くからです。リッチ端には、`semanticTap`、ネイティブ `conditionWait`、ネイティブ `network`、video、擬似 `multiTouch` が含まれます。v1 のスライスは、ゲートが緑のまま動く `run` を素早く出荷するために、あえて lean なセットで止めました。

v1 が先送りした capability は、Web をリッチ端の証明にするものです。この差を埋めると、「Web が動く」が「Web が capability の全勾配を行使する」に変わります。残る差は 4 つあります。

1. **ネイティブ `network`。** Playwright の route interception は、リクエストの観測とスタブを単一の API で行います。idb は BajutsuKit を介してアプリ層でモックするので、Web がネイティブ network を持つ**最初の** backend になります。アプリ側の協力なしに、`request` アサーションと HTTP モックが動きます。
2. **video と console の evidence。** `BrowserContext` の録画と `console` / `pageerror` の取得は、simctl の video と `deviceLog` という interval provider の Web 版にあたります。`capture` ポリシーが、Web でも同じ evidence 種別を運べるようになります。
3. **擬似 `multiTouch`。** Playwright は、ピンチと回転を合成できます。`run` が現在 Web で拒否しているジェスチャ step（`UnsupportedAction`）が、動くようになります。
4. **並列実行。** `BrowserContext` はほぼ無償の「デバイス」なので、N 個の context が N 本のレーンになります。v1 は単一レーンで、ダミーの udid が 1 本、`workers = 1` です。本項目で、device pool の Web 分岐を複数レーンへ一般化し、iOS の並列実行と揃えます。

## 詳細設計

### v1 が残したシーム

| capability / 機能 | v1（BE-0041） | 本項目 |
|---|---|---|
| `network`（観測 + スタブ） | — | `page.route()` interception → `request` アサーション + HTTP モック |
| video evidence | — | `BrowserContext` の `record_video_dir` → `video` capture 種別、**出荷済み（#299）** |
| console / page-error ログ | — | `page.on("console")` / `on("pageerror")` → `deviceLog` 相当の種別、**出荷済み（#298）** |
| `multiTouch`（ピンチ / 回転） | `UnsupportedAction` | タッチ点の合成 → `MULTI_TOUCH` を広告、**出荷済み（#300）** |
| 並列レーン | 単一レーン（`workers = 1`） | pool の Web 分岐で N 個の `BrowserContext` レーン、**出荷済み（#297）**: `--workers N` が N 本の Web レーン |

各行は、既存のシームに対応します。

- capture provider は、`FileSink` の interval 処理を拡張します。この処理は現在、simctl の `udid` で gate されています。
- network capability は、iOS で BajutsuKit が供給するのと同じ `request` アサーション経路に差し込みます。
- 並列レーンは、`bajutsu/runner/pool.py` に追加した `is_web` 分岐を一般化します。

決定論コア（セレクタ解決、オーケストレータ、シナリオ DSL）は、v1 と同じく変わりません。

### Web の record デモ

driver がリッチな capability を広告すれば、AI の `record` で Web シナリオを作成することはすでに可能です。driver が `query`、`tap`、`type` を実装しているからです。Web の `record` デモは、**新規項目ではありません**。既存の record 体験の提案が扱います。[BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md)（アクション捕捉 record）と [BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md) です。どちらも、任意の backend に適用されます。

## 検討した代替案

- **BE-0041 に差し戻す。** 却下します。BE-0041 はスライスを出荷済みで、現在は*実装中*です。残ったリッチ端の作業を独立した項目として扱えば、着手済みのスコープと先送りのスコープを明瞭に保てます。閉じたスライスを開け直さずに済みます。
- **capability ごとに別項目を立てる（network、video、multi-touch、並列）。** 過剰な分割として却下します。4 つの capability は、「Web backend を capability モデルのリッチ端まで引き上げる」という、1 つのまとまった取り組みです。pool の Web 分岐や `FileSink` の interval といった、同じシームを共有します。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[BE-0041 — Web（Playwright）backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)、[BE-0009 — クロスプラットフォーム抽象](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)、[multi-platform.md](../../docs/multi-platform.md)、`bajutsu/drivers/playwright.py`、`bajutsu/runner/pool.py`、`bajutsu/evidence.py`。
