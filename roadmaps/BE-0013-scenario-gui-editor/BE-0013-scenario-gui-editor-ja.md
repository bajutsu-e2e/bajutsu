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

シナリオ YAML とアサーション DSL を画面上で直接編集できます。スクリーンショット上で要素を選択してセレクタを確定し、doctor スコアと連携します。

## 動機

シナリオはただの YAML であり、それこそが要点です。AI が最初の下書きを書いた後は人間が所有します。しかし手で編集するには、著者は 2 つを同時に頭に置く必要があります。シナリオ文法（steps、waits、アサーション DSL）と、アプリの安定セレクタです。最も難しいのはセレクタです。`tap: { id: settings.toggle }` を正しく書くには、その要素の `accessibilityIdentifier` をあらかじめ知っていなければならず、それは `doctor` のダンプや実際の要素ツリーを手で読むことを意味します。`serve` UI（BE-0011）はすでに生の YAML テキストエリアを露出しています。本提案はそれを、スクリーンショットを真実の源とする構造化編集へと発展させます。意図する要素をクリックすれば、エディタが適切なセレクタを解決して挿入し、`doctor` の規約スコアがその選択の安定度を示します。著述を決してランナー側へ移すことなく、人間が所有する編集ループのコストを下げます。

## 詳細設計

エディタは新しい画面ではなく、既存の `serve` Web UI 内のシナリオビューの拡張として置きます。連動する 2 ペインを持ちます。シナリオの構造化ビュー（steps とアサーション DSL を、フィールド単位で編集可能に）と、各ステップが操作する画面のスクリーンショットです。YAML は正本のままです。エディタは既存のシナリオ load/save 経路で同じ `*.yaml` を読み書きするため、エディタ経由の往復と `$EDITOR` での手編集は互換で、PR でレビューできます。

### 要素ピッカーは 1 つの共有部品

点 → 要素 → セレクタの解決器は、**[BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md) が導入するものと同じ**です。その純粋コア `bajutsu/capture.py`（`_contains` の frame 包含による `hit_test(elements, point)`、および `resolve_capture(elements, point, namespaces) -> セレクタ + doctor のラング + 曖昧性`）と、共有の `serve.js` スクリーンショット・オーバーレイのピッカーから成ります。エディタとキャプチャは*同じ*解決器を呼び、違うのは**要素ツリーとスクリーンショットの供給源**だけです。

- **キャプチャ（BE-0012）**：mark 時のライブな `driver.query()` + `driver.screenshot()`。対話的で、booted なデバイスのセッションが要ります。
- **エディタ（本項目）**：実行がすでに取得した成果物。ステップごとに `runs/<runId>/<stepId>/elements.json`（`evidence.write_elements`）と `after.png`（`screenshot` の取得種別）です。`stepId` は `<scenarioId>/<step 名または stepN>`（例: `00-s/step0`。`orchestrator/loop.py` が組み立て `manifest.json` に記録する鍵）です。**オフラインで、ライブなデバイスは不要、決定的**です。

したがって BE-0012 / BE-0013 のどちらが先に着地しても、先のものが解決器を導入し、もう一方が再利用します。2 つのコピーにはなりません。（`el → Selector` の安定度ラダーは現在 `crawl` / `crawl_repro` / capture の 3 か所にあり、BE-0012 がすでに 1 つのヘルパへ括り出す計画です。ピッカーはその上に乗ります。）

ステップのスクリーンショット上の点をクリックすると、表示ピクセル → 正規化 `[0,1]` → points（`crawl.Action.perform` の `tap_point` と同じスケーリング）に変換して POST し、サーバはそのステップの `elements.json` に `resolve_capture` をかけます。最も安定したセレクタを提示し、`id` を第一に、identifier が無い場合のみラダーを下って（`label` / `traits`）フォールバックします。まさに `resolve_unique` と同じです。選んだセレクタはその `doctor` スコアと共に表示され、安定したラングか脆いものかが分かります（座標フォールバックは明示されます）。点が複数の要素に解決した場合、ピッカーは**曖昧さを表面化**し、黙って 1 つを選ぶのではなく著者に絞り込み（`within` / `index`）を求めます。これは、ランナーが強制する「曖昧なら失敗」を著述時に表面化させるものです。

