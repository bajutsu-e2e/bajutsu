[English](../getting-started-web.md) · **日本語**

# Getting started（web トラック。Mac 不要）

> どの OS でも最後まで辿り着ける、手を動かすチュートリアルです。読み終える頃には、Bajutsu を
> インストールし、ユニットテストを走らせ、シナリオを読み、**Playwright** バックエンドで実際の web
> アプリをブラウザ上で操作し、HTML レポートを開けているはずです。macOS も Xcode も iOS Simulator も
> 要りません。これは [Getting started チュートリアル](getting-started.md)の web 版です。インストール →
> シナリオ → 実行 → レポートという同じループを、同じシナリオ形式と同じ決定的ランナーの上で辿ります。
> 違うのはバックエンドだけです。

関連: [getting-started（iOS）](getting-started.md) ・ [cli](cli.md) ・ [scenarios](scenarios.md) ・ [drivers](drivers.md) ・ [reporting](reporting.md)

---

## なぜ web トラックがあるのか

Bajutsu の看板の主張は**「プラットフォームはバックエンドである」**です。
決定的コア、シナリオ形式、レポーターは、UI を実際に操作するバックエンドが何であっても同じです。
[iOS チュートリアル](getting-started.md)は Simulator（`idb` バックエンド）で完結しますが、これには
Xcode 入りの Mac が必要です。このトラックは同じループをブラウザ（`web` / Playwright バックエンド）で
完結させます。ブラウザなら Linux でも Windows でも macOS でも同じように動きます。これは、Bajutsu 自身の
web デモを Linux のゲート上で動かしているのと同じバックエンドです（[`demos/web`](../../demos/web/README.md)）。

ここでの用語は[用語集](glossary.md)に従います。**バックエンド**は `--backend` に渡すトークン（ここでは
`web`）で、これが**アクチュエーター**（タップや入力を実際に行う Playwright エンジン）に解決されます。
**ターゲット**はテスト対象アプリを記述する `targets.<name>` の設定エントリ一つで、web の場合は iOS の
`bundleId` ではなく `baseUrl` で識別されます。

> **最後まで Claude は要りません。** モデルに到達するのは AI オーサリングの経路（`record`、`crawl`）
> だけです。`run`、`doctor`、`lint`、`codegen`、`trace` は設定ゼロで動きます。キーも `.env` もログインも
> 不要です。どのコマンドが Claude を使い、どれが使わないかは
> [Claude を使う機能と使わない機能](ai-boundary.md)にまとめてあります。

> **ステータス注記。** Bajutsu は pre-alpha です。決定的コア、AI オーサリングループ、証跡サブシステム、
> codegen はいずれも実装済みで、ユニットテストも揃っています。web（Playwright）バックエンドは最初の
> スライスを実装済みで、ブラウザに対する決定的な `run` を Linux のゲート上で動かしています
> （[BE-0041](../../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)）。

---

## ステップ 1：インストール

```bash
git clone <this repo> && cd bajutsu       # もしくはチェックアウト済みディレクトリへ cd
uv sync --group dev                        # .venv（Python 3.13）+ 依存 + 開発ツールを作成
uv sync --extra web                        # Playwright の Python パッケージを追加
uv run playwright install chromium         # Playwright が操作する Chromium バイナリを取得
```

`uv` は `pyproject.toml` / `uv.lock` を読み、隔離された `.venv` を構築します。プロジェクトの
コマンドは `uv run`（例 `uv run bajutsu …`）を前に付け、この環境で実行してください。`--extra web` の
行と `playwright install` の行が、基本のインストールに web バックエンドが上乗せする分です。Chromium
バイナリは pip の依存ではなく、別途ダウンロードされます。

CLI（コマンドラインインターフェース）が配線されているかを確認します。

```bash
uv run bajutsu --help
```

`run` / `doctor` / `record` / `crawl` / `codegen` / `trace` / `triage` / `approve` / `serve` / `mcp` / `worker` / `lint` / `schema` のコマンドが表示されるはずです
（完全なリファレンスは [cli](cli.md)）。

