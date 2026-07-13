[English](BE-0251-driver-base-helper-hoist.md) · **日本語**

# BE-0251 — 重複した driver ヘルパーを drivers.base に集約し、細かな定数を統一する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0251](BE-0251-driver-base-helper-hoist-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0251") |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

コードベースの各所に、ほぼ同一かまったく同一の小さなロジックが四つ重複しています。すべての
実 driver に存在する単発判定の `wait_for` の本体、三つの driver に重複するフレーム中心座標の
計算とそのうち二つに重複するジェスチャー基点座標の派生形、runner 用と orchestrator 用に別々に
定義された何もしないネットワークソース、そして `config_source.py` と `cli/_shared.py` の双方に
定義されたデフォルト設定ファイル名の定数です。本項目は、これらをそれぞれ一つの共有定義へ
集約します。driver 側のヘルパーは `bajutsu/drivers/base.py` へ、残る二つはそれぞれ単一の
定義元へ集約し、以後どれかに手を入れる変更は一箇所で済むようにします。

## 動機

`wait_for` は四つの実 driver すべてで同一です。`bajutsu/drivers/idb.py:385`、
`bajutsu/drivers/adb.py:526`、`bajutsu/drivers/xcuitest.py:367`、
`bajutsu/drivers/playwright.py:683` のいずれも、本体は
`return len(base.find_all(self.query(), sel)) >= 1` という一文だけです。このメソッドは、
共有の `base.wait_until` が持つ締め切りポーリング（BE-0118）の内側にある単発判定であり、
その正しさは決定性に直結します。driver 適合性テスト（BE-0114）はすでに四つの挙動が一致する
ことを検証していますが、四つのうち三つだけを直して残り一つを直し忘れても、テストが気づく
までは静かに乖離が生まれます。これは、backend ごとの挙動の乖離を排除しようとする
backend 非依存の driver 設計（第三の絶対原則）が本来防ぎたい種類のずれです。

要素の `(x, y, w, h)` フレームから中心座標を求める `(x + w / 2, y + h / 2)` という計算は、
`bajutsu/drivers/idb.py:317-321`（`_center`）、`bajutsu/drivers/playwright.py:516-518`
（`_center`）、`bajutsu/drivers/adb.py:338-350`（`_center` および `_center_with_screen`）に
重複しています。これに関連するジェスチャー基点座標の派生形、つまり同じ中心座標に二本指
ジェスチャー用の半径 `min(w, h) / 4` を加えたものは、`bajutsu/drivers/playwright.py:601-608`
（`_gesture_anchor`）と `bajutsu/drivers/adb.py:492-494`（BE-0232 の二本指ジェスチャー処理内に
直接記述）に重複しています。どちらも、コピーの際にわずかに書き違えやすい幾何計算であり、
新しい backend（Android は三つ目の backend であり、四つ目もあり得ます）を追加するたびに
車輪の再発明を強いています。いずれかのコピーで除数を取り違えたり `x` と `y` を入れ替えたり
すれば、その backend でだけタップやジェスチャーの基点が静かにずれます。driver 適合性テスト
（BE-0114）は、たまたまその経路を通す場合にしかこれを検知できません。

これとは別に、`bajutsu/runner/types.py:66` は `_no_net` を、
`bajutsu/orchestrator/types.py:63` は `_no_network` を定義しており、どちらも引数を取らず
空の `list[NetworkExchange]` を返すだけの同一の関数です。ネットワークコレクターが接続されて
いないときのデフォルトの `NetworkSource` として使われています。
`bajutsu/runner/pipeline.py:39` は `_no_net` を、`bajutsu/orchestrator/loop.py:39` は
`_no_network` をそれぞれインポートしており、runner と orchestrator がそれぞれ独自のコピーを
持っている状態です。この値自体には runner 固有や orchestrator 固有の意味はなく、単に
「ネットワークを収集しなかった」ことを表すだけです。

