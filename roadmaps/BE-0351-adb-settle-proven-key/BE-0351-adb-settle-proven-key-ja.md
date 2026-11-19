[English](BE-0351-adb-settle-proven-key.md) · **日本語**

# BE-0351 — adb の settle ポーリングの高速経路を、実際に静止を証明したキーだけに限定する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0351](BE-0351-adb-settle-proven-key-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0351") |
| 実装 PR | [#1527](https://github.com/bajutsu-e2e/bajutsu/pull/1527) |
| トピック | Driver & backend architecture |
<!-- /BE-METADATA -->

## はじめに

`AdbDriver._settle()`（`bajutsu/drivers/adb.py`）は、アクチュエータが座標を解決する前に、Android の
画面が静止したかどうかを判定します。この高速経路は、`_last_stable_key` との単純な一致を静止の
証拠として信頼していました。しかし、このキャッシュは呼び出し元を問わず、`query()`が呼ばれるたびに
上書きされます。

本項目は、第 2 のキャッシュ`_settled_key`を追加します。`_settle`自身（または catch-up バリアの dwell
判定）が静止を確認したキーだけを保持する、より狭いキャッシュです。高速経路は、`_settled_key`だけを
根拠にします。

あわせて`rawTree`という capture 種別も追加します。同種のフレークが再発したとき、デバイスの生ダンプから
直接診断できるようにするためです。この種別がなければ、複数のランにまたがって`elements.json`と
スクリーンショットから、生ダンプの内容を再構成することになります。

## 動機

`gestures`シナリオの「long-press and double-tap targets mirror their result」というケースで、
失敗したランと成功したランを比較したところ、本物のフレークが見つかりました。`long_press`は
ジェスチャーが配信されたと報告しました（resident actuation channel からの`accepted: true`）。
それでも、アプリ側のミラー状態は`idle`のまま変わりませんでした。

- 失敗したランでは、`log.longpress`の frame が、4 つの連続したステップ（`wait`、`assert`、
  `long_press`、`double_tap`）にわたって同一でした。
- 成功したランでは、同じ frame が`assert`から`long_press`の間で動いています。シナリオ側の
  ジェスチャーが何もない区間での変化なので、`assert`ステップが読み取った時点より後も、スクロールが
  静止し切っていなかったことになります。

`_settle()`自身のコメントは、意図した契約をこう説明しています。「静止した画面は 1 回の読み取りで
settle する（最初の`query()`がキャッシュ済みのキーと一致するため）。実際にアニメーションしている
画面だけがポーリングする」。ところが、照合先の`_last_stable_key`は、`_record_tree`
（`bajutsu/drivers/coordinate_tree.py`）が**あらゆる**`query()`呼び出しのたびに書き換える値です。
`_settle`自身の「2 回連続で一致」という規律を経た読み取りに、書き込みが限られているわけではありません。

- `wait`ステップの内部ポーリングは、待っている 1 要素が一致した瞬間に止まります。ツリーの残り部分が
  どう動いているかは見ていません。
- 単発の`assert`は、読み取りを 1 回だけ行います。

どちらも、`_settle`自身のポーリングとまったく同じようにこの値を書き換えます。つまり、高速経路が実際に
検証していたのは「`_settle`がこのキーの静止を証明したか」ではありません。「どこかから来た直近 1 回の
読み取りが、たまたまこの値と一致したか」でした。分断された読み取りやアニメーション途中の読み取りが、
偶然一度だけ同じ値を繰り返せば、本物の静止として通ってしまいます。

ツリーが遷移の途中で、静止状態を誤って報告しうることは、仮説ではありません。成功したランの
`log.doubletap`の frame は、2 回連続の読み取りで`[42, 2102, 996, 25]`を示しました。高さが誤っており、
隣接する要素との順序も逆転しています。その後、frame は`[42, 1686, 996, 63]`に補正されました。
`_advance_catchup`自身の docstring も、この形を指摘しています。「Android はノードの境界を 1 つずつ
再公開する。catch-up の途中でとらえた読み取りは分断されうる」。失敗したランで 4 ステップにわたって
frame が固定されていたことは、同じ種類の読み取りが起きた場合と整合します。読み取りが過渡的な
スナップショットに着地し、タッチが発火する前に一度も再検証されなかったのです。

## 詳細設計

### 1. 由来を追跡する settle キャッシュ

`AdbDriver`に`self._settled_key: StableKey | None`を追加します。これは`_last_stable_key`とは意図的に
別物です。`_last_stable_key`は、他の利用箇所が「直近の観測値」として読み続けています。この用途自体は
正当なので、本項目では変更しません。

- `_pan_baseline`
- `_device_act`の catch-up ベースライン
- `_advance_catchup`自身の変化検出

`_settle()`は、自身の新しい読み取りの前に`prev_key = self._last_stable_key`を取得するのをやめます。
代わりに、新しい読み取りのキーを`_settled_key`と照合します。`_settled_key`が`None`でなく、かつ一致した
ときだけ、即座に返す高速経路をとります。

`_settled_key`を書き込む場所は、次の 2 箇所に限られます。どちらも、単発の読み取りが偶然一致した
だけのものではありません。本物の「2 回の観測が離れて一致した」静止の証明です。

- `_settle()`自身のポーリングの内部で、2 回連続の読み取りが一致したとき。既存の規律は変えません。
  その結果を、後続の呼び出しでも信頼できるものとして、新たに記録します。
- `_advance_catchup`の projection-dwell 分岐の内部（デバイスのイベントマークを持たない
  `uiautomator dump`経路）で、dwell 要件がバリアを閉じたとき。この dwell は、変化した projection が
  `_CATCHUP_DWELL_S`の間保持されて初めてバリアを閉じます。これは、`_settle`自身のポーリングと同じ
  「2 回の観測が離れて一致した」形です。違いは、`_settle`自身のポーリングではなく、`wait`や`assert`が
  すでに行っていた読み取りから組み立てられている点だけです。そのため、既存の
  `test_reads_the_runner_already_takes_close_the_barrier_for_free`というテストケースは、追加の
  ポーリングなしのまま保たれます。runner 自身が挟む読み取りは、観測されただけでなく証明済みと記録された
  キーを残すので、引き続き高速経路の資格を得ます。

`_advance_catchup`の**マーク postdate**分岐（resident channel 経由）は、意図的に`_settled_key`を
書き込みません。デバイスクロックのマークがアクチュエーションを postdate したという事実が証明するのは、
順序です。この読み取りは、ジェスチャーに対して古くありません。しかし、静止は証明しません。フリングは、
そのマークを postdate する最初の読み取りより後も、ずっと公開され続けることがあるからです。このクローズを
「証明済み」として信用すると、本項目が閉じようとしている隙間を、再び開けてしまいます。しかも、それは
resident channel 上の隙間です。今回診断したフレークが走っていたのも、この channel でした（失敗した
ランの`manifest.json`が`"via": "identity"`と記録しています）。

ポーリングが wall-clock の予算を使い切っても、2 回の読み取りが一度も一致しなければ、`_settled_key`は
`None`に戻ります。後続の呼び出しが、たまたま着地した先を証明済みとして扱わないようにするためです。

`_settled_key`は書き込まれるだけでなく、無効化もされます。あらゆるアクチュエーションがこれを
クリアします。証明済みのキーが指す画面は、アクチュエーションの後も同じとは限らないからです。

- `_act()`（すべてのジェスチャー）、`_device_act()`の成功時・不確定時の各分岐、`type_text()`は、
  これまで個別にこれをリセットしていました。後ろの 2 つは、自己レビューの中で見つかった隙間で、上の
  2 つの書き込み箇所と同じやり方で塞ぎました。
- 今は、この 3 箇所とも 1 つのメソッド`AdbDriver.invalidate_settled_cache()`を共有します。このメソッドは
  `_tree_current`と`_read_ordered`も同時にリセットします。この 2 つは、同じリセットがすでにカバー
  していた、アクチュエーションごとのキャッシュです。後から追加するアクチュエータは、同じ処理を自分で
  複製しなくても、構造として 3 つの無効化を手に入れます。

画面は、ドライバ自身のアクチュエータの外でも変わります。アプリの再起動と crawl のリセットは、どちらも
`adb.Env`を通じて画面を直接置き換えます。`AdbDriver`は経由しません。そのため、置き換えの前に証明された
キーを、上流の誰も疑いません。新しい画面の projection が、たまたま古いキーと一致することもあります。
多くのシナリオは同じホーム画面で始まり終わるので、これは珍しくありません。すると`_settle`の高速経路は、
自分では静止を証明していない画面の読み取りを、1 回だけで信用してしまいます。これは、本項目が閉じようと
している不具合と同じ種類のものです。ドライバ自身のアクチュエータの外にある入り口から、同じ不具合が
入り込みます。

`base.SettledCacheInvalidator`が、この入り口を閉じます。`invalidate_settled_cache()`を公開する
`runtime_checkable`な`Protocol`で、下記の`RawSourceProvider`と同じ、狭い opt-in の形をとります。
`AdbDriver`は、このプロトコルを通じてメソッドを公開します。具象ドライバをインポートする他の理由を
持たない呼び出し元へ、メソッドを届けるためです。`AndroidEnvironment.relauncher()`と`crawl_reset()`
（`bajutsu/platform_lifecycle/environments/android.py`）が、それぞれこれを呼び出します。呼び出す場所は、
`adb.Env`が画面を置き換えた直後です。

### 2. `rawTree` capture 種別

`_describe()`（`bajutsu/drivers/adb.py`）は、それまで生のダンプ文字列をパースした直後に、ローカル変数
として捨てていました。`AdbDriver`は、直近の読み取りの裏にあるテキストを`base.RawSource`として保持する
ようになりました。テキストは、新しい`base.RawSourceProvider`プロトコルを通じて公開します。これは
`ViewportProvider`、`ReadLagProvider`、`SettledReadProvider`と同じ、狭い opt-in の
`@runtime_checkable`プロトコルです。narrowing が何かを変えた場合、保持するテキストには、resident channel
の`narrow_to_active_window`適用前のボディも含まれます。

`write_raw_tree`（`bajutsu/evidence/core.py`）は、ステップのディレクトリ配下に`hierarchy.raw<suffix>`を
書き出します。パース前のボディがある場合は、`hierarchy.parsed-input<suffix>`も書き出します。
`<suffix>`は`base.RawSource.suffix`が持つ、バックエンド自身のダンプ形式です。adb は`.xml`、XCUITest の
未デコードの`GET /elements`ボディは`.json`です。

`capture()`は`rawTree`分岐を持ち、シナリオの capture トークン文法（`bajutsu/scenario/models/_base.py`）
もこのトークンを受け付けます。`rawTree`は`Defaults.capture`に含めません。捕捉したステップごとに同程度の
大きさのテキストアーティファクトが増えるため、シナリオが`capture: [rawTree, ...]`で明示的に opt-in した
ときだけ取得します。

これは、上記のキャッシュの不具合そのものを直すものではありません。解決した座標と実際の画面がずれる
事態が再発したときのためのものです。そのずれの裏にある生のデバイスダンプへ、直接アクセスできます。
今回の調査に必要だった、複数ランにまたがるスクリーンショットと`elements.json`の突き合わせは、不要に
なります。

生ダンプを正しい`elements.json`と対応づけたまま保つか、対応づけられないときは書き出さないようにする、
4 つの小さな正しさの性質があります。

- **取得の順序。** `capture()`は、自分自身の読み取りを引き起こしうる種別より後に、`rawTree`が来るよう
  並べ替えます。`elements`がその種別です。書き出し側は、まだ読み取っていなければ、ドライバ自身に問い
  合わせるからです。`sorted`は安定ソートなので、他の種別同士の相対順序は変わりません。これにより、
  シナリオがトークンをどの順序で並べても、`hierarchy.raw<suffix>`は常に`elements.json`と同じ読み取りを
  指します。
- **取得のタイミング。** ステップの pre-step baseline（ステップが動く前に書き出す`screenshot.before` +
  `elements`）は、ステップ自身のインラインの`capture`が`rawTree`を指定していれば、`rawTree`も一緒に
  書き出します。post-step の取得に委ねると、ダンプが誤った読み取りと対応づいてしまいます。
  `write_raw_tree`は、ドライバの**直近**の読み取りを保存します。その読み取りは、baseline の
  `elements`トークンがすでに書き出した pre-action の読み取りであり、このステップ自身のアクションでは
  ありません。委ねると、ダンプが post-action の読み取りと対応づいてしまいます。上記の並べ替えは、
  取得の**順序**についてはこの食い違いを塞ぎますが、取得の**タイミング**については塞げません。
- **`web`ブロック。** `web`ブロックの内部では、post-step の取得は常にネイティブドライバを対象に
  します（`WebContextDriver`はスクリーンショットを撮れません）。しかし、そこでの`elements.json`は
  **web**ドライバのツリーから書き出されています。`rawTree`の要求は、そこでは応じずに取り下げます。
  ネイティブドライバのダンプは、まったく別のバックエンドの、まったく別の読み取りを指してしまい、
  アーティファクトがないよりも悪い結果になるからです。
- **ラベルの redaction。** `redactor.has_label_rules`が真のとき（`redact.labels`の設定時）、
  `write_raw_tree`はアーティファクトそのものを拒否します。理由をログに出し、両方のファイルを
  書き出しません。`redact_elements`は、パース済みのツリーを使って、ラベルが設定された要素の`value`を
  構造的にマスクします。生ダンプは、そのような構造を持たないフリーテキストです。`redact_text`の
  キー・リテラル値によるマスクだけでは、`elements.json`がすでにマスクした内容の、無防備な上位集合を
  書き出してしまいます。それ以外の redact の規則は、生ダンプにもフリーテキストとしてそのまま適用され、
  取得を妨げません。

### 3. あわせて見つかった 2 つの小さな課題

調査では、ツリー加工の処理そのものにバグがある可能性も疑い、タイミングの問題とは別の観点でパイプライン
全体を調べました。スケーリング、クリップ、z 順序に関する誤ったロジックは見つかりませんでした。
`_bounds()`は、UI Automator の`bounds="[l,t][r,b]"`属性を取り出す、1 つの正規表現でしかありません。
それでも、同じ調査の中で閉じておく価値のある課題が 2 つ見つかりました。

- `_bounds()`は、不正な（属性自体は存在するがパースできない）`bounds`を、警告なしに
  `(0.0, 0.0, 0.0, 0.0)`として扱っていました。この結果は、その属性を本当に持たないノードと区別が
  つきません。属性を持たないノードは、それ自体は問題のない、想定内のケースです。今は、不正なケースに
  限って警告ログを出します。本当に属性が存在しないケースは、これまでどおり無言のままです。
- `narrow_to_active_window`（`bajutsu/adb_resident.py`）は、パッケージ名で SystemUI の装飾ウィンドウを
  除去します。非 SystemUI のウィンドウが複数あるときに、「どれがアクティブなウィンドウか」を判断する
  仕組みは持ちません。この隙間は、モジュール自身の docstring が permission dialog のケースとして、
  すでに指摘しています。特性化テストを追加し、現状の挙動（両方のウィンドウが narrowing を生き延びる）
  を固定しました。今後この挙動を変える変更があれば、テストに差分として現れます。

### 機械的に検証可能な成果

`tests/test_adb.py`が、状態遷移のテストを持ちます。

- `test_settle_does_not_trust_a_coincidental_match_with_no_catchup_pending`は、フェイクの`run`
  シーケンスに対して、キャッシュの偶然一致バグを直接再現します。修正前のコードに対しては失敗します。
- `test_settle_fast_path_trusts_a_key_it_proved_itself`、
  `test_settled_key_resets_when_the_poll_never_converges`、
  `test_catchup_dwell_close_sets_the_settled_key`、
  `test_catchup_mark_postdate_close_does_not_set_the_settled_key`が、新しい状態遷移の残りを固定します。
- 既存の`_settle` / catch-up 一式のテストは、`test_reads_the_runner_already_takes_close_the_barrier_for_free`
  を含めて、変更なしで通ります。既存の free-settle 最適化が保たれていることを裏づけます。

ほかのテストファイルは、周辺の挙動を固定します。

- `tests/test_adb_lifecycle.py`の`test_relauncher_invalidates_the_driver_settled_cache`と
  `test_crawl_reset_invalidates_the_driver_settled_cache`は、ドライバ自身のアクチュエータの外から
  `invalidate_settled_cache()`に到達する、2 つの lifecycle 呼び出し箇所を固定します。
- `tests/test_evidence.py`と`tests/test_adb_resident.py`は、`write_raw_tree`の次の挙動をカバーします。
  redaction と no-op 挙動、narrow 前ボディの有無、複数ウィンドウの特性化、ラベル規則による拒否です。
  `capture()`の安定ソートが保証する同一読み取りの対応づけも、あわせてカバーします。この対応づけは、
  トークンの順序を問いません。
- `tests/test_capture_firing.py`の`test_inline_raw_tree_joins_the_pre_step_baseline`と
  `test_inline_raw_tree_is_dropped_inside_a_web_block`は、run loop に残る 2 つの対応づけの性質を固定
  します。1 つ目は、インラインの`rawTree`要求が、pre-action の読み取りに対して取得されることです。この
  読み取りは、`elements`が読み取ったのと同じものです。post-action の食い違った読み取りに対しては、
  取得しません。2 つ目は、`active_driver`が`web`ブロックのドライバであるときの挙動です。`rawTree`が
  読み取るネイティブドライバとは異なるため、食い違ったまま取得せずに取り下げます。

判定者は`make check`です。ここでの変更はどれも、判定経路には触れません（第一原則）。

## 検討した代替案

**高速経路を全面的に無効化し、常に最低 1 回はポーリングする。** 見送りました。本項目の初期案がこれで、
既存の`test_reads_the_runner_already_takes_close_the_barrier_for_free`を壊しました。このテストが守って
いるのは、すでに出荷済みの意図的な最適化です。runner 自身が挟む読み取り（`wait`、`assert`）が dwell
要件を満たしたとき、次のアクチュエータは追加コストを払うべきではありません。

由来を追跡するキーは、この最適化が想定していたケースをそのまま保ちます。想定していたケースとは、
`uiautomator dump`経路です。catch-up の projection-dwell が、本物の「2 回の観測が離れて一致した」証明
であり、`_settled_key`を設定するからです。想定していなかったケースについては、隙間を閉じます。

resident channel では、この最適化を保ちません。マーク postdate によるクローズは、`_settled_key`を
設定しないからです（理由は次の代替案で説明します）。そのため、`_settle()`は resident channel 上の
あらゆるアクチュエーションのあとに、この見送った代替案とまったく同じコストを支払います。高速経路
そのものは残っていても、毎回 1 回の確認読み取りと`_SETTLE_POLL_S`分のスリープを払います。resident
channel は本番の経路であり、今回診断したフレークが走っていた経路でもあります。本項目は、リスクよりも
このコストを受け入れます。

**マーク postdate による catch-up のクローズでも`_settled_key`を書き込む。** 見送りました。
デバイスクロックのマークがジェスチャーを postdate したという事実は、順序を証明するのであって、静止を
証明するのではありません。今回診断したフレークは、まさにこの channel（resident server、
`via: identity`）の上で走っていました。単発の postdate 済み読み取りを「証明済み」として信用すれば、
本項目が閉じようとしている隙間を、1 手前で再び開けてしまいます。しかも、その隙間は resident channel
上で開きます。その見返りに得られるのは、上の代替案が失われると説明した free-settle 最適化ですが、
それはどのみち失われます。

**別のキーを追跡する代わりに、タイミング（2 つの一致する読み取りの間の最小 wall-clock 間隔）から
静止を再構成する。** 検討しました。採用した設計より高い確信度で、診断済みのケースを直せるわけでは
ありません。追跡すべきタイムスタンプのフィールドが増えます。さらに、catch-up バリア自身がすでに
検証済みの dwell 証明とも、きれいに組み合わさりません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] Unit 1 — `_settled_key`、書き換えた`_settle()`の高速経路、`_advance_catchup`の 2 つの
      書き込み箇所（dwell は静止を証明し、マーク postdate は証明しない）。その無効化の経路
      （`AdbDriver.invalidate_settled_cache()`が`_act()`・`_device_act()`の分岐・`type_text()`
      を集約）。`base.SettledCacheInvalidator`と、`AndroidEnvironment.relauncher()` /
      `crawl_reset()`の 2 つの lifecycle 呼び出し箇所。
