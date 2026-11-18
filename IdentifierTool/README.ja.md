# IdentifierTool

[English](README.md) · 日本語

IdentifierTool は、Android 上の [bajutsu](../) 向けのアプリ内識別子付与ライブラリです。bajutsu に
安定した `resource-id` や `content-desc` を渡すための、単独の Android ライブラリで、Android が隠している
仕込みをまとめます。Compose 向けのヘルパ（`testTag` と `testTagsAsResourceId`）と、Views 向けのヘルパ
（宣言済みの `android:id` に対する `resources.getIdentifier`）の2つです。

このライブラリは [`BajutsuAndroid`](../BajutsuAndroid)（BE-0233 のクリップボード／ネットワーク捕捉
ライブラリ）への**依存を持たず**、`BajutsuAndroid` 側にもこちらへの依存はありません。識別子だけを求める
アプリは、依存関係を1つ追加するだけで済みます。その依存は `BajutsuAndroid` のクリップボードレシーバや
ネットワークインターセプタを一切引き込まず、リリースも共有しません。

## アプリ内支援が要る理由

アプリが bajutsu に有効な識別子を渡すには、2つの知識が要ります。Android はどちらも隠しており、
プラットフォーム自身も文書化していません。

- **Compose。** `Modifier.testTag(id)` が UI Automator の `resource-id` 属性として現れるのは、Compose の
  `semantics {}` ブロックで `testTagsAsResourceId = true` を設定したときに限ります。このフラグは、
  モーダルウィンドウ（`ModalBottomSheet`、`Dialog`）の内側でも設定し直す必要があります。各モーダルは
  自分自身の semantics サブツリーを開始するため、ルートのフラグは届かないからです。設定を忘れたモーダルは、
  ビルド時にもランタイムにも警告を出さず、空の `resource-id` をダンプします。
- **Views。** `View` の id は、宣言済みの `android:id` リソースとして、あらかじめ存在していなければ
  なりません。この宣言があってはじめて、`resources.getIdentifier(name, "id", packageName)` が名前を解決
  できます。未宣言の名前は `0` に解決され、これは「typo」ではなく「この view には id がない」と読めて
  しまいます。

## 組み込み

ライブラリを Gradle のモジュールとして使うには、パスで取り込みます。showcase は
[`demos/showcase/android/settings.gradle.kts`](../demos/showcase/android/settings.gradle.kts) でこうして
います。

```kotlin
include(":identifier-tool")
project(":identifier-tool").projectDir = file("../../../IdentifierTool")
```

そのうえで、モジュールへの依存を宣言します。`implementation(project(":identifier-tool"))`。
[`sample/`](sample/README.md) は、この形で取り込んだ最小限の単独アプリで、ほかの bajutsu ライブラリは
一切組み合わせていません。

UI ツールキットに合わせて、対応するヘルパを呼びます。Views 側は `dev.bajutsu.identifier`、Compose 側は
`dev.bajutsu.identifier.compose` と、パッケージを分けています。この分け方によって、Views のみを使う
アプリが、Compose の classpath を持たないシンボルを import してしまうことはありません。

```kotlin
// Views
import dev.bajutsu.identifier.accessibilityId
import dev.bajutsu.identifier.accessibilityStateValue

view.accessibilityId("stable_refresh")
view.accessibilityStateValue("loading")
```

```kotlin
// Compose
import dev.bajutsu.identifier.compose.accessibilityId
import dev.bajutsu.identifier.compose.accessibilityStateValue
import dev.bajutsu.identifier.compose.enableAccessibilityIds

Modifier
    .enableAccessibilityIds() // コンテンツのルートと、モーダルウィンドウの内側それぞれで
    .accessibilityId("stable.refresh")
    .accessibilityStateValue("loading")
```

どの関数も、フラグを見ずに無条件でタグ付けします。ライブラリのアーティファクトは、消費側の任意の
classpath 上でアプリケーションモジュールごとに Gradle が生成する `BuildConfig.ACCESSIBLE` のような
フィールドを読めません。そのため、有効・無効の判断は呼び出し側に委ねられています。呼び出しは、自分の
チェックで囲ってください。チェックには、`BuildConfig` のフィールドや Gradle のビルドタイプを使えます。
計装ビルドで識別子を常に有効にしておくなら、チェックは不要です。

## `android:id` に関する注意点（Views のみ）

`accessibilityId` は、Android 自身の解決経路 `resources.getIdentifier(name, "id", context.packageName)` を
経由して id を解決します。そのため Views ベースのアプリは、渡す id 名をすべて、事前に `android:id`
リソースとして宣言しておく必要があります。パターンは、showcase 自身の
[`ids.xml`](../demos/showcase/android/views/src/main/res/values/ids.xml) を参照してください。

UI Automator の `resource-id` フィールドは、リソースのエントリ名を持つ view のためだけに存在します。
`View.generateViewId()` のように実行時に作られた id にはエントリ名がありません。これは Android 自身の
制約であり、パッケージングの工夫では埋められません。

Compose ベースのアプリは、この負担を負いません。`testTag` は実行時の任意の文字列を受け付け、リソース宣言を
必要としないからです。

## 提供しないもの

IdentifierTool はクリップボードやネットワーク捕捉のコードを一切持たず、`View.accessibilityId` は z 順も
報告しません。それらも必要なアプリは、[`BajutsuAndroid`](../BajutsuAndroid) を別の依存として追加し、
呼び出し自体は自分で組み合わせます。showcase 自身の
[`Accessibility.kt`](../demos/showcase/android/views/src/main/java/com/bajutsu/showcase/views/Accessibility.kt)
がその形を示しています。
