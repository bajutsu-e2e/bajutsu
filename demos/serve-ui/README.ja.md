# serve Web UI の Dogfood（Playwright backend）

[English](README.md)

Bajutsu が**自分自身**の `serve` Web UI をテストします。テスト対象は serve のシングルページアプリで、
**Playwright** backend（[BE-0041](../../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）で
駆動します。[demos/web](../web) と同じく **Mac も Simulator も不要**で、`make check` と同じツールチェーンの
Linux 上で動きます。Web UI の決定的なリグレッション網であり、合否は機械的なアサーションだけから決まり、LLM は
関与しません。

## 構成

| パス | 役割 |
|---|---|
| `dogfood.config.yaml` | `targets.webui`（`baseUrl`（起動中の serve）＋ `backend: [web]`、`bundleId` なし） |
| `scenarios/shell-navigation.yaml` | 上部タブ（Record / Replay / Crawl / Author / Stats / Coverage）が表示中のビューを入れ替える |
| `scenarios/modals.yaml` | config ブラウザと Settings パネルの開閉。AI provider は明示選択が必要（既定なし。選ぶまで Save は拒否される） |
| `scenarios/config-sources.yaml` | config モーダルが三つのバインド元（`--root` を一覧するファイルブラウザ、Git 指定、バンドルのアップロード）をすべて提示する |
| `scenarios/replay-contract.yaml` | 束ねた config が Replay のピッカーに届く（config → `/api/targets` → `/api/scenarios`） |
| `scenarios/replay-tabs.yaml` | Replay の Run / History タブが左パネルを入れ替える |
| `scenarios/replay-tools.yaml` | 選択中のシナリオに determinism grade が付き（BE-0145）、codegen が Playwright テストとして書き出し（BE-0137）、readiness パネル（BE-0148）が提示される |
| `scenarios/record-form.yaml` | Record の Save はシナリオが存在するまで disabled。goal フィールドは入力を受け取る |
| `scenarios/author-modes.yaml` | Author の Capture / Edit / Enrich モード切り替えが各モードのコントロールを出し分ける |
| `scenarios/author-editor.yaml` | Edit モードの Load が YAML エディタを埋めて grade を付ける。不正な YAML はインラインの problems パネルに出る（BE-0138） |
| `scenarios/stats.yaml` | Stats ビューが run のダッシュボードを読み込む（BE-0102） |
| `scenarios/coverage.yaml` | Compute がターゲットの coverage マップを描画する（BE-0146） |
| `scenarios/platform-ui.yaml` | Replay パネルは非 iOS backend のとき iOS 用デバイス UI（simulators / workers / erase）を隠す |
| `scenarios/panel-resize.yaml` | タイル区切り一本のドラッグは隣接する二枚だけを再配分し、三枚目に触れない |
| `package.json` / `playwright.config.ts` | **生成された**ネイティブ Playwright スペック用のハーネス（後述） |
| `Makefile` | `web-deps` / `serve-ui` / `e2e` / `codegen` / `e2e-playwright` |

## 実行

```bash
make -C demos/serve-ui e2e
```

これは web backend（`uv sync --extra web` ＋ `playwright install chromium`）を入れ、`bajutsu serve` を起動し
（[web デモ](../web)の config を読ませ、ドロップダウンに実在のアプリとシナリオが並ぶようにします）、Tier A の
シナリオで Web UI を Playwright backend から駆動し、serve を畳みます。serve は `make serve` ではなく直接起動
します。dogfood は Web 限定で、idb companion も iOS の actuator も関わらないからです。

Web UI を手で触るには、`make -C demos/serve-ui serve-ui` を実行して <http://127.0.0.1:8799/> を開きます。

## 同じ網をネイティブ Playwright テストとして（CI）

```bash
make -C demos/serve-ui e2e-playwright   # Node が必要
```

`bajutsu codegen --emit playwright`（BE-0137）が、上のシナリオ全部をネイティブな `@playwright/test` の
スペックとして `playwright-tests/` に書き出します（gitignore 対象。毎回 YAML から再生成するので、
シナリオが唯一のソースのまま保たれます）。ハーネス（`playwright.config.ts`）が内側の serve を自分で
起動するため、テスト対象は `e2e` が駆動するものと同じ自己完結の構成です。CI は serve UI に触れる PR ごとに
これを実行します（[`.github/workflows/serve-ui-e2e.yml`](../../.github/workflows/serve-ui-e2e.yml)）。
bajutsu 実行の `e2e` はローカルの dogfood として残ります。同じフローを bajutsu 自身が record し replay する
デモです。

## コアとの対応

Web UI のコントロールは `data-testid` 属性を持ちます。iOS の accessibilityIdentifier に相当する Web 側の id で、
ビューごとに名前空間を切っています（`nav.*`、`view.*`、`record.*`、`replay.*`、`crawl.*`、`author.*`、
`stats.*`、`coverage.*`、`settings.*`、`config.*`、`upload.*`）。
シナリオの `{ id: nav.replay }` という selector は、ほかの backend と**同じ** `resolve_unique` / `find_all` の
決定性コアで解決します。

シナリオは、LLM も device もなしに決定性コアが確かめられることだけをアサートします。どの `<main>` ビューと
どのモーダルが存在するか（SPA は `hidden` 属性で切り替えるので、Playwright backend はアクティブなものだけを
見ます）、ボタンが enabled か disabled か、フィールドやピッカーが持つ値です。これにより、この dogfood は
**Tier 2** にとどまります。AI 駆動の Record と Crawl の run（モデルと device が要る）は、LLM を run/CI ゲート
から締め出すのと同じ規則で、ここでは対象外です。

二つの状態は、意図して間接的にアサートします。レポート系のペイン（Stats、Coverage、run のレポート）は
**shadow root** に描画され、要素クエリはその中を覗けません。そこで該当シナリオは、light DOM の
プレースホルダ（`stats.empty` / `coverage.empty`）がツリーから*消える*ことをアサートします。これは
「描画パスが走った」ことと正確に一致します。また Record の Generate ボタンは Claude への到達可能性
（BE-0101）でゲートされ、これはホスト側の状態なので、存在だけをアサートし、enabled かどうかは
アサートしません。

## スコープ（現状）

web backend は座標でタップするため、ネイティブの `<select>` ドロップダウンを操作できません。これらのシナリオは、
オプションを切り替えるのではなく、ページ読み込み時の**状態**（ドロップダウン操作を要しません）をアサート
します。`<select>` の駆動と AI の往復は、dogfood のロードマップ項目で今後の課題として追跡します。
