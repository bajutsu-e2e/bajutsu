[English](../ios-device-cloud.md) · **日本語**

# iOS を実機とデバイスクラウドで動かす

デバイスクラウドは iOS を**実機**で動かします。その向こう側にシミュレータはありません。Bajutsu の iOS [バックエンド](glossary.md#driver-backend-actuator-platform)はシミュレータを対象とするため、この実機には届かず、デバイスクラウドへ到達するには、設定の切り替えではなく実際に新しいコードが要ります。このページでは、シミュレータ向けのバックエンドがなぜ届かないのか、それを解消する 1 つの変更（XCUITest バックエンドに実機を駆動させること）、そしてその変更が開く 2 つのルート（AWS Device Farm を経由する**バッチ**ルートと、Appium エンドポイントの先に予約されたデバイスを経由する**ライブ**ルート）を説明します。同じ実機対応の作業は、ローカル接続の iPhone や iPad を Bajutsu が駆動することも可能にします。これまではシミュレータ専用に近い状態でした。

## シミュレータ向けバックエンドが実機に届かない理由

このギャップは、オプションの欠落ではなく構造的なものです。Bajutsu の iOS バックエンドは、シミュレータを駆動する際、実機のデバイスクラウドが提供しないものに依存しています。

- **`simctl` はシミュレータだけを駆動します。** `simctl` は Apple の iOS シミュレータを制御するコマンドラインの操作面なので、物理デバイスを動かすクラウドでは対象がありません。そこに命令できるシミュレータは存在しないのです。XCUITest バックエンドは、各実行の前後でシミュレータの起動準備（消去、インストール、権限付与）を `simctl` に頼っており、そのいずれも物理デバイスは提供しません。

これらのクラウドが iOS で話せるのは、Apple 自身の **XCTest** であり、AWS Device Farm ではさらに **Appium の XCUITest ドライバー**です。どちらも、Bajutsu の [XCUITest バックエンド（BE-0019）](../../roadmaps/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)が `xcodebuild` を通じてすでに駆動している XCUITest の仕組みの上に立ちます。したがって進むべき道は、新しい iOS バックエンドを一から書くことではなく、既存の XCUITest バックエンドを実機へ一般化することです。

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

Bajutsu はこのエンドポイントを、ライブの **W3C WebDriver トランスポート**で駆動します。エンドポイントの `http(s)://` スキームがルーティングの信号です。これは共有の `device_id` の文字集合がちょうど拒否する値（URL は `/` を含みます）なので、実機の `simctl` udid と衝突することはありません。実行はこの値を `simctl` / `xcodebuild` の udid の仕組みから**迂回**させます。その仕組みは構造的に URL を運べないからです。タップしてアサートする一連の流れは、エンドツーエンドで動きます。ローカルのランナーがネイティブに駆動する意味的な操作は、そのエンドポイント越しに Appium の XCUITest `mobile:` コマンドへ写像されます。`tap`、`query`、スクリーンショット、条件待ち、入力ステップ（`type` / `delete`、`swipe` / `scroll`）、そして 2 本指の `pinch` / `rotate` ジェスチャーです。ローカルのバックエンドと同じく、曖昧なセレクターは**操作を始める前に**失敗します。セレクターの解決はグリッドではなく Bajutsu 側で行うからです。

WebDriver トランスポートが駆動できないものは、実機の経路が `simctl` に支えられた各系統を縮退させるのと同じように、前もって取り除かれます。

- **ネイティブのテキスト選択**（`select` / `copy`）には、対応する第一級の Appium XCUITest コマンドがありません。ローカルのランナーは select-all とクリップボードへのコピーをネイティブに行いますが、エンドポイントには忠実な相当物がありません。
- **`simctl` のデバイス制御と権限付与**も、他のあらゆる実機と同じく、ライブルートでは適用されません（後述の注意点を参照）。

そのためライブルートでは、preflight（[BE-0082](../../roadmaps/BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md)）はトランスポートが実際に駆動するものだけを宣言し、縮退したケーパビリティのいずれかを必要とするシナリオは、デバイス操作を始める前に明確な理由とともに**スキップ**します。実行の途中で `UnsupportedAction` として遅れて失敗することはありません。

動く例の設定は [`demos/showcase/live/showcase.live.config.yaml`](../../demos/showcase/live/showcase.live.config.yaml) にあります。ローカルの `showcase-swiftui` ターゲットを写し取りつつ、kind が `appium` の `deviceProvider` を持ち、ローカルの `appPath` / `xcuitest.testRunner` は持ちません。予約されたデバイスはすでにビルドを抱えており、ライブルートはランナーチャネルではなく WebDriver で話すからです。その `endpoint` を自分のグリッドに向けてから、共有の showcase スイートをそれに対して実行します。

```bash
bajutsu run --target showcase-swiftui-live --config demos/showcase/live/showcase.live.config.yaml
```

## 実機の注意点：再署名とケーパビリティの縮退

シミュレータではなく実機で動かすと、Bajutsu が前もって織り込む点が 2 つ変わります。いずれも特定のクラウドの性質ではなく物理ハードウェアの性質なので、XCUITest バックエンドが駆動するあらゆる実機に当てはまります。`xcuitest.deviceType: device`（Device Farm でもローカル接続のデバイスでも）で到達する場合も、上のライブ WebDriver ルートで到達する場合も同じです。

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
