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

Simulator 上でユーザーが行った実操作（tap / type / swipe）を記録し、AI を使わずにシナリオへ変換します。idb には入力イベントを*観測*する手段がないため、キャプチャは代理操作（proxy actuation）で駆動します。著者が `serve` の Web UI 上で画面のスクリーンショットの一点をクリックして次の操作を指示すると、bajutsu がその点を安定したセレクタへ解決し、著者に代わって操作を実行します。

## 動機

現在、「人が行う操作」からシナリオへ至る道は AI record ループ（`record.py`）だけです。自然言語のゴールがエージェントを駆動し、エージェントがアプリを探索してステップを提案します。意図からの著述には強力ですが、著者が常に払えるとは限らないコストがあります。`ANTHROPIC_API_KEY` が必要で、LLM（大規模言語モデル）の往復を消費し、著者がすでに体で覚えているフローでは、それをゴールとして言語化してエージェントを待つより、ただ *やってみせる* 方が速いのです。さらに、記述するより実演する方が容易なフロー（精密なスワイプ、複数フィールドのフォーム、正確なタップ順）もあります。Simulator 上の実操作を直接シナリオステップへ変換するキャプチャモードは、著者に高速でオフラインかつ決定的な入口を与え、AI ループを置き換えるのではなく補完します。

## 詳細設計

キャプチャは、起動中のデバイス上で人が行う操作（tap / type / swipe）を観測し、AI ループが生成するのと同じ `Scenario`（steps + `expect`）を出力します。したがって下流（`run`、`codegen`、レポート）は一切変わりません。難しいのは決定性です。生のイベントストリームは *座標* の列であり、シナリオは座標ではなく安定した `accessibilityIdentifier` で選択するという prime directive があります。そこで各キャプチャアクションは、その操作の瞬間に取得した新鮮な `query()` に対して解決し、可能な限り `id` セレクタとして書き出します。ランナーが強制する「曖昧なら最初にマッチしたものをタップせず失敗させる」と同じ規則を、著述の段階で適用します。

この設計は、新しい仕組みを作るというより、コードベースがすでに持つ部品の**組み立て**です。

- **点-in-frame のヒットテスト**はすでに存在します。`drivers/base.py:_contains(outer, inner)` が frame の包含判定を行い、`Element["frame"]` は points 単位の `(x, y, w, h)` です。アクセシビリティツリーは平坦で「親」は幾何的に決まるので、点から要素へのヒットテストが自然なモデルです（サイズ 0 の内側 box を渡せば `_contains` を再利用できます）。
- **安定度ラダー**はすでに `crawl.py:Action.as_selector()` と `crawl_repro.py:_selector()` に `id` → `label`（+ `index`）として符号化されています。座標タップはこのラダーの段ではなく、別種の `tap_point` action で表されます。`crawl_repro._selector()` は id も label も無い要素には `None` を返し、ステップを出しません（「忠実に再現できないなら出さない」）。キャプチャも同じ立場を取り、ラダーを `label` で止めます。id も label も無いタップは座標へ落とさず拒否します（決定 1）。
- **決定性の強制**は `drivers/base.py:resolve_unique` です。一致が 0 件（不在）または 2 件以上（曖昧）で例外を上げます。キャプチャはこの規則を*著述*の段階で適用します。
- **安定度スコア**は `doctor.score(elements, id_namespaces)` を再利用し、解決したセレクタの安定度を著者に示します（BE-0013 のピッカーが必要とするのと同じ `doctor` 連携です）。
- **正規化座標 `[0,1]`**（`crawl.py:_screen_size`、`Action.perform` の `tap_point` スケーリング、`Node.targets`）が、ブラウザでのスクリーンショットのクリックと要素ツリーの points 空間との橋渡しになります。
- **ステップ出力**は `Step` / `TypeText` / `Swipe` と `serialize.dump_scenario_file` を再利用します。著者が所有する書き出し経路は `serve/scenarios.py:ScenarioScope.save()` / `.authored()` で、AI ループが書くのと同じ経路です（自動命名し、上書きしません）。

### キャプチャの流れ（操作ごと）

