[English](README.md) · **日本語**

# Bajutsu roadmap / backlog

> [!IMPORTANT]
> **オープンな項目の担当者管理は、このファイルではなく GitHub Issues で行っています。** `状態` が
> `提案` または `実装中` の項目には、それぞれ対応する GitHub Issue があり、その Issue の
> **Assignee（担当者）** が、誰が担当しているかについての真実です（このリポジトリのどの欄も担当者を
> 追跡していません）。[`roadmap-tracking` ラベルの付いた Issue 一覧](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+is%3Aopen+label%3Aroadmap-tracking)
> から確認できます。`no:assignee` で未着手のバックログを、`assignee:<user>` で担当者ごとの分担を
> 絞り込めます。詳しくは
> [BE-0109](implemented/BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues-ja.md)
> を参照してください。

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
`NNNN` は**ゼロ詰め 4 桁・単調増加**の ID です。各項目ディレクトリは `状態` の値ごとに分かれた
**4 つ**のフォルダのいずれかに置きます（BE-0078）。`実装済み` は `roadmaps/implemented/`、
`実装中` は `roadmaps/in-progress/`、`提案` は `roadmaps/proposals/`、`提案（保留）` は
`roadmaps/deferred/` です。`状態` が唯一の真実であり、項目が置かれるフォルダとインデックスでの
バケットの両方を決めるので、両者が食い違うことはありません。

ロードマップ項目を追加するとき:

1. **次の ID を採番** = 既存の最大 `BE-NNNN` + 1（**4 つすべて**のフォルダの項目を数えます）。次で確認します:
   ```bash
   ls -d roadmaps/{implemented,in-progress,proposals,deferred}/BE-*/ | sort | tail -1
   ```
   番号の再利用や飛ばし、当て推量は禁止です。**ただし通常は未定のままにします。** 項目を
   `BE-XXXX-<slug>`（リテラルのプレースホルダ）と名付け、採番を CI に任せます。項目はレビューと
   マージを通じて `BE-XXXX` のまま保たれ、[`roadmap-id`](../.github/workflows/roadmap-id.yml)
   ワークフローが、**PR のマージ後に `main` 上で**
   [`scripts/allocate_roadmap_ids.py`](../scripts/allocate_roadmap_ids.py) を実行し、空いている
   次の ID をマージ順に採番して、リネームを `main` へコミットします（BE-0089）。`ideation` skill は
   この方式を使います。`BE-NNNN` の並びがマージ順で連続し（却下された PR は番号を消費しません）、
   進行中の 2 つのブランチが同じ番号を取り合うことも防げます。したがって BE 作成 PR は
   **`[BE-NNNN]` のタイトル接頭辞を持ちません**。本当の番号はマージ後まで分からないからです。
2. **項目ディレクトリと両言語のファイルを作成**します。提案なら `roadmaps/proposals/` の下に、同じ PR で実装も出荷する場合は `状態: 実装済み` にして `roadmaps/implemented/` の下に置きます（新規項目は原則まず提案ですが、コードが一緒に入るなら最初から実装済みにします）。`roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>.md`（英語）と
   `roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>-ja.md`（日本語・同一 ID と slug）です。**下の
   インデックス表は手で編集しません。** 各項目自身のメタデータから生成されます。`make roadmap-index`（または
   `python scripts/build_roadmap_index.py`）を実行して、**両方**のインデックスページの `<!-- GENERATED:* -->`
   マーカー間の表を再生成してください。項目の `状態`（バケット）+ `トピック` がセクションを決めるので、既存
   セクションの項目なら表の手編集は不要です。コミット済みインデックスがズレるとゲート（`tests/test_roadmap_index.py`、
   `make test` が実行）が落ちます。あるトピックがあるバケットに初めて入るときは、ページにマーカー付きセクションを
   追加します（不足している領域は生成スクリプトが名指しします）。
3. **ID は不変**です。既存項目を採番し直してはいけません。状態が変わっても、完了しても、表から削除しても、
   一度割り当てた BE ID は、その項目を永遠に指します。

各ファイルは **Swift-Evolution の proposal フォーマット**に従います。メタデータブロックの後に
`## はじめに` / `## 動機` / `## 詳細設計` / `## 検討した代替案` / `## 参考` を置きます（埋められる
範囲だけ記入し、不明は `TBD`）。メタデータは `| Field | Value |` 形式の囲み表で、
`| 項目 | 値 |` の見出し行（英語側は `| Field | Value |`）で始まり、
`<!-- BE-METADATA -->` … `<!-- /BE-METADATA -->` の中に `提案`・`提案者`・`状態`・`トピック`
（出荷後は `実装 PR`、該当時は末尾に `由来`）を並べます。英語側は `Proposal`・`Author`・
`Status`・`Topic` です。**提案者は GitHub のアカウント名で明記**します
（`| 提案者 | [@handle](https://github.com/handle) |`）。最初にその項目を作成した人（AI 支援で
書き起こした場合は、それを主導してコミットした人）のアカウントです。`tests/test_roadmap_format.py`
がこの形を検査します。
**状態**がフォルダとインデックスのバケットを決めます。`実装済み` / `実装中` / `提案` / `提案（保留）` です。
項目の状態が変わったら（着手した、あるいは出荷した）、`状態` を更新し、ディレクトリを対応するフォルダへ
（同じ ID と slug のまま）**移動**して、インデックスを再生成してください。誤って置かれた項目は
`make roadmap-promote` が整えます。

