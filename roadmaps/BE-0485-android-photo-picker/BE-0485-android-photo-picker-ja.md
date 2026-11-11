[English](BE-0485-android-photo-picker.md) · **日本語**

# BE-0485 — selectPhotos で Android の写真ピッカーから画像を選ぶ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0485](BE-0485-android-photo-picker-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0485") |
| 実装 PR | [#2411](https://github.com/bajutsu-e2e/bajutsu/pull/2411) |
| トピック | Platform support |
| 関連 | [BE-0433](../BE-0433-phpicker-select-photos/BE-0433-phpicker-select-photos-ja.md)、[BE-0479](../BE-0479-android-seed-photos/BE-0479-android-seed-photos-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、adb driver で `selectPhotos` ステップを実行できるようにします。`selectPhotos` は、テスト対象の
アプリが開いたシステムの写真ピッカーから、位置を指定して画像を選ぶステップです。Android 13 以降では、driver
がピッカーのグリッドを待ち、指定されたセルをタップして、選択を確定します。どのコントロールも、ローカライズ
された文字列ではなく resource id で探します。このステップを扱う showcase の 2 つのシナリオは、iOS と Android
で同じ内容のまま通るようになります。

## 動機

画像のアップロードを試す[シナリオ](../../docs/ja/glossary.md#scenario-authoring)には、画像を選ぶ操作が
必要です。iOS では `selectPhotos`
（[BE-0433](../BE-0433-phpicker-select-photos/BE-0433-phpicker-select-photos-ja.md)）が
`PHPickerViewController` のグリッドからセルを位置で選びます。Android の adb driver は同じステップに対して
`UnsupportedAction` を送出し、preflight チェックもこのステップを含むシナリオを拒否します。そのため作成者は、
Android ではこのフローを省くか、ピッカーのセルを生の座標でタップするしかありません。座標を使うと、シナリオが
特定の画面サイズに縛られます。

[BE-0479](../BE-0479-android-seed-photos/BE-0479-android-seed-photos-ja.md) は、この欠落の半分を埋めました。
Android の実行でも、アプリの起動前に既知の画像を既知の順序で用意できます。残る半分はピッカーの操作です。
システムの写真ピッカー（`MediaStore.ACTION_PICK_IMAGES`）は、アプリとは別のプロセスで動きます。グリッドの
セルには作成者が名前で指定できるラベルがありません。確定ボタンの表示は英語で「Add (2)」となり、ロケールごとに
変わります。

この項目が入ると、変化を 2 か所で確かめられます。`AdbDriver.CAPABILITIES` に `selectPhotos` が含まれ、
preflight チェックが Android のターゲットでこのステップを受け付けます。showcase のシナリオ
`select_photos.yaml` と `select_photo_single.yaml` が、iOS で通るのと同じステップのまま、showcase アプリの
Compose 版と Views 版で通ります。

## 詳細設計

### ピッカーの構造

API 34 の `google_apis` エミュレータでピッカーを調べました。継続的インテグレーション（CI）の Android の
エンドツーエンド（E2E）レーンが起動するものと同じイメージです。driver が頼る要素を次の表に示します。どの
resource id も、ピッカー自身のパッケージに属します。パッケージ名は、Google のビルドでは
`com.google.android.providers.media.module`、Android Open Source Project（AOSP）のビルドでは
`com.android.providers.media.module` です。

| 要素 | resource id | driver が読むもの |
|---|---|---|
| グリッドのセルのサムネイル | `icon_thumbnail` | 境界の矩形と、選択後の `selected="true"` |
| セルのチェックマーク | `icon_check` | ピッカーが複数枚の選択を許すときだけ現れます |
| 確定ボタン | `button_add` | 複数選択のピッカーで、1 枚目を選んだあとに現れます |

単一選択のピッカーには、チェックマークも確定ボタンもありません。最初のタップで 1 枚を返して閉じます。iOS
のピッカーで選択上限を 1 にしたときと同じ動きです。どちらのピッカーも、新しい画像を先頭に並べます。その
ため BE-0479 で用意した画像では、`seedPhotos` に最後に挙げた画像が位置 0 に来ます。iOS シミュレータで
`simctl addmedia` を使ったあとと同じ順序です。

### driver の動作

`AdbDriver.select_photos(indices, timeout)` は、次の順に動きます。

1. API 33 未満のデバイスでは `UnsupportedAction` で断ります。写真ピッカーは Android 13 以降に組み込まれ
   ています。Android 11 と 12 では Google のシステムアップデートで届く場合だけ使えます。それより前のバー
   ジョンでは、Google Play 開発者サービスのバックポート版かドキュメントピッカー（`ACTION_OPEN_DOCUMENT`）
   に切り替わります。いずれもまだ調べていないので、この項目の対象外です。
2. グリッドのセル数が、指定された最大の位置を超えるまで、最長 `timeout` 秒待ちます。セルとは、ピッカーの
   2 つのパッケージに属する `icon_thumbnail` のノードで、上の行から順に、同じ行の中では左から右へ並べた
   ものです。待っても足りなければ、ステップは `ElementNotFound` で失敗し、グリッドに見えていたセル数を示
   します。この待機は、アプリの起動後に終わる BE-0479 のメディアスキャンも吸収します。グリッドに
   `icon_check` のノードがあれば複数選択のピッカー、なければ単一選択のピッカーと判断します。単一選択のピ
   ッカーに 2 つ以上の位置を指定すると、タップの前にここで失敗します。
3. ピッカー自身のウィンドウが入力フォーカスを持つまで待ちます（`dumpsys window` の `mCurrentFocus`）。
   ピッカーのウィンドウはアタッチされた時点でツリーに現れますが、入力はまだ下のアプリに届くことがあります。
   そのときのタップは、セルの下にあるアプリのビューに当たります。
4. 指定された各セルを、境界の矩形の中心でタップします。複数選択のピッカーでは、タップのたびにそのセルが
   `selected="true"` になるまで待ちます。次のタップが前のタップと競合しないようにするためです。
5. 選択を確定します。複数選択のピッカーでは `button_add` を待ってタップします。単一選択のピッカーでは、
   手順 4 のタップで確定済みです。
6. ピッカーのパッケージのノードがツリーから消えるまで待ちます。次のステップがアプリ自身の画面を読めるように
   するためです。

どの待機も、ステップ自身の `timeout` を上限とする条件待ちです。推測で進めることはありません。グリッドを超える
位置を指定すると失敗し、確定ボタンのない複数選択のピッカーも失敗します。グリッドを超える位置の場合は、グリッドに見えていたセル数をメッセージに示します。

driver は、常駐チャネルまたは `uiautomator dump` が返したツリーを、driver 自身の要素解析にかける前の形で
読みます。解析後の要素はノードのパッケージを持たないためです。パッケージで絞ることで、アプリ自身が
`icon_thumbnail` という名前のビューを持っていても、グリッドに混ざりません。照合するのは resource id の
ローカル名だけなので、ロケールごとの対応表は要りません。

ライブラリの画像が 1 画面に収まらないと、セルの一部は見えている範囲の外に出ます。この項目は見えているセル
だけを対象にし、グリッドをスクロールしません。数枚の画像を用意したライブラリなら、最初の画面に収まります。

### capability

`AdbDriver.CAPABILITIES` に `selectPhotos` を加えます。この driver の `multiTouch` と同じく、capability は
静的なままにします。preflight チェックはデバイスなしで capability の集合を読むので、API レベルの条件は
ステップの実行時に driver が確かめます。

### showcase とシナリオ

showcase アプリの Compose 版と Views 版の Permissions 画面に、Photos の区画を加えます。区画には 2 つの
ボタンと 1 つの値を置きます。

- `perm.openPhotoPicker` は、複数選択のピッカー（`PickMultipleVisualMedia`）を開きます。
- `perm.openPhotoPickerSingle` は、単一選択のピッカー（`PickVisualMedia`）を開きます。
- `perm.photos.value` は、返ってきた画像の枚数を示します。

どちらのピッカーも画像だけを求めます。ID と動作は、SwiftUI 版の Photos の区画と揃えます。`android:id` には
ドットを使えないので、Views 版では ID をアンダースコアでつづります。

2 つのシナリオは、次の 3 つの変更で両プラットフォームに対応します。

- iOS のブロックの隣に `preconditions.device.android.seedPhotos` を置き、同じ fixture を用意します。
- グリッドを待つステップで、両プラットフォームのセルの ID `[PXGGridLayout-Info, icon_thumbnail]` を
  並べます。
- ID のセレクタごとに、ドットの形とアンダースコアの形を並べます。

Android の E2E レーンでは、両方のシナリオを Compose 版と Views 版の両方で実行します。

### ドキュメント

`docs/platform-parity.md` と `docs/drivers.md` の capability の表で、`selectPhotos` の行を Android でも対応に
変えます。`docs/scenarios.md` のステップの説明では、両方のピッカーを挙げます。どのページも英語版と日本語版を
更新します。

## 検討した代替案

- **ドキュメントピッカー（`ACTION_OPEN_DOCUMENT`）にも対応する。** システムの写真ピッカーがないデバイス
  でも動くようになります。しかしドキュメントピッカーは DocumentsUI が持つ別の構造で、ID も異なります。減
  っていくデバイスのために、driver が 2 つの構造を保守し続けることになります。
- **確定ボタンを表示文字列で照合する。** 文字列はロケールと枚数（「Add (2)」）で変わるので、ロケールごとの
  対応表が要ります。resource id を使えば、その対応表は要りません。
- **グリッドをスクロールして、最初の画面の外のセルまで届かせる。** スクロールすると、セルが何番目になる
  かがグリッドの移動量と再利用された行に左右されます。用意したライブラリは 1 画面に収まるので、範囲外の
  指定ははっきり失敗させます。

## 進捗

> 作業の進行に合わせて更新します。チェックリストは「詳細設計」の MECE な作業分解（作業単位ごとに
> 1つのボックス）を写し、ログは変更内容とその日付を古い順に記録し、PR へリンクします。

- [x] API 34 のエミュレータでピッカーの構造を調べる。
- [x] `AdbDriver.select_photos`：API の確認、グリッドの待機、フォーカスの待機、タップ、確定。
- [x] `AdbDriver.CAPABILITIES` への `selectPhotos` の追加。
- [x] 記録したピッカーの dump を使うユニットテスト。
- [x] Compose 版と Views 版への Photos の区画の追加。
- [x] 両プラットフォーム対応のシナリオと、Android の E2E レーンへの追加。
- [x] 英語と日本語のドキュメント。
- [x] エミュレータで両方のシナリオを通す。

ログ：

- 2026-11-11 — API 34 の `google_apis` arm64 エミュレータでピッカーを調べました。API 33 と API 35 の
  システムイメージはインストールされていなかったので、それらのレベルはまだ確かめていません。
- 2026-11-11 — driver、capability、showcase の 2 つのアプリ、両プラットフォーム対応のシナリオ、
  ドキュメントを実装し、記録した dump を使うユニットテストを加えました。同じエミュレータで、Views 版の
  実行が 1 回、ツリーにピッカーが見えている状態でセルをタップしました。そのタップは、下にあるアプリの位置情報の
  ボタンに当たりました。そこで driver は、先にピッカーのウィンドウが入力フォーカスを持つまで待つように
  しました。この変更のあと、両方のシナリオが Compose 版と Views 版で通り、Views 版の複数選択は 3 回続けて
  通りました。

## 参考

- [BE-0433](../BE-0433-phpicker-select-photos/BE-0433-phpicker-select-photos-ja.md)：`selectPhotos`
  ステップと、その iOS の driver。
- [BE-0479](../BE-0479-android-seed-photos/BE-0479-android-seed-photos-ja.md)：このピッカーが一覧に出す、
  Android の写真ライブラリの用意。
- [Photo picker](https://developer.android.com/training/data-storage/shared/photopicker)：
  `ACTION_PICK_IMAGES` とその contract についての Android のドキュメント。
