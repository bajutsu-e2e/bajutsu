[English](BE-XXXX-adb-settle-proven-key.md) · **日本語**

# BE-XXXX — adb の settle ポーリングの高速経路を、実際に静止を証明したキーだけに限定する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-adb-settle-proven-key-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#1527](https://github.com/bajutsu-e2e/bajutsu/pull/1527) |
| トピック | Driver & backend architecture |
<!-- /BE-METADATA -->

## はじめに

`AdbDriver._settle()`（`bajutsu/drivers/adb.py`）は、アクチュエータが座標を解決する前に、Android の
画面が本当に静止したかどうかを判定します。この高速経路は、`_last_stable_key` との単純な一致を静止の
証拠として信頼していました。しかしこのキャッシュは、呼び出し元を問わず`query()`が呼ばれるたびに
上書きされます。本項目は、`_settle`自身（または catch-up バリアの dwell 判定）が実際に静止を
確認したキーだけを保持する、より狭い第 2 のキャッシュ`_settled_key`を追加し、高速経路をそちらだけに
限定します。あわせて`rawTree`という capture 種別も追加します。同種のフレークが再発したとき、複数の
ランにまたがって`elements.json`とスクリーンショットから再構成する代わりに、デバイスの生ダンプから
直接診断できるようにするためです。

## 動機

`gestures`シナリオの「long-press and double-tap targets mirror their result」というケースで、
失敗したランと成功したランを比較したところ、本物のフレークが見つかりました。`long_press`は
ジェスチャーが配信されたと報告し（resident actuation channel からの`accepted: true`）、それでも
アプリ側のミラー状態は`idle`のまま変わりませんでした。失敗したランでは、`log.longpress`の frame が
`wait`、`assert`、`long_press`、`double_tap`という 4 つの連続したステップにわたって同一でした。
成功したランでは、同じ frame が`assert`から`long_press`の間で動いています。シナリオ側のジェスチャーが
何もない区間での変化であり、その裏では`assert`ステップが読み取った時点より後もスクロールが
静止し切っていなかったことを意味します。

`_settle()`自身のコメントは、意図した契約をこう説明しています。「静止した画面は 1 回の読み取りで
settle する（最初の`query()`がキャッシュ済みのキーと一致するため）。実際にアニメーションしている
画面だけがポーリングする」。ところが照合先の`_last_stable_key`は、`_record_tree`
（`bajutsu/drivers/coordinate_tree.py`）が**あらゆる**`query()`呼び出しのたびに書き換える値であり、
`_settle`自身の 2 回連続一致という規律を経た読み取りに限られません。`wait`ステップの内部ポーリング
（自分が待っている 1 要素が一致した瞬間に止まり、ツリーの残り部分がどう動いているかは見ていません）や、
単発の`assert`の読み取りも、`_settle`自身のポーリングとまったく同じようにこの値を書き換えます。
つまり高速経路が実際に検証していたのは「`_settle`がこのキーの静止を証明したか」ではなく、「どこかから
来た直近 1 回の読み取りが、たまたまこの値と一致したか」でしかありませんでした。分断された読み取りや
アニメーション途中の読み取りが偶然一度だけ同じ値を繰り返せば、それだけで本物の静止として通って
しまいます。

ツリーが遷移の途中で本当に静止状態を誤って報告しうることは、仮説ではありません。成功したランの
`log.doubletap`の frame は`[42, 2102, 996, 25]`（高さが誤っており、隣接する要素との順序も逆転して
いる）を 2 回連続の読み取りで示した後、`[42, 1686, 996, 63]`に補正されました。`_advance_catchup`自身の
docstring も、まさにこの形を指摘しています。「Android はノードの境界を 1 つずつ再公開する。catch-up の
途中でとらえた読み取りは分断されうる」。失敗したランで 4 ステップにわたって frame が固定されていた
ことは、この種の読み取りが過渡的なスナップショットに着地したまま、タッチが発火する前に一度も
再検証されなかった場合と整合します。

## 詳細設計

### 1. 由来を追跡する settle キャッシュ

`AdbDriver`に`self._settled_key: StableKey | None`を追加します。これは`_last_stable_key`とは
意図的に別物です。`_last_stable_key`は他の利用箇所（`_pan_baseline`、`_device_act`の catch-up
ベースライン、`_advance_catchup`自身の変化検出）が「直近の観測値」として読み続けており、この
用途自体は正当なので、本項目では変更しません。`_settle()`は、自身の新しい読み取りの前に
`prev_key = self._last_stable_key`を取得するのをやめ、代わりに新しい読み取りのキーを
`_settled_key`と照合します。その値が`None`でなく、かつ一致したときだけ、即座に返す高速経路を
とります。

