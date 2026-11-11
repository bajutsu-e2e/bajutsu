# BajutsuAndroid

[English](README.md) · 日本語

BajutsuAndroid は、Android 上の [bajutsu](../) 向けのアプリ内デバイス支援ライブラリです。プラットフォームが
アプリプロセスの内側からしか公開しない機能を、bajutsu が駆動できるようにします。test/debug 専用の
Android ライブラリで、**クリップボード**と**ネットワーク捕捉**を担います。iOS の
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

ライブラリを追加し、**test/debug ビルドに限って**、`Application.onCreate` から一度だけ
`startClipboard(context)` を呼びます。この呼び出しは、iOS の BajutsuKit の `App.init()` に対応します。

レシーバはどの Activity よりも先に登録します。そうすれば、bajutsu が最初にどの画面を駆動しても、レシーバが
用意されています。複数 Activity のアプリが別の入口から再開すると、ランチャの `Activity.onCreate` は通らない
ことがあります。

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

マニフェストの `<application android:name>` を、このクラスに向けます。レシーバは実行時に登録され、マニフェスト
には何も宣言しません。そのため、レシーバは `startClipboard` を呼んだときにだけ存在します。呼び出しは冪等
なので、二重登録は起きません。

ライブラリを Gradle のモジュールとして使うには、パスで取り込みます。showcase は
[`demos/showcase/android/settings.gradle.kts`](../demos/showcase/android/settings.gradle.kts) でこうしています。

```kotlin
include(":bajutsu-android")
project(":bajutsu-android").projectDir = file("../../../BajutsuAndroid")
```

そのうえで、モジュールへの依存を宣言します。`implementation(project(":bajutsu-android"))`。

## 安全性

レシーバは**エクスポートされて**おり、権限も設定していません。そのため、adb 経由でも、デバイスに
インストールされたほかのアプリ経由でも、*あらゆる*ローカルアプリが broadcast を送ってクリップボードを
読み書きできます。

このライブラリは**test/debug ビルド専用**です。`startClipboard` の呼び出しを `BuildConfig.DEBUG`（または独自の
テストフラグ）で囲み、リリースビルドでは呼び出しが走らないようにしてください。

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

## 対応範囲

クリップボードの対応範囲は、アプリのプライマリクリップに対する set / get / clear です。`get` は先頭の
クリップ項目をテキストに変換して読みます。テキスト以外のクリップ内容（画像、intent）は対象外です。

ネットワーク捕捉が見るのは **OkHttp 由来の HTTP(S)** の通信だけです。範囲は、iOS の `URLSession` に限られた
捕捉と同じです。次のものは対象外です。

- `HttpURLConnection` を直接使う通信
- 別の HTTP クライアントの通信
- `WebView` の通信（iOS と同じく、別途の追随が要ります）
