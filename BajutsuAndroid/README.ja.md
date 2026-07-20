# BajutsuAndroid

[English](README.md) · 日本語

Android 上の [bajutsu](../) 向けのアプリ内デバイス支援ライブラリです。プラットフォームがアプリ
プロセスの内側からしか公開しない機能を bajutsu が駆動できるようにする、test/debug 専用の Android
ライブラリです。**クリップボード**と**ネットワーク捕捉**を担い、iOS の [`BajutsuKit`](../BajutsuKit)
パッケージに対応する Android 版にあたります。

## アプリ内支援が要る理由

Android 10（API 29）以降、プライマリクリップを読み書きできるのは**フォアグラウンドのアプリと既定の
IME だけ**です。そのため shell uid のプロセスからは操作できません。`cmd clipboard set/get-primary-clip`
は `No shell command implementation` を返して黙って何もせず、`service call clipboard` は
`ClipboardService.checkAndSetPrimaryClip` に阻まれ、API レベルをまたぐと脆くなります。シナリオが
アプリを駆動している最中、対象アプリはフォアグラウンドにあります。そこで bajutsu は、クリップボード操作を
アプリの**内側**のレシーバに送り、アプリプロセスから実行させます。

この方式は bajutsu を app-agnostic に保ちます。どのアプリも**同一の**ライブラリを埋め込むので、
これは per-app の差分（`targets.<name>` の設定）ではありません。iOS の BajutsuKit が通信捕捉に使うのと
同じモデルです。

## 仕組み

bajutsu は、対象アプリを宛先にした順序付き broadcast を送ります。

```
adb shell am broadcast -a dev.bajutsu.CLIPBOARD -p <package> --es op set --es b64 <base64(text)>
```

`am broadcast` は順序付き broadcast の finish-receiver として振る舞うので、レシーバの結果が
`result=<code>, data="<base64>"` として stdout に返ります。レシーバは `set` の入力を base64 で復号し、
`get` の結果へクリップの内容を base64 で符号化します（base64 は shell で安全な文字だけからなるので、
`adb shell` の argv に引用符付けが要らず、シナリオのテキストがデバイス上で実行される余地もありません）。
成功時は `result=1` を立てます。対象アプリがレシーバを埋め込んでいなければコードは `0` のままになり、
bajutsu は空のクリップボードを成功と誤読せず、明確なエラーで失敗します。

## 組み込み

ライブラリを追加し、**test/debug ビルドに限って**、`Application.onCreate` から一度だけ
`startClipboard(context)` を呼びます。iOS の BajutsuKit の `App.init()` に対応します。どの Activity より
先に登録することで、bajutsu が最初にどの画面を駆動してもレシーバが用意されています（複数 Activity の
アプリが別の入口から再開すると、ランチャの `Activity.onCreate` は通らないことがあります）。

```kotlin
import android.app.Application
import dev.bajutsu.android.Bajutsu

class MyApp : Application() {
    override fun onCreate() {
        super.onCreate()
        if (BuildConfig.DEBUG) Bajutsu.startClipboard(this)
    }
}
```

マニフェストの `<application android:name>` をこれに向けます。レシーバは実行時に登録され（マニフェスト
には何も宣言しません）、`startClipboard` を呼んだときにだけ存在します。冪等なので、二重登録は起きません。

Gradle のモジュールとしては、パスで取り込みます（showcase は
[`demos/showcase/android/settings.gradle.kts`](../demos/showcase/android/settings.gradle.kts) で
こうしています）。

```kotlin
include(":bajutsu-android")
project(":bajutsu-android").projectDir = file("../../../BajutsuAndroid")
```

そのうえで依存を宣言します。`implementation(project(":bajutsu-android"))`。

## 安全性

レシーバは**エクスポートされて**おり、権限も設定していません。そのため adb だけでなく、デバイスに
インストールされたほかのアプリも broadcast を送ってクリップボードを読み書きできます。したがって
**test/debug ビルド専用**です。`startClipboard` の呼び出しを `BuildConfig.DEBUG`（または独自の
テストフラグ）で囲い、リリースビルドで動かないようにしてください。

## ネットワーク捕捉（BE-0283）

`request` / `requestSequence` のアサーションは、iOS と同じアプリ内コレクタのモデルで通信を観測します。
ネットワーク捕捉を伴うシナリオを走らせるとき、bajutsu はホストの `127.0.0.1:<port>` にコレクタを立て、
`adb reverse` でエミュレータへ橋渡しし、その URL を `BAJUTSU_COLLECTOR` の intent extra として注入します。
iOS の `URLProtocol` があらゆる `URLSession` に透過的に割り込むのに対し、Android には全クライアントに届く
単一の OS レベルの HTTP フックがありません。そこで対象アプリは、自分の OkHttp クライアントに一行を足し、
起動時に一度だけ報告を有効化します。

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

インターセプタは `configure` が `BAJUTSU_COLLECTOR` を見つけるまで不活性です。有効になると、完了した
やり取りごとに bajutsu の `NetworkExchange` に一致する JSON をコレクタへ POST します。報告自体が
インターセプトされないよう、送信には別のクライアントを使います。OkHttp はここでは `compileOnly` の依存
なので、バージョンは対象アプリが持ち込みます。

**テスト/デバッグ専用です。** インターセプタはヘッダとボディを記録します。`configure` の呼び出しは
（上記のように）デバッグフラグで守り、bajutsu の `redact` を設定して、`network.json` に書き出す証跡
から機密情報を隠してください。iOS 向けに [BajutsuKit の Safety note](../BajutsuKit/README.md#safety)
が述べているのと同じ注意点です。

**クリアテキストの例外設定が必要です。** `BAJUTSU_COLLECTOR` は平文 HTTP の `127.0.0.1` URL であり、
Android（API 28 以降）は既定でクリアテキスト通信を遮断します（loopback を ATS で除外する iOS とは
異なります）。テスト/デバッグビルドに `127.0.0.1` へのクリアテキスト例外を `network_security_config`
で追加してください（設定例は `demos/showcase/android/*/src/main/res/xml/network_security_config.xml`）。
これがないと、インターセプタの報告 POST は `CLEARTEXT communication to 127.0.0.1 not permitted` で
失敗し、ログには残るものの他に痕跡はなく、やり取りがコレクタに届きません。

## 対応範囲

アプリのプライマリクリップに対する set / get / clear です。`get` は先頭のクリップ項目をテキストに
変換して読みます。テキスト以外のクリップ内容（画像、intent）は対象外です。

ネットワーク捕捉が見るのは **OkHttp 由来の HTTP(S)** だけで、iOS の `URLSession` に限られた捕捉と同じ
範囲です。`HttpURLConnection` を直接使う通信、別の HTTP クライアント、`WebView` は対象外です（`WebView`
は iOS と同じく、別途の追随が要ります）。
