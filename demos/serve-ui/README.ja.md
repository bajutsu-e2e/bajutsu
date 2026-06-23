# serve Web UI の Dogfood（Playwright backend）

[English](README.md)

Bajutsu が**自分自身**の `serve` Web UI をテストします。テスト対象は serve のシングルページアプリで、
**Playwright** backend（[BE-0041](../../roadmaps/in-progress/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）で
駆動します。[demos/web](../web) と同じく **Mac も Simulator も不要**で、`make check` と同じツールチェーンの
Linux 上で動きます。Web UI の決定的なリグレッション網であり、合否は機械的なアサーションだけから決まり、LLM は
関与しません。

## 構成

| パス | 役割 |
|---|---|
| `dogfood.config.yaml` | `targets.webui`（`baseUrl`（起動中の serve）＋ `backend: [web]`、`bundleId` なし） |
| `scenarios/shell-navigation.yaml` | Record / Replay / Crawl タブが表示中のビューを入れ替える |
| `scenarios/modals.yaml` | config ブラウザと Settings パネルの開閉。AI provider は明示選択が必要（既定なし。選ぶまで Save は拒否される） |
| `scenarios/replay-contract.yaml` | 束ねた config が Replay のピッカーに届く（config → `/api/targets` → `/api/scenarios`） |
| `scenarios/record-form.yaml` | Record の Save はシナリオが存在するまで disabled。goal フィールドは入力を受け取る |
| `scenarios/platform-ui.yaml` | Replay パネルは非 iOS backend のとき iOS 用デバイス UI（simulators / workers / erase）を隠す |
| `Makefile` | `web-deps` / `serve-ui` / `e2e` |

## 実行

```bash
make -C demos/serve-ui e2e
```

これは web backend（`uv sync --extra web` ＋ `playwright install chromium`）を入れ、`bajutsu serve` を起動し
（[web デモ](../web)の config を読ませ、ドロップダウンに実在のアプリとシナリオが並ぶようにします）、Tier A の
シナリオで Web UI を Playwright backend から駆動し、serve を畳みます。serve は `make serve` ではなく直接起動
します。dogfood は Web 限定で、idb companion も iOS の actuator も関わらないからです。

Web UI を手で触るには、`make -C demos/serve-ui serve-ui` を実行して <http://127.0.0.1:8799/> を開きます。

## コアとの対応

Web UI のコントロールは `data-testid` 属性を持ちます。iOS の accessibilityIdentifier に相当する Web 側の id で、
ビューごとに名前空間を切っています（`nav.*`、`view.*`、`record.*`、`replay.*`、`settings.*`、`config.*`）。
シナリオの `{ id: nav.replay }` という selector は、ほかの backend と**同じ** `resolve_unique` / `find_all` の
決定性コアで解決します。

シナリオは、LLM も device もなしに決定性コアが確かめられることだけをアサートします。どの `<main>` ビューと
どのモーダルが存在するか（SPA は `hidden` 属性で切り替えるので、Playwright backend はアクティブなものだけを
見ます）、ボタンが enabled か disabled か、フィールドやピッカーが持つ値です。これにより、この dogfood は
**Tier 2** にとどまります。AI 駆動の Record と Crawl の run（モデルと device が要る）は、LLM を run/CI ゲート
から締め出すのと同じ規則で、ここでは対象外です。

## スコープ（現状）

web backend は座標でタップするため、ネイティブの `<select>` ドロップダウンを操作できません。これらのシナリオは、
オプションを切り替えるのではなく、ページ読み込み時の**状態**（ドロップダウン操作を要しません）をアサート
します。`<select>` の駆動と AI の往復は、dogfood のロードマップ項目で今後の課題として追跡します。