日本語版（`*-ja.md`）は、`docs/ja/` と同じく**敬体（ですます調）**で書きます。常体（だ・である調）は
使いません。これは [`japanese-tech-writing`](../.claude/skills/japanese-tech-writing/) 規範の一部で、
英語版の逐語訳ではなく、自然な敬体の日本語として読めるようにします。

---

## 実装済み

出荷済み — `main` に着地したものです。プロジェクトの**実装の記録**です。

### マイルストーン（M1–M4）

粗い粒度のデリバリ・マイルストーン（M1–M4）です。4 つとも出荷済みで、これらが分解された細かい機能は下のトピック群にあります。

<!-- GENERATED:implemented-milestones -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0001](implemented/BE-0001-m1-deterministic-runner/BE-0001-m1-deterministic-runner-ja.md) | 決定的ランナー（M1） | 実装済み |
| [BE-0002](implemented/BE-0002-m2-ai-loop-and-evidence/BE-0002-m2-ai-loop-and-evidence-ja.md) | AI オーサリングループと証跡（M2） | 実装済み |
| [BE-0003](implemented/BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci-ja.md) | codegen・トレース・ネットワーク・CI（M3） | 実装済み |
| [BE-0004](implemented/BE-0004-m4-self-healing-triage/BE-0004-m4-self-healing-triage-ja.md) | 自己修復トリアージ（M4） | 実装済み |
<!-- /GENERATED:implemented-milestones -->

### プラットフォーム拡張（着手済みスライス）

マルチプラットフォーム化のうち出荷済みのスライスです。**プラットフォーム対応の backend レジストリ**により、`--backend` / `backend:` は bare な actuator に加えてプラットフォームトークン（`ios` / `android` / `web` / `fake`）を受け付けます。加えて設定の `apps`→`targets` 改名と Web crawl です。まだ構築中のスライスは[実装中](#プラットフォーム拡張着手済みスライス-1)に、各プラットフォームの三点セットの残りは[提案](#プラットフォーム拡張android--web--flutter)にあります。

<!-- GENERATED:implemented-platform-landed -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0041](implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) | Web (Playwright) backend | 実装済み |
| [BE-0042](implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md) | プラットフォーム対応の backend レジストリと選択 | 実装済み |
| [BE-0054](implemented/BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md) | Web backend の完成（リッチな capability と並列実行） | 実装済み |
| [BE-0057](implemented/BE-0057-rename-apps-to-targets/BE-0057-rename-apps-to-targets-ja.md) | 設定の `apps` キーを `targets` に改名 | 実装済み |
| [BE-0066](implemented/BE-0066-web-crawl/BE-0066-web-crawl-ja.md) | Web crawl（Playwright backend） | 実装済み |
<!-- /GENERATED:implemented-platform-landed -->

### プラットフォーム拡張（Android / Web / Flutter）

<!-- GENERATED:implemented-platform -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0009](implemented/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md) | 抽象のクロスプラットフォーム化 | 実装済み |
| [BE-0010](implemented/BE-0010-update-scope-statement/BE-0010-update-scope-statement-ja.md) | スコープ文の更新 | 実装済み |
| [BE-0076](implemented/BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines-ja.md) | ブラウザエンジンの選択とクロスブラウザ互換マトリクス（web backend） | 実装済み |
| [BE-0082](implemented/BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md) | run の前に capability をプリフライト検査する | 実装済み |
<!-- /GENERATED:implemented-platform -->

### バックエンド拡張（iOS actuator）

<!-- GENERATED:implemented-backend -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0020](implemented/BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback-ja.md) | マルチ backend 証跡フォールバック | 実装済み |
<!-- /GENERATED:implemented-backend -->

### doctor / オンボーディング

<!-- GENERATED:implemented-doctor -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0024](implemented/BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md) | doctor / オンボーディング | 実装済み |
<!-- /GENERATED:implemented-doctor -->

### オーサリング体験（record / GUI エディタ）

AI 駆動の `record`（Tier 1）は実装済みです（[recording.md](../docs/ja/recording.md)）。これらの項目は記録 → 編集 → 再実行のサイクルを人が扱いやすくします。ローカル Web UI ランチャ `bajutsu serve` はその最初のステップです。

