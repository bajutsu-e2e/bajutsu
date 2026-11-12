[English](BE-XXXX-android-libraries-maven-central.md) · **日本語**

# BE-XXXX — BajutsuAndroid と IdentifierTool を Maven Central で公開する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-android-libraries-maven-central-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform support |
| 関連 | [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md), [BE-0283](../BE-0283-android-network-capture/BE-0283-android-network-capture-ja.md), [BE-0355](../BE-0355-native-z-position/BE-0355-native-z-position-ja.md), [BE-0405](../BE-0405-android-identifiertool/BE-0405-android-identifiertool-ja.md), [BE-0481](../BE-0481-android-device-set-location/BE-0481-android-device-set-location-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Bajutsu には、テスト対象のアプリに組み込む Android ライブラリが2つあります。
[`BajutsuAndroid`](../../BajutsuAndroid) は、クリップボード、通信の捕捉、z 順序の報告、位置情報の模擬のために、
bajutsu にアプリ内の足がかりを与えます。[`IdentifierTool`](../../IdentifierTool) は、アプリに安定した
`resource-id` と `content-desc` を与えます。現在、アプリがどちらかのライブラリを使う方法は1つしかありません。
このリポジトリを clone し、ライブラリのディレクトリをパス指定で Gradle モジュールとして取り込む方法です。

この項目では、両ライブラリを **Maven Central** に公開します。Maven Central は、Android Studio で新規作成した
プロジェクトが最初から依存解決に使うリポジトリです。公開後のアプリは、ビルドファイルに次の1行を足すだけで済みます。

```kotlin
debugImplementation("io.github.bajutsu-e2e:bajutsu-android:0.1.0")
```

ライブラリごとに独立したバージョンとリリース用のタグを持たせます。タグを push すると GitHub Actions の
ワークフローが動きます。ワークフローは成果物に署名し、Sonatype の Central Portal 経由でアップロードし、
対応する GitHub Release を作ります。

## 動機

iOS 側には、すでに1手順で済む導入方法があります。`BajutsuKit` はこのリポジトリのルートにある
Swift Package Manager（SwiftPM）のパッケージなので、Xcode のプロジェクトはリポジトリのアドレスと
バージョンを指定して追加できます。Android 側には、同等の手段はありません。導入するチームは、
現在3つの負担を抱えます。

- **clone か submodule が要ります。** アプリのビルドから、このリポジトリのファイルがディスク上に見えている
  必要があります。Git の submodule にすると、小さなディレクトリ1つのために Python のコア、ドキュメント、
  デモまで丸ごと取り込むことになります。
- **ライブラリ単体ではビルドできません。** `BajutsuAndroid/build.gradle.kts` はプラグインのバージョンを
  宣言しておらず、Android Gradle Plugin（AGP）と Kotlin のバージョンを取り込む側のビルドから受け取ります。
  AGP のバージョンが異なるアプリでは、bajutsu と関係のない理由でビルドの構成に失敗することがあります。
- **固定できるバージョンがありません。** パス指定の取り込みは、clone が指しているコミットの内容をそのまま
  使います。同じチームの開発者2人が、気付かないうちに別々のレシーバを組み込んでいることもあり得ます。
  どのレシーバで実行したかは、どこにも記録されません。

この3つの負担は、導入の最初の一歩にかかります。Android で bajutsu を評価しようとするチームは、
シナリオを1本も動かす前にこれらの負担に直面します。

この項目が完了したことは、次の結果で確かめられます。Android Studio のデフォルトのテンプレートから作った
プロジェクトに `debugImplementation("io.github.bajutsu-e2e:bajutsu-android:<version>")` を足すと、そのまま
ビルドが通ります。追加のリポジトリ設定も、clone も、認証情報も要りません。`bajutsu-identifier-tool` でも
同じ結果になります。

## 詳細設計

### 座標

| ライブラリ | ソースのディレクトリ | Maven の座標 | Kotlin のパッケージ（変更なし） |
|---|---|---|---|
| BajutsuAndroid | `BajutsuAndroid/` | `io.github.bajutsu-e2e:bajutsu-android` | `dev.bajutsu.android` |
| IdentifierTool | `IdentifierTool/` | `io.github.bajutsu-e2e:bajutsu-identifier-tool` | `dev.bajutsu.identifier` |

groupId の `io.github.bajutsu-e2e` は、GitHub の組織 `bajutsu-e2e` に対応します。Central Portal が
namespace を認める方法は2つあります。

- 公開者が所有するドメインを、Domain Name System（DNS）の `TXT` レコードで確認する方法
- `io.github.<name>` のように、コードホスティングのアカウントで確認する方法

Bajutsu はドメインを所有しておらず、取得する予定もないので、2つ目の方法を選びます。namespace の確認は、
人が一度だけ行う作業です（後述の「人が一度だけ行う準備」を参照してください）。

Kotlin のパッケージ名は変えません。Kotlin のパッケージ名にはハイフンを使えず、パッケージ名と Maven の
groupId を一致させる必要もありません。パッケージ名を変えると、既存の利用側のコードがすべて動かなくなるうえに、
得るものがありません。

各ライブラリは `release` バリアントだけを Android Archive（AAR）として公開し、sources jar と Javadoc jar を
添えます。Central Portal はこの3つすべてと、各ファイルへの署名を求めます。さらに、
Project Object Model（POM）ファイルに、ライセンス（Apache License 2.0）、ソースのリポジトリ、開発者を記載することも求めます。

### 公開用の独立した Gradle ビルド

現在は、どちらのライブラリのディレクトリも単体ではビルドできません。どちらもプラグインのバージョンを
取り込む側のビルドから受け取るためです。この項目では、2つのライブラリをビルドして公開するための
Gradle ビルドとして、トップレベルに `android-libraries/` を新設します。

- `android-libraries/settings.gradle.kts` は、`../BajutsuAndroid` を `:bajutsu-android` として、
  `../IdentifierTool` を `:bajutsu-identifier-tool` として、パス指定で取り込みます。showcase が現在
  使っている方法と同じです。
- `android-libraries/build.gradle.kts` は、AGP、Kotlin、
  [`com.vanniktech.maven.publish`](https://github.com/vanniktech/gradle-maven-publish-plugin)
  プラグインのバージョンを宣言します。両プロジェクトに公開用プラグインを適用し、共通の POM 項目を
  一か所で設定します。
- `android-libraries/` は独自の Gradle wrapper を持ちます。リリースのジョブと開発者の手元で、同じ
  Gradle のバージョンを使うためです。

2つのライブラリのディレクトリには、それぞれ `gradle.properties` を1つずつ足します。このファイルは
ライブラリごとの POM 項目（`POM_ARTIFACT_ID`、`POM_NAME`、`POM_DESCRIPTION`）を持ちます。各ライブラリの
`build.gradle.kts` は、公開に関する設定を持たないままにします。showcase（`demos/showcase/android`）と
`IdentifierTool/sample` は、引き続きパス指定でライブラリを取り込み、公開用プラグインを適用しません。
したがって、どちらのビルドも変わりません。

vanniktech のプラグインは、Central Portal へのアップロード、メモリ上での署名、sources jar と Javadoc jar の
生成、POM の作成を一か所で引き受けます。署名は署名用のプロパティがあるときにだけ行うので、secret を使わない
`verify` ジョブでも、開発者の手元の `publishToMavenLocal` でも、署名なしで動きます。Maven Central にある Android ライブラリの多くが、現在このプラグインで
公開しています。

### バージョンとタグ

各ライブラリは独立したバージョンを持ちます。BE-0405 が `IdentifierTool` を `BajutsuAndroid` から分けたのは、
両者がリリースを共有しないようにするためでした。バージョンを1つにまとめると、公開の段階でその分割を
打ち消してしまいます。

| ライブラリ | タグの形式 | 例 |
|---|---|---|
| BajutsuAndroid | `bajutsu-android-v<semver>` | `bajutsu-android-v0.1.0` |
| IdentifierTool | `bajutsu-identifier-tool-v<semver>` | `bajutsu-identifier-tool-v0.1.0` |

公開するバージョンは、タグだけから決まります。ツリー内のどのファイルもリリース番号を記録しないので、
リリースのためにバージョンを上げるコミットは要りません。タグなしのローカルビルドは `0.0.0-SNAPSHOT` を
公開し、このバージョンが Maven Central に届くことはありません。両ライブラリとも `0.1.0` から始めます。
セマンティックバージョニングの規則では、`1.0.0` 未満のマイナーバージョンでアプリケーション
プログラミングインタフェース（API）を壊してもかまいません。

### リリースのワークフロー

新しいワークフロー `.github/workflows/android-libraries.yml` は、2つのジョブを持ちます。

| ジョブ | 起動条件 | 実行内容 |
|---|---|---|
| `verify` | `BajutsuAndroid/`、`IdentifierTool/`、`android-libraries/` のいずれかに触れるプルリクエスト | 両ライブラリについて署名なしで `./gradlew publishToMavenLocal` を実行し、AAR、sources jar、Javadoc jar、POM がそれぞれ `~/.m2` にあることを確かめます |
| `release` | 上の2つの形式のどちらかに一致するタグの push | タグからライブラリとバージョンを読み取り、`:<library>:publishAndReleaseToMavenCentral` を実行し、AAR を添付した GitHub Release を作ります |

`verify` ジョブは、壊れた POM や欠けた jar を、原因となったプルリクエストの時点で捕まえます。このジョブが
なければ、壊れたことに次のリリースまで気付けません。`verify` ジョブは secret を使わないので、fork からの
プルリクエストでも動きます。

`release` ジョブは、`maven-central` という名前の GitHub environment の中で動きます。4つの secret は
この environment にだけ置き、プルリクエストのワークフローからは読めないようにします。

- `MAVEN_CENTRAL_USERNAME` と `MAVEN_CENTRAL_PASSWORD` は、Central Portal のユーザートークンです。
- `SIGNING_IN_MEMORY_KEY` と `SIGNING_IN_MEMORY_KEY_PASSWORD` は、テキスト形式で書き出した
  GNU Privacy Guard（GPG）の秘密鍵と、そのパスフレーズです。

リポジトリの ruleset で、2つのタグ形式を push できる人を制限します。さらに、`release` ジョブは最初の
ステップで、アップロードの前にタグを検証します。バージョンがセマンティックバージョニング
（`<major>.<minor>.<patch>`）の形式でない場合と、タグの指すコミットが `main` の祖先でない場合
（`git merge-base --is-ancestor "$GITHUB_SHA" origin/main`）に、ジョブを失敗させます。

`publishAndReleaseToMavenCentral` は成果物のバンドルをアップロードし、Central Portal 自身の検証を通ったら自動で
リリースします。Maven Central に公開したリリースは、削除や上書きができません。誤ったリリースは、次のパッチバージョンを
公開して直します。

### 人が一度だけ行う準備

次の作業には、適切なアカウント権限を持つ人が要ります。ワークフローでは行えません。

1. プロジェクト用の Central Portal アカウントを作り、`io.github.bajutsu-e2e` の namespace を申請します。
   Central Portal が確認用のキーを発行します。`bajutsu-e2e` 組織のオーナーが、そのキーを名前にした一時的な
   公開リポジトリを作ります。Central Portal が namespace を確認済みにしたら、そのリポジトリは削除します。
2. 署名用の GPG 鍵ペアを作り、Central Portal が参照する鍵サーバ（`keys.openpgp.org`）に公開鍵を登録します。
3. Central Portal のユーザートークンを発行します。
4. `maven-central` environment を作り、デプロイできるブランチとタグを2つのタグ形式だけに制限します。
   これで、プルリクエストのワークフローからは environment を読めなくなります。そのうえで、4つの secret を
   この environment に登録し、タグの ruleset を追加します。

これらの手順は developer guide に記録します（「ドキュメント」を参照してください）。後で鍵やトークンを
更新する保守担当者も、同じページに従えます。

### ドキュメント

- `BajutsuAndroid/README.md` と `IdentifierTool/README.md`（それぞれの日本語版を含む）は、パス指定の
  取り込み手順を Maven の座標に置き換えます。パス指定の方法も、ライブラリ自体を開発する人向けの2つ目の
  選択肢として残します。どちらのライブラリもテストとデバッグのための道具なので、両 README とも
  `debugImplementation` を勧めます。
- `BajutsuAndroid/README.md` には、`BajutsuNet` の OkHttp インターセプタが OkHttp を `compileOnly` で
  宣言していることを書き添えます。POM は OkHttp を依存に含めないので、インターセプタを呼ぶアプリは、
  現在と同じく自分で OkHttp を持ち込みます。
- `IdentifierTool/README.md` にも、ライブラリが `androidx.compose.ui` を `compileOnly` で宣言している
  ことを同じように書き添えます。POM は Compose を依存に含めないので、Compose 用のヘルパーを呼ぶアプリは、
  現在のパス指定の取り込みと同じく自分で Compose を持ち込みます。
- `docs/developer-guide.md` と `docs/ja/developer-guide.md` に「Android ライブラリのリリース」の節を足し、
  タグの形式、バージョンの方針、上記の一度だけ行う準備を記載します。
- `docs/drivers.md`（adb の節）とその日本語版で2つのライブラリに言及している箇所から、座標を参照します。

### 対象外とするもの

- **bajutsu 本体と組み込んだライブラリのバージョン互換チェック。** 公開済みのバージョンをアプリで固定する
  ようになると、Python の adb ドライバとアプリ内のレシーバとで、バージョンのずれが起こりえます。ずれを
  検出するには、broadcast にプロトコルのバージョンを載せ、`bajutsu/` 側で照合する必要があります。
  この変更は公開の仕組みではなくドライバに触れるので、別の項目で扱います。
- **`BajutsuAndroidUIAutomatorServer`。** このサーバの Android パッケージ（APK）は、bajutsu 自身が
  デバイスにインストールします。アプリのビルドが使うものではないので、Maven Central に置いても
  得るものがありません。
- **スナップショットの公開**（`main` の各コミットから `-SNAPSHOT` を出すこと）。現時点で要望がありません。
  この項目で作るものを変えずに、後の項目で追加できます。

### 作業分解（MECE：相互排他かつ全体網羅）

1. `android-libraries/`（settings、プラグインのバージョンと共通の POM 項目を持つルートのビルドファイル、
   Gradle wrapper）を追加し、`BajutsuAndroid/` と `IdentifierTool/` にライブラリごとの POM 項目を持つ
   `gradle.properties` を追加します。`android-libraries/` で `./gradlew publishToMavenLocal` を実行すると
   両ライブラリの AAR、sources jar、Javadoc jar、POM が生成され、showcase が変更なしでビルドできれば完了です。
2. `verify` と `release` の2つのジョブを持つ `.github/workflows/android-libraries.yml` を追加します。
   あわせて、`scripts/sync_roadmap_topic_labels.py` の既存の `IdentifierTool/` の規則の隣に、
   `android-libraries/` を `platform` トピックとして登録します。`scripts/e2e_changes.py` は変更しません。
   showcase は `android-libraries/` を経由してビルドしないので、Android のオンデバイス E2E で検証できる
   ものがないためです。`make lint-actions` が通り、プルリクエストで
   `verify` ジョブが成功し、`release` ジョブのタグ検証がセマンティックバージョニングでない版と `main` の外の
   コミットを拒否すれば完了です。
3. 前述の「人が一度だけ行う準備」を済ませます。Central Portal で `io.github.bajutsu-e2e` が確認済みと
   表示されれば完了です。
4. 「ドキュメント」に挙げた README とドキュメントを、両言語で更新します。
5. `bajutsu-android-v0.1.0` と `bajutsu-identifier-tool-v0.1.0` を push します。デフォルトのテンプレートから
   新規作成した Android Studio のプロジェクトが、`google()` と `mavenCentral()` 以外のリポジトリなしで
   両方の座標を解決してビルドできれば完了です。

## 検討した代替案

| 代替案 | 採らなかった理由 |
|---|---|
| JitPack | JitPack は Git のタグから要求に応じてライブラリをビルドするので、準備はほとんど要りません。しかし、利用者は全員、ビルドに `maven("https://jitpack.io")` を足す必要があります。あるバージョンへの最初の要求はビルドを待たされ、JitPack が止まると全利用者の同期が止まります。このリポジトリのルートは Python のプロジェクトなので、Gradle のビルドを見つけさせるための `jitpack.yml` も要ります。 |
| GitHub Packages | GitHub Packages は、公開パッケージの取得にも個人アクセストークンを求めます。利用者全員が、アプリをビルドする CI のジョブも含めて、トークンを用意しなければなりません。 |
| GitHub Release に AAR を添付するだけの配布 | ダウンロードした AAR には POM がないので、Gradle は `androidx.core` のような依存を解決しません。新しいバージョンが出たことも利用者に伝わりません。ただし、ファイル単体が欲しい読者のために、この項目でも各 GitHub Release に AAR を添付します。 |
| 所有ドメインの groupId（`bajutsu.dev` を取得して `dev.bajutsu`、または `bajutsu-e2e.dev` を取得して `dev.bajutsu-e2e`） | 座標にホスティングサービスの名前が入らず、`dev.bajutsu` なら Kotlin のパッケージ名とも完全に一致します。しかし、ドメインを購入し、毎年更新し、失効させないよう管理し続ける必要があります。失効したドメインは第三者の手に渡るおそれがあるためです。Bajutsu にはドメインを所有する予定がありません。将来ドメインを取得してその namespace に移ると、全利用者の座標が変わります。 |
| 両ライブラリで1つのバージョンと1つのタグを共有する | ワークフローの経路もリリースごとのタグも1つで済みます。しかし、片方のライブラリの修正で、変更のないもう片方のバージョンまで上がります。また、BE-0405 は2つのライブラリがそれぞれの都合でリリースできるように分けました。 |
| 各ライブラリの `build.gradle.kts` に公開用プラグインを直接適用する | パス指定でライブラリを取り込むすべてのビルドが、プラグインのバージョンも宣言しなければならなくなります。showcase と `IdentifierTool/sample` に、本来の目的と関係のない変更が要ります。`android-libraries/` からプラグインを設定すれば、両ビルドは手つかずのままです。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 単位 1：`android-libraries/` の公開用ビルドと、ライブラリごとの `gradle.properties`
- [ ] 単位 2：`android-libraries.yml` ワークフロー（`verify` と `release`）と、トピックラベルの登録
- [ ] 単位 3：人が一度だけ行う準備（namespace からタグの ruleset まで）
- [ ] 単位 4：README とドキュメントの両言語での更新
- [ ] 単位 5：両ライブラリの初回リリースと、新規の Android Studio プロジェクトからの確認

## 参考

- [`BajutsuAndroid/README.md`](../../BajutsuAndroid/README.md)
- [`IdentifierTool/README.md`](../../IdentifierTool/README.md)
- [`demos/showcase/android/settings.gradle.kts`](../../demos/showcase/android/settings.gradle.kts)
- [Sonatype Central Portal：公開の要件](https://central.sonatype.org/publish/requirements/)
- [Sonatype Central Portal：namespace の登録](https://central.sonatype.org/register/namespace/)
- [gradle-maven-publish-plugin](https://github.com/vanniktech/gradle-maven-publish-plugin)
