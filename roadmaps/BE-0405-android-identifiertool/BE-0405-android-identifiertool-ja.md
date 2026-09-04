[English](BE-0405-android-identifiertool.md) · **日本語**

# BE-0405 — IdentifierTool：Android向けの独立した識別子ライブラリ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0405](BE-0405-android-identifiertool-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0405") |
| 実装 PR | [#1904](https://github.com/bajutsu-e2e/bajutsu/pull/1904) |
| トピック | Platform support |
| 関連 | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md), [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee-ja.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md), [BE-0283](../BE-0283-android-network-capture/BE-0283-android-network-capture-ja.md), [BE-0355](../BE-0355-native-z-position/BE-0355-native-z-position-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Androidアプリがbajutsuに安定したresource-idやcontent-descを渡すには、専用の実装が要ります。今日、その実装は[ショーケース](../../demos/showcase/android)アプリ自身のソースにしかありません。ライブラリはまだ存在しません。同じ識別子を使いたいAndroidアプリは、それぞれゼロから自前の実装を書いています。

この項目は、リポジトリルートに新しいライブラリ**IdentifierTool**を追加します。[`BajutsuKit`](../../BajutsuKit)や[`BajutsuAndroid`](../../BajutsuAndroid)と並ぶ、独自のディレクトリと独自のGradleモジュールです。`BajutsuAndroid`への依存を持たず、`BajutsuAndroid`側にもこちらへの依存はありません。識別子だけを求めるアプリは、依存関係を1つ追加するだけで済みます。その依存は`BajutsuAndroid`のクリップボードやネットワークキャプチャのコードを一切引き込みません。リリースも共有しません。

## 動機

Androidは、bajutsuに渡す識別子を機能させるための知識を2つ隠し持っています。どちらもプラットフォーム自身のドキュメントには書かれておらず、どちらも間違えやすいものです。

1つ目はComposeに関する知識です。`Modifier.testTag(id)`がUI Automatorの`resource-id`属性として現れるには、条件が1つだけあります。その条件は、Composeの`semantics {}`ブロックで`testTagsAsResourceId = true`を設定することです。このフラグは、モーダルウィンドウの内側でもあらためて設定しなければなりません。モーダルウィンドウの例は`ModalBottomSheet`や`Dialog`です。モーダルはそれぞれ独自のセマンティクスサブツリーを開始します。ルートのフラグは、そこまで届きません。モーダルへの設定を1つ見落とすと、そのテストタグは`resource-id`が空のままダンプされます。ビルドと実行のどちらでも、警告は出ません。

2つ目はViewsに関する知識です。`View`のidは、あらかじめ`android:id`リソースとして宣言されていなければなりません。そうして初めて、`resources.getIdentifier(name, "id", packageName)`がそのidを解決できます。未宣言の名前は`0`に解決されます。これはよくある誤字ではなく「このビューにはidが付いていない」状態として読めてしまいます。

どちらの落とし穴も、ショーケースに固有のものではありません。bajutsuに安定した識別子でUIを扱わせたい、任意のAndroidアプリが両方に行き当たります。現状、どちらの回避策も書き記されている場所は、ショーケース自身のソースだけです。bajutsuを導入するアプリは、そのソースを読む必要があります。そのうえで、該当する行を手で写す必要もあります。この項目は、その手間を取り除きます。

この項目が実装されたあとは、読み手は次の手順で成果を直接確かめられます。新しいAndroidアプリを書きます。IdentifierToolのGradle依存関係を追加します。この項目が追加する2つか3つの関数を呼び出します。`uiautomator dump`を読み返します。期待どおりの`resource-id`が報告されているはずです。ショーケースから写した行は、1行もありません。

## 詳細設計

### IdentifierToolの置き場所

IdentifierToolは、新しいトップレベルディレクトリ`IdentifierTool/`に置きます。ファイルを2つ持ちます。

- `BajutsuAccessibility.kt`：View向けのヘルパーです。`View.accessibilityId(name)`と`View.accessibilityStateValue(value)`を追加します。どちらも、Views版ショーケースの`aid`／`stateValue`から移植します。
- `BajutsuAccessibilityCompose.kt`：Compose向けのヘルパーです。追加するのは`Modifier.accessibilityId(id)`と`Modifier.accessibilityStateValue(value)`の2つです。加えて`Modifier.enableAccessibilityIds()`も追加します。3つとも、Compose版ショーケースの`aid`／`stateValue`／`enableTestTagsAsResourceId`から移植します。

移植する関数はすべて、`BuildConfig.ACCESSIBLE`のチェックを取り除きます。理由は後述の「ゲーティングはライブラリの外に置く」を参照してください。Compose版ショーケースの`selectedState`は移植しません。理由は「検討した代替案」を参照してください。

片方のツールキットしか使わない消費側も、依存関係は1つで済みます。2つのファイルは1つのモジュールに収めます。こうすれば、ツールキットごとにコードレビュー上は分けて読めます。消費側にアーティファクトを2つ管理させることもありません。IdentifierToolの関数は、ただの拡張関数です。`@Composable`関数ではありません。`build.gradle.kts`が`androidx.compose.ui`を必要とするのはそのためで、`compileOnly`として追加します。Compose compilerプラグインは要りません。

`BajutsuAccessibilityCompose.kt`は、独自のサブパッケージ`dev.bajutsu.identifier.compose`に置きます。Viewsファイルの`dev.bajutsu.identifier`より1段下です。こうすることで、Viewsだけを使う消費側は、Composeの型を import することがありません。この分離は、コンパイル時の問題を解決します。

Viewsだけを使う消費側は、圧縮時にもう1つ別の問題を抱えます。`compileOnly`は推移的でないため、その消費側は自分のクラスパスにComposeを一切持ちません。それでも、R8の圧縮を有効にしたビルドは`androidx.compose.ui`の「missing classes」で失敗してしまいます。そのためIdentifierToolは、`consumerProguardFiles("consumer-rules.pro")`を出荷します。このファイルは、Composeのサブパッケージが参照するクラスを列挙しています。このルールを取り込んでも、Viewsだけを使う消費側のビルドサイズは変わりません。

### BajutsuAndroidへの依存を持たない

`View.accessibilityId`は、ショーケース現行の`aid()`と違い、`BajutsuZOrder.report`を呼びません。`BajutsuZOrder`は`BajutsuAndroid`にあります（[BE-0355](../BE-0355-native-z-position/BE-0355-native-z-position-ja.md)）。IdentifierToolからこれを呼ぶと、この項目が取り除こうとしている依存がそのまま戻ってきます。

識別子とZ位置報告の両方を求める消費側は、両方の依存関係を追加します。そのうえで、両方の呼び出しを自分で組み合わせます。

```kotlin
// demos/showcase/android/views/src/main/java/com/bajutsu/showcase/views/Accessibility.kt
fun <T : View> T.aid(name: String): T {
    if (BuildConfig.ACCESSIBLE) {
        accessibilityId(name)
        BajutsuZOrder.report(this)
    }
    return this
}
```

ショーケースはこの組み合わせをそのまま保つので、自身の振る舞いは変わりません。識別子だけを求める消費側は、2つ目の呼び出しを省けます。そのビルドは`BajutsuAndroid`を一切リンクしません。

### ゲーティングはライブラリの外に置く

`accessibilityId`と`accessibilityStateValue`は、どちらもフラグを一切見ません。どちらも無条件でタグ付けします。`BajutsuAndroid`の`Bajutsu.startClipboard`も、すでにこの形で動いています。iOS版のBajutsuKitの対応物も同じです。[`AccessibilityID.swift`](../../demos/showcase/ios/swiftui/Sources/AccessibilityID.swift)は、SwiftUI自身の`.accessibilityIdentifier(_:)`を包んでいます。この包みは、ショーケースアプリ自身のコンパイル条件`#if ACCESSIBLE`です。`BajutsuKit`の内部には、対応するゲートがありません。

コンパイル済みのライブラリは、`BuildConfig.ACCESSIBLE`という名前のフィールドを読めません。そのクラスは、アプリケーションモジュールごとに、消費アプリ自身のパッケージの下で生成されます。IdentifierToolは、あらかじめ参照しておけるシンボルを持ちません。

ライブラリ側にそのゲートを持たせる方法は、2通りあります。どちらも見合うコストではありません。

- **リフレクション。** 消費側に存在すると想定したフィールド名を参照します。R8は、そのフィールドを削れます。IdentifierToolは、自身が把握している依存（Compose）向けの`consumerProguardFiles`ルールなら出荷できます。ですがこれは別の問題です。あらかじめ名前を知りえないクラス上のフィールド、つまり消費側自身が生成する`BuildConfig`に対して、keepルールを出荷する手立てはありません。失敗のしかたは、ビルドエラーではなく識別子が黙って欠落するという形になります。
- **同名のGradle flavor。** IdentifierTool自身にproduct flavorを用意します。消費側には、同じ名前のflavorを追加してもらいます。これは「依存関係を1つ追加して関数を2つ呼ぶ」という目標を、別のものへ変えてしまいます。導入するすべてのアプリにとって必須の、ビルドファイル変更になってしまいます。

判断を呼び出し側に委ねれば、どちらのコストも避けられます。消費アプリは、無条件の関数を短いチェックで包むだけです。そのチェックは、自分で選べます。

- `BuildConfig`のフィールド
- Gradleのビルドタイプ
- チェックなし（インストゥルメンテーションビルドで識別子を常に出したい場合）

### ショーケースは呼び出し側を変えずに自前のゲートを保つ

ショーケースの2つの`Accessibility.kt`ファイルは、タグ付けのロジック自体を実装するのをやめます。代わりに、IdentifierToolの関数へ委譲します。その委譲は、既存の`BuildConfig.ACCESSIBLE`チェックの内側で行います。Views側の例は、上の`aid`のコードのとおりです。`demos/showcase/android`配下の呼び出し箇所は、grepによれば18ファイルにわたる122箇所あります。そのすべてが、`.aid(...)`／`.stateValue(...)`という呼び出しを変えずに済みます。ショーケース自身の関数名とシグネチャは、変わりません。実装だけが移ります。

ショーケースは、IdentifierToolの最初の消費者になります。既存の`android-e2e.yml`のエミュレータレーンを通じて、引き続き検証されます。現在のテストカバレッジは、そのまま引き継がれます。IdentifierToolに、専用のユニットテストは要りません。この振る舞いは、稼働中のUI Automatorのダンプに対してしか意味を持ちません。そのダンプは、エミュレータレーンがすでに生成しています。

### ライブラリでは取り除けないプラットフォームの制約

Viewsの`accessibilityId`は、依然としてAndroid自身の仕組みでidを解決します。その仕組みが`resources.getIdentifier(name, "id", context.packageName)`です。消費アプリは、渡すすべてのid名をあらかじめ宣言しておく必要があります。宣言先は`android:id`リソースです。`res/values/ids.xml`は、ショーケース自身の実例です。

UI Automatorの`resource-id`フィールドは、あるビューにしか存在しません。そのビューとは、リソースエントリ名を持つidを持つビューです。実行時だけのidには、そのエントリがありません。`View.generateViewId()`のようなidが、その一例です。未宣言の名前に対して`resources.getIdentifier`が`0`を返すのは、Android自身の制約です。この項目は、その隙間をパッケージングの工夫で埋められません。

Viewsを使う消費側は、自前の`ids.xml`を書き続けることになります。1つのエントリが、`accessibilityId`へ渡す文字列1つに対応します。Composeを使う消費側には、この負担がありません。`testTag`は実行時に任意の文字列を受け付けます。リソース宣言は必要ありません。

### ドキュメント

`IdentifierTool/README.md`と`README.ja.md`は、`BajutsuAndroid`自身のREADMEと同じ形にします。次の内容を書きます。

- アプリ内でのタグ付けに、そもそもなぜライブラリが要るのか
- どう組み込むか
- 前述したViews限定の`ids.xml`の注意点
- IdentifierToolがクリップボードやネットワークキャプチャのコードを一切含まないこと
- それらを求める消費側は、`BajutsuAndroid`を別途追加すること

他に2つのドキュメントにも、同じ相互参照を加えます。対象は[`docs/architecture.md`](../../docs/architecture.md)と[`docs/drivers.md`](../../docs/drivers.md)です。どちらも今、ショーケースの`Accessibility.kt`ファイルに触れています。その隣に、IdentifierToolの名前も加えます。3つ目の[`docs/developer-guide.md`](../../docs/developer-guide.md)は、実装の過程で見つかりました。そのリポジトリ最上位のディレクトリ表には、すでに`BajutsuAndroid/`が載っており、その隣にIdentifierToolを加える必要があります。これにより、adbのid規約をたどる読み手は、ライブラリ本体にもたどり着けます。今のところその読み手は、ショーケース自身のコピーにしかたどり着けません。

### 作業の分解（MECE：相互排他かつ全体網羅）

1. `IdentifierTool/`を新しいGradleライブラリモジュールとして作成します。`build.gradle.kts`の形（namespace、`compileSdk`、`minSdk`）は`BajutsuAndroid`自身に倣いますが、そこへの依存は持ちません。
2. `BajutsuAccessibility.kt`を追加します。`View.accessibilityId(name)`と`View.accessibilityStateValue(value)`を、Views版ショーケースから移植します（ゲートなし）。
3. `BajutsuAccessibilityCompose.kt`を追加します。`Modifier.accessibilityId(id)`と`Modifier.accessibilityStateValue(value)`を、Compose版ショーケースから移植します（ゲートなし）。`Modifier.enableAccessibilityIds()`も同様に移植します。`androidx.compose.ui`も`compileOnly`として追加します。
4. `demos/showcase/android/settings.gradle.kts`を更新し、IdentifierToolをパス経由で含めます。`BajutsuAndroid`もすでに同じ形で含まれています。
5. ショーケースの2つの`Accessibility.kt`ファイルを書き換えます。既存の`BuildConfig.ACCESSIBLE`チェックの内側から、IdentifierToolの関数へ委譲するようにします。Views側のファイルは、上記のとおり`BajutsuZOrder.report`も明示的に組み合わせます。`aid`、`stateValue`、`selectedState`、`enableTestTagsAsResourceId`はローカルな名前として残し、既存122箇所の呼び出しを変えません。
6. `IdentifierTool/README.md`と`README.ja.md`を、前述の形で書きます。
7. `docs/architecture.md`、`docs/drivers.md`、`docs/developer-guide.md`の相互参照を更新し、IdentifierToolに言及します。
8. 次の2通りで検証します。1つ目は、ショーケースの2モジュールそれぞれの両フレーバーがビルドできることの確認です。`demos/showcase/android`の既存Gradleタスクを使います。2つ目は、`android-e2e.yml`のCIレーンが、移行後のショーケースに対して通過することの確認です。このレーンは移した実装にとって唯一のカバレッジです。

## 検討した代替案

| 代替案 | 採らなかった理由 |
|---|---|
| 新しいモジュールではなく`BajutsuAndroid`にヘルパーを追加する | この項目の以前の草案は、この設計を採っていました。`BajutsuAndroid`のViews側はすでに`BajutsuZOrder.report`に依存しており、2つの機能は同じモジュールに収まるほど近く見えました。しかし識別子をクリップボードやネットワークキャプチャと束ねると、3つの無関係な機能が1つのリリースサイクルを共有することになります。識別子だけを求める消費側も、クリップボード受信部のコードをリンクすることになります。モジュールを分ければ、どちらのコストも消えます。代わりに、ショーケース側でディレクトリとGradleのincludeが1つずつ増えます。 |
| `BajutsuZOrder.report`を`View.accessibilityId`に組み込んだまま残し、IdentifierToolが`BajutsuAndroid`に依存する | ショーケースの「1回の呼び出しで済む」という利便性は保てます。しかしこれは、この項目が取り除こうとしている依存をそのまま戻してしまいます。IdentifierToolの消費側は、誰であっても`BajutsuAndroid`のクリップボード受信部を推移的に引き込むことになります。2つの呼び出しをショーケース自身の呼び出し箇所で組み合わせれば、コストは1行増えるだけで済み、2つのライブラリは独立を保てます。 |
| `BajutsuZOrder`自体をIdentifierToolへ移す | `BajutsuZOrder`は識別子付けとは別の、一般的な位置報告機能です。ショーケース自身のコードも、`accessibilityId`が届かない箇所からこれを呼んでいます。移しても依存は消えず、結合の置き場所が変わるだけです。しかも`BajutsuAndroid`から無関係な機能を1つ抜くだけで、得るものがありません。 |
| ライブラリが、消費側にある同名の`BuildConfig.ACCESSIBLE`フィールドをリフレクション経由で読む | ライブラリのアーティファクトは、消費側のパッケージ名をあらかじめ把握していません。IdentifierToolは、自身が把握している依存（Compose）向けの`consumerProguardFiles`ルールなら出荷できますが、あらかじめ名前を知りえないクラス（消費側自身が生成する`BuildConfig`）に対して*keep*する手立てはありません。R8はそのフィールドを削ってしまえるので、失敗のしかたはビルドエラーではなく、識別子が黙って欠落するという形になります。 |
| IdentifierTool自身にproduct flavorの次元を持たせ、消費側の同名flavorと突き合わせる | Gradleのvariant対応の依存解決は、同名のflavor同士を実際に突き合わせられるため、技術的には実現可能です。ただし、オフスイッチを得るためだけに、ライブラリと厳密に同じ名前・同じ値のflavor次元を消費側全員に追加させることになり、この項目が代わりに用意するラッパー関数より重い要求になります。 |
| `selectedState`も`accessibilityId`・`accessibilityStateValue`と一緒にIdentifierToolへ移植する | `selectedState`は`Modifier.semantics { selected = true }`そのものであり、`testTagsAsResourceId`や`resources.getIdentifier`と違ってAndroid特有の仕組みを何も抱えていません。消費アプリがこの3語を自分で書いても失うものはありません。 |

## 進捗

- [x] 単位1 — `IdentifierTool/` を新しい Gradle ライブラリモジュールとして作成しました。`BajutsuAndroid`
      の `build.gradle.kts` の形に倣い、それへの依存は持ちません
- [x] 単位2 — `BajutsuAccessibility.kt` を追加しました。`View.accessibilityId(name)` と
      `View.accessibilityStateValue(value)` を、Views の showcase から無条件（ゲートなし）で移植しました
- [x] 単位3 — `BajutsuAccessibilityCompose.kt` を追加しました。`Modifier.accessibilityId(id)`、
      `Modifier.accessibilityStateValue(value)`、`Modifier.enableAccessibilityIds()` を、Compose の
      showcase から無条件で移植しました。独自サブパッケージ `dev.bajutsu.identifier.compose` に置き、
      `consumerProguardFiles` ルールを持ちます（詳細はログを参照）
- [x] 単位4 — `demos/showcase/android/settings.gradle.kts` を更新し、IdentifierTool をパスで取り込みました
- [x] 単位5 — showcase の `Accessibility.kt` は、`BuildConfig.ACCESSIBLE` のゲートを保ったまま
      IdentifierTool へ委譲します。`aid`、`stateValue`、`selectedState`、`enableTestTagsAsResourceId`
      の名前は維持しました。既存の122箇所の呼び出しはどれも変わりません
- [x] 単位6 — `IdentifierTool/README.md` と `README.ja.md` を書きました
- [x] 単位7 — `docs/architecture.md`、`docs/drivers.md`、`docs/developer-guide.md`に、英語・
      `docs/ja/` の対訳ともIdentifierToolへの相互参照を追加しました
- [x] 単位8 — showcase の両モジュール・両フレーバーをビルドし、移行後の showcase に対して
      `android-e2e.yml` のエミュレータレーンを走らせて変更を検証しました

ログ：

- 2026-09-04 — 8つの単位をまとめて着地させました（PR #1904）。作業分解が暗に含んでいたものの列挙していなかった帰結が
  2つあります。1つ目です。`scripts/e2e_changes.py` の android レーンのパスフィルタは、
  新しいトップレベルディレクトリ `IdentifierTool/` を対象にしていませんでした。そのため、
  そこだけを触った変更は、どの E2E レーンも起動しないままでした。このフィルタに `IdentifierTool/` を追加しました。
  `tests/test_e2e_changes.py` に対応するアサーションも追加しています。
  `scripts/sync_roadmap_topic_labels.py` のパスとトピックの対応規則にも追加しました。これにより、
  `BajutsuKit/` を触る PR がすでに得ている `topic:platform` ラベルを、
  IdentifierTool を触る PR も得られるようになりました。

  2つ目です。セルフレビューの過程で見つかりました。`compileOnly` だけでは、Views だけを使う
  消費側の圧縮ビルドを R8 が失敗させることを防げません。原因は Compose ファイル自身のクラス
  参照です。このファイルを `dev.bajutsu.identifier.compose` へ分離し、
  `consumerProguardFiles("consumer-rules.pro")` を追加しました。ルールの対象は、Compose が
  実際に触る2つのサブパッケージに絞り、`androidx.compose.ui.**`
  への一括指定は避けています。

## 参考

- [`BajutsuAndroid/README.md`](../../BajutsuAndroid/README.md)
- [`BajutsuKit/README.md`](../../BajutsuKit/README.md)
- [`demos/showcase/android/compose/src/main/java/com/bajutsu/showcase/compose/Accessibility.kt`](../../demos/showcase/android/compose/src/main/java/com/bajutsu/showcase/compose/Accessibility.kt)
- [`demos/showcase/android/views/src/main/java/com/bajutsu/showcase/views/Accessibility.kt`](../../demos/showcase/android/views/src/main/java/com/bajutsu/showcase/views/Accessibility.kt)
- [`demos/showcase/ios/swiftui/Sources/AccessibilityID.swift`](../../demos/showcase/ios/swiftui/Sources/AccessibilityID.swift)
- [`docs/drivers.md`](../../docs/drivers.md#adb-android)
