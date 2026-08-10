[English](BE-0355-native-z-position.md) · **日本語**

# BE-0355 — 要素の実際の Z 位置を、opt-in の app 側 SDK 経由で明示する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0355](BE-0355-native-z-position-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0355") |
| トピック | ドライバとバックエンドのアーキテクチャ |
| 関連 | [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md)、[BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)、[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)、[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/drivers/base.py` の `Element` は `identifier` / `label` / `traits` / `value` / `frame`
だけを持ち、どの要素が実際に手前にあるかを示す情報を持ちません。
[BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md)
は、タップ時の正しさという観点ではこの欠落をすでに解消しています。iOS はプラットフォーム自身の
`isHittable` を使い、Android の adb バックエンドは `topmost_at_point`
(`bajutsu/drivers/base.py:830`、文書順を描画順の代理として使うヒューリスティック)にフォールバック
します。しかしどちらも、前後関係そのものを事後に読める証跡としては残しません。`elements.json` と
opt-in の `rawTree` 取得([docs/evidence.md](../../docs/evidence.md) 参照)はいずれも各要素の
frame を記録しますが、どの要素が実際にどれを覆っているかは記録しません。本提案は `Element` に
`nativeZ` という新しいオプションフィールドを追加します。対象アプリが Bajutsu 提供の小さな
Software Development Kit(`SDK`)のフックへ opt-in すると、各要素は実際の前後位置を持ちます。この値
は常にアプリ自身の計測結果です。iOS は BajutsuKit の既存 in-app フックを拡張し、アプリ自身のレイヤーツリー
から計算します。Android はアクセシビリティフレームワーク自身のオンデマンド追加データ機構経由で
`View.getZ()` を読みます。このフィールドは診断専用であり、既存のあらゆる遮蔽判定の挙動は変わり
ません。協力しないバックエンドやアプリでは常に `None` のままで、これは `ViewportProvider` /
`ReadLagProvider` / `RawSourceProvider` がすでに持つ opt-in の形と同じです。

## 動機

`ElementNotTappable` で失敗したステップを調べているときや、スクリーンショット上で 2 つの要素が
重なって見える理由を探しているシナリオ作者は、bajutsu がすでに書き出している証跡から実際の重なり順
を読み取る手段を持ちません。`elements.json` と `rawTree` はいずれも各要素の frame を記録しますが、どの
要素が手前かを示す唯一の情報は配列自身の文書順です。これはタップ時に `topmost_at_point` が使う
のと同じ代理指標であり、その関数自身の docstring が「ヒューリスティックであり本物の z-index では
ない」と明言している値でもあります。本当の重なり順を知るには、bajutsu の外に出るしかありません。
プラットフォームのデバッガをつなぐか、アプリ自身のソースを読むかのいずれかで、取得済みの証跡だけ
では調査者をそこへ導けません。

BE-0349 は、actuation(実際の操作)にとってのこの欠落を、正しさの観点ではすでに解消しています。
iOS のネイティブ `isHittable` と Android の `topmost_at_point` は、いずれもタップする際に十分な
精度で「解決した要素は到達可能か」を判定します。同項目自身の「検討した代替案」は、この問題のより
難しい半分(実ビュー階層に踏み込んで実際の Z 値を読む、本格的な Android の同一ウィンドウ機構)を
将来の提案に明示的に委ねており、「タップ可否チェックより明らかに大きいスコープ」と述べています。
本提案はその委ねられた機構であり、対称性のため iOS にも拡張したものです。本提案はまた、BE-0349
が別の理由で却下したもう 1 つの代替案、すなわち全バックエンド共通で `Element` に `z_index`
フィールドを追加することも再検討します。BE-0349 がこれを却下したのは、文書順から導出した値が、
実際の描画順と文書順が食い違う箇所(実証されたケースは Android の `View.elevation`)で誤って
いるにもかかわらず、権威ある値のように見えてしまうからです。`nativeZ` はその構造自体によってこの
問題を避けます。すでにあるツリーから導出することは一切なく、アプリ自身が計測し明示的な協力を通じて
報告した値だけが入るため、協力しないバックエンドやアプリは `None` を返します。これは誤った推測
ではなく、正直な「値なし」です。

本提案は `nativeZ` を診断用途に限定し、実行時の判断は一切変えません。`is_tappable` /
`topmost_at_point` / iOS の `isHittable` ガードはいずれも現在の挙動のままで、シナリオのアサー
ションやセレクタも新フィールドを読みません。BE-0349 がすでに出荷している遮蔽判定のヒューリスティック
をこの提案で置き換えるところまで広げず、この境界に留める理由は 2 つあります。1 つ目は、Jetpack
Compose のアクセシビリティノード生成が、Android 側の機構(後述)が依存する追加データを実際に転送
するかどうかが、まだ確認できていないことです。未検証の機構に、すでに動いている正しさのチェックを
賭けるのは早計です。2 つ目は、2 つの問題を分けておくことで、将来の提案が `topmost_at_point` の
判定に `nativeZ` を組み込むかどうかにかかわらず、本提案を独立して出荷できることです。

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

`nativeZ` には、以下で述べる app 側フックが該当するビューについて値を報告した場合に限り、要素
自身の実際の前後位置が入ります(`elements` 自身の文書順から導出することは一切ありません)。それ
以外の場合、つまり該当フックを持たないバックエンド(Playwright)、opt-in していないアプリ、あるい
は Android で Unit 0 のスパイクが Compose のアクセシビリティノード生成経由でこの値を運ぶ方法を
見つけられなかった場合は、`None` のままです。`resolve_unique`(`bajutsu/drivers/base.py:647`)や
既存のセレクタ照合はすべて影響を受けません。`nativeZ` はすでに `frame` が入っているのと同じ形で
レコードに加わり、読まれるだけで絞り込みには使われません。この数値が何を意味するかはプラットフォーム
ごとに異なり、Unit 0 が Unit 2・3 で符号化する前に決めます。iOS のレスポンダはレイヤーの走査から
得た前後の**序数**を報告する一方、Android は `View.getZ()` をデバイスピクセルで報告します。
`getZ()` は木全体ではなく同じ親を持つ兄弟同士の順序だけを決めるため、`getZ() == 8` の親の下にある
`getZ() == 0` の子は、その親の兄弟である `getZ() == 4` の要素より依然として手前に描かれます。した
がって 2 つの要素の `nativeZ` はバックエンドをまたいで比較できず、Android では親をまたいでも比較でき
ません。Unit 0 は、バックエンドをまたいで正規化した前後の序数にするか、それぞれのバックエンド固有の
単位を明示するかを選び、Unit 6 がその選択を[`docs/evidence.md`](../../docs/evidence.md)に記録しま
す。これにより読み手が `nativeZ` から、本提案の動機が導出した `z_index` 代替案を批判したのと同じ、
見かけ上権威ある誤った結論を引き出さないようにします。

### iOS: BajutsuKit の in-app フックへ新たに同期的な経路を通す

BajutsuKit はすでに、実行時に何かを計算してホスト側に報告する opt-in の in-app フックを 1 つ持って
います。`BajutsuScreen`(`BajutsuKit/Sources/BajutsuKit/BajutsuScreen.swift`、
[BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md))
です。`UIViewController.viewDidAppear` を swizzle し、完了した各遷移を、Python 側が Simulator の
共有ループバック上に立てる collector へ `POST` します。このチャネルは一方向でイベント駆動、つまり
アプリ側は、遷移が起きたときに送り出す形です。画面遷移の通知には向いていますが、本提案が必要とする
「今まさにこの要素の実際の位置は何か」を、ドライバが今まさに発行しているクエリと同じタイミングで
同期的に尋ねる用途には向きません。`BajutsuNet` の collector は受け取るだけで、答えを返しません。
この隙間を埋めるには、BajutsuKit に新しい能力が要ります。`BajutsuNet` がすでに使っている
`BAJUTSU_COLLECTOR` と同様の launch 環境変数で opt-in する、小さな in-app Hypertext Transfer
Protocol(`HTTP`)レスポンダです。
ドライバは自身の `/elements` クエリと並べてこれを呼び、レスポンダは古い push を再利用せず、要求
ごとに答えを新しく計算します。listener であるこのレスポンダには、送り出すだけの collector の形で
は要らなかった保護が必要です。ループバックだけに bind し、`BajutsuNet` がすでに持つ同じ per-run 共有
シークレット(`BAJUTSU_COLLECTOR_TOKEN`、`BajutsuKit/Sources/BajutsuKit/BajutsuNet.swift:16`)を毎回
の要求で必須にします。iOS のループバックはアプリ間で分離されておらず、このレスポンダはアプリの
ビュー階層全体を返すからです。この制約は後述する Unit 0 のポート形式の判断への入力であり、後付け
ではありません。固定の既知のポートは実機上の他のアプリから探知可能ですが、調整済みのポートはそう
ではありません。

このレスポンダが計算するのは、単純な `CALayer.zPosition` ではありません。Apple の
[`zPosition` 公式ドキュメント](https://developer.apple.com/documentation/quartzcore/calayer/1410884-zposition)
は、このプロパティを「兄弟レイヤーの順序を指定するために使うべきではない」と明記しており、通常の、
つまり 3D 変換のない場合は sublayer 配列自身の順序がその順序を決めます。`zPosition` の値をそのまま
渡すと、あたかも権威ある値のように見えながら、平坦なレイアウトでは全要素が 0 になりがちです。
これは、本提案の動機が BE-0349 の却下した `z_index` 代替案を批判した、まさにその失敗の形です。
レスポンダは代わりに実際のレイヤーツリー(各 `CALayer` の `sublayers` の順序に、0 でない
`zPosition` だけを反映させたもの)を辿り、`xcuitest.py` の `_query_with_handles`
(`bajutsu/drivers/xcuitest.py:533`)がすでに解決しているのと同じ識別子に紐づけて、要素ごとの確定
した前後インデックスを報告します。この計算で `UIView` と `CALayer` のどちらを辿る必要があるか、
あるいは両方を辿る必要があるかは、Unit 0 が実機で確認します。UIKit ほど直接的にレイアウトツリー
を外部に出さない SwiftUI のビュー階層についても、実際に画面へ合成される結果と一致するかを含めて
確認します。これは BE-0349 がドキュメントだけを信じずに `isHittable` の挙動を実機で確認したのと
同じやり方です。

### Android: アクセシビリティフレームワーク自身のオンデマンド追加データ機構

Android のアクセシビリティフレームワークは、Application Programming Interface(`API`)として
`AccessibilityNodeInfo` を公式に公開しています。Android には、まさにこの種の問題向けの、公式かつ
opt-in な機構がすでにあるため、この側には新しい
チャネルは要りません。`View` は
[`addExtraDataToAccessibilityNodeInfo(AccessibilityNodeInfo, String, Bundle)`](https://developer.android.com/reference/android/view/View)
をオーバーライドして、アクセシビリティクライアントが既定では要求しないデータを付加でき、同じ
オーバーライドの中で `AccessibilityNodeInfo.setAvailableExtraData(List<String>)` を通じてどの
追加データキーに対応しているかを宣言します。クライアント側、ここでは常駐サーバー自身のオンデバイス
リクエストハンドラ(`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt`
の `respondSource`、
[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md))
で、すでに `AccessibilityService` としてデバイスと通信しています。この `respondSource` がノード
ごとに[`AccessibilityNodeInfo.refreshWithExtraData(String key, Bundle args)`](https://developer.android.com/reference/android/view/accessibility/AccessibilityNodeInfo)
を呼び、結果を `getExtras()` から読み、今日すでに組み立てている Extensible Markup Language
(`XML`)ダンプと組み合わせます。この組み合わせは無償ではありません。`respondSource` の本体は
`settledDump` から `UiDevice.dumpWindowHierarchy` へと続く経路で作られており、これは 1 回のプラット
フォーム呼び出しの中で走査と直列化を同時に行うため、`refreshWithExtraData` を呼べる個々の
`AccessibilityNodeInfo` を外部に出しません。そのため追加データには `UiAutomation.getRootInActiveWindow()`
を使うもう 1 回の走査が必要になります。これはアクティブウィンドウしか対象にしませんが、
`dumpWindowHierarchy` は SystemUI のステータスバーを含む全ウィンドウに及びます。この 2 つの走査を
どう突き合わせるか、そしてもう一方の走査のコストは Unit 0 が確定します。これはプラットフォーム自身の
`EXTRA_DATA_TEXT_CHARACTER_LOCATION_KEY` がオンデマンドのテキスト境界のために使っているのと同じ
機構なので、本提案の Android 側 SDK は、`View` のサブクラスまたは `ViewCompat` 拡張からアプリが
呼ぶ小さなヘルパーで済みます。`view.getZ()`(elevation とZ軸方向の移動を合わせた値です。iOS の
`zPosition` とは違い、Android 自身の実際の描画順を並べ替える値であることを、BE-0349 自身のスパイク
が `View.elevation` についてすでに実証しています)を、Bajutsu 専用の追加データキーの下に報告します。
`dumpWindowHierarchy` の `XML` 形式には、プラットフォーム自身が定義していない値を入れる属性の枠が
ないため、`respondSource` は `nativeZ` を新しい `XML` 属性として直接追加できません。代わりに、`XML`
本体の `<node>` の並びとインデックスを揃えた小さな側方構造として各ノードの値を返します。文書順の
位置で紐づけるのであり、ノードの識別情報では紐づけません。識別情報だけではこれを紐づけられません。
`adb.py` 自身の `_identity()`(`bajutsu/drivers/adb.py:307`)が作る、`resource-id` / `content-desc` /
`text` / `class` の 4 項目の組は、意図的に一意ではないからです。常駐チャネルがこれを `index`
(`count` のうちの何番目か、`bajutsu/drivers/adb.py:104`–`106`)と組み合わせて使うのは、まさに同じ
内容の行を並べたリストは単一の識別情報に集約されてしまうからです。識別情報だけで紐づける実装は、その
ような行すべてに同じ `nativeZ` を黙って割り当ててしまいます。`bajutsu/adb_resident.py` の階層取得
(`fetch_source`、`bajutsu/adb_resident.py:90`)は今日と同じ、単一の `GET /source` 往復のままで、
この側方構造も一緒に運びます。そして `_elements_from_nodes`(`bajutsu/drivers/adb.py:322`)は、
`parse_hierarchy_with_identities`(`bajutsu/drivers/adb.py:336`)がすでに要素 *i* を対応づけている
のと同じ `<node>` の並びを辿っており、位置 *i* の値を要素 *i* の `nativeZ` へ運びます。この提案で
新たに加わる往復は `respondSource` の内部、つまりノードごとの `refreshWithExtraData` 呼び出しと、
上記の突き合わせのためのもう 1 回の走査です。これはそのノード自身の `getAvailableExtraDataKeys()`
がすでに Bajutsu のキーを含んでいる場合に限って発行されるため、協力しないアプリはこの 1 回の安価な
存在確認以上のコストを払いません。

Jetpack Compose のアクセシビリティノード生成(`AndroidComposeViewAccessibilityDelegateCompat`)が、
`Modifier` で宣言した追加データキーを、`View` ベースのオーバーライドが通常制御する
`AccessibilityNodeInfo` まで転送するかどうかは、まだ確認できていません。この生成処理自体は
Compose が握っており、本提案の著者も BE-0349 自身のスパイクもこれを検証していません。Unit 0 が
これを実機で確認します。BE-0349 自身の設計前スパイクが、ドキュメントで推測するのではなく
`View.elevation` と Compose の `zIndex` の違いを実機で確定させたのと同じやり方です。この節の
Android 側設計、具体的には Compose が上記の `View` オーバーライドとは別の報告経路を必要とするか、
それとも最初の一段では対象外とするかは、そのスパイクの結果が出た時点で確定します。

### コストは両 OS とも opt-in のまま

いずれの OS でも、協力しないアプリだけの run に発生する追加コストは、境界のある 1 回の探索呼び出し
を超えません。iOS 側では、レスポンダを組み込んでいないアプリに対してドライバの接続は拒否されます。
あるいは、ソケットが開いたまま一度も応答しない場合は、Unit 0 が定める短い connect/read タイムアウト
で終わります。いずれの場合も `nativeZ` は `None` のままです。これは `RawSourceProvider` を実装して
いないバックエンドが今
すでに受けている degrade と同じです。iOS には Android の(すでに手元にあるノード属性という)ゲート
に相当するものがなく、接続を試みる以外にアプリが計装されていないと知る手段がないため、ドライバは
最初の失敗をセッション単位でキャッシュして以降の探索を止めます。これにより協力しないアプリはこの
タイムアウトを一度だけ払い、`/elements` クエリごとに払うことはありません。これが、後述する
「保たれる大原則」で述べる「境界があり同期的」という意味を保つものです。Android のノードごとの
`refreshWithExtraData` 往復は、そのノード
自身の `getAvailableExtraDataKeys()` がすでに Bajutsu のキーを列挙している場合に限って発行される
ため、計装されていないアプリのツリー走査は 1 回の安価な存在確認だけで済みます。協力するアプリに
ついては、Unit 0 のスパイクで showcase 相当の大きさのツリーに対する要素ごとの往復コストも測定
します。この基準は、
[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)
自身の常駐チャネルの動機、つまり 1 回の読み取りごとのコストと同じです。この結果は、Unit 2・3 が
着手する前にこの節へ記録します。

### 作業分解(相互排他的かつ全体を尽くす、Mutually Exclusive, Collectively Exhaustive、`MECE`)

0. **スパイク。** `nativeZ` がバックエンドをまたいで何を意味するか(正規化した前後の序数か、明示
   したバックエンド固有の単位か)を、上記の詳細設計「`nativeZ` フィールド」に従って決める。iOS では、
   UIKit と SwiftUI の両画面について確定した前後インデックスを報告するために必要なレイヤーとビュー
   の走査方法を実機で確認する。レスポンダの connect/read タイムアウトと、協力しないアプリの 1 回の
   失敗探索が頼るセッション単位のネガティブキャッシュ設計を確定し、セキュリティ(固定のローカル
   ポートは実機上の他のアプリから探知可能)を既存の `BAJUTSU_COLLECTOR` と同様の launch 環境変数と
   比較しながらレスポンダの形を設計する。Android では、`Modifier.zIndex` と `graphicsLayer` を使った
   小さな Compose の画面で、Jetpack Compose のアクセシビリティノード生成が `Modifier.semantics`
   経由で宣言した独自の追加データキーを転送するかを確認し、`refreshWithExtraData` の呼び出しに必要な
   `UiAutomation.getRootInActiveWindow()` のノード集合が `dumpWindowHierarchy` の広い、複数ウィンド
   ウにわたるノード集合とどう突き合わさるかを確認する。上記の詳細設計にある文書順で紐づけた側方構造
   が、実際に付随する `XML` 本体と噛み合うことを前提のままにしないためである。両 OS で、showcase
   相当の大きさのツリーに対する要素ごとの往復コストを測定する。すべての結果を、Unit 2・3 が着手する
   前に上記の詳細設計へ記録する(動く / 回避策が必要 / 対応不可のいずれか)。これは BE-0349 自身の
   ログの記録の仕方と同じである。Unit 2・3 をブロックする。
1. **`nativeZ` フィールド。** `Element`(`bajutsu/drivers/base.py`)に追加する。`Element` は
   `total=False` を持たないため、adb、XCUITest、実機 XCUITest クライアント、
   web の Document Object Model(`DOM`)パーサー、
   `record_capture.py` のキャプチャ拒否時プレースホルダーを含む、既存のすべての dict リテラル構築箇所で
   `nativeZ` も設定しなければ `mypy --strict`(`make check` の一部)がビルドを止める。この検査自体
   が見落としを防ぐ安全網なので、この Unit で箇所を手作業で列挙する必要はない。まだ何もこれに
   実際の値を計算しないため、挙動は変わらない。
2. **iOS 側の報告。** 新しい BajutsuKit の in-app レスポンダと、`xcuitest.py` によるその読み取りを
   `nativeZ` へつなぐ。Unit 0 の結果でスコープが決まる。
3. **Android 側の報告。** app 側の追加データヘルパー。`ResidentServerTest.kt` の `respondSource` が
   行う opt-in なノードごとの `refreshWithExtraData` 呼び出しと、それが `XML` 本体と並べて返す側方
   構造。そして `adb.py` によるその構造から `nativeZ` への読み取り。Unit 0 の結果でスコープが決まり、
   Compose がキーを転送しないとわかった場合は `View` 系のみの最初の一段とする。
4. **`FakeDriver` 対応。** フィクスチャの要素ごとに設定可能な `nativeZ`
   (`bajutsu/drivers/fake.py`)を追加し、実機なしで `nativeZ` を読むシナリオレベルのテストを
   書けるようにする。
5. **driver conformance suite。**
   [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) の suite
   (`tests/driver_conformance.py`)に、フィクスチャまたはバックエンドが明示的に報告した場合に限り
   `nativeZ` が入り、それ以外は `None` のままであることを確認するケースを追加する。
6. **ドキュメント。** [`docs/evidence.md`](../../docs/evidence.md) と
   [`docs/architecture.md`](../../docs/architecture.md)(その `docs/ja/` 側も含む)に、`nativeZ`
   が診断専用であり、`is_tappable` / `topmost_at_point` / `isHittable` のいずれも変えないことを
   明記し、`nativeZ` がバックエンドをまたいで何を意味するかについての Unit 0 の決定を記録する。
7. **テスト。** 新しい報告経路について各バックエンドのユニットテストを書き、本提案によって
   `is_tappable` と `topmost_at_point` の挙動が変わっていないことを固定するリグレッションテスト
   も加える。

### 保たれる大原則

- **AI は判断しない。** `nativeZ` は app 側 SDK の呼び出しかアクセシビリティ API を通じて計測
  される値であり、これを生成または読み取る経路にモデル呼び出しは一切入らない。
- **決定性優先。** `nativeZ` の読み取りは両 OS とも境界のある同期呼び出しで、ポーリングも固定の
  `sleep` も持たない。今の `frame` の読み取りと同じ形である。
- **app 非依存。** SDK フックは両 OS とも統一された opt-in で、BajutsuKit の既存の
  `viewDidAppear` swizzle と同じ形である。ツール本体とドライバはそれぞれ 1 つのコードパスを追加
  するだけで、どこにも app 別の分岐はない。フックをリンクしないアプリは影響を受けず、
  `None` を報告する。

## 検討した代替案

- **`nativeZ` を今すぐ `topmost_at_point` / `is_tappable` に組み込み、診断専用のままにしないこと。**
  実際の値があれば、Android の遮蔽判定が `View.elevation` を誤判定しなくなるため検討した。
  本提案の最初の一段では却下する。Compose の追加データ対応がまだ確認できておらず、未検証の
  機構に、すでに動いている正しさのチェックを賭けるのは早計だからである。Unit 0 でこの機構が動くと
  確認できた後の、自然な後続作業として残す。
- **`rawTree` と並ぶ新しい opt-in の capture キーとし、`Element` の常時フィールドにしないこと。**
  `rawTree` の opt-in の形との対称性から検討した。却下する。`rawTree` はパース前の生の応答
  のスナップショットであり、意図的に `Element` 自体からは外されている
  ([docs/evidence.md](../../docs/evidence.md) 参照)。一方 `nativeZ` は `frame` や `traits` と
  同種の要素ごとの属性なので、読み手が `elements.json` と識別子で突き合わせなければならない第 2
  の成果物に持たせるより、`Element` 自身に持たせるべきである。これは `rawTree` のために
  `capture()` 自身の kind 順序付けルール(`bajutsu/evidence/core.py:218`)がすでに避けている、まさ
  にその整合性の問題である。
- **文書順とツールキットごとのヒューリスティックから一律に導出する、計測しない z フィールド。**
  これは BE-0349 が却下した `z_index` 代替案そのものの言い換えである。ここでも成り立たない理由は
  動機の節に述べたとおりである。
- **画面全体を覆うシステムダイアログやトーストといった、ウィンドウ間の遮蔽。** BE-0349 自身の
  見送りのまま対象外とする。`AccessibilityWindowInfo.getLayer()` は、そこでもっとも多い 2 つの
  ケースには不十分だとわかっており、本提案はその問いを再び開かない。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の `MECE` な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] Unit 0 — スパイク: iOS のレイヤー走査の実現性とレスポンダの形、Android の Compose の
      追加データ対応、両 OS での要素ごとの往復コスト
- [ ] Unit 1 — `Element` への `nativeZ` フィールド
- [ ] Unit 2 — iOS 側の報告(BajutsuKit のレスポンダ、`xcuitest.py` の読み取り)
- [ ] Unit 3 — Android 側の報告(追加データヘルパー、常駐サーバーの往復、`adb.py` の読み取り)
- [ ] Unit 4 — `FakeDriver` 対応
- [ ] Unit 5 — driver conformance suite のケース
- [ ] Unit 6 — ドキュメント(`evidence.md` / `architecture.md` とその `ja` 側)
- [ ] Unit 7 — テスト

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
