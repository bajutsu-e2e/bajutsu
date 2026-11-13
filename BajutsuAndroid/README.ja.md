# BajutsuAndroid

[English](README.md) · 日本語

BajutsuAndroid は、Android 上の [bajutsu](../) 向けのアプリ内デバイス支援ライブラリです。プラットフォームが
アプリプロセスの内側からしか公開しない機能を、bajutsu が駆動できるようにします。test/debug 専用の
Android ライブラリで、**クリップボード**、実機での**位置**、**ネットワーク捕捉**、`web:` ブロックが必要とする **WebView のデバッグ**を担います。iOS の
[`BajutsuKit`](../BajutsuKit) パッケージに対応する Android 版にあたります。

## アプリ内支援が要る理由

Android 10（API 29）以降、プライマリクリップを読み書きできるのは、**フォアグラウンドのアプリと既定の
入力メソッドエディタ（IME）だけ**です。そのため shell ユーザーのプロセスからは操作できません。

- `cmd clipboard set/get-primary-clip` は `No shell command implementation` を返して、黙って何もしません。
- `service call clipboard` は `ClipboardService.checkAndSetPrimaryClip` に阻まれ、API レベルをまたぐと脆くなります。

シナリオが駆動している最中、対象アプリはフォアグラウンドにあります。そこで bajutsu は、クリップボード操作を
アプリの**内側**のレシーバに送り、アプリプロセスから実行させます。

この方式は bajutsu を app-agnostic に保ちます。どのアプリも**同一の**ライブラリを埋め込むので、
ライブラリが per-app の差分（`targets.<name>` の設定）を増やすことはありません。iOS の BajutsuKit が
通信捕捉に使うのと同じモデルです。

## 仕組み

bajutsu は、対象アプリを宛先にした順序付き broadcast を送ります。

```
adb shell am broadcast -a dev.bajutsu.CLIPBOARD -p <package> --es op set --es b64 <base64(text)>
```

`am broadcast` は順序付き broadcast の finish-receiver として振る舞います。そのため、レシーバの結果は
`result=<code>, data="<base64>"` として stdout に返ります。

レシーバは `set` の入力を base64 で復号し、`get` の結果にはクリップの内容を base64 で符号化して入れます。
base64 は shell で安全な文字だけからなるので、`adb shell` の argv に引用符付けが要りません。
シナリオのテキストがデバイス上で実行される余地もありません。

レシーバは成功時に `result=1` を立てます。対象アプリがレシーバを埋め込んでいなければコードは `0` のままになり、
bajutsu は空のクリップボードを成功と誤読せず、明確なエラーで失敗します。

## 組み込み

ライブラリを追加し、`Application.onCreate` から `startClipboard(context)` と `startLocation(context)` を
一度ずつ呼びます。呼ぶのは **test/debug ビルドに限ります**。これらの呼び出しは、iOS の BajutsuKit の
`App.init()` に対応します。

レシーバはどの Activity よりも先に登録します。そうすれば、bajutsu が最初にどの画面を駆動しても、レシーバが
用意されています。複数 Activity のアプリが別の入口から再開すると、ランチャの `Activity.onCreate` は通らない
ことがあります。

```kotlin
import android.app.Application
import dev.bajutsu.android.Bajutsu

class MyApp : Application() {
    override fun onCreate() {
        super.onCreate()
        if (BuildConfig.DEBUG) {
            Bajutsu.startClipboard(this)
            Bajutsu.startLocation(this)
        }
    }
}
```

マニフェストの `<application android:name>` を、このクラスに向けます。2 つのレシーバはどちらも実行時に
登録され、マニフェストには何も宣言しません。そのため、各レシーバは対応する `start` 関数を呼んだときにだけ
存在します。呼び出しは冪等なので、二重登録は起きません。実機で位置を設定する必要のないアプリは、
`startLocation` を省けます。

ライブラリを Gradle のモジュールとして使うには、パスで取り込みます。showcase は
[`demos/showcase/android/settings.gradle.kts`](../demos/showcase/android/settings.gradle.kts) でこうしています。

```kotlin
include(":bajutsu-android")
project(":bajutsu-android").projectDir = file("../../../BajutsuAndroid")
```

そのうえで、モジュールへの依存を宣言します。`implementation(project(":bajutsu-android"))`。

## 安全性

2 つのレシーバはどちらも**エクスポートされて**おり、権限も設定していません。そのため、adb 経由でも、
デバイスにインストールされたほかのアプリ経由でも、*あらゆる*ローカルアプリが broadcast を送れます。
そうしたアプリはクリップボードを読み書きでき、bajutsu が mock location の許可を与えているあいだは
アプリの位置も動かせます。

