[English](BE-0013-scenario-gui-editor.md) · **日本語**

# BE-0013 — シナリオ GUI エディタ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0013](BE-0013-scenario-gui-editor-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0013") |
| 実装 PR | [#385](https://github.com/bajutsu-e2e/bajutsu/pull/385), [#387](https://github.com/bajutsu-e2e/bajutsu/pull/387) |
| トピック | オーサリング体験 |
<!-- /BE-METADATA -->

## はじめに

シナリオ GUI（グラフィカルユーザーインタフェース）エディタは、シナリオ YAML とアサーション DSL（ドメイン固有言語）を、画面上で視覚的に編集できるようにします。著者がスクリーンショット上で要素を選択すると、エディタがセレクタを解決します。エディタは `doctor` スコアと連携します。

## 動機

シナリオはただの YAML であり、それこそが要点です。AI が最初の下書きを書いた後は、人間がシナリオを所有します。しかし手で編集するには、著者が 2 つを同時に頭に置く必要があります。シナリオ文法（steps、waits、アサーション DSL）と、アプリの安定セレクタです。

最も難しいのはセレクタです。`tap: { id: settings.toggle }` を正しく書くには、その要素の `accessibilityIdentifier` をあらかじめ知っていなければなりません。それは、`doctor` のダンプや実際の要素ツリーを手で読むことを意味します。

`serve` UI（[BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)）は、すでに生の YAML テキストエリアを備えています。本提案は、このテキストエリアを、スクリーンショットを真実の源とする構造化編集へと発展させます。著者が意図する要素をクリックすると、エディタが適切なセレクタを解決して挿入します。`doctor` の規約スコアが、その選択の安定度を示します。人間が所有する編集ループのコストを下げながら、著述をランナー側へ移すことは決してありません。

## 詳細設計

エディタは、新しい画面ではなく、既存の `serve` Web UI 内のシナリオビューの拡張として置きます。連動する 2 つのペインを持ちます。

- シナリオの構造化ビュー（steps とアサーション DSL を、フィールド単位で編集可能にしたもの）。
- 各ステップが操作する画面のスクリーンショット。

YAML は正本のままです。エディタは、既存のシナリオ load/save 経路で同じ `*.yaml` を読み書きします。そのため、エディタ経由の往復と `$EDITOR` での手編集は互換であり、どちらも PR でレビューできます。

### 要素ピッカーは 1 つの共有部品

点から要素、要素からセレクタへ解決する resolver は、**[BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md) が導入するものと同じ部品**です。この部品は 2 つから成ります。

- 純粋コア `bajutsu/capture.py`：`_contains` の frame 包含による `hit_test(elements, point)` と、`resolve_capture(elements, point, namespaces) -> セレクタ + doctor の段 + 曖昧さ`。
- 共有の `serve.js` スクリーンショット・オーバーレイのピッカー。

エディタとキャプチャは*同じ* resolver を呼びます。違うのは、**要素ツリーとスクリーンショットの供給源**だけです。

- **キャプチャ（BE-0012）**：mark 時に取得するライブな `driver.query()` と `driver.screenshot()`。対話的で、起動済みデバイスのセッションが要ります。
- **エディタ（本項目）**：実行がすでに取得した成果物。ステップごとに、`runs/<runId>/<stepId>/elements.json`（`evidence.write_elements`）と `after.png`（`screenshot` の取得種別）です。`stepId` は `<scenarioId>/<step 名または stepN>`（例：`00-s/step0`）です。このキーは `orchestrator/loop.py` が組み立て、`manifest.json` が記録します。エディタは**オフラインで動き、ライブなデバイスは不要で、決定的**です。

BE-0012 と BE-0013 のどちらが先に着地しても、先のものが resolver を導入し、もう一方が再利用します。コピーが 2 つになることはありません。`el → Selector` の安定度ラダーは、現在 `crawl`、`crawl_repro`、capture の 3 か所に実装されています。BE-0012 は、このラダーを 1 つのヘルパへ括り出す計画をすでに持っており、ピッカーはそのヘルパの上に乗ります。

著者がステップのスクリーンショット上の点をクリックします。クライアントは、表示ピクセルを正規化 `[0,1]` に、続いて points に変換します。これは `crawl.Action.perform` の `tap_point` と同じスケーリングです。クライアントがその点を POST し、サーバがそのステップの `elements.json` に対して `resolve_capture` を実行します。

サーバは、最も安定したセレクタを提示します。まず `id` を使い、identifier が無い場合に限って、ラダーを下って（`label` / `traits`）フォールバックします。これはまさに `resolve_unique` と同じ動きです。エディタは、選んだセレクタを `doctor` スコアと共に表示するので、著者は安定した段と脆い段を見分けられます。座標フォールバックは明示されます。

点が複数の要素に解決した場合、ピッカーは**曖昧さを提示**します。黙って 1 つを選ぶことはせず、著者に絞り込み（`within` / `index`）を求めます。これは、ランナーが強制する「曖昧なら失敗」の規則を、著述時に適用するものです。

### 正本 YAML 上の構造化編集

構造化ペインは別モデルではなく、**YAML 上のビュー**です。エディタは、既存の load 経路でシナリオを steps と `expect` アサーションに展開し、フィールド単位で描画します。ピッカーは、解決したセレクタを、著者が編集中のフィールドへ書き込みます。エディタは隠れた状態を持ちません。YAML が唯一の真実なので、構造化ビューと手編集が食い違うことはありません。

各ステップは、その `stepId` で、操作する画面に対応づけます。`stepId` は、実行が `manifest.json` に記録し、その下に evidence ディレクトリを書くキーです。エディタは、位置から経路を推測しません。

著者は**選択した実行の文脈で**シナリオを編集します。選択した実行とは、著者がいま見ているレポートです。シナリオにまだ実行が無い場合、エディタは、対象にできるスクリーンショットのない生フィールド編集に劣化します。編集をブロックはしません。

### 保存経路と継ぎ目

保存は、既存の著者所有の書き込み経路 `serve/scenarios.py:ScenarioScope.save()` を通ります。保存は**書き込み前に `load_scenario_file` で検証**するので、エディタが、ランナーに拒否されるシナリオを永続化することはありません。

変更が触れる箇所は 3 つです。

- **`serve/handler.py` のルート**（FastAPI の `server/app.py` ミラーを含む）：シナリオとその実行のステップごとの成果物を編集用に読み、選んだ点をセレクタへ解決し、保存します。
- **`serve/operations.py` での調整。**
- **`bajutsu/templates/serve.js` の 2 ペイン UI とピッカーのオーバーレイ**：crawl レポートのスクリーンショット・オーバーレイの先例を再利用します。

キャプチャと違い、エディタはリクエストをまたいでライブな driver を**保持しません**。取得済みの成果物をステートレスに読むので、BE-0011 のステートレスな shell-out 方式を保ちます。

### 決定性・app-agnostic・ゲート

エディタは [Tier 1](../../docs/ja/glossary.md#2-つの層) かつ app-agnostic のままです。読むのは、`targets.<name>`（アプリ、そのシナリオディレクトリ、`doctor` スコアへ供給する identifier 名前空間）と、実行がすでに生成する成果物です。選択は構造的（点-in-frame と `doctor` ヒューリスティック）で、**LLM も `ANTHROPIC_API_KEY` も使いません**。決定的な `run` / CI ゲートには一切触れません。エディタは、人が所有する編集ループのコストを下げるだけです。

### 依存と順序

ピッカーの resolver は、BE-0012 の `capture.py` コア（`hit_test` / `resolve_capture`）と、共有の `el → Selector` 安定度ラダーです。BE-0012 はまだ proposal なので、**本項目はそのコアの存在を前提**とします。順序は 2 通りあり、どちらも単一の resolver に帰着します。

- **BE-0012 が先**（自然な順序）：BE-0012 が `capture.py` と統合したラダーを導入します。BE-0013 は両方をそのまま再利用し、自前の（成果物由来の）要素ツリーとスクリーンショットを与えます。
- **BE-0013 が先**：最初のスライスで、現在 `crawl` と `crawl_repro` に重複しているラダーを、`capture.py` の `resolve_capture` へ括り出します。BE-0012 は後で、ライブ採取の経路を同じ関数の上に作ります。

いずれの場合も、resolver の二重実装は生じません。エディタ自身の面（2 ペインのビュー、成果物由来のピッカー、検証付きの保存ルート）は、どちらの順序にも依存しません。したがって、ここでの設計はそのまま成り立ちます。

### エディタ API

エディタは、著者所有の `serve` ルートを 3 本足します（stdlib の `serve/handler.py` と、FastAPI の `server/app.py` ミラー）。3 本ともステートレスです。リクエストをまたいでライブな driver を保持せず、BE-0011 の shell-out モデルを保ちます。

- **編集用ロード**：既存の `GET /api/scenario`（`operations.read_scenario`）を拡張します。選んだ run について、各ステップの取得済み成果物のハンドルも返します。形は `{ yaml, steps: [{ stepId, action, fields, elementsUrl, screenshotUrl }] }` です。URL は、バイト配信済みの `runs/<runId>/<stepId>/elements.json` と `after.png` を指します。run は、著者が見ているレポートそのものです。run が無ければハンドルは null で、ペインは生フィールド編集に退化します。
- **ピックの解決**：`POST /api/scenario/resolve { target, runId, stepId, point: [x, y] }` が、`resolve_capture(elements_of(stepId), point, namespaces_of(target))` を呼びます。返す値は `{ selector, rung, doctorScore, ambiguous, candidates? }` です。`point` は正規化 `[0,1]` で、クライアントが表示ピクセルから正規化へ写像します。倍率は `crawl.Action.perform` がすでに使うものです。サーバはそのステップの `elements.json` を読み、**デバイスも LLM も使わず、構造的に解決します**。曖昧なヒットは `ambiguous: true` と候補を返し、著者が `within` / `index` で絞れるようにします。黙って 1 つを選ぶことはしません。
- **保存**：既存の `POST /api/scenario`（`operations.save_scenario` → `ScenarioScope.save()`）です。書き込み前に `load_scenario_file` で検証します。エディタは構造化ペインを YAML に直列化し、この不変の経路で保存します。したがって、エディタの保存と手編集は同じ書き込みであり、検証はどちらも拒否できます。

### 検証

高速ゲートは、デバイス、ブラウザ、LLM を必要としません。対象は次の 3 つです。

- *resolver の再利用。* 固定の `elements.json` フィクスチャに対し、ある点が、期待どおりの id 優先セレクタとその `doctor` の段に解決します。重なった frame 上の点は、候補付きで `ambiguous` を返します。これらは BE-0012 の resolver テストと同じアサーションです（resolver は 1 つ、テスト面も 1 つです）。
- *保存の検証。* 構造的に編集したが不正なシナリオの保存は、1 バイトも書く前に `load_scenario_file` が拒否します。部分書き込みは残りません。
- *ルートの形。* ロード、解決、保存のハンドラが、フィクスチャ run に対して上記の形を返します。run の無いシナリオは、エラーではなく null の成果物ハンドルへ退化します。

`serve.js` の 2 ペインのオーバーレイ描画は、高速ゲートの対象外です。描画は、既存の serve UI の経路（crawl レポートのスクリーンショット・オーバーレイと同様）で確認します。

## 検討した代替案

* **生の YAML テキストエリアだけに留める。** これは BE-0011 の現状です。到達点としては不採用です。著者は依然としてセレクタを手で知る必要があり、セレクタの安定度に対するフィードバックも得られません。ピッカーと `doctor` スコアこそが付加価値の全てです。
* **YAML を隠す、完全にビジュアルでノーコードのエディタ。** 不採用です。手編集でき、PR でレビューできる正本の YAML は、中核原則です。エディタは YAML を補強するもので、置き換えたり覆い隠したりしてはなりません。
* **ページ内に組み込んだ稼働中の Simulator（動作中アプリ上でピック）。** 初版では不採用です。編集セッションごとに実機 1 台とストリーミング基盤が必要になります。実行がすでに取得したスクリーンショットと要素ツリーに対してピックする方式は、オフラインで安価かつ決定的です。ライブのピッカーは後で導入できます。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[scenarios.md](../../docs/ja/scenarios.md)、[selectors.md](../../docs/ja/selectors.md)。`bajutsu/drivers/base.py`（`_contains`、`resolve_unique`、`Selector` / `Element`）、`bajutsu/doctor.py`（`score`、`ACTIONABLE_TRAITS`）、`bajutsu/evidence.py`（`write_elements` → ステップごとの `elements.json`、`screenshot` 種別 → `after.png`）、`bajutsu/serve/scenarios.py`（`ScenarioScope.save`）、`bajutsu/scenario/load.py`（`load_scenario_file`）、`bajutsu/serve/`（`handler.py` のルーティング、`operations.py`）、`bajutsu/templates/serve.js` と crawl レポートのスクリーンショット・オーバーレイの先例。

**依存・関連項目:** [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)（`serve` ホスト、`ScenarioScope`、本項目が拡張するスクリーンショットの仕組み）、[BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md)（**点 → 要素ピッカー + doctor スコアを共有。解決器は 1 つ、供給源が 2 つ**。エディタは取得済み成果物、キャプチャはライブな driver を読む）、[BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md)（オーサリング面どうしの役割分担）。
