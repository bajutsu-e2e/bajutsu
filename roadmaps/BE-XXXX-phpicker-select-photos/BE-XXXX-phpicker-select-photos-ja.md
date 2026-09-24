[English](BE-XXXX-phpicker-select-photos.md) · **日本語**

# BE-XXXX — PHPickerViewControllerからの画像選択

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-phpicker-select-photos-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#2008](https://github.com/bajutsu-e2e/bajutsu/pull/2008) |
| トピック | Platform support |
| 関連 | [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)、[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)、[BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)、[BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`PHPickerViewController`は、プロフィール画像や投稿への添付画像など、ユーザーが画像を選ぶ場面で
iOSアプリが提示するシステム標準の写真選択UIです。Bajutsuは、アプリ自身の画面の外にあるOS所有の
UIも、すでに操作できます。`handleSystemAlert`によるSpringBoardの許可プロンプト
([BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md))と、
統合した要素ツリーによる別プロセスのアプリ内ブラウザ
([BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md))
がその例です。一方で、写真選択ピッカーはまだ操作できません。画像を選ぶステップが存在しないため、
シナリオはこの操作の手前で止まるか、選択済みの状態を外部から注入するほかありません。

この項目は、ピッカーのグリッドから序列位置で1枚以上の画像を選び、選択を確定する
`selectPhotos`ステップを追加します。あわせて、グリッドの中身に決定性を持たせるため、
Simulatorの写真ライブラリへ既知のフィクスチャ画像を投入する`seedPhotos`プリコンディション
も追加します。上記2件の前例とは異なり、ピッカーには第2のプロセスハンドルは不要で、ツリー
統合も要りませんでした。実測の結果、ホストアプリ自身のプロセス内で動作していたためです。
一方で、Apple silicon Simulatorではグリッドのセルへのアクチュエーションがどれも選択を
確実には成立させられませんでした。この結果は「詳細設計」のUnit 3に全体を記録していますが、
機能そのものを不可能にはしません。Apple silicon Simulator固有の制約だと独立に報告されている
ためです。そこで`selectPhotos`は、通常の要素がすでに使っているハンドル経由のタップが機能する
と裏付けられている範囲、すなわち実機またはIntel Simulatorに限定して出荷します。それ以外
(Apple silicon Simulatorを含みます)では、実行時に非決定的に失敗する代わりに、名前の付いた
ケーパビリティエラーを送出します。

## 動機

このギャップは、機能そのものではなく、ステップの欠如です。`permissions: { photos: grant }`は
すでに存在します(BE-0276)。`photos`は`camera`や`location`と同じ、ごく普通の`simctl privacy`
のTCCサービスです。これはアプリ側のクエリでは観測できないOSレベルの写真アクセスを事前に許可
します。しかし、この許可を与えることと画像を選ぶことは別の問いです。許可を与えても、その先で
ピッカー自体を操作して画像を選ぶ手段は生まれません。ピッカーのグリッド自体は、`permissions`が
原理的にも事前に答えられる類いの許可プロンプトを一切起こさないからです。「アプリがライブラリを
見てよいか」と「ユーザーがどの写真を選んだか」の間には、OSレベルの同意ゲートは存在せず、
グリッドそのものがあるだけです。そのため、プロフィール画像の変更
や投稿への画像添付を扱うシナリオは、ユーザーが実際にたどる操作を再現できません。具体的には、
ピッカーを開き、画像を選び、確定するという操作です。「選択済みの状態」をあらかじめ用意してそこから
検証するか、ピッカーが開く手前で検証を止めるかのどちらかになります。

設計を決めた事実は2つあり、どちらも推測ではなくshowcaseアプリ上で実測したものです。

- **ピッカーのグリッドの中身は、デフォルトでは決定的ではありません。** 新規のSimulatorの写真
  ライブラリには、シナリオが何かを追加する前から、サンプル画像(風景や花)が複数入っています。
  そのため`indices: [0]`は、シナリオ自身が画像を投入しない限り、ライブラリがたまたま持っている
  中身を指します。`simctl`には現状メディアを追加するラッパーが一切なく、
  `bajutsu/common/backend_cli/simctl/_functions.py`は`privacy`・`push`・`erase`・`boot`は
  ラップしていても`addmedia`はラップしていません。
- **グリッドのセルは、通常の要素と同じハンドル経由の方法ではタップできません。** showcaseアプリ
  に対しXcode 26.6・iOS 26.5で実測しました。まず`{ id: "PXGGridLayout-Info", index: 0 }`で
  セルを解決します(全セルが同一の識別子を共有しているため、`index`だけが1つを名指しする手段です。
  詳細はUnit 2)。この解決結果をドライバの既存の`/tap`でタップすると、再現性をもって
  `element vanished (stale handle)`で失敗します。ドライバ自身が持つ失効時の再試行ループ
  (`_STALE_MAX_ATTEMPTS`回の再解決)を使い切っても、なお失敗します。ピッカーのコレクション
  ビューは、解決からアクチュエーションまでの間にセルのアクセシビリティノードを毎回無効化して
  おり、一時的な競合ではなく2回とも再現する失敗でした。同じピッカーを開いた直後に`Cancel`を
  同じ経路で解決してタップした場合は毎回成功しており、影響を受けるのは再利用されるグリッドの
  セルだけです。これは、アプリ内ブラウザのクロームで
  [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)
  が遭遇した失敗によく似ています。あちらは`stale`と報告される代わりに、`XCUIElement.tap()`が
  黙って効かない形でした。ただし今回はあちらと違い、対応する修正(解決済みの`XCUIElement`
  自体ではなく、要素の実際のフレーム中心を生の座標でタップする)は通用しません。実際に試すと、
  同じように黙って効かず、何も選択されません。Unit 3ではこの調査の全体と、そこから導いた
  ケーパビリティによる範囲限定を記録しています。

showcase自身のシナリオ(`select_photos.yaml`、Unit 4)は、Unit 1・2・4を、どのSimulator上
でも2枚のフィクスチャを投入したライブラリに対して検証します。あわせて、Unit 3の
ケーパビリティによる拒否を、この調査のMacが持つアーキテクチャ、すなわちApple silicon上で
検証します。Unit 3の
アクチュエーションが成功する側は検証できません。それには、この調査ではアクセスできなかった
ハードウェア(実機、またはIntel Mac)が必要だからです。そうしたハードウェアを持つ後の読者は、
同じシナリオをそこで実行し、`Selected: 2`になることを確認すれば、直接確かめられます。

## 詳細設計

### Unit 1: Simulatorの写真ライブラリへの投入

`bajutsu/common/backend_cli/simctl/_functions.py`に`addmedia_cmd(udid: str, media_path: str)
-> list[str]`を追加します。`privacy_cmd`・`push_cmd`と同じ形の引数配列ビルダーです。

```python
def addmedia_cmd(udid: str, media_path: str) -> list[str]:
    return ["xcrun", "simctl", "addmedia", validated_udid(udid), media_path]
```

1回の呼び出しにつきパスは1つだけです。バッチ呼び出しにはしません。後述する新しい順の並びは、
1つ前の呼び出しが完了してから次を投入する**別々の**呼び出しでのみ実測しており、1回の呼び出しに
複数のアセットをまとめて渡したときに`simctl`がどんな相対順序を割り当てるかはわかっていません。
バッチ呼び出しでは2枚のフィクスチャが同じタイムスタンプを持つこともあり得て、その場合はグリッド
の並びが`seedPhotos`の列挙順ではなく、その呼び出しがたまたま作った順になります。
`Env.add_media`(`bajutsu/common/backend_cli/simctl/env.py`)は、与えられたパスを1つずつ順に
`addmedia_cmd`へ渡すループにします。これにより、どの呼び出し元も実測した並びをそのまま得られ
ます。

`privacy`・`push`とは異なり、`addmedia`はバンドル単位ではなくデバイス全体の写真ライブラリへ
投入します。同じパスに対して再実行すると、冪等にはならずライブラリに重複したエントリが増えて
いきます。`seed_photos: list[str]`(YAMLでは`seedPhotos`)を追加する先は、`Scenario`ではなく
`Preconditions`(`bajutsu/common/scenario/models/scenario/preconditions.py`)です。同じリセット
をすでに司っている`erase`・`reinstall`の隣に置きます。`Scenario`側(`permissions`と同じ場所)
ではなくここに置くことで、後述の`_prepare_simulator`へ新しい配線を要らずに届きます。
`pre: Preconditions`はすでにそのメソッド自身の引数の1つだからです。`Scenario`側のフィールドに
すると、`permissions`がそうしているように(`bajutsu/common/runner/pool.py:419`)
`launch_driver`と`RunEnvironment.start`を通して配線する必要が生じますが、`_prepare_simulator`
はそもそも`Scenario`全体を必要としません。

`seed_photos`が空でない場合は、同じ`Preconditions`上で`erase: true`を必須とし、シナリオの
読み込み時に例外を送出する`model_validator`で確認します。声を上げるのであって、投入を黙って
飛ばすのではありません。これがなければ、`seedPhotos`だけを設定して`erase`を設定しないシナリオ
は何も投入しないまま(後述のゲートが一度も開かないため)エラーにもならず、
`selectPhotos: { indices: [0, 1] }`はSimulatorの既存ライブラリがたまたま持っている中身を
黙って指すことになります。これは「検討した代替案」が退けているのと同じ非再現的な状態に、
設計ではなく見落としによって行き着いた形です。パスの解決は`dataFile`がすでに使っているものと
同じです。シナリオファイル自身のディレクトリを起点とし、共有の`contained_ref`の関所
(`bajutsu/common/scenario/load_expanded.py:21-45`)によってスイートルートの内側に収まって
いるかを確認します。**ルートは起点ではなく境界です**。パスを連結する起点ではありません。これは
`dataFile`の解決方法そのままです。`bajutsu run`自身のローダー(`bajutsu/run/cli.py`)も
`dataFile`・`use`の参照をこの同じ関数で解決しているため、`seedPhotos`は両方の入口で追加の
作業なしに同じ封じ込めを引き継ぎます。

投入先は`_prepare_simulator`
(`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py:866-939`)
に限ります。対象は、コールド起動かつ`erase`を伴う経路(`cold and pre.erase`)だけです。上記の
バリデータにより、この経路は`erase`を伴う場合にしか`seed_photos`が空でなくなりません。この
経路はすでにSimulatorの既存状態を消去する経路です。ここに乗せることで、`cold`が`False`となる
ウォーム再開のリース(このブロック自体が実行されません)での重複投入を防げます。

ピッカーはライブラリを新しい順に並べます。実測では、数秒間隔で3枚のフィクスチャをそれぞれ別々の
`addmedia`呼び出しで追加したところ、追加順とは逆順に並びました。最後に追加したフィクスチャが
インデックス0に来ます。これは、Simulatorにあらかじめ入っているサンプル画像よりも前に来ます。
したがって、シナリオの`indices`は、`seedPhotos`に列挙した順序とは逆順でフィクスチャを指す
ことになります。この対応は、動作から発見させるのではなく、DSLのリファレンスに明記します。

### Unit 2: `selectPhotos` DSLアクション

```yaml
- selectPhotos:
    indices: [0, 1]
    timeout: 10
```

`bajutsu/common/scenario/models/actions/select_photos.py`に`SelectPhotos`
(`indices: list[int]`、`timeout: float`)を定義します。バリデーションは`HandleSystemAlert`が
自身の形を検証するのと同じ方法(`@model_validator(mode="after")`)で行います。`indices`が
空でないこと、負値を含まないこと、重複がないことを確認します。`sel`フィールドは不要です。セルには
シナリオ作者が名指しできるアプリ側の識別子がなく、グリッド上の序列位置だけが手がかりになる点は、
`handleSystemAlert`がラベルを付けられないSpringBoardのボタンを序列位置で扱うのと同じです。

グリッド内の全セルは`PXGGridLayout-Info`という1つの識別子を共有しています(実測。セルごとの値
ではありません)。そのため、1つのセルを他と区別する手段になるのは、`Selector`の持つ汎用の
`index`フィールドです(`bajutsu/common/scenario/models/selector.py`。
`bajutsu/common/drivers/base/_functions.py:321`の`resolve_unique`が消費します)。これは
`handleSystemAlert`がSpringBoardのボタンに対してすでに頼っている「複数候補のn番目」という
同じ仕組みです。新しいセレクタフィールドは不要です。`select_photos`のドライバメソッドは、
要求されたインデックスごとに通常の`{ id: "PXGGridLayout-Info", index: i }`セレクタを組み立て、
既存の`/elements`クエリで解決します。グリッドに専用のクエリエンドポイントは不要でした。必要
だったのは、そのクエリがすでに見つけている対象を「タップする」専用の方法だけです(Unit 3)。

### Unit 3: セルのアクチュエーション、機能する範囲に限定

`XcuitestDriver.select_photos(indices: list[int], *, timeout: float) -> None`は、`/elements`を
呼んで要求された各インデックスを`{ id: "PXGGridLayout-Info", index: i }`に対して解決し、
解決した各セルを通常の要素と同じハンドル経由の`/tap`でタップし、ピッカーがまだ提示されていれば
確定コントロール(後述)をタップします。専用のアクチュエーションプリミティブも新しいSwift側の
エンドポイントも要りません。DSLの既存の`tap`アクションがすでに使っているのと同じ仕組みです。
これは、このUnitが以下で導く範囲限定を前提にしてはじめて成り立ちます。この特定のビューに対して
壊れていると実証済みの仕組みの上に組み立てるのでは、以前の調査の誤りを繰り返すだけであり、
解決にはなりません。

ピッカーの提示方法はUnit 4が指定するとおりにしました。`selectionLimit = 0`(無制限)、
SwiftUIから`UIViewControllerRepresentable`経由です。showcaseアプリに対し、Xcode 26.6、
この調査で使えた唯一のMac(Apple silicon、M系チップ)で実測したところ、グリッドのセルへの
アクチュエーション手法はどれも選択を成立させられませんでした。

| 手法 | `via` | 結果 |
|---|---|---|
| ハンドルで解決したセルへの`XCUIElement.tap()` | handle | `element vanished (stale handle)`。ドライバ自身の失効時再試行ループを使い切っても再現する |
| 同じハンドルへの`XCUIElement.press(forDuration:)`(0.05秒・0.4秒の両方) | handle | どちらの秒数でも同じ`stale handle`で失敗 |
| セルの実際のフレーム中心への生の座標タップ(既存の`/tap`エンドポイントの`point`フィールド。DSLの`tapPoint`アクションがすでに使っているものなので、この試行のために新しいエンドポイントすら要らない) | coordinate | エラーは出ないが、どのセルも選択済みにならない。タップは受理されるが、観測できる効果が何もない |
| 同じ座標への0.15秒の**プレス**(`XCUICoordinate.press(forDuration:)`。[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)がブラウザのフレーム中心タップに使っているのと同じ仕組みを`tapPoint`に拡張し、この確認のために試作した) | coordinate | 生の座標タップと同じ結果。受理されるが、どのセルも選択済みにならない |

最初の3つの試行を、iOS 26.5とiOS 18.6の両方のSimulatorで繰り返し、結果は同一でした。これは
iOSバージョンの退行が原因ではないことを示しています。同じピッカーの中で、再利用されない
コントロール、`Cancel`に対して構造的に同じタップを行うと、同じ瞬間でも毎回成功します
(動機を参照)。したがって失敗しているのは、ピッカー一般でもXCUITestのタップ一般でもなく、
グリッドのセルに固有の何かです。

これはApple自身のdeveloper forumsで独立に報告されている制約と一致します。
`UIImagePickerController`・`PHPickerViewController`内の画像選択が、Apple silicon
Simulator上のXCUITestでは登録されない一方、Intel SimulatorやRealデバイスでは機能する、
という報告です([Apple Developer Forums, thread
714024](https://developer.apple.com/forums/thread/714024)、[Bitrise Discussions, "Cannot
pick image during
XCUITest"](https://discuss.bitrise.io/t/cannot-pick-image-during-xcuitest/14427))。
Bajutsuは、iOSのもう1つのアクチュエーション手段だった`idb`を
[BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md)
で廃止しており、iOS Simulatorを操作する経路はXCUITestだけが残っています。この制約を
迂回する既存の代替経路は、ツール側にありません。

決定性(prime directive 2)は、中核となる唯一の操作が、今多くの開発者が使っている
アーキテクチャ、すなわちApple silicon Mac上で機能しないステップの出荷を認めません。しかも、
シナリオ作者にはそれを事前に知る手段がありません。4番目の試行によって、もっとも有力だった
残りの候補もふさがりました。
[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)
自身の修正を、タップからプレスへ一般化しても、このコレクションビューには届きません。Apple側の
修正や回避策、あるいはまだ試していないアクチュエーション手法を待つのではありません。この項目は
`selectPhotos`を、通常のタップの仕組みが機能すると裏付けられている範囲、すなわち実機または
Intel Simulatorに限定します。それ以外ではシナリオを事前に拒否します。実機に欠けているsimctl系
ケーパビリティに対して`capabilities_for_run`がすでに与えているのと同じ、事前に確実に失敗を
知らせる約束です(BE-0238 Unit 3)。

`backends.apple_silicon_simulator_host() -> bool`(`bajutsu/common/backends.py`)は、対象の
デバイスに対して`xcrun simctl`を問い合わせるのではなく、bajutsuを実行しているマシン自身の
`platform.machine() == "arm64"`を読み取ります。この2つは同じ事実です。Simulatorのゲスト
OSは、別のアーキテクチャをエミュレートするのではなくホストのプロセッサ上でネイティブに動作するため、
「Apple silicon Simulator」と「Apple silicon Mac上のSimulator」は同じものを指します。単純な
ホストアーキテクチャの読み取りは、`simctl`呼び出しが抱えるデバイス障害由来の不安定さ
(prime directive 2)を負いません。`capabilities_for_run`(実機に対する実行時の絞り込みを
すでに担っている箇所。BE-0238)は、対象がSimulatorであり(実機でなく)、かつApple silicon
ホスト上である場合に限り、`XcuitestDriver.CAPABILITIES`の静的な集合から`SELECT_PHOTOS`を
落とします。Intelホストや実機ではそのまま残ります。ケーパビリティプリフライト(BE-0082)は、
そこで`selectPhotos`ステップをデバイス操作前に拒否します。`pickerWheel`・`selectOption`
にすでに与えているのと同じ、事前に確実に失敗を知らせる仕組みです。

**この調査が検証できたことと、できなかったこと。** 拒否される経路はここで完全に検証済みです。
この項目のテストスイートのうち、`apple_silicon_simulator_host`・`capabilities_for_run`の
絞り込み・ケーパビリティプリフライトを検証するものは、いずれも実機なしで動作し、成功しています。
一方、実機またはIntel Simulator上でアクチュエーションが実際に成立する側は、実機で検証できません
でした。どちらのハードウェアもこの調査では入手できなかったためです。代わりに拠り所とするのは
2つの事実です。1つは、これが同じピッカーの*別の*コントロール(前述の`Cancel`)や、この
ドライバが扱う他のあらゆる要素種別に対してすでに機能すると実証済みの、まさにその仕組みで
あるという事実です。もう1つは、Apple Developer ForumsやBitriseの報告が、この失敗をApple
silicon Simulator固有のものと名指ししており、XCUITestのタップ一般の問題だとはしていないと
いう事実です。この調査に欠けていた
ハードウェアを持つ後の読者は、`select_photos.yaml`(Unit 4)を実機またはIntel Macで実行し、
`Selected: 2`になることを確認すれば、そのギャップを直接埋められます。

確定ボタンはラベルではなく**構造的に**解決します。ピッカーが提示するナビゲーションバー
(`traits: ["navigationBar"]`。ピッカーが持つバーはこの1つだけです。バー自身のタイトル
「Photos」で名指すのではありません。このタイトルも`PHPickerViewController`が他のラベルと
同様にローカライズします)の内側にあるボタンのうち、識別子が`Cancel`ではないものです。実測
では、ピッカー自身の解散コントロールは`Cancel`という安定した識別子を持つのに対し、確定
コントロールには識別子がなく、ラベルも「Add」ではなく「Done」でした(このiOSバージョンでは
チェックマークの図柄です)。バーそのものの内側で、どちらのボタンのラベルにも頼らず除外に
よって解決する方法を取れば、この解決経路のどこにもロケールとともに変わる文字列は登場しません。
バーは自身のトレイトで見つけるのであって、ローカライズされたタイトルでは見つけないからです。
SpringBoardのアラートボタンとは違い、ここで安定しているのは確定コントロールのラベルではなく
識別子が付いていないという事実です。この解決方法は上記のアクチュエーションの範囲限定の
影響も受けません。ナビゲーションバーのボタンは再利用されるセルとは異なる静的なクローム要素
であり、`Cancel`の実測がすでに、この調査でテストできた唯一のアーキテクチャ上で、静的な
クロームはハンドル経由の通常の経路で問題なくアクチュエーションできることを示しています。
範囲限定そのものが拠り所としているのと同じ根拠です。

### Unit 4: Capability、他バックエンド、showcaseでの実証

`Capability.SELECT_PHOTOS = "selectPhotos"`(`bajutsu/common/drivers/base/capability.py`)は、
`XcuitestDriver.CAPABILITIES`(ユニットテスト用に`FakeDriver.CAPABILITIES`にも)が静的な
集合として宣言し、Apple silicon Simulator上では`capabilities_for_run`が実行時に取り除きます
(Unit 3)。いずれの場合も`bajutsu/common/capability/capability_preflight.py`で
`HANDLE_SYSTEM_ALERT`と同様にゲートします。Androidやwebのターゲット、あるいはApple silicon
Simulatorのターゲットが`selectPhotos`を使うと、実行時の不透明なエラーではなく、プリフライトの
時点で名指しのケーパビリティエラーになります。`playwright_driver.py`・
`adb_driver.py`・`xcuitest_live_driver.py`・`web_context_driver.py`は、それぞれ自身の
`select_photos`から`UnsupportedAction`を送出します。他のiOS専用アクションと同じ扱いです。

showcaseの`PermissionsView.swift`(SwiftUIのみ。UIKit側への展開は範囲外とします。「検討した
代替案」を参照)に「Photos」セクションを追加します。「Open Photo Picker」ボタン
(`perm.openPhotoPicker`)は、シートを提示します。シートの中身は`selectionLimit = 0`(無制限。
これにより確定タップの経路が常に実行されます)にした`PHPickerViewController`を
`UIViewControllerRepresentable`でラップしたものです。ミラーされた`Text`
(`perm.photos.value`)が選択件数を報告します。両方のIDは既存の`perm`名前空間にそのまま
収まるため、`idNamespaces`の変更は不要です。
`demos/showcase/fixtures/photos/`には判別できるフィクスチャ画像(単色)を数枚用意します。
`demos/showcase/scenarios/select_photos.yaml`が`preconditions: { erase: true, seedPhotos: [...]
}`(Unit 1のバリデータが`seedPhotos`と`erase: true`の同時指定を求めます)でこれらを投入し、
`perm.openPhotoPicker`をタップし、`selectPhotos: { indices: [0, 1] }`を実行し、
`perm.photos.value`が`2`になることを検証します。`demos/showcase/SPEC.md`§5.4には、この
セクションの既存IDと並べて新しい2つのIDを記載します。

## 検討した代替案

- **専用アクションではなく、汎用`tap`ステップでグリッドに到達する。** セレクタの半分は実際に
  今日でも動作します。`{ id: "PXGGridLayout-Info", index: 0 }`は新しいフィールドを要しない
  ただのセレクタです。それでも採用しなかったのは、アクチュエーションの半分が理由です。シナリオ
  作者が明示的に名指しすることになる同じ汎用`/tap`こそ、Unit 3でこのコレクションビューに対して
  失敗すると実測したものです。汎用ステップは、ピッカーのセルを扱っているとDSLが把握していない
  限り、専用のアクチュエーション経路へ振り分けられません。これが、既存ステップの組み合わせでは
  なく専用アクションにした理由です。
- **ピッカーをSpringBoardやSafariViewServiceと同様に扱う。** 別の`XCUIApplication`ハンドル、
  専用のクエリエンドポイント、別のハンドルストアを持たせる案です。実測の結果、不要でした。
  ピッカーが提示されている間、`launchctl list`に別プロセスは一切現れません。アプリ自身の
  `/elements`が、すでに全セルを報告しています。存在しないプロセス境界のために、2つ目の
  `SnapshotStore`、ハンドル衝突の回避、バンドル識別子の発見をわざわざ構築することになります。
- **確定コントロールを、`handleSystemAlert`がSpringBoardのボタンをロケール別ラベル表で解決
  するのと同じ方法で、ラベル「Done」で解決する。** このコントロールには識別子がないため、
  ラベルによる照合が最初の候補でした。採らなかったのは、構造的な除外(Unit 3)を選んだためです。
  このコントロールは、安定した識別子を持つ`Cancel`の隣にあります。これを除外するだけで済み、
  ラベル文言やロケール表には頼りません。他言語でラベルがどう読まれても影響を受けません。代償は、
  将来のiOSバージョンがそのバー内に3つ目のボタンを識別子付きで追加した場合に、ルールの
  再確認(再設計ではなく)が必要になることだけです。
- **`seedPhotos`を省き、シナリオにはSimulatorのライブラリがすでに持っている中身に頼らせる。**
  ライブラリはデフォルトで空ではありません(サンプル画像入り)。しかしその中身はこのプロジェクト
  のフィクスチャではなく、Xcode・Simulatorのランタイムバージョンをまたいで安定している保証も
  なく、バージョン管理下にもありません。`indices`がそれを指すと、マシンやCI実行をまたいで
  再現しなくなります。単に不便という以上に、決定性(prime directive 2)そのものに反します。
- **Apple側の修正や文書化された回避策、あるいはまだ試していないアクチュエーション手法を
  待ってから出荷する。** 却下しました。今回の4つの試行にも、それが一致する外部の報告にも、
  修正がいつ来るかを示す手がかりはありません。Unit 1・2・4は、Unit 3が将来どのアーキテクチャ
  まで対応できるようになっても手直しを必要としません。項目全体を、時期の読めない外部の修正
  待ちにすることは、すでにこの仕組みが機能しているハードウェア(実機、Intel Mac)を持つ
  すべての開発者から、今日使える機能を奪うことになります。
- **Simulator自身のアーキテクチャを、ホストではなく`xcrun simctl`への問い合わせで判定する。**
  却下しました。SimulatorのゲストOSはホストのプロセッサ上でネイティブに動作するため、両者への問いは
  同じ答えを返します。`simctl`の往復は、単純な`platform.machine()`の読み取りにはない
  デバイス障害由来の不安定さ(BE-0363)を負うだけで、同じ結果を得るのに単にホストを見るより
  複雑です。
- **同じ項目内でUIKit showcaseにも対応する。** 却下ではなく先送りです。上記のすべてのUnitを
  実証・文書化するにはSwiftUIの画面で十分です。`PHPickerViewController`はいずれにせよUIKit
  の型なので、UIKit側に持たせても新しいドライバ側のカバレッジは増えません。同じ配線を複製した
  もう1つのフィクスチャ画面が増えるだけです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解(作業の単位ごとに1つ)に対応し、ログには変更内容と時期(古い順)を PR へのリンクと
> ともに記録します。

- [x] Unit 1 — `addmedia_cmd`、`seedPhotos`プリコンディション、`_prepare_simulator`での
  `cold`かつ`erase`条件付きの投入。
- [x] Unit 2 — `SelectPhotos` DSLアクションとそのバリデーション。
- [x] Unit 3 — ピッカーのグリッドセルのアクチュエーション。`apple_silicon_simulator_host`
  による実機・Intel Simulator限定の範囲設定(「詳細設計」を参照)と、確定ボタンの構造的な解決。
- [x] Unit 4 — `SELECT_PHOTOS`ケーパビリティ、他バックエンドの`UnsupportedAction`、showcase
  での実証(`PermissionsView.swift`、フィクスチャ画像、`select_photos.yaml`、`SPEC.md`)。

ログは次のとおりです。

- 2026-09-16 — showcaseアプリに対してUnit 3を調査しました(Xcode 26.6、iOS 26.5とiOS 18.6の
  両Simulator、Apple silicon Mac)。最初の調査の後に試作した4番目の手法(座標プレス。
  `tapPoint`の`duration`を拡張して検証し、この項目を解除できなかったため元に戻しました)を
  含め、試したアクチュエーション手法はどれもセルの選択を成立させられませんでした。他の
  3つのUnitだけを出荷せず、この項目を保留としました。PRはありません。中核となる仕組みが
  動かない項目からは、何もマージしないためです。
- 2026-09-24 — 新しいアクチュエーション手法を見つけるのではなく、範囲を絞ることでUnit 3を
  解決しました。`selectPhotos`は、通常の要素が使っているのと同じハンドル経由のタップを使って
  出荷し、Apple silicon Simulatorに対しては`capabilities_for_run`
  (`apple_silicon_simulator_host`、ホストの`platform.machine()`読み取り)で無効化します。
  4つのUnitすべて、showcaseでの実証、そしてそれぞれのテストを実装し、`make check`が通ることを
  確認しました。実機またはIntel Simulator上で成立する側は、この調査ではそのハードウェアに
  アクセスできなかったため実機で検証できておらず、後の読者が`select_photos.yaml`をそこで
  実行して確認することに委ねます。

## 参考

- `bajutsu/common/scenario/models/actions/handle_system_alert.py` — この項目のUnit 2と
  Unit 3が部分的に踏襲し、部分的に離れているアクション形状とロケール表の前例。
- `bajutsu/common/drivers/base/_functions.py` — この項目が拡張ではなく再利用する、
  `resolve_unique`の既存の`index`処理(321行目)。
- [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)
  — Unit 3が試した、フレーム中心アクチュエーションと`Tappable`の仕組み、およびそれがピッカーの
  セルには通用しない理由。
- [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md)
  — `idb`の廃止。Unit 3が実測したApple silicon Simulatorの制約を迂回できていた可能性がある、
  iOSのもう1つのアクチュエーション手段であり、現在残っているのはXCUITestだけである。
- [Apple Developer Forums, thread 714024](https://developer.apple.com/forums/thread/714024)、
  [Bitrise Discussions, "Cannot pick image during
  XCUITest"](https://discuss.bitrise.io/t/cannot-pick-image-during-xcuitest/14427) —
  Apple silicon SimulatorのXCUITest下で画像ピッカーの選択が登録されないという、同じ系統の
  失敗の独立した報告。ここでの実測と一致する。
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) —
  この項目のセルのインデックス指定が踏襲する、序列位置によるラベル不要のボタン指定。
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) —
  この一連の流れが起こすプロンプトのうち、ピッカー自身のグリッドを除くすべてに答える
  `permissions:`の事前許可。
- [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md) —
  実機に対する実行時のケーパビリティ絞り込み(`capabilities_for_run`)。この項目自身の
  Apple silicon絞り込みは、これを逆方向(実機に対してケーパビリティを戻すのではなく、
  Simulatorのデフォルトからケーパビリティを取り除く)に拡張したもの。
- [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md) —
  `SELECT_PHOTOS`を欠くバックエンド、またはSimulator・ホストの組み合わせに対して、
  `selectPhotos`ステップを事前に拒否するプリフライト。
- [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout-ja.md) —
  ホストの`platform.machine()`読み取りが避けている`simctl`のデバイス障害由来の不安定さ
  (「検討した代替案」)。同じ事実を`simctl`への問い合わせで得ようとすると、これを負うことになる。
