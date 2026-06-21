[English](README.md) · **日本語**

# Bajutsu roadmap / backlog

> 今後実装したい機能を管理するドキュメントです。各項目は 1 ファイル（1 項目 = 1 BE ID）。
> まとまっていない思いつきはまず [未整理アイデア](#未整理アイデア) に追加し、内容が固まったら
> 採番済みの項目へ昇格させます。
>
> - **現状（実装済み / 未配線）の正確な一覧**は
>   [architecture.md#実装状況](../docs/ja/architecture.md#実装状況) が真実です。ここは「これから」を扱います。
> - 設計の背景（なぜ）は [`DESIGN.md`](../DESIGN.md) にあります。
> - **全体の戦略的な方向性**は [vision.md](../docs/ja/vision.md) にあります。

## 凡例

**優先度**：`P0`（次にやる） / `P1`（やる） / `P2`（あると良い） / `P3`（アイデア段階）
**状態**：💡 アイデア / 📋 計画済み / 🚧 進行中 / ❄️ 保留 / ✅ 完了

## ロードマップ項目の追加：BE ID（エージェントは厳守）

すべてのロードマップ項目は `BE-NNNN-<slug>/` ディレクトリに、英語版 `BE-NNNN-<slug>.md` と
日本語版 `BE-NNNN-<slug>-ja.md`（ID と slug は同一）を入れます。**BE** は *Bajutsu Evolution*、
`NNNN` は**ゼロ詰め 4 桁・単調増加**の ID です。各項目ディレクトリは進捗に応じて 2 つの
フォルダのどちらかに置きます。出荷済み（`状態: 実装済み`）は `roadmaps/implemented/`、
それ以外の進行中のものはすべて `roadmaps/proposals/` です。

ロードマップ項目を追加するとき:

1. **次の ID を採番** = 既存の最大 `BE-NNNN` + 1（**両方**のフォルダの項目を数えます）。次で確認します:
   ```bash
   ls -d roadmaps/{implemented,proposals}/BE-*/ | sort | tail -1
   ```
   番号の再利用や飛ばし、当て推量は禁止です。**または未定のままにする方法もあります。** 項目を
   `BE-XXXX-<slug>`（リテラルのプレースホルダ）と名付け、採番を CI に任せます。
   [`roadmap-id`](../.github/workflows/roadmap-id.yml) ワークフローが `roadmaps/**`
   に触れる PR ごとに [`scripts/allocate_roadmap_ids.py`](../scripts/allocate_roadmap_ids.py)
   を実行し、空いている次の ID を採番してブランチへリネームを push し返します。
   `ideation` skill はこの方式を使い、進行中の 2 つのブランチが同じ番号を
   取り合うのを防ぎます。
2. **項目ディレクトリと両言語のファイルを作成**します。提案なら `roadmaps/proposals/` の下に、同じ PR で実装も出荷する場合は `状態: 実装済み` にして `roadmaps/implemented/` の下に置きます（新規項目は原則まず提案ですが、コードが一緒に入るなら最初から実装済みにします）。`roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>.md`（英語）と
   `roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>-ja.md`（日本語・同一 ID と slug）です。**下の
   インデックス表は手で編集しません。** 各項目自身のメタデータから生成されます。`make roadmap-index`（または
   `python scripts/build_roadmap_index.py`）を実行して、**両方**のインデックスページの `<!-- GENERATED:* -->`
   マーカー間の表を再生成してください。項目の `Track` + `Topic` がセクションを決めるので、既存トピックの項目なら
   表の手編集は不要です。コミット済みインデックスがズレるとゲート（`tests/test_roadmap_index.py`、`make test` が実行）
   が落ちます。まったく新しいトピックの場合は、マーカー付きセクションとスクリプトの `Section` エントリも追加します。
3. **ID は不変**です。既存項目を採番し直してはいけません。状態が変わっても、完了しても、表から削除しても、
   一度割り当てた BE ID は、その項目を永遠に指します。

各ファイルは **Swift-Evolution の proposal フォーマット**に従います。メタデータブロック（`* 提案`・
`* Author`・`* 状態`・`* トラック`・`* トピック` …）の後に `## はじめに` / `## 動機` / `## 詳細設計` /
`## 検討した代替案` / `## 参考` を置きます（埋められる範囲だけ記入し、不明は `TBD`）。**Author は
GitHub のアカウント名で明記**します（`* Author: [@handle](https://github.com/handle)`）。最初に
その項目を作成した人（AI 支援で書き起こした場合は、それを主導してコミットした人）のアカウントです。
**状態**がトラックを決めます。`実装済み`・`可決・実装中` は **可決済み**（意思決定・実装の記録）に、
`提案`・`提案（保留）` は **提案**（検討中）に並びます。項目が出荷されたら状態を `実装済み` にし、
ディレクトリを `roadmaps/proposals/` から `roadmaps/implemented/` へ（同じ ID と slug のまま）
**移動**して、インデックスを再生成してください。

---

## 可決済み

下した意思決定です。すでに実装済み、または可決済みで今後実装します。プロジェクトの**意思決定・実装の記録**です。

### マイルストーン（M1–M4）

粗い粒度のデリバリ・マイルストーン（M1–M4）であり、プロジェクトの**意思決定・実装の記録**です。4 つとも可決・出荷済みで、これらが分解された細かい機能は下のトピック群にあります。

<!-- GENERATED:accepted-milestones -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0001](implemented/BE-0001-m1-deterministic-runner/BE-0001-m1-deterministic-runner-ja.md) | 決定的ランナー（M1） | 実装済み |
| [BE-0002](implemented/BE-0002-m2-ai-loop-and-evidence/BE-0002-m2-ai-loop-and-evidence-ja.md) | AI オーサリングループと証跡（M2） | 実装済み |
| [BE-0003](implemented/BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci-ja.md) | codegen・トレース・ネットワーク・CI（M3） | 実装済み |
| [BE-0004](implemented/BE-0004-m4-self-healing-triage/BE-0004-m4-self-healing-triage-ja.md) | 自己修復トリアージ（M4） | 実装済み |
<!-- /GENERATED:accepted-milestones -->

### プラットフォーム拡張（着手済みスライス）

マルチプラットフォーム化の最初のスライスは着手済みです。**プラットフォーム対応の backend レジストリ**により、`--backend` / `backend:` は bare な actuator に加えてプラットフォームトークン（`ios` / `android` / `web` / `fake`）を受け付け、実装済みかつ利用可能な最初の actuator へ展開します。各プラットフォームの三点セットの残り（プラットフォーム別 environment manager + actuator driver）は、提案の[プラットフォーム拡張](#プラットフォーム拡張android--web--flutter)で扱います。

<!-- GENERATED:accepted-platform-landed -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0041](proposals/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) | Web (Playwright) backend | 実装中 |
| [BE-0042](implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md) | プラットフォーム対応の backend レジストリと選択 | 実装済み |
<!-- /GENERATED:accepted-platform-landed -->

### オーサリング体験（record / GUI エディタ）

AI 駆動の `record`（Tier 1）は実装済みです（[recording.md](../docs/ja/recording.md)）。このセクションの目的は **AI に依らない操作キャプチャ**と**シナリオの視覚的編集**で、記録 → 編集 → 再実行のサイクルを人が扱いやすくすることです。ローカル Web UI ランチャ `bajutsu serve` はその最初のステップです。

<!-- GENERATED:accepted-authoring -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0011](implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md) | ローカル Web UI（`bajutsu serve`） | 実装済み |
<!-- /GENERATED:accepted-authoring -->

