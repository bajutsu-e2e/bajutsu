[English](BE-0012-action-capture-record.md) · **日本語**

# BE-0012 — 操作キャプチャ record

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0012](BE-0012-action-capture-record-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0012") |
| 実装 PR | [#381](https://github.com/bajutsu-e2e/bajutsu/pull/381), [#382](https://github.com/bajutsu-e2e/bajutsu/pull/382) |
| トピック | オーサリング体験 |
<!-- /BE-METADATA -->

## はじめに

操作キャプチャ record は、Simulator 上での実際のユーザー操作（tap / type / swipe）を、AI を使わずに直接シナリオへ変換します。idb には入力イベントを*観測*する手段がないため、キャプチャは代理操作（proxy actuation）で駆動します。著者は `serve` の Web UI で、画面のスクリーンショット上の一点をクリックして各操作を指示します。Bajutsu はその点を安定したセレクタへ解決し、著者に代わって操作を実行します。

## 動機

現在、「人が行う操作」からシナリオへ至る道は AI record ループ（`record.py`）だけです。自然言語のゴールがエージェントを駆動し、エージェントがアプリを探索してステップを提案します。意図からの著述には強力ですが、著者が常に払えるとは限らないコストがあります。

- `ANTHROPIC_API_KEY` が必要です。
- LLM（大規模言語モデル）の往復を消費します。
- 著者がすでに体で覚えているフローでは、ゴールとして言語化してエージェントを待つより、ただ*やってみせる*ほうが速くなります。

記述するより実演するほうが容易なフロー（精密なスワイプ、複数フィールドのフォーム、正確なタップ順）もあります。Simulator 上の実操作を直接シナリオステップへ変換するキャプチャモードは、著者に高速でオフラインかつ決定的な入口を与えます。AI ループを置き換えるのではなく、補完します。

## 詳細設計

キャプチャは、起動中のデバイス上で人が行う操作（tap / type / swipe）を観測します。AI ループが生成するのと同じ `Scenario`（steps + `expect`）を出力するので、下流（`run`、`codegen`、レポート）は一切変わりません。

難しいのは決定性です。生のイベントストリームは*座標*の列ですが、prime directive は、シナリオが座標ではなく安定した `accessibilityIdentifier` で選択することです。そこでキャプチャは、各操作を、その瞬間に取得した新鮮な `query()` に対して解決します。可能な限り `id` セレクタとして書き出します。ランナーが強制する規則も適用します。曖昧なセレクタは、最初にマッチしたものをタップせず失敗させます。

この設計は、新しい仕組みを作るというより、コードベースがすでに持つ部品の**組み立て**です。

- **点-in-frame のヒットテスト**はすでに存在します。`drivers/base.py:_contains(outer, inner)` が frame の包含判定を行い、`Element["frame"]` は points 単位の `(x, y, w, h)` です。アクセシビリティツリーは平坦で、「親」は幾何的に決まります。したがって、点から要素へのヒットテストが自然なモデルです。サイズ 0 の内側 box を渡せば、`_contains` を再利用できます。
- **安定度ラダー**は、すでに `crawl.py:Action.as_selector()` と `crawl_repro.py:_selector()` に `id` → `label`（+ `index`）として符号化されています。座標タップはこのラダーの段ではなく、別種の `tap_point` action で表されます。`crawl_repro._selector()` は、id も label も無い要素には `None` を返し、ステップを出しません（「忠実に再現できないなら出さない」）。キャプチャも同じ立場を取り、ラダーを `label` で止めます。id も label も無い要素へのタップは、座標へ落とさず拒否します（決定 1）。
- **決定性の強制**は `drivers/base.py:resolve_unique` です。一致が 0 件（不在）または 2 件以上（曖昧）なら例外を上げます。キャプチャは同じ規則を*著述*の段階で適用します。
- **安定度スコア**は `doctor.score(elements, id_namespaces)` です。キャプチャはこれを再利用し、解決したセレクタの安定度を示します。[BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md) のピッカーも、同じ `doctor` 連携を必要とします。
- **正規化座標 `[0,1]`** が、ブラウザでのスクリーンショットのクリックと、要素ツリーの points 空間を橋渡しします。正規化座標は、`crawl.py:_screen_size`、`Action.perform` の `tap_point` スケーリング、`Node.targets` に現れます。
- **ステップ出力**は、`Step` / `TypeText` / `Swipe` と `serialize.dump_scenario_file` を再利用します。著者が所有する書き出し経路は `serve/scenarios.py:ScenarioScope.save()` / `.authored()` です。AI ループも同じ経路で書き出します（自動命名し、上書きしません）。

### キャプチャの流れ（操作ごと）

1. **スナップショット。** backend が新鮮な `driver.query()`、`driver.screenshot(path)`、`_screen_size(driver)` を取得します。保存したスクリーンショットを `serve` の UI に表示します。
2. **指示。** 著者がスクリーンショット上で、一点（tap / type）をクリックするか、二点間（swipe）をドラッグします。ブラウザは、クリックを表示ピクセル空間から正規化 `[0,1]` へ、続いて points 空間へ変換します。これは `Action.perform` の `tap_point` スケーリングと同じです。変換結果をブラウザが POST します。
3. **ヒットテスト。** サーバが `_contains` を使い、その点を frame に含む要素をすべて求めます。そのうち**最も具体的で操作可能な**要素、つまり `doctor.ACTIONABLE_TRAITS` のうち frame が最小のものを選びます。結果は、背後の画面いっぱいの window ではなくボタンです。
4. **解決と検証。** サーバが安定度ラダーの方針でセレクタを組み立て、`resolve_unique` で一意性を検証します。セレクタが一意でなければ、キャプチャは競合する要素を著者に示して**曖昧さを提示**し、何も出力しません。推測はしません。
5. **採点。** サーバが `doctor.score` を実行し、選んだ段（安定した `id`、または `label` フォールバック）を示します。著者は著述しながら、選択の安定度を確認できます。指定できる要素に届かないタップは、座標へ落とさず、この時点で拒否します（決定 1）。
6. **代理操作。** サーバが driver を通じて操作を実行します（`tap(sel)` / `type_text` / `swipe`）。`type` の値は著者が UI で入力したものです（決定 3）。キャプチャは、シリアライズの前に対象の `Redact` 設定で値を redaction します（決定 4）。操作は実物なので、次のスナップショットはアプリの真の新状態を反映します。
7. **出力。** サーバが解決した `Step` を作成中のシナリオへ追記します。ステップごとに再シリアライズして `save()` するので、著者は常に現在の YAML を所有します。

### 操作の種類ごと

- **tap** → `Step(tap=sel)`。
- **type** → `driver.tap(sel)` でフィールドにフォーカスし、`type_text` を呼びます。キャプチャは `Step(type=TypeText(text, into=sel))` を出力します。テキストは UI の小さな入力欄から得ます。キャプチャは、タップと同様にキーストロークも*観測*できないためです（決定 3）。著者が値を入力し、キャプチャがそれを代理入力します。その値は秘密情報かもしれないため、ステップへシリアライズする前に、対象の `Redact` 設定を通します（決定 4）。秘密情報が redaction されないまま、著述済みの YAML に入り込むことはありません。
- **swipe** → 二点のクリック。キャプチャは、正規化座標で `Step(swipe=Swipe(from_, to))` を出力します。これは既存の規約で、再実行時にスケールされます。両端が 1 つの要素に解決するなら、より安定な `Swipe(on=sel, direction=…)` へ格上げします。

### セレクタ選択の方針（厳密）

方針は次の順で適用します。

1. `id` があれば、一意性を検証して出力します。画面内で id が重複していれば、曖昧さを提示します（`doctor` の `Blocked` 状態です）。
2. なければ、`label` があって一意なら出力します。label が一意でなければ、`index` を足して解消を試み、フレーキーとして印を付けます。それでも駄目なら、曖昧さを提示します。
3. `id` も `label` も無ければ、**キャプチャを拒否**し、その要素には `accessibilityIdentifier` が必要だと著者に伝えます（決定 1、「忠実に再現できないなら出さない」）。

座標 tap のステップ型は、意図的に**設けません**。`Step` は `tap: Selector` だけを持つので、`id` でも `label` でも指定できないタップが、座標として書き出されることはありません。これは、指定できないタップを出さずに飛ばす `crawl_repro` の方針と一致します。

`el → Selector` のラダーは、現状 `crawl` と `crawl_repro` の 2 箇所で実装されています。キャプチャを実装すると 3 つ目のコピーになります。本提案では、別の小さなリファクタとして、ラダーを 1 つの共有ヘルパへ集約することを推奨します。

### タイミングは固定 sleep ではなく条件待機

ある操作が、直前と fingerprint（`crawl.py:fingerprint`）の異なる画面に着地したとします。このときキャプチャは、次の操作が対象とする最初の要素への `wait` を挿入します。AI ループは `record._settle_step` で同じ自己完結性を得ています。したがって、再実行が実時間のタイミングに依存することはありません。

### 配置

キャプチャは本質的に対話的（指示 → 操作 → 観測 → 指示）であり、著者がクリックするスクリーンショットを*必要とします*。そのため、新しい CLI コマンドではなく `serve` に属します。構成は 2 つです。

- **新しい純粋モジュール `bajutsu/capture.py`** に、driver にも HTTP にも依存しないコアを置きます。モジュールは、`hit_test(elements, point)`、`resolve_capture(elements, point, namespaces)`（セレクタ、`doctor` の段、曖昧さ）、`step_for_{tap,type,swipe}` を提供します。これは、`record.py` と `crawl_repro.py` がロジックをテスト可能に保つ方針と同じです。`make check` の中で、fake な要素ツリーに対して Simulator なしで単体テストできます。
- **薄い serve 層**として、`serve/handler.py` のルート（例：`POST /api/capture/{start,mark,finish}`）と `serve/operations.py` の op を足します。

serve 層は、構造上の 1 点で、[BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md) の stateless な shell-out から逸脱します。キャプチャは、起動中の対象に対して、リクエストを跨いで保持する**ライブな `Driver`** を必要とします（決定 2）。

- `start` が 1 つの `Driver` を開き、`ServeState` に保存します。
- `mark` のたびに、その `Driver` を再利用します。
- `finish`（または切断）が `Driver` を閉じ、スロットをクリアします。
- **同一対象に対する 2 つ目の同時キャプチャは、単一セッションの保証によって拒否します**。1 台のデバイスは、2 つの交錯するセッションを同時には扱えないためです。
- 操作呼び出し（デバイスに触れる境界）は、純粋コアの外に保ちます。

このライフサイクルは、`ServeState` がすでに持つ、寿命の長い差し替え可能なシーム（`executor`、`sessions`）の持ち方をなぞっています。キャプチャ driver は、そうしたスロットがもう 1 つ増えるだけで、セッションの間だけ保持します。

resolver と emitter は、小さなインターフェースの背後に置きます。こうすれば、本物のイベントソースを、resolver と emitter を変えずに、後から代理入力と差し替えられます（決定 3）。候補は、idb のイベント取得と、XCUITest backend（[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)）が記録するイベントです。著者の UI でのテキスト入力は、いまの emitter への入力にすぎません。本物のキーストロークストリームがあれば、同じ emitter に流し込めます。

### 決定性・app 非依存・ゲート

上記のすべての段は、構造的です（点-in-frame、安定度ラダー、`resolve_unique`、`doctor.score`）。**モデル呼び出しも `ANTHROPIC_API_KEY` もありません**。これは、`record.py` の `agent.next_action` / `_plan_goal` との鋭い対比です。キャプチャは厳密に [Tier 1](../../docs/ja/glossary.md#2-つの層) です。AI ループと同じく、シナリオを著述するものであり、決定的な `run` / CI ゲートに**いかなる経路も持ち込みません**。キャプチャは、既存コマンドと全く同様に `targets.<name>`（対象アプリ、シナリオディレクトリ、redaction）を読み、同じ方法で著述済みの YAML を書き出します。したがって、app と backend に依存しないままです。

### テスト戦略

テストは Linux の `make check` ゲートに収まり、Simulator は不要で、モックは最小限です。fake な driver と要素ツリーは、挙動のモックではなく実テストデータです。

- **純粋コアのテスト**は、`Element` のリテラルリストに対して書きます。対象は次のとおりです。
  - `hit_test` が、最も小さい包含する操作可能要素を選ぶこと。
  - セレクタラダーが、id と label + index で働くこと。
  - id も label も無いタップを拒否し、ステップを出さないこと（決定 1）。
  - 曖昧さを提示し、ステップを出さないこと。
  - `doctor` の段を報告すること。
  - `type` と `swipe` のステップを出力すること。
  - `type` の redaction が、設定された秘密の値を、出力ステップに届く前にマスクすること（決定 4）。
  - 画面跨ぎの settle `wait` を挿入すること。
  - 出力した `Scenario` が、`dump_scenario_file` と `load_scenario_file` で round-trip すること。
- **`FakeDriver`** で代理操作を検証します。指定可能な要素では `tap(sel)` を使うことをアサートします。指定できないタップは、操作せずに「accessibilityIdentifier を足してください」という拒否を上げることも、アサートします。
- **serve op のテスト**は、HTTP 越しではなく operations 層で、fake なセッションを与えて行います。

### スコープと非目標

**スコープ内**：

- `serve` でのスクリーンショットクリックによる、tap / type / swipe の代理操作キャプチャ。
- 安定度ラダーと `doctor` スコアによる、構造的なセレクタ解決。
- 曖昧さの提示。
- 指定できないタップの拒否（決定 1）。
- `ServeState` に保持するセッションごとのライブ driver と、単一セッションの保証（決定 2）。
- `type` の取得元としての、UI のテキスト入力（決定 3）。
- シリアライズ時の、入力値の redaction（決定 4）。
- 画面跨ぎの settle wait。
- 著者が所有する YAML への、ステップの逐次反映。

**非目標**：

- 本物のタッチイベントの観測。idb のイベント取得、または XCUITest backend（[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)）に依存します。
- `expect` やアサーションの推定。意図の推定は AI ループの仕事です。[BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md) の enrichment 方針を参照してください。
- 構造化された GUI エディタ（[BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)）。
- マルチタッチ、pinch、rotate のキャプチャ。idb は単一タッチだからです。
- キャプチャを `record` コマンドへ統合すること（BE-0014）。

### 決定

以下の 4 点は草案の段階では未解決でしたが、いずれも決着しました。決着した内容は、すでに上記の各節へ織り込んであります。

1. **座標 tap の `Step` は設けない（「忠実に再現できないなら出さない」）。** キャプチャは、`id` も `label` も持たない要素へのタップを**拒否**します。拒否のメッセージは、座標で指定したステップを出力する代わりに、その要素へ `accessibilityIdentifier` を足すよう著者に伝えます。シナリオスキーマに座標 tap のステップ型は**追加しません**。`Step` は `tap: Selector` だけを持ちます。**根拠**：座標タップは、レイアウト、デバイスサイズ、翻訳のいずれが変わっても壊れ、著述済みの YAML に非決定性を持ち込みます。指定できないタップについて、`crawl_repro` がすでに同じ立場を取っています（`bajutsu/crawl_repro.py`）。
2. **ライブ driver を `ServeState` にセッションの間だけ保持する。** キャプチャセッションは、対象に対して**ただ 1 つ**のライブ `Driver` を開き、セッションの間 `ServeState` に保持します。`finish` または切断でクリーンアップします。**同一対象に対する 2 つ目の同時キャプチャは、単一セッションの保証で拒否します**。**根拠**：キャプチャは、指示 → 操作 → 観測という状態を持つループなので、リクエストを跨いで生き続ける driver が要ります。これが BE-0011 の stateless な shell-out からの唯一の逸脱です。また、1 台のデバイスは、2 つの交錯するキャプチャセッションを同時には扱えません。
3. **`type` のテキストは、著者が入力する UI 欄から得る。** キャプチャはキーストロークを観測できません。idb が入力イベントのストリームを提供しないためです。著者が `serve` の UI の小さな入力欄に値を入力し、キャプチャがそれをフォーカス中のフィールドへ代理入力します。**根拠**：今日得られる取得元は、これだけです。resolver / emitter のインターフェースのおかげで、本物のイベントソース（BE-0019）が、同じシームの背後でこの入力を後から置き換えられます。resolver や emitter を変える必要はありません。
4. **redaction はキャプチャ時に適用する。** 入力された `type` の値は秘密情報かもしれません。キャプチャはステップをシリアライズするとき、対象の `Redact` 設定を通します（`bajutsu/redaction.py` を再利用します）。これにより、秘密情報が redaction されないまま、著述済みの YAML に入り込むことはありません。**根拠**：著述したシナリオはコミットされ、共有されます。キャプチャは、秘密情報がシナリオへ最初に入る地点です。したがって、書き出し時にマスクしなければなりません。マスクには、evidence のパイプラインがすでに従っている `Redact` 設定と同じものを使います。

## 検討した代替案

* **生の座標タップを記録し、そのまま再生する。** 即座に不採用です。座標再生は、レイアウト、デバイスサイズ、翻訳のいずれが変わっても壊れ、選択による決定性に反します。各タップをキャプチャ時に安定 `id` へ解決することこそが要点です。
* **タップ点を最前面または最初にマッチした要素へ黙って解決する。** 不採用です。「最初にマッチしたものをタップする」を再導入してしまいます。点が曖昧なときは、`resolve_unique` と整合するよう、解消のために著者へ曖昧さを提示しなければなりません。
* **本物のイベント取得 backend を待ってからキャプチャを出す。** 本物のイベントストリーム（idb のイベント取得、または XCUITest backend が記録するイベント）があれば、著者は Web UI で点を示す代わりに、Simulator を直接操作できます。ブロッカーとしては不採用です。idb は今日そのようなストリームを提供せず、XCUITest backend（BE-0019）自体が未実装です。その backend を前提にすると、この機能全体が先送りになります。代理操作は、オフラインかつ API キー不要の経路を今すぐ出せます。resolver / emitter のインターフェースのおかげで、本物のイベントソースは後から書き換えなしに差し込めます。
* **キャプチャを既存の AI `record` ループの入力モードとして取り込む。** ありえますが、BE-0014 へ先送りします。BE-0014 が、両方式の役割分担と相互変換を定義します。本提案は、キャプチャの仕組みそのものだけを範囲とします。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[DESIGN §6.5](../../DESIGN.md)。`bajutsu/record.py`（AI ループ、`_settle_step`、スクリーンショットの配管）、`bajutsu/drivers/base.py`（`_contains`、`resolve_unique`、`Selector` / `Element`）、`bajutsu/crawl.py`（`Action.as_selector` / `Action.perform` / `_screen_size`、正規化された `Node.targets`）、`bajutsu/crawl_repro.py`（`_selector`、「忠実に再現できないなら出さない」方針）、`bajutsu/doctor.py`（`score`、`ACTIONABLE_TRAITS`）、`bajutsu/scenario/models`（`Step` / `TypeText` / `Swipe`）と `bajutsu/scenario/serialize.py`（`dump_scenario_file`）、`bajutsu/serve/`（`handler.py` のルーティング、`operations.py`、`scenarios.py` の `ScenarioScope`）、`bajutsu/templates/serve.js` と `crawl.html.j2`（スクリーンショット + オーバーレイの先例）。

**依存 / 関連項目**：[BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)（本提案が拡張する `serve` のホスト、`ScenarioScope`、スクリーンショットの配管）、[BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)（点 → 要素のピッカーと doctor スコアを共有します。ピッカーは 1 つの共有部品にすべきです）、[BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md)（AI ループとの役割分担と、キャプチャ → アサーションの enrichment）、[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)（同じ resolver / emitter インターフェースの背後に入る、将来の本物のイベントソース）。
