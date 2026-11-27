[English](BE-0475-ios-runner-set-location.md) · **日本語**

# BE-0475 — iOS 実機で XCUITest ランナー経由の位置情報設定を可能にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0475](BE-0475-ios-runner-set-location-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0475") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities-ja.md), [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md), [BE-0458](../BE-0458-real-device-host-channels/BE-0458-real-device-host-channels-ja.md), [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`setLocation` ステップは iOS Simulator と Android エミュレータで動きますが、iPhone 実機ではこのステップを含む
シナリオが skip されます。ステップが `simctl location` を呼んでおり、`simctl` が届くのは Simulator だけだからです。

Xcode 16.3 で `XCUIDevice.location` が追加されました。UI テストがこのプロパティに値を代入して、デバイスの位置を
模擬します。ドキュメントに Simulator 限定の記載はありません。

本項目では、このプロパティをテストプロセスの内側で設定する `/location` ルートを XCUITest ランナーに追加します。
`setLocation` は、そのルートを経由します。シナリオのステップの形は変わらず、実機は
`deviceControl.setLocation` capability を得ます。

## 動機

現在の XCUITest バックエンドは、Simulator ではデバイス制御の全体と権限の付与を capability として広告します。
実機では `capabilities_for_run` がそのすべてを差し引きます（`bajutsu/common/backends.py`）。どれも `simctl` が
裏付けているためです。その結果、`setLocation` を含むシナリオはプリフライトで「`deviceControl.setLocation` が必要」
という理由で skip されます。

店舗検索、ジオフェンス、地図画面のような位置情報に依存するフローは、実機で実行できません。手元に接続した実機でも、
Device Farm で予約した実機でも同じです。Core Location の挙動が Simulator と異なる実機こそ、チームがもっとも
確かめたい対象です。

ランナーはすでにデバイス上で動いており、すべての操作を HTTP で受け取っています。したがって欠けているのは
プラットフォームの機能ではなく、ルート 1 つだけです。

本項目が出荷されたあとに確認できる差は、次の 2 点です。
1 点目は、`setLocation: {lat, lon}` のあとにアプリが表示する座標を assert するシナリオが、現在は skip される iPhone 実機で通ることです。
2 点目は、`bajutsu doctor --scenario` が、実機ターゲットでそのシナリオに `deviceControl.setLocation` が必要だと報告しなくなることです。

## 詳細設計

### Xcode が提供するもの

`XCUIDevice.shared.location` は `XCUILocation?` 型の読み書きできるプロパティで、Xcode 16.3 と iOS 16.4 から
使えます。`XCUILocation(location: CLLocation)` が緯度、経度、進行方位を運びます。`nil` を代入するとデバイスは
物理的な位置に戻ります。

Apple のドキュメントは、このプロパティがテストの使う代理の位置情報を与えると述べています。位置情報を模擬する
UI 自動化の経路としても案内しています。テストプランの Simulated Location 設定は、別プロセスで動くアプリには
届きません。

実機が代理の位置情報に従うかどうかは、ドキュメントに書かれていません。そこで、他の作業より先に、作業単位 1 で実機を使って確かめます。

### 経路

`setLocation` は両方のターゲットで `simctl` からランナーへ移します。実機専用の経路を別に足すのではなく、
経路を統一します。そうすれば、シナリオはどのターゲットでも同じように動きます。

| ターゲット | 変更前 | 変更後 |
|---|---|---|
| Simulator | `simctl location set` | ランナーの `POST /location`。ランナーが `unsupported` を返したときは `simctl location set` |
| 実機 | 非対応（skip） | ランナーの `POST /location` |
| ライブ WebDriver エンドポイント | 非対応 | 変更なし（対象外） |

