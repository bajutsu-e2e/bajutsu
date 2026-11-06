[English](README.md) · **日本語**

# Bajutsu roadmap / backlog

> [!IMPORTANT]
> **オープンな項目の担当者管理は、このファイルではなく GitHub Issues で行っています。** `状態` が
> `承認済み` または `実装中` の項目には、それぞれ対応する GitHub Issue があります。その Issue の
> **Assignee（担当者）** が、誰か担当しているかどうかについての唯一の拠り所です。このリポジトリのどの欄も
> 担当者を追跡していません。[`roadmap-tracking` ラベルの付いた Issue 一覧](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+is%3Aopen+label%3Aroadmap-tracking)
> から確認できます。`no:assignee` で未着手のバックログを、`assignee:<user>` で担当者ごとの分担を
> 絞り込めます。詳しくは
> [BE-0109](BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues-ja.md)
> を参照してください。

> 今後実装したい機能を管理するドキュメントです。各項目は 1 ファイル（1 項目 = 1 BE ID）です。
> まとまっていない思いつきはまず [未整理アイデア](#未整理アイデア) に追加し、内容が固まったら
> 採番済みの項目へ昇格させます。
>
> - **各項目の状態**（実装済み、実装中、承認済み、保留、却下）は、このページではなく
>   [ロードマップダッシュボード](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html)で管理します。
>   ダッシュボードでは、トピックごとに整理され進捗バーも付いた一覧を、絞り込み・検索できます。このページが
>   扱うのは、ロードマップ項目とは何か、どう追加するかであり、誰がどこまで進んでいるかの一覧ではありません。
> - **現状**を、項目ごとの一覧ではなく文章でまとめたものは
>   [architecture.md#実装状況](../docs/ja/architecture.md#実装状況) にあります。
> - 設計の背景（なぜ）は [`DESIGN.md`](../DESIGN.md) にあります。
> - **全体の戦略的な方向性**は [vision.md](../docs/ja/vision.md) にあります。

## ロードマップ項目の追加：BE ID

すべてのロードマップ項目は `roadmaps/` の下に置きます。完全な手順の唯一の拠り所は
[`docs/ai-development.md`](../docs/ai-development.md#roadmap-items-be-ids-strict) です。手順には、
ディレクトリ構成、ID の採番、両言語ファイル、書式が含まれます。

項目を作成したあとは、`状態` フィールドだけが、
[ダッシュボード](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html)上の表示先を決めます。
このページ側で編集が必要な箇所はありません。

---

## 取り込まない（既に充足 / スコープ外）

- **変更履歴とバージョン管理**：シナリオは YAML で git 管理されるため既に充足しています。
- **既定としてのクラウド端末ファームや実機での実行**：決定的コアはローカル優先で CI に載せやすい backend（Simulator、ヘッドレスブラウザ、エミュレータ）を対象とし、実機やクラウド端末を既定とはしません（[DESIGN §1](../DESIGN.md)）。ホスト型のデバイスクラウド実行は既定ではありませんが、一律のスコープ外でもなく、*デバイスクラウド実行* の項目でオプトインの提案として管理しています。マルチプラットフォームも同様に *プラットフォーム対応* の項目にあります。
- **ステップ毎スクショ、エラー時 UI ツリー、端末ログ**：証跡サブシステム（capturePolicy + `result:error` 安全網）で充足済みです。
- **NL→テスト生成（Autopilot 相当）**：既存 `record` と *オーサリング体験* の項目に重複します。
- **スケジューリング、Slack / TestRail 連携**：CI・通知レイヤの領域です。優先度は低く、必要なら別途対応します。
- **失敗テストの自動リトライ**：決定性ファースト（固定 sleep 排除、条件待機）と緊張関係にあります。リトライは flaky を隠蔽する可能性があるため、入れるなら quarantine 用途に限定して検討が必要です。

---

## 未整理アイデア

> まとまっていない思いつきはここへ追加してください。後で採番済みの BE 項目に昇格させます。

- **project の過去の config バインディングを選べるようにする**：project は今のところ、有効な1つの config
  ソースへのバインディングだけを保持します（[BE-0225](BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）。
  バインディングはすでに、永続的で content-addressed なストレージ
  （[BE-0243](BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md)、
  [BE-0268](BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md)）を経由しており、
  このストレージは履歴一覧とロールバック UI の土台になり得ます。ただし、その一覧と UI はまだありません。
