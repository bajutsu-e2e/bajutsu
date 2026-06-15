[English](../getting-started.md) · **日本語**

# Getting started（はじめに）

> インストールから「テストが green になる」までを、手を動かして辿るチュートリアル。読み終える
> 頃には、Bajutsu をインストールし、ユニットテストを走らせ、シナリオを読み、同梱サンプルアプリを
> Simulator 上で動かし、HTML レポートを開けているはず。他のページが *リファレンス*（各機能が何を
> するか）であるのに対し、このページは *チュートリアル*（この手順を順にやる）。

関連: [cli](cli.md) ・ [scenarios](scenarios.md) ・ [sample-app](sample-app.md) ・ [run-loop](run-loop.md) ・ [reporting](reporting.md)

---

## 必要なもの

| 目的 | 必要なもの |
|---|---|
| 決定的コア + ユニットテスト | macOS or Linux、Python 3.13（[uv](https://github.com/astral-sh/uv) で管理） |
| Simulator 上でアプリを操作 | **Xcode** 入り macOS（iOS Simulator）、[XcodeGen](https://github.com/yonwoo9/XcodeGen)（サンプルのビルド用）、**idb** バックエンド（`brew install facebook/fb/idb-companion`） |
| AI オーサリング（`record`）/ `--dismiss-alerts` | `ANTHROPIC_API_KEY` |

**ステップ 1〜3 はどのマシンでも実行可能**（Simulator 不要）。ステップ 4〜6 は Xcode 入りの Mac が要る。

> **ステータス注記。** Bajutsu は pre-alpha。決定的コア・AI オーサリングループ・証跡サブシステム・
> codegen はいずれも実装済みでユニットテスト済み。idb バックエンド経由の実機実行は `make` ターゲット
> 経由で動くが、まだ堅牢化の途上。実機ステップで不審な挙動が出たら、安定地点であるユニットテストに
> 戻ること。

---

## ステップ 1 — インストール

```bash
git clone <this repo> && cd simpilot      # もしくはチェックアウト済みディレクトリへ cd
uv sync --extra dev                        # .venv（Python 3.13）+ 依存 + 開発ツールを作成
```

`uv` は `pyproject.toml` / `uv.lock` を読んで隔離された `.venv` を構築する。プロジェクトの
コマンドは `uv run`（例 `uv run bajutsu …`）を前置してこの環境で動かす。

CLI が配線されているか確認:

```bash
uv run bajutsu --help
```

`run` / `doctor` / `record` / `codegen` / `trace` / `triage` / `serve` が見えるはず（完全な仕様は
[cli](cli.md)）。

---

## ステップ 2 — ユニットテストを走らせる（Simulator 不要）

すべてが健全かを確かめる最速手段。決定性コア・シナリオスキーマ・アサーション・run ループを
インメモリの fake driver に対して検証する 405 のテストで、**Simulator には一切触れない**。

```bash
uv run pytest -q          # テストスイート
make check                # または: ruff（lint）+ mypy（strict 型）+ pytest をまとめて
```

ここが green なら、このマシンでエンジンは健全。ステップ 4 以降はすべてこの上に積み上がる。

---

## ステップ 3 — シナリオを読む

シナリオはただの YAML。名前付きテストのリストで、各テストは任意の `preconditions`、`steps` の
リスト、そして **機械チェック可能なアサーション** の `expect` ブロックを持つ。同梱の smoke テスト
[`demos/features/app/scenarios/smoke.yaml`](../../demos/features/app/scenarios/smoke.yaml) を開いてみる:

```yaml
# End-to-end smoke: onboarding -> login -> home -> counter.
- name: onboard, log in, and increment the counter
  preconditions:
    launchEnv: { SAMPLE_UITEST: "1" }     # アニメ無効 -> 条件待ちをタイトに
  steps:
    - tap:  { id: onboarding.start }
    - type: { text: "a@b.com", into: { id: auth.email } }
    - type: { text: "pw",      into: { id: auth.password } }
    - tap:  { id: auth.submit }
    - wait: { for: { id: home.title }, timeout: 5 }   # 固定 sleep ではなく条件待ち
    - tap:  { id: counter.increment }
    - tap:  { id: counter.increment }
  expect:
    - exists: { id: home.title }
    - value:  { sel: { id: counter.value }, equals: "2" }
```

体に入れておきたい形:

- **steps が操作し、`expect` が判定する。** `run` は steps を実行し、合否は `expect` の
  アサーションだけが決める —— AI も人も「正しそう」も介在しない。これが決定性の境界
  （[concepts](concepts.md)）。
- **セレクタは `accessibilityIdentifier` 優先**（`{ id: home.title }`）。安定でローカライズ非依存。
  *曖昧な* セレクタは「最初に一致した何か」をタップせず即座に失敗する（[selectors](selectors.md)）。
- **待機は sleep ではなく条件**（`wait: { for: …, timeout: 5 }`）—— 要素が現れるまで timeout まで
  ポーリングする。

文法の全体（全ステップ種別・待機・アサーション）は [scenarios](scenarios.md)、このアプリが公開する
識別子の一覧は [sample-app](sample-app.md)。

---

## ステップ 4 — サンプルアプリをビルドする（Xcode 必要）

リポジトリには、全 Bajutsu プリミティブを計装した小さな SwiftUI フィクスチャ `BajutsuSample` が
同梱されている。Simulator 向けにビルドする:

```bash
make -C demos/features sample-build         # xcodegen generate -> iOS Simulator 向けに xcodebuild
```

`demos/features/app/build/…` 下に `BajutsuSample.app` ができる（`.xcodeproj` と `build/` は gitignore 済み ——
`project.yml` が正）。launch-env フックと識別子カタログは [sample-app](sample-app.md) を参照。

---

## ステップ 5 — Simulator 上でシナリオを走らせる

Simulator を boot し、idb バックエンドが使える状態にする:

```bash
xcrun simctl boot "iPhone 15"                 # または Xcode > Open Developer Tool > Simulator から
brew install facebook/fb/idb-companion        # idb バックエンド（初回のみ）
uv sync --extra idb                           # idb の python クライアント
```

一発で通す経路は `make` ターゲット。ビルド済みアプリを install し、smoke シナリオと `doctor` を
booted デバイスで実行する:

```bash
make -C demos/features e2e
```

または CLI を直接叩く（同じことを書き下したもの）:

```bash
uv run bajutsu run demos/features/app/scenarios/smoke.yaml --app sample --backend idb --udid booted --no-erase
```

各フラグの意味:

- `--app sample` は [`bajutsu.config.yaml`](../../bajutsu.config.yaml) の `apps.sample` を選ぶ
  （bundle id・launch env・許可された id 名前空間）。ツール自体はアプリ非依存で、アプリ固有差分は
  すべて config に置く（[configuration](configuration.md)）。
- `--backend idb` で actuator を選び、`--udid booted` で現在 boot 中の Simulator を狙う。
- `--no-erase` は先頭で `simctl erase` せず、install 済みアプリをそのまま使う。

成功すると次のような行が出る:

```
PASS  runs/20260610-120000/manifest.json
```

`run` は **全シナリオ合格で終了コード 0、いずれか失敗で 1** —— この終了コードが CI（継続的インテグレーション）ゲート
（[run-loop](run-loop.md)）。

> 環境の問題（booted Simulator が無い・idb 未インストール）に当たったら、まず
> `uv run bajutsu doctor --app sample` を走らせる。必要な CLI と booted デバイスの ✓/✗
> チェックリストを表示し、続けて現在の画面が識別子規約にどれだけ従っているかを採点する
> （[configuration](configuration.md#doctor規約充足度スコア)）。

---

## ステップ 6 — レポートを読む

各実行は `runs/<runId>/`（`runId` は `YYYYMMDD-HHMMSS`）フォルダを書き出し、同じ結果を 3 つの
ビューで残す:

```
runs/20260610-120000/
├── manifest.json     # step -> 結果の対応 —— 唯一の正
├── junit.xml         # CI 連携（1 シナリオ = 1 testcase）
└── report.html       # 自己完結 HTML（ブラウザで開く）
```

HTML レポートを開くと、各ステップ・その結果・取得した証跡（スクリーンショット / 要素スナップ
ショット）がインラインで見られる:

```bash
open runs/<runId>/report.html      # 最新実行のフォルダ
```

ターミナルを離れずにテキストのタイムラインとして実行を眺めることもできる:

```bash
uv run bajutsu trace               # runs/ 下の最新実行
```

各フォーマットと `runs/` レイアウトの詳細は [reporting](reporting.md)。

---

## 次に読むもの

これでループ一周分が揃った: インストール → ユニットテスト → シナリオ → 実機実行 → レポート。
同じシナリオ形式を共有する入口があと 2 つある:

- **AI でオーサリング。** Claude にゴールへ向けて探索させ、シナリオを書かせる（Tier 1）。
  `.env` ファイルに `ANTHROPIC_API_KEY=sk-ant-…` を置いてから:
  ```bash
  uv run bajutsu record out.yaml --app sample --goal "log in and increment the counter to 3"
  ```
  オーサリングループとシステムアラートガードの仕組み: [recording](recording.md)。
- **ネイティブ XCUITest を吐く。** シナリオを Swift へ変換する（テスト時に Bajutsu ランタイム・AI は
  不要）:
  ```bash
  uv run bajutsu codegen demos/features/app/scenarios/smoke.yaml --app sample -o UITests/Smoke.swift
  ```
  構造マッピング: [codegen](codegen.md)。`make -C demos/features ui-test` で end-to-end に実行できる。

ここからは各リファレンスページが個別に深掘りしている —— まず *なぜ* を [concepts](concepts.md) で、
続けて [ドキュメント索引](README.md) の推奨読書順に従うとよい。サンプル以外の自前アプリを
オンボーディングするには [configuration](configuration.md#新しいアプリのオンボーディング) を参照。