`_settled_key`を書き込む場所は次の 2 箇所に限られます。どちらも単発の読み取りが偶然一致した
だけではなく、本物の「2 回の観測が離れて一致した」静止の証明です。

- `_settle()`自身のポーリングの内部で、2 回連続の読み取りが一致したとき。これは既存の規律を
  変えず、その結果を後続の呼び出しでも信頼できるものとして新たに記録するだけです。
- `_advance_catchup`の projection-dwell 分岐の内部で（デバイスのイベントマークを持たない
  `uiautomator dump`経路）、dwell 要件がバリアを閉じたとき。この dwell 自体が、変化した
  projection が`_CATCHUP_DWELL_S`の間保持されて初めてバリアを閉じるという、`_settle`自身の
  ポーリングと同じ「2 回の観測が離れて一致した」形をすでに満たしています。違いは、`_settle`
  自身のポーリングではなく、`wait`や`assert`がすでに行っていた読み取りから組み立てられている
  点だけです。これにより、既存の`test_reads_the_runner_already_takes_close_the_barrier_for_free`
  というテストケースは、追加のポーリングなしのまま保たれます。runner 自身が挟む読み取りも、
  観測されただけでなく証明済みと記録されたキーを残すため、引き続き高速経路の資格を得るからです。

`_advance_catchup`の**マーク postdate**分岐（resident channel 経由）は、意図的に`_settled_key`を
書き込みません。デバイスクロックのマークがアクチュエーションを postdate したという事実が証明する
のは順序（この読み取りはジェスチャーに対して古くない）であって、静止ではないからです。フリングは、
そのマークを postdate する最初の読み取りより後もずっと公開され続けることがあります。この
クローズを「証明済み」として信用してしまうと、本項目が閉じようとしている隙間を、まさに resident
channel 上で再び開けてしまいます。今回診断したフレークが実際に走っていたのも、この channel です
（失敗したランの`manifest.json`自身が`"via": "identity"`と記録しています）。

ポーリングが wall-clock の予算を使い切っても 2 回の読み取りが一度も一致しなければ、`_settled_key`
は`None`に戻ります。後続の呼び出しが、たまたま着地した先を証明済みとして扱わないようにするため
です。

`_settled_key`は書き込まれるだけでなく、無効化もされます。あらゆるアクチュエーションがこれを
クリアします。証明済みのキーが指す画面は、そのアクチュエーションより後も同じとは限らない
からです。

`_act()`（すべてのジェスチャー）は、これまで個別にこれをリセットしていました。
`_device_act()`の成功時・不確定時の各分岐と`type_text()`も同様です。この 2 つは、自己レビューの
中で見つかった隙間でした。上の 2 つの書き込み箇所と同じやり方で塞ぎました。今はこの 3 箇所とも、
1 つのメソッド`AdbDriver.invalidate_settled_cache()`を共有します。このメソッドは
`_tree_current`と`_read_ordered`も同時にリセットします。この 2 つは、同じリセットがすでに
カバーしていたキャッシュです。これにより、後から追加するアクチュエータは、同じ処理を自分で
複製しなくても、構造として 3 つの無効化を手に入れます。

画面はドライバ自身のアクチュエータの外でも変わります。アプリの再起動と crawl のリセットは、
どちらも`adb.Env`を通じて画面を直接置き換えます。`AdbDriver`は経由しません。そのため、
置き換えの前に証明されたキーを、上流の誰も疑いません。新しい画面の projection が、たまたま
古いキーと一致することもあります。多くのシナリオは同じホーム画面で始まり終わるため、これは
珍しくありません。すると`_settle`の高速経路は、自分自身では静止を証明していない画面の
読み取りを、1 回だけで信用してしまいます。これは本項目が閉じようとしている不具合と同じ種類です。
ドライバ自身のアクチュエータの外にある入り口から、同じ不具合が再び入り込む形です。

`base.SettledCacheInvalidator`は、`invalidate_settled_cache()`を公開する`runtime_checkable`な
`Protocol`です。下記の`RawSourceProvider`と同じ、狭い opt-in の形をとります。`AdbDriver`は、
このプロトコルを通じてこのメソッドを公開します。具象ドライバをインポートする他の理由を持たない
呼び出し元へ届けるためです。`AndroidEnvironment.relauncher()`と`crawl_reset()`が、それぞれこれを
呼び出します。呼び出す場所は、`adb.Env`が画面を置き換えた直後です。実装は
`bajutsu/platform_lifecycle/environments/android.py`にあります。

### 2. `rawTree` capture 種別