このライブラリは**test/debug ビルド専用**です。`startClipboard` と `startLocation` の呼び出しを
`BuildConfig.DEBUG`（または独自のテストフラグ）で囲んでください。リリースビルドでは呼び出しが走りません。

## 実機での位置設定

この節では、[BE-0481](../roadmaps/BE-0481-android-device-set-location/BE-0481-android-device-set-location-ja.md)
で加わった位置の経路を説明します。ローカルのエミュレータでは、`setLocation` ステップにライブラリは要りません。
bajutsu はエミュレータのコンソール（`adb emu geo fix`）で位置を設定します。実機にはコンソールがなく、
`adb connect` で接続したエミュレータにも adb から届くコンソールがありません。そうした対象では、bajutsu は
アプリを経由して位置を動かします。

1. アプリに mock location の app-op を許可します。
   `adb shell appops set <package> android:mock_location allow`
2. 座標を文字列にした順序付き broadcast を送ります。
   `adb shell am broadcast -a dev.bajutsu.LOCATION -p <package> --es op set --es lat <lat> --es lon <lon>`
3. レシーバが `LocationManager.addTestProvider` で `gps` と `network` をテスト用 provider として登録します。
   さらに両方を有効にし、それぞれに位置を設定します。

bajutsu は lease で最初の `setLocation` を実行するときに経路を選びます。コンソールの経路を選ぶのは、serial が
`emulator-<port>` の形で、かつ `ro.boot.qemu` か `ro.kernel.qemu` のプロパティが `1` のときです。それ以外の
対象はすべてアプリ内の経路を通ります。

レシーバは次の 4 つの結果コードのどれかを返します。

| 結果 | 意味 | bajutsu の報告 |
|---|---|---|
| `1` | 位置を設定または解除した | — |
| `2` | `addTestProvider` が `SecurityException` を投げた | アプリに mock location の app-op がない |
| `3` | provider の呼び出しが `IllegalArgumentException` を投げた | 結果データに入ったプラットフォームのメッセージ |
| `0` | broadcast を処理したレシーバがない | アプリが `startLocation` を呼んでいない |

テスト用 provider は、登録したアプリのプロセスが終わると消えるかもしれません。API 34 のエミュレータでは
強制停止と再起動のあとも残りましたが、ほかのイメージやデバイスでは異なる可能性があります。そのため bajutsu は、
自分がアプリを起動するたびに直前の位置を送り直します。対象は `relaunch` ステップ、`installApp` ステップのあとの起動、crawl のリセットです。
lease が終わるときは、次の 3 段階を実行します。

1. `clear` を broadcast し、2 つのテスト用 provider を取り除きます。アプリが動いていなければ、先に
   アプリを起動します。provider はプロセスより長く残り、取り除けるのはアプリだけだからです。
2. `appops set <package> android:mock_location default` で app-op を元に戻します。
3. すべての lease の終わりと同じく、アプリを強制停止します。

これにより、同じデバイスで次に走るシナリオは、デバイス本来の位置から始まります。最初の 2 段階が失敗しても
警告をログに残すだけで、シナリオの判定には影響しません。

ライブラリが mock するのは `gps` と `network` の provider だけです。Google Play 開発者サービスの fused
provider は mock しないので、ライブラリは Play 開発者サービスに依存しません。アプリが
`FusedLocationProviderClient` で位置を読む場合、mock が見えるかどうかはデバイスと Play 開発者サービスの
バージョンによります。この挙動は、まだ実機で確かめていません。

ライブラリは `ACCESS_MOCK_LOCATION` 権限を宣言しません。bajutsu がテストに使うエミュレータのイメージでは、
この権限がなくても `appops set` で app-op を許可できます。

## ネットワーク捕捉（BE-0283）

`request` / `requestSequence` のアサーションは、iOS と同じアプリ内コレクタのモデルで通信を観測します。
bajutsu はネットワーク捕捉を伴うシナリオを走らせるとき、次の3つを行います。

1. ホストの `127.0.0.1:<port>` にコレクタを立てます。
2. `adb reverse` でコレクタをエミュレータへ橋渡しします。
3. コレクタの URL を `BAJUTSU_COLLECTOR` の intent extra として注入します。

iOS の `URLProtocol` は、あらゆる `URLSession` に透過的に割り込みます。Android には単一の OS レベルの
HTTP フックがないので、対象アプリは自分の OkHttp クライアントに一行を足し、起動時に一度だけ報告を有効化
します。

