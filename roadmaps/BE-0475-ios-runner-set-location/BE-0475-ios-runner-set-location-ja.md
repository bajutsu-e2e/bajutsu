[English](BE-0475-ios-runner-set-location.md) · **日本語**

# BE-0475 — iOS 実機で XCUITest ランナー経由の位置情報設定を可能にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0475](BE-0475-ios-runner-set-location-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
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

### API が提供するもの

`XCUIDevice.shared.location` は `XCUILocation?` 型の読み書きできるプロパティで、Xcode 16.3 と iOS 16.4 から
使えます。`XCUILocation(location: CLLocation)` が緯度、経度、進行方位を運びます。`nil` を代入するとデバイスは
物理的な位置に戻ります。

Apple のドキュメントは、このプロパティがテストの使う代理の位置情報を与えると述べています。位置情報を模擬する
UI 自動化の経路としても案内しています。テストプランの Simulated Location 設定は、別プロセスで動くアプリには
届きません。

実機が代理の位置情報に従うかどうかは、ドキュメントに書かれていません。そこで、他の作業に着手する前に、作業単位 1 で
実機を使って確かめます。

### 経路

`setLocation` は両方のターゲットで `simctl` からランナーへ移します。実機専用の経路を別に足すのではなく、
経路を統一します。そうすれば、シナリオはどのターゲットでも同じように動きます。

| ターゲット | 変更前 | 変更後 |
|---|---|---|
| Simulator | `simctl location set` | ランナーの `POST /location`。ランナーが `unsupported` を返したときは `simctl location set` |
| 実機 | 非対応（skip） | ランナーの `POST /location` |
| ライブ WebDriver エンドポイント | 非対応 | 変更なし（対象外） |

Simulator では、既存の環境を回帰させないために `simctl` をフォールバック先として残します。iOS 16.4 より古い Simulator の
ランタイムは `unsupported` を返します。Xcode 16.3 より古いツールチェーンでビルドしたランナーも同じです。
そのとき `setLocation` は現在と同じく `simctl location set` を実行し、位置情報の寿命も後述する `simctl` の挙動になります。
実機にはフォールバック先がないため、`unsupported` はステップの失敗になります。

その他のデバイス制御（`push`、クリップボード系、`clearKeychain`、`background`、`foreground`、ステータスバーの上書き）と
権限の付与は `simctl` のままで、Simulator 専用のままです。

### ランナー（`BajutsuKit/Sources/BajutsuRunner`、`BajutsuKit/Runner/Sources`）

- **ルート。** `openapi.yaml` に、本文 `{lat: number, lon: number}` の `POST /location` を宣言します。
  実装は `APIHandler.swift` に置きます。これは、`RunnerServer` が `LegacyBackedTransport` 経由で提供する、
  生成された `APIProtocol` の実装です。他の操作と同じくメインスレッドで実行し、
  `XCUILocation(location: CLLocation(latitude:longitude:))` を作って `XCUIDevice.shared.location` に代入します。
  応答は `{"status": "ok"}` で、ランナーが呼び出しに応じられないときは `{"status": "unsupported"}` です。
  座標が欠けている、または範囲外（緯度が −90〜90 の外、経度が −180〜180 の外）の本文には 400 を返します。
  `DELETE /location` は `nil` を代入し、同じステータスを返します。ホストがシナリオの間でデバイスを初期状態に戻すために
  使うもので、シナリオのステップには公開しません。
- **プロバイダの継ぎ目。** `ElementProviding` に `setLocation(lat:lon:) -> LocationResult` と
  `clearLocation() -> LocationResult` を足します。他のルートと同様に、フェイクのプロバイダで `APIHandler` をテストできます。
  XCUITest 実装のプロバイダ（`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`）は、両方の呼び出しを
  `#available(iOS 16.4, *)` と `#if compiler(>=6.1)` で囲います。ここで効くのはコンパイラの判定で、
  `#if swift(>=6.1)` は効きません。ランナーのターゲットが Swift 5.9 の言語モードでビルドされるため、
  言語モードの判定では、どのツールチェーンでも呼び出しがコンパイルから外れるからです。
  ランナーのデプロイメントターゲットは iOS 15 のままです（`BajutsuKit/Runner/project.yml`）。
  Xcode 16.3 より古いツールチェーンでも、ランナーはビルドでき、`unsupported` を返します。
- **契約。** `openapi.yaml` はサーバーのスタブを生成する元なので、ルートと応答はまずそこで宣言します。
  `Router.swift` は旧来の実装で（BE-0381）、拡張しません。

### ホスト側のドライバと配線（`bajutsu/`）

- **ドライバ。** `XcuitestDriver` に `set_location(lat, lon)` と `clear_location()` を足します。どちらも既存のトランスポートで
  `/location` を呼び、`unsupported` を `UnsupportedAction` に変換します。メッセージには、下限が iOS 16.4 と
  Xcode 16.3 であることを含めます。
