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

## 実機のチャネルへの到達

シミュレータでは、ホストとアプリが 1 つのループバックアドレス `127.0.0.1` を共有します。Bajutsu と iOS アプリの間のチャネルは、どれもこの共有を前提にしています。実機は自分専用のループバックを持ちます。そこで `deviceType: device` のときは、各チャネルが別の手段でホストと実機の境界を越えます。手段は次の表のとおりです。

| チャネル | 待ち受ける側 | 向き | 実機での手段 |
|---|---|---|---|
| XCUITest runner | runner（実機上） | ホスト → 実機 | usbmuxd の橋渡し |
| `nativeZ` 応答器 | アプリ（実機上） | ホスト → 実機 | usbmuxd の橋渡し |
| WebView ブリッジ | アプリ（実機上） | ホスト → 実機 | usbmuxd の橋渡し |
| ネットワークの collector | Bajutsu（ホスト上） | 実機 → ホスト | 実行ごとに取り決めるホストアドレス |

### ホストから実機へ：usbmuxd の橋渡し

usbmuxd は、Xcode や `iproxy` が実機と通信する際に使う macOS のサービスです。usbmuxd の `Connect` 要求は、ソケットを実機のループバック上のポートにつなぎます。Bajutsu は `/var/run/usbmuxd` 上で usbmuxd のプロトコルを直接話すので、ホストに追加のツールは要りません。Bajutsu はチャネルごとにホストの `127.0.0.1` で待ち受け、各接続を実機のポートへトンネルします。Android の常駐チャネルが `adb forward` で行っていることと同じです。

橋渡しは Universal Serial Bus（USB）で接続した実機を優先します。usbmuxd がネットワーク接続しか列挙しないときは、そちらを使います。usbmuxd が実機をまったく列挙しない場合もあります。よくある原因は、ケーブルが抜けていることか、実機がホストを信頼していないことです。このとき runner の起動はタイムアウトを待たずにすぐ失敗し、その理由を示します。`nativeZ` フィールドは診断用です。`nativeZ` の橋渡しを開けなかったときは、フィールドを欠けたままにして実行を続けます。

実機上の待ち受けは、実機のループバックから動かしません。runner には認証がないので、ネットワークから届かない場所に置いておく必要があります。

### 実機からホストへ：取り決めたホストアドレス

アプリは、ホスト上の collector にネットワークのやり取りを報告します。usbmuxd は実機が開く接続を運べないので、このチャネルには実機から届くホストのアドレスが要ります。実機の場合、collector はホストのすべてのインタフェースで待ち受けます。アプリは、ホストアドレスの候補ごとに 1 つずつ collector の URL を受け取ります。アプリは報告を始める前に、各候補へ認証付きの `GET /ping` を送ります。そして、ホストが並べた順で最初に応答した候補を使います。collector は、実行ごとのトークンを持たない要求には 401 を返します。そのトークンを持つのは、この実行のアプリだけです。

Bajutsu は候補を実行のたびに解決するので、ホストのアドレスが変わっても動き続けます。候補は、次のうち値のある最初の手段から決まります。

1. 環境変数 `BAJUTSU_HOST_ADDRESS`
2. ターゲットの `xcuitest.hostAddress`（[設定](configuration.md)を参照してください）
3. ホストの有効なインタフェースが持つ、経路のある IPv4 と IPv6 のアドレスすべて。この一覧には、CoreDevice トンネルの IPv6 アドレスも含まれます。Xcode 自身も、実機のテストにこのトンネルを使います。

ネットワークのやり取りを記録する実行で候補が 1 つもないときは、実機を操作する前に失敗し、エラーで 2 つの設定を示します。どの候補にも届かない実機では、やり取りが 1 件も記録されません。その場合、ネットワークのアサーションは空の記録に対して失敗します。

この経路には、実機側で注意の要る点が 2 つあります。

- iOS 14 以降、アプリはローカルネットワークのアドレスへ初めて接続する前に、**ローカルネットワークの許可**をユーザーに求めます。確認の要求はこの接続にあたります。テスト対象のアプリは、`Info.plist` に `NSLocalNetworkUsageDescription` を持つ必要があります。実機でも許可を与える必要があります。許可されるまで確認の要求には応答がなく、システムのアラートがアプリの最初の画面を覆うこともあります。Device Farm のジョブのように新しくインストールした直後は、許可がありません。この点は、まだ実機で測っていません。
- 確認の要求と各報告は、選んだ経路を**平文**の HTTP で流れます。トークンも同じです。共有のネットワークでは、`BAJUTSU_HOST_ADDRESS` を CoreDevice トンネルのアドレスに固定してください。

