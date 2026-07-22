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

本項目は、AWS Device Farm のバッチ経路が必要とする署名済み iOS デバイス成果物をビルドし、
[BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md) が唯一
残していた欠落を埋めます。BE-0238 は XCUITest バックエンドに実機を駆動させ、Device Farm の
サブミッターに iOS 投入を生成させましたが、その投入がアップロードするビルド自体の生成には
踏み込みませんでした。未署名の Simulator 用 `.app` は実機にインストールできないため、バッチ
経路にはアップロードすべきアプリ `.ipa` もテストランナー `.xctestrun` も存在しなかったのです。
本項目の貢献は、その両方を生成するビルド導線です。すなわち、showcase アプリの development
署名済みデバイス `.ipa` と、汎用 XCUITest ランナーのデバイス向け `.xctestrun`、そしてそれらを
予約デバイスに結び付ける Device Farm 用 iOS 設定を用意します。署名は環境変数で渡す Apple
Developer チームでパラメータ化するため、認証情報はツリーに入りません。また Simulator 用の
レシピと `make check` は未署名のまま変更しません。

## 動機

バッチ経路は物理デバイスに到達し、物理デバイスは署名済みビルドを要求します。これは Simulator
経路が決して踏まない一手です。

- **Device Farm はデバイスビルドをインストールし、デバイスビルドは署名を要します。** Device
  Farm はバッチサービスであり、アップロードされたアプリを予約デバイスにインストールし、その
  udid に対して `bajutsu run --backend xcuitest` を実行します。この投入はデバイス `.ipa` で
  なければならず、`xcodebuild archive` は署名識別子なしに `.ipa` を生成しません。既存の
  `swiftui-build` が出力する未署名の `iphonesimulator` 向け `.app` とは異なります。
- **成果物は 2 つあり、署名のされ方が異なります。** アプリは `.ipa` としてインストールされ、
  Device Farm が自前のプロビジョニングプロファイルで再署名するため、development エクスポートで
  十分です。一方 XCUITest ランナーは Python テストパッケージの**内側**にデバイスビルド済みの
  `.xctestrun` として同梱され、Device Farm はこれを再署名しません。したがってランナーは、
  パッケージ化の時点でデバイスに有効な署名を既に帯びている必要があります。
- **署名の認証情報はリポジトリに置きません。** [`CLAUDE.md`](../../CLAUDE.md) は認証情報の
  ハードコードを禁じているため、ビルドは Team ID を環境から読み取ります。Simulator 用のレーンと
  決定的な `make check` ゲートは未署名のままとし、Apple Developer アカウントのない任意のマシン
  （Linux を含む）でゲートが引き続き動くようにします。

## 詳細設計

作業はビルド導線の 4 ユニットと、手動の検証ユニット 1 つに分かれます。ユニット 1〜4 は自己
完結し、クラウドアカウントを必要としません。ユニット 5 は実機での実証で、意図的にゲートの外に
置きます。

