# IdentifierTool

[English](README.md) · 日本語

Android 上の [bajutsu](../) 向けに、安定した `resource-id` や `content-desc` を渡すための、
アプリ内識別子付与ライブラリです。Android が隠している2つの仕込みをまとめます。Compose 向けの
仕込みは `testTag` と `testTagsAsResourceId` です。Views 向けの仕込みは、宣言済みの `android:id`
に対する `resources.getIdentifier` です。

[`BajutsuAndroid`](../BajutsuAndroid)（BE-0233 のクリップボード／ネットワーク捕捉ライブラリ）への
**依存を持ちません**。`BajutsuAndroid` 側にもこちらへの依存はありません。識別子だけを求めるアプリは
依存関係を1つ追加するだけで済みます。その依存は `BajutsuAndroid` のクリップボードレシーバやネットワーク
インターセプタを一切引き込まず、リリースも共有しません。

## アプリ内支援が要る理由

Android は、bajutsu に有効な識別子を渡すために必要な2つの知識を隠しています。どちらもプラットフォーム
自身は文書化していません。

- **Compose。** `Modifier.testTag(id)` は、UI Automator の `resource-id` 属性として現れます。ただし、
  Compose の `semantics {}` ブロックで `testTagsAsResourceId = true` を設定したときに限ります。この
  フラグは、モーダルウィンドウ（`ModalBottomSheet`、`Dialog`）の内側でも設定し直す必要があります。
  各モーダルは自分自身の semantics サブツリーを開始するため、ルートのフラグは届きません。設定を
  忘れたモーダルは、ビルド時とランタイムのどちらでも警告なく、空の `resource-id` をダンプします。
- **Views。** `View` の id は、宣言済みの `android:id` リソースとしてあらかじめ存在していなければ
  なりません。この宣言があってはじめて、`resources.getIdentifier(name, "id", packageName)` が名前を
  解決できます。未宣言の名前は `0` に解決され、これは「typo」ではなく「この view には id がない」と
  読めてしまいます。

## 組み込み

Gradle のモジュールとしては、パスで取り込みます（showcase は
[`demos/showcase/android/settings.gradle.kts`](../demos/showcase/android/settings.gradle.kts) で
こうしています）。

```kotlin
include(":identifier-tool")
project(":identifier-tool").projectDir = file("../../../IdentifierTool")
```

そのうえで依存を宣言します。`implementation(project(":identifier-tool"))`。

UI ツールキットに合わせて、対応するヘルパを呼びます。Views 側は `dev.bajutsu.identifier`、
Compose 側は `dev.bajutsu.identifier.compose` と、パッケージを分けています。Views のみを使う
アプリが、Compose の classpath を持たないシンボルを import してしまわないためです。

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

どの関数も、フラグを見ずに無条件でタグ付けします。ライブラリのアーティファクトは、消費側の
アプリケーションモジュールごとに生成される `BuildConfig.ACCESSIBLE` のようなフィールドを読めません。
そのため、有効・無効の判断は呼び出し側に委ねられています。`BuildConfig` のフィールド、Gradle の
ビルドタイプ、あるいは計装ビルドで識別子を常に有効にしておくならチェックなし、といった形で、呼び出し
側が自分の判断で囲ってください。

## `android:id` に関する注意点（Views のみ）

`accessibilityId` は、Android 自身の解決経路を経由して id を解決します。その経路は
`resources.getIdentifier(name, "id", context.packageName)` です。そのため Views ベースのアプリは、
渡す id 名をすべて事前に `android:id` リソースとして宣言しておく必要があります。パターンは showcase 自身の
[`ids.xml`](../demos/showcase/android/views/src/main/res/values/ids.xml) を参照してください。
UI Automator の `resource-id` フィールドは、リソースのエントリ名を持つ view のためだけに存在します。
`View.generateViewId()` のように実行時に作られた id にはエントリ名がなく、これは Android 自身の
制約です。パッケージングの工夫では埋められません。Compose ベースのアプリはこの負担を負いません。
`testTag` は実行時の任意の文字列を受け付け、リソース宣言を必要としないからです。

## 提供しないもの

IdentifierTool はクリップボードやネットワーク捕捉のコードを一切持たず、`View.accessibilityId` は
z 順も報告しません。それらも必要なアプリは、[`BajutsuAndroid`](../BajutsuAndroid) を別の依存として
追加し、呼び出し自体は自分で組み合わせます。showcase 自身の
[`Accessibility.kt`](../demos/showcase/android/views/src/main/java/com/bajutsu/showcase/views/Accessibility.kt)
がその形を示しています。
