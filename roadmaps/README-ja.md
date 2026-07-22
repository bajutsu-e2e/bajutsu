[English](README.md) · **日本語**

# Bajutsu roadmap / backlog

> [!IMPORTANT]
> **オープンな項目の担当者管理は、このファイルではなく GitHub Issues で行っています。** `状態` が
> `提案` または `実装中` の項目には、それぞれ対応する GitHub Issue があり、その Issue の
> **Assignee（担当者）** が、誰が担当しているかについての真実です（このリポジトリのどの欄も担当者を
> 追跡していません）。[`roadmap-tracking` ラベルの付いた Issue 一覧](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+is%3Aopen+label%3Aroadmap-tracking)
> から確認できます。`no:assignee` で未着手のバックログを、`assignee:<user>` で担当者ごとの分担を
> 絞り込めます。詳しくは
> [BE-0109](BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues-ja.md)
> を参照してください。

> 今後実装したい機能を管理するドキュメントです。各項目は 1 ファイル（1 項目 = 1 BE ID）。
> まとまっていない思いつきはまず [未整理アイデア](#未整理アイデア) に追加し、内容が固まったら
> 採番済みの項目へ昇格させます。
>
> - **現状**は、このページでは扱いません。文章によるまとめは
>   [architecture.md#実装状況](../docs/ja/architecture.md#実装状況) にあります。出荷済みの項目を
>   トピックごとに一覧できるのは、後述する[実装済み](#実装済み)からリンクしている
>   [ロードマップダッシュボード](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html)です。
>   このページが扱うのは、まだ終わっていないもの（提案・実装中・保留）です。
> - 設計の背景（なぜ）は [`DESIGN.md`](../DESIGN.md) にあります。
> - **全体の戦略的な方向性**は [vision.md](../docs/ja/vision.md) にあります。

## ロードマップ項目の追加：BE ID

すべてのロードマップ項目は `roadmaps/` の下に置きます。完全な手順（ディレクトリ構成、ID の採番、
両言語ファイル、書式）は、唯一の拠り所である
[`docs/ai-development.md`](../docs/ai-development.md#roadmap-items-be-ids-strict) にあります。

**下のインデックス表は手で編集しません。** 各項目自身のメタデータから生成されるので、
`make roadmap-index` で再生成してください。項目に `Status: Implemented` を設定すると、その行は
このページの表に移るのではなく、ページから完全に外れます。以降はダッシュボードが引き継ぎます。

---

## 実装済み

`main` に着地した、出荷済みの項目です。トピックごとに整理され、進捗バーも付いた一覧は
[ロードマップダッシュボード](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html)にあります。
このページでは個別には一覧しません。

## 実装中

可決済みで、現在構築中です。PR が進行中か、まもなく出ます。

### プラットフォーム対応（iOS / Android / Web / Flutter）

<!-- GENERATED:in-progress-platform -->

<!-- /GENERATED:in-progress-platform -->

### デバイスクラウド実行

<!-- GENERATED:in-progress-device-cloud -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0288](BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build-ja.md) | バッチ経路向け iOS デバイス署名ビルド | 実装中 |
<!-- /GENERATED:in-progress-device-cloud -->

### 検証とカバレッジ

<!-- GENERATED:in-progress-verification -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0282](BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md) | ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する | 実装中 |
| [BE-0285](BE-0285-scenario-feature-real-backend-coverage/BE-0285-scenario-feature-real-backend-coverage-ja.md) | シナリオ作成機能の実バックエンドカバレッジを検証する | 実装中 |
<!-- /GENERATED:in-progress-verification -->

### AI プロバイダ設定

<!-- GENERATED:in-progress-ai-provider -->

<!-- /GENERATED:in-progress-ai-provider -->

### Web UI のホスティング（クラウド / セルフホスト）

<!-- GENERATED:in-progress-hosting -->

<!-- /GENERATED:in-progress-hosting -->

### コードベース品質・技術的負債

<!-- GENERATED:in-progress-quality-debt -->

<!-- /GENERATED:in-progress-quality-debt -->

### オーサリング体験（record / GUI エディタ）

<!-- GENERATED:in-progress-authoring -->

<!-- /GENERATED:in-progress-authoring -->

## 提案

検討中で、まだ決定していません。着手したら *実装中* に、出荷したら *実装済み* に昇格してください。

### プラットフォーム対応（iOS / Android / Web / Flutter）

<!-- GENERATED:proposals-platform -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0008](BE-0008-flutter-support/BE-0008-flutter-support-ja.md) | Flutter 対応 | 提案 |
| [BE-0289](BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve-ja.md) | 操作対象への参照が古くなっても、失敗として扱う前に XCUITest のしくみで指定し直す | 提案 |
| [BE-0290](BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md) | XCUITest を iOS のデフォルトバックエンドにし、idb を撤去する | 提案 |
| [BE-0292](BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner-ja.md) | XCUITest ランナーを同梱して testRunner を省略可能にする | 提案 |
<!-- /GENERATED:proposals-platform -->

### ドライバとバックエンドのアーキテクチャ

<!-- GENERATED:proposals-driver-architecture -->

<!-- /GENERATED:proposals-driver-architecture -->

### デバイスクラウド実行

ローカルの Simulator やエミュレータ、ブラウザではなく、ホスト型のデバイスファーム上でシナリオを実行します。共通のプロバイダ抽象の背後に置き、決定的コアのローカル優先の既定とは別の、オプトインの実行ターゲットとして提供します。

<!-- GENERATED:proposals-device-cloud -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0237](BE-0237-firebase-device-streaming-adapter/BE-0237-firebase-device-streaming-adapter-ja.md) | Firebase Test Lab / Device Streaming adapter | 提案 |
<!-- /GENERATED:proposals-device-cloud -->

