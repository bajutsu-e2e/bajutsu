[English](../overview.md) · **日本語**

# Bajutsu ドキュメント

> 自然言語駆動の E2E（end-to-end）テストツールの実装に基づいたリファレンスです。決定的コアはプラット
> フォーム非依存で、プラットフォーム固有の継ぎ目は 1 つの `Driver` インターフェースの背後の **backend**
> だけです。つまり新しいプラットフォームは新しい backend です。iOS Simulator（XCUITest）、
> web（Playwright）backend、Android（adb）backend はいずれも実装済みで、Flutter が次に予定されています。
> [`README.md`](../../README.md) が紹介、[`DESIGN.md`](../../DESIGN.md) が設計の根拠を扱うのに対し、この
> ドキュメント群は **現状のコードが実際に何をするか** を機能ごとに説明します。今後の計画は
> [ロードマップ](../../roadmaps/README-ja.md)にあります。

Bajutsu は、自然言語で書かれた（または記録された）テストシナリオを受け取り、アプリを操作（tap / type /
swipe / wait）し、**機械チェック可能なアサーション**で結果を検証します。backend を差し替えるだけで、同じシナリオが iOS Simulator（XCUITest）でもブラウザ
（Playwright）でも動きます。Bajutsu は AI を CI（継続的インテグレーション）ゲートに持ち込みません。AI はシナリオの著者であり失敗時の調査役であって、合否は判定しません
（[concepts](concepts.md) 参照）。

## 全体像（データフロー）

![データフロー図。自然言語のゴールまたは人手編集がシナリオ YAML を生成し、Tier 2 の Orchestrator が backend 非依存の Driver API を通じて XCUITest・adb・Playwright のいずれかに対して決定的に実行します。合否は Reporter に渡り、失敗時は triage がシナリオへの修正案を提案します。](assets/diagrams/architecture-data-flow-ja.svg)

各ボックスをどのモジュールが担当し、互いにどう依存しているか（同じシステムを依存レイヤとして見た図を
含む）は [architecture](architecture.md) で説明します。

## トピック一覧（推奨の読む順）

> **はじめての方は [Getting started チュートリアル](getting-started/index.md) から始めてください。** 手を
> 動かして辿るチュートリアルです（インストール → ユニットテスト → シナリオ → 実機実行 →
> レポート）。そのうえで、下のリファレンス各ページに戻ってきてください。**Mac のないマシン**（Linux、
> Windows、コンテナ）では、代わりに [web トラック](getting-started/web.md)を辿ってください。Playwright
> バックエンドでブラウザに対して同じループを辿ります。Xcode も Simulator も要りません。

