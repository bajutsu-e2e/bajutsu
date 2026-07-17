[English](../../getting-started/index.md) · **日本語**

# Getting started（はじめに）

> 手を動かして辿るチュートリアルです。読み終える頃には、Bajutsu をインストールし、ユニットテストを
> 走らせ、シナリオを読み、実際のアプリを操作し、HTML レポートを開けているはずです。他のページが
> 各機能の役割を説明するリファレンスであるのに対し、このページは手順を順に実行していくチュートリアル
> です。

関連: [cli](../cli.md) · [scenarios](../scenarios.md) · [run-loop](../run-loop.md) · [reporting](../reporting.md)

---

## 2 つのトラック、1 つのループ

以下のステップ 1〜3 と 6 は、何を操作するかに関わらず共通です。インストール、ユニットテスト、
シナリオを読む、レポートを読む、という部分です。異なるのはステップ 4〜5（対象アプリのビルドまたは
配信と、それに対するシナリオの実行）だけで、これは **[backend](../glossary.md#driver-backend-actuator-platform)** ごとに変わります。「プラットフォームは
backend にすぎない」からです（[concepts](../concepts.md)）。

- **[iOS トラック](ios.md)** — idb backend で iOS Simulator 上まで完結します。macOS と Xcode が必要です。
- **[web トラック](web.md)** — Playwright backend でブラウザに対して完結します。Linux、Windows、
  macOS のどの OS でも動きます。Xcode も Simulator も不要です。

自分のマシンに合うほうを選ぶか、両方試してください。同じシナリオスキーマ、ランナー、CLI が、
どちらのターゲットも変わらず操作します。

> **始めるのに Claude は要りません。** モデルに到達するのは AI オーサリングの経路だけです。`run`、
> `doctor`、`lint`、`codegen`、`trace` などは設定ゼロで動きます。キーも `.env` もログインも不要です。
> どのコマンドが Claude を使い、どれが使わないかは
> [Claude を使う機能と使わない機能](../ai-boundary.md)にまとめてあります。

> **ステータス注記。** Bajutsu は pre-alpha です。決定的コア、AI オーサリングループ、証跡サブシステム、
> codegen はいずれも実装済みで、ユニットテストも揃っています。実機実行は iOS Simulator と Android
> エミュレータの両方で end-to-end に検証済みで、web（Playwright）backend も同じ決定的なループを
> ブラウザ上で動かします。実機ステップで不審な挙動が出たら、安定しているユニットテストに戻ってください。

---

## ステップ 1：インストール

```bash
git clone <this repo> && cd bajutsu       # もしくはチェックアウト済みディレクトリへ cd
uv sync --group dev                        # .venv（Python 3.13）+ 依存 + 開発ツールを作成
```

`uv` は `pyproject.toml` / `uv.lock` を読み、隔離された `.venv` を構築します。プロジェクトの
コマンドは `uv run`（例 `uv run bajutsu …`）を前に付け、この環境で実行してください。

> **PyPI からインストールする場合**：基本パッケージは AI-free です。`pip install bajutsu` は
> 決定論的なオーサリングと実行の経路を AI SDK なしで導入し、`pip install bajutsu[ai]`（または
> `bajutsu[bedrock]`）が Claude の経路のための SDK を追加します。分離の詳細は
> [What uses Claude](../ai-boundary.md#claude-の経路をインストールする)にあります。

CLI（コマンドラインインターフェース）が配線されているかを確認します。

```bash
uv run bajutsu --help
```

`run` / `doctor` / `record` / `crawl` / `codegen` / `trace` / `triage` / `approve` / `serve` / `mcp` / `worker` / `lint` / `schema` のコマンドが表示されるはずです
（完全なリファレンスは [cli](../cli.md)）。

各トラックは、この上にもう 1 つだけインストール手順を足します（iOS なら idb backend のツール、
web なら Playwright のブラウザ）。詳細は選んだトラックのページを参照してください。

---

## ステップ 2：ユニットテストを走らせる

すべてが健全かを確かめる最速の手段です。決定性コア、シナリオスキーマ、アサーション、run ループを
インメモリの fake driver に対して検証するユニットテストで、**実機にもブラウザにも一切触れません**。

```bash
uv run pytest -q          # テストスイート
make check                # または: ruff（lint）+ mypy（strict 型）+ pytest をまとめて
```

ここが green なら、このマシンでエンジンは動作しています。どちらのトラックに進んでも、この上に
積み上がります。

---

## ステップ 3：シナリオを読む

シナリオファイルはただの YAML です。名前付きシナリオのリスト（`{ description, scenarios }` の
マッピングで包んでもかまいません）で、各シナリオは任意の `preconditions`、`steps` のリスト、
そして **機械チェック可能なアサーション** からなる `expect` ブロックを持ちます。showcase 一式の
smoke テスト [`demos/showcase/scenarios/smoke.yaml`](../../../demos/showcase/scenarios/smoke.yaml) は、
起動時に固定件数の行が表示されることを確かめます。

```yaml
- name: stable catalog smoke
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }     # アニメ無効 -> 条件待ちをタイトに
  steps:
    - wait: { for: { id: [stable.row.1, stable_row_1] }, timeout: 10 }
  expect:
    - count: { sel: { idMatches: ["stable.row.*", "stable_row_*"] }, equals: 5 }
    - exists: { id: [stable.row.1, stable_row_1] }
```

要点は次のとおりです。

- **steps が操作し、`expect` が判定します。** `run` は steps を実行し、合否は `expect` の
  アサーションだけが決めます。AI も人の判断も介在しません。これが決定性の境界です
  （[concepts](../concepts.md)）。
- **セレクタは id を優先します**（`{ id: stable.row.1 }`）。安定していて、ローカライズにも
  左右されません。曖昧なセレクタは最初に一致した要素をタップしたりせず、即座に失敗します
  （[selectors](../selectors.md)）。このシナリオの `id` が 2 つの形（`stable.row.1` と
  `stable_row_1`）を列挙しているのは、同じファイルを Android でも変更なく動かすためです。
  Android の native な id 構文は `.` を保持できません
  （[scenarios](../scenarios.md#プラットフォームをまたぐ-id候補のリストbe-0221)）。
- **待機は sleep ではなく条件です**（`wait: { for: …, timeout: 10 }`）。Bajutsu は要素が現れるまで、
  timeout に達するまでポーリングします。

文法の全体（すべてのステップ種別、待機、アサーション）は [scenarios](../scenarios.md) にまとまって
います。各トラックの smoke テストは中身が少しずつ異なります（web トラックはログインとカウンターの
流れです）が、この step/expect の文法自体はどのトラックでも共通です。

---

## ステップ 6：レポートを読む

実行ごとに `runs/<runId>/`（`runId` は `YYYYMMDD-HHMMSS`）というフォルダを書き出し、同じ結果を
3 つのビューで残します。

```
runs/20260610-120000/
├── manifest.json     # step -> 結果の対応（唯一の正）
├── junit.xml         # CI 連携（1 シナリオ = 1 testcase）
└── report.html       # 自己完結 HTML（ブラウザで開く）
```

HTML レポートを開くと、各ステップとその結果、取得した証跡（スクリーンショットや要素スナップ
ショット）をインラインで確認できます。

```bash
open runs/<runId>/report.html      # macOS。Linux では xdg-open を使ってください
```

ターミナルを離れずに、完了した実行をテキストのタイムラインとして眺めることもできます。

```bash
uv run bajutsu trace               # runs/ 下の最新実行
```

各フォーマットと `runs/` のレイアウトの詳細は [reporting](../reporting.md) を参照してください。

---

## 次に読むもの

これでループを一周する分が揃いました。インストール → ユニットテスト → シナリオ → 実機実行 →
レポートです。同じシナリオ形式を使う入口があと 2 つあります。

- **AI でオーサリングする。** Claude にゴールへ向けて探索させ、シナリオを書かせます（Tier 1）。
  `.env` ファイルに `ANTHROPIC_API_KEY=sk-ant-…` を置いてから、`bajutsu record --target <name>
  --goal "…"` を実行します。正確な呼び出し方は選んだトラックのページに載っています。Web UI
  （`make serve`）の **API key** ボタンからも、実行中のセッション用に同じキーを設定できます。
  キーは write-once です。マスクしたプレビューだけを表示し、設定後に内容を再表示することはありません。
  変更するには新しいキーを設定します（読み出す手段はありません）。メモリ上に保持するだけでディスクには
  書き込まないため、再起動をまたいで保持したい場合は、引き続き `.env` を使ってください。オーサリング
  ループとシステムアラートガードの仕組みは [recording](../recording.md) を参照してください。Web UI の
  各タブの操作方法は [web-ui](../web-ui.md) を参照してください。
- **ネイティブテストを出力する。** シナリオを XCUITest、Playwright、または UI Automator のネイティブ
  テストへ変換します（テスト実行時に Bajutsu のランタイムや AI は不要です）。
  ```bash
  uv run bajutsu codegen demos/showcase/scenarios/smoke.yaml --target showcase-swiftui -o UITests/Smoke.swift
  ```
  構造のマッピングは [codegen](../codegen.md) を参照してください。

ここからは、各リファレンスページがそれぞれの要素を詳しく説明します。まず設計の根拠を
[concepts](../concepts.md) で確認し、続けて[ドキュメント概要](../overview.md)の推奨読書順に従って
ください。showcase やデモ以外の自前のアプリをオンボーディングするには
[configuration](../configuration.md#新しいターゲットのオンボーディング) を参照してください。
