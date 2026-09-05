# IdentifierTool サンプル

最小限の単独 Android アプリです。[`IdentifierTool`](../README.ja.md) だけに依存します。他社の
アプリが導入するときと同じ形をしています。独自の Gradle ルートを持ち、
[`demos/showcase/android`](../../demos/showcase/android)とは別物です。あちらははるかに大きな
フィクスチャで、Views・Compose 両方の半分と `BajutsuAndroid` を組み合わせて IdentifierTool を
使っています。

`MainActivity.kt` は、2 つの view に対して `View.accessibilityId` / `View.accessibilityStateValue`
を呼びます。それぞれの名前は、`app/src/main/res/values/ids.xml` にあらかじめ宣言しています。
これは IdentifierTool 自身の README が説明している、Views 側の注意点です。どちらの呼び出しも
フラグの内側には置いていません。IdentifierTool の関数はすべて無条件にタグ付けするため、この
アプリには示すべき `noax` 相当のオフスイッチがありません。

## 実行方法

```bash
./gradlew :app:installDebug
```

または、このディレクトリを Android Studio で開いてください。
