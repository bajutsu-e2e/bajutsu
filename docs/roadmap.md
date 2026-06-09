# Bajutsu ロードマップ / バックログ

> 今後実装したい機能を集約する**生きたドキュメント**。思いついた機能はまず
> [未整理アイデア置き場](#未整理アイデア置き場)に放り込み、固まってきたら下の表へ昇格させる。
>
> - **現状（実装済み / 未配線）の正確な一覧**は [architecture.md#implementation-status](architecture.md#implementation-status) が真実。ここは「これから」を扱う。
> - 設計の背景（なぜ）は [`../DESIGN.md`](../DESIGN.md)、開発経緯は [`../REPORT.md`](../REPORT.md)。

## 凡例

**優先度** — `P0`（次にやる） / `P1`（やる） / `P2`（あると良い） / `P3`（アイデア段階）
**状態** — 💡アイデア / 📋計画済み / 🚧進行中 / ❄️保留・スコープ外寄り / ✅完了（完了したら表から削除し architecture.md へ反映）

---

## 1. 実機検証で残るもの（M1 クローズアウト）

決定的コアは FakeDriver で end-to-end に通る。残るのは「実機（Xcode + Simulator）に触れる境界」の検証。

| 機能 | 概要 | 優先度 | 状態 | 出典 / 関連 |
|---|---|---|---|---|
| idb backend の subprocess 実行検証 | パーサはテスト済みだが、外部 CLI のコマンド面と JSON スキーマは「推測」。導入済み idb の実出力に対して確認・調整する | P0 | 📋 | [REPORT §5](../REPORT.md)、`bajutsu/drivers/idb.py` 冒頭注記、[architecture.md](architecture.md#implementation-status) |
| simctl 起動シーケンスの検証 | erase/boot/launch/openurl/io の順序と待ちを実機で確定（ベストエフォート実装中） | P0 | 📋 | `bajutsu/env.py` |
| `idb_companion` バージョン監視 | idb 自体のメンテ頻度・最新ランタイム互換に追従。CI でバージョンを固定/監視 | P1 | 💡 | [DESIGN §11](../DESIGN.md) |
| idb 要素ツリー正規化の精度 | `.searchable` 等 SwiftUI 標準要素のツリー表現が崩れないか実機で確認 | P1 | 💡 | [DESIGN §11](../DESIGN.md) |

## 2. プラットフォーム拡張（Android / Flutter）

現状はスコープを **iOS Simulator 限定**としている（[DESIGN §1](../DESIGN.md)）。driver / backend 抽象を活かして
マルチプラットフォーム化する大きな方向性。**コアのスコープ文（DESIGN §1・README）の更新を伴う戦略的判断**。

| 機能 | 概要 | 優先度 | 状態 | 出典 / 関連 |
|---|---|---|---|---|
| Android backend | Android エミュレータ向け driver。adb + UIAutomator 等で操作。セレクタは `resource-id` / `content-desc` を id ファーストに対応づけ | P2 | 💡 | [DESIGN §5](../DESIGN.md)、`bajutsu/drivers/` |
| Flutter 対応 | Flutter は独自レンダリングで OS の a11y ツリーに要素が出にくい。Flutter の semantics ツリー（`integration_test` / VM Service / Flutter Driver）経由の解決を検討 | P2 | 💡 | — |
| 抽象のクロスプラットフォーム化 | セレクタ解決・安定度順ラダー・証跡サブシステムを OS 横断で再利用できるか設計レビュー（プラットフォーム差は抽象側で吸収） | P2 | 💡 | [DESIGN §5](../DESIGN.md)、[architecture.md](architecture.md) |
| スコープ文の更新 | 「やること / やらないこと」とプロダクト説明を iOS 限定からマルチプラットフォームへ改訂 | P2 | 💡 | [DESIGN §1](../DESIGN.md)、[`../README.md`](../README.md) |

## 3. オーサリング体験（record / GUI エディタ）

AI 駆動の `record`（Tier 1）は実装済み（[recording.md](recording.md)）。ここでの狙いは **AI に依らない操作キャプチャ**と
**シナリオの可視編集**で、§6.5 のラウンドトリップ（記録 → 編集 → 再実行）を人にとって扱いやすくすること。

| 機能 | 概要 | 優先度 | 状態 | 出典 / 関連 |
|---|---|---|---|---|
| 操作キャプチャ record | Simulator 上の実操作（tap / type / swipe）を記録してシナリオ化（AI 非依存）。idb のイベント取得 or アクセシビリティイベント監視が要 | P2 | 💡 | [DESIGN §6.5](../DESIGN.md)、`bajutsu/record.py` |
| シナリオ GUI エディタ | シナリオ YAML / アサーション DSL を可視編集。スクショ上で要素ピッカー → セレクタ確定、doctor スコア連携 | P3 | 💡 | [scenarios.md](scenarios.md)、[selectors.md](selectors.md) |
| 既存 AI record との棲み分け | 「AI が探索して書く」と「人の操作を写経する」の役割分担と相互変換を明文化 | P3 | 💡 | [recording.md](recording.md) |

## 4. 統合・自動化（MCP 化）

| 機能 | 概要 | 優先度 | 状態 | 出典 / 関連 |
|---|---|---|---|---|
| MCP サーバ化 | `run` / `doctor` / `record` / `codegen` を MCP ツールとして公開し、Claude 等のエージェントから直接駆動。Tier 1（AI オーサリング）と相性が良い | P2 | 💡 | [cli.md](cli.md)、`bajutsu/agent.py` / `claude_agent.py` |
| 証跡を MCP リソースで返す | `manifest.json` / `report.html` 等の実行結果をエージェントが読めるリソースとして公開 | P2 | 💡 | [reporting.md](reporting.md) |

## 5. バックエンド拡張（iOS actuator）

| 機能 | 概要 | 優先度 | 状態 | 出典 / 関連 |
|---|---|---|---|---|
| XCUITest backend | idb に次ぐ 2 つ目の actuator。安定度順ラダーの上位として登録できるようにする（抽象は既に維持済み） | P2 | 💡 | [DESIGN §5 / §3](../DESIGN.md)、`bajutsu/backends.py` |
| マルチ backend 証跡フォールバック | 現状 actuator は単一。証跡取得だけ別 backend に逃がす能力差吸収（§9 で設計済み・未配線） | P2 | 💡 | [drivers.md](drivers.md)、[DESIGN §9](../DESIGN.md) |

## 6. 自己修復トリアージ（M4）

AI を「判定者」にせず、調査役に限定したまま回帰の保守コストを下げる。

| 機能 | 概要 | 優先度 | 状態 | 出典 / 関連 |
|---|---|---|---|---|
| AI triage（原因要約・修正提案） | 失敗証跡を AI が読み、原因要約・修正提案を出す（人間レビュー前提）。決定的な `trace` コマンド（run のテキストタイムライン）は**実装済み**・本項はその上の AI 層 | P2 | 🚧 | [DESIGN §3.1 / §12](../DESIGN.md)、`bajutsu/triage.py` |
| `update`（最小差分提案） | UI 変更で壊れたシナリオを全体再記録せず、最小差分で更新提案。手編集を保全 | P2 | 💡 | [DESIGN §6.5](../DESIGN.md) |
| 「テストを甘くする」防止策 | 自己修復が合否を緩める方向に働くリスクへの歯止め（必ず人間レビュー、差分の可視化） | P2 | 💡 | [DESIGN §11](../DESIGN.md) |

## 7. doctor / オンボーディング

> doctor の実行可能性ゲート（CLI 群 + 起動済み Simulator の有無チェック）は**実装済み**
> （[architecture.md](architecture.md#implementation-status)）。新しいオンボーディング系の候補が出たらここに追加する。

## 8. codegen 網羅性

| 機能 | 概要 | 優先度 | 状態 | 出典 / 関連 |
|---|---|---|---|---|
| 座標 swipe の生成 | 現状 `swipe { from, to }` は `// TODO` にフォールバック | P2 | 💡 | [codegen.md](codegen.md) |
| 未対応構文の縮小 | 未知セレクタ等が `// TODO` に落ちる範囲を減らす | P3 | 💡 | [codegen.md](codegen.md) |

## 9. その他・保留

| 機能 | 概要 | 優先度 | 状態 | 出典 / 関連 |
|---|---|---|---|---|
| `mockServer`（外部モック） | config スキーマのみ存在。宣言的な in-protocol `mocks`（実装済み）に置き換えられており、外部サーバ方式が本当に要るか要検討 | P3 | ❄️ | [architecture.md](architecture.md#implementation-status)、`config.py` `MockServer` |
| 証跡ルールの過剰マッチ対策 | capturePolicy の過剰マッチで成果物が肥大するのを防ぐ（`--explain` ドライラン・既定ポリシー軽量化） | P2 | 💡 | [DESIGN §11](../DESIGN.md) |

---

## 10. 競合調査（MagicPod / Autify）由来の候補

MagicPod・Autify は **AI 自己修復（self-healing）+ ノーコード + クラウド端末ファーム + ビジュアル系**が DNA。
両社の旗艦機能は「**実行中に AI がロケータ/タップ位置を自動補正する**」点だが、これは Bajutsu の核心
（[DESIGN §2](../DESIGN.md)：**AI を CI ゲートに入れない / 決定性ファースト**）と正面衝突する。
よって「決定的にそのまま取り込めるもの」と「**ゲート外（Tier 1 / triage）に限れば取り込めるもの**」を分けて評価した。

### 10.1 取り込む（決定的・思想に合致）

| 機能 | 概要 / Bajutsu での形 | 由来 | 優先度 | 状態 | 関連 |
|---|---|---|---|---|---|
| ビジュアル回帰アサーション | スクショをベースラインと差分比較する**新アサーション種別**。除外領域・per-device / per-locale ベースライン対応。AI ではなく決定的な機械チェックなので「機械アサーションのみで合否」に合致 | 両社 | P1 | 💡 | [DESIGN §6.4](../DESIGN.md)、[evidence.md](evidence.md) |
| パラメータ化シェアドステップ | `setup` プレリュード（引数なし・実装済み）を超え、**引数付きの再利用ステップ部品**を定義・呼び出し。ログイン等の共通手順を DRY 化 | MagicPod | P1 | 💡 | [DESIGN §6.5](../DESIGN.md)、`bajutsu/scenario.py` |
| データ駆動シナリオ | 1 シナリオを**データ表（CSV / inline）で複数回反復**実行。多言語・境界値テストに有効 | MagicPod | P2 | 💡 | [scenarios.md](scenarios.md) |
| シークレット変数 | 入力に使い、証跡では**自動マスク**される変数。既存 `redact`（証跡側・実装済み）を入力値まで拡張 | MagicPod | P2 | 💡 | [evidence.md](evidence.md)、`bajutsu/redaction.py` |
| シナリオ変数 + 軽い制御フロー | 値を capture して後続で再利用。条件分岐 / ループは**決定性を崩さない範囲**で慎重に（過度な分岐は避ける） | MagicPod | P2 | 💡 | [scenarios.md](scenarios.md) |
| タグ / ラベル + 選択実行 | シナリオにタグを付け、`--tag` 等でサブセット実行（include/exclude）。CI の段階実行に有効 | MagicPod | P2 | 💡 | [cli.md](cli.md) |
| デバイス制御プリミティブ拡張 | 位置情報 / タイムゾーン / クリップボード / 前面・背面遷移 / シェイク / push 通知 など（`rotate`/`swipe`/`pinch` は実装済み） | MagicPod | P2 | 💡 | [DESIGN §6.2](../DESIGN.md)、`bajutsu/scenario.py` |
| ユーティリティステップ | HTTP リクエスト発行 / OTP・2FA コード生成 / メール受信を API で検証。実アプリのログインフロー自動化に必要 | MagicPod | P3 | 💡 | [scenarios.md](scenarios.md) |
| WebView / ハイブリッド対応 | 現状はネイティブ a11y ツリー前提。WebView 内 DOM への橋渡し | MagicPod | P3 | 💡 | [drivers.md](drivers.md) |

### 10.2 ゲート外限定で取り込む（Tier 1 / triage のみ・CI ゲートには入れない）

| 機能 | 概要 / Bajutsu での形 | 由来 | 優先度 | 状態 | 関連 |
|---|---|---|---|---|---|
| 自律クロール探索（App Explorer 風） | AI が**自律的に画面遷移をクロールして画面マップを生成 + クラッシュ/到達不能を報告**。Tier 1 の `record` を強化。「AI = 探索者」に合致 | Autify VAX | P2 | 💡 | [recording.md](recording.md)、[DESIGN §3.1](../DESIGN.md) |
| 自己修復は「提案」に限定 | 両社は実行中に自動補正。Bajutsu は §6 の **triage が最小差分を提案 → 人間レビュー**に留める（自動適用しない＝「テストを甘くする」防止・[DESIGN §11](../DESIGN.md)） | 両社 | P2 | 💡 | [#6-自己修復トリアージm4](#6-自己修復トリアージm4) |
| AI アサーション | 自然言語の期待を AI が判定。**CI ゲートには絶対入れない**（決定性が崩れる）。record / triage の下書き支援に限る | MagicPod | P3 | ❄️ | [DESIGN §2 / §3.1](../DESIGN.md) |

### 10.3 取り込まない（既に充足 / スコープ外）

- **変更履歴・バージョン管理** — シナリオは YAML で git 管理されるため既に充足。
- **クラウド端末ファーム / 実機・クラウド実行** — iOS Simulator 限定の現スコープ外（[DESIGN §1](../DESIGN.md)）。マルチプラットフォームは別途 [§2](#2-プラットフォーム拡張android--flutter)。
- **ステップ毎スクショ / エラー時 UI ツリー / 端末ログ** — 証跡サブシステム（capturePolicy + `result:error` 安全網）で充足済み。
- **NL→テスト生成（Autopilot 相当）** — 既存 `record` + [§3](#3-オーサリング体験record--gui-エディタ) と重複。
- **スケジューリング / Slack / TestRail 連携** — CI・通知レイヤの領域。優先度低（必要なら別途）。
- **失敗テストの自動リトライ** — 決定性ファースト（固定 sleep 排除・条件待機）と緊張。flaky 隠蔽になり得るため、入れるなら quarantine 用途に限定して要検討。

---

## 未整理アイデア置き場

> 形になっていない思いつきはここへ。後で上の表に昇格させる。

-
