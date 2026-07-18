[English](../ios-device-cloud.md) · **日本語**

# iOS を実機とデバイスクラウドで動かす

デバイスクラウドは iOS を**実機**で動かします。その向こう側にシミュレータはありません。Bajutsu の 2 つの iOS [バックエンド](glossary.md#driver-backend-actuator-platform)はこの実機に届かないため、デバイスクラウドへ到達するには、設定の切り替えではなく実際に新しいコードが要ります。このページでは、既存のバックエンドがなぜ届かないのか、それを解消する 1 つの変更（XCUITest バックエンドに実機を駆動させること）、そしてその変更が開く 2 つのルート（AWS Device Farm を経由する**バッチ**ルートと、Appium エンドポイントの先に予約されたデバイスを経由する**ライブ**ルート）を説明します。同じ実機対応の作業は、ローカル接続の iPhone や iPad を Bajutsu が駆動することも可能にします。これまではシミュレータ専用に近い状態でした。

## idb と simctl が実機に届かない理由

このギャップは、オプションの欠落ではなく構造的なものです。Bajutsu の 2 つの iOS バックエンドは、実機のデバイスクラウドが提供しないものにそれぞれ依存しています。

- **`simctl` はシミュレータだけを駆動します。** `simctl` は Apple の iOS シミュレータを制御するコマンドラインの操作面なので、物理デバイスを動かすクラウドでは対象がありません。そこに命令できるシミュレータは存在しないのです。
- **`idb` は Mac 上に常駐する `idb_companion` デーモンを必要とします。** idb バックエンドは、Mac ホスト上で動く companion プロセスと通信します。マネージドなクラウドの macOS ホストは限定的なシェルしか提供せず、任意の常駐デーモンの実行を許しません。そのため、バックエンドが依存する companion を起動できません。

これらのクラウドが iOS で話せるのは、Apple 自身の **XCTest** であり、AWS Device Farm ではさらに **Appium の XCUITest ドライバー**です。どちらも、Bajutsu の [XCUITest バックエンド（BE-0019）](../../roadmaps/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)が `xcodebuild` を通じてすでに駆動している XCUITest の仕組みの上に立ちます。したがって進むべき道は、3 つ目の iOS バックエンドを一から書くことではなく、既存の XCUITest バックエンドを実機へ一般化することです。

## 再利用する核：XCUITest の実機ターゲティング

XCUITest バックエンドは、既定ではシミュレータを対象にします。[ターゲット](glossary.md#target-app-device)の設定キー 1 つで、代わりに実機を選べます。

```yaml
targets:
  my-app:
    xcuitest:
      deviceType: device   # "simulator"（既定）または "device"
```

`deviceType: device` にすると、バックエンドは `xcodebuild` の `-destination` を、名前を指定したシミュレータから `platform=iOS` へ一般化します。これにより、同じ `xcodebuild test-without-building` の駆動層が実機に対して走ります。この destination が解決するデバイスは、実行時の udid から決まります。ローカル接続のデバイスの udid か、デバイスクラウドが実行に渡す udid です。

実機ではさらに、`simctl` が行うシミュレータの起動準備を省きます。この省略により、シミュレータの経路が当然としている 3 つの前提が落ちます。デバイスの消去、ローカルの `appPath` からのアプリのインストール、そして権限の事前付与です。実機でこれらのいずれかを必要とするシナリオは、黙って何もしないのではなく、はっきりと失敗します。後述の Device Farm ルートでは、代わりにクラウドがアプリをインストールします。この同じキーはローカル接続の iPhone や iPad も駆動するので、実機対応の作業は、クラウドが関わる前から単体で役立ちます。

## デバイスクラウドへの 2 つのルート

どちらのルートも、上で述べた実機版の XCUITest の核を共有します。両者が異なるのは、デバイスをどう予約し、実行がそこへどう到達するかです。

### バッチ — AWS Device Farm

AWS Device Farm は**バッチ**サービスです。デバイスをネットワーク越しに貸し出して駆動させるのではなく、予約したデバイスがすでに接続されたホスト上で、あなたのコマンドを実行します。Bajutsu は、CI 側のサブミッターを通じてここに到達します。サブミッターは、アプリとシナリオをパッケージし、予約されたデバイスの udid に対して `bajutsu run --backend xcuitest` を実行するテスト仕様とともにアップロードし、成果物を回収します。判定は依然として Bajutsu 自身の機械的に検証可能なアサーションから得られ、Device Farm 独自の合否分類からではありません。

サブミッター、それが生成するテスト仕様、再署名の注意点、そして手動の実証手順は、いずれも [AWS Device Farm](devicefarm.md) のページに書かれています。iOS の実行はその同じバッチの仕組みを再利用し、プラットフォームに応じて iOS アプリのアップロードと `xcuitest` バックエンドを選びます。

### ライブ — Appium エンドポイントのプロバイダー

クラウドが 1 台の iOS デバイスを予約し、それを Appium / WebDriver の**エンドポイント**の先に公開する場合（たとえば自前ホストのグリッド）、Bajutsu はその予約を、ライブの継ぎ目（[BE-0236](../../roadmaps/BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)）上のデバイスプロバイダーとしてモデル化します。組み込みの `appium` プロバイダーは、ローカル接続の udid の代わりに、予約されたデバイスの固定エンドポイントを実行へ渡します。

```yaml
targets:
  my-app:
    deviceProvider:
      kind: appium
      endpoint: https://grid.example.com/wd/hub   # 予約されたデバイスの Appium / WebDriver アドレス
```

このプロバイダーは、予約を、Bajutsu が `simctl` で起動もインストールもしないデバイスとして扱います。デバイスはビルドが入った状態で準備済みと報告し、解放するものは何もありません。予約はグリッドのものだからです。`endpoint` が欠けている場合は、実行がプロバイダーを解決する時点で fail-closed になり、未知のプロバイダー `kind` に対するガードと同じ挙動になります。

このライブルートは、今日の時点では**継ぎ目だけ**であり、**まだエンドツーエンドでは実行できません**。エンドポイントを Appium / WebDriver プロトコルで駆動するのは後続の作業です。XCUITest バックエンドは現状、W3C WebDriver ではなく独自のランナーチャネルで話すため、WebDriver クライアントを今日の経路の上に重ねるだけでは済みません。この障害は具体的です。プロバイダーのエンドポイントは、実行の udid として XCUITest 環境へそのまま流れ込み、そこで `_destination()` が `simctl` の udid 検証にかけます。共有の `device_id` の文字集合は URL 中の `/` を除外するため、実際の `https://` エンドポイントは今すぐ `DeviceError: invalid udid` を投げます。後続のトランスポートは、このエンドポイントを `simctl` / `xcodebuild` の udid の仕組みからすっかり迂回させる必要があります。その仕組みは構造的に URL を運べないからです。

## 実機の注意点：再署名とケーパビリティの縮退

シミュレータではなく実機で動かすと、Bajutsu が前もって織り込む点が 2 つ変わります。いずれも特定のクラウドの性質ではなく物理ハードウェアの性質なので、XCUITest バックエンドが駆動するあらゆる実機（`xcuitest.deviceType: device`）に当てはまります。Device Farm でもローカル接続のデバイスでも同じです。

- **再署名でエンタイトルメントが剥がれます。** デバイスクラウドは、アップロードされたアプリを自前のプロビジョニングプロファイルで再署名し、予約されたデバイスにインストールできるようにします。この再署名では、新しいプロファイルが持たないエンタイトルメント（多くは Push と App Groups）が落ちます。剥がれたエンタイトルメントに依存するアプリの機能は再署名後のビルドの挙動になるため、そうした機能をアサートするシナリオは、App Store 版ではなく再署名後の挙動を前提にしてください。
- **`simctl` のデバイス制御と権限付与は適用されません。** Bajutsu の iOS デバイス制御（`setLocation`、クリップボード系のステップ、`push`、`clearKeychain`、`background` / `foreground`、ステータスバーの上書き）と権限付与は、いずれも `simctl` に支えられ、届くのはシミュレータだけです。実機では XCUITest バックエンドはこれらを宣言しないため、いずれかを使うシナリオは、デバイス操作を始める前に **preflight でスキップ**され（BE-0082）、実行の途中で `simctl` エラーとして遅れて失敗する代わりに、明確な理由とともに弾かれます。XCTest ランナー自身が駆動する実機側のケーパビリティ（query、elements、スクリーンショット、タップ、2 本指ジェスチャー）は影響を受けません。

[AWS Device Farm](devicefarm.md#ios-再署名と実機のケーパビリティ) のページは、バッチの文脈で同じ 2 つの注意点を、Device Farm の再署名が落とす具体的なエンタイトルメントのキーを含めて扱います。

## 参考

- [AWS Device Farm](devicefarm.md) — バッチルート。サブミッター、テスト仕様、手動の実証。
- [ドライバー](drivers.md) — `Driver` インターフェースと、その背後のバックエンド（XCUITest を含む）。
- [設定](configuration.md) — ターゲットの `xcuitest.deviceType` と `deviceProvider` のキー。
- [BE-0019 — XCUITest バックエンド](../../roadmaps/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)
- [BE-0236 — デバイスクラウドのプロバイダー抽象化](../../roadmaps/BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)
- [BE-0238 — iOS のデバイスクラウド実行](../../roadmaps/BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)