---

## ステップ 2：ユニットテストを走らせる（ブラウザ不要）

すべてが健全かを確認する一番速い方法です。ユニットテストは、決定性コア、シナリオスキーマ、アサーション、
実行ループを、インメモリの fake ドライバーに対して検証します。ブラウザは起動しません。

```bash
uv run pytest -q          # テストスイート
make check                # または ruff（lint）+ mypy（strict types）+ pytest をまとめて
```

ここで緑なら、このマシンでエンジンが動いています。ステップ 4 以降はすべてこの上に積み上がります。

---

## ステップ 3：シナリオを読む

シナリオはただの YAML です。名前付きテストのリストで、各テストは任意の `preconditions`、`steps` の
リスト、そして**機械的に検証できるアサーション**の `expect` ブロックを持ちます。web デモのスモークテスト
[`demos/web/scenarios/smoke.yaml`](../../demos/web/scenarios/smoke.yaml) を開いてみます。

```yaml
# End-to-end smoke for the web backend: onboarding -> login -> home -> counter.
scenarios:
  - name: onboard, log in, and increment the counter
    steps:
      - tap: { id: onboarding.start }
      - type: { text: "a@b.com", into: { id: auth.email } }
      - type: { text: "pw", into: { id: auth.password } }
      - tap: { id: auth.submit }
      - wait: { for: { id: home.title }, timeout: 5 }   # 固定 sleep ではなく条件待ち
      - tap: { id: counter.increment }
      - tap: { id: counter.increment }
    expect:
      - exists: { id: home.title }
      - value: { sel: { id: counter.value }, equals: "2" }
```

これは **iOS のスモークテストとバイト単位で同じ step/expect スキーマ**です。そこがこのトラックの要点です。
押さえどころは次の三つです。

- **step が操作し、`expect` が判定します。** `run` は step を実行し、その後は `expect` のアサーション
  だけが合否を決めます。AI も人間の判断も介在しません。これが決定性の境界です（[concepts](concepts.md)）。
