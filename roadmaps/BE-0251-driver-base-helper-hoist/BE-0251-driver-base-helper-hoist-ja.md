[English](BE-0251-driver-base-helper-hoist.md) · **日本語**

# BE-0251 — 重複した driver ヘルパーを drivers.base に集約し、細かな定数を統一する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0251](BE-0251-driver-base-helper-hoist-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0251") |
| 実装 PR | [#1069](https://github.com/bajutsu-e2e/bajutsu/pull/1069) |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

コードベースの各所に、小さなロジックが 4 つ、ほぼ同一かまったく同一のまま重複しています。

- 実 driver に存在する、単発判定の `wait_for` の本体
- 3 つの driver に重複するフレーム中心座標の計算と、そのうち 2 つに重複するジェスチャー基点座標の派生形
- runner 用と orchestrator 用に別々に定義された、何もしないネットワークソース
- `config_source.py` と `cli/_shared.py` の双方に定義された、デフォルト設定ファイル名の定数

本項目は、これらをそれぞれ 1 つの共有定義へ集約します。driver 側のヘルパーは `bajutsu/drivers/base.py` へ集約します。残る 2 つは、それぞれ単一の定義元へ集約します。以後、どれかに手を入れる変更は 1 か所で済み、コピーごとのずれは生じません。

## 動機

`wait_for` は、実 driver の間で同一です。対象は `coordinate_tree.py`、`bajutsu/drivers/xcuitest.py`、`bajutsu/drivers/playwright.py` の 3 つです。先頭の `coordinate_tree.py` は、idb と adb が継承する共有基底（BE-0254）です。どの本体も `return len(base.find_all(self.query(), sel)) >= 1` という 1 文だけです。
このメソッドは、共有の `base.wait_until` が持つ締め切りポーリング（BE-0118）の内側にある単発判定です。そのため、正しさは決定性に直結します。

driver 適合性テスト（BE-0114）は、すべての backend の挙動が一致することをすでに検証しています。ただし、一部のコピーだけを直して別のコピーを直し忘れた場合、乖離は静かに生まれます。
backend ごとの挙動の乖離は、backend 非依存の driver 設計（プライムディレクティブ 3）が防ぎたい種類のずれです。

要素の `(x, y, w, h)` フレームから中心座標を求める `(x + w / 2, y + h / 2)` という計算は、3 か所に重複しています。

- `bajutsu/drivers/idb.py:317-321`（`_center`）
- `bajutsu/drivers/playwright.py:516-518`（`_center`）
- `bajutsu/drivers/adb.py:338-350`（`_center` および `_center_with_screen`）

ジェスチャー基点座標の派生形は、同じ中心座標に、二本指ジェスチャー用の半径 `min(w, h) / 4` を加えたものです。こちらは 2 か所に重複しています。

- `bajutsu/drivers/playwright.py:601-608`（`_gesture_anchor`）
- `bajutsu/drivers/adb.py:492-494`（BE-0232 の二本指ジェスチャー処理内に直接記述）

どちらも、コピーの際にわずかに書き違えやすい幾何計算です。新しい backend を追加するたびに、同じ計算を作り直すことになります。Android は 3 つ目の backend であり、4 つ目もあり得ます。
いずれかのコピーで除数を取り違えたり、`x` と `y` を入れ替えたりすれば、その backend でだけ、タップやジェスチャーの基点が静かにずれます。driver 適合性テスト（BE-0114）が検知できるのは、たまたまその経路を通す場合に限られます。

これとは別に、`bajutsu/runner/types.py:66` は `_no_net` を、`bajutsu/orchestrator/types.py:63` は `_no_network` を定義しています。どちらも、引数を取らず空の `list[NetworkExchange]` を返すだけの、同一の関数です。ネットワークコレクターが接続されていないときの、デフォルトの `NetworkSource` として使われています。
`bajutsu/runner/pipeline.py:39` は `_no_net` を、`bajutsu/orchestrator/loop.py:39` は `_no_network` を、それぞれ import しています。runner と orchestrator が、それぞれ独自のコピーを持っている状態です。
この値自体に、runner 固有や orchestrator 固有の意味はありません。単に「ネットワークを収集しなかった」ことを表します。

最後に、`DEFAULT_CONFIG = "bajutsu.config.yaml"` は 2 か所に定義されています。1 つは `bajutsu/config_source.py:29` で、設定スペックのパス解決（`config_source.py:391`）に使われています。もう 1 つは `bajutsu/cli/_shared.py:36` で、おそらく CLI 側のデフォルトパスのフォールバックとして、独立に定義されています。
マジックストリングに過ぎないファイル名の定数が、2 か所で別々に保守されています。そのため、デフォルトの設定ファイル名を変更するとき（あるいは解決方法を変えるとき）に、2 か所を思い出さなければなりません。両者を結びつけて検証するテストは、今のところありません。

これらの重複は、今日時点では挙動のバグではありません。共有された `wait_for` の本体、3 つのフレーム中心座標のコピー、2 つの何もしないネットワークソース、2 つの `DEFAULT_CONFIG` の定数は、現時点ですべて一致しています。
それでも、どれも重複した真実の小さな島です。2 つは決定性に直結するコード（`wait_for` とジェスチャーの幾何計算）に属し、残る 2 つは単純に 1 つへまとめられるコード（何もしないネットワークソースとデフォルト設定ファイル名の定数）に属します。
作業の規模は、4 項目それぞれがサイズ XS/S 程度です。4 つとも、挙動を変えない 1 つの小さな PR にまとめて対応できます。

## 詳細設計

作業は 4 つの独立した単位に分解できます。各単位は、1 つの重複を 1 つの共有定義へ集約するだけで、挙動は変わりません。

- **`wait_for` を `drivers.base` へ集約する。** 現在の単発判定（`len(base.find_all(driver.query(), sel)) >= 1`）を本体とする、`base.default_wait_for(driver: Driver, sel: Selector) -> bool` を追加します。各 backend の `wait_for` は、これを呼び出します（`return base.default_wait_for(self, sel)`）。

  BE-0254 で、共有基底 `CoordinateTreeDriver` が導入されました。idb と adb は、それぞれが独自の `wait_for` を持つのではなく、この基底で 1 つの `wait_for` を継承します。したがって、呼び出し箇所は `coordinate_tree.py`（両方の座標 backend を担う）、`xcuitest.py`、`playwright.py` です。

  各 backend は、`Driver` プロトコルが要求する `wait_for` メソッド自体を、引き続き持ちます。将来、単発判定とポーリングという共有の契約を介さずにネイティブに待機できる backend が現れても、その driver は自分の `wait_for` を上書きするだけで済みます。移動するのはデフォルトの本体だけで、プロトコルの形は変わりません。
- **フレーム中心座標とジェスチャー基点座標の計算を `drivers.base` へ集約する。** `(x + w / 2, y + h / 2)` を計算する `base.frame_center(frame: Frame) -> Point` と、中心座標に `min(w, h) / 4` を加えた `base.gesture_anchor(frame: Frame) -> tuple[float, float, float]` を追加します。どちらも、すでに解決済みの `(x, y, w, h)` フレームタプルを受け取るだけにします。driver 固有の要素解決ロジックを持ち込まない、純粋な幾何計算にとどめます。

  書き換える呼び出し箇所は、`idb.py` の `_center`、`playwright.py` の `_center` と `_gesture_anchor`、`adb.py` の `_center_with_screen` とその内部のジェスチャー基点座標の計算です。これらを、2 つのヘルパー経由にします。各 driver 固有の要素解決手順（`_resolve`、`resolve_unique`、`_resolve_frame_and_screen`）は変えません。置き換えるのは、解決済みフレームに対する計算だけです。
- **何もしないネットワークソースを 1 つに統合する。** `bajutsu/orchestrator/types.py` の `_no_network` を、唯一の定義として残します。`runner` は、すでに `orchestrator` に依存しています（`runner/types.py`、`runner/pipeline.py`、`runner/pool.py`、`runner/mailbox.py` が、いずれも `orchestrator` から import しています）。`orchestrator` 側から `runner` を import している箇所はありません。定義を `orchestrator` 側に残せば、この一方向の依存を保ったまま統合できます。`bajutsu/runner/pipeline.py` は、`runner/types.py` の独自コピーではなく、この単一の定義を import するように変更します。重複は削除します。
- **`DEFAULT_CONFIG` を統合する。** `bajutsu/config_source.py:29` の定義を、唯一の定義元として残します。設定スペックのパスを、すでにこの定数に対して解決しているモジュールだからです。`bajutsu/cli/_shared.py` は、同じ文字列リテラルを再定義せず、`from bajutsu.config_source import DEFAULT_CONFIG` として再エクスポートします。

4 つの単位は、それぞれ独立して出荷でき、独立してテストできます。互いに依存関係はありません。1 つの PR にまとめて着手することも、順序を気にせず別々に着手することもできます。

## 検討した代替案

- **`wait_for` を driver ごとに独立させたまま残す。** 共有ヘルパーに触れず、各 backend のメソッド本体が自由に乖離できる状態を保つ案です。これは却下ではなく、上記の設計ですでに対応しています。
  現在同一である本体を `base.default_wait_for` へ集約しても、driver ごとの上書きという余地はなくなりません。ネイティブな待機手段を得た backend は、引き続き自分の `wait_for` を定義し、デフォルトを呼ばないだけです。共有ヘルパーが置き換えるのは、今日 4 つのコピーとして存在する*同一*の挙動だけです。プロトコルが持つ、driver ごとの余地そのものは置き換えません。
- **何もせず、driver 適合性テスト（BE-0114）が乖離を検知するのに任せる。** 却下しました。適合性テストが検証するのは、乖離が持ち込まれたあとです。うまくいっても、レビューや CI で発覚した時点になります。
  単一の共有定義は、乖離の可能性自体を取り除きます。検知は乖離が起きたあとです。決定性に直結するコードでは、取り除くほうが安上がりで、長く効きます。
- **`frame_center` と `gesture_anchor` を、解決済みの `Frame` ではなく `Selector` を受け取り、自前で解決する形に一般化する。** 各 driver の現在の `_center` のシグネチャに合わせる案です。却下しました。各 driver は、selector をフレームへ解決する手順が異なります。
  - `idb.py` は、スナップショットのツリーに対して、安定を待ってから解決します。
  - `adb.py` は、タッチデバイスの座標系へスケールするための画面サイズも、一緒に返します。
  - `playwright.py` は、毎回新しいクエリに対して直接解決します。

  解決まで担う共有関数にすると、driver 固有の解決手順をコールバックとして受け取るか、driver ごとの違いを失うかのどちらかになります。コールバックは、幾何計算そのものより重い間接層です。素の `Frame` を受け取る形にとどめれば、集約後のヘルパーは純粋な幾何計算のままです。解決は、これまでどおりの場所に残ります。
- **何もしないネットワークソースと `DEFAULT_CONFIG` は、どちらも 1 行の定義に過ぎないため、重複したまま残す。** 却下しました。1 行の重複であっても、値が別々である積極的な理由がない重複です。この項目の他の 2 つの単位は、すでにモジュール間の import 境界に手を入れる作業です。低リスクかつ低コストな 2 つの統合を同じ PR に含めるほうが、1 行の修正のためだけに別の項目を起こすより安上がりです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `base.default_wait_for` を追加し、各 backend の `wait_for` からこれを呼び出すように
      する。呼び出し箇所は `coordinate_tree.py`（BE-0254 で idb と adb が継承する共有基底）、
      `xcuitest.py`、`playwright.py`。
- [x] `base.frame_center` と `base.gesture_anchor` を追加し、呼び出し元をこれら経由に
      書き換える。`idb.py`、`adb.py`、`playwright.py` に加え、提案の棚卸しが見落としていた
      frame-center 計算の五つ目のコピーを持つ `orchestrator/actions/handlers/gestures.py` も
      対象に含めました。
- [x] `_no_net` と `_no_network` を一つの定義に統合し、`runner/pipeline.py` と
      `orchestrator/loop.py` の双方からインポートするようにする。
- [x] `cli/_shared.py` が `config_source.DEFAULT_CONFIG` を再定義せず再エクスポートするように
      する。

ログ:

- [#1069](https://github.com/bajutsu-e2e/bajutsu/pull/1069) — 四つの単位すべてを、振る舞いを
  変えない一つの PR にまとめました。`base.default_wait_for` / `base.frame_center` /
  `base.gesture_anchor` を追加し、各 backend と `gestures.py` をこれら経由に書き換えています
  （BE-0254 の `CoordinateTreeDriver` へリベースしたため、idb と adb はこの共有基底を通じて
  `wait_for` を委譲し、それぞれが独自に持つことはありません）。`_no_network` と
  `DEFAULT_CONFIG` は単一の所有者へ統合しています。

## 参考

- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — 本項目が集約したヘルパーを
  追加する対象である、共有の selector 解決の中核（`find_all`、`resolve_unique`、
  `wait_until`）。
- [`bajutsu/drivers/coordinate_tree.py`](../../bajutsu/drivers/coordinate_tree.py)（BE-0254 で
  idb と adb が継承する共有基底）、
  [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py)、
  [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py) — 本項目が集約する、
  同一な `wait_for` 本体。
- [`bajutsu/drivers/idb.py:317-321`](../../bajutsu/drivers/idb.py)、
  [`bajutsu/drivers/playwright.py:516-518`](../../bajutsu/drivers/playwright.py)、
  [`bajutsu/drivers/adb.py:338-350`](../../bajutsu/drivers/adb.py) — 本項目が集約する
  フレーム中心座標の計算。
- [`bajutsu/drivers/playwright.py:601-608`](../../bajutsu/drivers/playwright.py)、
  [`bajutsu/drivers/adb.py:492-494`](../../bajutsu/drivers/adb.py) — 本項目が集約する
  ジェスチャー基点座標の派生形。
- [`bajutsu/runner/types.py:66`](../../bajutsu/runner/types.py)、
  [`bajutsu/orchestrator/types.py:63`](../../bajutsu/orchestrator/types.py) — 本項目が
  統合する、重複した何もしない `NetworkSource`。
- [`bajutsu/config_source.py:29`](../../bajutsu/config_source.py)、
  [`bajutsu/cli/_shared.py:36`](../../bajutsu/cli/_shared.py) — 本項目が統合する、重複した
  `DEFAULT_CONFIG` 定数。
- [BE-0118 — Unify the wait_for polling contract across drivers](../BE-0118-wait-for-contract-unification/BE-0118-wait-for-contract-unification.md)
  — 本項目の `wait_for` の集約が変えずに保つ、単発判定の `wait_for` の契約。
- [BE-0114 — Driver conformance suite for backend-agnostic behavior](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)
  — 四つの driver の挙動がすでに一致することをテストで検証している仕組み。実装を一つに
  集約することで、これをテストだけでなく構造によっても保証できるようになります。
- [BE-0232 — Multi-touch gestures on the adb driver (pinch / rotate)](../BE-0232-adb-multitouch-gestures/BE-0232-adb-multitouch-gestures.md)
  — `playwright.py` と並んで本項目が集約する、`adb.py` のジェスチャー基点座標の計算を導入した
  項目。