<!-- GENERATED:implemented-authoring -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0011](implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md) | ローカル Web UI（`bajutsu serve`） | 実装済み |
| [BE-0012](implemented/BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md) | 操作キャプチャ record | 実装済み |
| [BE-0013](implemented/BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md) | シナリオ GUI エディタ | 実装済み |
| [BE-0014](implemented/BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md) | 既存 AI record との棲み分け | 実装済み |
| [BE-0044](implemented/BE-0044-scenario-provenance/BE-0044-scenario-provenance-ja.md) | シナリオの来歴（`from:` — ステップ ↔ 自然言語の対応） | 実装済み |
| [BE-0060](implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) | run の実行レポートの zip ダウンロードとエクスポート | 実装済み |
| [BE-0068](implemented/BE-0068-regenerable-reports/BE-0068-regenerable-reports-ja.md) | 再生成できるレポート（保存済み run データから描画する） | 実装済み |
| [BE-0072](implemented/BE-0072-responsive-web-ui/BE-0072-responsive-web-ui-ja.md) | serve Web UI のレスポンシブ対応（小さい画面・タッチ操作） | 実装済み |
<!-- /GENERATED:implemented-authoring -->

### 自己修復トリアージ（M4）

AI を「判定者」にせず調査役に限定したまま、回帰の保守コストを下げます。

<!-- GENERATED:implemented-self-healing -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0021](implemented/BE-0021-ai-triage/BE-0021-ai-triage-ja.md) | AI triage（原因要約・修正提案） | 実装済み |
| [BE-0022](implemented/BE-0022-update-structured-fixes/BE-0022-update-structured-fixes-ja.md) | `update`（最小差分提案＝構造化 fix の適用） | 実装済み |
| [BE-0023](implemented/BE-0023-self-healing-guards/BE-0023-self-healing-guards-ja.md) | 「テストを甘くする」防止策 | 実装済み |
<!-- /GENERATED:implemented-self-healing -->

### 競合調査（MagicPod / Autify）由来の候補

MagicPod と Autify は **AI 自己修復（self-healing）+ ノーコード + クラウド端末ファーム + ビジュアル系**を主軸とするツールです。両社の旗艦機能は「**実行中に AI がロケータ/タップ位置を自動補正する**」ことですが、これは Bajutsu の中核原則（[DESIGN §2](../DESIGN.md)：**AI を CI ゲートに入れない / 決定性ファースト**）と直接矛盾します。そのため、決定的に取り込める機能とゲート外限定で取り込める機能を分けて評価しました。

<!-- GENERATED:implemented-competitive -->
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
| [BE-0037](implemented/BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md) | WebView / ハイブリッド対応 | 実装済み | MagicPod |
| [BE-0039](implemented/BE-0039-self-healing-propose-optin/BE-0039-self-healing-propose-optin-ja.md) | 自己修復は「提案＋opt-in 適用」に限定 | 実装済み | 両社 |
| [BE-0046](implemented/BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md) | OTP・メールの側方チャネルステップ | 実装済み | MagicPod |
<!-- /GENERATED:implemented-competitive -->

### 競合調査（Maestro）由来の候補

Bajutsu の「契約としての決定性」という立場を、Maestro の flakiness 許容と対比させ、機械検証可能な具体機能へ落とし込みます。

<!-- GENERATED:implemented-competitive-maestro -->
| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0047](implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) | AI データ主権（プロバイダ非依存・秘匿化された AI 経路） | 実装済み | Maestro |
| [BE-0048](implemented/BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions-ja.md) | 振る舞い／プロトコルアサーション | 実装済み | Maestro |
| [BE-0049](implemented/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) | 決定性／フレーキネス監査 | 実装済み | Maestro |
| [BE-0050](implemented/BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map-ja.md) | E2E カバレッジマップ | 実装済み | Maestro |
| [BE-0097](implemented/BE-0097-crawl-ai-data-sovereignty/BE-0097-crawl-ai-data-sovereignty-ja.md) | crawl ガイドと serve が起動する AI 経路の AI データ主権 | 実装済み |  |
<!-- /GENERATED:implemented-competitive-maestro -->

### 統合と自動化（MCP）

<!-- GENERATED:implemented-mcp -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0017](implemented/BE-0017-mcp-server/BE-0017-mcp-server-ja.md) | MCP サーバ化 | 実装済み |
| [BE-0018](implemented/BE-0018-evidence-as-mcp-resources/BE-0018-evidence-as-mcp-resources-ja.md) | 証跡を MCP リソースで返す | 実装済み |
<!-- /GENERATED:implemented-mcp -->

### 開発基盤（コントリビュータ体験）

このリポジトリで並行作業する多数のセッションの摩擦を減らします。マージコンフリクトを設計の臭いとして扱い、独立した変更が互いに素なファイルだけに触れるようファイル流動を見直します。

