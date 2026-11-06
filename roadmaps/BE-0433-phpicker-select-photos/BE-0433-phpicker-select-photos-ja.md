[English](BE-0433-phpicker-select-photos.md) · **日本語**

# BE-0433 — PHPickerViewControllerからの画像選択

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0433](BE-0433-phpicker-select-photos-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0433") |
| 実装 PR | [#2008](https://github.com/bajutsu-e2e/bajutsu/pull/2008) |
| トピック | Platform support |
| 関連 | [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)、[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)、[BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`PHPickerViewController`は、システム標準の写真選択UIです。プロフィール画像や投稿への添付画像など、
ユーザーが画像を選ぶ場面で、iOSアプリが提示します。Bajutsuは、アプリ自身の画面の外にあるOS所有の
UIを、すでに操作できます。

- `handleSystemAlert`によるSpringBoardの許可プロンプト
  （[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)）
- 統合した要素ツリーによる、別プロセスのアプリ内ブラウザ
  （[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)）

一方で、写真選択ピッカーはまだ操作できません。画像を選ぶステップが存在しないため、
[シナリオ](../../docs/ja/glossary.md#シナリオのオーサリング)はこの操作の手前で止まるか、選択済みの状態を外部から注入するほかありません。

この項目は、2つの機能を追加します。

- `selectPhotos`ステップは、ピッカーのグリッドから序列位置で1枚以上の画像を選び、選択を確定します。
- `seedPhotos`プリコンディションは、Simulatorの写真ライブラリへ既知のフィクスチャ画像を投入します。
  グリッドの中身が決定的になります。

ピッカーには、第2のプロセスハンドルもツリー統合も要りません。実測の結果、ピッカーはホストアプリ
自身のプロセス内で動作していたためです。上記2件の前例とは異なります。

グリッドのセルのタップについても、実測の結果が2つあります。

- ハンドル経由のタップは失敗します。ハンドル経由のタップは、通常の要素がすべて使っている仕組みです。
- 解決したセルの正確なフレーム中心への、生の座標タップは、確実に選択を成立させます。

`selectPhotos`は、座標タップを使って出荷します。この項目の`Capability.SELECT_PHOTOS`が広告される
すべてのSimulatorと実機で、同じように動作します。

## 動機

このギャップは、機能そのものではなく、ステップの欠如です。`permissions: { photos: grant }`は
すでに存在します（BE-0276）。`photos`は、`camera`や`location`と同じ、ごく普通の`simctl privacy`の
Transparency, Consent, and Control（TCC）サービスです。この設定は、アプリ側のクエリでは観測できない
OSレベルの写真アクセスを、事前に許可します。

しかし、許可を与えることと画像を選ぶことは、別の問いです。許可を与えても、その先でピッカー自体を
操作して画像を選ぶ手段は生まれません。ピッカーのグリッドは、`permissions`が原理的にも事前に答えられる
類いの許可プロンプトを、一切起こしません。「アプリがライブラリを見てよいか」と「ユーザーがどの写真を
選んだか」の間に、OSレベルの同意ゲートは存在しません。あるのは、グリッドそのものだけです。

そのため、プロフィール画像の変更や投稿への画像添付を扱うシナリオは、ユーザーが実際にたどる操作を
再現できません。その操作とは、ピッカーを開き、画像を選び、確定することです。シナリオは、「選択済みの
状態」をあらかじめ用意してそこから検証するか、ピッカーが開く手前で検証を止めるかのどちらかになります。
前者では、検証したい操作が飛ばされます。

設計を決めた事実は、さらに2つあります。どちらも、推測ではなくshowcaseアプリ上で実測したものです。

- **ピッカーのグリッドの中身は、デフォルトでは決定的ではありません。** 新規のSimulatorの写真
  ライブラリには、シナリオが何かを追加する前から、サンプル画像（風景や花）が複数入っています。
  そのため、シナリオ自身が画像を投入しない限り、`indices: [0]`は、ライブラリがたまたま持っている
  中身を指します。`simctl`には、現状メディアを追加するラッパーも一切ありません。
  `bajutsu/common/backend_cli/simctl/_functions.py`は、`privacy`、`push`、`erase`、`boot`を
  ラップしていますが、`addmedia`はラップしていません。
- **グリッドのセルは、通常の要素と同じハンドル経由の方法ではタップできません。** 実測は、
  showcaseアプリに対して行いました。環境は、Xcode 26.6とiOS 26.5の、Apple silicon Simulatorです。
  - 全セルが同一の識別子を共有しているため、1つのセルを名指しする手段は`index`だけです（詳細は
    Unit 2）。`{ id: "PXGGridLayout-Info", index: 0 }`でセルを解決し、ドライバの既存のハンドル経由
    `/tap`でタップすると、再現性をもって`element vanished (stale handle)`で失敗します。ドライバ自身が
    持つ失効時の再試行ループ（`_STALE_MAX_ATTEMPTS`回の再解決）を使い切っても、失敗は続きます。
  - 別のインデックスを同じ方法で解決すると、ハンドルは有効な要素として解決されます。ただし、
    タップは`ElementNotTappable`（`element resolved but not hittable`）として拒否されます。
  - 同じピッカーを開いた直後に、`Cancel`を同じ経路で解決してタップした場合は、毎回成功します。
    影響を受けるのは、再利用されるグリッドのセルだけです。
  - セルの正確な解決済みフレーム中心への生の座標タップは、確実に選択を成立させます。2行×3列の
    グリッドに投入した6枚すべてのセルで確認しました。いずれも狙った写真を選択でき、画面上の
    選択件数も毎回一致しました。
  - フレームそのものの中心ではなく、隣接セルと共有する境界上の座標をタップすると、狙ったセルでは
    なく隣のセルが選択されることがあります。

  Unit 3は、この調査の全体と、`select_photos`が使う座標アクチュエーションを記録しています。

showcase自身のシナリオ（`select_photos.yaml`、Unit 4）は、2枚のフィクスチャを投入したライブラリに
対して、Unit 1から4までを一気通貫で検証します。この検証は、Apple silicon Simulator上で直接確認済み
です。実機とIntel Simulatorでは、実行していません。この調査では、どちらにもアクセスできなかった
ためです。そうしたハードウェアを持つ後の読者は、同じシナリオをそこで実行し、アプリが`Selected: 2`を
ミラーすることを確認すれば、直接確かめられます。

## 詳細設計

### Unit 1: Simulatorの写真ライブラリへの投入

`bajutsu/common/backend_cli/simctl/_functions.py`に、`addmedia_cmd(udid: str, media_path: str)
-> list[str]`を追加します。`privacy_cmd`、`push_cmd`と同じ形の引数配列ビルダーです。

```python
def addmedia_cmd(udid: str, media_path: str) -> list[str]:
    return ["xcrun", "simctl", "addmedia", validated_udid(udid), media_path]
```

1回の呼び出しにつき、パスは1つだけです。バッチ呼び出しにはしません。後述する新しい順の並びは、
1つ前の呼び出しが完了してから次を投入する**別々の**呼び出しでのみ、実測しました。1回の呼び出しに
複数のアセットをまとめて渡したとき、`simctl`がどんな相対順序を割り当てるかは、わかっていません。
バッチ呼び出しでは、2枚のフィクスチャが同じタイムスタンプを持つこともあり得ます。その場合、
グリッドの並びは、`seedPhotos`の列挙順ではなく、その呼び出しがたまたま作った順になります。

`Env.add_media`（`bajutsu/common/backend_cli/simctl/env.py`）は、与えられたパスを1つずつ順に
`addmedia_cmd`へ渡すループにします。これにより、どの呼び出し元も、実測した並びをそのまま得られます。

`privacy`、`push`とは異なり、`addmedia`はバンドル単位ではなく、デバイス全体の写真ライブラリへ
投入します。同じパスに対して再実行すると、冪等にはならず、ライブラリに重複したエントリが増えて
いきます。

`seed_photos: list[str]`（YAMLでは`seedPhotos`）を追加する先は、`Scenario`ではなく`Preconditions`
（`bajutsu/common/scenario/models/scenario/preconditions.py`）です。同じリセットをすでに司っている
`erase`、`reinstall`の隣に置きます。

ここに置くことで、後述の`_prepare_simulator`へ、新しい配線なしに届きます。`pre: Preconditions`は、
すでにそのメソッド自身の引数の1つだからです。`Scenario`側（`permissions`と同じ場所）のフィールドに
すると、`permissions`がそうしているように（`bajutsu/common/runner/pool.py:419`）、`launch_driver`と
`RunEnvironment.start`を通して配線する必要が生じます。`_prepare_simulator`は、この値を読むために
`Scenario`全体を必要とはしません。

`seed_photos`が空でない場合は、同じ`Preconditions`上で`erase: true`を必須とします。シナリオの
読み込み時に例外を送出する`model_validator`で確認します。声を上げるのであって、投入を黙って
飛ばすのではありません。

この確認がなければ、`seedPhotos`だけを設定して`erase`を設定しないシナリオは、何も投入しません。
後述のゲートが、一度も開かないためです。エラーも出ません。すると、
`selectPhotos: { indices: [0, 1] }`は、Simulatorの既存ライブラリがたまたま持っている中身を、
黙って指すことになります。これは、「検討した代替案」が退けているのと同じ非再現的な状態です。
設計ではなく、見落としによって行き着いた形になります。

パスの解決は、`dataFile`がすでに使っているものと同じです。

- パスは、シナリオファイル自身のディレクトリを起点とします。
- 共有の`contained_ref`の関所（`bajutsu/common/scenario/load_expanded.py:21-45`）が、パスがスイート
  ルートの内側に収まっているかを確認します。
- **ルートは境界であり**、パスを連結する起点ではありません。これは`dataFile`の解決方法そのままです。

`bajutsu run`自身のローダー（`bajutsu/run/cli.py`）も、`dataFile`と`use`の参照をこの同じ関数で
解決しています。別の実装は使いません。そのため、`seedPhotos`は、両方の入口で追加の作業なしに、同じ
封じ込めを引き継ぎます。

投入先は、`_prepare_simulator`
（`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py:866-939`）に
限ります。対象は、コールド起動かつ`erase`を伴う経路（`cold and pre.erase`）だけです。上記の
バリデータにより、`seed_photos`が空でなくなるのは、`erase`を伴う場合に限られます。この経路は、
すでにSimulatorの既存状態を消去する経路です。ここに乗せることで、`cold`が`False`となるウォーム再開の
リースでの、重複投入を防げます。ウォーム再開のリースでは、このブロック自体が実行されません。

ピッカーは、ライブラリを新しい順に並べます。実測では、数秒間隔で3枚のフィクスチャを、それぞれ別々の
`addmedia`呼び出しで追加しました。すると、追加順とは逆順に並びました。最後に追加したフィクスチャが、
インデックス0に来ます。投入したフィクスチャは、Simulatorにあらかじめ入っているサンプル画像よりも
前に来ます。

シナリオの`indices`は、`seedPhotos`に列挙した順序とは逆順で、フィクスチャを指すことになります。
この対応は、動作から発見させるのではなく、DSLのリファレンスに明記します。

### Unit 2: `selectPhotos` DSLアクション

```yaml
- selectPhotos:
    indices: [0, 1]
    timeout: 10
```

`bajutsu/common/scenario/models/actions/select_photos.py`に、`SelectPhotos`
（`indices: list[int]`、`timeout: float`）を定義します。バリデーションは、`HandleSystemAlert`が
自身の形を検証するのと同じ方法（`@model_validator(mode="after")`）で行います。`indices`が空でない
こと、負値を含まないこと、重複がないことを確認します。

`sel`フィールドは不要です。セルには、シナリオ作者が名指しできるアプリ側の識別子がありません。
手がかりは、グリッド上の序列位置だけです。`handleSystemAlert`も、ラベルを付けられないSpringBoardの
ボタンを、同じように序列位置で扱います。

グリッド内の全セルは、`PXGGridLayout-Info`という1つの識別子を共有しています。実測の結果、これは
セルごとの値ではありません。そのため、1つのセルを他と区別するのは、`Selector`の持つ、すでに汎用の
`index`フィールドです。フィールドは`bajutsu/common/scenario/models/selector.py`にあり、
`bajutsu/common/drivers/base/_functions.py:321`の`resolve_unique`が消費します。これは、
`handleSystemAlert`がSpringBoardのボタンに対してすでに頼っている、「複数候補のn番目」という同じ
仕組みです。

新しいセレクタフィールドは不要です。`select_photos`のドライバメソッドは、要求されたインデックスごとに、
通常の`{ id: "PXGGridLayout-Info", index: i }`セレクタを組み立てます。そして、既存の`/elements`
クエリで解決します。グリッドに専用のクエリエンドポイントは不要でした。必要だったのは、そのクエリが
すでに見つけている対象を「タップする」専用の方法です（Unit 3）。

### Unit 3: 座標タップによるセルのアクチュエーション

`XcuitestDriver.select_photos(indices: list[int], *, timeout: float) -> None`は、次の順に動きます。

1. `/elements`を呼びます。
2. 要求された各インデックスを、`{ id: "PXGGridLayout-Info", index: i }`に対して解決します。
3. 解決した各セルの正確なフレーム中心（`base.frame_center`）を、生の座標`/tap`でタップします。
4. ピッカーがまだ提示されていれば、確定コントロール（後述）をタップします。

新しいSwift側のエンドポイントは要りません。DSLの`tapPoint`アクションがすでに使っているのと同じ
`/tap`エンドポイントの、`point`フィールドを使います。

実測では、ピッカーをUnit 4が指定するとおりに提示しました。`selectionLimit = 0`（無制限）で、SwiftUI
から`UIViewControllerRepresentable`経由です。showcaseアプリに対し、Xcode 26.6、Apple silicon
Simulator（iOS 26.5）で実測したところ、次の結果が得られました。

| 手法 | `via` | 結果 |
|---|---|---|
| ハンドルで解決したセルへの`XCUIElement.tap()` | handle | `element vanished (stale handle)`。ドライバ自身の失効時再試行ループを使い切っても再現する |
| 別のセル(同じ識別子、異なる`index`)へのハンドル経由タップ | handle | ハンドルは有効な要素として解決されるが、タップは`ElementNotTappable`(`element resolved but not hittable`)として拒否される |
| セルの正確な解決済みフレーム中心(`base.frame_center`)への生の座標タップ | coordinate | 狙ったセルを選択できる。2行×3列のグリッドに投入した6枚すべてのセルに対して繰り返し、いずれも意図したとおりに選択でき、画面上の選択件数もタップした回数と毎回一致した |
| フレームそのものの中心ではなく、隣接セルと共有する境界上の座標へのタップ | coordinate | 狙ったセルではなく隣のセルを選択した |

再利用されないコントロールに対しては、構造的に同じハンドル経由のタップが、毎回成功します。そのコン
トロールとは、同じピッカーの中にある、同じ瞬間の`Cancel`です（動機を参照）。したがって、ハンドル経由
タップが失敗するのは、ピッカー一般でもXCUITestのタップ一般でもなく、グリッドのセルに固有の何かが
原因です。

そのため`select_photos`は、ハンドルではなく、解決した各セルの正確なフレーム中心への生の座標で
タップします。1回の呼び出しの中でも、タップのたびにフレームを再解決します。この再利用される
コレクションビューでは、同じ呼び出し内で先に処理したインデックスをタップしている間に、セルのフレーム
が変わらない保証はありません。

`Capability.SELECT_PHOTOS`は、Simulatorのホストアーキテクチャによる制限を一切持ちません。このドライバ
の静的な`CAPABILITIES`がこのトークンを広告するあらゆる対象に対して、同じアクチュエーションが行われ
ます。

**この調査が検証できたことと、できなかったこと。** アクチュエーションは、Apple silicon Simulator上で
直接検証済みです。解決した各セルの正確なフレーム中心への座標タップは、2行×3列のグリッドに投入した
6枚すべてのセルで、狙った写真を選択できました。画面上の選択件数も、毎回一致しました。実機と
Intel Simulator上での挙動は、未検証です。どちらも、この調査では入手できませんでした。これらの
ハードウェアを持つ後の読者は、`select_photos.yaml`（Unit 4）をそこで実行し、アプリが`Selected: 2`を
ミラーすることを確認すれば、直接確かめられます。

確定ボタンは、ラベルではなく**構造的に**解決します。確定ボタンとは、ピッカーが提示するナビゲーション
バー（`traits: ["navigationBar"]`）の内側にあるボタンのうち、識別子が`Cancel`ではないものです。
ピッカーが持つバーは、この1つだけです。バー自身のタイトル「Photos」では名指しません。このタイトルも、
`PHPickerViewController`が他のラベルと同様にローカライズするからです。

実測では、2つのコントロールについて次のことがわかりました。

- ピッカー自身の解散コントロールは、`Cancel`という安定した識別子を持ちます。
- 確定コントロールには識別子がなく、ラベルだけがあります。ラベルは「Add」ではなく「Done」でした。
  このiOSバージョンでは、チェックマークの図柄です。

バーそのものの内側で、どちらのボタンのラベルにも頼らず、除外によって解決する方法を取れば、この解決
経路のどこにもロケール別のルックアップ表は要りません。バーは自身のトレイトで見つけるのであって、
ローカライズされたタイトルでは見つけません。解決経路のどこにも、ロケールとともに変わる文字列は
登場しません。SpringBoardのアラートボタンとは違い、ここで安定しているのは、確定コントロールの
ラベルではなく、識別子が付いていないという事実です。

この解決方法は、通常のハンドル経由の経路のままです。ナビゲーションバーのボタンは、再利用される
セルとは異なる静的なクローム要素です。前述の`Cancel`の実測が、静的なクロームはこの経路で問題なく
アクチュエーションできることを、すでに示しています。

### Unit 4: Capability、他バックエンド、showcaseでの実証

`Capability.SELECT_PHOTOS = "selectPhotos"`（`bajutsu/common/drivers/base/capability.py`）は、
`XcuitestDriver.CAPABILITIES`が静的な集合として宣言します。ユニットテスト用には、
`FakeDriver.CAPABILITIES`も宣言します。実行時の絞り込みは一切ありません。

`bajutsu/common/capability/capability_preflight.py`が、`HANDLE_SYSTEM_ALERT`と同様にこの
ケーパビリティをゲートします。Androidやwebのターゲットが`selectPhotos`を使うと、実行時の不透明な
エラーではなく、プリフライトの時点で名指しのケーパビリティエラーになります。`playwright_driver.py`、
`adb_driver.py`、`xcuitest_live_driver.py`、`web_context_driver.py`は、それぞれ自身の
`select_photos`から`UnsupportedAction`を送出します。他のiOS専用アクションと同じ扱いです。

showcaseの`PermissionsView.swift`に、「Photos」セクションを追加します。対象はSwiftUIのみで、UIKit側
への展開は範囲外とします（「検討した代替案」を参照）。セクションには、2つの要素を置きます。

- 「Open Photo Picker」ボタン（`perm.openPhotoPicker`）は、シートを提示します。シートの中身は、
  `PHPickerViewController`を`UIViewControllerRepresentable`でラップしたもので、`selectionLimit = 0`
  です。無制限にするのは、確定タップの経路を常に実行するためです。
- ミラーされた`Text`（`perm.photos.value`）が、選択件数を報告します。

両方のIDは、既存の`perm`名前空間にそのまま収まるため、`idNamespaces`の変更は不要です。

`demos/showcase/scenarios/fixtures/photos/`には、判別できるフィクスチャ画像（単色）を数枚用意します。
置き場所は、`scenarios/`の兄弟ディレクトリではなく、その内側です。`seedPhotos`の参照も、他のシナリオ
参照と同じく、スイートのルートに閉じ込められるからです
（[BE-0174](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment-ja.md)）。

`demos/showcase/scenarios/select_photos.yaml`は、`preconditions: { erase: true, seedPhotos: [...]
}`でこれらを投入します。Unit 1のバリデータが、`seedPhotos`と`erase: true`の同時指定を求めます。
シナリオは、`perm.openPhotoPicker`をタップし、`selectPhotos: { indices: [0, 1] }`を実行し、
`perm.photos.value`が`2`になることを検証します。`demos/showcase/SPEC.md`§5.4には、このセクションの
既存IDと並べて、新しい2つのIDを記載します。

## 検討した代替案

- **専用アクションではなく、汎用`tap`ステップでグリッドに到達する。** セレクタの半分は、今日でも
  実際に動作します。`{ id: "PXGGridLayout-Info", index: 0 }`は、新しいフィールドを要しない、ただの
  セレクタです。それでも採用しなかったのは、アクチュエーションの半分が理由です。DSLの汎用`tap`ステップ
  は、ハンドル経由でアクチュエーションします。これは、Unit 3でこのコレクションビューに対して失敗する
  と実測した経路そのものです。シナリオ作者には、解決した要素のフレーム中心への座標タップを求める
  手段がありません。汎用ステップは、ピッカーのセルを扱っているとDSLが把握していない限り、専用の
  アクチュエーション経路へ振り分けられません。この把握が必要なことが、既存ステップの組み合わせでは
  なく専用アクションにした理由です。
- **ピッカーをSpringBoardやSafariViewServiceと同様に扱う。** 別の`XCUIApplication`ハンドル、専用の
  クエリエンドポイント、別のハンドルストアを持たせる案です。実測の結果、不要でした。ピッカーが
  提示されている間、`launchctl list`に別プロセスは一切現れません。アプリ自身の`/elements`が、すでに
  全セルを報告しています。この案では、2つ目の`SnapshotStore`、ハンドル衝突の回避、バンドル識別子の
  発見を、わざわざ構築することになります。そのすべてが、存在しないプロセス境界のためのものです。
- **確定コントロールを、`handleSystemAlert`がSpringBoardのボタンをロケール別ラベル表で解決するのと
  同じ方法で、ラベル「Done」で解決する。** このコントロールには識別子がないため、ラベルによる照合が
  最初の候補でした。採らなかったのは、構造的な除外（Unit 3）を選んだためです。このコントロールは、
  安定した識別子を持つ`Cancel`の隣にあります。`Cancel`を除外するだけで済み、ラベル文言やロケール表には
  頼りません。他言語でラベルがどう読まれても、影響を受けません。代償は小さく、将来のiOSバージョンが
  そのバー内に3つ目のボタンを識別子付きで追加した場合に、ルールの再確認が必要になることだけです。
  再設計は要りません。
- **`seedPhotos`を省き、シナリオにはSimulatorのライブラリがすでに持っている中身に頼らせる。**
  ライブラリは、デフォルトで空ではありません（サンプル画像入り）。しかし、その中身はこのプロジェクト
  のフィクスチャではありません。Xcodeやランタイムのバージョンをまたいで安定している保証もなく、
  バージョン管理下にもありません。`indices`がその中身を指すと、マシンやCI実行をまたいで再現しなく
  なります。単に不便という以上に、決定性（prime directive 2）そのものに反します。
- **同じ項目内でUIKit showcaseにも対応する。** 却下ではなく先送りです。上記のすべてのUnitを実証・
  文書化するには、SwiftUIの画面で十分です。`PHPickerViewController`は、いずれにせよUIKitの型なので、
  UIKit側に持たせても新しいドライバ側のカバレッジは増えません。増えるのは、同じ配線を複製した、
  もう1つのフィクスチャ画面だけです。

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