### 実行前のチャネルの確認

`deviceType: device` のターゲットに対して `bajutsu doctor --udid <udid> --environment-only` を実行すると、両方の経路を報告します。usbmuxd が実機を列挙しているかと、どの接続方式かを示します。アプリが受け取るホストアドレスの一覧も示します。デバイスクラウドのジョブでは、`bajutsu run` の前にこれを実行すると、ホストに欠けている経路がわかります。この 2 行は情報の表示だけで、doctor の終了コードは変えません。Device Farm のルートも同じ 2 つの経路に依存します。この 2 つは、Device Farm ではまだ誰も検証していません。

## 署名済みの実機用 runner

実機にインストールできる XCUITest runner は、その実機が信頼するチームで署名したものに限られます。そのため Bajutsu は、Simulator 用 runner と違って、実機用 runner をビルド済みで同梱できません。各ユーザーは自分の Apple Developer アカウントで、`bajutsu runner build --device` を使って実機用 runner をビルドします。ビルドが必要になるのは、後述のビルドの入力の組み合わせごとに 1 回です。以後、`deviceType: device` を指定して `xcuitest.testRunner` を指定しないターゲットは、そのビルドに解決します。ターゲットの設定には、特定のユーザーに固有の値が入りません。

### 署名ファイル

署名はターゲットではなくユーザーに属します。runner は汎用で、ターゲットの設定はリポジトリを通じて共有されます。設定に Team ID を書くと、その値がほかのすべてのユーザーに届いてしまいます。そこで署名の設定は、リポジトリの外に置くユーザーごとのファイルに書きます。Bajutsu は次の順にファイルを探し、最初に見つかったものを使います。

1. `bajutsu runner build` の `--signing <path>`（このフラグはビルドコマンドにしかありません）
2. 環境変数 `BAJUTSU_SIGNING_FILE`
3. `$XDG_CONFIG_HOME/bajutsu/signing.yaml`（未設定なら `~/.config/bajutsu/signing.yaml`）

実行時に参照するのは 2 と 3 だけです。`--signing` でビルドしたユーザーは、実行時に `BAJUTSU_SIGNING_FILE` で同じファイルを指定する必要があります。もう 1 つの方法は、`--out` で成果物をコピーし、そのパスを `testRunner` に指定することです。

```yaml
bundleIdPrefix: com.acme           # host app = com.acme.bajutsu.runner-host,
                                   # UI-test bundle = com.acme.bajutsu.runner-uitests
# bundleIds:                       # bundleIdPrefix の代わりに、2 つの識別子を自分で指定する
#   host: com.acme.e2e.runner-host
#   uitests: com.acme.e2e.runner-tests
teamId: ABCDE12345                 # 必須
signing: automatic                 # automatic（既定）または manual
# manual:                          # signing: manual のとき必須（automatic では無視）
#   identity: "Apple Development: Jane Doe (ABCDE12345)"
#   profile: "Acme Bajutsu Wildcard"         # すべての識別子を覆う 1 つのプロファイル、または
#   profiles:                                # 署名する成果物ごとのプロファイル
#     host: "Acme Bajutsu Host"
#     runner: "Acme Bajutsu Runner"          # UI テストバンドルの .xctrunner アプリ
```

このファイルには秘密情報が入りません。Team ID、証明書名、プロファイル名は、鍵とプロファイルを指す名前にすぎません。鍵とプロファイルそのものは、キーチェインと `~/Library/MobileDevice/Provisioning Profiles/` に残ります。