1. **スナップショット。** backend が新鮮な `driver.query()` と `driver.screenshot(path)`、`_screen_size(driver)` を取得し、保存したスクリーンショットを `serve` の UI に表示します。
2. **指示。** 著者がスクリーンショット上で一点（tap / type）をクリック、または二点（swipe）をドラッグします。ブラウザはクリックを表示ピクセル空間 → 正規化 `[0,1]` → points 空間へ変換し（`Action.perform` の `tap_point` スケーリングと同じ）、POST します。
3. **ヒットテスト。** サーバが `_contains` でその点を frame に含む要素をすべて求め、**最も具体的で操作可能な**要素（`doctor.ACTIONABLE_TRAITS` のうち frame 面積が最小のもの）を選びます。これにより、背後の画面いっぱいの window ではなくボタンに解決します。
4. **解決と検証。** 安定度ラダーの方針でセレクタを組み立て、`resolve_unique` で一意性を検証します。一意でなければ、キャプチャは推測せず、競合する要素を著者に**提示**します。
5. **採点。** `doctor.score` を実行し、選んだ段（安定した `id` / `label` フォールバック）を示します。著者は著述しながら選択の安定度を確認できます。指定できる要素に届かないタップは座標へ落とさず、ここで拒否します（決定 1）。
6. **代理操作。** driver を通じて操作を実行します（`tap(sel)` / `type_text` / `swipe`）。`type` の値は著者が UI で入力したものであり（決定 3）、シリアライズの前に対象の `Redact` 設定で redaction します（決定 4）。操作は実物なので、次のスナップショットはアプリの真の新状態を反映します。
7. **出力。** 解決した `Step` を作成中のシナリオへ追記し、ステップごとに再シリアライズして `save()` します。著者は常に現在の YAML を所有します。

### 操作の種類ごと

- **tap** → `Step(tap=sel)`。
- **type** → フィールドにフォーカス（`driver.tap(sel)`）してから `type_text`。`Step(type=TypeText(text, into=sel))` を出力します。テキストは UI の小さな入力欄から得ます。キャプチャはタップと同様にキーストロークを*観測*できないためです（決定 3）。著者が値を入力し、キャプチャがそれを代理入力します。その値は秘密情報かもしれないため、ステップへシリアライズする前に対象の `Redact` 設定を通します（決定 4）。これにより、秘密情報が redaction されないまま著述済みの YAML に入り込むことはありません。
- **swipe** → 二点のクリック。正規化座標で `Step(swipe=Swipe(from_, to))` を出力します（既存の規約。再実行時にスケールされます）。両端が 1 要素に解決するなら、より安定な `Swipe(on=sel, direction=…)` へ格上げします。

### セレクタ選択の方針（厳密）

`id` があれば一意性を検証して出力します（画面内で id が重複していれば曖昧として提示します。これは `doctor` の `Blocked` 状態です）。なければ `label` で一意なら出力し、そうでなければ `index` を足して解消を試み（フレーキーとして印を付けます）、それでも駄目なら提示します。`id` も `label` も無ければ**キャプチャを拒否し**、その要素には `accessibilityIdentifier` が必要だと著者に伝えます（決定 1、「忠実に再現できないなら出さない」）。座標 tap のステップ型は意図的に**設けません**。`Step` は `tap: Selector` だけを持ち、`id` / `label` で指定できないタップが座標として書き出されることはありません（これは指定できないタップを出さずに飛ばす `crawl_repro` の方針と一致します）。`el → Selector` のラダーは現状 `crawl` と `crawl_repro` の 2 箇所で実装されており、キャプチャを実装すると 3 つ目のコピーになるため、本提案では別の小さなリファクタとして 1 つの共有ヘルパへ集約することを推奨します。

### タイミングは固定 sleep ではなく条件待機

あるアクションが、直前と fingerprint（`crawl.py:fingerprint`）の異なる画面に着地したとき、キャプチャは次のアクションが対象とする最初の要素への `wait` を挿入します。これは AI ループが `record._settle_step` で得るのと同じ自己完結性であり、再実行が実時間のタイミングに依存しないようにします。

### 配置

キャプチャは本質的に対話的（指示 → 操作 → 観測 → 指示）で、著者がクリックするスクリーンショットを*必要とする*ため、新しい CLI コマンドではなく `serve` に属します。構成は 2 つです。

- **新しい純粋モジュール `bajutsu/capture.py`** に、driver にも HTTP にも依存しないコア（`hit_test(elements, point)`、`resolve_capture(elements, point, namespaces)`（セレクタ + doctor の段 + 曖昧さ）、`step_for_{tap,type,swipe}`）を置きます。`record.py` / `crawl_repro.py` がロジックをテスト可能に保つのと同じ方針で、fake な要素ツリーに対して Simulator なしで `make check` の中で単体テストできます。
- **薄い serve 層**として、`serve/handler.py` のルート（例 `POST /api/capture/{start,mark,finish}`）と `serve/operations.py` の op を足します。BE-0011 の stateless な shell-out からの唯一の構造的逸脱は、起動中の対象に対しリクエストを跨いで保持する**ライブな `Driver`** が要る点です（決定 2）。`start` が 1 つの `Driver` を開いて `ServeState` に保存し、各 `mark` がそれを再利用し、`finish`（または切断）がそれを閉じてスロットをクリアします。**同一対象に対する 2 つ目の同時キャプチャは、単一セッションの保証によって拒否します**。1 台のデバイスは 2 つの交錯するセッションを同時には扱えないためです。操作呼び出し（デバイスに触れる境界）は純粋コアの外に保ちます。このライフサイクルは、`ServeState` がすでに持つ寿命の長い差し替え可能なシーム（`executor`、`sessions`）の持ち方をなぞったもので、キャプチャ driver もそうしたスロットの 1 つを、セッションの間だけ保持します。