<!-- GENERATED:implemented-dev-infra -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0043](implemented/BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md) | コンフリクトに強いファイル流動（索引の生成・ファイル分割・git 衛生） | 実装済み |
| [BE-0061](implemented/BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening-ja.md) | 衝突しない BE ID 採番（原子的な予約と自動修復） | 実装済み |
| [BE-0065](implemented/BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference-ja.md) | docstring の規範と API リファレンス生成 | 実装済み |
| [BE-0067](implemented/BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md) | コード品質ゲートの強化（CI の忠実性、セキュリティ lint、サプライチェーン） | 実装済み |
| [BE-0069](implemented/BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md) | コントリビュータ向けガードレールの実行可能化（手順をコマンドに） | 実装済み |
| [BE-0074](implemented/BE-0074-be-template-standardization/BE-0074-be-template-standardization-ja.md) | BE 項目テンプレートの標準化（EN / JA） | 実装済み |
| [BE-0078](implemented/BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders-ja.md) | 状態ごとのロードマップフォルダ（提案 / 保留 / 実装中 / 実装済み） | 実装済み |
| [BE-0089](implemented/BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation-ja.md) | マージ後に main で BE ID を採番する | 実装済み |
| [BE-0092](implemented/BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction-ja.md) | クロール調整役をクラスに切り出す | 実装済み |
| [BE-0093](implemented/BE-0093-public-docs-site/BE-0093-public-docs-site-ja.md) | 公式サイトとドキュメントポータルの公開（GitHub Pages） | 実装済み |
| [BE-0094](implemented/BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard-ja.md) | GitHub Pages で公開するロードマップ状況ダッシュボード | 実装済み |
| [BE-0096](implemented/BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity-ja.md) | 項目昇格で docs のロードマップリンクが腐るのを防ぐ | 実装済み |
| [BE-0100](implemented/BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template-ja.md) | BE テンプレートへの進捗管理と項目間の関連の追加 | 実装済み |
| [BE-0103](implemented/BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering-ja.md) | 開発タスクごとにモデルと推論エフォートを適正化する | 実装済み |
| [BE-0109](implemented/BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues-ja.md) | GitHub Issues as the ownership tracker for open roadmap items | 実装済み |
| [BE-0113](implemented/BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md) | DESIGN.md を現状の実装に合わせる | 実装済み |
<!-- /GENERATED:implemented-dev-infra -->

### Dogfood フィクスチャ（デモアプリ）

コマンドを端から端まで行使するための目的特化のテスト対象です。showcase 群は次世代の dogfood 対象で、同じアプリを UIKit と SwiftUI で書き、各々をアクセシビリティ有/無の変種で出します（2 コードベースで 4 プロダクト）。これにより、`run`（id ベース）、`record`（id 無しフォールバック）、`doctor`（Ready vs Blocked）、そして `crawl` が、1 つの豊かで代表的な対象を持ちます。画面ごとの契約は [`demos/showcase/SPEC.md`](../demos/showcase/SPEC.md) にあります。

<!-- GENERATED:implemented-dogfood -->
| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0045](implemented/BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps-ja.md) | Dogfood ショーケースアプリ群（UIKit × SwiftUI、アクセシビリティ対比） | 実装済み | Dogfooding |
| [BE-0079](implemented/BE-0079-consolidate-demos-on-showcase/BE-0079-consolidate-demos-on-showcase-ja.md) | デモ／dogfood 用アプリを showcase 群へ統合する | 実装済み | Dogfooding |
<!-- /GENERATED:implemented-dogfood -->

### Dogfood フィクスチャ（Web UI）

Bajutsu 自身の `serve` Web UI も Web アプリなので、Web（Playwright）backend で駆動します。これは Web UI の決定的なリグレッション網（Tier 2）で、iOS の [BE-0045](implemented/BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps-ja.md) ショーケースに対応する Web 側のフィクスチャです。

<!-- GENERATED:implemented-dogfood-web-ui -->
| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0058](implemented/BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui-ja.md) | serve Web UI の Dogfood（web backend のリグレッション網） | 実装済み | Dogfooding |
| [BE-0059](implemented/BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md) | run のためにターゲットサーバを起動する（`launchServer`） | 実装済み | Dogfooding |
<!-- /GENERATED:implemented-dogfood-web-ui -->

### AI プロバイダ設定

Tier-1 の AI 経路（`record` ／ `triage` ／ `--dismiss-alerts` ／ `crawl`）は、差し替え可能なプロバイダ経由で Claude を呼びます。このトピックは、そのプロバイダの選択と設定を扱います。例えば、直接の Anthropic API の代替としての Amazon Bedrock（AWS の認証情報で認証）です。決定的な `run` ／ CI ゲートはモデルを呼ばず、影響を受けません。

<!-- GENERATED:implemented-ai-provider -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0053](implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider-ja.md) | 差し替え可能な AI プロバイダとしての Amazon Bedrock | 実装済み |
| [BE-0101](implemented/BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config-ja.md) | Claude を使う機能と使わない機能の分離と、AIを使わない経路のゼロ設定実行 | 実装済み |
<!-- /GENERATED:implemented-ai-provider -->

### Web UI のホスティング（クラウド / セルフホスト）

`bajutsu serve` を loopback の外で公開するための取り組み。既存の stdlib サーバを公開しても安全にするハードニング（認証・入力検証）は出荷済み。完全なホスティング構成は下記の提案に残っています。