| キー | 意味 |
|---|---|
| `bundleIdPrefix` | runner の 2 つの識別子を導出します。このキーか `bundleIds` のどちらか一方だけを指定します。 |
| `bundleIds.host`、`bundleIds.uitests` | 2 つの識別子を直接指定します。App ID とプロファイルを別の名前ですでに持っているアカウント向けです。2 つの値は異なっている必要があります。 |
| `teamId` | 2 つの成果物に署名する Apple Developer のチームです。 |
| `signing` | `automatic` では Xcode がプロファイルを発行します。`manual` では下の証明書とプロファイルを固定します。 |
| `manual.identity` | コード署名の ID です。名前か証明書のハッシュで指定します。 |
| `manual.profile` / `manual.profiles` | すべての成果物に共通の 1 つのプロファイルか、runner のホストアプリ（`profiles.host`）と `.xctrunner` アプリ（`profiles.runner`）それぞれのプロファイルです。どちらか一方の形で指定します。期限内のプロファイルが同じ名前で 2 つ以上あるときは、プロファイルの `UUID` フィールドの値で指定します。 |

UI テストバンドルの `.xctrunner` アプリは、`<uitests の識別子>.xctrunner` という識別子を持ちます。成果物ごとのプロファイルで手動署名する場合は、この識別子のプロファイルも必要です。

runner の識別子は、テスト対象のアプリの識別子と別にしてください。アプリと同じ識別子の runner をインストールすると、アプリが上書きされます。ビルドはターゲットの情報を持たないため、Bajutsu はこの衝突を検出できません。

### ビルド

```bash
bajutsu runner build --device [--signing PATH] [--out DIR] [--force]
```

コマンドは次の 3 段階で動きます。

1. 前提条件を確認します。対象は macOS、`xcodebuild`、`xcodegen` です。手動署名では、署名 ID と指定したすべてのプロファイルも確認します。足りないものは、それぞれ対処法と一緒に表示します。
2. runner のソースを作業用ディレクトリにコピーし、そのコピーを署名ファイルに合わせて書き換えます。チェックアウトには手を加えません。wheel でインストールした環境では、wheel に同梱したソースのコピーを使います。
3. 作業用のコピーを `xcodebuild build-for-testing` でビルドし、できあがった `BajutsuRunner.xctestrun` のパスを表示します。

成果物は Bajutsu のキャッシュの `xcuitest-runner-device/<key>/Products/` に置きます。キーには、署名済みの成果物を変えるすべての入力が入ります。

- runner のソース
- 署名の設定（解決後の 2 つの識別子を含む）
- Xcode のビルドバージョン
- 手動署名では、証明書のハッシュと各プロファイルファイルのダイジェスト

このため、同じ名前のまま更新した証明書やプロファイルは、新しいキーになります。入力が変わらないビルドはキャッシュ済みの成果物を再利用し、`--force` を付けると再利用せずにビルドし直します。`--out DIR` は、`Products` ディレクトリ全体を `DIR` にもコピーします。Device Farm へのパッケージングにはこのコピーが必要です。`.xctestrun` は、自身の隣にあるテストバンドルを参照するためです。

### 実行時の動作

`deviceType: device` を指定し `testRunner` を指定しない実行は、署名ファイルを読んで同じキーを計算し、キャッシュ済みの `.xctestrun` を使います。明示した `testRunner` は、引き続きキャッシュより優先します。

実行中に runner をビルドすることはありません。署名付きのビルドは時間がかかり、キーチェインのプロンプトを出すことがあり、Apple に識別子を登録することもあるからです。キャッシュが使えない場合は、すぐに失敗して対処法を示します。

- **署名ファイルがない**：エラーに 2 つの探索場所を示します。
- **現在の入力に対応するビルドがない**：エラーに `bajutsu runner build --device` を示します。
- **キャッシュ済みのビルドのプロファイルが期限切れ**：エラーに `bajutsu runner build --device --force` を示します。

`bajutsu doctor` は、実機ターゲットが使う署名ファイルを表示します。この表示のためにビルドやキーの計算はしません。

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
- [コマンドリファレンス](cli.md#runner) — `bajutsu runner build --device`。
- [BE-0019 — XCUITest バックエンド](../../roadmaps/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)
- [BE-0236 — デバイスクラウドのプロバイダー抽象化](../../roadmaps/BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)
- [BE-0238 — iOS のデバイスクラウド実行](../../roadmaps/BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)
- [BE-0456 — 利用者ごとに署名する XCUITest ランナーの実機ビルド](../../roadmaps/BE-0456-runner-device-signing-build/BE-0456-runner-device-signing-build-ja.md)
