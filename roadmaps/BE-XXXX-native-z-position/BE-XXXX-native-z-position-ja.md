[English](BE-XXXX-native-z-position.md) · **日本語**

# BE-XXXX — 要素の実際の Z 位置を、opt-in の app 側 SDK 経由で明示する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-native-z-position-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
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
レコードに加わり、読まれるだけで絞り込みには使われません。

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
ごとに答えを新しく計算します。

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
で、すでに `AccessibilityService` としてデバイスと通信しています。この `respondSource` が今日
すでに組み立てている Extensible Markup Language(`XML`)ダンプを直列化して返す前に、ノードごとに
[`AccessibilityNodeInfo.refreshWithExtraData(String key, Bundle args)`](https://developer.android.com/reference/android/view/accessibility/AccessibilityNodeInfo)
を呼び、結果を `getExtras()` から読みます。これはプラットフォーム自身の
`EXTRA_DATA_TEXT_CHARACTER_LOCATION_KEY` がオンデマンドのテキスト境界のために使っているのと同じ
機構なので、本提案の Android 側 SDK は、`View` のサブクラスまたは `ViewCompat` 拡張からアプリが
呼ぶ小さなヘルパーで済みます。`view.getZ()`(elevation とZ軸方向の移動を合わせた値です。iOS の
`zPosition` とは違い、Android 自身の実際の描画順を並べ替える値であることを、BE-0349 自身のスパイク
が `View.elevation` についてすでに実証しています)を、Bajutsu 専用の追加データキーの下に報告します。
`dumpWindowHierarchy` の `XML` 形式には、プラットフォーム自身が定義していない値を入れる属性の枠が
ないため、`respondSource` は `nativeZ` を新しい `XML` 属性として直接追加できません。代わりに、収集
した各ノードの値を、ノードの識別情報で紐づけた小さな側方構造として、変更のない `XML` 本体と並べて
返します。この識別情報は `resource-id` / `content-desc` / `text` / `class` の 4 項目の組です。`adb.py`
自身の `_identity()`(`bajutsu/drivers/adb.py:307`)が、常駐チャネルの stale handle 再解決のために
すでに使っているものと同じです。`bajutsu/adb_resident.py` の階層取得(`fetch_source`、
`bajutsu/adb_resident.py:90`–`123`)は今日と同じ、単一の `GET /source` 往復のままで、この側方構造
も一緒に運びます。`adb.py` の `_to_element`(`bajutsu/drivers/adb.py:280`)がこれを識別情報
で解析済みノードへ突き合わせ、`nativeZ` へ運びます。この提案で新たに加わる往復は `respondSource`
の内部、つまりノードごとの `refreshWithExtraData` 呼び出しだけです。これはそのノード自身の
`getAvailableExtraDataKeys()` がすでに Bajutsu のキーを含んでいる場合に限って発行されるため、協力
しないアプリはこの 1 回の安価な存在確認以上のコストを払いません。

Jetpack Compose のアクセシビリティノード生成(`AndroidComposeViewAccessibilityDelegateCompat`)が、
`Modifier` で宣言した追加データキーを、`View` ベースのオーバーライドが通常制御する
`AccessibilityNodeInfo` まで転送するかどうかは、まだ確認できていません。この生成処理自体は
Compose が握っており、本提案の著者も BE-0349 自身のスパイクもこれを検証していません。Unit 0 が
これを実機で確認します。BE-0349 自身の設計前スパイクが、ドキュメントで推測するのではなく
`View.elevation` と Compose の `zIndex` の違いを実機で確定させたのと同じやり方です。この節の
Android 側設計、具体的には Compose が上記の `View` オーバーライドとは別の報告経路を必要とするか、
それとも最初の一段では対象外とするかは、そのスパイクの結果が出た時点で確定します。

### コストは両 OS とも opt-in のまま

いずれの OS でも、協力しないバックエンドやアプリに追加コストは発生しません。iOS 側では、レスポン
ダを組み込んでいないアプリに対してドライバの接続は単に拒否される、または応答が返らないだけで、
`nativeZ` は `None` のままです。これは `RawSourceProvider` を実装していないバックエンドが今すでに
受けている degrade と同じです。Android のノードごとの `refreshWithExtraData` 往復は、そのノード
自身の `getAvailableExtraDataKeys()` がすでに Bajutsu のキーを列挙している場合に限って発行される
ため、計装されていないアプリのツリー走査は 1 回の安価な存在確認だけで済みます。協力するアプリに
ついては、Unit 0 のスパイクで showcase 相当の大きさのツリーに対する要素ごとの往復コストも測定
します。この基準は、
[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)
自身の常駐チャネルの動機、つまり 1 回の読み取りごとのコストと同じです。この結果は、Unit 2・3 が
着手する前にこの節へ記録します。

### 作業分解(相互排他的かつ全体を尽くす、Mutually Exclusive, Collectively Exhaustive、`MECE`)

0. **スパイク。** iOS では、UIKit と SwiftUI の両画面について確定した前後インデックスを報告する
   ために必要なレイヤーとビューの走査方法を実機で確認し、新しい同期的な in-app レスポンダの形
   (固定のローカルポートか、既存の `BAJUTSU_COLLECTOR` と同様の launch 環境変数で調整するポート
   か)を設計する。Android では、`Modifier.zIndex` と `graphicsLayer` を使った小さな Compose の
   画面で、Jetpack Compose のアクセシビリティノード生成が `Modifier.semantics` 経由で宣言した独自
   の追加データキーを転送するかを確認する。両 OS で、showcase 相当の大きさのツリーに対する要素
   ごとの往復コストを測定する。すべての結果を、Unit 2・3 が着手する前に上記の詳細設計へ記録
   する(動く / 回避策が必要 / 対応不可のいずれか)。これは BE-0349 自身のログの記録の仕方と
   同じである。Unit 2・3 をブロックする。
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
   明記する。
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
