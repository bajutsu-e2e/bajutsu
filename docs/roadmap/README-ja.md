[English](README.md) · **日本語**

# Bajutsu roadmap / backlog

> 今後実装したい機能を管理するドキュメントです。各項目は 1 ファイル（1 項目 = 1 BE ID）。
> まとまっていない思いつきはまず [未整理アイデア](#未整理アイデア) に追加し、内容が固まったら
> 採番済みの項目へ昇格させます。
>
> - **現状（実装済み / 未配線）の正確な一覧**は
>   [architecture.md#実装状況](../ja/architecture.md#実装状況) が真実です。ここは「これから」を扱います。
> - 設計の背景（なぜ）は [`DESIGN.md`](../../DESIGN.md)。
> - **全体の戦略的な方向性**は [vision.md](../ja/vision.md)。

## 凡例

**優先度** — `P0`（次にやる） / `P1`（やる） / `P2`（あると良い） / `P3`（アイデア段階）
**状態** — 💡 アイデア / 📋 計画済み / 🚧 進行中 / ❄️ 保留 / ✅ 完了

## ロードマップ項目の追加 — BE ID（エージェントは厳守）

すべてのロードマップ項目は `BE-NNNN-<slug>/` ディレクトリに、英語版 `BE-NNNN-<slug>.md` と
日本語版 `BE-NNNN-<slug>-ja.md`（ID・slug は同一）を入れます。**BE** は *Bajutsu Evolution*、
`NNNN` は**ゼロ詰め 4 桁・単調増加**の ID です。

ロードマップ項目を追加するとき:

1. **次の ID を採番** = 既存の最大 `BE-NNNN` + 1。次で確認します:
   ```bash
   ls -d docs/roadmap/BE-*/ | sort | tail -1
   ```
   番号の再利用・飛ばし・当て推量は禁止です。**または未定のままにする:** 項目を
   `BE-XXXX-<slug>`（リテラルのプレースホルダ）と名付け、採番は CI に任せます ——
   [`roadmap-id`](../../.github/workflows/roadmap-id.yml) ワークフローが `docs/roadmap/**`
   に触れる PR ごとに [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py)
   を実行し、空いている次の ID を採番してブランチへリネームを push し返します。
   `ideation` skill はこの方式を使い、進行中の 2 つのブランチが同じ番号を
   取り合うのを防ぎます。
2. **項目ディレクトリと両言語のファイルを作成** — `docs/roadmap/BE-NNNN-<slug>/BE-NNNN-<slug>.md`
   （英語）と `docs/roadmap/BE-NNNN-<slug>/BE-NNNN-<slug>-ja.md`（日本語・同一 ID & slug）— そして
   **両方**のインデックスページの該当トピック表に行を追加します。
3. **ID は不変**です。既存項目を採番し直してはいけません — 状態が変わっても、完了しても、表から削除しても。
   一度割り当てた BE ID は、その項目を永遠に指します。

各ファイルは **Swift-Evolution の proposal フォーマット**に従います: メタデータブロック（`* 提案`・
`* 状態`・`* トラック`・`* トピック` …）の後に `## はじめに` / `## 動機` / `## 詳細設計` /
`## 検討した代替案` / `## 参考`（埋められる範囲だけ記入し、不明は `TBD`）。**状態**がトラックを
決めます: `実装済み`・`可決・実装中` は **可決済み**（意思決定・実装の記録）に、`提案`・`提案（保留）`
は **提案**（検討中）に並びます。進捗に応じてファイルを移動するのではなく状態を更新してください。

---

## 可決済み

下した意思決定 — すでに実装済み、または可決済みで今後実装します。プロジェクトの**意思決定・実装の記録**です。

### マイルストーン（M1–M4）

粗い粒度のデリバリ・マイルストーン（M1–M4）— プロジェクトの**意思決定・実装の記録**です。4 つとも可決・出荷済みで、これらが分解された細かい機能は下のトピック群にあります。

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0001](BE-0001-m1-deterministic-runner/BE-0001-m1-deterministic-runner-ja.md) | 決定的ランナー（M1） | 実装済み |
| [BE-0002](BE-0002-m2-ai-loop-and-evidence/BE-0002-m2-ai-loop-and-evidence-ja.md) | AI オーサリングループと証跡（M2） | 実装済み |
| [BE-0003](BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci-ja.md) | codegen・トレース・ネットワーク・CI（M3） | 実装済み |
| [BE-0004](BE-0004-m4-self-healing-triage/BE-0004-m4-self-healing-triage-ja.md) | 自己修復トリアージ（M4） | 実装済み |

### プラットフォーム拡張（着手済みスライス）

マルチプラットフォーム化の最初のスライスは着手済みです: **プラットフォーム対応の backend レジストリ**により、`--backend` / `backend:` は bare な actuator に加えてプラットフォームトークン（`ios` / `android` / `web` / `fake`）を受け付け、実装済みかつ利用可能な最初の actuator へ展開します。各プラットフォームの三点セットの残り（プラットフォーム別 environment manager + actuator driver）は、提案の[プラットフォーム拡張](#プラットフォーム拡張android--web--flutter)で扱います。

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0042](BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md) | プラットフォーム対応の backend レジストリと選択 | 実装済み |

### オーサリング体験（record / GUI エディタ）

AI 駆動の `record`（Tier 1）は実装済みです（[recording.md](../ja/recording.md)）。このセクションの目的は **AI に依らない操作キャプチャ**と**シナリオの視覚的編集**で、記録 → 編集 → 再実行のサイクルを人が扱いやすくすることです。ローカル Web UI ランチャ `bajutsu serve` はその最初のステップです。

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0011](BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md) | ローカル Web UI（`bajutsu serve`） | 実装済み |