<!-- GENERATED:implemented-hosting -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0051](implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) | ホスティングのための serve ハードニング（認証・入力検証） | 実装済み |
| [BE-0055](implemented/BE-0055-operational-logging/BE-0055-operational-logging-ja.md) | ホスト型 serve の運用ログ | 実装済み |
| [BE-0090](implemented/BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md) | アップロードされたバンドル config からのコマンド実行を統制し、サンドボックス化する | 実装済み |
| [BE-0106](implemented/BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md) | 完了後連携 worker モデル（Redis 依存の排除） | 実装済み |
<!-- /GENERATED:implemented-hosting -->

### config の取得元

プロジェクトの config とシナリオがどこから来るか。Git リポジトリ + ref は、CI とセルフホストの `serve` にとって、不変のコミットで実体化される、いま動く取得元です。

<!-- GENERATED:implemented-config-sourcing -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0063](implemented/BE-0063-git-config-source/BE-0063-git-config-source-ja.md) | Git リポジトリ + ref から config（とシナリオ一式）を読み込む | 実装済み |
| [BE-0073](implemented/BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) | config・シナリオ・アプリバイナリを zip でまとめてアップロードし Web UI から実行する | 実装済み |
<!-- /GENERATED:implemented-config-sourcing -->

### codegen 網羅性

通過したシナリオを、出力先フレームワークの流儀に沿ったネイティブテストに変換する取り組み。元の XCUITest に加えて web（Playwright）向けが着地しました。

<!-- GENERATED:implemented-codegen -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0025](implemented/BE-0025-coordinate-swipe-generation/BE-0025-coordinate-swipe-generation-ja.md) | 座標 swipe の生成 | 実装済み |
| [BE-0026](implemented/BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) | 未対応構文の縮小 | 実装済み |
| [BE-0062](implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md) | Playwright codegen ターゲット | 実装済み |
| [BE-0083](implemented/BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md) | codegen の emitter を共通のシナリオ走査へ統一する | 実装済み |
| [BE-0085](implemented/BE-0085-shrink-web-codegen-syntax/BE-0085-shrink-web-codegen-syntax-ja.md) | web（Playwright）codegen の未対応構文の縮小 | 実装済み |
<!-- /GENERATED:implemented-codegen -->

### クロール性能 / スケールアウト

自律クロールを複数台のデバイスで走らせ、全画面マップの構築にかかる実時間を大幅に短縮する取り組み。

<!-- GENERATED:implemented-crawl -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0064](implemented/BE-0064-parallel-crawl/BE-0064-parallel-crawl-ja.md) | 複数シミュレータでの並列クロール | 実装済み |
| [BE-0077](implemented/BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl-ja.md) | 複数ブラウザでの並列 Web クロール | 実装済み |
<!-- /GENERATED:implemented-crawl -->

### 実機検証（M1 クローズアウト）

<!-- GENERATED:implemented-on-device -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0005](implemented/BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring-ja.md) | `idb_companion` バージョン監視 | 実装済み |
| [BE-0006](implemented/BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization-ja.md) | idb 要素ツリー正規化の精度 | 実装済み |
| [BE-0087](implemented/BE-0087-idb-action-settle/BE-0087-idb-action-settle-ja.md) | idb アクションのタイミング堅牢化（操作前の settle） | 実装済み |
| [BE-0088](implemented/BE-0088-overlap-simulator-boot/BE-0088-overlap-simulator-boot-ja.md) | Simulator の boot をビルドと並行させる | 実装済み |
<!-- /GENERATED:implemented-on-device -->

### 外部サービスとの連携

<!-- GENERATED:implemented-external-integration -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0099](implemented/BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md) | 実行結果の Webhook 通知 | 実装済み |
<!-- /GENERATED:implemented-external-integration -->

### その他と保留

<!-- GENERATED:implemented-misc -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0028](implemented/BE-0028-evidence-rule-overmatch-guard/BE-0028-evidence-rule-overmatch-guard-ja.md) | 証跡ルールの過剰マッチ対策 | 実装済み |
<!-- /GENERATED:implemented-misc -->

## 実装中

可決済みで、現在構築中です。PR が進行中か、まもなく出ます。

### プラットフォーム拡張（着手済みスライス）

Web（Playwright）backend とその完成（リッチな capability、並列実行）。能力モデルの豊かな端を、既存の Linux ゲートの上で行使します。

<!-- GENERATED:in-progress-platform-landed -->

<!-- /GENERATED:in-progress-platform-landed -->

### プラットフォーム拡張（Android / Web / Flutter）

<!-- GENERATED:in-progress-platform -->

<!-- /GENERATED:in-progress-platform -->

### 競合調査（MagicPod / Autify）由来の候補

<!-- GENERATED:in-progress-competitive -->
| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0038](in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md) | 自律クロール探索（App Explorer 風） | 実装中 | Autify VAX |
| [BE-0052](in-progress/BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md) | デバイス状態プリミティブ: タイムゾーン・クリップボード・シェイク | 実装中 | MagicPod |
<!-- /GENERATED:in-progress-competitive -->

### 競合調査（Maestro）由来の候補