最後に、`DEFAULT_CONFIG = "bajutsu.config.yaml"` は二箇所に定義されています。一つは
`bajutsu/config_source.py:29` で、設定スペックのパス解決（`config_source.py:391`）に
使われています。もう一つは `bajutsu/cli/_shared.py:36` で、おそらく CLI 側の
デフォルトパスのフォールバックとして独立に定義されています。ファイル名という、それ自体は
マジックストリングに過ぎない定数が二つの場所で別々に保守されているということは、デフォルトの
設定ファイル名を変更する（あるいは解決方法を変える）ときに、二箇所を思い出さなければならない
ということです。両者を結びつけて検証するテストは今のところありません。

これらはいずれも、今日時点では挙動のバグではありません。四つの `wait_for` の本体も、三つの
フレーム中心座標のコピーも、二つの何もしないネットワークソースも、二つの `DEFAULT_CONFIG` の
定数も、現時点ではすべて一致しています。ただし、それぞれが決定性に直結するコード
（`wait_for`、ジェスチャーの幾何計算）か、単純に一つへまとめられるコード（何もしない
ネットワークソース、デフォルト設定ファイル名の定数）のどちらかに属する、重複した真実の
小さな島です。四項目それぞれはサイズ XS/S 程度の作業であり、いずれも挙動を変えない一つの
小さな PR にまとめて対応できます。

## 詳細設計

作業は四つの独立した単位に分解できます。それぞれが一つの重複を一つの共有定義へ集約するだけで、
挙動は変わりません。

- **`wait_for` を `drivers.base` へ集約する。** 現在の単発判定
  （`len(base.find_all(driver.query(), sel)) >= 1`）を本体とする
  `base.default_wait_for(driver: Driver, sel: Selector) -> bool` を追加し、`idb.py`、
  `adb.py`、`xcuitest.py`、`playwright.py` それぞれの `wait_for` からこれを呼び出すように
  します（`return base.default_wait_for(self, sel)`）。各 driver は `Driver` プロトコルが
  要求する `wait_for` メソッド自体は引き続き自分で持つため、将来、単発判定＋ポーリングという
  共有の契約を介さずネイティブに待機できる backend が現れても、その driver は自分の
  `wait_for` を上書きするだけで済みます。移動するのはデフォルトの本体だけで、プロトコルの
  形は変わりません。
- **フレーム中心座標とジェスチャー基点座標の計算を `drivers.base` へ集約する。**
  `(x + w / 2, y + h / 2)` を計算する `base.frame_center(frame: Frame) -> Point` と、
  中心座標に `min(w, h) / 4` を加えた `base.gesture_anchor(frame: Frame) -> tuple[float,
  float, float]` を追加します。どちらも、すでに解決済みの `(x, y, w, h)` フレームタプルを
  受け取るだけにし、driver 固有の要素解決ロジックを持ち込まない純粋な幾何計算にとどめます。
  `idb.py` の `_center`、`playwright.py` の `_center` と `_gesture_anchor`、`adb.py` の
  `_center_with_screen` とその内部のジェスチャー基点座標の計算を、これら二つのヘルパー経由に
  書き換えます。各 driver 固有の要素解決手順（`_resolve`、`resolve_unique`、
  `_resolve_frame_and_screen`）自体は変えず、解決済みフレームに対する計算だけを置き換えます。
- **何もしないネットワークソースを一つに統合する。** `bajutsu/orchestrator/types.py` の
  `_no_network` を唯一の定義として残します。`runner` はすでに `orchestrator` に依存しており
  （`runner/types.py`、`runner/pipeline.py`、`runner/pool.py`、`runner/mailbox.py` がいずれも
  `orchestrator` からインポートしています）、`orchestrator` 側から `runner` をインポートして
  いる箇所はありません。定義を `orchestrator` 側に残せば、この一方向の依存を保ったまま統合
  できます。`bajutsu/runner/pipeline.py` が `runner/types.py` の独自コピーではなく、この単一の
  定義をインポートするように変更し、重複を削除します。
- **`DEFAULT_CONFIG` を統合する。** `bajutsu/config_source.py:29` の定義を唯一の定義元として
  残します（設定スペックのパスをすでにこの定数に対して解決しているモジュールだからです）。
  `bajutsu/cli/_shared.py` は同じ文字列リテラルを再定義するのではなく、
  `from bajutsu.config_source import DEFAULT_CONFIG` として再エクスポートするように
  変更します。