### 自己修復トリアージ（M4）

AI を「判定者」にせず調査役に限定したまま、回帰の保守コストを下げます。

<!-- GENERATED:accepted-self-healing -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0021](implemented/BE-0021-ai-triage/BE-0021-ai-triage-ja.md) | AI triage（原因要約・修正提案） | 実装済み |
| [BE-0022](implemented/BE-0022-update-structured-fixes/BE-0022-update-structured-fixes-ja.md) | `update`（最小差分提案＝構造化 fix の適用） | 実装済み |
| [BE-0023](implemented/BE-0023-self-healing-guards/BE-0023-self-healing-guards-ja.md) | 「テストを甘くする」防止策 | 実装済み |
<!-- /GENERATED:accepted-self-healing -->

### 競合調査（MagicPod / Autify）由来の候補

MagicPod と Autify は **AI 自己修復（self-healing）+ ノーコード + クラウド端末ファーム + ビジュアル系**を主軸とするツールです。両社の旗艦機能は「**実行中に AI がロケータ/タップ位置を自動補正する**」ことですが、これは Bajutsu の中核原則（[DESIGN §2](../DESIGN.md)：**AI を CI ゲートに入れない / 決定性ファースト**）と直接矛盾します。そのため、決定的に取り込める機能とゲート外限定で取り込める機能を分けて評価しました。