- **セレクターは id 優先です**（`{ id: home.title }`）。web ではこれが `data-testid` 属性に解決されます。
  `data-testid` は iOS の `accessibilityIdentifier` に相当するブラウザ側の属性で、他のどのバックエンドとも
  **同じ**セレクター解決コアを通ります（[selectors](selectors.md)、[drivers](drivers.md#playwrightweb)）。
  曖昧なセレクターは、最初に一致した要素をタップするのではなく、即座に失敗します。
- **待機は sleep ではなく条件です**（`wait: { for: …, timeout: 5 }`）。Bajutsu はタイムアウトまで、
  要素が現れるまでポーリングします。

文法の全体（step の種類、待機、アサーションのすべて）は [scenarios](scenarios.md) にあります。

---

## ステップ 4：デモ web アプリを配信する

web デモは、[`demos/web/app`](../../demos/web/README.md) の下に小さな静的アプリを同梱しています。
オンボーディング → ログイン → カウンターという流れを、素の JavaScript と安定した `data-testid` id で
書いたものです。ブラウザ（と Bajutsu）から届くように、ローカルで配信します。

```bash
make -C demos/web app-serve        # demos/web/app を 127.0.0.1:8787 で配信（Ctrl-C で停止）
```

配信したまま、自分のブラウザで <http://127.0.0.1:8787/index.html> を開くと、アプリを手で触れます。
Get started を押し、任意のメールアドレスとパスワードでサインインし、カウンターを増やしてみてください。
これがステップ 3 のシナリオが自動化している流れそのものです。

---

## ステップ 5：ブラウザでシナリオを実行する

一発で済ませる経路は `make` ターゲットです。必要なら web バックエンドを導入し、アプリをバックグラウンドで
配信し、デモのシナリオを Playwright で実行し、サーバーを片付けます。手動での配信は要りません。

```bash
make -C demos/web e2e
```

あるいは、ステップ 4 で配信したアプリに対して CLI を直接動かします（同じ実行を書き下したものです）。

```bash
uv run bajutsu run --scenario demos/web/scenarios/smoke.yaml --target web --backend web --config demos/web/demo.config.yaml
```

フラグの意味は次のとおりです。

- `--target web` は [`demos/web/demo.config.yaml`](../../demos/web/demo.config.yaml) の `targets.web`
  （その `baseUrl` とシナリオディレクトリ）を選びます。ツール自体はアプリ非依存で、ターゲットごとの差分は
  すべて設定に置かれます（[configuration](configuration.md)）。
- `--backend web` はアクチュエーターを選びます。Chromium を操作する Playwright エンジンです。Xcode も
  idb も Simulator も関わりません。
- `--scenario` を省くとターゲットのシナリオディレクトリ全体を実行します（`make -C demos/web e2e` が
  行っているのがこれです）。

成功すると、次のような行が出ます。

```
PASS  runs/20260610-120000/manifest.json
```

`run` は**すべてのシナリオが通れば 0、一つでも失敗すれば 1 を返します**。この終了コードが CI
（継続的インテグレーション）のゲートです（[run-loop](run-loop.md)）。合否はシナリオの機械的な
アサーション（カウンターが `2` を示すこと）だけで決まり、LLM は関わりません。

> 環境の問題（Chromium が未インストール、web extra が未導入など）に当たったら、まず
> `uv run bajutsu doctor --target web --config demos/web/demo.config.yaml` を走らせてください。
> web バックエンドが必要とするものの ✓/✗ チェックリストを表示します。

---

## ステップ 6：レポートを読む

実行のたびに `runs/<runId>/` フォルダ（`runId` は `YYYYMMDD-HHMMSS`）が書き出され、同じ結果を三つの
ビューで持ちます。**iOS トラックと同一の形式**です。

```
runs/20260610-120000/
├── manifest.json     # step と結果の対応。唯一の真実の源
├── junit.xml         # CI 連携（シナリオ 1 = テストケース 1）
└── report.html       # 自己完結の HTML（ブラウザで開く）
```

HTML レポートを開くと、各 step、その結果、取得された証跡（スクリーンショット）がインラインで見えます。

```bash
# Linux
xdg-open runs/<runId>/report.html
# macOS
open runs/<runId>/report.html
```

ターミナルを離れずに、完了した実行をテキストのタイムラインとして確認することもできます。

```bash
uv run bajutsu trace               # runs/ 直下の最新の実行
```

各形式と `runs/` レイアウトの詳細は [reporting](reporting.md) にあります。

---

## 次に読むもの

これで、どのマシンでも通用する完全なループが手に入りました。インストール → ユニットテスト → シナリオ →
ブラウザ実行 → レポートです。同じシナリオ形式はさらに広がります。

- **AI でオーサリングする。** Claude に web アプリをゴールへ向けて探索させ、シナリオを書かせられます
  （Tier 1）。`.env` ファイルに `ANTHROPIC_API_KEY=sk-ant-…` を入れてから、次を実行します。
  ```bash
  make -C demos/web record GOAL="Get started, then increment the counter three times and confirm it shows 3."
  ```
  キーもブラウザもない場合は、オフラインの双子が同じ record ループを決定的に再現します
  （`make -C demos/web record-offline`）。オーサリングループの仕組みは [recording](recording.md) にあります。
- **自分の web アプリをオンボーディングする。** 新しい `targets.<name>` エントリを自分のアプリの
  `baseUrl` に向け、要素に安定した `data-testid` id を付けます。
  [configuration](configuration.md#新しいターゲットのオンボーディング) を参照してください。
- **Mac もある場合は。** [iOS トラック](getting-started.md)が同じループを idb バックエンドで Simulator 上で
  完結させ、同じシナリオがバックエンドを変えてもそのまま動くことを示します。

ここから先は、リファレンスの各ページが個々の要素を詳しく扱います。まず理屈を知るなら [concepts](concepts.md)、
続いて[ドキュメント概要](overview.md)の推奨読書順に進んでください。