`_describe()`（`bajutsu/drivers/adb.py`）は、それまで生のダンプ文字列をパースした直後にローカル
変数として捨てていました。`AdbDriver`は、直近の読み取りの裏にあるテキストを保持するように
なりました（`base.RawSource`。`ViewportProvider` / `ReadLagProvider` / `SettledReadProvider`と
同じ、狭い opt-in の`@runtime_checkable`プロトコルである`base.RawSourceProvider`を通じて公開
します）。narrowing が何かを変えた場合は、resident channel の`narrow_to_active_window`適用前の
ボディも保持します。`write_raw_tree`（`bajutsu/evidence/core.py`）は、ステップのディレクトリ配下に
`hierarchy.raw<suffix>`と、存在する場合は`hierarchy.pre-transform<suffix>`を書き出します
（`<suffix>`は`base.RawSource.suffix`が持つバックエンド自身のダンプ形式で、adb は`.xml`、
XCUITest の未デコードの`GET /elements`ボディは`.json`）。`capture()`は
`rawTree`分岐を得て、シナリオの capture トークン文法（`bajutsu/scenario/models/_base.py`）も
これを受け付けます。`Defaults.capture`には含めません。捕捉したステップごとに同程度の大きさの
テキストアーティファクトが増えるため、シナリオが`capture: [rawTree, ...]`で明示的に opt-in した
ときだけ取得します。これは上記のキャッシュの不具合そのものを直すものではありません。解決した座標と
実際の画面がずれるという事態が再発したとき、今回の調査に必要だった複数ランにまたがる
スクリーンショットと`elements.json`の突き合わせなしに、その裏の生のデバイスダンプへ直接
アクセスできるようにするためのものです。

生ダンプを正しい`elements.json`と対応づけたまま保つか、対応づけられないときは書き出さない
ようにするための、4 つの小さな正しさの性質があります。

- `capture()`は、自分自身の読み取りを引き起こしうる種別（`elements`。書き出し側は、まだ読み
  取っていなければドライバ自身に問い合わせます）より後に`rawTree`が来るよう並べ替えます。
  `sorted`は安定ソートなので、他の種別同士の相対順序は変わりません。これにより、シナリオが
  トークンをどの順序で並べても、`hierarchy.raw<suffix>`は常に`elements.json`と同じ読み取りを
  指します。
- ステップの pre-step baseline（ステップが動く前に書き出す`screenshot.before` + `elements`）が
  あります。ステップ自身のインラインの`capture`が`rawTree`を指定していれば、それも一緒に
  書き出します。post-step の取得には委ねません。`write_raw_tree`はドライバの**直近**の読み取り
  を保存するものです。その読み取りは baseline の`elements`トークンがすでに書き出した
  pre-action の読み取りです。このステップ自身のアクションではありません。委ねてしまうと、
  ダンプが post-action の
  読み取りと対応づいてしまいます。上記の並べ替えが順序について塞いだのと同じ食い違いが、今度は
  タイミングについて再び開いてしまいます。
- `web`ブロックの内部では、この同じ post-step の取得は常にネイティブドライバを対象にします
  （`WebContextDriver`はスクリーンショットを撮れません）。しかしそこでの`elements.json`は
  **web**ドライバのツリーから書き出されています。`rawTree`の要求は、そこでは応じるのでは
  なく取り下げます。ネイティブドライバのダンプは、まったく別のバックエンドの、まったく別の
  読み取りを指してしまいます。アーティファクトがないよりも悪い結果になるからです。
- `redactor.has_label_rules`（`redact.labels`の設定時）には、`write_raw_tree`が理由をログに
  出しつつ、アーティファクトそのものを拒否します。両方のファイルを書き出しません。
  `redact_elements`は、パース済みのツリーを持っているため、ラベルが設定された要素の`value`
  を構造的にマスクできます。しかし生ダンプは、そのような構造を持たないフリーテキストなので、
  `redact_text`のキー・リテラル値によるマスクだけでは、`elements.json`がすでにマスクした
  内容の無防備な上位集合を書き出してしまいます。それ以外の redact の規則は、生ダンプにも
  フリーテキストとしてそのまま適用され、取得を妨げません。

### 3. あわせて見つかった 2 つの小さな課題

ツリー加工の処理そのものにバグがある可能性を疑い、タイミングの問題とは別の観点でパイプライン全体を
調査しました。しかしスケーリング・クリップ・z 順序に関するロジックは見つかりませんでした。
`_bounds()`は、UI Automator の`bounds="[l,t][r,b]"`属性を取り出す 1 つの正規表現でしかありません。
それでも、同じ調査の中で閉じておく価値のある課題が 2 つ見つかりました。