1. **Device Farm 用 iOS 設定。** `demos/showcase/devicefarm/showcase.devicefarm.ios.config.yaml`
   は、ローカルの `showcase-swiftui` [ターゲット](../../docs/ja/glossary.md#target-app-device)を写しつつ `xcuitest.deviceType: device` を持ち、
   `appPath` / `build` を持ちません。Device Farm がアップロードした `.ipa` をインストールする
   ため、実行はインストール済みビルドを駆動します。`testRunner` はアップロードパッケージ内の
   デバイスランナーを指します。これは既に出荷済みの Android 用 Device Farm 設定と、
   [`docs/ja/ios-device-cloud.md`](../../docs/ja/ios-device-cloud.md) に記載されたライブ経路の
   パターンを写したものです。
2. **署名済みデバイスアプリビルド。** `demos/showcase/Makefile` の 2 ターゲット、
   `swiftui-archive-device`（`generic/platform=iOS` に対する `xcodebuild archive`）と
   `swiftui-ipa-device`（`xcodebuild -exportArchive`）です。署名はここでのみ、コマンドライン
   ビルド設定（`CODE_SIGN_STYLE=Automatic`、`DEVELOPMENT_TEAM=…`）と
   `-allowProvisioningUpdates` で有効化するため、その上にある Simulator 用レシピは無変更のまま
   です。
3. **署名済みデバイスランナービルド。** `runner-build-device` は `generic/platform=iOS` に対して
   `xcodebuild build-for-testing` を実行し、`CODE_SIGNING_ALLOWED=YES` でランナープロジェクトの
   Simulator 専用の既定値 `NO` を、`project.yml` ではなくコマンドラインで上書きします。専用の
   `derivedData` パスにより、デバイス `.xctestrun` がコピー用の glob 下で Simulator 用のものと
   衝突しないようにします。
4. **認証情報の衛生。** `ExportOptions.plist` テンプレートは `__DEVELOPMENT_TEAM__` プレース
   ホルダーを持ち、Makefile がビルド時に差し込むため Team ID はコミットされません。
   `require_team` ガードは `DEVELOPMENT_TEAM` が未設定のときデバイスビルドを明確なメッセージで
   即座に失敗させます。そしてデバイスの出力はすべて gitignore 済みの `build/` ディレクトリに
   置かれます。
5. **Device Farm への投入エンドツーエンド（手動の実証）。** ランナーの `Products` ディレクトリ
   全体をパッケージ化します。`.xctestrun` は隣接する `__TESTROOT__` を通じてテストバンドルを
   参照するため、ファイル単体では不十分です。そのうえで 1 シナリオを Device Farm の実機で
   エンドツーエンドに実証します。これは Apple Developer アカウントと AWS Device Farm アカウントの
   両方を必要とするため、`make check` の外の手動手順とし、
   [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md) の
   シリアル解決の実証を写します。

## 検討した代替案

- **`.ipa` をエクスポートせず `.app` を手動で zip する。** デバイス `.ipa` は
  `Payload/<アプリ>.app` を zip して拡張子を変えただけのものなので、署名済み `.app` を手作業で
  パッケージ化しても Device Farm が受け付ける成果物になります（aws-samples の iOS デモはまさに
  この手法です）。それでも `archive` + `exportArchive` を採ります。署名スタイルとエクスポート
  設定を、手作業のフォルダ操作ではなく再現可能なビルド設定として捉えられるからです。（Device
  Farm が受け付けないのは、`Payload/` ラッパー無しで素の `.app` を zip したものです。）
- **Team ID やプロビジョニングプロファイルをコミットする。** 却下します。`CLAUDE.md` は
  認証情報のコミットを禁じ、プロファイルはデモを 1 つのアカウントに縛ります。環境変数による
  パラメータ化がツリーを認証情報なしに保ちます。
- **ランナーの `project.yml` で `CODE_SIGNING_ALLOWED` を切り替える。** 却下します。それでは
  Simulator ビルドまで署名を試みます。デバイスのコマンドラインでのみ上書きすることで、
  `make check` と Simulator レーンを未署名のまま保ちます。
- **デバイス成果物を今すぐ CI でビルドする。** 先送りします。署名済みデバイスビルドは、
  休眠中の Device Farm ワークフローが AWS 認証情報を待つのと同様に、CI に組み込んだ Apple
  Developer アカウントを必要とするため、CI ジョブはアカウントが用意されるまで後続作業とします。

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

- 2026-07-20 — ビルド導線の 4 ユニット（1〜4）を 1 つの変更でまとめて実装しました（[#1209](https://github.com/bajutsu-e2e/bajutsu/pull/1209)）。
  Device Farm 用 iOS 設定（`showcase.devicefarm.ios.config.yaml`）、`demos/showcase/Makefile` の
  署名済みデバイスアプリ／ランナーターゲット（`swiftui-archive-device` / `swiftui-ipa-device` /
  `runner-build-device`）、そしてそれらを取り巻く認証情報の衛生（`__DEVELOPMENT_TEAM__` の
  `ExportOptions.plist` テンプレートと `require_team` ガード）です。署名はデバイス向けのコマンド
  ラインでのみ有効化するため、Simulator 用のレシピと `make check` は未署名のまま変わりません。
  ユニット 5（実機での Device Farm 投入）は残します。Apple Developer と AWS のアカウントを要する
  ため、ゲートの外の手動の実証として据え置きます。
- 2026-07-20 — ユニット 5 の手動手順書を [AWS Device Farm](../../docs/devicefarm.md) のページ
  （および日本語ミラー）に記載しました（[#1213](https://github.com/bajutsu-e2e/bajutsu/pull/1213)）。「iOS のデバイス署名の実証（手動）」という新しい節で、
  プロジェクトとデバイスプールの作成、署名済みデバイス成果物 2 つのビルド、`--platform ios` での
  シナリオ 1 件の投入までを順に案内します。ユニット 5 のチェックは未完のままです。実機での経験的な
  実証には依然として Apple Developer と AWS のアカウントが必要ですが、実施する担当者のための手順書が
  そろいました。

## 参考

- [BE-0238 — iOS device-cloud execution](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md) — この署名軸を先送りした項目。
- [BE-0235 — AWS Device Farm submitter](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md) — これらの成果物を供給するバッチサブミッター。
- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) — ここで実機向けにビルドする汎用ランナー。
- [iOS を実機とデバイスクラウドで動かす](../../docs/ja/ios-device-cloud.md) · [AWS Device Farm](../../docs/ja/devicefarm.md)。
