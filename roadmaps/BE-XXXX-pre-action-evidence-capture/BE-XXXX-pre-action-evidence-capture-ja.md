[English](BE-XXXX-pre-action-evidence-capture.md) · **日本語**

# BE-XXXX — ステップの動作前に、ステップごとのレポート証跡を取得するようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-pre-action-evidence-capture-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#1471](https://github.com/bajutsu-e2e/bajutsu/pull/1471) |
| トピック | 検証とカバレッジ |
<!-- /BE-METADATA -->

## はじめに

すべてのステップにおいて、レポート用のスクリーンショットと要素ツリーは、ステップがすでに動作した
あとにしか取得されません。常に事後で、事前になることは一度もありません。この項目は、この常時発火
するベースライン取得をステップ自身の動作より前へ動かします。これにより、レポートがステップごとに
示す証跡は、そのステップの動作が残した画面ではなく、そのステップが動作の対象とした画面になります。

## 動機

ステップのレポート項目には、1 つの問いに答える役割があります。このステップは何を、何に対して行った
のか、という問いです。ところが今の実装は、別の問いに答えてしまいます。`_handle_action`
（`bajutsu/orchestrator/loop.py:864`）は、まずステップ本体を実行します。`_run_step_body` がタップ・
入力・スワイプ・待機・アサーションを行い（`loop.py:961`）、その呼び出しが返ったあとになって初めて
`_ScreenRead`（`loop.py:1056`）を組み立て、得られたツリーを `self.cfg.sink.capture(...)`
（`loop.py:1096`）に渡します。この呼び出しが `elements.json` を書き出し、その時点でスクリーンショット
を撮影します（`bajutsu/evidence/core.py` の `write_screenshot` / `write_elements`）。この挙動を
支配している定数 `_BASELINE_INSTANT = ("screenshot.after", "elements")`
（`bajutsu/orchestrator/evidence_rules.py:14`）は無条件です。
[`capturePolicy`](../../docs/glossary.md#evidence-capturepolicy-trace-triage) の有無にかかわらず、
どのステップもこの組を必ず受け取ります。つまり、すべてのステップが確実に手にする唯一の証跡は、
タップがどこに着地したかを示す一枚であって、タップした対象そのものではありません。

`tap: { id: settings.reindex }` というステップのスクリーンショットは、タップが遷移したあとの
設定画面を示すのであって、そのステップが押した行そのものは映っていません。前のステップの非同期更新
がまだツリーに反映されていない値に対する `assert` が失敗したときも、スクリーンショットに映るのは、
そのアサーションが実際に読んだ画面からすでに先へ進んだあとの画面です。「なぜこのシナリオは違う行を
タップしたのか」「このアサーションが実行された時点で画面に何が映っていたのか」をレポートから読み解く
には、1 つ前のステップの証跡を読み、その少しあとに画面がどうなっていたはずかを頭の中で再構成する
しかありません。これはまさに、証跡サブシステムが不要にするはずの再構成です
（[evidence](../../docs/evidence.md)）。

この欠落は、既定の取得タイミングだけの問題ではありません。それをすでに解決していると謳っている
修飾子自体にも、実際の不具合が残っています。[`docs/evidence.md`](../../docs/evidence.md#evidence-kinds-and-acquisition-timing)
は `before` / `after` / `around` を実際の取得タイミング修飾子として文書化しており、シナリオは
すでに `capture: [screenshot.before]` と書けます。しかし `capture()`
（`bajutsu/evidence/core.py:145-180`）は、前段で述べた事後の一点だけから呼ばれ、`write_screenshot`
はその場で `driver.screenshot()` を呼びます。どの修飾子がファイル名を決めたかにかかわらずです
（`core.py:170-178`：`name = f"{modifier or 'after'}.png"`）。そのため今日、`screenshot.before`
は `before.png` という名前のファイルを生成しますが、中身は `after.png` と同じ事後のピクセルです。
修飾子が変えるのはラベルだけで、シャッターが切られるタイミングは一度も変わっていません。この項目より
前に、ステップの動作より前にスクリーンショットを実際に撮った実装は存在しません。

この項目のスコープは、すべてのステップがすでに無条件に受け取っている証跡、つまりベースラインと、
既存の `before` 修飾子をどこで使っても正直な動作にすることだけです。`extract` や `assert` が
どの値・どの状態を正しいと判定するかには触れません。その判定の仕組み
（[BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md)、
[BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md)）は `_poll_asserts` /
`_settle_extract_read` を通じて自前のツリーを読んでおり、レポート証跡とは独立しています。値がそれを
生んだ動作より後の状態を表していなければならないからです。レポート証跡が答える問いはそれとは別で、
このステップが渡された状態は何か、という問いです。両者を混同してはいけません。

## 詳細設計

この修正は、既存の 1 つの事実の上に成り立ちます。前のステップがすでにキャッシュした事後の
読み取り、`self.state.prev_after`（`loop.py:1102`。BE-0234 Unit 2）は、`capturePolicy` や
`wants_screen_changed` の有無にかかわらず、今日すでに無条件に保持されています。
`screen_changed` や `before` / `wants_screen_changed` の分岐（`loop.py:899-936`）、割り込みガードには、
何も変更を加えません。この項目が加えるのは、`prev_after` がすでに保持しているツリーの、新しい
独立した利用先が 1 つ増えるだけです。それらの既存の仕組みは、今のままにします。

同じくらい重要なのは、この修正が**してはならない**ことです。ステップ前のツリーを手にするためだけに、
ループの層で新規のデバイス読み取りを強制してはいけません。`tests/orchestrator/test_read_count.py`
は、ループ自身の遅延評価を固定しています。`test_plain_tap_issues_no_runner_read` は、消費者のいない
プレーンな `tap` について、ループが発行する `query()` 呼び出しがゼロであることを検証しており、
このモジュールのドキュストリングは、まさに「冗長な読み取りを再導入する将来の変更を捕まえる」ために
存在します。既存の事後の取得呼び出しは、すでにこの原則に従っています。シンクには
`elements=screen.cached`（`loop.py:1094`）を渡しており、他の何かがすでにツリーを実体化していない
限りこれは `None` になり、問い合わせるかどうかはシンク側の判断に委ねられます。実運用の `FileSink`
は問い合わせますが、read-count のテストが使うスタブのシンクはあえて問い合わせません。これは、
ループ自身の挙動だけを測定するためです。新しいステップ前の呼び出しも、まったく同じパターンに
従う必要があります。`prev_after` がすでに保持しているものをそのまま渡し(最初のステップ、あるいは
ツリーを返さないステップの直後を除けば実データが入っています)、その隙間を埋めるために自ら
`query()` を呼び出してはいけません。

### 作業分解（MECE）

1. **ステップ前の取得呼び出しを追加し、ベースラインを事後の呼び出しから外します。**
   `_run_step_body` が実行される直前に、
   `outcome.artifacts.extend(self.cfg.sink.capture(self.cfg.driver, step_id, ["screenshot.before", "elements"], elements=self.state.prev_after))`
   を無条件に呼びます。既存の事後の呼び出し（`loop.py:1095-1097`）がすでに行っているのと同じ方法で
   `outcome.artifacts` を拡張するため、新しい `before.png` / `elements.json` の項目が
   `manifest.json` に記録され、Unit 5 がそれを見つけられるようになります。呼び出しの対象は
   `active_driver` ではなく `self.cfg.driver` です。既存の事後の呼び出しがすでに同じ選択をしており
   （`loop.py:1090-1092`）、理由も同じです。`web` ブロックの内側では `active_driver` が
   `WebContextDriver` になり、その `screenshot()` は無条件に `UnsupportedAction` を送出するため
   （`bajutsu/webview.py:193-194`）、`active_driver` に対して取得すると `web` ブロックを持つすべての
   シナリオの全ステップで失敗します。新規に問い合わせたツリーではなく `self.state.prev_after` を
   渡すことが、この呼び出しのコストを今日と同じに保つ鍵です。`write_elements` は `elements` が
   `None` のときだけ自らドライバへ問い合わせるので（`core.py:69`）、これは既存の事後の呼び出し自身が
   渡している `screen.cached` という引数とまったく同じ形です。何も読み取らないシンクを使うシナリオは、
   ここでも何のコストも払わず、`tests/orchestrator/test_read_count.py` が固定しているゼロは、
   ゼロのままです。これにより、動作前の状態とその時点のスクリーンショットから `before.png` と
   `elements.json` を書き出します。今日すべてのステップが受け取っている 2 つの証跡と同じもので、
   タイミングが早まり、`elements` についてはすでに手元にツリーがある限り正しいものから作られる点
   だけが変わります。`_BASELINE_INSTANT` は `_collect_captures`（`evidence_rules.py:157-168`）から
   取り除きます。これ以降 `_collect_captures` が返すのは、シナリオが実際に要求したもの
   （`step.capture` と一致した `capturePolicy` ルール）だけになり、重複排除すべき暗黙の
   ベースラインは残りません。
   **実装メモ：** `web` ブロックの内側では `self.state.prev_after` がブロック全体を挟んで `None` に
   リセットされており（BE-0234 Unit 2）、そのままでは `write_elements` のフォールバックが実際に
   動作した `WebContextDriver` ではなく `self.cfg.driver`（ネイティブ）へ問い合わせてしまいます。
   そのためステップ前の呼び出しは、`prev_after` が未設定かつ `active_driver is not self.cfg.driver`
   のときに限り `active_driver.query()` で `elements` を明示的に解決します。この問い合わせは
   `try`/`except (ConnectionError, base.UnsupportedAction, OSError)` で保護されたベストエフォート
   であり、ブリッジが失われている場合はステップ自身の動作が始まる前にクラッシュさせるのではなく、
   この証跡だけをスキップします。同じ問い合わせは、ローカル変数 `pre_elements` だけでなく
   `self.state.prev_after` 自体にも種を撒きます。そうしなければ、直後で計算する
   `screenChanged` ポリシー用の `before` が、この同じ最初のネストしたステップで `prev_after` が
   まだ未設定であることを見て、同じ web ドライバへ同じ動作前の時点をもう一度、重複して
   問い合わせてしまいます。この `before` の計算では、手元にあるツリーが今回のイテレーションで
   読んだばかりのものか、それとも前のステップの境界から持ち越したスナップショットかを
   `before_is_fresh` として追跡します。これは、直後にある割り込みガードが、この値が真のときに
   限って自分自身の再問い合わせを省略するためです。ローカル変数 `pre_query_was_fresh` は、この
   呼び出し自身の `active_driver.query()` が成功したときだけ立てるフラグで、`before` がまさに
   この呼び出しが種を撒いたツリーだった場合に、その `before_is_fresh` へ引き継ぎます。そうで
   ない場合（すでに最新の `prev_after` を前のステップから引き継いで再利用するだけの場合）は
   立てないままにし、この項目より前と挙動を変えません。これがなければ、ガードは実際には
   読み直す必要のないツリーを古いものとみなし、自分自身の重複した `query_dom()` を発行して
   しまいます。

2. **シナリオの最終ステップの欠落を閉じます。** ステップ *i* の結果をステップ *(i+1)* のステップ前
   ベースラインとして引き継ぐ設計（Unit 1）は、ちょうど 1 つのステップだけを覆えません。実際に
   最後に実行されたステップです。その結果を引き継ぐ後続ステップが存在しないからです。放置すれば、
   そのステップ自身の事後の状態は既定の証跡に一度も残らなくなります。これは、今日すべてのステップ
   （最後のステップを含む）が無条件に `after.png` を受け取っている現状からの後退です。ループが
   進む間、最後の**リーフ**ステップの識別情報を追跡します。2 つの並行した `Optional` フィールドでは
   なく、`LastLeafStep(outcome, step_id)` という 1 つの値にまとめます（`loop.py`）。こうする
   ことで、2 つは常に一緒に設定されることが構造上保証され、利用側も 1 回の `is not None` 判定だけで
   両方とも絞り込めます。`StepLoopState` は単一の `last_leaf: LastLeafStep | None` フィールドを
   持ちます。`_handle_action` は、`self.state.prev_after = screen.cached` の隣、その末尾でこれを
   構築します。`_handle_action` は、動作を伴うすべての種別と `wait` / `assert` / `email` が通る唯一の
   ハンドラなので（`_run_one`、`loop.py:754-775`）、これは `if` / `forEach` / `web` コンテナ自身の
   記録用 outcome では一度も発火せず、実際に最後に実行されたリーフステップでだけ発火します。
   `_handle_action` には、その末尾の代入より前に早期 `return` が 1 箇所だけあります。`handleSystemAlert`
   ステップが、対象の locale にカバーされたラベルを持たない場合（`UncoveredSystemAlertLocale`）に
   失敗して即座に返るパスです。そのため、このパスでは自分自身の outcome を追加するその場で
   `last_leaf` も設定します。そうしないと、この失敗で終わるシナリオは、最終取得が前のステップの
   古い `last_leaf` に付いてしまうか（単一ステップのシナリオなら）まったく付かなくなってしまいます。
   トップレベルの `exec_steps` 呼び出しが `_run_steps` で返ったあと、その結果の成否にかかわらず、
   `leaf.outcome.artifacts` を次の呼び出しでもう一度拡張します。
   `self.cfg.sink.capture(driver, leaf.step_id, ["screenshot.after"])`。これが追加するのはスクリーン
   ショットだけで、`elements` は意図的に含めません。`elements.json` はファイル名が 1 つに固定されて
   いるため、ここで再取得するとステップ前ベースラインの動作前ツリーを事後のツリーで上書きしてしまい
   ます。一方 `reads.py` の `_step_artifacts`（Unit 5）は `screenshotUrl` を**最初に**記録された
   スクリーンショット、つまり `before.png` に解決したままです。これでは、エディタの要素ピッカーが
   同じ瞬間を指すことに依存している、対になったスクリーンショットとツリーがずれてしまいます。
   最後のステップを含むすべてのステップで `elements.json` を動作前のツリーのままにしておけば、
   その組は実行全体を通じて一貫します。`after.png` は、マニフェストを直接読む用途のための生の
   証跡として記録されますが、現在のビューア（HTML レポートと serve エディタ）はいずれもステップ
   の表示用スクリーンショットを最初に記録されたもの、つまり `before.png` に解決するため、既定
   では表示されません。最後のステップに限って `after.png` を優先させることは、別の将来の検討課題
   とします。この取得は常に `self.cfg.driver`（ネイティブ）を対象にするため（Unit 1 のステップ前
   呼び出しと同じ選択であり、スクリーンショットにはそもそもツリーが不要です）、Unit 1 のステップ前
   呼び出しとは異なり、web ドライバへの再問い合わせも、それ専用の例外保護も必要ありません。

3. **重複する `.before` トークンを、事後の呼び出しから除きます。** シナリオのインライン `capture`
   や `capturePolicy` ルールは、引き続き `screenshot.before` を明示的に書けます。Unit 1 のステップ前
   呼び出しがすでにそのファイルを書いているため、事後の呼び出しリスト（`loop.py:1093`。
   `_collect_captures` から供給される）は、`sink.capture(...)` に渡す前に `screenshot.before` トークン
   を取り除きます。こうして事後経路が、その名前で誤ったピクセルを撮り直すことは二度となくなります。
   これは動機で述べた不具合を、ベースラインだけでなくすべての呼び出し元について閉じます。
   `screenshot.after`・修飾子なしの `screenshot`（既定で `after` になる）・`screenshot.around` は
   変更しません。今日と同じ地点で、引き続き事後に発火します。

4. **`elements` は、ルールが要求する場合に限り事後でも発火し続けます。** `elements` には取得
   タイミングの修飾子がなく（`docs/evidence.md:44-46`）、ファイルは `elements.json` の 1 つだけです。
   `capturePolicy` ルール（[evidence.md](../../docs/evidence.md#a-capturepolicy-rule-based) にある
   「エラー時に最大限の証跡を取る」パターンなど）が `elements` も要求している場合、そのルールの事後
   書き込みは引き続き発火し、ディスク上のステップ前の内容を上書きします。ルールの条件
   （`screenChanged` / `result: error`）はステップが実行されたあとにしか判定できず、そのルールが
   本当に見たいのは事後の状態だからです。エラールールがステップの残した壊れた画面を映すのは、
   抑えるべき欠陥ではなく正しい挙動です。この項目が変えるのは既定の挙動、つまり一致するルールが
   ないときにすべてのステップが受け取る証跡だけであり、ルール自身の明示的な要求は変えません。
   シナリオ作者が知っておくべき優先順位として明記します。ステップ前のベースラインは下限であり、
   発火したルール自身の取得リストが上限です。Unit 2 の最終ステップの取得は、ルールではなく
   1 つのステップに無条件に適用される、まったく同じ事後の形です。
   この選択が受け入れている副作用も明記します。`reads.py` の `_step_artifacts`（Unit 5）は、
   `screenshotUrl` を引き続き**最初に**記録されたスクリーンショット（`before.png`、発火したルール
   自身の `screenshot.after` / `.around` が触れない別ファイル）に解決します。そのため、`elements`
   も要求するルールが発火すると、エディタの要素ピッカーが対応づけているペアは、動作前の
   `before.png` と動作後のツリーという組み合わせになってしまいます。`elements` にも
   `screenshot` と同様の取得タイミングの修飾子を持たせれば、ルールの事後ツリーを共有の
   `elements.json` を上書きせず別名で書き込めるようになり、Unit 2 が最後のステップについて
   閉じたのと同じやり方でこれも閉じられます。ただしそれは DSL の変更（新しい取得トークンの形、
   `evidence/core.py`、文書、テストが必要)であり、この項目が変える固定された 1 つの既定の挙動より
   はるかに大きな範囲です。将来の項目に残します。この項目が保証するのは、発火するルールがない
   シナリオが受け取る**既定**のペアだけであり、それを Unit 6 が検証します。

5. **ハードコードされた唯一の利用箇所を修正します。** `bajutsu/serve/operations/reads.py` の
   `_step_artifacts`（`reads.py:411-456`）は、そのステップが実際に記録した内容を読む代わりに、
   `.../elements.json` と `.../after.png` という決め打ちのパスを `_safe_exists` で探しています。
   ベースラインのスクリーンショットが `before.png` になると、この決め打ちの探索は、通常のあらゆる
   ステップに対して「スクリーンショットなし」と報告してしまいます。すでに読み込み済みの
   `manifest.json` のステップごとの `artifacts` 一覧（各要素は `{"name", "kind", "provider"}`。
   `bajutsu/report/manifest.py` の `asdict()` によるシリアライズで確認済み）を読み、種別が
   `"screenshot"` / `"elements"` の最初の要素を選ぶようにします。これは `bajutsu/report/rows.py`
   がすでに使っている `by_kind.setdefault(a.kind, a)` というパターン（`report/rows.py:113-122`）を
   踏襲するもので、こちらは種別による汎用的な読み取りをすでに行っており、変更の必要はありません。
   `_artifact_names` は `by_kind` へ入れる前に、各 `kind` / `name` を単に非 `None` であることだけ
   でなく `str` 型であることまで絞り込みます。壊れた、あるいは書き込みが途中で止まった manifest
   のエントリが、そこから作る URL へ文字列でない値を流し込む代わりに「リンクなし」に落ち着くよう
   にするためです。`_step_artifacts` は、削除した `_find_sid` が `sid` に対して意図的に適用していた
   `or None` という補正も復元します。空文字列の `sid` を持つシナリオレコードが、`/step0` のような
   壊れたステップ ID を組み立てる代わりに、`sid` が欠けている場合と同じように `[]` へ抜けるように
   します。このステップごとの参照は、各 outcome 自身が記録した証跡パスから取り出した実行時のステップ
   ID（`name.rsplit("/", 1)[0]`）をキーにし、outcome の `index` はキーにしません。`index` は
   `if` / `forEach` / `web` のネストした実行ステップも含めた、実行された全ステップを数えるカウンタ
   です。一方、`matched.steps` を走査するループはトップレベルの YAML ステップだけを数えるため、対象
   のステップより前にネストがあれば両者はずれます。名前付きステップの実行時 ID はどちらのカウンタにも
   依存しないため、ネストがあっても正しい証跡を解決し続けます。この探索の各段階、つまり manifest
   自体、`scenarios`、各シナリオの `steps`、各ステップの `artifacts`、そして各証跡は、使う前に
   `isinstance` で確認します。ステップ ID の探索も、最初の要素で止まるのではなく、使える（`str` 型
   でスラッシュを含む）`name` を持たない証跡を読み飛ばします。これにより、書き込みが途中で止まった
   manifest のどこかに壊れた要素が1つあっても、そのステップの証跡が欠けるだけで済み、500 エラーには
   なりません。

6. **決定的スイートで発火順序・最終ステップの取得・読み取り回数の不変条件・非回帰を検証します。**
   `tests/orchestrator/test_loop.py` に、`FakeDriver` を使い、動作を伴うステップ（`tap`）と伴わない
   ステップ（`assert_` / `wait`）の両方について、`screenshot()` / `query()` の呼び出し順を記録し、
   取得呼び出しが動作の呼び出しより前であることを検証するテストを追加します。さらに、複数ステップの
   シナリオを実行し、最後のステップの `outcome.artifacts` にステップ前（`before.png`）と Unit 2 の
   最終ステップ用（`after.png`）の両方が記録され、それより前のステップには前者だけが記録される
   ことを検証するケースと、`if` / `forEach` で終わるシナリオでも最終取得がコンテナ自身の outcome
   ではなく最後の**リーフ**ステップに載ることを検証するケースを追加します。内容レベルのケースでは、
   最後のステップの `elements.json` が動作前のツリーのままであること、つまり `before.png` と
   一致し、`screenshot.after` だけが示す事後の状態には決してならないことを検証します。これは
   Unit 2 の設計上のメモが前提としている対応関係そのものです。
   `tests/test_alerts.py` と `tests/orchestrator/test_waits.py` には、`alert_guard`（反応的な
   システムアラートガード）の 2 つの再試行経路（`_handle_action` の事後 1 回だけの再試行、
   `waits.py` の `_AlertGuardGate` によるステップ実行中の再試行）が、この項目の証跡を壊さないことを
   検証するケースを追加します。再試行・解消されたステップのステップ前ベースラインは引き続き真の
   試行前の状態を示し、**次の**ステップ自身のベースラインは復旧後の落ち着いた状態を反映します。
   アラートが解消される前の古い状態を示すことはありません。
   `tests/orchestrator/test_read_count.py` には、シンクが `elements` を消費しないとき、新しい
   ステップ前の呼び出しがループ発行の読み取りを追加で発生させないことを検証するケースを追加し、
   `test_plain_tap_issues_no_runner_read` がすでに固定している不変条件そのものを守ります。これに
   より、この項目がそれを気づかぬうちに壊すことはなくなります。加えて、`web` ブロック自身のステップ前
   ベースラインが `NullSink` の下でブリッジへの問い合わせを一切発生させないことを検証するケースも
   追加します。`_step_artifacts` をすでに検証している `tests/serve/test_editor_ops.py` には、決め
   打ちの名前ではなく manifest に記録された名前を解決することを検証するケースを追加します。さらに、
   ネストした制御フローのあとに続く名前付きステップが、ネストしたステップのものではなく自分自身の
   証跡を解決することを検証するケースも追加します。最後に、`extract` / `assert` が引き続き事後の
   安定したツリーを読んでいることを検証する回帰テストを加えます。

7. **新しい既定値を文書化します。** [`docs/evidence.md`](../../docs/evidence.md) とその日本語版は、
   「既定の修飾子は … `after` になる」という記述（`docs/evidence.md:44-46`）を、常時発火する
   ベースラインが `screenshot.before` と `elements` であり、ステップの動作前に取得される、という
   記述に改めます。明示的なルールやインラインの要求については、今日どおり `after` が既定のまま
   です。[`DESIGN.md`](../../DESIGN.md) はすでにディレクトリレイアウト（§9）で `before.png` を
   `after.png` と並べて示しており、取得タイミングの表にも同じ区別を加えます。
   [`docs/architecture.md`](../../docs/architecture.md) は、この挙動を記述している箇所がないかを
   確認し、あれば更新します（BE-0113）。

### 機械的に検証可能な結果

決定的スイートで検証します。`FakeDriver` を使うステップが `screenshot` / `query` の呼び出しを順序
つきで記録し、テストは動作を伴うステップ・伴わないステップの両方について、取得呼び出しが動作の
呼び出しより前であることと、シナリオの最後のステップの `outcome.artifacts` にステップ前と最終
事後の両方の項目が記録されることを検証します。この経路に AI は入りません。`make check` が判定を
下す点は、`loop.py` の他のテストと変わりません。

## 検討した代替案

**名前は変えず、既存の `after.png` の書き込みを早めるだけにする。** 最初に検討した形です。
ファイル名は `after.png` のままにし、ピクセルを撮るタイミングだけを早めます。`reads.py` や、
`after.png` の存在を前提とするすべての箇所を、そのまま残せます。この案を採らなかったのは、
証跡自身の名前が中身について嘘をつき続けることになるからです。`_BASELINE_INSTANT` やレポートの
`after.png` を読む保守担当者には、この項目を読まない限り、それが動作前のスクリーンショットを
保持していると知る手立てがありません。`screenshot.before` / `before.png` は、すでにこのコード
ベース自身の語彙にあります（`docs/evidence.md`、`DESIGN.md`）。それを使い直すコストは、固定の
利用箇所 1 か所（`reads.py`、Unit 5）と引き換えに、名前が中身どおりになることで見合います。

**ルールが発火させるすべての瞬時証跡を、ベースラインと同様にステップ前へ動かす。** 動機の議論に
隣接する形ですでに検討し、採らないと判断しました。`screenChanged` と `result: error` のトリガーは
ステップが実行されたあとでしか判定できず、それらが取得したいのはまさに事後の状態、つまりステップが
引き起こしたエラーや変化だからです。これらをステップ前へ動かしても不具合は直らず、実際に何が
うまくいかなかったかを見る手段が失われるだけです。

**`elements` にも `screenshot` と同様の事前・事後修飾子を持たせる。** これができれば Unit 4 の
優先順位のルールは丸ごと不要になり、事後のツリーを求めるルールは、ベースラインの `elements.json`
を変えずに `elements.after.json` を書けます。この案は、確立済みの単一ファイルという証跡の形を、
`elements.json` を読むすべての利用箇所（`report/rows.py`、`reads.py`、`object_store.py`、golden
比較や視覚回帰の経路、それぞれのテスト）にわたって変えることになります。しかもその対象は、
ベースラインに加えて `elements` を要求するケースであり、`_collect_captures` の既存の重複排除が
すでに 1 回の書き込みとして扱っているものです。この規模には見合わないと判断し、小さくスコープを
絞った項目として、その形は変えずに 1 つの優先順位のルールを文書化する道を選びました。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] Unit 1 — ステップ前の取得呼び出しを追加し、`self.state.prev_after` を再利用する。
      ベースラインを `_collect_captures` から外す。
- [x] Unit 2 — シナリオの最後のリーフステップの outcome に、最終ステップの欠落を閉じる事後の
      取得を追加する。
- [x] Unit 3 — 重複する `.before` トークンを事後の呼び出しから除く。
- [x] Unit 4 — 事後の `elements` / `screenshot.after` の優先順位を文書化し、挙動は変えない。
- [x] Unit 5 — `reads.py` が決め打ちのパスではなく manifest に記録された証跡の名前を読むようにする。
- [x] Unit 6 — 取得順序・最終ステップの取得・読み取り回数の不変条件、`reads.py` の解決、
      `extract` / `assert` の非回帰について決定的な網羅を追加する。
- [x] Unit 7 — `docs/evidence.md`（日本語版含む）、`DESIGN.md`、`docs/architecture.md` を更新する。

## 参考

- [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md) — 遅延評価でキャッシュ
  される事後の読み取り（`_ScreenRead`、`prev_after`）。この項目のステップ前の取得呼び出しは、
  最初のステップを除くすべてのステップでこれを追加コストなしに再利用します。
- [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md) ·
  [BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md) — `extract` / `assert` が使う
  事後の安定したツリー読み取り。この項目は意図的に触れません。
- [BE-0028](../BE-0028-evidence-rule-overmatch-guard/BE-0028-evidence-rule-overmatch-guard.md) —
  この項目のトピックが並ぶ、証跡ルールの過剰発火ガード。
- [`docs/evidence.md`](../../docs/evidence.md) — この項目が `screenshot` について正直にする、
  取得タイミングの修飾子（`before` / `after` / `around`）。