- `_bounds()`は、不正な（属性自体は存在するがパースできない）`bounds`をサイレントに
  `(0.0, 0.0, 0.0, 0.0)`にデフォルトしていました。これは、その属性を本当に持たないノード
  （それ自体は問題のない、想定内のケースです）と区別がつきません。不正なケースに限って警告
  ログを出すようにし、本当に属性が存在しないケースはこれまでどおり無言のままにしました。
- `narrow_to_active_window`（`bajutsu/adb_resident.py`）は、パッケージ名で SystemUI の装飾
  ウィンドウを除去しますが、非 SystemUI のウィンドウが複数あるときに「どれがアクティブな
  ウィンドウか」を判断する仕組みを持ちません。この gap は、モジュール自身の docstring がすでに
  permission dialog のケースとして指摘しているものです。特性化テストを追加し、現状の挙動
  （両方のウィンドウが narrowing を生き延びる）をそのまま固定しました。今後この挙動を変える
  変更があれば、ここに差分として現れます。

### 機械的に検証可能な成果

`tests/test_adb.py`の
`test_settle_does_not_trust_a_coincidental_match_with_no_catchup_pending`は、フェイクの`run`
シーケンスに対してキャッシュの偶然一致バグを直接再現し、修正前のコードに対しては失敗します。
`test_settle_fast_path_trusts_a_key_it_proved_itself`、
`test_settled_key_resets_when_the_poll_never_converges`、
`test_catchup_dwell_close_sets_the_settled_key`、
`test_catchup_mark_postdate_close_does_not_set_the_settled_key`が、新しい状態遷移の残りを固定
します。既存の`_settle` / catch-up 一式のテスト（
`test_reads_the_runner_already_takes_close_the_barrier_for_free`を含む）は変更なしでとおり、
既存の free-settle 最適化が保たれていることを裏づけます。`tests/test_adb_lifecycle.py`の
`test_relauncher_invalidates_the_driver_settled_cache`と
`test_crawl_reset_invalidates_the_driver_settled_cache`は、ドライバ自身のアクチュエータの外から
`invalidate_settled_cache()`に到達する 2 つの lifecycle 呼び出し箇所を固定します。
`tests/test_evidence.py`と`tests/test_adb_resident.py`は、`write_raw_tree`の redaction と
no-op 挙動、narrow 前ボディの有無、複数ウィンドウの特性化をカバーします。ラベル規則による拒否と、
`capture()`の安定ソートが保証する同一読み取りの対応づけも、あわせてカバーします。この対応づけは、
トークンの順序を問いません。

`tests/test_capture_firing.py`の`test_inline_raw_tree_joins_the_pre_step_baseline`と
`test_inline_raw_tree_is_dropped_inside_a_web_block`は、run loop に残る 2 つの対応づけの性質を
固定します。1 つ目は、インラインの`rawTree`要求が pre-action の読み取りに対して取得されること
です。この読み取りは`elements`が読み取ったのと同じものです。post-action の食い違った読み取りに
対しては取得しません。2 つ目は、`active_driver`が`web`ブロックのドライバであるときの挙動です。
このとき`rawTree`が本来読み取るべきネイティブドライバとは異なるため、食い違ったまま取得する
のではなく取り下げます。判定者は`make check`であり、ここでの変更はどれも判定経路には触れません
（第一原則）。

## 検討した代替案

**高速経路を全面的に無効化し、常に最低 1 回はポーリングする。** 見送りました。本項目の初期案が
まさにこれで、既存の`test_reads_the_runner_already_takes_close_the_barrier_for_free`を壊しました。
これは、runner 自身が挟む読み取り（`wait`、`assert`）が dwell 要件を満たしたとき、次の
アクチュエータは追加コストを払うべきではないという、すでに出荷済みの意図的な最適化です。由来を
追跡するキーは、この最適化が想定していたケースをそのまま保ちながら、想定していなかったケース
だけを閉じます。

**マーク postdate による catch-up のクローズでも`_settled_key`を書き込む。** 見送りました。
デバイスクロックのマークがジェスチャーを postdate したという事実は順序を証明するのであって、
静止を証明するのではありません。今回診断したフレークは、まさにこの channel（resident server、
`via: identity`）の上で走っていました。単発の postdate 済み読み取りを「証明済み」として信用
すれば、本項目が閉じようとしている隙間を 1 手前で再び開けてしまいます。

**別のキーを追跡する代わりに、タイミング（2 つの一致する読み取りの間の最小 wall-clock 間隔）から静止を再構成する。** 検討しましたが、採用した設計より高い確信度で診断済みのケースを
直せるわけではなく、追跡すべきタイムスタンプのフィールドが増え、catch-up バリア自身が
すでに検証済みの dwell 証明ともきれいに組み合わさりません。

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