### 自己修復トリアージ（M4）

AI を「判定者」にせず調査役に限定したまま、回帰の保守コストを下げます。

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0021](BE-0021-ai-triage/BE-0021-ai-triage-ja.md) | AI triage（原因要約・修正提案） | 実装済み |
| [BE-0022](BE-0022-update-structured-fixes/BE-0022-update-structured-fixes-ja.md) | `update`（最小差分提案＝構造化 fix の適用） | 実装済み |
| [BE-0023](BE-0023-self-healing-guards/BE-0023-self-healing-guards-ja.md) | 「テストを甘くする」防止策 | 実装済み |

### 競合調査（MagicPod / Autify）由来の候補

MagicPod・Autify は **AI 自己修復（self-healing）+ ノーコード + クラウド端末ファーム + ビジュアル系**を主軸とするツールです。両社の旗艦機能は「**実行中に AI がロケータ/タップ位置を自動補正する**」ことですが、これは Bajutsu の中核原則（[DESIGN §2](../../DESIGN.md)：**AI を CI ゲートに入れない / 決定性ファースト**）と直接矛盾します。そのため、決定的に取り込める機能とゲート外限定で取り込める機能を分けて評価しました。

| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0029](BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md) | ビジュアル回帰アサーション | 実装済み | 両社 |
| [BE-0030](BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md) | パラメータ化シェアドステップ | 実装済み | MagicPod |
| [BE-0031](BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios-ja.md) | データ駆動シナリオ | 実装済み | MagicPod |
| [BE-0032](BE-0032-secret-variables/BE-0032-secret-variables-ja.md) | シークレット変数 | 実装済み | MagicPod |
| [BE-0033](BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md) | シナリオ変数 + 軽い制御フロー | 実装中 | MagicPod |
| [BE-0034](BE-0034-tags-selective-runs/BE-0034-tags-selective-runs-ja.md) | タグ / ラベル + 選択実行 | 実装済み | MagicPod |
| [BE-0039](BE-0039-self-healing-propose-optin/BE-0039-self-healing-propose-optin-ja.md) | 自己修復は「提案＋opt-in 適用」に限定 | 実装済み | 両社 |

### 統合・自動化（MCP）

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0017](BE-0017-mcp-server/BE-0017-mcp-server-ja.md) | MCP サーバ化 | 実装済み |

## 提案

検討中 — まだ決定していません。決定したら *可決済み* に昇格してください。

### 実機検証（M1 クローズアウト）

決定的コアは FakeDriver で end-to-end に通っており、**idb backend の subprocess 実行（`describe-all` パース・frame-center tap/text/swipe）と simctl 起動シーケンスは実機（iPhone 17 Pro / 最新 iOS）で検証済み**です。残るのは継続メンテ系の監視のみです。

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0005](BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring-ja.md) | `idb_companion` バージョン監視 | 提案 |
| [BE-0006](BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization-ja.md) | idb 要素ツリー正規化の精度 | 提案 |

### プラットフォーム拡張（Android / Web / Flutter）

現状はスコープを **iOS Simulator 限定**としています（[DESIGN §1](../../DESIGN.md)）。このセクションは driver / backend 抽象を活用したマルチプラットフォーム化の方向性を扱います。コアのスコープ文の更新を伴う戦略的な判断です。全体像（大枠）は [multi-platform.md](../ja/multi-platform.md) にあり、**プラットフォーム別の具体的な設計**は以下の各項目にあります: [BE-0009](BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md) が共有の抽象、続いて Web（既存の Linux ゲートで動くため最初に推奨）・Android・Flutter です。最初のスライス —— プラットフォーム対応の backend レジストリ —— はすでに着手済みです（[BE-0042](BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)、可決済み）。

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0007](BE-0007-android-backend/BE-0007-android-backend-ja.md) | Android backend | 提案 |
| [BE-0008](BE-0008-flutter-support/BE-0008-flutter-support-ja.md) | Flutter 対応 | 提案 |
| [BE-0009](BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md) | 抽象のクロスプラットフォーム化 | 提案 |
| [BE-0010](BE-0010-update-scope-statement/BE-0010-update-scope-statement-ja.md) | スコープ文の更新 | 提案 |
| [BE-0041](BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) | Web（Playwright）backend | 提案 |

