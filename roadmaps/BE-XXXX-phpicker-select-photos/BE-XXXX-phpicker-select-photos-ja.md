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
| 関連 | [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)、[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)、[BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md) |
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
グリッドのセルに対するハンドル経由のタップ(通常の要素がすべて使っている仕組み)は失敗すると
実測した一方、解決したセルの正確なフレーム中心への生の座標タップは確実に選択を成立させると
実測しました。`selectPhotos`は後者を使って出荷し、この項目の`Capability.SELECT_PHOTOS`が
広告されるすべてのSimulator・実機で同じように動作します。

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
  に対し実測しました。環境はXcode 26.6・iOS 26.5のApple silicon Simulatorです。まず
  `{ id: "PXGGridLayout-Info", index: 0 }`でセルを解決します(全セルが同一の識別子を共有して
  いるため、`index`だけが1つを名指しする手段です。詳細はUnit 2)。この解決結果をドライバの
  既存のハンドル経由`/tap`でタップすると、再現性をもって`element vanished (stale handle)`で
  失敗します。ドライバ自身が持つ失効時の再試行ループ(`_STALE_MAX_ATTEMPTS`回の再解決)を
  使い切っても、なお失敗します。別のインデックスを同じ方法で解決すると、ハンドルは有効な要素
  として解決されるものの、タップは`ElementNotTappable`(`element resolved but not
  hittable`)として拒否されます。同じピッカーを開いた直後に`Cancel`を同じ経路で解決してタップ
  した場合は毎回成功しており、影響を受けるのは再利用されるグリッドのセルだけです。一方、セルの
  正確な解決済みフレーム中心への生の座標タップは、確実に選択を成立させると実測しました。
  2行×3列のグリッドに投入した6枚すべてのセルに対して行い、いずれも狙った写真を選択でき、
  画面上の選択件数も毎回一致しました。隣接セルと共有する境界上の座標(フレームそのものの中心
  ではなく)をタップすると、狙ったセルではなく隣のセルが選択されることがあります。Unit 3では
  この調査の全体と、`select_photos`が使う座標アクチュエーションを記録しています。

showcase自身のシナリオ(`select_photos.yaml`、Unit 4)は、2枚のフィクスチャを投入したライブラリ
に対してUnit 1から4までを一気通貫で検証します。この検証はApple silicon Simulator上で直接
確認済みです。実機またはIntel Simulatorでは実行していません。どちらもこの調査ではアクセスできなかった
ハードウェアです。そうしたハードウェアを持つ後の読者は、同じシナリオをそこで実行し、
`Selected: 2`になることを確認すれば、直接確かめられます。

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

### Unit 3: 座標タップによるセルのアクチュエーション

`XcuitestDriver.select_photos(indices: list[int], *, timeout: float) -> None`は、`/elements`を
呼んで要求された各インデックスを`{ id: "PXGGridLayout-Info", index: i }`に対して解決し、
解決した各セルの正確なフレーム中心(`base.frame_center`)を生の座標`/tap`でタップし、ピッカーが
まだ提示されていれば確定コントロール(後述)をタップします。新しいSwift側のエンドポイントは
要りません。DSLの`tapPoint`アクションがすでに使っているのと同じ`/tap`エンドポイントの
`point`フィールドです。

ピッカーの提示方法はUnit 4が指定するとおりにしました。`selectionLimit = 0`(無制限)、
SwiftUIから`UIViewControllerRepresentable`経由です。showcaseアプリに対し、Xcode 26.6、
Apple silicon Simulator(iOS 26.5)で実測したところ、次の結果が得られました。

| 手法 | `via` | 結果 |
|---|---|---|
| ハンドルで解決したセルへの`XCUIElement.tap()` | handle | `element vanished (stale handle)`。ドライバ自身の失効時再試行ループを使い切っても再現する |
| 別のセル(同じ識別子、異なる`index`)へのハンドル経由タップ | handle | ハンドルは有効な要素として解決されるが、タップは`ElementNotTappable`(`element resolved but not hittable`)として拒否される |
| セルの正確な解決済みフレーム中心(`base.frame_center`)への生の座標タップ | coordinate | 狙ったセルを選択できる。2行×3列のグリッドに投入した6枚すべてのセルに対して繰り返し、いずれも意図したとおりに選択でき、画面上の選択件数もタップした回数と毎回一致した |
| フレームそのものの中心ではなく、隣接セルと共有する境界上の座標へのタップ | coordinate | 狙ったセルではなく隣のセルを選択した |

同じピッカーの中で、再利用されないコントロール、`Cancel`に対して構造的に同じハンドル経由の
タップを行うと、同じ瞬間でも毎回成功します(動機を参照)。したがって、ハンドル経由タップが
失敗するのは、ピッカー一般でもXCUITestのタップ一般でもなく、グリッドのセルに固有の何かです。