- **コントローラ。** XCUITest 環境の `controller()` は、`set_location` が稼働中のドライバを呼び、それ以外の操作は
  従来どおり `simctl` が担う `DeviceControl` を返します（`bajutsu/common/platform_lifecycle/device_control.py`）。
  Simulator では、この `set_location` がドライバの `UnsupportedAction` を受け止め、代わりに `simctl location set` を
  実行します。実機ではそれ以外の操作が capability 集合に含まれないため、プリフライトがそれらに到達することはありません。
  `controller(eff)` はドライバを引数に取らないため、`set_location` は環境の `_foreground` と同じく、呼び出し時にリースの
  ドライバを参照します。ドライバは `_lease_driver` で、デバイスグループではメンバーのドライバです。
- **capability。** `capabilities_for_run` が、実機ターゲットで `Capability.DC_SET_LOCATION` を差し引くのをやめます。

  ```python
  caps - (base.DEVICE_CONTROL_ALL - {base.Capability.DC_SET_LOCATION}) - ...
  ```

  ライブ WebDriver の分岐には手を入れません。`XcuitestLiveDriver.CAPABILITIES` が独自の正本だからです。
  `bajutsu doctor` は同じ絞り込み後の集合を読みます。そのため、`doctor` 自身を修正しなくても、実機ターゲットの
  `setLocation` をプリフライトで指摘しなくなります。

  広告する capability 集合は、デバイスを確保する前に設定から計算します。デバイスの iOS のバージョンや、ランナーを
  ビルドしたツールチェーンは、その時点では不明です。そのため iOS 16.4 と Xcode 16.3 の下限を満たさない実機でも
  `deviceControl.setLocation` を広告します。現在はプリフライトで skip されるシナリオが、実行時にステップで失敗します。

  これは `capabilities_for_run` が定める不変条件（広告した capability が `UnsupportedAction` を出さないこと）からの
  意図的な例外です。プリフライトの時点でデバイスの OS バージョンを表す設定項目がないため、この例外を受け入れます。
  作業単位 4 で、この失敗と下限を示すメッセージをテストします。

### 模擬した位置情報の寿命

`simctl location set` は Simulator 上で消去するまで残ります。`XCUIDevice.location` は、ランナーのテストプロセスが
保持する代理の値です。ランナー自体の寿命は、ターゲットによって異なります。

Simulator では、Bajutsu はランナーを複数のシナリオにまたがって warm のまま再利用します
（[BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios-ja.md)）。
`_MAX_WARM_REUSES` 回の再利用のあとやクラッシュのあとには、cold で起動し直します。実機では、ランナーは 1 回のリースの間だけ
続きます。

何もしなければ、Simulator ではあるシナリオで設定した位置情報が、続く数シナリオに持ち越されます。位置情報は次の cold 起動で
消えます。シナリオが観測する位置情報は、実行の中での順番に左右されます。これは決定性の原則（prime directive 2）に
反します。そこでホストが位置情報をシナリオに結び付けます。

- **リース開始時のリセット。** リースが始まるたびに、cold でも warm（`_resume_warm`）でも、環境が
  `DELETE /location` を呼びます。どのシナリオも、前に何が動いたかに関係なく、デバイスの物理的な位置から始まります。
  Simulator では、リセットで `simctl location clear`（`simctl.Env.clear_location`）も実行します。`nil` の代入で
  消えるのはランナーの代理の値だけで、デバイスは `simctl` が最後に設定した位置情報に戻るためです。前述の
  フォールバックで書き込んだ位置情報も、これに含まれます。そのうえで、ランナーの `unsupported` の応答は何もしない
  扱いにします。
- **クラッシュ後の再適用は不要。** シナリオの途中でランナーがクラッシュすると、実行パイプラインはそのリースを捨てます。
  新しいリースで、シナリオ全体を最初から実行し直します（`bajutsu/common/runner/pipeline.py`）。リース開始時のリセットが
  働き、シナリオ自身の `setLocation` ステップがもう一度動きます。ホストが状態を覚えておかなくても、座標は元に戻ります。

テストプロセスの終了後にアプリが何を見るかも、作業単位 1 で記録し、結果をドキュメントに書きます。現在の Bajutsu は、
シナリオの間で `simctl` の位置情報を消去していません。そのためリース開始時のリセットで挙動が変わるのは、前のシナリオが
設定した位置情報に依存していたシナリオだけです。

### 実機での権限

位置情報の権限付与は `simctl privacy` の操作で、実機では引き続き使えません。位置情報へのアクセスを許可されていない
アプリは、許可を求めるプロンプトを出します。そのプロンプトには、既存の `handleSystemAlert` ステップと
`systemAlertHandling.rules` で応答します。ドキュメントでは、新しい capability の隣にこの点を書きます。