### シナリオ記述機能

<!-- GENERATED:proposals-scenario-authoring -->

<!-- /GENERATED:proposals-scenario-authoring -->

### 検証とカバレッジ

<!-- GENERATED:proposals-verification -->

<!-- /GENERATED:proposals-verification -->

### オーサリング体験（record / GUI エディタ）

<!-- GENERATED:proposals-authoring -->

<!-- /GENERATED:proposals-authoring -->

### codegen 網羅性

<!-- GENERATED:proposals-codegen -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0293](BE-0293-codegen-playwright-real-compile/BE-0293-codegen-playwright-real-compile-ja.md) | Playwright（TypeScript）codegen ターゲットの実コンパイル検証 | 提案 |
| [BE-0294](BE-0294-codegen-uiautomator-real-compile/BE-0294-codegen-uiautomator-real-compile-ja.md) | UI Automator（Kotlin）codegen ターゲットの実コンパイル検証 | 提案 |
<!-- /GENERATED:proposals-codegen -->

### serve Web UI への CLI 機能の取り込み

<!-- GENERATED:proposals-serve-cli-features -->

<!-- /GENERATED:proposals-serve-cli-features -->

### Web UI のホスティング（クラウド / セルフホスト）

<!-- GENERATED:proposals-hosting -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0167](BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out-ja.md) | ロードバランサ配下での制御プレーンのスケールアウト | 提案 |
| [BE-0168](BE-0168-self-host-high-availability/BE-0168-self-host-high-availability-ja.md) | セルフホストの高可用性と単一障害点の堅牢化 | 提案 |
| [BE-0170](BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch-ja.md) | 組織間で公平な重み付きジョブ分配 | 提案 |
| [BE-0244](BE-0244-deploy-hosted-web-ui-service/BE-0244-deploy-hosted-web-ui-service-ja.md) | ホスト版 Web UI サービスのデプロイ | 提案 |
<!-- /GENERATED:proposals-hosting -->

### config の取得元

<!-- GENERATED:proposals-config-sourcing -->

<!-- /GENERATED:proposals-config-sourcing -->

### セキュリティ強化

<!-- GENERATED:proposals-security -->

<!-- /GENERATED:proposals-security -->

### 開発基盤（コントリビュータ体験）

<!-- GENERATED:proposals-developer-experience -->

<!-- /GENERATED:proposals-developer-experience -->

### コードベース品質・技術的負債

<!-- GENERATED:proposals-quality-debt -->

<!-- /GENERATED:proposals-quality-debt -->

## 保留

棚上げした提案です。検討の上で今は見送ったもので、判断とその理由を記録に残すためにここに置いています（削除はしません）。`状態` を `提案` に戻せば保留解除です。

### シナリオ記述機能

<!-- GENERATED:deferred-scenario-authoring -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0157](BE-0157-shake-device-primitive/BE-0157-shake-device-primitive-ja.md) | シェイクのデバイスプリミティブ | 保留 |
| [BE-0158](BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive-ja.md) | タイムゾーンのデバイスプリミティブ | 保留 |
<!-- /GENERATED:deferred-scenario-authoring -->

### 検証とカバレッジ

<!-- GENERATED:deferred-verification -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0040](BE-0040-ai-assertions/BE-0040-ai-assertions-ja.md) | AI アサーション | 保留 |
<!-- /GENERATED:deferred-verification -->

### Web UI のホスティング（クラウド / セルフホスト）

<!-- GENERATED:deferred-hosting -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0070](BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md) | 制御プレーンと worker をまたいだ実行中アーティファクトのライブ表示 | 保留 |
<!-- /GENERATED:deferred-hosting -->

### セキュリティ強化

<!-- GENERATED:deferred-security -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0154](BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha-ja.md) | roadmap-promote をベース SHA から実行する | 保留 |
<!-- /GENERATED:deferred-security -->

### その他、保留

<!-- GENERATED:deferred-misc -->
| ID | 項目 | 状態 |
|---|---|---|
| [BE-0027](BE-0027-mock-server-external/BE-0027-mock-server-external-ja.md) | `mockServer`（外部モック） | 保留 |
<!-- /GENERATED:deferred-misc -->

## 取り込まない（既に充足 / スコープ外）

- **変更履歴とバージョン管理**：シナリオは YAML で git 管理されるため既に充足しています。
- **既定としてのクラウド端末ファームや実機での実行**：決定的コアはローカル優先で CI に載せやすい backend（Simulator、ヘッドレスブラウザ、エミュレータ）を対象とし、実機やクラウド端末を既定とはしません（[DESIGN §1](../DESIGN.md)）。ホスト型のデバイスクラウド実行は既定ではありませんが、一律のスコープ外でもなく、*デバイスクラウド実行* の項目でオプトインの提案として管理しています。マルチプラットフォームも同様に *プラットフォーム対応* の項目にあります。
- **ステップ毎スクショ、エラー時 UI ツリー、端末ログ**：証跡サブシステム（capturePolicy + `result:error` 安全網）で充足済みです。
- **NL→テスト生成（Autopilot 相当）**：既存 `record` と *オーサリング体験* の項目に重複します。
- **スケジューリング、Slack / TestRail 連携**：CI・通知レイヤの領域です。優先度は低く、必要なら別途対応します。
- **失敗テストの自動リトライ**：決定性ファースト（固定 sleep 排除、条件待機）と緊張関係にあります。flaky を隠蔽する可能性があるため、入れるなら quarantine 用途に限定して検討が必要です。

---

## 未整理アイデア

> まとまっていない思いつきはここへ追加してください。後で採番済みの BE 項目に昇格させます。

-