<!-- GENERATED:accepted-competitive -->
| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0029](implemented/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md) | ビジュアル回帰アサーション | 実装済み | 両社 |
| [BE-0030](implemented/BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md) | パラメータ化シェアドステップ | 実装済み | MagicPod |
| [BE-0031](implemented/BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios-ja.md) | データ駆動シナリオ | 実装済み | MagicPod |
| [BE-0032](implemented/BE-0032-secret-variables/BE-0032-secret-variables-ja.md) | シークレット変数 | 実装済み | MagicPod |
| [BE-0033](implemented/BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md) | シナリオ変数 + 軽い制御フロー | 実装済み | MagicPod |
| [BE-0034](implemented/BE-0034-tags-selective-runs/BE-0034-tags-selective-runs-ja.md) | タグ / ラベル + 選択実行 | 実装済み | MagicPod |
| [BE-0035](implemented/BE-0035-device-control-primitives/BE-0035-device-control-primitives-ja.md) | デバイス制御ステップ（background・ステータスバー上書き） | 実装済み | MagicPod |
| [BE-0036](implemented/BE-0036-utility-steps/BE-0036-utility-steps-ja.md) | HTTP ユーティリティステップ | 実装済み | MagicPod |
| [BE-0038](proposals/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md) | 自律クロール探索（App Explorer 風） | 実装中 | Autify VAX |
| [BE-0039](implemented/BE-0039-self-healing-propose-optin/BE-0039-self-healing-propose-optin-ja.md) | 自己修復は「提案＋opt-in 適用」に限定 | 実装済み | 両社 |
<!-- /GENERATED:accepted-competitive -->

### 統合と自動化（MCP）

<!-- GENERATED:accepted-mcp -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0017](implemented/BE-0017-mcp-server/BE-0017-mcp-server-ja.md) | MCP サーバ化 | 実装済み |
| [BE-0018](implemented/BE-0018-evidence-as-mcp-resources/BE-0018-evidence-as-mcp-resources-ja.md) | 証跡を MCP リソースで返す | 実装済み |
<!-- /GENERATED:accepted-mcp -->

### 開発基盤（コントリビュータ体験）

このリポジトリで並行作業する多数のセッションの摩擦を減らします。マージコンフリクトを設計の臭いとして扱い、独立した変更が互いに素なファイルだけに触れるようファイル流動を見直します。

<!-- GENERATED:accepted-dev-infra -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0043](implemented/BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md) | コンフリクトに強いファイル流動（索引の生成・ファイル分割・git 衛生） | 実装済み |
| [BE-0067](implemented/BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md) | コード品質ゲートの強化（CI の忠実性、セキュリティ lint、サプライチェーン） | 実装済み |
<!-- /GENERATED:accepted-dev-infra -->

### Dogfood フィクスチャ（デモアプリ）