### 作業分解（MECE）

1. **実機と Simulator でのスパイク。** 使い捨ての UI テストで `XCUIDevice.shared.location` に代入し、アプリの画面に
   `CLLocationManager` の座標を表示します。iPhone 実機と Simulator が代入に従うか、`XCUIApplication` ではなく
   `simctl launch` で起動したアプリにも代入が届くか、テストプロセスの終了後にアプリが何を見るかを記録します。

   **判定：** 実機が従わなければ、証拠を添えて本項目を `Rejected` で閉じます。Simulator だけ挙動が違う場合は、
   Simulator は `simctl` のままにして実機の経路だけを出荷します。
2. **ランナーのルート。** `openapi.yaml` と `APIHandler.swift` の `POST` と `DELETE /location`、プロバイダのメソッド、成功、
   `unsupported`、400 を扱う `BajutsuRunnerTests`。
3. **ホスト側のドライバ。** `XcuitestDriver.set_location` と `clear_location`、フェイクのトランスポートを使った単体テスト。
4. **コントローラ、寿命、capability。** Simulator のフォールバック先を持つ合成した `DeviceControl`、リース開始時（cold と
   warm、Simulator では `simctl location clear` も含む）のリセット、`capabilities_for_run` の絞り込み。テストでは次の
   3 点を示します。`simctl` へのフォールバックを使ったシナリオのあとでも、warm で再利用したランナーで次のシナリオが
   物理的な位置から始まること。`unsupported` の応答が、Simulator では `simctl` にフォールバックし、実機では
   iOS 16.4 と Xcode 16.3 の下限を示すメッセージで失敗すること。実機ターゲットで `setLocation` が動き、`push` は
   skip されたままであること。
5. **ドキュメント。** `docs/drivers.md`（capability の表）、`docs/ios-device-cloud.md`、`docs/devicefarm.md`、
   `docs/architecture.md`。それぞれに `docs/ja/` の対訳を付けます。
6. **実機での検証。** 位置情報を設定し、表示された座標を assert するショーケースのシナリオを、iPhone 実機と
   Simulator で実行します。

## 検討した代替案

| 案 | 採らなかった理由 |
|---|---|
| 実機だけ対象にし、Simulator は `simctl` のままにする | 影響範囲は小さくなり、作業単位 1 で Simulator の挙動が違うとわかった場合のフォールバック先でもあります。ただし 1 つのステップに 2 つの経路が残るので、片方のターゲットで通ってもう片方で挙動が違うシナリオが生まれえます。 |
| ホスト側の位置情報サービス（`idevicesetlocation`、`pymobiledevice3`） | ホストに依存が増え、iOS のリリースごとに変わる開発者イメージやトンネルの設定が要り、ホストがデバイスクラウド側にある Device Farm では動きません。 |
| Xcode のテストプランの Simulated Location や GPX ファイル | 位置情報がテスト実行ごとに固定で、シナリオの途中で変えられません。Apple のドキュメントも、別プロセスで動くアプリ、つまり UI 自動化のケースには届かないと述べています。 |
| BajutsuKit によるアプリ側のモック | アプリが Bajutsu のフックをリンクして従う必要があり、アプリは本物の Core Location を通らなくなります。OS がすでに提供している機能のために、app-agnostic の原則を破ることになります。 |
| `clearLocation` ステップ | `nil` の代入で実現できますが、本項目ではシナリオのスキーマを変えません。実行中に物理的な位置へ戻したいシナリオが現れた時点で足します。 |
| ライブ WebDriver 経路での Appium `mobile:` コマンド | 独自の capability 集合を持つ別のトランスポートです。ネイティブ経路で実証できたあとの別項目で扱います。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 作業単位 1：スパイク（iPhone 実機と Simulator が `XCUIDevice.location` に従うか）
- [ ] 作業単位 2：ランナーの `POST`／`DELETE /location`、プロバイダのメソッド、OpenAPI 契約、Swift のテスト
- [ ] 作業単位 3：`XcuitestDriver.set_location`／`clear_location` とテスト
- [ ] 作業単位 4：Simulator のフォールバック先を持つ `DeviceControl`、リース開始時のリセット、`capabilities_for_run` の絞り込み、テスト
- [ ] 作業単位 5：英語と日本語のドキュメント
- [ ] 作業単位 6：iPhone 実機と Simulator での検証

## 参考

- [`XCUIDevice.location`](https://developer.apple.com/documentation/xcuiautomation/xcuidevice/location)、
  [`XCUILocation`](https://developer.apple.com/documentation/xcuiautomation/xcuilocation)、
  [Simulating location in tests](https://developer.apple.com/documentation/xcode/simulating-location-in-tests)。
- [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)：実機の実行から
  `simctl` が裏付ける capability を外した項目です。
- [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control-ja.md)：デバイス制御の一部を
  プラットフォームごとに裏付けた前例です。