| # | ページ | 何を説明するか |
|---|---|---|
| 1 | [concepts](concepts.md) | 設計思想と中核原則（決定性、2 層、安定度順ラダー、AI の関与境界） |
| 2 | [glossary](glossary.md) | ドメイン用語の一語ずつのリファレンス。混同しやすい語のかたまり（driver / backend / actuator / platform、target / app / device、scenario と test、trace と triage）を切り分けます |
| 3 | [architecture](architecture.md) | モジュール構成、依存関係、**実装状況（実装済み / 未配線）** |
| 4 | [scenarios](scenarios.md) | シナリオ YAML の文法（ステップ / 待機 / アサーション / 証跡トークン）= オーサリングリファレンス |
| 5 | [dsl-grammar](dsl-grammar.md) | シナリオ DSL（ドメイン固有言語）の **形式文法**（EBNF と全検証制約）。[scenarios](scenarios.md) の背後にある規範仕様です |
| 6 | [selectors](selectors.md) | セレクタモデルと決定的解決（0/1/2+ 件）、アサーション評価の仕組み = 決定性の核 |
| 7 | [drivers](drivers.md) | Driver 抽象、XCUITest (iOS) / playwright (web) / adb (Android) / fake、能力差の吸収、simctl 環境 |
| 8 | [run-loop](run-loop.md) | Orchestrator（observe → act → verify）、待機、リトライ、実行結果 |
| 9 | [evidence](evidence.md) | 証跡サブシステム（瞬時 / 区間、capturePolicy、provider、redact） |
| 10 | [reporting](reporting.md) | レポート（manifest.json / JUnit / HTML）と `runs/` レイアウト |
| 11 | [configuration](configuration.md) | 設定の階層（defaults × targets）、ターゲットのオンボーディング、`doctor` 充足度スコア |
| 12 | [recording](recording.md) | AI オーサリング（Tier 1 `record`）、Agent 抽象、システムアラート対処 |
| 13 | [codegen](codegen.md) | シナリオ → ネイティブ XCUITest 生成 |
| 14 | [cli](cli.md) | CLI のコマンドとオプションの完全リファレンス |
| 15 | [showcase](showcase.md) | showcase 群（唯一の iOS フィクスチャ、全プリミティブを網羅） |
| 16 | [ci](ci.md) | CI で動かす。リポ自身の workflow と再利用可能な `bajutsu-e2e` アクション |
| 17 | [self-hosting](self-hosting.md) | `serve` を単一 Mac 上でトークン認証付き LaunchAgent として常駐させ、Tailscale 越しに公開します（BE-0016 段階 A） |
| 18 | [vision](vision.md) | 成長の 3 軸（reach / scale / authoring）。各軸がすでにどこまで進んでいるかと、そのすべてが守る制約を扱います。reach のプラットフォーム可搬性設計（セレクタ、id 規約、段階分け）も自身の節にあります |
| 19 | [ai-development](ai-development.md) | AI エージェントと人間が並行して開発するための運用規約（ゲート、ブランチ、pre-push フック、worktree）。[`CLAUDE.md`](../../CLAUDE.md) の詳細版です |
| 20 | [roadmap-workflow](roadmap-workflow.md) | **着想から実装までの循環**：`ideation` スキルが BE 提案を起草し、`implement-be` スキルがそれを出荷します（プレースホルダー ID、Proposal → Implemented のライフサイクル） |
| 21 | [contributor-workflow-tutorial](contributor-workflow-tutorial.md) | その循環を **手を動かしながら** 辿る walkthrough：一つのアイデアを `/ideation` からマージ済みの提案へ、続いて `/implement-be` からマージ済みの PR へ。良い提案と悪い提案の実例、`propose-and-build` を使うときも扱います |

## クイックスタート

```bash
uv sync --group dev                  # .venv 作成 + 依存 + 開発ツール
uv run pytest -q                     # ユニットテスト（実機不要）

# showcase フィクスチャに対して（実機 Simulator が必要）
make -C demos/showcase swiftui-build                    # フィクスチャアプリをビルド
make -C demos/showcase run-swiftui                     # iOS（XCUITest）バックエンドでシナリオを実行
```

CLI の最小形は次のとおりです。

```bash
bajutsu run    --target <name> [--scenario file.yaml]    # 既定: アプリのシナリオディレクトリ全体
bajutsu doctor --target <name>                           # 規約充足度スコア
bajutsu record --target <name> --goal "..." [--out file] # AI で探索・記録（要 API キー）
bajutsu codegen <scenario.yaml> --target <name> -o UITests/Foo.swift
bajutsu serve                                         # ローカル Web UI（Tier 1・CI 用ではない）
```

詳細は [cli](cli.md) を参照してください。

## このドキュメントの方針

- **コードが正**：記述は現在の実装（`bajutsu/`）に対応づけ、要所で `file.py:line` を示します。
- **設計と実装の差を明示**：[`DESIGN.md`](../../DESIGN.md) に書かれていても、まだ配線されていない機能
  （外部 `mockServer` コマンド。シナリオの `mocks` で代替済み）は、各ページと
  [architecture の実装状況](architecture.md#実装状況) で「未実装」と明記します。
- **言語**：散文は日本語で書き（[`DESIGN.md`](../../DESIGN.md) に合わせます）、コード内のコメントと docstring は英語です。
