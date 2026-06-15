[English](../../roadmap/README.md) · **日本語**

# Bajutsu roadmap / backlog

> 今後実装したい機能を集約する**生きたドキュメント**。各項目は1ファイル（1 項目 = 1 BE ID）。
> 形になっていない思いつきはまず [未整理アイデア](#未整理アイデア) に放り込み、固まってきたら
> 採番済みの項目へ昇格させる。
>
> - **現状（実装済み / 未配線）の正確な一覧**は
>   [architecture.md#実装状況](../architecture.md#実装状況) が真実。ここは「これから」を扱う。
> - 設計の背景（なぜ）は [`DESIGN.md`](../../../DESIGN.md)。
> - **全体の戦略的な「形」（north star）**は [vision.md](../vision.md)。

## 凡例

**優先度** — `P0`（次にやる） / `P1`（やる） / `P2`（あると良い） / `P3`（アイデア段階）
**状態** — 💡 アイデア / 📋 計画済み / 🚧 進行中 / ❄️ 保留 / ✅ 完了

## ロードマップ項目の追加 — BE ID（エージェントは厳守）

すべてのロードマップ項目は `BE-NNNN-<slug>.md` という単一ファイルにする。**BE** は *Bajutsu
Evolution*、`NNNN` は**ゼロ詰め 4 桁・単調増加**の ID。英語版は `docs/roadmap/`、日本語ミラーは
`docs/ja/roadmap/` に置く（ID と slug は同一）。

ロードマップ項目を追加するとき:

1. **次の ID を採番** = 既存の最大 `BE-NNNN` + 1。次で確認する:
   ```bash
   ls docs/roadmap/BE-*.md | sort | tail -1
   ```
   番号の再利用・飛ばし・当て推量は禁止。
2. **両言語のファイルを作成** — `docs/roadmap/BE-NNNN-<slug>.md`（英語）と
   `docs/ja/roadmap/BE-NNNN-<slug>.md`（日本語ミラー・同一 ID & slug）— そして**両方**の
   `README.md` インデックスの該当トピック表に行を追加する。
3. **ID は不変**。既存項目を採番し直さない — 状態が変わっても、完了しても、表から削除しても。
   一度割り当てた BE ID は、その項目を永遠に指す。

各ファイルは **Swift-Evolution の proposal フォーマット**に従う: メタデータブロック（`* 提案`・
`* 状態`・`* トラック`・`* トピック` …）の後に `## はじめに` / `## 動機` / `## 詳細設計` /
`## 検討した代替案` / `## 参考`（埋められる範囲だけ記入し、不明は `TBD`）。**状態**がトラックを
決める: `実装済み`・`可決・実装中` は **可決済み**（意思決定・実装の記録）に、`提案`・`提案（保留）`
は **提案**（検討中）に並ぶ。進捗に応じてファイルを移動するのではなく状態を更新する。

---

## 可決済み

下した意思決定 —— すでに実装済み、または可決済みで今後実装する。プロジェクトの**意思決定・実装の記録**。

### マイルストーン（M1–M4）

粗い粒度のデリバリ・マイルストーン（M1–M4）—— プロジェクトの**意思決定・実装の記録**。4 つとも可決・出荷済みで、これらが分解された細かい機能は下のトピック群にある。

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0001](BE-0001-m1-deterministic-runner.md) | 決定的ランナー（M1） | 実装済み |
| [BE-0002](BE-0002-m2-ai-loop-and-evidence.md) | AI オーサリングループと証跡（M2） | 実装済み |
| [BE-0003](BE-0003-m3-codegen-traces-network-ci.md) | codegen・トレース・ネットワーク・CI（M3） | 実装済み |
| [BE-0004](BE-0004-m4-self-healing-triage.md) | 自己修復トリアージ（M4） | 実装済み |

### オーサリング体験（record / GUI エディタ）

AI 駆動の `record`（Tier 1）は実装済み（[recording.md](../recording.md)）。ここでの狙いは **AI に依らない操作キャプチャ**と**シナリオの可視編集**で、記録 → 編集 → 再実行のラウンドトリップを人にとって扱いやすくすること。ローカル Web UI ランチャ `bajutsu serve` はその最初の一歩。

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0011](BE-0011-local-web-ui-serve.md) | ローカル Web UI（`bajutsu serve`） | 実装済み |

### 自己修復トリアージ（M4）

AI を「判定者」にせず、調査役に限定したまま回帰の保守コストを下げる。

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0021](BE-0021-ai-triage.md) | AI triage（原因要約・修正提案） | 実装済み |
| [BE-0022](BE-0022-update-structured-fixes.md) | `update`（最小差分提案＝構造化 fix の適用） | 実装済み |
| [BE-0023](BE-0023-self-healing-guards.md) | 「テストを甘くする」防止策 | 実装済み |

### 競合調査（MagicPod / Autify）由来の候補

MagicPod・Autify は **AI 自己修復（self-healing）+ ノーコード + クラウド端末ファーム + ビジュアル系**が DNA。両社の旗艦機能は「**実行中に AI がロケータ/タップ位置を自動補正する**」点だが、これは Bajutsu の核心（[DESIGN §2](../../../DESIGN.md)：**AI を CI ゲートに入れない / 決定性ファースト**）と正面衝突する。よって決定的に取り込めるものとゲート外限定で取り込めるものを分けて評価した。

| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0030](BE-0030-parameterized-shared-steps.md) | パラメータ化シェアドステップ | 実装済み | MagicPod |
| [BE-0031](BE-0031-data-driven-scenarios.md) | データ駆動シナリオ | 実装済み | MagicPod |
| [BE-0032](BE-0032-secret-variables.md) | シークレット変数 | 実装済み | MagicPod |
| [BE-0033](BE-0033-scenario-variables-control-flow.md) | シナリオ変数 + 軽い制御フロー | 実装中 | MagicPod |
| [BE-0034](BE-0034-tags-selective-runs.md) | タグ / ラベル + 選択実行 | 実装済み | MagicPod |
| [BE-0039](BE-0039-self-healing-propose-optin.md) | 自己修復は「提案＋opt-in 適用」に限定 | 実装済み | 両社 |

## 提案

検討中 —— まだ決定していない。決定したら *可決済み* に昇格する。

### 実機検証（M1 クローズアウト）

決定的コアは FakeDriver で end-to-end に通り、**idb backend の subprocess 実行（`describe-all` パース・frame-center tap/text/swipe）と simctl 起動シーケンスは実機（iPhone 17 Pro / 最新 iOS）で検証済み**。残るのは継続メンテ系の監視のみ。

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0005](BE-0005-idb-companion-version-monitoring.md) | `idb_companion` バージョン監視 | 提案 |
| [BE-0006](BE-0006-idb-element-tree-normalization.md) | idb 要素ツリー正規化の精度 | 提案 |

### プラットフォーム拡張（Android / Flutter）

現状はスコープを **iOS Simulator 限定**としている（[DESIGN §1](../../../DESIGN.md)）。driver / backend 抽象を活かしてマルチプラットフォーム化する大きな方向性で、コアのスコープ文の更新を伴う戦略的判断。具体的な方針・設計は [multi-platform.md](../multi-platform.md) に詳述。

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0007](BE-0007-android-backend.md) | Android backend | 提案 |
| [BE-0008](BE-0008-flutter-support.md) | Flutter 対応 | 提案 |
| [BE-0009](BE-0009-cross-platform-abstractions.md) | 抽象のクロスプラットフォーム化 | 提案 |
| [BE-0010](BE-0010-update-scope-statement.md) | スコープ文の更新 | 提案 |

### オーサリング体験（record / GUI エディタ）

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0012](BE-0012-action-capture-record.md) | 操作キャプチャ record | 提案 |
| [BE-0013](BE-0013-scenario-gui-editor.md) | シナリオ GUI エディタ | 提案 |
| [BE-0014](BE-0014-record-demarcation.md) | 既存 AI record との棲み分け | 提案 |
| [BE-0015](BE-0015-web-ui-public-hosting.md) | Web UI の公開ホスティング | 提案 |
| [BE-0016](BE-0016-web-ui-self-hosting.md) | Web UI のセルフホスティング | 提案 |

### 統合・自動化（MCP 化）

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0017](BE-0017-mcp-server.md) | MCP サーバ化 | 提案 |
| [BE-0018](BE-0018-evidence-as-mcp-resources.md) | 証跡を MCP リソースで返す | 提案 |

### バックエンド拡張（iOS actuator）

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0019](BE-0019-xcuitest-backend.md) | XCUITest backend | 提案 |
| [BE-0020](BE-0020-multi-backend-evidence-fallback.md) | マルチ backend 証跡フォールバック | 提案 |

### doctor / オンボーディング

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0024](BE-0024-doctor-onboarding.md) | doctor / オンボーディング | 提案 |

### codegen 網羅性

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0025](BE-0025-coordinate-swipe-generation.md) | 座標 swipe の生成 | 提案 |
| [BE-0026](BE-0026-shrink-unsupported-syntax.md) | 未対応構文の縮小 | 提案 |

### その他・保留

| ID | 項目 | 状態 |
|---|---|---|
| [BE-0027](BE-0027-mock-server-external.md) | `mockServer`（外部モック） | 保留 |
| [BE-0028](BE-0028-evidence-rule-overmatch-guard.md) | 証跡ルールの過剰マッチ対策 | 提案 |

### 競合調査（MagicPod / Autify）由来の候補

| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0029](BE-0029-visual-regression-assertions.md) | ビジュアル回帰アサーション | 提案 | 両社 |
| [BE-0035](BE-0035-device-control-primitives.md) | デバイス制御プリミティブ拡張 | 提案 | MagicPod |
| [BE-0036](BE-0036-utility-steps.md) | ユーティリティステップ | 提案 | MagicPod |
| [BE-0037](BE-0037-webview-hybrid-support.md) | WebView / ハイブリッド対応 | 提案 | MagicPod |
| [BE-0038](BE-0038-autonomous-crawl-exploration.md) | 自律クロール探索（App Explorer 風） | 提案 | Autify VAX |
| [BE-0040](BE-0040-ai-assertions.md) | AI アサーション | 保留 | MagicPod |

## 取り込まない（既に充足 / スコープ外）

- **変更履歴・バージョン管理** — シナリオは YAML で git 管理されるため既に充足。
- **クラウド端末ファーム / 実機・クラウド実行** — iOS Simulator 限定の現スコープ外（[DESIGN §1](../../../DESIGN.md)）。マルチプラットフォームは別途、提案として管理（*プラットフォーム拡張* の項目）。
- **ステップ毎スクショ / エラー時 UI ツリー / 端末ログ** — 証跡サブシステム（capturePolicy + `result:error` 安全網）で充足済み。
- **NL→テスト生成（Autopilot 相当）** — 既存 `record` + *オーサリング体験* の項目と重複。
- **スケジューリング / Slack / TestRail 連携** — CI・通知レイヤの領域。優先度低（必要なら別途）。
- **失敗テストの自動リトライ** — 決定性ファースト（固定 sleep 排除・条件待機）と緊張。flaky 隠蔽になり得るため、入れるなら quarantine 用途に限定して要検討。

---

## 未整理アイデア

> 形になっていない思いつきはここへ。後で採番済みの BE 項目に昇格させる。

-