resolver と emitter は小さなインターフェースの背後に置くので、本物のイベントソース（idb のイベント取得、または XCUITest backend（BE-0019）が記録するイベント）を、resolver / emitter を変えずに後から代理入力と差し替えられます（決定 3）。著者の UI でのテキスト入力は、いまの emitter への入力にすぎません。本物のキーストロークストリームがあれば、同じ emitter に流し込めます。

### 決定性・app 非依存・ゲート

上記のすべての段は構造的（点-in-frame、安定度ラダー、`resolve_unique`、`doctor.score`）であり、**モデル呼び出しも `ANTHROPIC_API_KEY` もありません**。これは `record.py` の `agent.next_action` / `_plan_goal` との鋭い対比です。キャプチャは厳密に Tier 1 で、シナリオを著述するものであり、AI ループと同じく決定的な `run` / CI ゲートに**いかなる経路も持ち込みません**。既存コマンドと全く同様に `targets.<name>`（対象アプリ、シナリオディレクトリ、redaction）を読み、同じ方法で著述済みの YAML を書き出すので、app・backend 非依存を保ちます。

### テスト戦略

Linux の `make check` ゲートに収まり、Simulator は不要で、モックは最小限です（fake な driver と要素ツリーは、挙動のモックではなく実テストデータです）。

- **純粋コアのテスト**を `Element` のリテラルリストに対して書きます。`hit_test` が最も小さい包含する操作可能要素を選ぶこと、セレクタラダー（id / label + index）、id も label も無いタップを拒否してステップを出さないこと（決定 1）、曖昧さの提示でステップを出さないこと、`doctor` の段、`type` / `swipe` の出力、`type` の redaction が設定された秘密の値を出力ステップに届く前にマスクすること（決定 4）、画面跨ぎの settle `wait`、そして出力した `Scenario` が `dump_scenario_file` → `load_scenario_file` で round-trip すること。
- **`FakeDriver`** で代理操作を検証します。指定可能な要素では `tap(sel)` を使うこと、指定できないタップは操作せずに「accessibilityIdentifier を足してください」という拒否を上げることをアサートします。
- **serve op のテスト**を（HTTP 越しではなく）operations 層で、fake なセッションを与えて行います。

### スコープと非目標

**スコープ内**：`serve` でのスクリーンショットクリックによる tap / type / swipe の代理操作キャプチャ、安定度ラダー + doctor スコアによる構造的なセレクタ解決、曖昧さの提示、指定できないタップの拒否（決定 1）、`ServeState` に保持するセッションごとのライブ driver と単一セッションの保証（決定 2）、`type` の取得元としての UI のテキスト入力（決定 3）、シリアライズ時の入力値の redaction（決定 4）、画面跨ぎの settle wait、著者が所有する YAML へのステップの逐次反映。

**非目標**：本物のタッチイベントの観測（idb のイベント取得または XCUITest backend（BE-0019）に依存します）、`expect` / アサーションの推定（意図の推定は AI ループの仕事です。BE-0014 の enrichment 方針を参照）、構造化された GUI エディタ（BE-0013）、マルチタッチ / pinch / rotate のキャプチャ（idb は単一タッチです）、キャプチャを `record` コマンドへ統合すること（BE-0014）。

### 決定

以下の 4 点は草案の段階では未解決でしたが、いずれも決着しました。決着した内容はすでに上記の各節へ織り込んであります。

