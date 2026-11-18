[English](README.md) · **日本語**

# BajutsuAndroidUIAutomatorServer — 常駐 UI Automator サーバ

BajutsuAndroidUIAutomatorServer は、自己完結した UI Automator インストルメンテーションです。bajutsu が
対象デバイスにインストールし、画面の読み取りをローカルソケット越しに答えます。実行のあいだ、
`UiAutomation` セッションを一つだけ生かし続けます。

このサーバの目的は、`adb exec-out uiautomator dump` が呼び出しのたびに払う起動コスト（毎回およそ 2.4 秒、
ツリーの大きさによりません）を取り除くことです。このコストのために、Android の adb バックエンドは iOS より
一桁遅く画面を読みます。ロードマップ項目
[BE-0245](../roadmaps/BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)
を参照してください。

## BajutsuAndroid との違い

[`BajutsuAndroid`](../BajutsuAndroid/) は**アプリ埋め込み型**のライブラリで、BajutsuKit の Android 版です。
対象アプリが Gradle のパス指定で取り込みます。このライブラリは、クリップボード（BE-0233）のように、
プラットフォームがアプリのプロセスからしか公開しない機能への足がかりを、bajutsu に与えます。

このサーバはその逆で、bajutsu 自身がインストールして駆動する**アプリ非依存**のインストルメンテーション
です。Appium の UiAutomator2 サーバと同じ働き方をします。画面（UI）を持たず、どのアプリにも埋め込まれず、
そのとき画面に出ているアプリを読みます。そのため、アプリのビルドには取り込まず、独立した Gradle プロジェクト
として存在します。

## 構成

- サーバは `androidTest` のインストルメンテーションです。`am instrument -w` が、返らない単一のブロッキング
  `@Test`（`ResidentServerTest.serve`）を走らせます。この `@Test` はソケットを開き、インストルメンテーション
  が終了させられるまで応答を続けます。これによって `UiAutomation` セッションが温かいまま保たれます。
- 通信は、ループバック（ポート 6790）に束ねた素の `ServerSocket` の上に手書きした HTTP/1.1 です。HTTP や
  JSON の依存はありません。サーバは少数の決まった経路だけに答えます。
- `GET /source` は `UiDevice.dumpWindowHierarchy()` の XML を返します。XML の形式は、bajutsu の
  `parse_hierarchy` がすでに解析している `AccessibilityNodeInfoDumper` の形式と同じです。
- `POST /type` は、`adb shell input text` で入力できない文字列を、入力フォーカスを持つ欄に入力します。
  文字列は base64 のリクエスト本文で届き、サーバは `ACTION_SET_TEXT` で設定します。

### Python 側で揃えるウィンドウの差異

`dumpWindowHierarchy()` はすべてのウィンドウをたどります。そのため XML には、SystemUI のステータスバー
（時計、Wi-Fi、電池、通知アイコン、29 ノード）も入ります。プラットフォームの `uiautomator dump` は、
対象ウィンドウに絞ってこれを除いています。アプリの内容は同一です。

`bajutsu.common.backend_cli.adb_resident.narrowed_root` が SystemUI の装飾ウィンドウを取り除くので、
両方の経路が同じ Element を返します。ロードマップ項目の作業単位 2 が、この等価化を求めています。

### Python 側の実装

Python 側は、`adb forward`、`fetch_hierarchy` の配線、デバイスのリース（貸し出し）に結んだライフサイクルの
3つでこのソケットに届きます。コードは
[`bajutsu/common/backend_cli/adb_resident.py`](../bajutsu/common/backend_cli/adb_resident.py) にあり、
`bajutsu/platform_lifecycle.py` が Android のリースに配線しています（BE-0245 PR-C）。

この機能は、Android の e2e レーンがサーバをビルドして導入するまで、環境変数 `BAJUTSU_ADB_RESIDENT` による
オプトインです。変数を設定しなければ、adb バックエンドはこれまでどおり `uiautomator dump` で読み取ります。

## ビルド

```bash
make -C BajutsuAndroidUIAutomatorServer build   # ホスト APK と インストルメンテーション（androidTest）APK
```

このビルドは `make check` に含まれません。Python のゲートは Kotlin をビルドしないからです。ビルドは追跡下の
`./gradlew` を通るので、新しいクローンにシステム側の Gradle は要りません。必要なのは Android SDK だけです。

## 遅い読み取りの測定

次の手順では、idle にならない画面で 1 回の `GET /source` を測り、時間の内訳を確かめます。最も時間が
かかるのは、500 ミリ秒より短い間隔でアクセシビリティイベントを出し続ける画面です。このとき
`UiDevice.waitForIdle()` は上限いっぱいまで待ちます。待ちの予算を導入する前は、この画面の読み取りに
API 34 のエミュレータで 120.9 秒かかりました。

ビルド済みのサーバー、インストール済みの 2 つの APK、起動済みのエミュレータまたは端末が必要です。Bajutsu
は実行時に APK を自分でインストールするので、Android の実行のあとならインストールは不要です。

1. 時計アプリのストップウォッチのタブを開き、ストップウォッチを開始します。表示は 1 秒に約 10 回更新されます。

2. サーバーを起動します。このコマンドは終了しないので、専用の端末で実行します。

   ```bash
   adb shell am instrument -w -e class dev.bajutsu.android.server.ResidentServerTest#serve dev.bajutsu.android.server.test/androidx.test.runner.AndroidJUnitRunner
   ```

3. サーバーのポートを転送します。

   ```bash
   adb forward tcp:6790 tcp:6790
   ```

4. 1 回の読み取りを測ります。約 3.6 秒で `200` が返れば、ホストの読み取りの待ち時間 5 秒に予算が収まっています。

   ```bash
   curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' 'http://127.0.0.1:6790/source?budgetMs=3500'
   ```

5. 時間の内訳を読みます。`source waits` の行に各段階が並びます。

   ```bash
   adb logcat -d -s BajutsuResidentServer:D
   ```

   ```text
   source waits: idle=1074ms dump=2140ms idle=330ms dump=25ms total=3570ms budget=3500ms
   ```

6. 後片付けをします。

   ```bash
   adb forward --remove tcp:6790
   ```

   ```bash
   adb shell am force-stop dev.bajutsu.android.server
   ```

`tests/test_adb_resident_budgets.py` は、端末なしで `make check` のたびに、これらの待ちがホストの待ち時間に
収まることを確かめます。数値そのものは
[`bajutsu/common/backend_cli/adb_resident/budgets.py`](../bajutsu/common/backend_cli/adb_resident/budgets.py)
にあります。
