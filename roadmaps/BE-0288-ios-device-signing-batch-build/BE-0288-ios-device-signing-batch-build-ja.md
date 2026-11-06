[English](BE-0288-ios-device-signing-batch-build.md) · **日本語**

# BE-0288 — バッチ経路向け iOS デバイス署名ビルド

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0288](BE-0288-ios-device-signing-batch-build-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0288") |
| 実装 PR | [#1209](https://github.com/bajutsu-e2e/bajutsu/pull/1209), [#1213](https://github.com/bajutsu-e2e/bajutsu/pull/1213) |
| トピック | デバイスクラウド実行 |
<!-- /BE-METADATA -->

## はじめに

本項目は、AWS Device Farm のバッチ経路が必要とする、署名済み iOS デバイス成果物をビルドします。[BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md) が唯一残していた欠落を埋めます。

BE-0238 は、XCUITest バックエンドに実機を駆動させました。Device Farm のサブミッターにも、iOS 向けの投入を生成させました。しかし、その投入がアップロードするビルド自体の生成には、踏み込みませんでした。未署名の Simulator 用 `.app` は、実機にインストールできません。そのため、バッチ経路には、アップロードすべきアプリ `.ipa` もテストランナー `.xctestrun` も存在しなかったのです。

本項目の貢献は、この 2 つの成果物を生成するビルド導線です。

- showcase アプリの、development 署名済みデバイス `.ipa`。
- 汎用 XCUITest ランナーの、デバイス向け `.xctestrun`。

あわせて、2 つの成果物を予約デバイスに結び付ける、Device Farm 用 iOS 設定を用意します。

署名は、環境変数で渡す Apple Developer チームでパラメータ化します。そのため、認証情報はツリーに入りません。Simulator 用のレシピと `make check` は、未署名のまま変更しません。

## 動機

バッチ経路は物理デバイスに到達し、物理デバイスは署名済みビルドを要求します。Simulator 経路は、この一手を踏みません。

- **Device Farm はデバイスビルドをインストールし、デバイスビルドは署名を要します。** Device Farm はバッチサービスです。アップロードされたアプリを予約デバイスにインストールし、そのデバイスの udid に対して `bajutsu run --backend xcuitest` を実行します。アップロードは、デバイス `.ipa` でなければなりません。既存の `swiftui-build` ターゲットが出力するのは、未署名の `iphonesimulator` 向け `.app` です。`xcodebuild archive` は、署名識別子なしにはデバイス `.ipa` を生成しません。
- **成果物は 2 つあり、署名のされ方が異なります。** アプリは `.ipa` としてインストールされ、Device Farm が自前のプロビジョニングプロファイルで再署名します。そのため、development エクスポートで十分です。一方、XCUITest ランナーは、Python テストパッケージの**内側**に、デバイスビルド済みの `.xctestrun` として同梱されます。Device Farm は、これを再署名しません。したがって、ランナーは、パッケージ化の時点で、デバイスに有効な署名をすでに帯びている必要があります。
- **署名の認証情報はリポジトリに置きません。** [`CLAUDE.md`](../../CLAUDE.md) は認証情報のハードコードを禁じているため、ビルドは Team ID を環境から読み取ります。Simulator 用のレーンと決定的な `make check` ゲートは、未署名のままとします。そうすれば、Apple Developer アカウントのない任意のマシン（Linux を含む）で、ゲートが引き続き動きます。

## 詳細設計

作業は、ビルド導線の 4 ユニットと、手動の検証ユニット 1 つに分かれます。ユニット 1〜4 は自己完結し、クラウドアカウントを必要としません。ユニット 5 は実機での実証で、意図的にゲートの外に置きます。

1. **Device Farm 用 iOS 設定。** `demos/showcase/devicefarm/showcase.devicefarm.ios.config.yaml` は、ローカルの `showcase-swiftui` [ターゲット](../../docs/ja/glossary.md#target-app-device)を写します。`xcuitest.deviceType: device` を持ち、`appPath` と `build` は持ちません。Device Farm がアップロードした `.ipa` をインストールするため、実行はインストール済みのビルドを駆動します。設定の `testRunner` は、アップロードパッケージ内のデバイスランナーを指します。この設定は、すでに出荷済みの Android 用 Device Farm 設定を写したものです。[`docs/ja/ios-device-cloud.md`](../../docs/ja/ios-device-cloud.md) に記載されたライブ経路のパターンにも従います。
2. **署名済みデバイスアプリビルド。** `demos/showcase/Makefile` の 2 つのターゲットで、アプリをビルドします。`swiftui-archive-device`（`generic/platform=iOS` に対する `xcodebuild archive`）と、`swiftui-ipa-device`（`xcodebuild -exportArchive`）です。署名は、ここでだけ有効化します。コマンドラインのビルド設定（`CODE_SIGN_STYLE=Automatic`、`DEVELOPMENT_TEAM=…`）と `-allowProvisioningUpdates` を使います。これらのターゲットの上にある Simulator 用レシピは、無変更のままです。
3. **署名済みデバイスランナービルド。** `runner-build-device` は、`generic/platform=iOS` に対して `xcodebuild build-for-testing` を実行します。`CODE_SIGNING_ALLOWED=YES` により、ランナープロジェクトにある Simulator 専用の既定値 `NO` を、コマンドラインで上書きします。`project.yml` は変更しません。専用の `derivedData` パスにより、デバイス `.xctestrun` が、コピー用の glob の下で Simulator 用のものと衝突しないようにします。
4. **認証情報の衛生。** 3 つの仕組みで、認証情報をツリーの外に置きます。
   - `ExportOptions.plist` テンプレートが `__DEVELOPMENT_TEAM__` プレースホルダーを持ち、Makefile がビルド時に差し込みます。Team ID はコミットされません。
   - `require_team` ガードは、`DEVELOPMENT_TEAM` が未設定のとき、デバイスビルドを明確なメッセージで即座に失敗させます。
   - デバイスの出力は、すべて gitignore 済みの `build/` ディレクトリに置かれます。
5. **Device Farm への投入エンドツーエンド（手動の実証）。** ランナーの `Products` ディレクトリ全体を、パッケージ化します。`.xctestrun` は、隣接する `__TESTROOT__` を通じてテストバンドルを参照するため、ファイル単体では不十分です。そのうえで、1 シナリオを Device Farm の実機でエンドツーエンドに実証します。この実証は、Apple Developer アカウントと AWS Device Farm アカウントの両方を必要とします。そのため、`make check` の外の手動手順とします。[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md) のシリアル解決の実証と同じ扱いです。

## 検討した代替案

- **`.ipa` をエクスポートせず、`.app` を手動で zip する。** デバイス `.ipa` は、`Payload/<アプリ>.app` を zip して拡張子を変えただけのものです。したがって、署名済み `.app` を手作業でパッケージ化しても、Device Farm が受け付ける成果物になります。aws-samples の iOS デモは、まさにこの手法です。それでも `archive` と `exportArchive` を採ります。この経路は、署名スタイルとエクスポート設定を、手作業のフォルダ操作ではなく、再現可能なビルド設定として捉えられるからです。Device Farm が受け付けないのは、`Payload/` ラッパー無しで素の `.app` を zip したものです。
- **Team ID やプロビジョニングプロファイルをコミットする。** 却下します。`CLAUDE.md` は認証情報のコミットを禁じています。プロファイルは、デモを 1 つのアカウントに縛ります。環境変数によるパラメータ化が、ツリーを認証情報なしに保ちます。
- **ランナーの `project.yml` で `CODE_SIGNING_ALLOWED` を切り替える。** 却下します。切り替えると、Simulator ビルドまで署名を試みます。デバイスのコマンドラインでだけ上書きすれば、`make check` と Simulator レーンを未署名のまま保てます。
- **デバイス成果物を、今すぐ CI でビルドする。** 先送りします。署名済みデバイスビルドは、CI に組み込んだ Apple Developer アカウントを必要とします。休眠中の Device Farm ワークフローも、AWS 認証情報を待っています。そのため、CI ジョブはアカウントが用意されるまで後続作業とします。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] ユニット 1 — Device Farm 用 iOS 設定（`showcase.devicefarm.ios.config.yaml`）
- [x] ユニット 2 — 署名済みデバイスアプリビルド（`swiftui-archive-device` / `swiftui-ipa-device`、`ExportOptions.plist`）
- [x] ユニット 3 — 署名済みデバイスランナービルド（`runner-build-device`）
- [x] ユニット 4 — 認証情報の衛生（`require_team` ガード、プレースホルダーテンプレート、gitignore 済み出力）
- [ ] ユニット 5 — Device Farm 投入のエンドツーエンド実証（Apple Developer と AWS のアカウントが必要。`make check` の外の手動手順）

ログ:

- 2026-07-20 — ビルド導線の 4 ユニット（1〜4）を、1 つの変更でまとめて実装しました（[#1209](https://github.com/bajutsu-e2e/bajutsu/pull/1209)）。
  - Device Farm 用 iOS 設定（`showcase.devicefarm.ios.config.yaml`）。
  - `demos/showcase/Makefile` の、署名済みデバイスアプリとランナーのターゲット（`swiftui-archive-device`、`swiftui-ipa-device`、`runner-build-device`）。
  - それらを取り巻く認証情報の衛生。`__DEVELOPMENT_TEAM__` の `ExportOptions.plist` テンプレートと、`require_team` ガードです。
  - 署名はデバイス向けのコマンドラインでだけ有効化するため、Simulator 用のレシピと `make check` は未署名のまま変わりません。
  - ユニット 5（実機での Device Farm 投入）は残します。Apple Developer と AWS のアカウントを要するため、ゲートの外の手動の実証として据え置きます。
- 2026-07-20 — ユニット 5 の手動手順書を、[AWS Device Farm](../../docs/devicefarm.md) のページと日本語ミラーに記載しました（[#1213](https://github.com/bajutsu-e2e/bajutsu/pull/1213)）。
  - 新しい節「iOS のデバイス署名の実証（手動）」が、プロジェクトとデバイスプールの作成、署名済みデバイス成果物 2 つのビルド、`--platform ios` でのシナリオ 1 件の投入までを、順に案内します。
  - ユニット 5 のチェックは、未完のままです。実機での経験的な実証には、依然として Apple Developer と AWS のアカウントが必要です。実施する担当者のための手順書は、そろいました。

## 参考

- [BE-0238 — iOS device-cloud execution](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md) — この署名軸を先送りした項目。
- [BE-0235 — AWS Device Farm submitter](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md) — これらの成果物を供給するバッチサブミッター。
- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) — ここで実機向けにビルドする汎用ランナー。
- [iOS を実機とデバイスクラウドで動かす](../../docs/ja/ios-device-cloud.md) · [AWS Device Farm](../../docs/ja/devicefarm.md)。