1. **座標 tap の `Step` は設けない（「忠実に再現できないなら出さない」）。** `id` も `label` も持たない要素へのタップは、座標で指定したステップを出力するのではなく、**キャプチャ時に拒否**し、その要素に `accessibilityIdentifier` を足すよう著者に伝えます。シナリオスキーマに座標 tap のステップ型は**追加しません**。`Step` は `tap: Selector` だけを持ちます。**根拠**：座標タップはレイアウト / デバイスサイズ / 翻訳のいずれが変わっても壊れ、著述済みの YAML に非決定性を持ち込みます。これは指定できないタップに対して `crawl_repro` がすでに取っている立場と同じです（`bajutsu/crawl_repro.py`）。
2. **ライブ driver を `ServeState` にセッションの間だけ保持する。** キャプチャセッションは対象に対して**ただ 1 つ**のライブ `Driver` を開き、セッションの間それを `ServeState` に保持します。`finish` または切断でクリーンアップし、**同一対象に対する 2 つ目の同時キャプチャは単一セッションの保証で拒否**します。**根拠**：キャプチャは指示 → 操作 → 観測という状態を持つループなので、リクエストを跨いで生き続ける driver が要ります（BE-0011 の stateless な shell-out からの唯一の逸脱です）。また、1 台のデバイスは 2 つの交錯するキャプチャセッションを同時には扱えません。
3. **`type` のテキストは著者が入力する UI 欄から得る。** キャプチャはキーストロークを観測できない（idb は入力イベントのストリームを提供しません）ため、著者が `serve` の UI の小さな入力欄に値を入力し、キャプチャがそれをフォーカス中のフィールドへ代理入力します。**根拠**：今日得られる取得元はこれだけです。resolver / emitter のインターフェースのおかげで、本物のイベントソース（BE-0019）が同じシームの背後でこの入力を後から置き換えられます。resolver や emitter を変える必要はありません。
4. **redaction はキャプチャ時に適用する。** 入力された `type` の値は秘密情報かもしれないため、ステップをシリアライズするときに対象の `Redact` 設定を通します（`bajutsu/redaction.py` を再利用します）。これにより、秘密情報が redaction されないまま著述済みの YAML に入り込むことはありません。**根拠**：著述したシナリオはコミットされ共有されます。キャプチャは秘密情報がそこへ最初に入る地点なので、書き出し時にマスクしなければなりません。これは evidence のパイプラインがすでに従っている `Redact` 設定と同じものです。

## 検討した代替案

* **生の座標タップを記録し、そのまま再生する。** 即座に不採用です。座標再生はレイアウト、デバイスサイズ、翻訳のいずれが変わっても壊れ、選択による決定性に反します。各タップをキャプチャ時に安定 `id` へ解決することこそが要点です。
* **タップ点を最前面 / 最初にマッチした要素へ黙って解決する。** 不採用です。「最初にマッチしたものをタップする」を再導入してしまいます。点が曖昧なときは、`resolve_unique` と整合するよう、解消のために著者へ提示しなければなりません。
* **本物のイベント取得 backend を待ってからキャプチャを出す。** 本物のイベントストリーム（idb のイベント取得、または XCUITest backend が記録するイベント）があれば、著者は Web UI で点を示す代わりに Simulator を直接操作できます。これはブロッカーとして不採用です。idb は今日そのようなストリームを提供せず、XCUITest backend（BE-0019）自体が未実装なので、それを前提にするとこの機能全体が先送りになります。代理操作はオフラインかつ API キー不要の経路を今すぐ出せますし、resolver / emitter のインターフェースのおかげで、本物のイベントソースは後から書き換えなしに差し込めます。
* **キャプチャを既存の AI `record` ループの入力モードとして取り込む。** ありえますが BE-0014 へ先送りします。同提案が両方式の役割分担と相互変換を定義します。本提案はキャプチャの仕組みそのものだけを範囲とします。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[DESIGN §6.5](../../DESIGN.md)。`bajutsu/record.py`（AI ループ、`_settle_step`、スクリーンショットの配管）、`bajutsu/drivers/base.py`（`_contains`、`resolve_unique`、`Selector` / `Element`）、`bajutsu/crawl.py`（`Action.as_selector` / `Action.perform` / `_screen_size`、正規化された `Node.targets`）、`bajutsu/crawl_repro.py`（`_selector`、「忠実に再現できないなら出さない」方針）、`bajutsu/doctor.py`（`score`、`ACTIONABLE_TRAITS`）、`bajutsu/scenario/models`（`Step` / `TypeText` / `Swipe`）と `bajutsu/scenario/serialize.py`（`dump_scenario_file`）、`bajutsu/serve/`（`handler.py` のルーティング、`operations.py`、`scenarios.py` の `ScenarioScope`）、`bajutsu/templates/serve.js` と `crawl.html.j2`（スクリーンショット + オーバーレイの先例）。

**依存 / 関連項目**：[BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)（本提案が拡張する `serve` のホスト、`ScenarioScope`、スクリーンショットの配管）、[BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)（点 → 要素のピッカーと doctor スコアを共有します。ピッカーは 1 つの共有部品にすべきです）、[BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md)（AI ループとの役割分担と、キャプチャ → アサーションの enrichment）、[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)（同じ resolver / emitter インターフェースの背後に入る、将来の本物のイベントソース）。
