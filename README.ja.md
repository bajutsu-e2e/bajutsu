[English](README.md) · **日本語**

# Bajutsu

> iOS Simulator 向けの自然言語駆動 E2E テスト。
> **ステータス: pre-alpha** — 決定的コア・AI オーサリングループ（`record`）・証跡サブシステム・
> XCUITest codegen はすべて実装・ユニットテスト済み。デバイス操作を担うバックエンド *実行部*
> （idb）は実装済みだが実機未検証で、デバイス上での E2E 実行は未確認。

Bajutsu は自然言語で書かれた（または記録された）テストシナリオを受け取り、iOS Simulator 上の
アプリを操作（tap / type / swipe / wait）し、**機械チェック可能なアサーション**で結果を検証する。

> **名前について。** *Bajutsu*（馬術）は「馬を御する技」。ここでの暴れ馬は **iOS Simulator** —
> フレーキーなタイミング、非同期遷移、突然のシステムアラートがテストを振り落とす。Bajutsu は
> それを *taming*（手懐ける）こと、すなわち決定的な手綱でシナリオ通りに Simulator を毎回同じ場所へ
> 走らせることを目指す。

中心思想は **LLM を CI ゲートに持ち込まない** こと:

- **AI は著者と失敗時の調査役であり、判定者ではない。** シナリオを *書く*（探索 + 記録）・失敗を
  *調べる* のは助けるが、`run` は完全に決定的で AI を含まない —— 合否は機械アサーションのみで決まる。
- **2 層構成。** Tier 1 = AI ライブ操作（探索 / オーサリング）。Tier 2 = CI 回帰向けの決定的ランナー。

設計指針（日本語）は [`DESIGN.md`](DESIGN.md)。実装ベースの機能別ドキュメント（日本語）は
[`docs/ja/`](docs/ja/README.md)。

## 中核原則

- **決定性ファースト。** 固定 `sleep` 禁止（条件待機のみ）。曖昧なセレクタは「最初の一致を叩く」の
  ではなく即失敗。各テストはクリーン環境から開始。
- **安定セレクタ。** `accessibilityIdentifier`（非ローカライズ・データ由来）優先。座標は最終手段。
- **安定度順ラダー。** UI 操作は最も安定する手段から試し（id による semantic tap → 座標 tap → …）、
  選ぶバックエンドも利用可能な中で最も安定なもの。
- **アプリ非依存。** アプリ固有差分はすべて config（`apps.<name>`）に置き、ツール・ドライバ・
  ランナーはアプリをまたいで不変。
- **証跡はルール。** 「X のたびに取得」を再利用可能なルールへ正規化し、2 度目以降は AI なしで
  同じ証跡を再現する。

## アーキテクチャ

```
自然言語ゴール ──(record, Tier 1 / AI)──▶ シナリオ (YAML) ◀──(人手編集)
                                                       │
                                                       ▼
   Orchestrator  ── observe → act → verify (run, Tier 2; 決定的・AI 非依存)
        │ 抽象ドライバ API (tap/type/swipe/wait/query/screenshot)
        ▼
 idb バックエンド   ← 1 つの Driver IF に統一（テストは fake driver）
        │
        ▼
 Environment Manager (simctl)  +  Mock Server (決定的ネットワーク; 予定)
        │
        ▼
 Evidence/Trace  →  Reporter (manifest.json + JUnit + HTML)
                                                       │
                                                       ▼
                                  codegen ──▶ 同等の XCUITest (Swift)
```

3 つのエントリポイントがシナリオ形式を共有する: `record`（AI オーサリング）・`run`（決定的リプレイ）・
`codegen`（ネイティブ XCUITest を出力）。機能別の詳細は [`docs/ja/`](docs/ja/README.md)。

## ステータス

実装済み・テスト済み（約 150 のユニットテスト、Simulator 不要で走る）:

- ドライバ抽象と **セレクタ解決**（決定性の核）
- **シナリオスキーマ**（ステップ / 待機 / アサーション）の厳格検証 + YAML ラウンドトリップ
- **アサーション評価**（exists / value / label / count / enabled / disabled / selected）
- **Tier 2 run ループ**（act → wait → verify）、インメモリ fake driver で検証
- **証跡サブシステム**: 瞬時（screenshot / elements）、`video` / `deviceLog` 区間証跡（simctl）、
  `capturePolicy` トリガールール
- **レポート**（`manifest.json` + JUnit XML + 自己完結 HTML）
- **config 解決**（チーム既定 × アプリ別）と **バックエンド選択**（安定度順）
- **simctl コマンド層**、**idb 出力パーサ**、**doctor** 規約スコア
- **AI オーサリングループ**（`record`）: Agent 抽象 + Claude 実装 + システムアラートガード
- **XCUITest codegen**（構造マッピング・テスト時 AI 不要）
- 配線済み CLI: `run` / `doctor` / `record` / `codegen`

実装済みだが実機未検証（Xcode + Simulator が必要）:

- idb バックエンドの subprocess 実行。出力パーサはテスト済みだが、外部 CLI のサーフェスと
  JSON スキーマは **想定** で、インストール済みツールに対する確認が要る。simctl の launch 手順も
  best-effort。