Simulator では、既存の環境を回帰させないために `simctl` をフォールバック先として残します。iOS 16.4 より古い Simulator の
ランタイムは `unsupported` を返します。Xcode 16.3 より古いツールチェインでビルドしたランナーも同じです。
そのとき `setLocation` は現在と同じく `simctl location set` を実行し、位置情報の寿命も後述する `simctl` の挙動になります。
実機にはフォールバック先がないため、`unsupported` はステップの失敗になります。

その他のデバイス制御は `simctl` のままで、Simulator 専用のままです。権限の付与も同じです。
対象は次の表のとおりです。

| 操作 |
|---|
| `push` とクリップボード系 |
| `clearKeychain`、`background`、`foreground` |
| ステータスバーの上書き |

### ランナー（`BajutsuKit/Sources/BajutsuRunner`、`BajutsuKit/Runner/Sources`）

**ルート。** `openapi.yaml` に、本文 `{lat: number, lon: number}` の `POST /location` を宣言します。
実装は `APIHandler.swift` に置き、生成された `APIProtocol` を実装します。
`RunnerServer` は、その `APIProtocol` を `LegacyBackedTransport` 経由で提供します。
ハンドラは、他の操作と同じくメインスレッドで動きます。

**リクエストと応答。** ハンドラは `XCUILocation(location: CLLocation(latitude:longitude:))` を作ります。
その値を `XCUIDevice.shared.location` に代入します。応答は `{"status": "ok"}` です。
ランナーが呼び出しに応じられないときは `{"status": "unsupported"}` を返します。
代入が例外を出したときは `{"status": "error"}` を返します。この応答はランナーの不具合を表し、`simctl` には切り替えません。
座標が欠けている本文や範囲外の本文には、400 を返します。緯度は −90〜90、経度は −180〜180 です。

**リセット用のルート。** `DELETE /location` は `nil` を代入し、同じステータスを返します。
Python 側は、このルートでシナリオの間の実機を初期状態へ戻します。シナリオのステップからは使えません。

**プロバイダの継ぎ目。** `ElementProviding` に `setLocation(lat:lon:) -> LocationResult` を足します。
`clearLocation() -> LocationResult` も足します。他のルートと同様に、フェイクのプロバイダで
`APIHandler` をテストできます。

**ツールチェインの判定。** `BajutsuKit/Runner/Sources/XcuitestElementProvider.swift` が、両方の呼び出しを囲います。
囲いは `#available(iOS 16.4, *)` と `#if compiler(>=6.1)` です。ここで効くのはコンパイラの判定で、
`#if swift(>=6.1)` は効きません。ランナーのターゲットは Swift 5.9 の言語モードでビルドします。
言語モードの判定では、どのツールチェインでも呼び出しがコンパイルから外れます。

**デプロイメントターゲット。** ターゲットは iOS 15 のままです（`BajutsuKit/Runner/project.yml`）。
Xcode 16.3 より古いツールチェインでも、ランナーはビルドできます。そのランナーは `unsupported` を返します。

**契約。** `openapi.yaml` が、サーバーのスタブを生成する元です。ルートと応答は、まずそこで宣言します。
`Router.swift` は旧来の実装で（BE-0381）、本項目では拡張しません。

### Python 側のドライバと配線（`bajutsu/`）

**ドライバ。** `XcuitestDriver` に `set_location(lat, lon)` と `clear_location()` を足します。
どちらも既存のトランスポートで `/location` を呼びます。どちらも `unsupported` を `UnsupportedAction` に変換します。
メッセージには、下限が iOS 16.4 と Xcode 16.3 であることを含めます。

**コントローラ。** XCUITest 環境の `controller()` は `DeviceControl` を返します。
`set_location` は稼働中のドライバを呼びます。それ以外の操作は、従来どおり `simctl` が担います
（`bajutsu/common/platform_lifecycle/device_control.py`）。

**Simulator でのフォールバック。** Simulator では、`set_location` がドライバの `UnsupportedAction` を受け止めます。
そのうえで `simctl location set` を実行します。実機では、それ以外の操作が capability 集合に含まれません。
プリフライトがそれらに到達することはありません。