コマンドを端から端まで行使するための目的特化のテスト対象です。showcase 群は次世代の dogfood 対象で、同じアプリを UIKit と SwiftUI で書き、各々をアクセシビリティ有/無の変種で出します（2 コードベースで 4 プロダクト）。これにより、`run`（id ベース）、`record`（id 無しフォールバック）、`doctor`（Ready vs Blocked）、そして来たる `crawl`（[BE-0038](proposals/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）が、1 つの豊かで代表的な対象を持ちます。画面ごとの契約は [`demos/showcase/SPEC.md`](../demos/showcase/SPEC.md) にあります。

<!-- GENERATED:accepted-dogfood -->
| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0045](implemented/BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps-ja.md) | Dogfood ショーケースアプリ群（UIKit × SwiftUI、アクセシビリティ対比） | 実装済み | Dogfooding |
<!-- /GENERATED:accepted-dogfood -->

### Dogfood フィクスチャ（Web UI）

Bajutsu 自身の `serve` Web UI も Web アプリなので、Web（Playwright）backend で駆動します。これは [BE-0041](proposals/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) の上に立つ、Web UI の決定的なリグレッション網（Tier 2）で、iOS の [BE-0045](implemented/BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps-ja.md) ショーケースに対応する Web 側のフィクスチャです。

<!-- GENERATED:accepted-dogfood-web-ui -->
| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0058](implemented/BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui-ja.md) | serve Web UI の Dogfood（web backend のリグレッション網） | 実装済み | Dogfooding |
| [BE-0059](implemented/BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md) | run のためにターゲットサーバを起動する（`launchServer`） | 実装済み | Dogfooding |
<!-- /GENERATED:accepted-dogfood-web-ui -->

### AI プロバイダ設定

Tier-1 の AI 経路（`record` ／ `triage` ／ `--dismiss-alerts` ／ `crawl`）は、差し替え可能な
プロバイダ経由で Claude を呼びます。このトピックは、そのプロバイダの選択と設定を扱います。
例えば、直接の Anthropic API の代替としての Amazon Bedrock（AWS の認証情報で認証）です。決定的な
`run` ／ CI ゲートはモデルを呼ばず、影響を受けません。この軸は `backend`（UI の actuator）とは別物です。

<!-- GENERATED:accepted-ai-provider -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0053](implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider-ja.md) | 差し替え可能な AI プロバイダとしての Amazon Bedrock | 実装済み |
<!-- /GENERATED:accepted-ai-provider -->

### Web UI のホスティング（クラウド / セルフホスト）

`bajutsu serve` を loopback の外で公開するための取り組み。既存の stdlib サーバを公開しても安全にする
ハードニング（認証・入力検証）は出荷済み。完全なホスティング構成（クラウドのコントロールプレーン、
セルフホストのマルチテナント）は下記の提案に残っています。

<!-- GENERATED:accepted-hosting -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0051](implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) | ホスティングのための serve ハードニング（認証・入力検証） | 実装済み |
<!-- /GENERATED:accepted-hosting -->

## 提案

検討中で、まだ決定していません。決定したら *可決済み* に昇格してください。

### 実機検証（M1 クローズアウト）

決定的コアは FakeDriver で end-to-end に通っており、**idb backend の subprocess 実行（`describe-all` パース、frame-center tap/text/swipe）と simctl 起動シーケンスは実機（iPhone 17 Pro / 最新 iOS）で検証済み**です。残るのは継続メンテ系の監視のみです。

<!-- GENERATED:proposals-on-device -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0005](proposals/BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring-ja.md) | `idb_companion` バージョン監視 | 提案 |
| [BE-0006](proposals/BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization-ja.md) | idb 要素ツリー正規化の精度 | 提案 |
<!-- /GENERATED:proposals-on-device -->

### プラットフォーム拡張（Android / Web / Flutter）

現状はスコープを **iOS Simulator 限定**としています（[DESIGN §1](../DESIGN.md)）。このセクションは driver / backend 抽象を活用したマルチプラットフォーム化の方向性を扱います。コアのスコープ文の更新を伴う戦略的な判断です。全体像（大枠）は [multi-platform.md](../docs/ja/multi-platform.md) にあり、**プラットフォーム別の具体的な設計**は以下の各項目にあります。[BE-0009](proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md) が共有の抽象、続いて Web（既存の Linux ゲートで動くため最初に推奨）、Android、Flutter です。最初のスライス（プラットフォーム対応の backend レジストリ）はすでに着手済みです（[BE-0042](implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)、可決済み）。

