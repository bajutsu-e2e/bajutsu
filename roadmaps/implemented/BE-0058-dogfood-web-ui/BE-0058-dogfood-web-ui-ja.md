[English](BE-0058-dogfood-web-ui.md) · **日本語**

# BE-0058 — serve Web UI の Dogfood（web backend のリグレッション網）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0058](BE-0058-dogfood-web-ui-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| 実装 PR | [#169](https://github.com/bajutsu-e2e/bajutsu/pull/169) |
| トラック | [可決済み](../../README-ja.md#可決済み) |
| トピック | Dogfood フィクスチャ（Web UI） |
| 由来 | Dogfooding |
<!-- /BE-METADATA -->

## はじめに

ローカルの `serve` Web UI（[BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)）自体が
Web アプリなので、Web（Playwright）backend（[BE-0041](../../proposals/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）で
駆動できます。本項目は、Bajutsu に自分自身の Web UI をテストさせます。配信中のシングルページアプリを、
ほかのシナリオと同じ `run` 経路、同じ決定性コアで駆動する、決定的な Tier 2 のリグレッション網です。iOS の
ショーケース（[BE-0045](../BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps-ja.md)）に
対応する Web 側で、目的に合わせて整えたテスト対象（Web UI と、それを駆動可能にする `data-testid` の id）と、
それを動かすシナリオ群からなります。どのゲートにも LLM 呼び出しを足しません。

最初のスライスでは 3 つを用意します。Web UI のコントロールへの `data-testid` 付与、`serve` を起動して web
backend の run を向ける `demos/serve-ui/` のハーネス、そしてナビゲーション、モーダル、config からピッカーへの
contract、フォーム状態、プラットフォームに応じた表示を確かめる Tier A の決定的シナリオです。

## 動機

**1. Web UI にはテストされていない実体があり、静かに退行します。** `serve` は 3 つのビュー（Record / Replay /
Crawl）、2 つのモーダル（config ブラウザ、Settings）、プロバイダに応じて切り替わる設定パネル、そして Replay
のピッカーを駆動する config→apps→scenarios の contract を備えるに至りました。Python のテストは HTTP 層
（`bajutsu/serve/operations.py`）と、index が資産をインライン化することは押さえていますが、描画された SPA を
駆動するものは 1 つもありません。タブ切り替えやモーダル、ピッカーの結線を壊すリファクタリングが、今日の
ゲートを通ってしまいます。実 UI に対する決定的な run が、この穴を塞ぎます。

**2. web backend を最も安く、最も誠実に証明します。** `demos/web` は、書きやすさを狙った小さな静的ページで
Playwright backend を確かめます。Web UI は私たちが所有する現実の、成長し続けるアプリで、web backend が本物の
UI を駆動する証拠としてははるかに優れています。しかも調達コストはかかりません。Mac も Simulator もモデルも
要らず、`make check` と同じツールチェーンの中で Linux 上のヘッドレスで動きます。自分たちのプロダクトで
backend を dogfood することが、それが通用するという最も強い根拠になります。

**3. ユーザーに求める id の規律を、自分たちのコードで示します。** selector の安定性は決定性の要です
（[DESIGN §2/§5](../../../DESIGN.md)）。Web UI に `data-testid`（iOS の `accessibilityIdentifier` に相当する
Web 側の id）を与えることは、それを行儀のよいテスト対象にすると同時に、ツールが報いようとする規律を自分たちの
コードで実演します。BE-0045 と同じ対照実験の精神を、Web 側で行うものです。

**4. すべての prime directive を尊重します**（[CLAUDE.md](../../../CLAUDE.md)）。シナリオは純粋な Tier 2 で、
合否は機械的なアサーション（どのビューやモーダルが存在するか、ボタンの enabled / disabled、フィールドの値）
だけから決まり、モデルは関与しません。Web UI は `baseUrl` の背後にあるもう 1 つのアプリにすぎず、1 つの
`apps.<name>` エントリで取り込みます。app 非依存で、アプリ固有の差分は config に置きます。

## 詳細設計

### テスト対象：Web UI への `data-testid`

[`bajutsu/templates/serve.html.j2`](../../../bajutsu/templates/serve.html.j2) のコントロールに、領域ごとに
名前空間を切った `data-testid` を与えます。`nav.*`（トップタブ、Open config、Settings、テーマ）、`view.*`
（3 つの `<main>` ビュー）、`record.*`、`replay.*`、`crawl.*`、`settings.*`、`config.*` です。web backend は、
シナリオの `{ id: nav.replay }` という selector を、iOS が `accessibilityIdentifier` を解決するのと同じ
`resolve_unique` / `find_all` コアで `data-testid` に解決します。driver が読む属性が違うだけです。

### ハーネス：`demos/serve-ui/`

`demos/web` を写したものです。`dogfood.config.yaml` は、`baseUrl`（起動中の `serve`）と `backend: [web]` を
持つ `apps.webui` を宣言します。`Makefile` の `e2e` ターゲットは、`serve` をバックグラウンドで起動し
（ドロップダウンに実在のアプリとシナリオが並ぶよう、`demos/web` の config を読ませます）、Web UI を Playwright
backend で駆動し、`serve` を畳みます。`serve` は `make serve` 経由ではなく直接起動します。dogfood は Web 限定で、
idb companion も iOS の actuator も関わらないからです。

### 表示・非表示が確かめられる仕組み

SPA は各ビューとモーダルを HTML の `hidden` 属性（`display:none`）で切り替え、Playwright backend の DOM 走査は
`display:none` やサイズ 0 のノードを要素ツリーから落とします。したがって、`data-testid` を持つコンテナに対する
`exists` と `exists … negate` の組が、どのビューやモーダルが今アクティブかをそのまま表します。スクリーン
ショット比較も、待ち時間の当て推量も要りません。

### Tier A のシナリオ目録（本項目）

| シナリオ | 確かめること |
|---|---|
| `shell-navigation` | 3 つのトップタブが表示中の `<main>` を入れ替える（アクティブは存在、ほかは消える） |
| `modals` | config ブラウザと Settings パネルが開閉する。Settings は既定で Anthropic の節を表示し、Bedrock のフィールドは隠れる |
| `replay-contract` | 束ねた config が Replay のピッカーに届く（`/api/apps` → `/api/scenarios`）。既定のアプリとシナリオの値が config の宣言どおりになる |
| `record-form` | Record の Save はシナリオが存在するまで disabled のまま。goal フィールドは入力を受け取る |
| `platform-ui` | Replay パネルの iOS 用デバイス UI（simulators / workers / erase）は iOS backend のときだけ表示し、`web` を選ぶと隠れる |

### ティア分け（この網に入るもの、入らないもの）

- **Tier A（決定的、本項目）**：フロントエンドの挙動と、読み取りと contract の面です。`demos/web` と同じく
  Linux の CI ジョブに載せられます（core の `make check` ではなく、`make -C demos/serve-ui e2e` のターゲット
  です。`make check` はブラウザを持ちません）。
- **Tier B（ここでは対象外）**：AI 駆動の Record と Crawl の run（モデルと device が要る）は Tier 1 で、第一の
  prime directive により決してゲートに入れません。決定的な Replay の run 往復（UI を駆動して `demos/web` の
  smoke を実行し、レポートを確かめる）も可能ですが、最初のブラウザの中に 2 つ目のブラウザが入れ子になります。
  これは見送ります。

### 既知の制約：ネイティブの `<select>`

web backend は座標でタップするため、ネイティブの `<select>` ドロップダウンを操作できません。Tier A の
シナリオは、オプションを切り替えるのではなく、ページが読み込む既定の選択（config からピッカーへの連鎖が最初の
アプリのシナリオを自動で読み込み、ドロップダウン操作を要しません）をアサートします。`<select>` の駆動には web
driver の意味的な `selectOption` capability が要り、これは
[BE-0054](../../proposals/BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md)（web backend の完成）に
属します。それまでは、ドロップダウンの切り替えに依存するシナリオ（たとえばプロバイダを Bedrock にする）は、
この網の外に置きます。

### スコープと段階

- **今回のスコープ**：`data-testid` の id、`demos/serve-ui` のハーネス、5 つの Tier A シナリオファイルです。
  web backend を通して Linux 上で今日動きます。
- **今後**：`<select>` の操作（BE-0054 とともに）、要素の入った Replay の History のアサート（コミットした
  fixture run が要る）、決定的な Replay run 往復、そして `demos/web` と並べて CI ジョブに載せることです。

## 検討した代替案

- **Bajutsu ではなく JavaScript のスタック（Playwright Test / Jest）で Web UI をテストする。** 却下します。
  狙いは dogfood であり、Bajutsu が自分の決定性コアで自分の UI をテストすることにあります。別の JS テスト
  スタックは selector とアサーションのモデルを二重化し、ハードニングが本来の仕事である web backend について
  何も裏づけません。
- **BE-0041 か BE-0045 に畳み込む。** 却下します。BE-0041 は backend（enabler）、BE-0045 は iOS の対象で、
  本項目は別個の Web の対象でありリグレッション網です。BE-0045 の一部ではなく、その兄弟にあたります。
- **`selectOption` を本項目で web driver に足す。** スコープの観点で却下します。これは Driver プロトコルと
  シナリオスキーマの変更で、web backend の完成（BE-0054）に属します。ページが既定の選択を自動で読み込むので、
  それなしでも dogfood は十分に価値があります。
- **`data-testid` をやめ、可視テキストや role で選ぶ。** 却下します。コントロールの多くはアイコンのみ
  （テーマ、更新、ズーム）か、ラベルを共有するため、テキストや role の selector は曖昧になります。曖昧な
  selector は設計上失敗します（[DESIGN §2](../../../DESIGN.md)）。安定した id が誠実な解であり、ツールが報いる
  規律でもあります。

## 参考

- [BE-0041 Web（Playwright）backend](../../proposals/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)：enabler
- [BE-0045 Dogfood ショーケースアプリ群](../BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps-ja.md)：対応する iOS 側
- [BE-0011 ローカル Web UI（`bajutsu serve`）](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)：テスト対象
- [BE-0054 Web backend の完成](../../proposals/BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md)：`<select>` 操作の置き場所
- [`demos/serve-ui`](../../../demos/serve-ui)：ハーネス。[`demos/web`](../../../demos/web)：写した元
- [DESIGN §2 / §5 / §7.1](../../../DESIGN.md)：決定性、安定性のはしご、アプリごとの取り込み
