[English](BE-0479-android-seed-photos.md) · **日本語**

# BE-0479 — 実行前に Android の写真ライブラリへ画像を投入する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0479](BE-0479-android-seed-photos-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0479") |
| 実装 PR | [#2382](https://github.com/bajutsu-e2e/bajutsu/pull/2382) |
| トピック | Platform support |
| 関連 | [BE-0433](../BE-0433-phpicker-select-photos/BE-0433-phpicker-select-photos-ja.md)、[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)、[BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings-ja.md)、[BE-0469](../BE-0469-runson-destination-blocks/BE-0469-runson-destination-blocks-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は、Android の[ターゲット](../../docs/ja/glossary.md#target-app-device)に
`runsOn.android.seedPhotos` を、[シナリオ](../../docs/ja/glossary.md#シナリオのオーサリング)に
`preconditions.device.android.seedPhotos` を追加します。アプリを起動する前に、Android の環境は
並べた画像を順に、デバイスの共有ストレージにある Bajutsu 専用のディレクトリへ置きます。続けて、
メディアスキャナに画像の公開を依頼します。これで、テスト対象のアプリが開く写真ピッカーに既知の画像が
並びます。iOS の Simulator で `runsOn.ios.seedPhotos` がすでに実現している状態と同じです。

## 動機

画像ピッカーを操作するシナリオには、選ぶための画像が要ります。iOS では、`runsOn.ios.seedPhotos` が
erase の経路で `simctl addmedia` を使い、Simulator の写真ライブラリに画像を投入します。そのうえで
`selectPhotos`（[BE-0433](../BE-0433-phpicker-select-photos/BE-0433-phpicker-select-photos-ja.md)）が
既知のグリッドを指します。Android のターゲットには、これに当たる機能がありません。新しいエミュレータには
画像が1枚もありません。使い回したエミュレータには、以前の実行の画像が残っています。そのため Android の
シナリオは、ピッカーを開いても安定した画像の集合を得られません。作者は Android でこのフローを
省くか、`hooks.setup` のシェルのステップで画像を手作業で送るかしています。後者は、ほかのデバイスの状態が
通る決定的な preconditions の外にあります。
[BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings-ja.md) の
設定の表もこの欠落を記録しており、Android のルートの `seedPhotos` の行は `unsupported` です。

iOS の設計は、そのままでは Android に移せません。iOS の設計は、Android にない wipe に頼っているからです。
iOS は `subject.reinstall: erase` のときにだけ投入します。投入した画像だけが写真ライブラリに入ることを、
`simctl erase` が保証するためです。Android の `erase` は、アプリのアンインストールと `pm clear` を
意味します。どちらも共有ストレージには触れないので、あるシナリオが投入した画像は次のシナリオにも
残ります。したがって Android の設計には、冪等性を別の仕組みで確保する必要があります。

本項目が入ると、2つの箇所で変化を確かめられます。1つ目は起動時のデバイスの状態です。
`runsOn.android.seedPhotos` を設定した Android のターゲットは、どの reinstall の段階でも同じ状態で
起動します。`/storage/emulated/0/Pictures/Bajutsu` には、並べた画像だけが並べた順で入っています。
2つ目は設定の表です。Android の行の `seedPhotos` が `supported` になり、ターゲットは欠落を宣言する
`runsOn.ignoredSettings` のエントリを必要としなくなります。

## 詳細設計

### config とシナリオのフィールド

`runsOn.android` に、画像のパスのリストである `seedPhotos` を加えます。`runsOn.ios.seedPhotos` と
同じく、このフィールドは条件ではなくデバイスの状態です。デバイスの照合はこのフィールドを読まず、
パスは config ファイルを基準に解決します（テンプレートのパスも同じく付け替えます）。シナリオは
`preconditions.device.android.seedPhotos` でターゲットのリストを置き換えます。このパスは
シナリオファイルを基準に解決し、iOS のフィールドと同じくスイートのルートの内側に閉じ込めます。
`preconditions.device` はこれまで iOS 専用のブロックでした。今後は `ios` の隣に `android` の
ブロックを持ちます。run が動かさないプラットフォームのブロックに効果がない点は変わりません。

### wipe に頼らない冪等性

Android の環境は `/storage/emulated/0/Pictures/Bajutsu` という1つのディレクトリを所有し、投入の
たびにこのディレクトリを空にします。投入は次の順で進みます。

1. 各画像のデバイス側の名前を、リスト内の位置と元のファイルの拡張子から決めます（`000.png` など）。英数字だけの拡張子を持たないファイルは、デバイスに触れる前に失敗します。
2. ディレクトリがなければ作り、中身を空にして、以前のシナリオの画像を残しません。ディレクトリ自体は
   残すので、手順 4 のスキャンがこのディレクトリをたどり、削除した画像の行を消します。
3. 各画像を `adb push` で送り、`touch -t` で更新時刻を付けます。更新時刻は固定で、1分ずつ昇順に
   並びます。
4. ディレクトリを対象に `MEDIA_SCANNER_SCAN_FILE` をブロードキャストします。暗黙的な
   ブロードキャストが Android 8 以降でも MediaProvider のマニフェストのレシーバに届くよう、
   `--receiver-include-background` を付けます。

メディアストア全体ではなく専用のディレクトリだけを空にするので、投入はデバイスがすでに持つ画像を
消しません。この性質は実機で重要になります。実機の画像はテストではなく、持ち主のものだからです。

固定の更新時刻は、並び順を再現可能にします。日付で並べるピッカーは、リストの最後の画像を先頭に
表示します。この順は、iOS で `simctl addmedia` が生む順と一致します。更新時刻は 2037-01-01 から
始めます。実際の撮影日時より先の日付なので、投入した画像はまとめて、デバイスがすでに持つ画像より前に
並びます。`simctl addmedia` の画像が Simulator 自身の画像より前に並ぶのと同じです。2037年は 32 ビットの
`time_t` に収まります。更新時刻を付けなければ、
`adb push` は手元の各ファイルの更新時刻を引き継ぎ、並び順がチェックアウトの状態に左右されます。
なお、メディアストアは更新時刻より画像の EXIF の撮影日時を優先します。そのため、この並び順が
成り立つのは撮影日時を持たない画像です。カメラから書き出した画像は、その画像自身の日時で並びます。

### 投入の時機と前提条件

`AndroidEnvironment.start` は、実行時パーミッションの付与のあと、`am start` の前に投入します。投入は
どの reinstall の段階でも走ります。iOS では wipe が担う冪等性を、Android ではディレクトリを空にすることが
担うので、Android のターゲットに `subject.reinstall: erase` は要りません。そのため
`check_preconditions` と、`record` / `crawl` / `repl` の検査は、erase の要件を iOS のターゲットにだけ
課します。プライマリだけが投入するという規則は、両方のプラットフォームに残ります。複数のターゲットを
動かすシナリオでは、プライマリのターゲットだけが投入します。

リストが空でも、以前の投入がディレクトリを作っていれば、その中身を空にして再スキャンします。
そのため、何も投入しないシナリオがほかのシナリオの画像を見ることはありません。一度も投入していない
デバイスでは、共有ストレージに触れません。メディアスキャンは起動のあとに非同期で終わります。
そのため、シナリオは投入した画像が現れるのを待ってから選びます。iOS の showcase のシナリオがすでに
使っている条件待ちと同じです。

### 設定の表

Android のルートの `seedPhotos` の行を、`unsupported` から `supported` に変えます。2つのプラット
フォームがこの設定を持つようになったので、メッセージに出す設定元のフィールドは
`runsOn.<platform>.seedPhotos` と表記します。

## 検討した代替案

- **iOS と同じく `subject.reinstall: erase` を必須にする案です。** 規則は両方のプラットフォームで同じ
  形になります。しかし Android の `erase` はアプリ自身のデータを消すだけで、共有ストレージには
  触れません。そのため、この要件は画像について何も保証しません。しかも、既知のピッカーが欲しいだけの
  シナリオにも、毎回の再インストールを強いることになります。
- **投入の前にメディアストアの画像をすべて削除する案です。** ピッカーには投入した画像だけが並びます。
  しかし実機では、この手順が持ち主の写真を消してしまいます。テストツールが冒してよい危険では
  ありません。
- **アプリ自身のストレージに送る案です。** ピッカーが読むのは共有のメディアストアで、アプリの
  非公開のディレクトリではありません。そのため、画像はピッカーに現れません。

## 進捗

> 作業の進行に合わせて更新します。チェックリストは「詳細設計」の MECE な作業分解（作業単位ごとに
> 1つのボックス）を写し、ログは変更内容とその日付を古い順に記録し、PR へリンクします。

- [x] `runsOn.android.seedPhotos` と `preconditions.device.android.seedPhotos` のフィールド
- [x] パスの解決と、シナリオの値によるターゲットの値の置き換え
- [x] adb のコマンドビルダーと `Env.add_media`（ディレクトリのリセット、順序どおりの送信、固定の
  更新時刻、メディアスキャン）
- [x] `AndroidEnvironment.start` での投入と、erase の要件の iOS への限定
- [x] 設定の表の Android の `seedPhotos` の行を `supported` に変更
- [x] 英語と日本語のドキュメント
- [x] エミュレータで、メディアスキャンとピッカーの並び順を確認

ログ：

- 2026-11-10 — フィールド、投入、ドキュメントを実装し、コマンドの並びに対するユニットテストを
  加えました。開発中はエミュレータを接続していなかったため、デバイス上でのメディアスキャンの動作は
  まだ確認していません。チェックリストの最後のボックスに印が付くまで、本項目は実装中のままにします。
この確認では、ディレクトリのスキャンが削除した画像の行を消すかどうかと、ピッカーの並び順を
確かめます。
- 2026-11-10 — API 34 の arm64 エミュレータ（`bajutsu-api34-arm64`、読み取り専用で起動）で確認しました。
  投入した画像の行は、スキャンから約2秒でメディアストアに現れました。`date_modified` は昇順で、
  `datetaken` は空でした。1枚だけを投入し直すと以前の3行は消え、空のリストではすべて消えました。
  システムの写真ピッカー（`PICK_IMAGES`）は、リストの最後の画像を先頭に表示しました。同じファイル名で
  投入し直したあとは、新しい画像を表示しました。ほかの API レベルはまだ確認していません。
- 2026-11-10 — レビューを受けて、更新時刻の起点を 2020年から 2037年に移しました。これで、投入した
  画像はデバイス自身の画像より前に並びます。あわせて、空のリストでの消去が `rm` に失敗したときに、
  再スキャンを飛ばさずに失敗するようにしました。同じエミュレータで確かめ直し、ピッカーは投入した画像を
  デバイスがすでに持つ画像より前に表示しました。消去は投入先のディレクトリの行だけを消しました。

## 参考

- [BE-0433](../BE-0433-phpicker-select-photos/BE-0433-phpicker-select-photos-ja.md)：`selectPhotos`
  と、それが頼る iOS の `seedPhotos` です。
- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)：
  `seedPhotos` が属するターゲットの config のグループです。
- [BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings-ja.md)：
  本項目が更新する、ルートごとの設定の表です。
- [BE-0469](../BE-0469-runson-destination-blocks/BE-0469-runson-destination-blocks-ja.md)：
  `runsOn` の宛先ブロックと、シナリオの `preconditions.device` です。