**ドライバの参照。** `set_location` は、環境の `_foreground` と同じく、呼び出し時にリースのドライバを参照します。
ドライバは常に `_lease_driver` です。位置情報はデバイス全体の値で、デバイスグループのメンバーも同じランナーを共有します。
呼び出し時に参照するのは、`controller(eff)` がドライバを引数に取らないからです。

**capability。** 実機向けの絞り込みは、`capabilities_for_route("ios-device")` にあります。
`capabilities_for_run` とサーバーのワーカー振り分けは、どちらもこのルートを読みます。
このルートが、実機ターゲットで `Capability.DC_SET_LOCATION` を差し引くのをやめます。

```python
caps - (base.DEVICE_CONTROL_ALL - {base.Capability.DC_SET_LOCATION}) - ...
```

ライブ WebDriver の分岐は、独自の正本 `XcuitestLiveDriver.CAPABILITIES` を持ち続けます。
`bajutsu doctor` は同じ絞り込み後の集合を読みます。実機ターゲットの `setLocation` をプリフライトで
指摘しなくなり、`doctor` 自身の修正は要りません。

Python 側は、広告する capability 集合を、デバイスを確保する前に設定から計算します。
デバイスの iOS のバージョンや、ランナーをビルドしたツールチェインは、その時点では不明です。
そのため、iOS 16.4 と Xcode 16.3 の下限を満たさない実機も、`deviceControl.setLocation` を広告します。
ステップは実行時に失敗します。現在は、プリフライトがそのシナリオを skip します。

`capabilities_for_run` は不変条件を定めています。広告した capability は `UnsupportedAction` を出しません。
本項目は、この不変条件に意図的な例外を設けます。プリフライトの時点では、デバイスの OS バージョンを表す設定項目がありません。
その事情が、例外を必要にします。作業単位 4 で、このエラーと下限を示すメッセージをテストします。

### 模擬した位置情報の寿命

`simctl location set` は、Simulator 上で消去するまで残ります。作業単位 1 のスパイクで、Simulator では
`XCUIDevice.location` も同じように残ることがわかりました。代入は `simctl location set` と同じデバイス全体の値を書きます。
後から書いたほうが勝ち、その値はランナーのテストプロセスが終わっても残ります。
実機でランナーの終了後に値が残るかどうかは、まだ確かめていません。

ランナー自体の寿命は、ターゲットによって異なります。Simulator では、Bajutsu はランナーを複数のシナリオにまたがって
warm のまま再利用します
（[BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios-ja.md)）。
`_MAX_WARM_REUSES` 回の再利用のあとや、シナリオ途中でのランナー終了のあとには、cold で起動し直します。
実機のランナーは、1 回のリースの間だけ続きます。

何もしなければ、Simulator では、あるシナリオで設定した位置情報が後続のシナリオに持ち越されます。
warm での再利用でも、cold での起動し直しでも同じです。シナリオが観測する位置情報は、実行の中での順番に左右されます。
決定性の原則（prime directive 2）に反するため、Bajutsu は位置情報をシナリオに結び付けます。

**Simulator のリセット。** リースが始まるたびに、cold でも warm（`_resume_warm`）でも、デバイスの準備の中で
`simctl location clear` を実行します。実行するのは、アプリが起動する前です。
そのため、アプリ起動時の位置情報の要求でも、どのシナリオもデバイス自身の位置から始まります。
スパイクでは、ランナーも同じデバイス全体の値を書くとわかったので、この 1 回で両方の経路が消えます。

**実機のリセット。** 実機には `simctl` がありません。代わりに、リースの開始時は `DELETE /location` を呼びます。
呼ぶのは、ランナーがアプリを起動した後です。`unsupported` の応答は、ランナーが位置情報を設定していないことを表します。
それ以外の応答では、ランナーを破棄してリースを失敗させます。