<!-- GENERATED:in-progress-competitive-maestro -->

<!-- /GENERATED:in-progress-competitive-maestro -->

### バックエンド拡張（iOS actuator）

<!-- GENERATED:in-progress-backend -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0019](in-progress/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) | XCUITest backend | 実装中 |
<!-- /GENERATED:in-progress-backend -->

### Dogfood フィクスチャ（デモアプリ）

デモ／dogfood 用アプリを showcase 群へ統合します。旧 `sample` / `demo` / `sample2` フィクスチャと同等まで showcase を引き上げ（codegen → XCUITest、ビジュアルリグレッション、ジェスチャ標的、証跡ツアー）、デモと実機 CI を showcase に張り替え、旧 3 アプリを退役させて、showcase を唯一の iOS フィクスチャにします。

<!-- GENERATED:in-progress-dogfood -->

<!-- /GENERATED:in-progress-dogfood -->

### Web UI のホスティング（クラウド / セルフホスト）

<!-- GENERATED:in-progress-hosting -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0015](in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) | Web UI の公開ホスティング | 実装中 |
| [BE-0016](in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) | Web UI のセルフホスティング | 実装中 |
| [BE-0110](in-progress/BE-0110-evidence-store-uri/BE-0110-evidence-store-uri-ja.md) | URI 指定によるオブジェクトストレージへの証跡アップロード | 実装中 |
<!-- /GENERATED:in-progress-hosting -->

### codegen 網羅性

<!-- GENERATED:in-progress-codegen -->

<!-- /GENERATED:in-progress-codegen -->

### 実機検証（M1 クローズアウト）

<!-- GENERATED:in-progress-on-device -->

<!-- /GENERATED:in-progress-on-device -->

### オーサリング体験（record / GUI エディタ）

<!-- GENERATED:in-progress-authoring -->

<!-- /GENERATED:in-progress-authoring -->

## 提案

検討中で、まだ決定していません。着手したら *実装中* に、出荷したら *実装済み* に昇格してください。

### 実機検証（M1 クローズアウト）

決定的コアは FakeDriver で end-to-end に通っており、**idb backend の subprocess 実行（`describe-all` パース、frame-center tap/text/swipe）と simctl 起動シーケンスは実機で検証済み**です。残るのは継続メンテ系の監視のみです。

<!-- GENERATED:proposals-on-device -->

<!-- /GENERATED:proposals-on-device -->

### プラットフォーム拡張（Android / Web / Flutter）

現状はスコープを **iOS Simulator 限定**としています（[DESIGN §1](../DESIGN.md)）。このセクションは driver / backend 抽象を活用したマルチプラットフォーム化の方向性を扱います。全体像は [multi-platform.md](../docs/ja/multi-platform.md) にあり、**プラットフォーム別の具体的な設計**は以下の各項目にあります。[BE-0009](proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md) が共有の抽象、続いて Web（最初に推奨）、Android、Flutter です。

<!-- GENERATED:proposals-platform -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0007](proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md) | Android backend | 提案 |
| [BE-0008](proposals/BE-0008-flutter-support/BE-0008-flutter-support-ja.md) | Flutter 対応 | 提案 |
| [BE-0114](proposals/BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) | backend 非依存の挙動を検査する driver conformance suite | 提案 |
| [BE-0118](proposals/BE-0118-wait-for-contract-unification/BE-0118-wait-for-contract-unification-ja.md) | ドライバ間で wait_for のポーリング契約を統一する | 提案 |
| [BE-0126](proposals/BE-0126-per-platform-effective-config/BE-0126-per-platform-effective-config-ja.md) | Effective をプラットフォームごとの設定に分割する | 提案 |
| [BE-0128](proposals/BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md) | デバイス制御ステップをケイパビリティで preflight ゲートする | 提案 |
| [BE-0141](proposals/BE-0141-backend-lifecycle-protocol/BE-0141-backend-lifecycle-protocol-ja.md) | backend のライフサイクルを型システムに載せる | 提案 |
<!-- /GENERATED:proposals-platform -->

### オーサリング体験（record / GUI エディタ）

<!-- GENERATED:proposals-authoring -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0095](proposals/BE-0095-interactive-crawl-graph/BE-0095-interactive-crawl-graph-ja.md) | クロールグラフのインタラクティブ操作（ノードのドラッグと再整列） | 提案 |
| [BE-0098](proposals/BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface-ja.md) | serve の統合オーサリングサーフェス | 提案 |
| [BE-0102](proposals/BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard-ja.md) | 実行結果の集計ダッシュボード | 提案 |
<!-- /GENERATED:proposals-authoring -->

### serve Web UI への CLI 機能の取り込み

<!-- GENERATED:proposals-serve-cli-features -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0137](proposals/BE-0137-serve-codegen/BE-0137-serve-codegen-ja.md) | serve Web UI からネイティブテストコードを生成する | 提案 |
| [BE-0138](proposals/BE-0138-serve-lint/BE-0138-serve-lint-ja.md) | serve エディタでのシナリオ検証（lint / schema） | 提案 |
<!-- /GENERATED:proposals-serve-cli-features -->

