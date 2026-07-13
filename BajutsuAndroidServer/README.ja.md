[English](README.md) · **日本語**

# BajutsuAndroidServer — 常駐 UI Automator サーバ

bajutsu が対象デバイスにインストールし、画面の読み取りをローカルソケット越しに答える、自己完結した
UI Automator インストルメンテーションです。実行のあいだ `UiAutomation` セッションを一つだけ生かし
続けます。目的は、`adb exec-out uiautomator dump` が読み取りのたびに払う起動コスト（毎回およそ 2.4
秒、ツリーの大きさによりません）を取り除くことです。このコストのために、Android の adb バックエンド
は iOS より一桁遅く画面を読みます。ロードマップ項目
[BE-0245](../roadmaps/BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)
を参照してください。

## BajutsuAndroid との違い

[`BajutsuAndroid`](../BajutsuAndroid/) は**アプリ埋め込み型**のライブラリ（BajutsuKit の Android
版）です。対象アプリが Gradle のパス指定で取り込み、クリップボード（BE-0233）のように、プラットフォーム
がアプリのプロセスからしか公開しない機能への足がかりを bajutsu に与えます。

このサーバはその逆で、bajutsu 自身がインストールして駆動する**アプリ非依存**のインストルメンテーション
です。Appium の UiAutomator2 サーバと同じ働き方をします。画面（UI）を持たず、どのアプリにも埋め込ま
れず、そのとき画面に出ているアプリを読みます。そのため、いずれかのアプリのビルドに取り込まれるのではなく、
独立した Gradle プロジェクトとして存在します。

## 構成

- サーバは `androidTest` のインストルメンテーションです。`am instrument -w` が、返らない単一のブロッ
  キング `@Test`（`ResidentServerTest.serve`）を走らせます。この `@Test` はソケットを開き、インスト
  ルメンテーションが終了させられるまで応答を続けます。これが `UiAutomation` セッションを温かいまま保ち
  ます。
- 通信は、ループバック（ポート 6790）に束ねた素の `ServerSocket` の上に手書きした HTTP/1.1 です。HTTP
  や JSON の依存はありません。サーバは一つの経路の一つのメソッドだけに答えます。
- `GET /source` は `UiDevice.dumpWindowHierarchy()` の XML を返します。これは bajutsu の
  `parse_hierarchy` がすでに解析している `AccessibilityNodeInfoDumper` の形式です。

揃えるべき差異が一つ残っています。`dumpWindowHierarchy()` はすべてのウィンドウをたどるので、その XML
にはプラットフォームの `uiautomator dump` が対象ウィンドウに絞って除く SystemUI のステータスバー（時計・
Wi-Fi・電池・通知アイコン、29 ノード）も入ります。アプリの内容は同一です。両方の経路が同じ Element を返すように常駐側のダンプを
対象ウィンドウへ絞る作業（ロードマップ項目の作業単位 2 が求める等価化）は、端から端まで回帰テストできる
トランスポート配線のスライスで入れます。

このソケットに届く Python 側（`adb forward`、`fetch_hierarchy` の配線、デバイスのリース（貸し出し）
に結んだライフサイクル）は、BE-0245 の後続スライスで実装します。

## ビルド

```bash
make -C BajutsuAndroidServer build   # ホスト APK と インストルメンテーション（androidTest）APK
```

これは `make check`（Python のゲート）には含まれません。ゲートは Kotlin をビルドしないからです。ビルド
は追跡下の `./gradlew` を通るので、新しいクローンにシステム側の Gradle は要りません。必要なのは Android
SDK だけです。