そのため`select_photos`は、ハンドルではなく解決した各セルの正確なフレーム中心への生の座標で
タップし、1回の呼び出しの中でもタップのたびにフレームを再解決します。この再利用される
コレクションビューでは、同じ呼び出し内で先に処理したインデックスをタップしている間にセルの
フレームが変わらない保証はありません。`Capability.SELECT_PHOTOS`はSimulatorのホスト
アーキテクチャによる制限を一切持ちません。このドライバの静的な`CAPABILITIES`がこのトークンを
広告するあらゆる対象に対して、同じアクチュエーションが行われます。

**この調査が検証できたことと、できなかったこと。** アクチュエーションはApple silicon
Simulator上で直接検証済みです。解決した各セルの正確なフレーム中心への座標タップは、2行×3列の
グリッドに投入した6枚すべてのセルで狙った写真を選択でき、画面上の選択件数も毎回一致しました。
実機・Intel Simulator上での挙動は未検証です。どちらもこの調査では入手できませんでした。
これらのハードウェアを持つ後の読者は、`select_photos.yaml`(Unit 4)をそこで実行し、
`Selected: 2`になることを確認すれば、直接確かめられます。

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
識別子が付いていないという事実です。この解決方法は通常のハンドル経由の経路のままです。
ナビゲーションバーのボタンは再利用されるセルとは異なる静的なクローム要素であり、前述の
`Cancel`の実測が、静的なクロームがこの経路で問題なくアクチュエーションできることを
すでに示しています。

### Unit 4: Capability、他バックエンド、showcaseでの実証

`Capability.SELECT_PHOTOS = "selectPhotos"`(`bajutsu/common/drivers/base/capability.py`)は、
`XcuitestDriver.CAPABILITIES`(ユニットテスト用に`FakeDriver.CAPABILITIES`にも)が静的な
集合として宣言し、実行時の絞り込みは一切ありません。
`bajutsu/common/capability/capability_preflight.py`で`HANDLE_SYSTEM_ALERT`と同様に
ゲートします。Androidやwebのターゲットが`selectPhotos`を使うと、実行時の不透明なエラーでは
なく、プリフライトの時点で名指しのケーパビリティエラーになります。`playwright_driver.py`・
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
  ただのセレクタです。それでも採用しなかったのは、アクチュエーションの半分が理由です。DSLの
  汎用`tap`ステップはハンドル経由でアクチュエーションします。これはUnit 3でこの
  コレクションビューに対して失敗すると実測した経路そのものであり、シナリオ作者には解決した
  要素のフレーム中心への座標タップを求める手段がありません。汎用ステップは、ピッカーのセルを
  扱っているとDSLが把握していない限り、専用のアクチュエーション経路へ振り分けられません。
  これが、既存ステップの組み合わせではなく専用アクションにした理由です。
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
- [x] Unit 3 — 座標タップによるピッカーのグリッドセルのアクチュエーション(「詳細設計」を
  参照)と、確定ボタンの構造的な解決。
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
  出荷し、Apple silicon Simulatorに対しては`capabilities_for_run`で無効化しました。4つの
  Unitすべて、showcaseでの実証、そしてそれぞれのテストを実装し、`make check`が通ることを
  確認しました。
- 2026-09-25 — 前日の範囲限定を覆しました。Read Eval Print Loop (REPL) を使った対話的な
  再調査で、グリッドセルの正確な解決済みフレーム中心への座標タップが確実に選択を成立させる
  ことが判明しました。前日の調査が使ったのと同じApple silicon Simulator上で、showcaseの
  投入済みグリッド6枚すべてに対して実測しました。画面上の選択件数も毎回一致しました。前回の
  試行での座標タップは、フレームの正確な中心を狙っていませんでした。
  `XcuitestDriver.select_photos`のハンドル経由タップを、解決したフレーム中心への座標タップに
  置き換えました。あわせて、Apple silicon Simulatorに対するケーパビリティの絞り込み
  (`apple_silicon_simulator_host`、`capabilities_for_run`)を削除しました。
  `Capability.SELECT_PHOTOS`はもはやSimulatorのホストアーキテクチャで制限されません。
  `make check`が通ることを確認しました。

## 参考

- `bajutsu/common/scenario/models/actions/handle_system_alert.py` — この項目のUnit 2と
  Unit 3が部分的に踏襲し、部分的に離れているアクション形状とロケール表の前例。
- `bajutsu/common/drivers/base/_functions.py` — この項目が拡張ではなく再利用する、
  `resolve_unique`の既存の`index`処理(321行目)と`frame_center`(362行目)。
- [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)
  — この項目のUnit 3のアクチュエーションが再利用する、フレーム中心への座標タップの仕組み。
- [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) —
  この項目のセルのインデックス指定が踏襲する、序列位置によるラベル不要のボタン指定。
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) —
  この一連の流れが起こすプロンプトのうち、ピッカー自身のグリッドを除くすべてに答える
  `permissions:`の事前許可。
- [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md) —
  `SELECT_PHOTOS`を欠くバックエンドに対して`selectPhotos`ステップを事前に拒否するプリフライト。