### Dogfood フィクスチャ（デモアプリ）

<!-- GENERATED:proposals-dogfood -->
| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0107](proposals/BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut-ja.md) | showcase の各タブへ、起動時の近道ではなくナビゲーションで到達する | 提案 | Dogfooding |
<!-- /GENERATED:proposals-dogfood -->

### AI プロバイダ設定

<!-- GENERATED:proposals-ai-provider -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0104](proposals/BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md) | ベンダー中立な AI バックエンドインターフェース | 提案 |
| [BE-0111](proposals/BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency-ja.md) | AI SDK を extra へ降ろし、決定的ゲートを AI 非依存でインストールできるようにする | 提案 |
<!-- /GENERATED:proposals-ai-provider -->

### Web UI のホスティング（クラウド / セルフホスト）

ローカルの `bajutsu serve` ランチャを共有サービスにします。ランナーは iOS Simulator を駆動するため Mac が必要で、コントロールプレーン（Linux）⇄ macOS ワーカーの分離を強います。[BE-0015](in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) はマネージドなマルチテナント公開スタックを選定し、[BE-0016](in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) は自前の Mac での運用を扱います。

<!-- GENERATED:proposals-hosting -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0108](proposals/BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md) | ホスティング時は config の取得元をアップロードと Git だけに絞る | 提案 |
| [BE-0127](proposals/BE-0127-split-serve-operations-module/BE-0127-split-serve-operations-module-ja.md) | serve operations の巨大モジュールを分割する | 提案 |
| [BE-0129](proposals/BE-0129-serve-scope-boundary/BE-0129-serve-scope-boundary-ja.md) | serve のスコープを画定し、ホスト固有の関心事を共有 config から締め出す | 提案 |
<!-- /GENERATED:proposals-hosting -->

### セキュリティ強化

決定論コアが触れない縁（edge）を塞ぐ取り組みです。`serve` の HTTP 面、秘密情報がキャプチャ／record／成果物を通じてどう流れるか、ドライバの引数の扱い、CI のサプライチェーンが対象です。ここで扱う項目は、共有マシン上でも安全に実行でき、信頼できない取得元から渡されたシナリオを扱っても安全を保てることを、prime directive を弱めずに実現します。

<!-- GENERATED:proposals-security -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0115](proposals/BE-0115-inprocess-collector-auth/BE-0115-inprocess-collector-auth-ja.md) | iOS 用インプロセスネットワークコレクタを認証する | 提案 |
| [BE-0116](proposals/BE-0116-udid-argument-validation/BE-0116-udid-argument-validation-ja.md) | UDID の検証を引数インジェクションに対して厳格化する | 提案 |
| [BE-0120](proposals/BE-0120-recorded-scenario-secret-tokenization/BE-0120-recorded-scenario-secret-tokenization-ja.md) | 記録された scenario の YAML でシークレットをトークン化する | 提案 |
| [BE-0121](proposals/BE-0121-serve-csrf-host-allowlist/BE-0121-serve-csrf-host-allowlist-ja.md) | serve の CSRF・Host allowlist 防御を無条件化する | 提案 |
| [BE-0123](proposals/BE-0123-composite-action-input-indirection/BE-0123-composite-action-input-indirection-ja.md) | composite action の入力を env 経由の間接参照にする | 提案 |
| [BE-0124](proposals/BE-0124-config-source-owner-repo-validation/BE-0124-config-source-owner-repo-validation-ja.md) | config-source の owner・repo 検証を厳格化する | 提案 |
| [BE-0125](proposals/BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction-ja.md) | claude-code オーサリングエージェントのツールを制限する | 提案 |
| [BE-0130](proposals/BE-0130-default-network-secret-redaction/BE-0130-default-network-secret-redaction-ja.md) | ネットワークの機密ヘッダーと Cookie を既定で redact する | 提案 |
| [BE-0131](proposals/BE-0131-run-artifact-permissions/BE-0131-run-artifact-permissions-ja.md) | 実行証跡ファイルのパーミッションを制限する | 提案 |
| [BE-0133](proposals/BE-0133-pin-actionlint-installer/BE-0133-pin-actionlint-installer-ja.md) | actionlint インストーラを SHA で固定する | 提案 |
| [BE-0136](proposals/BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md) | serve の秘密情報ストアを書き込み専用にする | 提案 |
<!-- /GENERATED:proposals-security -->

### config の取得元

`bajutsu` が config とシナリオ一式をどこから読むか。現在はローカルパスだが、ここで扱う項目は **ある ref を指す Git リポジトリ**（`github:owner/repo@ref:path`）の指定や zip でのアップロードを可能にし、ホスト型やセルフホストの `serve`、あるいは CI のランナーが、チームのテストリポジトリを直接取得できるようにする。

