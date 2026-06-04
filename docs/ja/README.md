[English](../README.md) · **日本語**

# Bajutsu ドキュメント

> 自然言語駆動 iOS E2E テストツール（iOS Simulator 限定）の実装ベースの体系ドキュメント。
> [`README.md`](../../README.md) が紹介、[`DESIGN.md`](../../DESIGN.md) が設計指針（思想）であるのに対し、
> このドキュメント群は **現状のコードが実際に何をするか** を機能単位で説明する。

Bajutsu は、自然言語で書かれた（または記録された）テストシナリオを受け取り、iOS Simulator
上のアプリを操作（tap / type / swipe / wait）し、**機械チェック可能なアサーション**で結果を
検証する。中心思想は「**AI を CI ゲートに持ち込まない**」こと —— AI はシナリオの *著者* と失敗時の
*調査役* であって、合否の *判定者* にはならない（[concepts](concepts.md) 参照）。

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
   observe → act → verify   ──tap/type/swipe/wait/query──▶  RocketSim / idb / fake
              │                          (simctl で boot/launch)            │
              ▼                                                             ▼
        Reporter ──────────────▶ runs/<runId>/{manifest.json, junit.xml, report.html}
                                              │
                                              ▼
                                  codegen ──▶ 同等の XCUITest (Swift)
```

各ボックスの担当モジュールと依存関係は [architecture](architecture.md) を参照。

## トピック一覧（推奨の読む順）

| # | ページ | 何を説明するか |
|---|---|---|
| 1 | [concepts](concepts.md) | 設計思想と中核原則（決定性・2 層・安定度順ラダー・AI の関与境界） |
| 2 | [architecture](architecture.md) | モジュール構成・依存関係・**実装状況（実装済み / 未配線）** |
| 3 | [scenarios](scenarios.md) | シナリオ YAML の文法（ステップ / 待機 / アサーション / 証跡トークン）= オーサリングリファレンス |
| 4 | [selectors](selectors.md) | セレクタモデルと決定的解決（0/1/2+ 件）・アサーション評価の仕組み = 決定性の核 |
| 5 | [drivers](drivers.md) | Driver 抽象・RocketSim / idb / fake・能力差の吸収・simctl 環境 |
| 6 | [run-loop](run-loop.md) | Orchestrator（observe → act → verify）・待機・リトライ・実行結果 |
| 7 | [evidence](evidence.md) | 証跡サブシステム（瞬時 / 区間・capturePolicy・provider・redact） |
| 8 | [reporting](reporting.md) | レポート（manifest.json / JUnit / HTML）と `runs/` レイアウト |
| 9 | [configuration](configuration.md) | 設定の階層（defaults × apps）・アプリのオンボーディング・`doctor` 充足度スコア |
| 10 | [recording](recording.md) | AI オーサリング（Tier 1 `record`）・Agent 抽象・システムアラート対処 |
| 11 | [codegen](codegen.md) | シナリオ → ネイティブ XCUITest 生成 |
| 12 | [cli](cli.md) | CLI コマンド・オプションの完全リファレンス |
| 13 | [sample-app](sample-app.md) | 同梱フィクスチャ `BajutsuSample`（全プリミティブを網羅） |

## クイックスタート

```bash
uv sync --extra dev                  # .venv 作成 + 依存 + 開発ツール
uv run pytest -q                     # 150 のユニットテスト（実機不要）

# 同梱サンプルに対して（実機 Simulator が必要）
make sample-build                    # フィクスチャアプリをビルド
make e2e                             # idb バックエンドで smoke シナリオを実行
```

CLI の最小形:

```bash
bajutsu run    <scenario.yaml> --app <name> [--backend rocketsim,idb] [--udid booted]
bajutsu doctor               --app <name>            # 規約充足度スコア
bajutsu record <out.yaml>    --app <name> --goal "..."  # AI で探索・記録（要 API キー）
bajutsu codegen <scenario.yaml> --app <name> -o UITests/Foo.swift
```

詳細は [cli](cli.md)。

## このドキュメントの方針

- **コードが正**: 記述は現在の実装（`bajutsu/`）に対応づけ、要所で `file.py:line` を示す。
- **設計と実装の差を明示**: [`DESIGN.md`](../../DESIGN.md) に書かれていてもまだ配線されていない機能
  （並列実行・モックサーバ・`network`/`appTrace` 証跡・`trace` コマンド・`relaunch`/`within` など）は、
  各ページと [architecture の実装状況](architecture.md#実装状況) で「未実装」と明記する。
- **言語**: 散文は日本語（[`DESIGN.md`](../../DESIGN.md) に合わせる）。コード内のコメント / docstring は英語。