<!-- GENERATED:proposals-platform -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0007](proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md) | Android backend | 提案 |
| [BE-0008](proposals/BE-0008-flutter-support/BE-0008-flutter-support-ja.md) | Flutter 対応 | 提案 |
| [BE-0009](proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md) | 抽象のクロスプラットフォーム化 | 提案 |
| [BE-0010](proposals/BE-0010-update-scope-statement/BE-0010-update-scope-statement-ja.md) | スコープ文の更新 | 提案 |
| [BE-0054](proposals/BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md) | Web backend の完成（リッチな capability と並列実行） | 提案 |
| [BE-0057](proposals/BE-0057-rename-apps-to-targets/BE-0057-rename-apps-to-targets-ja.md) | 設定の `apps` キーを `targets` に改名 | 提案 |
| [BE-0066](proposals/BE-0066-web-crawl/BE-0066-web-crawl-ja.md) | Web crawl（Playwright backend） | 提案 |
<!-- /GENERATED:proposals-platform -->

### オーサリング体験（record / GUI エディタ）

<!-- GENERATED:proposals-authoring -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0012](proposals/BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md) | 操作キャプチャ record | 提案 |
| [BE-0013](proposals/BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md) | シナリオ GUI エディタ | 提案 |
| [BE-0014](proposals/BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md) | 既存 AI record との棲み分け | 提案 |
| [BE-0044](proposals/BE-0044-scenario-provenance/BE-0044-scenario-provenance-ja.md) | シナリオの来歴（`from:` — ステップ ↔ 自然言語の対応） | 提案 |
| [BE-0060](proposals/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) | run の実行レポートの zip ダウンロードとエクスポート | 提案 |
<!-- /GENERATED:proposals-authoring -->

### Web UI のホスティング（クラウド / セルフホスト）

ローカルの `bajutsu serve` ランチャを共有サービスにします。ランナーは iOS Simulator を駆動するため Mac が必要で、コントロールプレーン（Linux）⇄ macOS ワーカーの分離を強います。[BE-0015](proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) はマネージドなマルチテナント公開スタックを選定し、[BE-0016](proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) は自前の Mac での運用を扱います。後者は、既存 `serve` で今日から使える単一 Mac 構成と、完全セルフホストのマルチテナント構成です。

<!-- GENERATED:proposals-hosting -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0015](proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) | Web UI の公開ホスティング | 提案 |
| [BE-0016](proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) | Web UI のセルフホスティング | 提案 |
| [BE-0055](proposals/BE-0055-operational-logging/BE-0055-operational-logging-ja.md) | ホスト型 serve の運用ログ | 提案 |
<!-- /GENERATED:proposals-hosting -->

### 統合と自動化（MCP 化）

<!-- GENERATED:proposals-mcp -->
| ID | 項目 | 状態 |
|---|---|---|
<!-- /GENERATED:proposals-mcp -->

### バックエンド拡張（iOS actuator）

<!-- GENERATED:proposals-backend -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0019](proposals/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) | XCUITest backend | 提案 |
| [BE-0020](proposals/BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback-ja.md) | マルチ backend 証跡フォールバック | 提案 |
<!-- /GENERATED:proposals-backend -->

### doctor / オンボーディング

<!-- GENERATED:proposals-doctor -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0024](proposals/BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md) | doctor / オンボーディング | 提案 |
<!-- /GENERATED:proposals-doctor -->

### codegen 網羅性

<!-- GENERATED:proposals-codegen -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0025](proposals/BE-0025-coordinate-swipe-generation/BE-0025-coordinate-swipe-generation-ja.md) | 座標 swipe の生成 | 提案 |
| [BE-0026](proposals/BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) | 未対応構文の縮小 | 提案 |
| [BE-0062](proposals/BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md) | Playwright codegen ターゲット | 提案 |
<!-- /GENERATED:proposals-codegen -->