<!-- GENERATED:proposals-config-sourcing -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0119](proposals/BE-0119-scenario-schema-versioning/BE-0119-scenario-schema-versioning-ja.md) | バージョン間の読み込みに備えてシナリオスキーマにバージョンを持たせる | 提案 |
<!-- /GENERATED:proposals-config-sourcing -->

### codegen 網羅性

通過したシナリオを出力先フレームワークの流儀のネイティブテストに変換する取り組み。ここで扱う項目は、エミッタが `// TODO` に落とす構文の範囲を減らします。

<!-- GENERATED:proposals-codegen -->

<!-- /GENERATED:proposals-codegen -->

### クロール性能 / スケールアウト

自律クロールを高速に保ち、成長に合わせてコードを簡潔に保つ取り組みです。

<!-- GENERATED:proposals-crawl -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0132](proposals/BE-0132-dedupe-crawl-screenshot-helpers/BE-0132-dedupe-crawl-screenshot-helpers-ja.md) | クロールのスクリーンショットヘルパーを重複排除する | 提案 |
<!-- /GENERATED:proposals-crawl -->

### バックエンド拡張（iOS actuator）

<!-- GENERATED:proposals-backend -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0105](proposals/BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md) | XCUITest の要素取得を単一スナップショット化する | 提案 |
<!-- /GENERATED:proposals-backend -->

### doctor / オンボーディング

<!-- GENERATED:proposals-doctor -->

<!-- /GENERATED:proposals-doctor -->

### 開発基盤（コントリビュータ体験）

<!-- GENERATED:proposals-dev-infra -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0112](proposals/BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement-ja.md) | コア・契約・周辺のレイヤ境界をゲートで検査する | 提案 |
| [BE-0117](proposals/BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet-ja.md) | CLI コマンド層の残りをテストしてから、カバレッジフロアをラチェットする | 提案 |
| [BE-0122](proposals/BE-0122-workflow-name-legibility/BE-0122-workflow-name-legibility-ja.md) | Legible GitHub Actions workflow and job names | 提案 |
| [BE-0134](proposals/BE-0134-serve-cli-flag-mirror-drift/BE-0134-serve-cli-flag-mirror-drift-ja.md) | serve と CLI のフラグ二重管理による drift をなくす | 提案 |
| [BE-0135](proposals/BE-0135-module-naming-debt/BE-0135-module-naming-debt-ja.md) | トップレベルモジュールの命名の負債を解消する | 提案 |
| [BE-0139](proposals/BE-0139-roadmap-dashboard-issue-links/BE-0139-roadmap-dashboard-issue-links-ja.md) | ロードマップのダッシュボードと項目ファイルからトラッキング Issue へリンクする | 提案 |
| [BE-0140](proposals/BE-0140-dedupe-claude-client-init/BE-0140-dedupe-claude-client-init-ja.md) | Claude クライアント初期化の重複をなくす | 提案 |
<!-- /GENERATED:proposals-dev-infra -->

### 外部サービスとの連携

実行の結果を、チームがすでに使っているサービスへ送り出します。いずれも判定後の決定的な送信路であり、runner がすでに算出した判定を運ぶだけで、LLM の判定を運ぶことはありません。配信に失敗しても実行の結果は動きません。

<!-- GENERATED:proposals-external-integration -->

<!-- /GENERATED:proposals-external-integration -->

### 競合調査（MagicPod / Autify）由来の候補

<!-- GENERATED:proposals-competitive -->

<!-- /GENERATED:proposals-competitive -->

### 競合調査（Maestro）由来の候補

Maestro（mobile.dev）はオープンソースのクロスプラットフォーム UI E2E ツールで、その方向性は、幅、ホストされた端末クラウド、そして *既定では optional ／助言的* な AI 機能に傾いています。これらの項目は、Bajutsu の逆の立場（契約としての決定性、UI より下の層での検証、ユーザーの管理下に厳密に置かれた AI）を尖らせます。

<!-- GENERATED:proposals-competitive-maestro -->

<!-- /GENERATED:proposals-competitive-maestro -->

## 保留

棚上げした提案です。検討の上で今は見送ったもので、判断とその理由を記録に残すためにここに置いています（削除はしません）。`状態` を `提案` に戻せば保留解除です。

### 競合調査（MagicPod / Autify）由来の候補

<!-- GENERATED:deferred-competitive -->
| ID | 項目 | 状態 | 由来 |
|---|---|---|---|
| [BE-0040](deferred/BE-0040-ai-assertions/BE-0040-ai-assertions-ja.md) | AI アサーション | 保留 | MagicPod |
<!-- /GENERATED:deferred-competitive -->

### Web UI のホスティング（クラウド / セルフホスト）

<!-- GENERATED:deferred-hosting -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0070](deferred/BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md) | 制御プレーンと worker をまたいだ実行中アーティファクトのライブ表示 | 保留 |
<!-- /GENERATED:deferred-hosting -->

### その他と保留

<!-- GENERATED:deferred-misc -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0027](deferred/BE-0027-mock-server-external/BE-0027-mock-server-external-ja.md) | `mockServer`（外部モック） | 保留 |
<!-- /GENERATED:deferred-misc -->

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
