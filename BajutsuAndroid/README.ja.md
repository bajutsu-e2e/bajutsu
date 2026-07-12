# BajutsuAndroid

[English](README.md) · 日本語

Android 上の [bajutsu](../) 向けのアプリ内デバイス支援ライブラリです。プラットフォームがアプリ
プロセスの内側からしか公開しない機能を bajutsu が駆動できるようにする、test/debug 専用の Android
ライブラリです。現在は**クリップボード**を担い、iOS の [`BajutsuKit`](../BajutsuKit) パッケージに
対応する Android 版にあたります。

## アプリ内支援が要る理由

Android 10（API 29）以降、プライマリクリップを読み書きできるのは**フォアグラウンドのアプリと既定の
IME だけ**です。そのため shell uid のプロセスからは操作できません。`cmd clipboard set/get-primary-clip`
は `No shell command implementation` を返して黙って何もせず、`service call clipboard` は
`ClipboardService.checkAndSetPrimaryClip` に阻まれ、API レベルをまたぐと脆くなります。シナリオが
駆動している最中、対象アプリはフォアグラウンドにあります。そこで bajutsu は、クリップボード操作を
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

## 対応範囲

アプリのプライマリクリップに対する set / get / clear です。`get` は先頭のクリップ項目をテキストに
変換して読みます。テキスト以外のクリップ内容（画像、intent）は対象外です。
