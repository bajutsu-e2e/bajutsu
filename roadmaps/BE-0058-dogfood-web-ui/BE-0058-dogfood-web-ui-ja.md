[English](BE-0058-dogfood-web-ui.md) · **日本語**

# BE-0058 — serve Web UI の Dogfood（web backend のリグレッション網）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0058](BE-0058-dogfood-web-ui-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0058") |
| 実装 PR | [#169](https://github.com/bajutsu-e2e/bajutsu/pull/169), [#742](https://github.com/bajutsu-e2e/bajutsu/pull/742) |
| トピック | Dogfood フィクスチャ（Web UI） |
| 関連 | [BE-0189](../BE-0189-serve-ui-dogfood-ci-gate/BE-0189-serve-ui-dogfood-ci-gate-ja.md) |
| 由来 | Dogfooding |
<!-- /BE-METADATA -->

## はじめに

ローカルの `serve` Web UI（[BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)）自体が Web アプリなので、Web（Playwright）backend（[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）で駆動できます。本項目は、Bajutsu に自分自身の Web UI をテストさせます。

成果物は、決定的な Tier 2 のリグレッション網です。配信中のシングルページアプリを、ほかのシナリオと同じ `run` 経路と決定性コアで駆動します。どのゲートにも LLM 呼び出しを足しません。

この網は、iOS のショーケースのフィクスチャ（[BE-0045](../BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps-ja.md)）に対応する、Web 側の仕組みです。2 つの部分からなります。目的に合わせて整えたテスト対象（Web UI と、それを駆動可能にする `data-testid` の id）と、そのテスト対象を動かすシナリオ群です。

最初のスライスでは、3 つを用意します。

- Web UI のコントロールへの `data-testid` の付与。
- `serve` を起動して web backend の run を向ける、`demos/serve-ui/` のハーネス。
- ナビゲーション、モーダル、config からピッカーへの contract、フォーム状態、プラットフォームに応じた表示を確かめる、Tier A の決定的シナリオ。

## 動機

**1. Web UI にはテストされていない実体があり、静かに退行します。** `serve` は、3 つのビュー（Record、Replay、Crawl）、2 つのモーダル（config ブラウザと Settings）、プロバイダに応じて切り替わる設定パネル、Replay のピッカーを駆動する config→apps→scenarios の contract を備えるに至りました。Python のテストは、HTTP 層（`bajutsu/serve/operations.py`）と、index が資産をインライン化することを押さえています。**描画された SPA を駆動するものは、1 つもありません。** タブの切り替え、モーダル、ピッカーの結線を壊すリファクタリングが、今日のゲートを通ってしまいます。実 UI に対する決定的な run が、この穴を塞ぎます。

**2. web backend を最も安く、最も誠実に証明します。** `demos/web` は、書きやすさを狙った小さな静的ページで Playwright backend を確かめます。Web UI は私たちが所有する、現実の、成長し続けるアプリです。web backend が本物の UI を駆動する証拠として、はるかに優れています。調達コストもかかりません。Mac も Simulator もモデルも要らず、`make check` と同じツールチェーンの中で、Linux 上のヘッドレスで動きます。自分たちのプロダクトで backend を dogfood することが、backend が通用するという最も強い根拠になります。

**3. ユーザーに求める id の規律を、自分たちのコードで示します。** selector の安定性は、決定性の要です（[DESIGN §2/§5](../../DESIGN.md)）。Web UI には、iOS の `accessibilityIdentifier` に相当する Web 側の id として `data-testid` を与えます。これは Web UI を行儀のよいテスト対象にします。ツールが報いようとする規律を、自分たちのコードで実演もします。BE-0045 と同じ対照実験の精神を、Web 側で行うものです。

**4. すべての prime directive を尊重します**（[CLAUDE.md](../../CLAUDE.md)）。シナリオは純粋な Tier 2 です。合否は、機械的なアサーションだけから決まります。どのビューやモーダルが存在するか、ボタンの enabled と disabled、フィールドの値です。モデルは関与しません。Web UI は `baseUrl` の背後にあるもう 1 つのアプリにすぎず、1 つの `apps.<name>` エントリで取り込みます。app 非依存を保ち、アプリ固有の差分は config に置きます。

## 詳細設計

### テスト対象：Web UI への `data-testid`

[`bajutsu/templates/serve.html.j2`](../../bajutsu/templates/serve.html.j2) のコントロールに、領域ごとに名前空間を切った `data-testid` を与えます。`nav.*`（トップタブ、Open config、Settings、テーマ）、`view.*`（3 つの `<main>` ビュー）、`record.*`、`replay.*`、`crawl.*`、`settings.*`、`config.*` です。

web backend は、シナリオの `{ id: nav.replay }` という selector を、`data-testid` に解決します。iOS が `accessibilityIdentifier` を解決するのと**同じ** `resolve_unique` と `find_all` のコアを使います。違いは、driver が読む属性だけです（[drivers.md](../../docs/ja/drivers.md)）。

### ハーネス：`demos/serve-ui/`

ハーネスは、`demos/web` を写したものです。`dogfood.config.yaml` は、`baseUrl`（起動中の `serve`）と `backend: [web]` を持つ `apps.webui` を宣言します。

`Makefile` の `e2e` ターゲットは、3 つのことを順に行います。まず、`serve` をバックグラウンドで起動します。ドロップダウンに実在のアプリとシナリオが並ぶよう、`demos/web` の config を読ませます。次に、Web UI を Playwright backend で駆動します。最後に、`serve` を畳みます。ターゲットは `serve` を `make serve` 経由ではなく直接起動します。dogfood は Web 限定で、idb companion も iOS の actuator も関わらないからです。

### 表示と非表示が確かめられる仕組み

SPA は、各ビューとモーダルを HTML の `hidden` 属性（`display:none`）で切り替えます。Playwright backend の DOM 走査は、`display:none` とサイズ 0 のノードを要素ツリーから落とします。したがって、`data-testid` を持つコンテナに対する `exists` と `exists … negate` の組が、どのビューやモーダルが今アクティブかをそのまま表します。スクリーンショットの比較も、待ち時間の当て推量も要りません。

### Tier A のシナリオ目録（本項目）

| シナリオ | 確かめること |
|---|---|
| `shell-navigation` | 3 つのトップタブが表示中の `<main>` を入れ替える（アクティブは存在、ほかは消える） |
| `modals` | config ブラウザと Settings パネルが開閉する。Settings は既定で Anthropic の節を表示し、Bedrock のフィールドは隠れる |
| `replay-contract` | 束ねた config が Replay のピッカーに届く（`/api/apps` → `/api/scenarios`）。既定のアプリとシナリオの値が config の宣言どおりになる |
| `record-form` | Record の Save はシナリオが存在するまで disabled のまま。goal フィールドは入力を受け取る |
| `platform-ui` | Replay パネルの iOS 用デバイス UI（simulators / workers / erase）は iOS backend のときだけ表示し、`web` を選ぶと隠れる |

### ティア分け（この網に入るもの、入らないもの）

- **Tier A（決定的、本項目）。** フロントエンドの挙動と、読み取りと contract の面を覆います。`demos/web` と同じく、`make -C demos/serve-ui e2e` のターゲットとして、Linux の CI ジョブに載せられます。core の `make check` はブラウザを持たないので、これらは実行しません。
- **Tier B（ここでは対象外）。** AI 駆動の Record と Crawl の run には、モデルと device が要ります。これらは Tier 1 で、第一の prime directive により、どのゲートにも入れません。決定的な Replay の run 往復は可能です。UI を駆動して `demos/web` の smoke を実行し、レポートを確かめます。ただし、最初のブラウザの中に 2 つ目のブラウザが入れ子になるので、見送ります。

### 既知の制約：ネイティブの `<select>`

web backend は座標でタップするので、ネイティブの `<select>` ドロップダウンを操作できません。Tier A のシナリオは、オプションを切り替えず、ページが読み込む**既定**の選択をアサートします。config からピッカーへの連鎖が、最初のアプリのシナリオを自動で読み込むので、ドロップダウンの操作は要りません。

`<select>` を駆動するには、web driver に意味的な `selectOption` capability が必要です。この capability は、[BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md)（web backend の完成）に属します。それまでは、ドロップダウンの切り替えに依存するシナリオ（たとえば、プロバイダを Bedrock にする）を、この網の外に置きます。

### スコープと段階

- **今回のスコープ：** `data-testid` の id、`demos/serve-ui` のハーネス、5 つの Tier A シナリオファイルです。web backend を通して、Linux 上で今日動きます。
- **今後：** `<select>` の操作（BE-0054 とともに）、要素の入った Replay の History のアサート（コミットした fixture run が要ります）、決定的な Replay run の往復、`demos/web` と並べて CI ジョブに載せることです。

## 検討した代替案

- **Bajutsu ではなく、JavaScript のスタック（Playwright Test や Jest）で Web UI をテストする。** 却下しました。狙いは dogfood で、Bajutsu が自分の決定性コアで自分の UI をテストすることです。別の JS テストスタックは、selector とアサーションのモデルを二重化します。プロジェクトが堅牢化すべき web backend について、何も裏づけません。
- **BE-0041 か BE-0045 に畳み込む。** 却下しました。BE-0041 は backend（enabler）で、BE-0045 は iOS の対象です。本項目は、別個の Web の対象であり、リグレッション網です。BE-0045 の一部ではなく、その兄弟にあたります。
- **`selectOption` を、本項目で web driver に足す。** スコープの観点で却下しました。これは、Driver プロトコルとシナリオスキーマの変更で、web backend の完成（BE-0054）に属します。ページが既定の選択を自動で読み込むので、これがなくても dogfood は十分に価値があります。
- **`data-testid` をやめ、可視テキストや role で選ぶ。** 却下しました。コントロールの多くは、アイコンのみ（テーマ、更新、ズーム）か、ラベルを共有します。テキストや role の selector は曖昧になります。曖昧な selector は、設計上失敗します（[DESIGN §2](../../DESIGN.md)）。安定した id が誠実な解で、ツールが報いる規律でもあります。

## 進捗

- [x] 最初のスライスを出荷しました。`data-testid` の id、`demos/serve-ui` のハーネス、Tier A の
  シナリオです。[#169](https://github.com/bajutsu-e2e/bajutsu/pull/169)
- [x] カバレッジを完成させました。[#742](https://github.com/bajutsu-e2e/bajutsu/pull/742) 残りのビュー
  （Author / Stats / Coverage）と新しい Replay のツール（audit バッジ、codegen、doctor）のシナリオを
  揃え、完成した `docs/web-ui.md` に対する機能カバレッジマップを dogfood の README に置きました（意図して
  覆わないものとその理由も列挙）。補助的な修正として、ネイティブ checkbox の `checked` を `selected`
  トレイトとして読むようにし（テーマ切替の状態をアサート可能に）、serve UI の描画不具合三つ（空の grade
  バッジ、shadow に取り残されるプレースホルダ、見切れる Author の load 行）を直しました。ハーネスの CI
  ゲートは [BE-0189](../BE-0189-serve-ui-dogfood-ci-gate/BE-0189-serve-ui-dogfood-ci-gate-ja.md) で別途
  追跡します。

## 参考

- [BE-0041 Web（Playwright）backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)：enabler
- [BE-0045 Dogfood ショーケースアプリ群](../BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps-ja.md)：本項目が写す、対応する iOS 側
- [BE-0011 ローカル Web UI（`bajutsu serve`）](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)：テスト対象
- [BE-0054 Web backend の完成](../BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md)：`<select>` 操作の置き場所
- [`demos/serve-ui`](../../demos/serve-ui)：ハーネス。[`demos/web`](../../demos/web)：ハーネスが写す元のパターン
- [DESIGN §2 / §5 / §7.1](../../DESIGN.md)：決定性、安定性のはしご、アプリごとの取り込み
