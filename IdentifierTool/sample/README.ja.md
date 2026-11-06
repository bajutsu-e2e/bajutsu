# IdentifierTool サンプル

このサンプルは、最小限の単独 Android アプリです。[`IdentifierTool`](../README.ja.md) だけに依存します。
他社のアプリがこのライブラリを導入するときと同じ形をしています。

サンプルは独自の Gradle ルートを持ち、[`demos/showcase/android`](../../demos/showcase/android) とは別物です。
あちらははるかに大きなフィクスチャで、IdentifierTool を別の形で使っています。Views と Compose の
両方の半分を、`BajutsuAndroid` と組み合わせています。

`MainActivity.kt` は、2つの view に対して `View.accessibilityId` / `View.accessibilityStateValue` を呼びます。
それぞれの名前は、`app/src/main/res/values/ids.xml` にあらかじめ宣言しています。この Views 側の注意点は、
IdentifierTool 自身の README が説明しています。

IdentifierTool の関数はすべて無条件にタグ付けするので、どちらの呼び出しもフラグの内側には置いていません。
その結果、このアプリには `noax` 相当のオフスイッチがありません。

## 実行方法

```bash
./gradlew :app:installDebug
```

または、このディレクトリを Android Studio で開いてください。
