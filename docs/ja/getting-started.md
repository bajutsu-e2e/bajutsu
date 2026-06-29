[English](../getting-started.md) · **日本語**

# Getting started（はじめに）

> 手を動かして辿るチュートリアルです。読み終える頃には、Bajutsu をインストールし、ユニットテストを
> 走らせ、シナリオを読み、同梱のサンプルアプリを Simulator 上で動かし、HTML レポートを開けている
> はずです。他のページが各機能の役割を説明するリファレンスであるのに対し、このページは手順を順に
> 実行していくチュートリアルです。

関連: [cli](cli.md) ・ [scenarios](scenarios.md) ・ [sample-app](sample-app.md) ・ [run-loop](run-loop.md) ・ [reporting](reporting.md)

---

## 必要なもの

| 目的 | 必要なもの |
|---|---|
| 決定的コア + ユニットテスト | macOS or Linux、Python 3.13（[uv](https://github.com/astral-sh/uv) で管理） |
| Simulator 上でアプリを操作 | **Xcode** 入り macOS（iOS Simulator）、[XcodeGen](https://github.com/yonwoo9/XcodeGen)（サンプルのビルド用）、**idb** バックエンド（`brew install facebook/fb/idb-companion`） |
| web アプリを操作（Playwright） | 任意の OS。`uv sync --extra web` + `uv run playwright install chromium`（Mac / Simulator 不要。[`demos/web`](../../demos/web/README.md) 参照） |
| AI オーサリング（`record` / `crawl`）/ `--dismiss-alerts` | `ANTHROPIC_API_KEY`（または `--agent claude-code` で Claude Code ログイン） |

**ステップ 1〜3 はどのマシンでも実行可能です**（Simulator 不要）。ステップ 4〜6 は Xcode 入りの Mac が必要です。

> **ステータス注記。** Bajutsu は pre-alpha です。決定的コア、AI オーサリングループ、証跡サブシステム、
> codegen はいずれも実装済みで、ユニットテストも揃っています。idb バックエンド経由の実機実行は `make`
> ターゲットを通して動きますが、まだ堅牢化の途上です。実機ステップで不審な挙動が出たら、安定している
> ユニットテストに戻ってください。

---

## ステップ 1：インストール

```bash
git clone <this repo> && cd bajutsu       # もしくはチェックアウト済みディレクトリへ cd
uv sync --group dev                        # .venv（Python 3.13）+ 依存 + 開発ツールを作成
```

`uv` は `pyproject.toml` / `uv.lock` を読み、隔離された `.venv` を構築します。プロジェクトの
コマンドは `uv run`（例 `uv run bajutsu …`）を前に付け、この環境で実行してください。

CLI（コマンドラインインターフェース）が配線されているかを確認します。

```bash
uv run bajutsu --help
```

`run` / `doctor` / `record` / `crawl` / `codegen` / `trace` / `triage` / `approve` / `serve` / `mcp` / `worker` / `lint` / `schema` のコマンドが表示されるはずです
（完全なリファレンスは [cli](cli.md)）。

---

## ステップ 2：ユニットテストを走らせる（Simulator 不要）

すべてが健全かを確かめる最速の手段です。決定性コア、シナリオスキーマ、アサーション、run ループを
インメモリの fake driver に対して検証するユニットテストで、**Simulator には一切触れません**。

```bash
uv run pytest -q          # テストスイート
make check                # または: ruff（lint）+ mypy（strict 型）+ pytest をまとめて
```

ここが green なら、このマシンでエンジンは動作しています。ステップ 4 以降はすべてこの上に積み上がります。

---

## ステップ 3：シナリオを読む

シナリオはただの YAML です。名前付きテストのリストで、各テストは任意の `preconditions`、`steps` の
リスト、そして **機械チェック可能なアサーション** からなる `expect` ブロックを持ちます。同梱の smoke
テスト [`demos/features/app/scenarios/smoke.yaml`](../../demos/features/app/scenarios/smoke.yaml) を開いてみましょう。

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

要点は次のとおりです。

- **steps が操作し、`expect` が判定します。** `run` は steps を実行し、合否は `expect` の
  アサーションだけが決めます。AI も人の判断も介在しません。これが決定性の境界です
  （[concepts](concepts.md)）。
- **セレクタは `accessibilityIdentifier` を優先します**（`{ id: home.title }`）。安定していて、
  ローカライズにも左右されません。曖昧なセレクタは最初に一致した要素をタップしたりせず、即座に
  失敗します（[selectors](selectors.md)）。
- **待機は sleep ではなく条件です**（`wait: { for: …, timeout: 5 }`）。Bajutsu は要素が現れるまで、
  timeout に達するまでポーリングします。

文法の全体（すべてのステップ種別、待機、アサーション）は [scenarios](scenarios.md) に、このアプリが
公開する識別子の一覧は [sample-app](sample-app.md) にまとまっています。

---

## ステップ 4：サンプルアプリをビルドする（Xcode 必要）

リポジトリには、Bajutsu のすべてのプリミティブを計装した小さな SwiftUI フィクスチャ `BajutsuSample`
が同梱されています。Simulator 向けにビルドします。

```bash
make -C demos/features sample-build         # xcodegen generate -> iOS Simulator 向けに xcodebuild
```

`demos/features/app/build/…` の下に `BajutsuSample.app` ができます（`.xcodeproj` と `build/` は gitignore
済みで、`project.yml` が正です）。launch-env フックと識別子カタログは [sample-app](sample-app.md) を参照してください。

---

## ステップ 5：Simulator 上でシナリオを走らせる

Simulator を boot し、idb バックエンドが使える状態にします。

```bash
xcrun simctl boot "iPhone 15"                 # または Xcode > Open Developer Tool > Simulator から
brew install facebook/fb/idb-companion        # idb バックエンド（初回のみ）
uv sync --extra idb                           # idb の python クライアント
```

一発で通す経路は `make` ターゲットです。ビルドしたばかりのアプリを install し、smoke シナリオと
`doctor` チェックを booted デバイスで実行します。

```bash
make -C demos/features e2e
```

あるいは CLI を直接叩くこともできます（上と同じ手順を書き下したものです）。

```bash
uv run bajutsu run --scenario demos/features/app/scenarios/smoke.yaml --target sample --backend idb --udid booted --no-erase
```

各フラグの意味は次のとおりです。

- `--target sample` は [`bajutsu.config.yaml`](../../bajutsu.config.yaml) の `targets.sample` を選びます
  （bundle id、launch env、許可された id 名前空間を含みます）。ツール自体はアプリ非依存で、アプリ
  ごとの差分はすべて config に置きます（[configuration](configuration.md)）。
- `--backend idb` で actuator を選び、`--udid booted` で現在 boot 中の Simulator を対象にします。
- `--no-erase` は最初に `simctl erase` をかけず、install 済みのアプリをそのまま使います。

成功すると、次のような行が出ます。

```
PASS  runs/20260610-120000/manifest.json
```

`run` は **全シナリオ合格で終了コード 0、いずれか失敗で 1** を返し、この終了コードが CI（継続的
インテグレーション）ゲートになります（[run-loop](run-loop.md)）。

> 環境の問題（booted Simulator が無い、idb が未インストールなど）に当たったら、まず
> `uv run bajutsu doctor --target sample` を走らせてください。必要な CLI と booted デバイスの ✓/✗
> チェックリストを表示し、続けて現在の画面が識別子規約にどれだけ従っているかを採点します
> （[configuration](configuration.md#doctor規約充足度スコア)）。

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
open runs/<runId>/report.html      # 最新実行のフォルダ
```

ターミナルを離れずに、完了した実行をテキストのタイムラインとして眺めることもできます。

```bash
uv run bajutsu trace               # runs/ 下の最新実行
```

各フォーマットと `runs/` のレイアウトの詳細は [reporting](reporting.md) を参照してください。

---

## 次に読むもの

これでループを一周する分が揃いました。インストール → ユニットテスト → シナリオ → 実機実行 →
レポートです。同じシナリオ形式を使う入口があと 2 つあります。

- **AI でオーサリングする。** Claude にゴールへ向けて探索させ、シナリオを書かせます（Tier 1）。
  `.env` ファイルに `ANTHROPIC_API_KEY=sk-ant-…` を置いてから実行します。
  ```bash
  uv run bajutsu record --target sample --goal "log in and increment the counter to 3"   # アプリのシナリオディレクトリへ書く
  ```
  Web UI（`make serve`）の **API key** ボタンからも、実行中のセッション用に同じキーを設定できます。
  伏字表示にしたうえで表示切り替えで内容を確認できますが、メモリ上に保持するだけでディスクには
  書き込みません。再起動をまたいで保持したい場合は、引き続き `.env` を使ってください。オーサリング
  ループとシステムアラートガードの仕組みは [recording](recording.md) を参照してください。
- **ネイティブ XCUITest を出力する。** シナリオを Swift へ変換します（テスト実行時に Bajutsu の
  ランタイムや AI は不要です）。
  ```bash
  uv run bajutsu codegen demos/features/app/scenarios/smoke.yaml --target sample -o UITests/Smoke.swift
  ```
  構造のマッピングは [codegen](codegen.md) を参照してください。`make -C demos/features ui-test` で
  end-to-end に実行できます。

ここからは、各リファレンスページがそれぞれの要素を詳しく説明します。まず設計の根拠を
[concepts](concepts.md) で確認し、続けて [ドキュメント概要](overview.md) の推奨読書順に従ってください。
サンプル以外の自前のアプリをオンボーディングするには
[configuration](configuration.md#新しいターゲットのオンボーディング) を参照してください。