四つの単位はそれぞれ独立して出荷可能かつ独立してテスト可能であり、互いに依存関係はありません。
一つの PR にまとめて着手することも、順序を気にせず別々に着手することもできます。

## 検討した代替案

- **`wait_for` を driver ごとに独立させたまま残す。** 共有ヘルパーに触れず、各 backend の
  メソッド本体が自由に乖離できる状態を保つ案です。これは却下ではなく、上記の設計ですでに
  対応しています。現在同一である本体を `base.default_wait_for` へ集約しても、driver ごとの
  上書きという余地はなくなりません。ネイティブな待機手段を得た backend は、引き続き自分の
  `wait_for` を定義してデフォルトを呼ばないだけです。共有ヘルパーが置き換えるのは、今日
  四つのコピーとして存在する*同一*の挙動だけであり、プロトコルが持つ driver ごとの余地
  そのものではありません。
- **何もせず、driver 適合性テスト（BE-0114）が乖離を検知するのに任せる。** 却下しました。
  適合性テストは、乖離がすでに持ち込まれたあと、うまくいけばレビューや CI で発覚した時点で
  それを検証するものです。単一の共有定義は、乖離が起きたあとに検知するのではなく、乖離の
  可能性自体を取り除きます。決定性に直結するコードに対しては、こちらのほうが安上がりで
  長く効く直し方です。
- **`frame_center` と `gesture_anchor` を、解決済みの `Frame` ではなく `Selector` を受け取り
  自前で解決する形に一般化する**（各 driver の現在の `_center` のシグネチャに合わせる）案。
  却下しました。各 driver は selector をフレームへ解決する手順自体が異なります。`idb.py` は
  スナップショットのツリーに対して安定を待ってから解決し、`adb.py` はさらにタッチデバイスの
  座標系へスケールするための画面サイズも一緒に返し、`playwright.py` は毎回新しいクエリに
  対して直接解決します。解決まで担う共有関数にすると、driver 固有の解決手順をコールバック
  として受け取る（幾何計算そのものより間接層のほうが重くなります）か、これらの driver ごとの
  違いを失うかのどちらかになります。素の `Frame` を受け取る形にとどめておけば、集約後の
  ヘルパーは純粋な幾何計算のままであり、解決はこれまでどおりの場所に残ります。
- **何もしないネットワークソースと `DEFAULT_CONFIG` は、どちらも一行の定義に過ぎないため
  重複したまま残す。** 却下しました。一行の重複であっても、値が別々である積極的な理由がない
  重複であることに変わりはありません。この項目の他の二つの単位もすでにモジュール間の
  インポート境界に手を入れる作業であり、この二つの低リスクかつ低コストな統合を同じ PR に
  含めるほうが、一行の修正のためだけに別の項目を起こすより安上がりです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `base.default_wait_for` を追加し、`idb.py`、`adb.py`、`xcuitest.py`、`playwright.py` の
      `wait_for` からこれを呼び出すようにする。
- [ ] `base.frame_center` と `base.gesture_anchor` を追加し、五つの呼び出し元
      （`idb.py`、`adb.py`、`playwright.py`）をこれら経由に書き換える。
- [ ] `_no_net` と `_no_network` を一つの定義に統合し、`runner/pipeline.py` と
      `orchestrator/loop.py` の双方からインポートするようにする。
- [ ] `cli/_shared.py` が `config_source.DEFAULT_CONFIG` を再定義せず再エクスポートするように
      する。

## 参考

- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — 本項目が集約したヘルパーを
  追加する対象である、共有の selector 解決の中核（`find_all`、`resolve_unique`、
  `wait_until`）。
- [`bajutsu/drivers/idb.py:385`](../../bajutsu/drivers/idb.py)、
  [`bajutsu/drivers/adb.py:526`](../../bajutsu/drivers/adb.py)、
  [`bajutsu/drivers/xcuitest.py:367`](../../bajutsu/drivers/xcuitest.py)、
  [`bajutsu/drivers/playwright.py:683`](../../bajutsu/drivers/playwright.py) — 本項目が
  集約する、四つの同一な `wait_for` 本体。
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
