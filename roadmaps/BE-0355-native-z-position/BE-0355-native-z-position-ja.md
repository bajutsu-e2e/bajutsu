[English](BE-0355-native-z-position.md) · **日本語**

# BE-0355 — 要素の実際の Z 位置を、opt-in の app 側 SDK 経由で明示する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0355](BE-0355-native-z-position-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0355") |
| 実装 PR | [#1556](https://github.com/bajutsu-e2e/bajutsu/pull/1556)、[#1709](https://github.com/bajutsu-e2e/bajutsu/pull/1709) |
| トピック | ドライバとバックエンドのアーキテクチャ |
| 関連 | [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md)、[BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)、[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)、[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/drivers/base.py` の `Element` は、`identifier`、`label`、`traits`、`value`、`frame` の 5 つのフィールドだけを持ちます。どの要素が実際に手前にあるかを示す情報は、どれも持ちません。[BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md) は、タップ時の正しさという観点では、この欠落をすでに解消しています。iOS はプラットフォーム自身の `isHittable` を使います。Android の adb バックエンドは、文書順を描画順の代理として使うヒューリスティックである `topmost_at_point`（`bajutsu/drivers/base.py:830`）にフォールバックします。

しかしどちらも、前後関係そのものを、事後に読める証跡としては残しません。`elements.json` と opt-in の `rawTree` 取得（[docs/evidence.md](../../docs/evidence.md) 参照）は、いずれも各要素の frame を記録します。どの要素が実際にどれを覆っているかは、記録しません。

本提案は、`Element` に `nativeZ` というオプションのフィールドを追加します。対象アプリが Bajutsu 提供の小さな Software Development Kit（SDK）のフックへ opt-in すると、各要素は実際の前後位置を持ちます。この値は、常にアプリ自身の計測結果です。

- iOS は、BajutsuKit の既存の in-app フックを拡張し、アプリ自身のレイヤーツリーから計算します。
- Android は、アクセシビリティフレームワーク自身のオンデマンド追加データ機構を通じて、`View.getZ()` を読みます。

このフィールドは診断専用であり、既存のあらゆる遮蔽判定の挙動は変わりません。協力しないバックエンドやアプリでは、常に `None` のままです。`ViewportProvider`、`ReadLagProvider`、`RawSourceProvider` がすでに持つ opt-in の形と同じです。

## 動機

`ElementNotTappable` で失敗したステップを調べているときや、スクリーンショット上で 2 つの要素が重なって見える理由を探しているシナリオ作者は、bajutsu がすでに書き出している証跡から、実際の重なり順を読み取る手段を持ちません。`elements.json` と `rawTree` は、いずれも各要素の frame を記録します。どの要素が手前かを示す唯一の情報は、配列自身の文書順です。これはタップ時に `topmost_at_point` が使うのと同じ代理指標であり、その関数自身の docstring が「ヒューリスティックであり本物の z-index ではない」と明言している値でもあります。

本当の重なり順を知るには、bajutsu の外に出るしかありません。プラットフォームのデバッガをつなぐか、アプリ自身のソースを読むかのいずれかです。取得済みの証跡だけでは、調査者をどちらへも導けません。

BE-0349 は、actuation（実際の操作）にとってのこの欠落を、正しさの観点ではすでに解消しています。iOS のネイティブな `isHittable` と Android の `topmost_at_point` は、いずれもタップする際に十分な精度で「解決した要素は到達可能か」を判定します。同項目自身の「検討した代替案」は、この問題のより難しい半分を、将来の提案に明示的に委ねています。実ビュー階層に踏み込んで実際の Z 値を読む、本格的な Android の同一ウィンドウ機構です。同項目は、これを「タップ可否チェックより明らかに大きいスコープ」と述べています。本提案は、その委ねられた機構であり、対称性のため iOS にも拡張したものです。

本提案はまた、BE-0349 が別の理由で却下したもう 1 つの代替案も再検討します。全バックエンド共通で `Element` に `z_index` フィールドを追加する案です。BE-0349 がこれを却下したのは、文書順から導出した値が、実際の描画順と文書順が食い違う箇所で誤っているにもかかわらず、権威ある値のように見えてしまうからです。実証されたケースは、Android の `View.elevation` です。

`nativeZ` は、その構造自体によってこの問題を避けます。すでにあるツリーから導出することは一切なく、アプリ自身が計測し、明示的な協力を通じて報告した値だけが入ります。協力しないバックエンドやアプリは、`None` を返します。これは誤った推測ではなく、正直な「値なし」です。

本提案は、`nativeZ` を診断用途に限定し、実行時の判断は一切変えません。`is_tappable`、`topmost_at_point`、iOS の `isHittable` ガードは、いずれも現在の挙動のままです。シナリオのアサーションやセレクタも、新フィールドを読みません。BE-0349 がすでに出荷している遮蔽判定のヒューリスティックを、この提案で置き換えるところまで広げず、この境界に留める理由は 2 つあります。

1. Jetpack Compose のアクセシビリティノード生成が、Android 側の機構（後述）が依存する追加データを実際に転送するかどうかが、まだ確認できていません。未検証の機構に、すでに動いている正しさのチェックを賭けるのは早計です。
2. 2 つの問題を分けておくことで、将来の提案が `topmost_at_point` の判定に `nativeZ` を組み込むかどうかにかかわらず、本提案を独立して出荷できます。

## 詳細設計

### `nativeZ` フィールド

`Element`(`bajutsu/drivers/base.py:139`)に新しいフィールドを 1 つ追加します。

```python
class Element(TypedDict):
    identifier: str | None
    label: str | None
    traits: list[str]
    value: str | None
    frame: Frame
    nativeZ: float | None
```

`nativeZ` には、以下で述べる app 側フックが、該当するビューについて値を報告した場合に限り、要素自身の実際の前後位置が入ります。`elements` 自身の文書順から導出することは、一切ありません。それ以外の場合は、`None` のままです。

- 該当フックを持たないバックエンド（Playwright）
- opt-in していないアプリ
- Android で、Unit 0 のスパイクが、Compose のアクセシビリティノード生成を経由してこの値を運ぶ方法を見つけられなかった場合

`resolve_unique`（`bajutsu/drivers/base.py:647`）や既存のセレクタ照合は、すべて影響を受けません。`nativeZ` は、すでに `frame` が入っているのと同じ形でレコードに加わり、読まれるだけで絞り込みには使われません。

この数値が何を意味するかは、プラットフォームごとに異なります。Unit 0 が、Unit 2 と Unit 3 で符号化する前に決めます。

- iOS のレスポンダは、レイヤーの走査から得た前後の**序数**を報告します。
- Android は、`View.getZ()` をデバイスピクセルで報告します。`getZ()` は、木全体ではなく、同じ親を持つ兄弟同士の順序だけを決めます。`getZ() == 8` の親の下にある `getZ() == 0` の子は、その親の兄弟である `getZ() == 4` の要素より、依然として手前に描かれます。

したがって、2 つの要素の `nativeZ` は、バックエンドをまたいで比較できず、Android では親をまたいでも比較できません。

**Unit 0 はこれを、バックエンド固有の単位を明示する形で決着させました。共通の約束は 1 つだけで、
どのバックエンドでも `nativeZ` が大きいほど手前です。** もう一方の案、つまり両者を 1 つの序数へ
正規化する案は却下しました。Android の `getZ()` を木全体の序数へ正規化するには、アプリの計測値と
bajutsu がすでに持つ文書順を組み合わせるしかなく、それこそ本提案の動機が却下した `z_index`
代替案を批判している導出そのものだからです。加えて、Android の値が順序を超えて運んでいる情報、
つまりどれだけ手前かという量も捨ててしまいます。Unit 6 がこの選択を
[`docs/evidence.md`](../../docs/evidence.md)に記録し、読み手が `nativeZ` から見かけ上権威ある
誤った結論を引き出さないようにします。

### iOS: BajutsuKit の in-app フックへ新たに同期的な経路を通す

BajutsuKit はすでに、実行時に何かを計算してホスト側に報告する、opt-in の in-app フックを 1 つ持っています。`BajutsuScreen`（`BajutsuKit/Sources/BajutsuKit/BajutsuScreen.swift`、[BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)）です。`UIViewController.viewDidAppear` を swizzle し、完了した各遷移を collector へ `POST` します。collector は、Python 側が Simulator の共有ループバック上に立てます。

このチャネルは一方向でイベント駆動です。アプリ側は、遷移が起きたときに送り出します。画面遷移の通知には向いていますが、本提案が必要とする用途には向きません。ドライバが、「今まさにこの要素の実際の位置は何か」を、同期的に尋ねる必要があるからです。しかも、ドライバが今まさに発行しているクエリと、同じタイミングで尋ねます。`BajutsuNet` の collector は、受け取るだけで答えを返しません。

この隙間を埋めるには、BajutsuKit に新しい能力が要ります。小さな in-app Hypertext Transfer Protocol（`HTTP`）レスポンダです。`BajutsuNet` がすでに使っている `BAJUTSU_COLLECTOR` と同様の launch 環境変数で、opt-in します。ドライバは、自身の `/elements` クエリと並べてこれを呼びます。レスポンダは、古い push を再利用せず、要求ごとに答えを新しく計算します。

listener であるこのレスポンダには、送り出すだけの collector の形では要らなかった保護が必要です。ループバックだけに bind します。`BajutsuNet` がすでに持つ、同じ per-run 共有シークレットを、毎回の要求で必須にします（`BAJUTSU_COLLECTOR_TOKEN`、`BajutsuKit/Sources/BajutsuKit/BajutsuNet.swift:16`）。iOS のループバックはアプリ間で分離されておらず、このレスポンダはアプリのビュー階層全体を返すからです。

この制約は、後述する Unit 0 のポート形式の判断への入力であり、後付けではありません。固定の既知のポートは、実機上の他のアプリから探知できます。調整済みのポートは、探知できません。

このレスポンダが計算するのは、単純な `CALayer.zPosition` ではありません。Apple の [`zPosition` 公式ドキュメント](https://developer.apple.com/documentation/quartzcore/calayer/1410884-zposition)は、このプロパティを「兄弟レイヤーの順序を指定するために使うべきではない」と明記しています。3D 変換のない通常の場合は、sublayer 配列自身の順序が、兄弟の順序を決めます。

`zPosition` の値をそのまま渡すと、あたかも権威ある値のように見えます。しかし、平坦なレイアウトでは全要素が 0 になりがちです。これは、本提案の動機が BE-0349 の却下した `z_index` 代替案を批判した、まさにその失敗の形です。

レスポンダは代わりに、実際のレイヤーツリーを辿ります。各 `CALayer` の `sublayers` の順序に、0 でない `zPosition` だけを反映させます。要素ごとの確定した前後インデックスを報告し、キーは、`xcuitest.py` の `_query_with_handles`（`bajutsu/drivers/xcuitest.py:533`）がすでに解決しているのと同じ識別子です。

この計算で `UIView` と `CALayer` のどちらを辿る必要があるか、あるいは両方を辿る必要があるかは、Unit 0 が実機で確認しました。UIKit ほど直接的にレイアウトツリーを外部に出さない SwiftUI のビュー階層についても、実際に画面へ合成される結果と一致するかを含めて確認しました。BE-0349 が、ドキュメントだけを信じずに `isHittable` の挙動を実機で確認したのと、同じやり方です。

**Unit 0 の結果**（起動済みの Simulator 上で、showcase の UIKit アプリと SwiftUI アプリに対して計測）。

- **レスポンダは、結局のところ新しい能力ではありませんでした。** `BajutsuWebView`（[`BajutsuKit/Sources/BajutsuKit/BajutsuWebView.swift`](../../BajutsuKit/Sources/BajutsuKit/BajutsuWebView.swift)、BE-0037）が、ホストの割り当てたエフェメラルポートを使うループバックのソケットサーバを、テスト対象のアプリの中ですでに動かしています。今回のレスポンダは、新たに考案するのではなくこの形を踏襲します。既存のブリッジが持っていないトークン検査を、加えました。
- **ポートは調整済みで、シークレットは専用に持ちます。** `BAJUTSU_ZORDER_PORT` と `BAJUTSU_ZORDER_TOKEN` は、WebView ブリッジ自身のポートと並べて、lease ごとに割り当てます。並列するデバイス同士が競合せず、固定の既知のポートを探知される余地もありません。シークレットは、本提案が当初想定した `BAJUTSU_COLLECTOR_TOKEN` の再利用ではなく、このレスポンダ専用に生成します。その token は、シナリオが network collector を動かすときにしか存在しません。認証のないリクエストを必ず拒否しなければならないレスポンダが、しばしば存在しないシークレットに依存するわけにはいかないからです。
- **UIKit では `UIView` のツリー走査で値が取れます。** 各ビューの前後の序数は、`subviews` の配列順から求めます。アプリが `zPosition` を設定した箇所だけが、その順序を上書きします。キーは `accessibilityIdentifier` です。
- **SwiftUI では何も報告できず、私有 API を使わない経路では変えられません。** SwiftUI の showcase のビューツリーを全走査しても、`accessibilityIdentifier` はどこにもなく、外部に出されたアクセシビリティ要素もありませんでした。葉は `CGDrawingView` と `_UIShapeHitTestingView` です。SwiftUI がアクセシビリティ要素を実体化するのは、支援技術がプロセスへ接続したときだけです。自分自身を覗いているアプリは、これにあたりません。ナビゲーションバーとタブバーの項目も、どちらのツールキットでも対象外です。ビューではなく、`UIBarButtonItem` と `UITabBarItem` だからです。したがって Unit 2 は UIKit のみの最初の一段とし、Unit 3 の `View` のみの一段と対称になります。
- **コスト。** showcase 相当の大きさのツリーに対する `/zorder` の 1 往復は、2.0〜2.4 ミリ秒でした。レスポンダを持たないアプリへの接続拒否は、0.16 ミリ秒でした。

### Android: アクセシビリティフレームワーク自身のオンデマンド追加データ機構

Android のアクセシビリティフレームワークは、`AccessibilityNodeInfo` を公開の Application Programming Interface（`API`）として提供しています。Android には、まさにこの種の問題向けの、公式かつ opt-in な機構がすでにあります。この側には、新しいチャネルは要りません。

`View` は、[`addExtraDataToAccessibilityNodeInfo(AccessibilityNodeInfo, String, Bundle)`](https://developer.android.com/reference/android/view/View) をオーバーライドします。これで、アクセシビリティクライアントが既定では要求しないデータを付加できます。同じオーバーライドの中で、どの追加データキーに対応しているかを、`AccessibilityNodeInfo.setAvailableExtraData(List<String>)` を通じて宣言します。

クライアント側は、ここでは常駐サーバー自身のオンデバイスリクエストハンドラです（`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt` の `respondSource`、[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)）。このハンドラは、すでに `AccessibilityService` としてデバイスと通信しています。

`respondSource` は、ノードごとに [`AccessibilityNodeInfo.refreshWithExtraData(String key, Bundle args)`](https://developer.android.com/reference/android/view/accessibility/AccessibilityNodeInfo) を呼び、結果を `getExtras()` から読みます。結果は、今日すでに組み立てている Extensible Markup Language（`XML`）ダンプと組み合わせます。

この組み合わせは無償ではありません。`respondSource` の本体は、`settledDump` から `UiDevice.dumpWindowHierarchy` へと続く経路で作られます。この呼び出しは、1 回のプラットフォーム呼び出しの中で、走査と直列化を同時に行います。`refreshWithExtraData` を呼べる個々の `AccessibilityNodeInfo` は、外部に出しません。

そのため追加データには、`UiAutomation.getRootInActiveWindow()` を使うもう 1 回の走査が必要になります。この走査は、アクティブウィンドウしか対象にしません。`dumpWindowHierarchy` は、SystemUI のステータスバーを含む全ウィンドウに及びます。この 2 つの走査をどう突き合わせるか、そしてもう一方の走査のコストは、Unit 0 が確定します。

これは、プラットフォーム自身の `EXTRA_DATA_TEXT_CHARACTER_LOCATION_KEY` が、オンデマンドのテキスト境界のために使っているのと同じ機構です。本提案の Android 側 SDK は、小さなヘルパーで済みます。アプリは、`View` のサブクラスまたは `ViewCompat` 拡張から、このヘルパーを呼びます。ヘルパーは `view.getZ()` を、Bajutsu 専用の追加データキーの下に報告します。

`getZ()` は、elevation と Z 軸方向の移動を合わせた値です。iOS の `zPosition` とは違い、Android 自身の実際の描画順を並べ替える値です。このことは、BE-0349 自身のスパイクが、`View.elevation` についてすでに実証しています。

`dumpWindowHierarchy` の `XML` 形式には、プラットフォーム自身が定義していない値を入れる属性の枠がありません。そのため `respondSource` は、`nativeZ` を新しい `XML` 属性として直接追加できません。代わりに、各ノードの値を小さな側方構造として返します。この構造は、`XML` 本体の `<node>` の並びとインデックスを揃えます。文書順の位置で紐づけるのであり、ノードの識別情報では紐づけません。

識別情報だけでは、これを紐づけられません。`adb.py` 自身の `_identity()`（`bajutsu/drivers/adb.py:307`）が作る、`resource-id`、`content-desc`、`text`、`class` の 4 項目の組は、意図的に一意ではないからです。常駐チャネルはこの組を、`index`（`count` のうちの何番目か、`bajutsu/drivers/adb.py:104`–`106`）と組み合わせて使います。同じ内容の行を並べたリストは、単一の識別情報に集約されてしまうからです。識別情報だけで紐づける実装は、そのような行すべてに、同じ `nativeZ` を黙って割り当ててしまいます。

`bajutsu/adb_resident.py` の階層取得（`fetch_source`、`bajutsu/adb_resident.py:90`）は、今日と同じ単一の `GET /source` 往復のままです。この側方構造も、一緒に運びます。`_elements_from_nodes`（`bajutsu/drivers/adb.py:322`）は、`parse_hierarchy_with_identities`（`bajutsu/drivers/adb.py:336`）がすでに要素 *i* を対応づけているのと、同じ `<node>` の並びを辿ります。位置 *i* の値を、要素 *i* の `nativeZ` へ運びます。

この提案で新たに加わるコストは、`respondSource` の内部にあります。アクティブウィンドウに対する、突き合わせのためのもう 1 回の走査は、アプリが opt-in したかどうかにかかわらず、毎回の読み取りで実行されます。その走査の中のノードごとの `refreshWithExtraData` 呼び出しだけが、ゲートされます。そのノード自身の `getAvailableExtraDataKeys()` が、すでに Bajutsu のキーを列挙している場合に限って発行されます。走査そのもののコストの計測値は、後述する Unit 0 のコストの結果に記録しました。出荷したスライスは、この走査をゲートしていません。

**Unit 0 の結果**（API 34 のエミュレータ上で、showcase の Compose アプリと Views アプリに対して計測）。

- **Compose は、アプリが宣言した追加データキーを転送しません。** Bajutsu のキーと同じ名前で独自の `SemanticsPropertyKey` を宣言した、`Modifier.semantics` のノードを 2 つ置いて計測しました。公開されたのは、Compose 自身の固定のキー（`androidx.compose.ui.semantics.id`、`…testTag`）と、プラットフォーム自身のキーだけです。Bajutsu のキーに応答したノードは、ありませんでした。したがって Unit 3 は、この節がすでに代替として挙げていた、`View` のみの最初の一段になります。本項目ではなく将来の項目に向けた、関連する観測が 2 つあります。1 つは、常駐チャネルの `XML` が `drawing-order` 属性を持つ（`uiautomator dump` のフォールバックは持たない）一方、Compose のノードは実体が `View` ではないためすべて `0` になることです。もう 1 つは、Compose 自身のアクセシビリティツリーが、`Modifier.zIndex` の兄弟をすでに描画順で並べていることです。`View.elevation` のときのように、文書順が誤りにはなりません。
- **この節が挙げた API 名は存在しません。** クライアント側の getter は `AccessibilityNodeInfo.getAvailableExtraData()` であり、`getAvailableExtraDataKeys()` ではありません。この節が両方に付くと想定した `…ExtraData` の名前を持つのは、setter だけです。
- **プラットフォームは、オンデマンドのコールバックをアクセシビリティデリゲートに回しません。** デリゲートの `onInitializeAccessibilityNodeInfo` は呼ばれるので、そこからの `setAvailableExtraData` は効きます。しかし `addExtraDataToAccessibilityNodeInfo` は呼ばれないため、`refreshWithExtraData` は true を返しながら、何も届けませんでした。そこでアプリ側のヘルパーは、キーを宣言すると同時に、ノードの構築中に `AccessibilityNodeInfo.getExtras()` へ値を書き込みます。この経路は、実際に届きます。あわせてオンデマンドのオーバーライドも残すので、この節が当初示した形で書かれた `View` のサブクラスも応答します。`respondSource` は、すでにある値を先に読み、無いときだけ問い合わせます。一般的な場合は、ノードごとの往復を一切払いません。
- **文書順で紐づけた側方構造は、28 ノードずれます。** ありふれた showcase の画面で、`dumpWindowHierarchy` は全ウィンドウにわたる 72 ノードを返しました。`getRootInActiveWindow()` の走査は 45 ノードでした。同じ要素が、本体では文書順 35 番目、走査では 7 番目にありました。そのため各値は、ホストが読んでいる `<node>` から再計算できるものでキーにします。bounds、class、package と、その 3 つが一致するノードのうち何番目か、です。両側とも同じアクセシビリティツリーを深さ優先で辿るため、この出現順は一致します。ただし対象は、アクティブウィンドウに限られます。ホスト側の `narrow_to_active_window` は、SystemUI 自身のウィンドウを取り除きます。しかし、テスト対象のアプリ自身が持つ 2 つ目のウィンドウ（自分のメインウィンドウの上に乗ったダイアログなど）は、取り除きません。アプリ自身の複数ウィンドウにまたがって、bounds、class、package を共有する、opt-in 済みの 2 つのノードでは、互いの出現数がずれます。`_native_z_key` の docstring に既知の限界として記録し、このスライスでは解消しません。
- **キーの bounds 側は、生の値ではなく画面でクリップした値でなければなりません。** `dumpWindowHierarchy` 自身の `bounds` 属性は、ノードの矩形を画面と交差させた値です。この値は、`AccessibilityNodeInfoDumper` が `AccessibilityNodeInfoHelper.getVisibleBoundsInScreen` を経由して得ています。生の `getBoundsInScreen()` の矩形をキーへ使う走査は、画面の端からはみ出たノードで、ホスト側が再計算したキーと食い違っていました。アプリが実際に測定し報告した値なのに、正直な欠損として報告されてしまいます。しかも起きるのは、調査者が証跡を開くまさにそのクリップされた、一部隠れたレイアウトです。走査は、各矩形をデバイス自身の画面境界と交差させてからキーにします。これでダンプ側と一致します。この修正が再現するのは、画面端によるクリッピングの半分だけです。祖先自身の境界によるクリッピング（画面端まで届かない小さなスクロール可能コンテナの中にあるノードなど）は、再現しません。上記のマルチウィンドウの限界と並ぶ、より狭い範囲の課題です。
- **コスト。** 45 ノードすべてに対する `refresh()` の走査は、24 ミリ秒でした。ウォームな `dumpWindowHierarchy` は、19 ミリ秒でした。上記の先読みの経路は、ノードごとの往復は完全に避けますが、走査そのものは避けません。走査は、アプリが opt-in したかどうかにかかわらず、毎回の読み取りで実行されます（後述の「コストは両 OS とも opt-in のまま」を参照）。

### コストは両 OS とも opt-in のまま

いずれの OS でも、協力しないアプリだけの run に発生する追加コストは、境界のある 1 回の探索呼び出しを超えません。

iOS 側では、レスポンダを組み込んでいないアプリに対して、ドライバの接続は拒否されます。あるいは、ソケットが開いたまま一度も応答しない場合は、Unit 0 が定める短い connect/read タイムアウトで終わります。いずれの場合も、`nativeZ` は `None` のままです。`RawSourceProvider` を実装していないバックエンドが、今すでに受けている degrade と同じです。

iOS には、Android のゲート（すでに手元にあるノード属性）に相当するものがありません。接続を試みる以外に、アプリが計装されていないと知る手段がないからです。そのためドライバは、最初の失敗をセッション単位でキャッシュして、以降の探索を止めます。協力しないアプリは、このタイムアウトを一度だけ払い、`/elements` クエリごとには払いません。これが、後述する「保たれる大原則」で述べる「境界があり同期的」という意味を保ちます。

Android のノードごとの `refreshWithExtraData` 往復は、そのノード自身の `getAvailableExtraData()` が、すでに Bajutsu のキーを列挙している場合に限って発行されます。そのためこの呼び出し**単体**は、計装されていないアプリのツリー走査でも、1 回の安価な存在確認だけで済みます。

協力するアプリについては、Unit 0 のスパイクで、showcase 相当の大きさのツリーに対する要素ごとの往復コストも測定します。この基準は、[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md) 自身の常駐チャネルの動機、つまり 1 回の読み取りごとのコストと同じです。この結果は、Unit 2 と Unit 3 が着手する前に、この節へ記録します。

**実装が完了した時点で、この節の見出しは Android 側の実態を言い過ぎています。** 突き合わせのための走査そのものは、ヘルパーを一度もリンクしていないアプリでも、`GET /source` のたびに走ります。`respondSource` には、木を実際に歩いてみるまで、opt-in したノードが 1 つもないと知る手立てがないからです。

Unit 0 自身のコスト計測（前述）は、この走査を、常駐チャネルのウォームな `dumpWindowHierarchy` と同程度の重さだと示しています。そのため `nativeZ` を使わない Android の実行でも、読み取りごとのコストはおよそ倍になります。

ここでは解消せず、既知の課題として残します。この節が本来約束していた opt-in のコストを取り戻す道は、この走査自体をゲートすることです。ゲートする信号は、`nativeZ` に opt-in したターゲットに対してのみ、ホストが送ります（`since=` をすでに読み取りごとに通しているのと同じ形）。ただしその配線は、後続の Unit が設計して仕上げるべきものであり、このレビューの場で即興に作るべきではありません。

### 作業分解(相互排他的かつ全体を尽くす、Mutually Exclusive, Collectively Exhaustive、`MECE`)

0. **スパイク。** `nativeZ` がバックエンドをまたいで何を意味するかを決めます。正規化した前後の序数か、明示したバックエンド固有の単位かです。上記の詳細設計「`nativeZ` フィールド」に従います。次の項目も、このスパイクの範囲です。
   - iOS では、UIKit と SwiftUI の両画面について、確定した前後インデックスを報告するために必要な、レイヤーとビューの走査方法を実機で確認します。
   - iOS では、レスポンダの connect/read タイムアウトと、協力しないアプリの 1 回の失敗探索が頼る、セッション単位のネガティブキャッシュ設計を確定します。
   - iOS では、セキュリティ（固定のローカルポートは実機上の他のアプリから探知可能）を、既存の `BAJUTSU_COLLECTOR` と同様の launch 環境変数と比較しながら、レスポンダの形を設計します。
   - Android では、`Modifier.zIndex` と `graphicsLayer` を使った小さな Compose の画面で、Jetpack Compose のアクセシビリティノード生成が、`Modifier.semantics` 経由で宣言した独自の追加データキーを転送するかを確認します。
   - Android では、`refreshWithExtraData` の呼び出しに必要な `UiAutomation.getRootInActiveWindow()` のノード集合が、`dumpWindowHierarchy` の広い、複数ウィンドウにわたるノード集合と、どう突き合わさるかを確認します。上記の詳細設計にある、文書順で紐づけた側方構造が、実際に付随する `XML` 本体と噛み合うことを、前提のままにしないためです。
   - 両 OS で、showcase 相当の大きさのツリーに対する、要素ごとの往復コストを測定します。

   すべての結果を、Unit 2 と Unit 3 が着手する前に、上記の詳細設計へ記録します（動く、回避策が必要、対応不可のいずれか）。BE-0349 自身のログの記録の仕方と同じです。Unit 0 は、Unit 2 と Unit 3 をブロックします。
1. **`nativeZ` フィールド。** `Element`（`bajutsu/drivers/base.py`）に追加します。`Element` は `total=False` を持ちません。そのため、既存のすべての dict リテラル構築箇所で、`nativeZ` も設定しなければなりません。対象は、adb、XCUITest、実機 XCUITest クライアント、web の Document Object Model（`DOM`）パーサー、`record_capture.py` のキャプチャ拒否時プレースホルダーを含みます。さもないと `mypy --strict`（`make check` の一部）がビルドを止めます。この検査自体が見落としを防ぐ安全網なので、この Unit で箇所を手作業で列挙する必要はありません。まだ何もこれに実際の値を計算しないため、挙動は変わりません。
2. **iOS 側の報告。** 新しい BajutsuKit の in-app レスポンダと、`xcuitest.py` によるその読み取りを `nativeZ` へつなぎます。Unit 0 の結果でスコープが決まります。
3. **Android 側の報告。** app 側の追加データヘルパー。`ResidentServerTest.kt` の `respondSource` が行う、opt-in なノードごとの `refreshWithExtraData` 呼び出しと、それが `XML` 本体と並べて返す側方構造。そして `adb.py` による、その構造から `nativeZ` への読み取りです。Unit 0 の結果でスコープが決まり、Compose がキーを転送しないとわかった場合は、`View` 系のみの最初の一段とします。
4. **`FakeDriver` 対応。** フィクスチャの要素ごとに設定可能な `nativeZ`（`bajutsu/drivers/fake.py`）を追加し、実機なしで `nativeZ` を読むシナリオレベルのテストを書けるようにします。
5. **driver conformance suite。** [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) の suite（`tests/driver_conformance.py`）に、ケースを追加します。フィクスチャまたはバックエンドが明示的に報告した場合に限り `nativeZ` が入り、それ以外は `None` のままであることを確認します。
6. **ドキュメント。** [`docs/evidence.md`](../../docs/evidence.md) と [`docs/architecture.md`](../../docs/architecture.md)（その `docs/ja/` 側も含む）に、`nativeZ` が診断専用であり、`is_tappable`、`topmost_at_point`、`isHittable` のいずれも変えないことを明記します。`nativeZ` がバックエンドをまたいで何を意味するかについての、Unit 0 の決定も記録します。
7. **テスト。** 新しい報告経路について、各バックエンドのユニットテストを書きます。本提案によって `is_tappable` と `topmost_at_point` の挙動が変わっていないことを固定する、リグレッションテストも加えます。

### 保たれる大原則

- **AI は判断しません。** `nativeZ` は、app 側 SDK の呼び出しかアクセシビリティ API を通じて計測される値です。これを生成または読み取る経路に、モデル呼び出しは一切入りません。
- **決定性を優先します。** `nativeZ` の読み取りは、両 OS とも境界のある同期呼び出しで、ポーリングも固定の `sleep` も持ちません。今の `frame` の読み取りと同じ形です。
- **app 非依存です。** SDK フックは、両 OS とも統一された opt-in で、BajutsuKit の既存の `viewDidAppear` swizzle と同じ形です。ツール本体とドライバは、それぞれ 1 つのコードパスを追加するだけで、どこにも app 別の分岐はありません。フックをリンクしないアプリは影響を受けず、`None` を報告します。

## 検討した代替案

- **`nativeZ` を今すぐ `topmost_at_point` / `is_tappable` に組み込み、診断専用のままにしないこと。** 実際の値があれば、Android の遮蔽判定が `View.elevation` を誤判定しなくなるため、検討しました。本提案の最初の一段では却下します。Compose の追加データ対応がまだ確認できておらず、未検証の機構に、すでに動いている正しさのチェックを賭けるのは早計だからです。Unit 0 でこの機構が動くと確認できた後の、自然な後続作業として残します。
- **`rawTree` と並ぶ新しい opt-in の capture キーとし、`Element` の常時フィールドにしないこと。** `rawTree` の opt-in の形との対称性から、検討しました。却下します。`rawTree` はパース前の生の応答のスナップショットであり、意図的に `Element` 自体からは外されています（[docs/evidence.md](../../docs/evidence.md) 参照）。一方 `nativeZ` は、`frame` や `traits` と同種の、要素ごとの属性です。読み手が `elements.json` と識別子で突き合わせなければならない第 2 の成果物に持たせるより、`Element` 自身に持たせるべきです。これは、`rawTree` のために `capture()` 自身の kind 順序付けルール（`bajutsu/evidence/core.py:218`）がすでに避けている、まさにその整合性の問題です。
- **文書順とツールキットごとのヒューリスティックから一律に導出する、計測しない z フィールド。** これは、BE-0349 が却下した `z_index` 代替案そのものの言い換えです。ここでも成り立たない理由は、動機の節に述べたとおりです。
- **画面全体を覆うシステムダイアログやトーストといった、ウィンドウ間の遮蔽。** BE-0349 自身の見送りのまま対象外とします。`AccessibilityWindowInfo.getLayer()` は、そこでもっとも多い 2 つのケースには不十分だとわかっており、本提案はその問いを再び開きません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の `MECE` な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] Unit 0 — スパイク: iOS のレイヤー走査の実現性とレスポンダの形、Android の Compose の
      追加データ対応、両 OS での要素ごとの往復コスト
- [x] Unit 1 — `Element` への `nativeZ` フィールド
- [x] Unit 2 — iOS 側の報告(BajutsuKit のレスポンダ、`xcuitest.py` の読み取り)
- [x] Unit 3 — Android 側の報告(追加データヘルパー、常駐サーバーの往復、`adb.py` の読み取り)
- [x] Unit 4 — `FakeDriver` 対応
- [x] Unit 5 — driver conformance suite のケース
- [x] Unit 6 — ドキュメント(`evidence.md` / `architecture.md` とその `ja` 側)
- [x] Unit 7 — テスト

### ログ

- 最初のスライスでは、Python 側の土台を入れ、実機を必要とする作業の単位はすべて後続の変更に回しました。`Element` に必須フィールドとして `nativeZ` を追加したので、`Element` を構築する 12 箇所を `mypy --strict` がすべて指摘しました。後から 1 箇所だけ取り残される余地はありません。12 箇所のうち 10 箇所は、ドライバのパーサー、`record_capture`、デモ用のスクリプトで、いずれも `None` を返します。アプリ側のフックを持たないバックエンドが、読み手に対して負うべき、正直な欠損の表現です。残る 2 箇所は、後述する証跡の読み手で、成果物が記録していた値をそのまま持ち回ります。

  `FakeDriver` は、テストが与えた `nativeZ` をそのまま返します。スクロール可能モードでフレームを平行移動する経路を通しても、値は保たれます。この経路が、実機なしの決定的なゲートで、`nativeZ` を読むコードを動かすための接点です。

  書き出した証跡を読み戻す 2 つの経路は、`base.native_z_from_json` という 1 つの変換を共有します。golden ファイルのローダーと、`serve` の pick 解決です。成果物を経由した値が、ドライバから直接読んだ値と同じ意味を持つようにするためです。あわせて golden のローダーは、このフィールドを必須の項目から外しました。今回の変更より前に記録した golden は、どれもこのフィールドを持ちません。また、golden が固定するのは同一性と状態であって、その瞬間の測定値ではないからです。

  driver conformance suite には、現時点の契約を全バックエンド共通で固定するケースを追加しました。フィールドは常に存在し、アプリが測るまでは常に `None` である、という内容です。`docs/evidence.md` とその日本語版には、このフィールドの意味と、重なりの判定がこれを読まないことを明記しました。

  Unit 6 は、`nativeZ` がバックエンド間で何を意味するかという Unit 0 の決定を待って、残しています。Unit 7 も、Unit 2 と Unit 3 が追加するバックエンドごとの報告経路を待って、残しています。今回のスライスが持つ回帰テストは `tests/test_native_z.py` にあり、正直な欠損と、`is_tappable` および `topmost_at_point` の挙動が変わっていないことの両方を固定します。
- 2 番目のスライスでは、Unit 0 のスパイクを実機で実行し、そこで範囲の定まった 2 つの報告経路を実装しました。スパイクの結果は、上の詳細設計に記録してあります。そのうち 3 つは、設計を追認するのではなく変更させました。
  - iOS のレスポンダは、本提案が想定した BajutsuKit の新しい能力ではありません。`BajutsuWebView` が、テスト対象のアプリの中でループバックのソケットサーバをすでに動かしているためです。新しいレスポンダはその形を写し取り、既存のものが持たないトークン検査を加えました。シークレットは、`BAJUTSU_COLLECTOR_TOKEN` ではなく、実行ごとに専用のものを持ちます。その token は、シナリオが network collector を動かすときにしか存在しないからです。
  - Android のフレームワークは、`addExtraDataToAccessibilityNodeInfo` をアクセシビリティデリゲートに回しません。そのためアプリ側のヘルパーは、ノードの構築中にその extras へ位置を書き込み、`View` のサブクラス向けにオンデマンドのオーバーライドも残しました。常駐サーバーは、すでにある値を読み、無いときだけ問い合わせます。
  - 側方構造は、read mark と同じくレスポンスヘッダーに載せ、`XML` 本体を `uiautomator dump` とバイト単位で同一に保ちます。キーは、bounds、class、package と出現順です。デバイスが計測する走査のノードの並びが、本体の並びと揃わないためです。

  宣言的な UI ツールキットは、2 つとも値を報告しません。どちらの OS でも理由は同じで、SwiftUI も Compose も、自身でアクセシビリティ要素を生成し、測定元となる実体を外に出さないからです。したがって出荷した対象は、UIKit と Android の `View` で、それ以外は正直な欠損のままです。

  showcase の Views アプリは、テスト用にビューへ印を付ける既存のヘルパー 1 か所から opt-in しており、これが Android 側の経路に実機での網羅を与えています。conformance の契約も、コードに合わせて動かしました。フィールドが常に欠損しているという主張ではなく、欠損しているか、実際の有限な計測値であるという主張に変えています。バックエンドは、opt-in のどちら側にも立てるようになったからです。
- 実装 PR に対するレビューで、さらに 5 件の実在する問題が見つかりました。すべて修正するか、上に記録しています。黙って先送りにはしていません。
  - `ZOrderResponder.positions()` は、接続段階のタイムアウトでも、ネガティブキャッシュをラッチしていました。`urllib` は、このタイムアウトを `URLError` で包みます。読み取り段階の節がすでに捕捉している、素の `TimeoutError` とは別扱いです。そのため、アプリがまだ起動中なだけのリースから、`nativeZ` を丸ごと失っていました。包まれた reason を調べるよう直しました。
  - `Element.nativeZ` と `xcuitest.py` の `_to_element` の脇にあった 2 つのコメントは、iOS と Android 側の経路が未出荷だと、まだ書いていました。本 PR によってその記述は偽になっていたので、実際に何を報告しているかへ書き直しました。
  - Android の常駐サーバーの bounds キーは、生の `getBoundsInScreen()` の矩形を使っていました。突き合わせる相手のダンプ本体は、`AccessibilityNodeInfoDumper` が書き出す、画面クリップ済みの矩形をキーにしていました。そのため、画面端からはみ出たノードの位置が、黙って落ちていました。デバイス自身の画面境界と交差させてからキーにするよう、直しました。
  - 上の「詳細設計」の説明文は、存在しない API 名を挙げていました。Android の走査のコストについても、「1 回の安価な存在確認以上は払わない」と書いていました。どちらも、Unit 0 自身の結果の数行下で否定されている内容です。その場で訂正し（英語、日本語とも）、レビューの圧力の下で最適化するのではなく、既知の後続課題として記録しました。

## 参考

- [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md) —
  タップ可否チェック。その「検討した代替案」が本提案の Android 側機構を将来へ委ね、本提案の
  `nativeZ` が避けている理由で `z_index` フィールドを却下している
- [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md) —
  `BajutsuScreen`。既存の in-app フックとその一方向の collector チャネルを、本提案の iOS 側機構が
  新しい同期的なレスポンダで拡張する
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md) —
  常駐 `AccessibilityService` チャネル。本提案の Android 側機構がノードごとの往復を追加する対象
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) — Unit 5
  が拡張する conformance suite
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — `Element`、`topmost_at_point`、
  `resolve_unique`
- [`BajutsuKit/Sources/BajutsuKit/BajutsuScreen.swift`](../../BajutsuKit/Sources/BajutsuKit/BajutsuScreen.swift) —
  本提案の iOS 側レスポンダが隣に置かれる既存の in-app フック
- [`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt) —
  `respondSource`。本提案のノードごとの `refreshWithExtraData` 呼び出しがその内部で走るオンデバイス
  リクエストハンドラ
- [`bajutsu/adb_resident.py`](../../bajutsu/adb_resident.py)、
  [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) — 変更のない Python 側の階層取得トラン
  スポートと、本提案の Android 側機構が拡張する `Element` の構築
- [`zPosition` — Apple Developer Documentation](https://developer.apple.com/documentation/quartzcore/calayer/1410884-zposition) —
  このプロパティを兄弟レイヤーの順序決定に使うべきではないと明記している。本提案の iOS 側
  レスポンダが直接読まず実際のレイヤーツリーを辿る理由
- [`View` — Android Developers API リファレンス](https://developer.android.com/reference/android/view/View) —
  `addExtraDataToAccessibilityNodeInfo`。本提案の Android 側ヘルパーが使うオンデマンドの追加
  データ機構
- [`AccessibilityNodeInfo` — Android Developers API リファレンス](https://developer.android.com/reference/android/view/accessibility/AccessibilityNodeInfo) —
  `refreshWithExtraData` / `setAvailableExtraData`。同じ機構のクライアント側の半分
