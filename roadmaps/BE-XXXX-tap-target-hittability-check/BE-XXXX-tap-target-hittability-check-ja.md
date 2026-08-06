[English](BE-XXXX-tap-target-hittability-check.md) · **日本語**

# BE-XXXX — 操作前にタップ可能性を検証し、回数を区切ったスクロールを安全網として備える

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-tap-target-hittability-check-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#1524](https://github.com/bajutsu-e2e/bajutsu/pull/1524) |
| トピック | ドライバとバックエンドのアーキテクチャ |
| 関連 | [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element-ja.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`tap`(および `double_tap`、`long_press`、`type` / `clear` / `select` の内部にあるフォーカスタップ)は、セレクタを 1 つの要素へ解決し、その frame の中心点を操作します。解決の段階では、その点が画面上で実際に到達可能かどうかを一切確認しません。要素はセレクタに一意に一致し、有効な frame を持ちながら、固定ヘッダーやトースト、あるいは薄暗いモーダルの背景の下に置かれることがあります。その場合、タップは対象ではなく遮蔽物に着弾します。各バックエンドは今後、自分のプラットフォームにもっとも自然な方法で、解決済みの対象がその点に実在するものかどうかを操作前に確認します。確認に失敗すると、オーケストレータが遮蔽物を取り除くために、回数を区切った決定的なスクロールを最大 3 ステップまで試み、操作をもう一度だけ再試行します。それでも対象へ到達できなければ、操作は誤解を招く `ElementNotFound` ではなく、新設の専用エラー `ElementNotTappable` で失敗します。

## 動機

`bajutsu/drivers/base.py` の `Element` は、`identifier` / `label` / `traits` / `value` / `frame` だけを持つ平坦なレコードです。`children`、`parent`、Z 値のいずれも持ちません。`resolve_unique`(`base.py:647`)は、すでにセレクタの曖昧性を防いでいます。2 件以上が一致すると、prime directive 2 に従ってステップは即座に失敗します。その一方で、遮蔽を防ぐ仕組みはありません。一致は 1 件で、その frame も実在するのに、その点における最前面の要素ではない、という状況を検出できません。

各バックエンドのタップ経路は、点を 1 つ解決するとプラットフォームのプリミティブをただちに発火し、その点が到達可能かを一度も確認しません。XCUITest の `tap()`(`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:57`)は、ネイティブの `XCUIElement.tap()` を直接呼び出します。Playwright の `tap`(`bajutsu/drivers/playwright.py:566`)は、生の座標に対して `page.mouse.click(x, y)` を呼び出し、ロケータ経由のクリックなら無償で得られるアクショナビリティ確認を素通りします。adb の `tap`(`bajutsu/drivers/adb.py:1097`)は frame を解決して `adb.tap_cmd` へシェルアウトします。既存の回復手段は「見つからなければ、そちらへ向けてスクロールする」(`_scroll_into_view`、`adb.py:915`)だけです。「見つかったが、覆われている」場合を扱う手段は何もありません。

シナリオの作者は、これが起きたことを見る手立てを持ちません。ランは、タップが遮蔽物へ静かに着弾するために何も起こらないか、セレクタ自体は解決しているのに `ElementNotFound` を返して作者を積極的に誤導します。

[BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element-ja.md) は、関連しながらも別の問題を、明示的な `scroll` アクションですでに解決しています。その問題とは、要素がいまのビューポートに存在しない状態です。BE-0326 の「検討した代替案」は、この scroll-into-view を `tap` の内部で暗黙に行う案を主案として却下し、暗黙の自動スクロールは意図を隠すと述べました。その理由は、本提案へそのまま持ち越せません。BE-0326 が却下したのは、対象が最初から画面外にあると作者がすでに知っている場面で、明示的な `scroll` を書く代わりに暗黙のスクロールへ置き換える案です。本提案はその置き換えではありません。対象が fold より下にあるとすでに知っている作者は、そのままでも `scroll` を書くべきです。

本提案は、セレクタがすでに解決した要素に対する正しさの確認です。その回復手段は、あえて狭く作ります。ステップ数の上限は小さく、方向は 1 つだけ、しかも新設の遮蔽シグナルによってのみ起動します。この形は、adb 自身の not-found フォールバックがすでに持つ形と同じです。本提案はそれを遮蔽のケースへ一般化し、全バックエンドへ広げます。

## 詳細設計

### バックエンドごとのタップ可能性チェック

新しい `Driver` プロトコルのメソッド `is_tappable(self, sel: Selector) -> bool` を追加します(`base.py`、`Driver` プロトコル内の `tap` / `double_tap` / `long_press` の隣、`base.py:186`–`218`)。このメソッドは、`ViewportProvider` / `ReadLagProvider` / `SettledReadProvider`(`base.py:277`–`369`)のような狭い opt-in ではなく、必須のメンバーとしてプロトコルへ加わります。必須にする理由は、狙いが全面的なカバレッジそのものだからです。どのバックエンドも、`sel` を 1 つの要素へ解決するのに、すでに使っている同じ決定性の核 `resolve_unique` を通します。そのうえで、その要素が自分の点において実際に到達可能なものかどうかを、自分の流儀で確認します。`is_tappable` は純粋な問い合わせであり、決して操作しません。そのため、タップ経路はこれを一度呼んで操作を守れますし、後述のスクロール回復ループは、副作用なしにこれを何度でも呼び直せます。

**iOS(XCUITest)** は、プラットフォームがすでに計算しているネイティブのシグナルを使います。Apple 自身の [`isHittable`](https://developer.apple.com/documentation/xctest/xcuielement/1500561-ishittable) のドキュメントは、この値が要素の不在時、画面外にあるとき、あるいは別の要素に覆われているときに `false` を返し、それ以外では `true` を返すと述べています。近似ではなく、到達可能性そのものへのネイティブな回答です。本提案では、ドキュメントだけを信じるのではなく、実機で確認しました。スパイク用の画面で、`ScrollView` の中に固定オーバーレイを重ね、その下にボタンを配置しました。iOS シミュレータ上で XCUITest のテストを 5 回実行したところ、対象がオーバーレイの下にある間は `isHittable` が一貫して `false` を返し、スクロールでオーバーレイをどかすと `true` に変わりました。対象自身の frame は終始画面内にとどまっており、この遷移が画面外への移動ではなく遮蔽を追跡していたことを裏づけます。コミュニティの報告([Apple Developer Forums のスレッド](https://developer.apple.com/forums/thread/720155))は、`isHittable` がクリーンな真偽値を返す代わりに「Failed to determine hittability」エラーを投げることがあると述べています。今回のスパイクは 5 回の実行で合計 60 回読み取りましたが、この失敗を一度も再現しませんでした。これは、この環境で再現しなかったという事実にとどまり、他の環境でこの問題が存在しないことの証明ではありません。

`XcuitestElementProvider.tap(backingElement:taps:duration:)`(`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:57`)は、ネイティブのタップ呼び出しの直前にガードを 1 つ得ます。`guard el.isHittable else { return .notHittable }` です。`TapResult`(`BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift:31`)は、既存の `.ok` / `.stale` / `.notFound` の隣に `.notHittable` ケースを得ます。`xcuitest.py` の `_actuate` は、4 つ目のステータス定数と分岐を得て、既存の stale / not-found の扱いと並行に `base.ElementNotTappable` を送出します。`is_tappable(sel)` は、同じ往復の軽量版として実現します。ハンドルを解決し、操作せずにランナーへ `isHittable` を尋ねるだけです。

**web(Playwright)** は、このコードベースにすでに前例がある `document.elementFromPoint` のパターンを再利用します。前例は `select_option`(`playwright.py:764`–`782`)です。対象の中心は、既存の `base.frame_center(base.resolve_unique(...))` で解決します。そのうえで、`document.elementFromPoint(x, y)` から祖先方向へたどり、解決済みの対象がその経路に現れるかどうかを確認する JavaScript を評価します。経路が対象へ一度も到達しないなら、無関係な要素がその点を実際に覆っているということです。`is_tappable` は `false` を返し、`tap` / `double_tap` / `long_press` は `page.mouse.click` / `dblclick` を呼ぶ代わりに `base.ElementNotTappable` を送出します。すべてのアクチュエータは、すでに 1 つの継ぎ目 `_center_with_element(sel)`(`playwright.py:531` — `tap`、`double_tap`、`long_press`、`select_option` のいずれもこれを直接呼んでいます。これを内部で呼ぶだけの薄いラッパー `_center(sel)`〔`playwright.py:527`〕自体には呼び出し元がありません)を通して点を解決しています。そこでこのチェックは、ロジックを三重化する代わりに、その継ぎ目を `_center_checked(sel)` へ置き換えます。生の座標での操作を保ち、Playwright 自身の(独自のアクショナビリティ確認を持つ)`locator.click()` へ乗り換えず、代わりに点の確認を足す理由は 2 つあります。1 つは、`find_all` / `resolve_unique`(`base.py:534`、647)が全バックエンドで共有する唯一の決定性の核であることです。Cascading Style Sheets(`CSS`)セレクタやテキストロケータは、その核がすでに扱う OR 結合の id リストや `within` による幾何的スコープを理解できません。もう 1 つは、`locator.click()` の自動待機が暗黙のタイムアウトと再試行の形を持ち、ドライバ層の他の部分が守る「一度解決し、一度確認する」契約ではないことです。

**Android(adb)** には対応するネイティブのシグナルがありません。スパイクが明らかにしたのは、単一のきれいな近似ではなく、2 つの UI ツールキットのあいだの実在する非対称でした。`bajutsu/drivers/adb.py` の `parse_hierarchy` / `parse_hierarchy_with_identities`(`adb.py:295`–`345`)は、すでに UI Automator のダンプを並べ替えなしの文書順でたどっています。この文書順は、XCUITest 自身の `flattenSnapshot`(`BajutsuKit/Sources/BajutsuRunner/PositionPath.swift:69`)がすでに使っている、「後に並ぶ兄弟ほど後から描かれる」という代理指標と同じものです。`elevation` を使わないスパイク画面では、Jetpack Compose と View システムのどちらでも、文書順は視覚的な前後関係と一致しました。これは通常のケースを裏づけます。Compose で `Modifier.zIndex` を使い、先に宣言されているのに手前に描かれる要素を配置したスパイク画面では、アクセシビリティのセマンティクスツリー自体が視覚的な結果に合わせて並べ替わりました。文書順という経験則が本来持つはずの盲点は、Compose では実際には現れませんでした。View システムで同等の `View.elevation` を使ったスパイクでは、この盲点が実際に現れました。`elevation` を持つビューは手前に描かれる一方で、文書順では先に留まったままです。つまり、文書順だけを読むと、その点をどの要素が覆っているかを誤って判断します。Compose ではさらに別の限界も見つかりました。軽量な位置指定モディファイア `Modifier.absoluteOffset` は、ビューの描画位置を画面上で動かす一方、アクセシビリティツリーは元の bounds を報告し続けます。そのため、座標だけに基づくチェックは、文書順の正確さとは無関係に、すでに誤った frame から出発してしまうことがあります。

これを踏まえ、`base.py` は `_contains`(`base.py:527`)と `frame_center`(`base.py:737`)の隣に新しいヘルパー `topmost_at_point(elements: list[Element], point: Point, target: Element) -> Element | None` を得ます。このヘルパーは、文書順に並んだリストを走査し、対象自身の子孫(子を貫通するタップは対象へのタップのままとする)と祖先を除外したうえで、その点を含む最後の要素を探します。`None` でない結果は、無関係な要素がその点を覆っていることを意味します。`adb.py` の `_resolve_frame_and_screen`(`adb.py:892`)は、frame を解決した直後にこれを呼びます。既存の not-found 時の `_scroll_into_view` 回復のあとという順序は変えません。一致すれば、`adb.tap_cmd` が発火する前に `base.ElementNotTappable` を送出します。動機の節に記したとおり、この既知の限界は率直に文書化します。この確認は `elevation` を使う View ベースのレイアウトを誤判定しうる一方、Compose の `zIndex` は影響を受けません。また、Compose のレイアウトが軽量なオフセットモディファイアを使うと、すでに古い frame を渡してしまうことがあります。

`_resolve_frame_and_screen` はタップ系専用ではありません。`adb.py:889` はタップ系のために、`adb.py:1295` は二本指の `pinch` / `rotate` ジェスチャのために、それぞれこれを呼びます。この共有された継ぎ目にチェックを組み込むと、覆われた `pinch` / `rotate` の対象も `ElementNotTappable` を送出するようになります。実際に別の要素に覆われている対象に対しては、これが正しい結果です。ただし今回のスクロール安全網は、そこまでは届きません。`_tap_with_recovery`(後述)が包むのはタップ系だけなので、このチェックが入ると、覆われた対象への `pinch` や `rotate` はスクロールを試みることなく即座に失敗します。二本指ジェスチャへ回復ラッパーを広げるのは、本提案の最初のスライスではなく、後続の作業に残します。

`is_tappable(sel)` の Android での実現は、それとは別の、副作用のない読み取りのままにします。新しくツリーを settle し、`resolve_unique` で `sel` を解決し、`topmost_at_point` を実行します。`_resolve_frame_and_screen` が持つ not-found 時の `_scroll_into_view` フォールバックは経由しません。`is_tappable` をそのフォールバック経由にしてしまうと、1 回の問い合わせが静かに画面をスクロールしうることになり、後述の `scroll_until_tappable` の停止述語がこれを繰り返し呼ぶたびに、その問い合わせ自身のステップ数上限へ二重に計上されてしまいます。これは、このタップ可能性チェックがどのバックエンドでも約束している「副作用がない」という性質そのものに反します。not-found 時のスクロールフォールバックを保つのは、`_resolve_frame_and_screen` を経由する操作時の呼び出しだけであり、タップ可能性の問い合わせ自体はスクロールしません。

`AccessibilityWindowInfo.getLayer()` を使って、ウィンドウをまたぐ遮蔽(ダイアログ、トースト、画面全体を覆うシステムオーバーレイ)を検出する案も検討しました。これは、ウィンドウの相対的な Z 順序を報告する公式の Application Programming Interface(`API`)です。スパイクの結果、この API は存在が示唆するほど実用的ではないとわかりました。これを読み取るだけでも、UiAutomation 自身の `AccessibilityServiceInfo` に `FLAG_RETRIEVE_INTERACTIVE_WINDOWS` を設定する必要があり、これはデフォルトでは設定されていません。もっとも一般的な実際のケースであるフルスクリーンのモーダルダイアログでは、異なる層に共存する 2 つのウィンドウは生まれず、比較のしようがありませんでした。ベースウィンドウは `getWindows()` から完全に姿を消し、ダイアログのウィンドウがその場所を占めました。つまり、この一般的なケースは、層の比較ではなく、対象セレクタが解決不能になることを通じて表面化します。トーストは `getWindows()` に一切現れないため、この仕組みではボタンを覆うトーストを検出できません。この隙間は、スパイクの持ち時間の中では埋められませんでした。この準備コストと、この 2 つの現実の隙間を踏まえ、本提案はウィンドウレベルの遮蔽検出を最初のスライスの範囲外に置きます。詳細は「検討した代替案」を参照してください。

### 新設の型付きエラー `ElementNotTappable`

`bajutsu/drivers/base.py` に、`ElementNotFound` / `AmbiguousSelector`(`base.py:421`–`427`)の隣へこれを加えます。ただし `SelectorError` のサブクラスにはしません。

```python
class ElementNotTappable(Exception):
    """The selector resolved to exactly one element, but it could not be reached at its own point
    (obstructed by another on-screen element, or the platform's own hit-test refused it) — even
    after the bounded scroll safety net tried to clear the obstruction.

    Distinct from SelectorError: resolution succeeded. Only reachability failed.
    """
```

`SelectorError` 自身のドキュメント文字列「selector resolution failed」は、ここでは端的に誤りになります。セレクタ自体は解決しているので、これはサブクラスではなく並列のトップレベル例外です。`loop.py:399` の汎用的なステップ実行の catch、`except (base.SelectorError, base.UnsupportedAction, NotImplementedError) as e: return False, str(e), [], None` は、そのタプルに `base.ElementNotTappable` を加えます。これにより、この例外を送出したステップも、ラン全体をクラッシュさせず、クリーンに失敗します(prime directive 1)。各バックエンドは、本来なら本当に存在しない要素に対して `ElementNotFound` を送出していた地点でこれを送出します。`xcuitest.py` の `_actuate` は新しい not-hittable ステータスで送出します。`playwright.py` のタップ系メソッドはヒットテスト失敗で送出します。`adb.py` の `_resolve_frame_and_screen` は `topmost_at_point` が非 `None` を返した場合に送出します。

### 安全網としての、回数を区切った決定的スクロール回復

`is_tappable` が失敗するたびに既存の `scroll_to_target(driver, sel, "down", None, max_scrolls)`(`bajutsu/orchestrator/actions/handlers/scroll.py:442`)を呼ぶのは、一見自然な選択に見えます。しかし、実際には何もしないバグです。`scroll_to_target` の停止条件は「`to` が解決し、その frame の中心がビューポート内にある」(`_center_in_viewport`、`scroll.py:123`)ことです。遮蔽された要素の中心は、すでにビューポート内にあります。画面外ではなく遮蔽されているのは、まさにそのためです。手を加えずに呼ぶと、1 回もスクロールせずにただちに戻ってしまいます。

この修正は、`scroll_to_target` の唯一のハードコードされた停止確認の部分を一般化します。BE-0329 の動きと末尾検出の管理という数百行を複製するわけではありません。そのループ本体(`scroll.py:474`–`537`)は、`_center_in_viewport` をハードコードする代わりに、停止述語を引数として受け取ります。`scroll_to_target` は既存のシグネチャと挙動を保ち、デフォルトとして `_center_in_viewport` を渡します。新しい関数 `scroll_until_tappable(driver, sel, direction, within, max_scrolls)` は、代わりに `lambda frame, viewport: _center_in_viewport(frame, viewport) and driver.is_tappable(sel)` を渡します。それ以外の行、末尾検出による早期失敗、行き過ぎの検出、read-lag の再読み取り予算、ビューポートの契約は、すべて共有されたまま変わりません。

この停止述語が両方の確認を論理積で結び、どちらか一方だけに緩めていないのは、本提案が保とうとする不変条件が両方向に効くからです。`scroll_until_tappable` は「ビューポートへスクロールして入ったこと」をタップ可能性の証拠として扱ってはいけません。遮蔽された対象の中心はすでにビューポート内にあり、まさにそれゆえに `_center_in_viewport` 単体ではここで意味を持たないのです。しかし同じくらい、「ビューポートの外へスクロールして出たこと」もタップ可能性の証拠として扱ってはいけません。複数のバックエンドは、ビューポート外の点をあえて「覆われていない」と読みます(fold より下の対象は `scroll` の問題であり、遮蔽の問題ではないからです)。そのため `is_tappable` 単体では、ループが遮蔽された対象を、覆いから外すのではなくビューポートの外へスクロールさせることで「成功」してしまい、そのあとに続く座標タップはビューポートの外に着地し、何にも触れません。どちらかの確認が失敗し続けたままスクロールの上限を使い切ることは、常に失敗です。途中でビューポート所属や末尾検出、単なる再照会のどのシグナルが満たされたように見えても、この判定は変わりません。この失敗は `ElementNotTappable` として表面化します。`scroll_until_tappable` を追加するユニットと、それをテストするユニットの両方が、この両方向の不変条件を明示的に述べます。テストスイートには、対象をビューポートへスクロールさせつつ別の要素がまだそれを覆っているケースと、覆いが外れるのが対象をビューポートの外へスクロールさせたあとであるケースの両方を持たせ、どちらの方向も誤って成功と読まれないことを確認します。

`scroll.py` はすでに `gestures.py` から `_SWIPE_FRACTION` と `_scroll_gesture` を import しています。回復の配線を(`_do_tap` / `_do_double_tap` / `_do_long_press` がすでに置かれている)`gestures.py` に置きながら、`scroll.py` の `scroll_until_tappable` を呼ぶ必要があると、2 つのモジュールが互いを import することになります。この修正は、小さく機械的な前提作業です。`_SWIPE_FRACTION` と `_scroll_gesture` を、`gestures.py` と `scroll.py` の両方が import する中立なモジュール(たとえば `_gesture_math.py`)へ抽出します。挙動の変更はありません。これは、回復の配線より前に、独立したユニットとして着地させます。

配線そのものは、`gestures.py` にある小さなラッパー `_tap_with_recovery(actuate, driver, sel)` です。これは指定された `driver.tap(sel)` / `driver.double_tap(sel)` / `driver.long_press(sel, duration)` を呼びます。`base.ElementNotTappable` を受けると、`_TAP_RECOVERY_DIRECTIONS = ("down", "up")` の各方向について `scroll_until_tappable(driver, sel, direction, None, _TAP_RECOVERY_MAX_SCROLLS)` を呼び、`sel` がタップ可能になった最初の方向で止めたうえで、操作をちょうど一度だけ再試行します。方向を1つに固定すると、よくある2種類の遮蔽の両方には対応できません。`down` は画面上のコンテンツを上方向へ動かすので、下端に張り付いた遮蔽(トースト、スナックバー、固定フッター)は取り除けますが、上端に張り付いた遮蔽(スティッキーヘッダー)の下に入り込んだ対象はさらにその下へ押し込んでしまい、抜け出せません。下端の遮蔽のほうがよくあるケースなので、まず `down` を試し、その回数上限を使い切ってもタップ可能にならなかったときにかぎり `up` を試します。この経路上のどの失敗も、単一の `base.ElementNotTappable` へ収束します。最初の試行自身が投げた例外(`base.raise_if_covered` により、対象を覆っていたものを名指しします)は、捨てられるのではなく最終メッセージへ埋め込まれます。回復のきっかけとなった、最後に試した方向のスクロールの失敗は、`raise ... from exc` で連鎖させ、その隣に添えます。どちらの事実も失われません。`_do_tap`、`_do_double_tap`、`_do_long_press`、そして `_do_type` / `_do_clear` / `_do_delete` / `_do_select` の内部にあるフォーカスタップの呼び出し箇所は、いずれも生の `driver.tap(sel)` 呼び出しをこの 1 つの共有ラッパーへ切り替えます。同じ try/except を 7 か所へ複製するのではありません。`_TAP_RECOVERY_MAX_SCROLLS` は方向ごとに小さく保ち、両方向を合わせても `scroll` 自身のデフォルトである 15 を大きく下回ります。これは探索ではなく、一時的なオーバーレイや、スティッキーなヘッダーやフッターといった、よくあるケースのための安全網です。対象を特定の方向、特定のコンテナ越しにスクロールする必要があるとすでに知っている作者は、それでも明示的な `scroll` アクションを書きます。この安全網が保険をかけるのは、作者が想定していなかったケースだけです。

### 作業分解(相互排他的かつ全体を尽くす、Mutually Exclusive, Collectively Exhaustive、`MECE`)

作業単位は次のとおりです。

0. **前提となるリファクタリング。**

   [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py) から `_SWIPE_FRACTION` と `_scroll_gesture` を、これと `scroll.py` の両方が import する中立なモジュールへ抽出する。
   挙動は変えない。
   Unit 6 の前提を外す。

1. **`ElementNotTappable` エラー。**

   [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) に、`ElementNotFound` / `AmbiguousSelector` の隣としてこのクラスを追加する。
   [`bajutsu/orchestrator/loop.py:399`](../../bajutsu/orchestrator/loop.py) のステップ実行 catch タプルにも加える。

2. **`Driver.is_tappable` と、差し替え可能なスクロール停止条件。**

   `Driver` プロトコルに `is_tappable(self, sel: Selector) -> bool` を `base.py` へ追加する。
   [`scroll_to_target`](../../bajutsu/orchestrator/actions/handlers/scroll.py) をリファクタリングし、停止述語を引数として受け取れるようにする。
   既存のデフォルトと既存の呼び出し元は保つ。
   `scroll_until_tappable` を追加する。
   その停止述語は `is_tappable` そのものであり、ビューポート確認や存在確認をその代わりに使うことはない。

3. **iOS のヒットテスト。**

   `TapResult.notHittable` ケース(`BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift`)。
   `XcuitestElementProvider.tap` の `isHittable` ガード(`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:57`)。
   `Router.tapResultResponse` のマッピング。
   `xcuitest.py` の `_actuate` に新しいステータス定数と分岐を追加する。
   `is_tappable` は、同等の操作しない問い合わせとして実現する。

4. **web のヒットテスト。**

   [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py) に、`document.elementFromPoint` に基づく祖先チェインのチェックを追加する。
   `select_option` の前例(`playwright.py:764`–`782`)を一般化し、`tap` / `double_tap` / `long_press` が共有する `_center_checked(sel)` にする。

5. **Android のヒットテスト。**

   [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) に `topmost_at_point` を追加し、`adb.py` の `_resolve_frame_and_screen`(`adb.py:892`)へ、既存の not-found 時の `_scroll_into_view` 回復のあと、frame をアクチュエータへ返す前に配線する。
   この継ぎ目は二本指の `pinch` / `rotate` ジェスチャ(`adb.py:1295`)とも共有されているため、覆われた対象はそちらでも `ElementNotTappable` を送出するが、本提案のタップ専用ラッパーが提供するスクロール回復は及ばない。この範囲の境界は暗黙のままにせず、明示的に書き残す。
   `is_tappable` の Android での問い合わせは、settle してから `resolve_unique`、そして `topmost_at_point` という独自の経路として実現し、`_resolve_frame_and_screen` の not-found 時 `_scroll_into_view` フォールバックは経由しない。これにより、問い合わせ自体は決してスクロールしない。
   この経験則の限界をコードコメントとして書いておく。
   Compose の `zIndex` では成り立ち、両ツールキットの通常のケースでも成り立つこと。
   `elevation` を使う View ベースのレイアウトは誤判定しうること。
   軽量な Compose のオフセットモディファイアに依存すると bounds が古くなりうること。

6. **オーケストレータの回復配線。**

   `gestures.py` の `_tap_with_recovery` を、`_do_type` / `_do_clear` / `_do_delete` / `_do_select` 内部のフォーカスタップを含む、すべての `driver.tap` / `driver.double_tap` / `driver.long_press` の呼び出し箇所へ適用する。
   Unit 0 と Unit 2 に依存する。

7. **`FakeDriver` 対応。**

   `FakeDriver.is_tappable`([`bajutsu/drivers/fake.py`](../../bajutsu/drivers/fake.py))は、`self.screen` に対して直接 `topmost_at_point` を再利用する。
   これにより、実機やエミュレータなしに、汎用の回復ループを高速な Linux ゲート上でテストできる。

8. **ドライバ適合スイート。**

   [`tests/driver_conformance.py`](../../tests/driver_conformance.py) に、遮蔽のケースを追加する。
   対象と、その点を共有する 2 つ目の要素を持つ画面を用意し、各バックエンドのフィクスチャがそれぞれの流儀で「手前にある」と表現できる形にする。
   覆われている間は `is_tappable` が偽であり、同じ画面上の別の、実際に覆われていない要素では真になることを確認する。
   覆われている対象に対しては、`driver.tap` 自身が `ElementNotTappable` を送出することを確認する。
   どちらもドライバの継ぎ目で直接確認するもので、
   [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) の、全バックエンドに共通する仕様 1 本という既存のパターンに従い、このケースを実装している各バックエンド(`FakeDriver`、Playwright の実 Chromium ハーネス。実機の iOS/Android ハーネスはまだ実装していません)で走らせる。
   オーケストレータ側の、回数を区切ったスクロール回復(上限を使い切って初めて `ElementNotTappable` を送出し、スクロールが遮蔽を取り除けば成功する)は別の挙動であり、この適合スイートは確認しません(`driver.tap` を直接呼ぶだけで、`_tap_with_recovery` は経由しません)。そのため、この部分は実バックエンドごとではなく、Unit 10 の `tests/orchestrator/test_tap_recovery.py` で `FakeDriver` に対して汎用的に確認したままとします。

9. **ドキュメント。**

   [`docs/drivers.md`](../../docs/drivers.md) の注記(現在は adb の not-found スクロール回復を adb 専用の堅牢化の安全網と記している)を更新し、いまや移植可能になったタップ可能性チェックとそのスクロール安全網を説明する。
   ただし枠組みは変えない。
   対象が最初から画面外にあると作者がすでに知っているときの答えは、引き続き明示的な `scroll` アクションである。
   この確認は、遮蔽されているがツリーには存在するケース向けの、別の狭い正しさの網である。
   `docs/selectors.md` / `docs/drivers.md` とその `docs/ja/` 対訳に、`ElementNotFound` / `AmbiguousSelector` の隣として `ElementNotTappable` を文書化する。

10. **テスト。**

    各ヒットテストの仕組みを個別にカバーするバックエンドごとのユニットテスト。
    `isHittable` ガードと新しい `TapResult` ケース。
    `elementFromPoint` の祖先チェインのチェック(ヒット位置が対象自身の子孫に落ちるケースを含む。これは遮蔽ではない)。
    `topmost_at_point` における子孫と祖先の除外、および実際に覆っているケース。
    `FakeDriver` に対するオーケストレータレベルのテストは `_tap_with_recovery` を対象にする。
    すでにタップ可能なら回復なしで成功すること。
    N 回のスクロール後に回復すること。
    回復の上限を使い切ると `ElementNotFound` ではなく `ElementNotTappable` を送出すること。
    対象がビューポートへスクロールして入りながらまだ覆われているケースで、これが成功と読まれないことの確認。
    曖昧なセレクタの経路は影響を受けず、回復へ届く前に、これまでどおり即座に `AmbiguousSelector` を送出すること。

### prime directive の保持

- **AI は判定しない。**
  タップ可能性のチェックとその回復は、純粋な幾何学、ネイティブ API の読み取り、あるいは Document Object Model(`DOM`)への問い合わせと、決定的で回数を区切ったループにすぎない。
  この経路にモデル呼び出しは入らない。
  最終的に失敗するステップも、他のセレクタや操作の失敗と同じ形の、通常の `ElementNotTappable` によるステップ失敗の経路をたどる。

- **決定性が第一。**
  `is_tappable` は決してアクチュエートせず、固定 `sleep` も持たないため、副作用なく何度でも呼び出せる。
  ただし、単なる単発の読み取りとは言い切れない。adb では、境界のあるキャッチアップ読み取りと安定性ポーリング(`_SETTLE_DEADLINE_S = 8.0` 秒まで)による settle と、対象が一時的に不在のときの境界のある再解決(`_RESOLVE_TIMEOUT_S`)を経る。どちらも意図的な設計であり、`scroll_until_tappable` の停止述語がループのたびにこれを呼ぶ以上、adb 上の回数を区切った回復は、各スクロールステップがすでに支払っている settle に加えて、実際の(境界はあるが無視できない)時間を費やしうる。
  回復ループそのものは、`scroll_to_target` がすでに持つ、固定 `sleep` のない決定的で回数を区切ったステップの仕組みを、停止述語だけを差し替えて再利用する。
  末尾検出、read-lag の予算、行き過ぎの扱いも同じもの。
  セレクタの曖昧性の扱いは変わらない。
  `resolve_unique` は、タップ可能性の問いが立つ前に、2 件以上の一致で相変わらず即座に失敗する。
  その場合は回復経路へ入らない。

- **アプリに依存しない。**
  このチェックはプラットフォームごとであり、アプリごとではない。
  ドライバ層がもともと持つべき種類の違い。
  「タップ可能でなければ、回数を区切ってスクロールし、再確認する。それでも無理なら名前付きのエラーで失敗する」という汎用のポリシーは、オーケストレータ層に一度だけ存在し、全バックエンドで共有される。
  そこにアプリごとの分岐はない。

## 検討した代替案

- **暗黙のタップ時チェックの代わりに、新しい明示的なシナリオアクション(たとえば `unobstruct`)を用意する案。**

  `scroll` の明示アクションという形との対称性のために検討。
  主案としては却下。
  遮蔽は、作者が一般に前もって知りうるものではない。
  今回のランでたまたま出ているトースト、特定のスクロール位置にあるスティッキーヘッダーがその例。
  作者が代わりに手を伸ばせる明示的な動詞がそもそもない。
  ほとんど遮蔽が起きない大多数のタップに、防御的なステップを毎回添えるよう求めるのは、単なる雑音になる。
  正しさのチェックと、回数を区切った狭い安全網は、作者が予期できない条件に合う。
  明示的な `scroll` は、作者がすでに知っている条件に合う形のまま。
  両者は競合ではなく補完の関係にある。

- **`Element` に、全バックエンド共通の `z_index` フィールドを足す案。**

  遮蔽を、3 つのバックエンド別々のチェックではなく、1 つの共有された幾何学的チェックにできるため検討。
  特に web について却下。
  Cascading Style Sheets(`CSS`)の `z-index` と `position` が絡むと、Document Object Model(`DOM`)の順序は描画順の代理指標として当てにならない。
  web バックエンド向けに素朴に導いたフィールドは、しばしば誤る。
  しかも、いかにも権威ある値のように見えてしまう分、フィールドがない場合より悪い。
  web にはすでに、より厳密な仕組み `document.elementFromPoint` があり、iOS にもすでに `isHittable` がある。
  どちらも、「この点に実在するのは何か」という問いへのネイティブな回答であり、代理指標ではない。
  文書順という幾何学的な代理指標から本当に利益を得るのは Android だけであり、その Android でさえ、スパイクはこの代理指標が Compose の `zIndex` では成り立ち、View の `elevation` では成り立たないことを示した。
  共有フィールドを 1 つ用意しても、本提案がそのまま述べているのと同じツールキットごとの注意書きが結局必要になる。

- **最初から完全に厳密な Android の仕組みを用意する案(アプリの実際のビュー階層へリフレクションし、本当の Z 値を読む)。**

  UiAutomator のアクセシビリティ API は、ノードごとの Z 値を一切公開していない。
  そのため、プロセス内リフレクションか、新しい統一的なアプリ側のテスト支援フックが必要になり、本提案の最初のスライスよりも大幅に大きな範囲になる。
  将来の別の提案に残す。

- **クロスウィンドウの遮蔽(ダイアログ、トースト、画面全体を覆うシステムオーバーレイ)向けに、`AccessibilityWindowInfo.getLayer()` を v1 へ組み込む案。**

  スパイクはこの API 自体が機能することを確認したが、もっとも重要なケースについては、存在が示唆するほど有用ではないとわかった。
  もっとも一般的な実際のケースであるフルスクリーンのモーダルダイアログは、異なる層にあるベースウィンドウと共存しない。
  ベースウィンドウは `getWindows()` から完全に姿を消すため、このケースはすでに、層の比較を必要とせず、対象セレクタが解決不能になることを通じて表面化する。
  トーストは `getWindows()` に一切現れないため、この仕組みではこの隙間をどれだけ手をかけても埋められない。
  準備コスト(`FLAG_RETRIEVE_INTERACTIVE_WINDOWS` はデフォルトで設定されていない)と合わせ、本提案はウィンドウレベルの遮蔽検出を、もっとも一般的な 2 つの対象ケースをきれいに扱えない仕組みとして作るのではなく、将来の項目へ先送りする。

- **`pinch` / `rotate` にもチェックを広げ、iOS と web を Android に合わせる案。**

  Android の `pinch` / `rotate` は `tap` と同じ `_resolve_frame_and_screen` を共有しているため、遮蔽された二本指ジェスチャの対象はすでに正しく `ElementNotTappable` を送出します。
  一方、iOS の `XcuitestElementProvider.gesture` には `isHittable` によるガードがありません(`tap` にはあります)。
  web の `pinch` / `rotate` も、チェック済みの `_center_checked` ではなく、未チェックの `_center_with_element` を経由したままです。
  一部が覆われた対象への `pinch` という同じシナリオが、iOS と web では通り、Android だけ失敗します。
  本スライスではこのままとします。
  これを埋めるには、他の 2 バックエンドそれぞれの二本指ジェスチャの呼び出し経路に、新たなネイティブチェックを追加する必要があり、本提案が中心に据えるタップ系のチェックより範囲が広がってしまうためです。
  上記の Android のウィンドウレベル遮蔽の先送りと並び、既知かつ受け入れたバックエンド間の非対称として、ここに記録します。
  どちらも、本提案が最初のスライスではあえて埋めない、バックエンド間の対応漏れです。

- **回復をまったく行わず、遮蔽を検出した直後に失敗する案。**

  最小の代替案であり、本提案の他のユニットがなくても意味を持つ床。
  スクロールの安全網なしの `ElementNotTappable` だけでも、静かな誤タップや誤導的な `ElementNotFound` を、正直に名付けられた失敗に変える。
  唯一の設計として採用するのは却下。
  失敗する前に是正の行動を取るという要求を満たさないため。
  また、not-found のケースにはすでに類似の安全網が存在し、価値を認められているため(adb の `_scroll_into_view`)。
  遮蔽のケースだけこれを落とすと、「見つからない」失敗と「覆われている」失敗のあいだに不要な非対称が残る。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の `MECE` な
> 作業分解(作業の単位ごとに 1 つ)に対応し、ログには変更内容と時期(古い順)を PR へのリンクと
> ともに記録します。

- [x] Unit 0 — `gestures.py` / `scroll.py` の import 循環を断つための `_SWIPE_FRACTION` / `_scroll_gesture` の抽出
- [x] Unit 1 — `ElementNotTappable` エラーと `loop.py` の catch タプルへの配線
- [x] Unit 2 — `Driver.is_tappable`、差し替え可能な `scroll_to_target` 停止条件、`scroll_until_tappable`
- [x] Unit 3 — iOS の `isHittable` ヒットテスト
- [x] Unit 4 — web の `elementFromPoint` ヒットテスト
- [x] Unit 5 — Android の `topmost_at_point` 幾何学的ヒットテスト
- [x] Unit 6 — オーケストレータの `_tap_with_recovery` 配線
- [x] Unit 7 — `FakeDriver.is_tappable`
- [x] Unit 8 — ドライバ適合スイートの遮蔽ケース
- [x] Unit 9 — ドキュメント(`drivers.md`、`selectors.md`、それぞれの `ja` 対訳)
- [x] Unit 10 — テスト

### ログ

- 本提案の設計を確定させる前に、実機での経験的なスパイクを実施しました。ドキュメントや調査だけを信じるのではなく、設計の核となる前提を確認または反証するため、iOS シミュレータと Android エミュレータ上に使い捨ての最小限の画面を作りました。iOS では、XCUITest のテストを 5 回実行し、対象が固定オーバーレイの下にある間は `isHittable` が `false` を返し、スクロールがそれを取り除くと `true` を返すことを確認しました。コミュニティが報告する不安定さの再現はありませんでした。Android では、Compose と View システムのどちらでも通常のケースでは文書順が視覚的な順序と一致することを確認しました。Compose の `zIndex` はアクセシビリティツリーを視覚的な結果に合わせて並べ替える(盲点なし)ことを確認し、View の `elevation` はそうならない(実在し再現可能な盲点である)ことを確認しました。また、Compose のオフセットモディファイアに伴う別の bounds 陳腐化のリスクを見つけ、`AccessibilityWindowInfo.getLayer()` は実在するものの、もっとも一般的な 2 つのクロスウィンドウのケース(モーダルダイアログとトースト)には不十分だとわかりました。これが、ウィンドウレベルの遮蔽検出を先送りする決定につながりました(「検討した代替案」を参照)。スパイクのコードはすべて元に戻し、本提案の一部としては出荷していません。
- 上記10個の作業単位はすべて、本提案を運ぶブランチ上でまとめて実装しました。`make check` は green です。詳細は上の「実装 PR」の行を参照してください。
- レビューで、`down` 方向だけの回復では上端に張り付いた遮蔽(スティッキーヘッダー)を取り除けないという指摘を受けました。`down` は画面上のコンテンツを上方向へ動かすため、ヘッダーの下に入り込んだ対象をさらにその下へ押し込んでしまうためです。`down` の回数上限を使い切ってもタップ可能にならないときにかぎり `up` を試すフォールバックを `gestures.py` に追加しました(`_TAP_RECOVERY_DIRECTIONS = ("down", "up")`)。この回帰は `FakeDriver` を使った新しいテストケース(`test_tap_recovery_falls_back_to_up_when_down_cannot_clear_a_top_anchored_cover`、`tests/orchestrator/test_tap_recovery.py`)で固定しています。

## 参考

- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — `Element`、`Driver` プロトコル、`resolve_unique`、`_contains`、`frame_center`、そして本提案が `ElementNotTappable` を隣に加える `SelectorError` / `ElementNotFound` / `AmbiguousSelector` の階層
- [`bajutsu/orchestrator/actions/handlers/scroll.py`](../../bajutsu/orchestrator/actions/handlers/scroll.py) — `scroll_to_target`。本提案が再実装ではなく一般化する、BE-0326 / BE-0329 の慣性なしのステップと末尾検出の仕組み
- [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py) — `_do_tap` / `_do_double_tap` / `_do_long_press` と、回復ラッパーが配線されるフォーカスタップの呼び出し箇所
- [`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py) — `ElementNotTappable` が加わるステップ実行の catch タプル
- `bajutsu/drivers/xcuitest.py`、`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`、`BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift` / `Router.swift` — iOS の `isHittable` 配線
- [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py) — 本提案が一般化する `elementFromPoint` の前例(`select_option`、764〜782 行目)
- [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) — `_resolve_frame_and_screen` / `_scroll_into_view`。本提案の遮蔽向け安全網が隣に並ぶ、既存の not-found 向け安全網
- [`tests/driver_conformance.py`](../../tests/driver_conformance.py) — 遮蔽のケースを追加する共有の適合契約
- [`isHittable` — Apple Developer Documentation](https://developer.apple.com/documentation/xctest/xcuielement/1500561-ishittable) — この値が画面外または遮蔽された要素に対して `false` を返すことを確認するドキュメント
- [Apple Developer Forums のスレッド 720155](https://developer.apple.com/forums/thread/720155) — `isHittable` が真偽値の代わりに例外を投げることがあるというコミュニティの報告。本提案のスパイクでは再現しなかった
- [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element-ja.md) — 本提案が再利用する慣性なしの `scroll` の仕組み、および本提案が折り合いをつける、暗黙の自動スクロールに関する前例
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) — 遮蔽のケースが加わる適合スイート
- [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md) — 本提案の遮蔽向け安全網が全バックエンドへ一般化する、adb の not-found スクロールフォールバック