### 正本 YAML 上の構造化編集

構造化ペインは別モデルではなく、**YAML 上のビュー**です。エディタは既存の load 経路でシナリオを steps + `expect` アサーションに展開してフィールド単位で描画し、ピッカーは解決したセレクタを、著者が編集中のフィールドへ書き込みます。隠れたエディタ状態はありません。YAML が唯一の真実なので、構造化ビューと手編集が食い違うことはありません。

各ステップは、その `stepId`（`<scenarioId>/<step 名または stepN>`。実行が `manifest.json` に記録し、その下に evidence ディレクトリを書く鍵）で、それが操作する画面に対応づけます。位置から推測しません。著者は**選択した実行の文脈で**シナリオを編集します。いま見ているレポートです。シナリオにまだ実行が無い場合は、ブロックするのではなく、生フィールド編集（対象にできるスクリーンショットなし）に劣化します。

### 保存経路と継ぎ目

保存は既存の著者所有の書き込み経路 `serve/scenarios.py:ScenarioScope.save()` を通り、**書き込み前に `load_scenario_file` で検証**します。そのため、ランナーが拒否するシナリオを永続化することはありません。具体的には、ルートを `serve/handler.py`（FastAPI の `server/app.py` ミラーも）に置き、シナリオとその実行のステップごとの成果物を編集用に読み、選んだ点をセレクタへ解決し、保存します。調整は `serve/operations.py`、2 ペインの UI とピッカーのオーバーレイは `bajutsu/templates/serve.js`（crawl レポートのスクリーンショット・オーバーレイの先例を再利用）です。キャプチャと違い、エディタはリクエストをまたいでライブな driver を**保持しません**。取得済み成果物をステートレスに読むので、BE-0011 のステートレスなシェルアウト方式を保ちます。

### 決定性・app-agnostic・ゲート

エディタは Tier 1 かつ app-agnostic を保ちます。読むのは `targets.<name>`（アプリ、そのシナリオディレクトリ、`doctor` スコアへ供給する identifier 名前空間）と、実行がすでに生成する成果物です。選択は構造的（点-in-frame と `doctor` ヒューリスティック）で、**LLM も `ANTHROPIC_API_KEY` も使いません**。決定的な `run` / CI ゲートには一切触れず、人が所有する編集ループのコストを下げるだけです。

### 依存と順序

ピッカーのリゾルバは BE-0012 の `capture.py` コア（`hit_test` ／ `resolve_capture`）と、共有の
`el → Selector` 安定度ラダーです。BE-0012 はまだ proposal なので、**本項目はそのコアの存在を前提**とします。
順序は二通りあり、どちらも単一のリゾルバに帰着します。

- **BE-0012 が先**（自然）：BE-0012 が `capture.py` と統合したラダーを導入し、BE-0013 はそれをそのまま再利用して、
  自前の（成果物由来の）要素ツリーとスクリーンショットを与えます。
- **BE-0013 が先**：最初のスライスでラダー（現在 `crawl` ／ `crawl_repro` に重複）を `capture.py` の
  `resolve_capture` へ括り出し、BE-0012 が後でそのライブ採取の経路を同じ関数の上に作ります。

いずれの場合もリゾルバの二重実装は生じません。エディタ自身の面（二枚ペインのビュー、成果物由来のピッカー、
検証付き保存ルート）はどちらの順序にも依存しないので、以下の設計はそのまま成り立ちます。

### エディタ API

3 本の author 所有の `serve` ルート（stdlib の `serve/handler.py` と FastAPI の `server/app.py` ミラー）で、
いずれもステートレスです。リクエストをまたいでライブドライバを保持せず、BE-0011 の shell-out モデルを保ちます。

- **編集用ロード** — 既存の `GET /api/scenario`（`operations.read_scenario`）を拡張し、選んだ run について
  各ステップの採取済み成果物のハンドルも返します。`{ yaml, steps: [{ stepId, action, fields, elementsUrl,
  screenshotUrl }] }` で、URL はバイト配信済みの `runs/<runId>/<stepId>/elements.json` と `after.png` を指します。
  run は著者が見ているレポートそのものです。run が無ければハンドルは null で、ペインは raw フィールド編集に
  退化します。
