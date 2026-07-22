[English](../../getting-started/web.md) · **日本語**

# Getting started（web トラック。Mac 不要）

> [Getting started](index.md) のループを、**どの OS でも**、実際の web アプリに対してブラウザ上で
> **Playwright** backend を使って完結させます。macOS も Xcode も iOS Simulator も要りません。
> Mac もある場合は、代わりに[iOS トラック](ios.md)で同じループを Simulator 上で完結できます。

関連: [Getting started](index.md) · [iOS トラック](ios.md) · [drivers](../drivers.md) · [用語集](../glossary.md)

まず[共通ウォークスルーのステップ 1〜3](index.md)（インストール、ユニットテスト、シナリオを読む）を
済ませてください。ステップ 1 は基本のインストールを扱い、このページは web backend が追加で必要と
する 1 行だけを足します。このページはステップ 4 から続きます。

## なぜ web トラックがあるのか

Bajutsu の看板の主張は**「プラットフォームは backend にすぎない」**です。決定的コア、シナリオ形式、
レポーターは、UI を実際に操作する backend が何であっても同じです。[iOS トラック](ios.md)は
Simulator（`XCUITest` backend）で完結しますが、これには Xcode 入りの Mac が必要です。このトラックは
同じループをブラウザ（`web` / Playwright backend）で完結させます。ブラウザなら Linux でも Windows
でも macOS でも同じように動きます。これは、Bajutsu 自身の web デモを Linux のゲート上で動かしている
のと同じ backend です（[`demos/web`](../../../demos/web/README.md)）。

ここでの用語は[用語集](../glossary.md)に従います。**backend** は `--backend` に渡すトークン（ここでは
`web`）で、これが **actuator**（タップや入力を実際に行う Playwright エンジン）に解決されます。
**target** はテスト対象アプリを記述する `targets.<name>` の設定エントリ一つで、web の場合は iOS の
`bundleId` ではなく `baseUrl` で識別されます。

## 必要なもの

| 目的 | 必要なもの |
|---|---|
| ステップ 1〜3（共通） | 任意の OS、Python 3.13（[uv](https://github.com/astral-sh/uv) で管理） |
| 以下のステップ 4〜5 | `uv sync --extra web` + `uv run playwright install chromium`（Mac / Simulator 不要） |

```bash
uv sync --extra web                        # Playwright の Python パッケージを追加
uv run playwright install chromium         # Playwright が操作する Chromium バイナリを取得
```

`--extra web` の行と `playwright install` の行が、[ステップ 1](index.md#ステップ-1インストール) の
基本インストールに web backend が上乗せする分です。Chromium バイナリは pip の依存ではなく、別途
ダウンロードされます。

## ステップ 4：デモ web アプリを配信する

web デモは、[`demos/web/app`](../../../demos/web/README.md) の下に小さな静的アプリを同梱しています。
オンボーディング → ログイン → カウンターという流れを、素の JavaScript と安定した `data-testid` id で
書いたものです。ブラウザ（と Bajutsu）から届くように、ローカルで配信します。

```bash
make -C demos/web app-serve        # demos/web/app を 127.0.0.1:8787 で配信（Ctrl-C で停止）
```

配信したまま、自分のブラウザで <http://127.0.0.1:8787/index.html> を開くと、アプリに手で触れます。
Get started を押し、任意のメールアドレスとパスワードでサインインし、カウンターを増やしてみてください。
これがデモのシナリオが自動化している流れそのものです。

```yaml
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

これは[共通ウォークスルーの iOS 側の smoke テスト](index.md#ステップ-3シナリオを読む)と**まったく
同じ step/expect の文法**を使っています。steps が操作し `expect` が判定すること、セレクタが id 優先
であること（ここでは `data-testid`。iOS の `accessibilityIdentifier` に相当する web 側の属性で、
他のどの backend とも**同じ**セレクタ解決コアを通ります。[selectors](../selectors.md)、
[drivers](../drivers.md#playwrightweb) 参照）、待機が条件であること、曖昧なセレクタが即座に失敗する
ことも同じです。トラックごとに違うのは個々のシナリオの中身だけです。全文は
[`demos/web/scenarios/smoke.yaml`](../../../demos/web/scenarios/smoke.yaml) にあります。

## ステップ 5：ブラウザでシナリオを実行する

一発で済ませる経路は `make` ターゲットです。必要なら web backend を導入し、アプリをバックグラウンドで
配信し、デモのシナリオを Playwright で実行し、サーバーを片付けます。手動での配信は要りません。

```bash
make -C demos/web e2e
```

あるいは、ステップ 4 で配信したアプリに対して CLI を直接動かします（同じ実行を書き下したものです）。

```bash
uv run bajutsu run --scenario demos/web/scenarios/smoke.yaml --target web --backend web --config demos/web/demo.config.yaml
```

フラグの意味は次のとおりです。

- `--target web` は [`demos/web/demo.config.yaml`](../../../demos/web/demo.config.yaml) の `targets.web`
  （その `baseUrl` とシナリオディレクトリ）を選びます。ツール自体はアプリ非依存で、target ごとの差分は
  すべて config に置かれます（[configuration](../configuration.md)）。
- `--backend web` は actuator を選びます。Chromium を操作する Playwright エンジンです。Xcode も
  Simulator も関わりません。
- `--scenario` を省くと target のシナリオディレクトリ全体を実行します（`make -C demos/web e2e` が
  行っているのがこれです）。

成功すると、次のような行が出ます。

```
PASS  runs/20260610-120000/manifest.json
```

`run` は**すべてのシナリオが通れば 0、一つでも失敗すれば 1 を返します**。この終了コードが CI
（継続的インテグレーション）のゲートです（[run-loop](../run-loop.md)）。合否はシナリオの機械的な
アサーション（カウンターが `2` を示すこと）だけで決まり、LLM は関わりません。

> 環境の問題（Chromium が未インストール、web extra が未導入など）に当たったら、まず
> `uv run bajutsu doctor --target web --config demos/web/demo.config.yaml` を走らせてください。
> web backend が必要とするものの ✓/✗ チェックリストを表示します。

共通ウォークスルーの[ステップ 6：レポートを読む](index.md#ステップ-6レポートを読む)へ進んでください
（macOS の `open` の代わりに Linux では `xdg-open` を使います）。

## AI でオーサリングする（web）

Claude に web アプリをゴールへ向けて探索させ、シナリオを書かせられます（Tier 1）。`.env` ファイルに
`ANTHROPIC_API_KEY=sk-ant-…` を入れてから、次を実行します。

```bash
make -C demos/web record GOAL="Get started, then increment the counter three times and confirm it shows 3."
```

キーもブラウザもない場合は、オフラインの双子が同じ record ループを決定的に再現します
（`make -C demos/web record-offline`）。オーサリングループの仕組みは [recording](../recording.md) にあります。

## 自分の web アプリをオンボーディングする

新しい `targets.<name>` エントリを自分のアプリの `baseUrl` に向け、要素に安定した `data-testid` id を
付けます。[configuration](../configuration.md#新しいターゲットのオンボーディング) を参照してください。
