[English](../overview.md) · **日本語**

# Bajutsu ドキュメント

> 自然言語駆動の E2E（end-to-end）テストツールの実装に基づいたリファレンスです。決定的コアはプラット
> フォーム非依存で、プラットフォーム固有の継ぎ目は 1 つの `Driver` インターフェースの背後の **backend**
> だけです。つまり新しいプラットフォームは新しい backend です。iOS Simulator（idb / XCUITest）、
> web（Playwright）backend、Android（adb）backend はいずれも実装済みで、Flutter が次に予定されています。
> [`README.md`](../../README.md) が紹介、[`DESIGN.md`](../../DESIGN.md) が設計の根拠を扱うのに対し、この
> ドキュメント群は **現状のコードが実際に何をするか** を機能ごとに説明します。今後の計画は
> [ロードマップ](../../roadmaps/README-ja.md)にあります。

Bajutsu は、自然言語で書かれた（または記録された）テストシナリオを受け取り、アプリを操作（tap / type /
swipe / wait）し、**機械チェック可能なアサーション**で結果を検証します。決定的コアはプラットフォーム
非依存で、プラットフォーム固有の継ぎ目は **backend** だけなので、この backend を差し替えるだけで同じ
シナリオが iOS Simulator（idb）でもブラウザ（Playwright）でも動きます。中心にあるのは、AI を CI（継続的インテグレーション）ゲートに持ち込まないという考え方
です。AI はシナリオの著者であり失敗時の調査役であって、合否を判定する役割は担いません
（[concepts](concepts.md) 参照）。

## 全体像（データフロー）

```
自然言語ゴール ──(record / Tier 1, AI)──▶ シナリオ YAML ◀──(人手編集)
                                              │
                                              ▼
                                  run（Tier 2, AI 非依存・決定的）
                                              │
              ┌───────────────────────────────┼───────────────────────────────┐
              ▼                               ▼                               ▼
        Orchestrator                    Driver 抽象                     Evidence Sink
   observe → act → verify   ──tap/type/swipe/wait/query──▶  idb (iOS) / playwright (web) / fake
              │                          (simctl で boot/launch)            │
              ▼                                                             ▼
        Reporter ──────────────▶ runs/<runId>/{manifest.json, junit.xml, report.html}
                                              │
                                              ▼
                                  codegen ──▶ 同等の XCUITest (Swift)
```

各ボックスをどのモジュールが担当し、互いにどう依存しているかは [architecture](architecture.md) で
説明します。

## トピック一覧（推奨の読む順）

> **はじめての方は [Getting started チュートリアル](getting-started.md) から始めてください。** 手を
> 動かして辿るチュートリアルです（インストール → ユニットテスト → シナリオ → 実機実行 →
> レポート）。そのうえで、下のリファレンス各ページに戻ってきてください。**Mac のないマシン**（Linux、
> Windows、コンテナ）では、代わりに [web トラック](getting-started-web.md)を辿ってください。Playwright
> バックエンドでブラウザに対して同じループを辿ります。Xcode も Simulator も要りません。

| # | ページ | 何を説明するか |
|---|---|---|
| 1 | [concepts](concepts.md) | 設計思想と中核原則（決定性、2 層、安定度順ラダー、AI の関与境界） |
| 2 | [glossary](glossary.md) | ドメイン用語の一語ずつのリファレンス。混同しやすい語のかたまり（driver / backend / actuator / platform、target / app / device、scenario と test、trace と triage）を切り分けます |
| 3 | [architecture](architecture.md) | モジュール構成、依存関係、**実装状況（実装済み / 未配線）** |
| 4 | [scenarios](scenarios.md) | シナリオ YAML の文法（ステップ / 待機 / アサーション / 証跡トークン）= オーサリングリファレンス |
| 5 | [dsl-grammar](dsl-grammar.md) | シナリオ DSL（ドメイン固有言語）の **形式文法**（EBNF と全検証制約）。[scenarios](scenarios.md) の背後にある規範仕様です |
| 6 | [selectors](selectors.md) | セレクタモデルと決定的解決（0/1/2+ 件）、アサーション評価の仕組み = 決定性の核 |
| 7 | [drivers](drivers.md) | Driver 抽象、idb (iOS) / playwright (web) / fake、能力差の吸収、simctl 環境 |
| 8 | [run-loop](run-loop.md) | Orchestrator（observe → act → verify）、待機、リトライ、実行結果 |
| 9 | [evidence](evidence.md) | 証跡サブシステム（瞬時 / 区間、capturePolicy、provider、redact） |
| 10 | [reporting](reporting.md) | レポート（manifest.json / JUnit / HTML）と `runs/` レイアウト |
| 11 | [configuration](configuration.md) | 設定の階層（defaults × targets）、ターゲットのオンボーディング、`doctor` 充足度スコア |
| 12 | [recording](recording.md) | AI オーサリング（Tier 1 `record`）、Agent 抽象、システムアラート対処 |
| 13 | [codegen](codegen.md) | シナリオ → ネイティブ XCUITest 生成 |
| 14 | [cli](cli.md) | CLI のコマンドとオプションの完全リファレンス |
| 15 | [showcase](showcase.md) | showcase 群 — 唯一の iOS フィクスチャ（全プリミティブを網羅） |
| 16 | [ci](ci.md) | CI で動かす。リポ自身の workflow と再利用可能な `bajutsu-e2e` アクション |
| 17 | [self-hosting](self-hosting.md) | `serve` を単一 Mac 上でトークン認証付き LaunchAgent として常駐させ、Tailscale 越しに公開する（BE-0016 段階 A） |
| 18 | [vision](vision.md) | **将来構想**：成長の 3 軸（reach / scale / authoring）と、そのすべてが守る制約 |
| 19 | [multi-platform](multi-platform.md) | 既存の driver 抽象の背後に Android（エミュレータ）backend と Web（ブラウザ）backend をどう追加したか、そして次のプラットフォーム（Flutter）でも同じ手が使えることの説明。具体的なプラットフォーム別の計画は[ロードマップ](../../roadmaps/README-ja.md#プラットフォーム対応)にあります |
| 20 | [ai-development](ai-development.md) | AI エージェントと人間が並行して開発するための運用規約（ゲート、ブランチ、pre-push フック、worktree）。[`CLAUDE.md`](../../CLAUDE.md) の詳細版です |
| 21 | [roadmap-workflow](roadmap-workflow.md) | **着想から実装までの循環**：`ideation` スキルが BE 提案を起草し、`implement-be` スキルがそれを出荷する（プレースホルダー ID、Proposal → Implemented のライフサイクル） |

## クイックスタート

```bash
uv sync --group dev                  # .venv 作成 + 依存 + 開発ツール
uv run pytest -q                     # ユニットテスト（実機不要）

# showcase フィクスチャに対して（実機 Simulator が必要）
make -C demos/showcase swiftui-build                    # フィクスチャアプリをビルド
make -C demos/showcase run-swiftui                     # idb バックエンドでシナリオを実行
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