**アタッチ。** アタッチはデバイス全体に触れない約束なので、`repl --attach` の実行はリセットを行いません。

**再適用は不要。** シナリオの途中でランナーが終了すると、実行パイプラインはそのリースを捨てます。
新しいリースで、シナリオ全体を最初から実行し直します（`bajutsu/common/runner/pipeline.py`）。
リース開始時のリセットが働き、シナリオ自身の `setLocation` ステップがもう一度動きます。
Bajutsu が状態を覚えておかなくても、座標は元に戻ります。

テストプロセスの終了後にアプリが何を見るかも、作業単位 1 で記録し、結果をドキュメントに書きます。
現在の Bajutsu は、シナリオの間で `simctl` の位置情報を消去していません。
そのため、リース開始時のリセットで挙動が変わるのは、前のシナリオが設定した位置情報に依存していたシナリオだけです。

### 実機での権限

位置情報の権限付与は `simctl privacy` の操作で、実機では引き続き使えません。
位置情報へのアクセスを許可されていないアプリは、許可を求めるプロンプトを出します。
そのプロンプトには、既存の `handleSystemAlert` ステップと `systemAlertHandling.rules` で応答します。
ドキュメントでは、新しい capability の隣にこの点を書きます。

### 作業分解（MECE）

**作業単位 1：実機と Simulator でのスパイク。** 使い捨ての UI テストで `XCUIDevice.shared.location` に代入します。
アプリの画面には、`CLLocationManager` の座標を表示します。スパイクは次の 3 点を記録します。

| 記録する事実 |
|---|
| iPhone 実機と Simulator が代入に従うか |
| `XCUIApplication` ではなく `simctl launch` で起動したアプリにも代入が届くか |
| テストプロセスの終了後にアプリが何を見るか |

**判定：** 実機が代入に従わなければ、証拠を添えて本項目を `Rejected` で閉じます。
Simulator だけ挙動が違う場合は、Simulator を `simctl` のままにして、実機の経路を出荷します。

**作業単位 2：ランナーのルート。** `openapi.yaml` と `APIHandler.swift` に `POST` と `DELETE /location` を足します。
プロバイダのメソッドも足します。`BajutsuRunnerTests` が、成功、`unsupported`、400 を扱います。

**作業単位 3：Python 側のドライバ。** `XcuitestDriver.set_location` と `clear_location` を足します。
フェイクのトランスポートを使ったユニットテストを付けます。

**作業単位 4：コントローラ、寿命、capability。** Simulator のフォールバックを持つ合成した `DeviceControl` を作ります。
リース開始時のリセットとして、Simulator では cold と warm の両方で `simctl location clear` を実行します。
実機では `DELETE /location` を呼びます。
最後に、`capabilities_for_run` の絞り込みを入れます。テストは、次の 3 点を示します。

| テストが示す結果 |
|---|
| `simctl` へのフォールバックを使ったシナリオのあとでも、warm のランナーが次のシナリオを物理的な位置から始める |
| `unsupported` の応答は、Simulator では `simctl` にフォールバックし、実機ではステップを失敗させる。メッセージは iOS 16.4 と Xcode 16.3 の下限を示す |
| 実機ターゲットで `setLocation` が動き、`push` は skip されたままである |

**作業単位 5：ドキュメント。** `docs/drivers.md`（capability の表）を更新します。
`docs/ios-device-cloud.md`、`docs/devicefarm.md`、`docs/architecture.md` も更新します。
それぞれに `docs/ja/` の対訳を付けます。

**作業単位 6：実機での検証。** 位置情報を設定し、表示された座標を assert するショーケースのシナリオを足します。
iPhone 実機と Simulator で実行します。

## 検討した代替案

