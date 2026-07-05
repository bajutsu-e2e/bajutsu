[English](README.md) · **日本語**

# Showcase：Android（Compose + Views）

Bajutsu showcase ドッグフード一式の Android 版です。[BE-0007 Android バックエンド](../../../roadmaps/BE-0007-android-backend/BE-0007-android-backend-ja.md)
に先行して、そのバックエンドが駆動する対象アプリとして用意しました。挙動、要素の一覧、起動時の
環境変数フック、ディープリンク、OS アラートを出す画面は、すべて [`../SPEC.md`](../SPEC.ja.md) に
一度だけ定義され、ここでは要素単位でそのまま実装しています。iOS のペアと対をなす構成で、
**Jetpack Compose** 版が SwiftUI 版に、**Android Views** 版が UIKit 版に対応し、それぞれを
2 つのアクセシビリティ flavor でビルドします。

## 2 つのコードベースから 4 つのプロダクト

flavor の違いは Gradle の product flavor（`a11y` / `noax`）が定める `BuildConfig.ACCESSIBLE` の
真偽 1 つだけで、ソースを分岐させてはいません。各モジュールの `Accessibility.kt`（SPEC §8）の
ヘルパーが、`a11y` flavor のときだけ identifier を付与し、アサーション用に状態を反映します。

| Gradle タスク | ツールキット | `ACCESSIBLE` | Application id | 表示名 | ディープリンクのスキーム |
|---|---|---|---|---|---|
| `:compose:assembleA11yDebug` | Compose | true | `com.bajutsu.showcase.android.compose` | Showcase Compose | `showcasecompose` |
| `:compose:assembleNoaxDebug` | Compose | false | `com.bajutsu.showcase.android.compose.noax` | Showcase Compose (no a11y) | `showcasecomposenoax` |
| `:views:assembleA11yDebug` | Views | true | `com.bajutsu.showcase.android.views` | Showcase Views | `showcaseviews` |
| `:views:assembleNoaxDebug` | Views | false | `com.bajutsu.showcase.android.views.noax` | Showcase Views (no a11y) | `showcaseviewsnoax` |

`a11y` ビルドは SPEC §5 の要素一覧を UI Automator の `resource-id` として公開し、`noax` ビルドは
identifier の無いツリーになります。アクセシビリティ対応を省いたときのコストを具体的に示すものです
（BE-0007 が実装されれば、`doctor --target` がこのペアを Ready / Blocked と判定します）。

## identifier の現れ方（2 つの id 規約）

2 つのモジュールは、BE-0007 が定める Android の 2 つの id 経路を意図的に一つずつ担います。

- **Compose**：`Modifier.aid("stable.refresh")` が `testTag` を設定し、コンテンツのルートで
  `testTagsAsResourceId = true` を有効にしているため、UI Automator はそのタグを `resource-id`
  として公開します。`testTag` は任意の文字列を受け付けるので、SPEC §5 のドット区切り id が
  **そのまま**再現され、共有の [`../scenarios/`](../scenarios) 一式を変更なしで実行できます。
- **Views**：`View.aid("stable_refresh")` が本物の `android:id`（[`views/src/main/res/values/ids.xml`](views/src/main/res/values/ids.xml)
  に宣言）を割り当て、UI Automator がそれをネイティブに公開します。`android:id` の名前には `.` も
  `-` も使えないため、SPEC の id は機械的に対応づけます（どちらも `_` になり、`stable.refresh` →
  `stable_refresh`、`search.results-empty` → `search_results_empty`）。BE-0007 のドライバーが照合時に
  `.` と `_` を同一視するか、Views 用のシナリオ variant を用意するかは BE-0007 側の設計判断です。
  このフィクスチャは、差異を取り繕わずプラットフォーム本来の規約を正直に公開します。

状態の反映は両ツールキットで同じです。どちらも値を `content-desc` に反映します。これは
`uiautomator dump` が公開するチャネルです（Compose の `stateDescription` はダンプに現れません）。
SPEC §2.1 を参照してください。

## 起動時の環境変数フックとディープリンク

`launchEnv`（SPEC §3）は、launcher Activity の **intent extras** として届きます（BE-0007 の起動
シーケンスが `am start` で渡します）。起動時に一度だけ読み込む変数は `SHOWCASE_UITEST`、
`SHOWCASE_TAB`、`SHOWCASE_API_URL`、`SHOWCASE_HTTP_BASE` です。ディープリンク（SPEC §4）は上記の
プロダクトごとのスキームと共通のホスト文法（`…://stable` 〜 `…://permissions`）を使います。
launcher Activity は `singleTask` なので、ディープリンクは起動中のアプリでタブを選択し、push
された詳細画面を閉じます。

OS アラートのフィクスチャ（SPEC §7）は、Android では**実行時パーミッションのダイアログ**に
対応します。Permissions タブの 2 つのボタンが `POST_NOTIFICATIONS` と `ACCESS_FINE_LOCATION` の
プロンプトを出します。どちらもプロセス外のシステム UI であり、iOS の SpringBoard プロンプトと
同じくアラートガードのフィクスチャになります。動かすときは **API 33 以上のエミュレータ**を使って
ください。`POST_NOTIFICATIONS` は Android 13 以降でのみ実行時パーミッションになるため、それより
古いイメージでは通知プロンプトが出ません（SPEC §2.1）。`SHOWCASE_UITEST` によるアニメーション
無効化は、Android ではアプリ側ではなくドライバーがデバイス全体で行います（SPEC §2.1）。

## ビルド

JDK（17 以上）と Android SDK が必要です。Gradle wrapper はコミット済みなので、別途 Gradle を
インストールする必要はありません。`./gradlew`（Makefile 経由）が初回に固定バージョンの Gradle を
ダウンロードします。

```bash
make -C demos/showcase/android build-all   # 4 つの APK すべて
make -C demos/showcase/android compose-build      # Compose の a11y プロダクトのみ
make -C demos/showcase/android views-noax-build   # Views の no-a11y プロダクトのみ
```

各 APK は `<module>/build/outputs/apk/<flavor>/debug/` に生成されます。
[`../showcase.config.yaml`](../showcase.config.yaml) の android ターゲットの `appPath` は、この
場所をそのまま指しています。これらのターゲットに対する `run` / `doctor` には BE-0007 の adb
バックエンドが必要で、実装されるまでは `--backend android` はレジストリの「not implemented yet」
エラーで失敗します。