### クロール性能 / スケールアウト

<!-- GENERATED:proposals-crawl -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0064](proposals/BE-0064-parallel-crawl/BE-0064-parallel-crawl-ja.md) | 複数シミュレータでの並列クロール | 提案 |
<!-- /GENERATED:proposals-crawl -->

### その他と保留

<!-- GENERATED:proposals-misc -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0027](proposals/BE-0027-mock-server-external/BE-0027-mock-server-external-ja.md) | `mockServer`（外部モック） | 保留 |
| [BE-0028](proposals/BE-0028-evidence-rule-overmatch-guard/BE-0028-evidence-rule-overmatch-guard-ja.md) | 証跡ルールの過剰マッチ対策 | 提案 |
<!-- /GENERATED:proposals-misc -->

### 競合調査（MagicPod / Autify）由来の候補

<!-- GENERATED:proposals-competitive -->
| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0037](proposals/BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md) | WebView / ハイブリッド対応 | 提案 | MagicPod |
| [BE-0040](proposals/BE-0040-ai-assertions/BE-0040-ai-assertions-ja.md) | AI アサーション | 保留 | MagicPod |
| [BE-0046](proposals/BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md) | OTP・メールの側方チャネルステップ | 提案 | MagicPod |
| [BE-0052](proposals/BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md) | デバイス状態プリミティブ: タイムゾーン・クリップボード・シェイク | 提案 | MagicPod |
<!-- /GENERATED:proposals-competitive -->

### 競合調査（Maestro）由来の候補

Maestro（mobile.dev）はオープンソースのクロスプラットフォーム UI E2E ツールで、2026 年の方向性は、
幅、ホストされた端末クラウド（Robin）、そして *既定では optional ／助言的* でベンダー管理クラウドを
経由する AI 機能に傾いています。これらの項目は、Bajutsu の逆の立場（契約としての決定性、UI より下の
層での検証、ユーザーの管理下に厳密に置かれた AI）を、UI 層・計装なしの競合には容易に追随できない
具体的な機能へと尖らせます。

<!-- GENERATED:proposals-competitive-maestro -->
| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0047](proposals/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) | AI データ主権（プロバイダ非依存・秘匿化された AI 経路） | 提案 | Maestro |
| [BE-0048](proposals/BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions-ja.md) | 振る舞い／プロトコルアサーション | 提案 | Maestro |
| [BE-0049](proposals/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) | 決定性／フレーキネス監査 | 提案 | Maestro |
| [BE-0050](proposals/BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map-ja.md) | E2E カバレッジマップ | 提案 | Maestro |
<!-- /GENERATED:proposals-competitive-maestro -->

## 取り込まない（既に充足 / スコープ外）

- **変更履歴とバージョン管理**：シナリオは YAML で git 管理されるため既に充足しています。
- **クラウド端末ファーム、実機やクラウドでの実行**：iOS Simulator 限定の現スコープ外です（[DESIGN §1](../DESIGN.md)）。マルチプラットフォームは別途、提案として管理しています（*プラットフォーム拡張* の項目）。
- **ステップ毎スクショ、エラー時 UI ツリー、端末ログ**：証跡サブシステム（capturePolicy + `result:error` 安全網）で充足済みです。
- **NL→テスト生成（Autopilot 相当）**：既存 `record` と *オーサリング体験* の項目に重複します。
- **スケジューリング、Slack / TestRail 連携**：CI・通知レイヤの領域です。優先度は低く、必要なら別途対応します。
- **失敗テストの自動リトライ**：決定性ファースト（固定 sleep 排除、条件待機）と緊張関係にあります。flaky を隠蔽する可能性があるため、入れるなら quarantine 用途に限定して検討が必要です。

---

## 未整理アイデア

> まとまっていない思いつきはここへ追加してください。後で採番済みの BE 項目に昇格させます。

-