| 案 | 採らなかった理由 |
|---|---|
| 実機だけ対象にし、Simulator は `simctl` のままにする | 影響範囲は小さくなり、作業単位 1 で Simulator の挙動が違うとわかった場合のフォールバック先でもあります。ただし 1 つのステップに 2 つの経路が残るので、片方のターゲットで通ってもう片方で挙動が違うシナリオが生まれえます。 |
| ホスト側の位置情報サービス（`idevicesetlocation`、`pymobiledevice3`） | ホストに依存が増え、iOS のリリースごとに変わる開発者イメージやトンネルの設定が要り、ホストがデバイスクラウド側にある Device Farm では動きません。 |
| Xcode のテストプランの Simulated Location や Global Positioning System（GPS）Exchange Format のファイル | 位置情報がテスト実行ごとに固定で、シナリオの途中で変えられません。Apple のドキュメントも、別プロセスで動くアプリ、つまり UI 自動化のケースには届かないと述べています。 |
| BajutsuKit によるアプリ側のモック | アプリが Bajutsu のフックをリンクして従う必要があり、アプリは本物の Core Location を通らなくなります。OS がすでに提供している機能のために、app-agnostic の原則を破ることになります。 |
| `clearLocation` ステップ | `nil` の代入で実現できますが、本項目ではシナリオのスキーマを変えません。実行中に物理的な位置へ戻したいシナリオが現れた時点で足します。 |
| ライブ WebDriver 経路での Appium `mobile:` コマンド | 独自の capability 集合を持つ別のトランスポートです。ネイティブ経路で実証できたあとの別項目で扱います。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 作業単位 1：スパイク（iPhone 実機と Simulator が `XCUIDevice.location` に従うか）
- [x] 作業単位 2：ランナーの `POST`／`DELETE /location`、プロバイダのメソッド、OpenAPI 契約、Swift のテスト
- [x] 作業単位 3：`XcuitestDriver.set_location`／`clear_location` とテスト
- [x] 作業単位 4：Simulator のフォールバック先を持つ `DeviceControl`、リース開始時のリセット、`capabilities_for_run` の絞り込み、テスト
- [ ] 作業単位 5：英語と日本語のドキュメント
- [ ] 作業単位 6：iPhone 実機と Simulator での検証

ログは次のとおりです。

2026-11-28：作業単位 2〜4 をまとめて実装しました。作業単位 1 のスパイクのうち Simulator の側を、
Xcode 27.0 と iOS 27.0 の Simulator で実行し、次の 4 点を確かめました。

| スパイクの結果 |
|---|
| `XCUIApplication` で起動したアプリにも、`simctl launch` で起動したアプリにも、代入した座標が届く |
| テストプロセスが終わった後にアプリを起動し直しても、最後に代入した座標が読める |
| `XCUIDevice.location` と `simctl location set` は、デバイス全体で 1 つの値を書き、後から書いたほうが勝つ |
| `nil` を代入すると、`simctl location clear` と同じく、模擬した位置情報がなくなる。以前に `simctl` で設定した値には戻らない |

実機の側はまだ実行していないため、作業単位 1 は未完了のままです。
リース開始時のリセットはスパイクの結果に合わせました。Simulator では、アプリ起動前の 1 回の `simctl location clear` で両方の経路が消えます。
実機は `DELETE /location` でリセットします。
`/location` ルートより前にビルドしたランナーは、404 のステータスを返します。
ドライバは、ランナーの再ビルドを求めるメッセージでリースを失敗させます。

## 参考

- [`XCUIDevice.location`](https://developer.apple.com/documentation/xcuiautomation/xcuidevice/location)、
  [`XCUILocation`](https://developer.apple.com/documentation/xcuiautomation/xcuilocation)、
  [Simulating location in tests](https://developer.apple.com/documentation/xcode/simulating-location-in-tests)。
- [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)：実機の実行から
  `simctl` が裏付ける capability を外した項目
- [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control-ja.md)：デバイス制御の一部を
  プラットフォームごとに裏付けた前例