```kotlin
import dev.bajutsu.android.BajutsuNet

// 起動時に一度、すでに読んでいる launch env のマップから（bajutsu がコレクタを注入していなければ
// 何もしないので、test/debug ビルドでは無条件に呼んでも安全です）:
BajutsuNet.configure(launchEnv)

// 対象アプリが組み立てる OkHttpClient に:
val client = OkHttpClient.Builder()
    .addInterceptor(BajutsuNet.interceptor())
    .build()
```

インターセプタは、`configure` が `BAJUTSU_COLLECTOR` を見つけるまで不活性です。有効になると、完了した
やり取りごとに、bajutsu の `NetworkExchange` に一致する JSON をコレクタへ POST します。POST には別のクライアントを
使うので、報告自体がインターセプトされることはありません。OkHttp はここでは `compileOnly` の依存なので、
バージョンは対象アプリが持ち込みます。

**テスト/デバッグ専用です。** インターセプタはヘッダとボディを記録します。上記のとおり、`configure` の呼び出しは
デバッグフラグで守ってください。あわせて bajutsu の `redact` を設定し、書き出す証跡（`network.json`）から
機密情報を隠してください。iOS 向けにも、[BajutsuKit の Safety note](../BajutsuKit/README.md#safety) が
同じ注意点を述べています。

**クリアテキストの例外設定が必要です。** `BAJUTSU_COLLECTOR` は平文 HTTP の `127.0.0.1` URL です。
Android（API 28 以降）は既定でクリアテキスト通信を遮断します。loopback を App Transport Security（ATS）の
対象から除外する iOS とは異なります。

テスト/デバッグビルドに、`127.0.0.1` へのクリアテキスト例外を `network_security_config` で追加してください。
設定例は `demos/showcase/android/*/src/main/res/xml/network_security_config.xml` にあります。例外がないと、
インターセプタの報告 POST は `CLEARTEXT communication to 127.0.0.1 not permitted` で失敗します。この失敗は
ログには残りますが、ほかに痕跡がなく、やり取りはコレクタに届きません。

## WebView のデバッグ

シナリオの `web:` ブロックは、アプリが埋め込んだ WebView の中を読み取り、タップします（[BE-0482](../roadmaps/BE-0482-android-webview-cdp/BE-0482-android-webview-cdp-ja.md)）。Android では、bajutsu は Chrome DevTools Protocol（CDP）でページに到達します。デバッグを有効にした WebView は、`webview_devtools_remote_<pid>` という名前の abstract なローカルソケットで待ち受けます。bajutsu はブロックの間だけ、このソケットを `adb` で転送します。アプリ側に専用のサーバーは要りません。WebView を作る前に、`Application.onCreate` からデバッグを 1 回の呼び出しで有効にします。

```kotlin
class MyApp : Application() {
    override fun onCreate() {
        super.onCreate()
        if (BuildConfig.DEBUG) {
            Bajutsu.enableWebViewDebugging(this)
        }
    }
}
```

`enableWebViewDebugging` は、アプリがデバッグ可能なときにデバッグを有効にします。どの debug ビルドタイプも `android:debuggable` を設定します。それ以外では何もしません。そのため、誤って呼んだリリースビルドもソケットを開きません。デバッグが有効な間は、端末上のどのプロセスもソケットに接続できます。接続したプロセスは、アプリが表示するすべてのページを読み取り、操作できます。セッションの Cookie やフォームの入力も含みます。この確認が要るのはそのためです。関数は、デバッグを有効にしたかどうかを返します。

WebView を別の `android:process` に置くアプリは、そのプロセスでだけヘルパーを呼びます。`Application.onCreate` はすべてのプロセスで走ります。そしてこの呼び出しは WebView を読み込み、プロセスごとのデータディレクトリのロックを取ります。Android 9（API 28）以降は、このロックを 2 つのプロセスで共有させません。

アプリがデバッグを有効にしていないと、bajutsu はこの呼び出しを名指しする理由で `web:` ブロックを失敗させます。シナリオ側の説明は[シナリオ](../docs/ja/scenarios.md#webwebview-の-dom-コンテキストに入る)にあります。

## 対応範囲

クリップボードの対応範囲は、アプリのプライマリクリップに対する set / get / clear です。`get` は先頭の
クリップ項目をテキストに変換して読みます。テキスト以外のクリップ内容（画像、intent）は対象外です。

位置の対応範囲は `gps` と `network` の provider です。fused provider は対象外です。

ネットワーク捕捉が見るのは **OkHttp 由来の HTTP(S)** の通信だけです。範囲は、iOS の `URLSession` に限られた
捕捉と同じです。次のものは対象外です。

- `HttpURLConnection` を直接使う通信
- 別の HTTP クライアントの通信
- `WebView` の通信（iOS と同じく、別途の追随が要ります）