- **ピックの解決** — `POST /api/scenario/resolve { target, runId, stepId, point: [x, y] }` →
  `resolve_capture(elements_of(stepId), point, namespaces_of(target))` を実行し、
  `{ selector, rung, doctorScore, ambiguous, candidates? }` を返します。`point` は正規化 `[0,1]`
  （クライアントが表示ピクセル → 正規化に写像。`crawl.Action.perform` が既に使う倍率）で、サーバはその
  ステップの `elements.json` を読み**構造的に解決します。端末も LLM も使いません**。曖昧なヒットは
  `ambiguous: true` と候補を返し、著者が `within` ／ `index` で絞れるようにします。黙って 1 つ選ぶことは
  しません。
- **保存** — 既存の `POST /api/scenario`（`operations.save_scenario` → `ScenarioScope.save()`）で、書き込み前に
  `load_scenario_file` で検証します。エディタは構造化ペインを YAML に直列化してこの不変の経路で保存するので、
  エディタの保存と手編集は同じ書き込みであり、同じように弾かれます。

### 検証

高速ゲート（端末・ブラウザ・LLM 不要）：

- *リゾルバの再利用。* 固定の `elements.json` フィクスチャに対し、ある点が期待どおりの id 優先 selector とその
  `doctor` rung に解決し、重なった frame 上の点は候補付きで `ambiguous` を返すこと。BE-0012 のリゾルバの
  テストと同じアサーションです（リゾルバは 1 つ、テスト面も 1 つ）。
- *保存の検証。* 構造的に編集したが不正なシナリオの保存が、1 バイトも書く前に `load_scenario_file` で
  弾かれること（部分書き込みなし）。
- *ルートの形。* ロード ／ 解決 ／ 保存のハンドラがフィクスチャ run に対して上記の形を返し、run の無いシナリオは
  エラーではなく null の成果物ハンドルへ退化すること。

`serve.js` の二枚ペインのオーバーレイ描画は、高速ゲートではなく既存の serve UI の経路（crawl レポートの
スクリーンショットオーバーレイと同様）で確認します。

## 検討した代替案

* **生の YAML テキストエリアだけに留める。** これは BE-0011 の現状です。到達点としては不採用です。依然として著者がセレクタを手で知る必要があり、セレクタの安定度に対するフィードバックも得られません。ピッカーと `doctor` スコアこそが付加価値の全てです。
* **YAML を隠す、完全にビジュアルでノーコードのエディタ。** 不採用です。YAML が正本で、手編集でき、PR でレビューできる成果物であることは中核原則です。エディタは YAML を補強するもので、置き換えたり覆い隠したりしてはなりません。
* **ページ内に組み込んだ稼働中の Simulator（動作中アプリ上でピック）。** 初版では不採用です。編集セッションごとに実機 1 台とストリーミング基盤が必要になります。実行がすでに取得したスクリーンショットと要素ツリーに対してピックする方式は、オフラインで安価かつ決定的です。ライブのピッカーは後で導入できます。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[scenarios.md](../../docs/ja/scenarios.md)、[selectors.md](../../docs/ja/selectors.md)。`bajutsu/drivers/base.py`（`_contains`、`resolve_unique`、`Selector` / `Element`）、`bajutsu/doctor.py`（`score`、`ACTIONABLE_TRAITS`）、`bajutsu/evidence.py`（`write_elements` → ステップごとの `elements.json`、`screenshot` 種別 → `after.png`）、`bajutsu/serve/scenarios.py`（`ScenarioScope.save`）、`bajutsu/scenario/load.py`（`load_scenario_file`）、`bajutsu/serve/`（`handler.py` のルーティング、`operations.py`）、`bajutsu/templates/serve.js` と crawl レポートのスクリーンショット・オーバーレイの先例。

**依存・関連項目:** [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)（`serve` ホスト、`ScenarioScope`、本項目が拡張するスクリーンショットの仕組み）、[BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md)（**点 → 要素ピッカー + doctor スコアを共有。解決器は 1 つ、供給源が 2 つ**。エディタは取得済み成果物、キャプチャはライブな driver を読む）、[BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md)（オーサリング面どうしの役割分担）。
