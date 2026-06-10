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
   observe → act → verify   ──tap/type/swipe/wait/query──▶  idb / fake
              │                          (simctl で boot/launch)            │
              ▼                                                             ▼
        Reporter ──────────────▶ runs/<runId>/{manifest.json, junit.xml, report.html}
                                              │
                                              ▼
                                  codegen ──▶ 同等の XCUITest (Swift)
```

各ボックスの担当モジュールと依存関係は [architecture](architecture.md) を参照。

## トピック一覧（推奨の読む順）

> **はじめての人は [Getting started チュートリアル](getting-started.md) から。** インストールから
> 「テストが green になる」までを手を動かして辿る（インストール → ユニットテスト → シナリオ →
> 実機実行 → レポート）。その後で下のリファレンス各ページへ。

| # | ページ | 何を説明するか |
|---|---|---|
| 1 | [concepts](concepts.md) | 設計思想と中核原則（決定性・2 層・安定度順ラダー・AI の関与境界） |
| 2 | [architecture](architecture.md) | モジュール構成・依存関係・**実装状況（実装済み / 未配線）** |
| 3 | [scenarios](scenarios.md) | シナリオ YAML の文法（ステップ / 待機 / アサーション / 証跡トークン）= オーサリングリファレンス |
| 4 | [dsl-grammar](dsl-grammar.md) | シナリオ DSL の **形式文法**（EBNF + 全検証制約）—— [scenarios](scenarios.md) の背後にある規範仕様 |
| 5 | [selectors](selectors.md) | セレクタモデルと決定的解決（0/1/2+ 件）・アサーション評価の仕組み = 決定性の核 |
| 6 | [drivers](drivers.md) | Driver 抽象・idb / fake・能力差の吸収・simctl 環境 |
| 7 | [run-loop](run-loop.md) | Orchestrator（observe → act → verify）・待機・リトライ・実行結果 |
| 8 | [evidence](evidence.md) | 証跡サブシステム（瞬時 / 区間・capturePolicy・provider・redact） |
| 9 | [reporting](reporting.md) | レポート（manifest.json / JUnit / HTML）と `runs/` レイアウト |
| 10 | [configuration](configuration.md) | 設定の階層（defaults × apps）・アプリのオンボーディング・`doctor` 充足度スコア |
| 11 | [recording](recording.md) | AI オーサリング（Tier 1 `record`）・Agent 抽象・システムアラート対処 |
| 12 | [codegen](codegen.md) | シナリオ → ネイティブ XCUITest 生成 |
| 13 | [cli](cli.md) | CLI コマンド・オプションの完全リファレンス |
| 14 | [sample-app](sample-app.md) | 同梱フィクスチャ `BajutsuSample`（全プリミティブを網羅） |
| 15 | [ci](ci.md) | CI で動かす — リポ自身の workflow + 再利用可能な `bajutsu-e2e` アクション |
| 16 | [cloud-hosting](cloud-hosting.md) | **将来構想** — Web UI を共有・公開サービスとしてホスティング（サーバ/DB/ストレージ/デプロイ選定） |

## クイックスタート

```bash
uv sync --extra dev                  # .venv 作成 + 依存 + 開発ツール
uv run pytest -q                     # 306 のユニットテスト（実機不要）

# 同梱サンプルに対して（実機 Simulator が必要）
make sample-build                    # フィクスチャアプリをビルド
make e2e                             # idb バックエンドで smoke シナリオを実行
```

CLI の最小形:

```bash
bajutsu run    <scenario.yaml> --app <name> [--backend idb] [--udid booted]
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