未配線（スキーマ / フラグはあるが実行時に効かない）: 並列実行（`--workers`）、`locale` の適用、
再利用 `setup` precondition、外部 `mockServer` コマンド（シナリオ `mocks` で代替済み）、
`relaunch` / `within`、`trace` コマンド、自己修復トリアージ。完全な
「実装済み vs 未配線」表は [`docs/ja/architecture.md`](docs/ja/architecture.md)。

## 要件

- macOS + Xcode（iOS Simulator 用）— デバイスを動かすのに必須
- Python 3.13（[uv](https://github.com/astral-sh/uv) で管理）

## セットアップ

```bash
uv sync --extra dev      # .venv（Python 3.13）を作成し、依存 + 開発ツールを導入
```

## 使い方

CLI の概要（完全リファレンスは [`docs/ja/cli.md`](docs/ja/cli.md)）:

```bash
bajutsu run    <scenario.yaml> --app <name> [--backend idb] [--udid booted]
bajutsu record <out.yaml>      --app <name> --goal "..."   # 探索 + 記録（Tier 1・要 API キー）
bajutsu doctor                 --app <name>                # 現在画面の規約スコア
bajutsu codegen <scenario.yaml> --app <name> -o UITests/Foo.swift   # ネイティブ XCUITest を出力
```

アプリ別設定は `bajutsu.config.yaml`（リポジトリ同梱の `sample` アプリ）:

```yaml
defaults:
  backend: [idb]   # UI 安定度順; 最初に利用可能なものが actuator
  device: "iPhone 15"
  locale: en_US

apps:
  sample:
    bundleId: com.bajutsu.sample
    deeplinkScheme: bajutsusample
    launchEnv: { SAMPLE_UITEST: "1" }
    idNamespaces: [home, list, counter, settings, onboarding, auth, nav, comp, ctrl, text, lists]
```

## 開発

```bash
uv run pytest -q          # テスト
uv run ruff check .       # lint
uv run mypy bajutsu      # 型チェック（strict）
```

## プロジェクト構成

```
bajutsu/
├── drivers/base.py        # Driver プロトコル + セレクタ解決（決定性の核）
├── drivers/fake.py        # テスト用インメモリ fake driver
├── drivers/idb.py         # idb バックエンド（ヘッドレス・フレーム中心の座標 tap）
├── scenario.py            # シナリオスキーマ + YAML ラウンドトリップ
├── assertions.py          # 機械チェック可能なアサーション評価
├── orchestrator.py        # 決定的 Tier 2 run ループ
├── runner.py              # config + シナリオ -> レポート; デバイス factory
├── report.py              # manifest.json + JUnit + HTML
├── evidence.py            # 証跡: 瞬時（screenshot / elements）+ Sink
├── intervals.py           # 区間証跡（video / deviceLog）via simctl
├── config.py              # チーム既定 × アプリ別の解決
├── backends.py            # バックエンド選択 + ドライバ生成
├── env.py                 # simctl コマンド層
├── doctor.py              # 規約スコア
├── agent.py               # オーサリング Agent 抽象（Tier 1）
├── claude_agent.py        # Claude バックの Agent（ツール強制 / prompt cache）
├── record.py              # record ループ: 探索 -> シナリオ出力
├── alerts.py              # システムアラートガード（視覚ロケータ）
├── codegen.py             # シナリオ -> XCUITest (Swift)
├── dotenv.py              # 最小 .env ローダ
├── _yaml.py               # on/off を文字列のまま読む YAML ローダ
└── cli.py                 # CLI (typer)
```

## ロードマップ

- **M1 — 完了（実機検証待ち）。** 決定的ランナー: env (simctl) + ドライバ + シナリオ +
  アサーション + 軽量証跡 + manifest + アプリ別 config + `run` / `doctor`。完了条件: 同一の
  id ファーストシナリオが idb 上で決定的に通り、config だけで対象アプリを切り替えられること
  （idb は id ファーストセレクタをネイティブの `AXUniqueId` から直接解決し、フレーム中心の座標で
  操作する）。*（ロジックは実装・テスト済み。「idb で同一シナリオが通る」は実機での確認が必要。）*
- **M2 — ほぼ完了。** AI ループ（`record`）+ `capturePolicy` 証跡ルール + `video` / `deviceLog` +
  レポーター（JUnit/HTML）。*（完了。冪等な正規化 / 来歴コメントはまだ軽い。）*
- **M3 — CI を残してほぼ完了。** XCUITest codegen ✅、アプリトレース（`appTrace` / os_signpost）✅、
  証跡への redaction 適用 ✅、ネットワーク**観測**（アプリ内 collector + `request` アサーション）✅、
  **決定的モック**（シナリオ `mocks` → オフラインのプロトコル内スタブ）✅ — いずれも実機検証済み。
  残り: CI 統合。
- **M4 — 未着手。** 自己修復トリアージ（失敗の要約、最小シナリオ差分の提案。人間レビュー前提）。