- [x] Unit 2 — `rawTree` capture 種別。`base.RawSource` / `RawSourceProvider`、`AdbDriver`が
      生のダンプと resident channel の narrow 前ボディを保持すること、`write_raw_tree`、
      `capture()`の分岐、シナリオの capture トークン文法。加えて、正しい対応づけを保つ 4 つの
      性質。`capture()`の同一読み取りソート、pre-action での取得、`web`ブロックでの取り下げ、
      `Redactor.has_label_rules`によるラベル規則時の拒否。
- [x] Unit 3 — `_bounds()`が不正な（単に存在しないだけではない）`bounds`属性に警告を出す
      こと。特性化テストが`narrow_to_active_window`の現状の複数ウィンドウ挙動を固定する
      こと。
- [x] Unit 4 — 4 つの Unit すべての決定的なテストカバレッジ、および新しい capture 種別に
      合わせた`docs/evidence.md` / `docs/ja/evidence.md`の更新。`redact.headers` /
      `redact.fields`が単一行のダンプに対して持つ、末尾が欠ける注意点も含みます。

## 参考

- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)
  — resident UI Automator server と、本項目が精緻化する wall-clock 上限つきの`_settle`
  ポーリング。
- [BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier-ja.md) — read-lag catch-up
  バリア。本項目は、その dwell によるクローズとマーク postdate によるクローズを初めて
  区別します。
- [BE-0339](../BE-0339-adb-device-side-actuation/BE-0339-adb-device-side-actuation-ja.md) —
  診断したフレークの`long_press`が経由していたデバイス側アクチュエーション channel
  （`_device_act`）。
- [BE-0345](../BE-0345-actuation-record/BE-0345-actuation-record-ja.md) — この診断を、ログ
  レベルを上げなくても可能にした actuation record（`manifest.json`の frame・`via`・
  `accepted`）。
- [`docs/ja/evidence.md`](../../docs/ja/evidence.md) — `rawTree`種別が加わる証跡サブシステム。