### オーサリング体験（record / GUI エディタ）

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0012](BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md) | 操作キャプチャ record | 提案 |
| [BE-0013](BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md) | シナリオ GUI エディタ | 提案 |
| [BE-0014](BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md) | 既存 AI record との棲み分け | 提案 |

### Web UI のホスティング（クラウド / セルフホスト）

ローカルの `bajutsu serve` ランチャを共有サービスにします。ランナーは iOS Simulator を駆動するため Mac が必要で、コントロールプレーン（Linux）⇄ macOS ワーカーの分離を強います。[BE-0015](BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) はマネージドなマルチテナント公開スタックを選定し、[BE-0016](BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) は自前の Mac での運用 —— 既存 `serve` で今日から使える単一 Mac 構成と、完全セルフホストのマルチテナント構成 —— を扱います。

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0015](BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) | Web UI の公開 / クラウドホスティング | 提案 |
| [BE-0016](BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) | Web UI のセルフホスティング | 提案 |

### 統合・自動化（MCP 化）

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0018](BE-0018-evidence-as-mcp-resources/BE-0018-evidence-as-mcp-resources-ja.md) | 証跡を MCP リソースで返す | 提案 |

### バックエンド拡張（iOS actuator）

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0019](BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) | XCUITest backend | 提案 |
| [BE-0020](BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback-ja.md) | マルチ backend 証跡フォールバック | 提案 |

### doctor / オンボーディング

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0024](BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md) | doctor / オンボーディング | 提案 |

### codegen 網羅性

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0025](BE-0025-coordinate-swipe-generation/BE-0025-coordinate-swipe-generation-ja.md) | 座標 swipe の生成 | 提案 |
| [BE-0026](BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) | 未対応構文の縮小 | 提案 |

### その他・保留

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0027](BE-0027-mock-server-external/BE-0027-mock-server-external-ja.md) | `mockServer`（外部モック） | 保留 |
| [BE-0028](BE-0028-evidence-rule-overmatch-guard/BE-0028-evidence-rule-overmatch-guard-ja.md) | 証跡ルールの過剰マッチ対策 | 提案 |

### 競合調査（MagicPod / Autify）由来の候補

| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0035](BE-0035-device-control-primitives/BE-0035-device-control-primitives-ja.md) | デバイス制御プリミティブ拡張 | 提案 | MagicPod |
| [BE-0036](BE-0036-utility-steps/BE-0036-utility-steps-ja.md) | ユーティリティステップ | 提案 | MagicPod |
| [BE-0037](BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md) | WebView / ハイブリッド対応 | 提案 | MagicPod |
| [BE-0038](BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md) | 自律クロール探索（App Explorer 風） | 提案 | Autify VAX |
| [BE-0040](BE-0040-ai-assertions/BE-0040-ai-assertions-ja.md) | AI アサーション | 保留 | MagicPod |

### 開発基盤（コントリビュータ体験）

このリポジトリで並行作業する多数のセッションの摩擦を減らす — マージコンフリクトを設計の臭いとして扱い、独立した変更が互いに素なファイルだけに触れるようファイル流動を見直す。

| ID | 項目 | 状態 |
|---|---|---|
| [BE-XXXX](BE-XXXX-conflict-resistant-file-flow/BE-XXXX-conflict-resistant-file-flow-ja.md) | コンフリクトに強いファイル流動（索引の生成・ファイル分割・git 衛生） | 提案 |

## 取り込まない（既に充足 / スコープ外）

- **変更履歴・バージョン管理** — シナリオは YAML で git 管理されるため既に充足しています。
- **クラウド端末ファーム / 実機・クラウド実行** — iOS Simulator 限定の現スコープ外です（[DESIGN §1](../../DESIGN.md)）。マルチプラットフォームは別途、提案として管理しています（*プラットフォーム拡張* の項目）。
- **ステップ毎スクショ / エラー時 UI ツリー / 端末ログ** — 証跡サブシステム（capturePolicy + `result:error` 安全網）で充足済みです。
- **NL→テスト生成（Autopilot 相当）** — 既存 `record` + *オーサリング体験* の項目と重複します。
- **スケジューリング / Slack / TestRail 連携** — CI・通知レイヤの領域です。優先度低（必要なら別途対応）。
- **失敗テストの自動リトライ** — 決定性ファースト（固定 sleep 排除・条件待機）と緊張関係にあります。flaky を隠蔽する可能性があるため、入れるなら quarantine 用途に限定して検討が必要です。

---

## 未整理アイデア

> まとまっていない思いつきはここへ追加してください。後で採番済みの BE 項目に昇格させます。

-
