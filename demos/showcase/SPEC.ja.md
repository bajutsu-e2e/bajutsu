[English](SPEC.md) · **日本語**

# Bajutsu Showcase：フィクスチャ仕様書

> showcase dogfood アプリ群の唯一の真実（single source of truth）です。UIKit 版と SwiftUI 版は
> 同じ識別子のまま *この仕様* を実装するので、1 つのシナリオ集がすべての変種を駆動します。アプリの
> ソースを変える前にこれを読んでください。設計の根拠は [`DESIGN.md`](../../DESIGN.md) に、ロードマップ項目は
> `dogfood-showcase-apps` の BE 項目にあります。

## 1. 目的

showcase は Bajutsu の **次世代 dogfood 対象** です。`record`（Tier 1 オーサリング）、
`crawl`（Tier 1 探索、[BE-0038](../../roadmaps/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)）、
`run`（Tier 2 決定的ゲート）を実践するための土台です。実アプリが持つ操作面（タブ＋ナビゲーション＋
モーダルによる画面遷移、テキスト入力、ジェスチャ、非同期ロード、通信（実通信＋モック可能）、そして
意図的に OS レベルのアラートを上げる画面）を、その全体を語れる最小のアプリに収めています。

これは**唯一の iOS フィクスチャ**です（BE-0079 で旧 `demo`/`sample`/`sample2` を退役させました）。
単一変種のアプリが 1 コードベースなのに対し、showcase は**同じアプリを 2 回書き**（UIKit と SwiftUI）、
**さらに各々をアクセシビリティの 2 変種**で出します。これは Bajutsu の設計全体が依って立つ要点を
可視化するためです。

| 変種 | アクセシビリティ識別子 | 何を示すか |
|---|---|---|
| **`*-a11y`**（識別子 ON） | すべての操作可能要素に安定した `accessibilityIdentifier` | `run`。id ベースのセレクタが一意に解決し、シナリオが決定的に再実行できる。`doctor --target` は **Ready** |
| **`*-noax`**（識別子 OFF） | 識別子を一切持たない | `record`。AI 著者は stability ladder（[DESIGN §5](../../DESIGN.md)）を下って `label`/`traits`/座標へ落ちる。`doctor --target` は **Blocked**。アクセシビリティを省いた代償を具体化する |

> **対比が肝心な理由**。セレクタの安定性が決定性のレバー（[DESIGN §2](../../DESIGN.md)）です。
> `-a11y` ↔ `-noax` の双子は対照実験です。アプリもフローも同じで、識別子だけが違います。同じゴールを
> `record` に両方へ通せば、その差分こそがアクセシビリティ作業の価値です。

## 2. アプリマトリクス

以下の iOS マトリクスは 2 コードベース × 2 ビルド変種 = 4 プロダクトで、§2.1 が Android 版の 4
プロダクトを加えます。各ツールキットは **1 コードベース、2 ビルドターゲット** です。変種の差は Swift
のアクティブコンパイル条件 `ACCESSIBLE` ただ 1 つで、ソースの分岐はありません（§8）。

| アプリ名（`targets.<name>`） | ツールキット | `ACCESSIBLE` | Bundle id | Deeplink scheme | 表示名 |
|---|---|---|---|---|---|
| `showcase-swiftui` | SwiftUI | 定義 | `com.bajutsu.showcase.ios.swiftui` | `showcaseswiftui` | Showcase SwiftUI |
| `showcase-swiftui-noax` | SwiftUI | — | `com.bajutsu.showcase.ios.swiftui.noax` | `showcaseswiftuinoax` | Showcase SwiftUI (no a11y) |
| `showcase-uikit` | UIKit | 定義 | `com.bajutsu.showcase.ios.uikit` | `showcaseuikit` | Showcase UIKit |
| `showcase-uikit-noax` | UIKit | — | `com.bajutsu.showcase.ios.uikit.noax` | `showcaseuikitnoax` | Showcase UIKit (no a11y) |

2 つの `-a11y` アプリは、識別子集合、launch-env フック、deeplink を **完全に同一**（byte-for-byte）に
露出しなければなりません。これにより `demos/showcase/scenarios/*.yaml` がどちらにも無改変で通ります。
UIKit と SwiftUI のビュー構築は違ってよいですが、以下の契約は違えてはいけません。

### 2.1 Android 版（[`android/`](android/)、BE-0007 の準備）

同じフィクスチャの Android 版があります。[BE-0007 Android バックエンド](../../roadmaps/BE-0007-android-backend/BE-0007-android-backend-ja.md)
に先行して、そのバックエンドが駆動するアプリのペアとして用意したものです。**Jetpack Compose** 版が
SwiftUI のコードベースに、**Android Views** 版が UIKit に対応し、a11y/noax のペアは Gradle の
flavor 切り替え（`BuildConfig.ACCESSIBLE`）1 つです。iOS の `ACCESSIBLE` と同じく、ソースの分岐は
ありません。

| アプリ名（`targets.<name>`） | ツールキット | `ACCESSIBLE` | Application id | Deeplink scheme | 表示名 |
|---|---|---|---|---|---|
| `showcase-compose` | Compose | true | `com.bajutsu.showcase.android.compose` | `showcasecompose` | Showcase Compose |
| `showcase-compose-noax` | Compose | false | `com.bajutsu.showcase.android.compose.noax` | `showcasecomposenoax` | Showcase Compose (no a11y) |
| `showcase-views` | Views | true | `com.bajutsu.showcase.android.views` | `showcaseviews` | Showcase Views |
| `showcase-views-noax` | Views | false | `com.bajutsu.showcase.android.views.noax` | `showcaseviewsnoax` | Showcase Views (no a11y) |

§5 の契約は、共有する**論理的な**要素一覧です。各プラットフォームでそれをどう露出するかは、
チャネル（BE-0007 のセレクタ対応）だけが違います。

| iOS（§5、§8） | Android Compose | Android Views |
|---|---|---|
| `accessibilityIdentifier` | `testTag` → `resource-id`（`testTagsAsResourceId`）。ドット区切り id を**そのまま**再現 | `android:id` → `resource-id`。id の `.`/`-` は `_` に対応（`stable.refresh` → `stable_refresh`） |
| `accessibilityValue` の反映 | `content-desc` | `content-desc` |
| `ProcessInfo` 経由の `launchEnv` | intent extras | intent extras |
| deeplink のスキーム + ホスト | VIEW の intent-filter、ホスト文法は共通（§4） | 同じ |
| SpringBoard アラート（§7） | 実行時パーミッションのダイアログ（`POST_NOTIFICATIONS`、`ACCESS_FINE_LOCATION`） | 同じ |
| `UIPasteboard` の往復（§5.4） | `ClipboardManager` | 同じ |

Android では、どちらのツールキットも状態の値を `content-desc` に反映します（Compose の
`stateDescription` は `uiautomator dump` に現れないため使いません）。共有の契約から外れる Android
固有の例外が 2 つあります。

- **`SHOWCASE_UITEST`（アニメーション無効化、§3）**：iOS ではアプリ自身がアニメーションを無効に
  しますが、Android ではドライバーがデバイス全体で無効にします（`adb shell settings put global
  animator_duration_scale 0`）。そのためアプリはフックを読むだけで、アプリ側では何もしません。
- **通知プロンプト（§7）には API 33 以上が必要です**：`POST_NOTIFICATIONS` は Android 13（API 33）
  以降でのみ実行時パーミッションになります。それより古いエミュレータではプロンプトが出ないため、
  アラートガードの流れを試すには API 33 以上のエミュレータで動かしてください。

Compose の testTag はドット区切り id をそのまま再現するので、共有の [`scenarios/`](scenarios)
一式が `showcase-compose` に無改変で通ります。Views の id はアンダースコアへの対応づけです
（`android:id` の名前には `.` も `-` も使えません）が、共有一式は `showcase-views` にも無改変で
通ります。各セレクタが id を**両方の形**で持ち（`id: [stable.refresh, stable_refresh]`）、画面に
現れたほうにマッチする OR として決定論的コアが解決するからです。id 規約はドライバー側の
`.` と `_` の書き換えではなく、シナリオに明示的に残します（BE-0221）。ネットワークは **OkHttp** を通し、
`BajutsuAndroid` のインターセプタを使うので、`network` の証跡は Android でも効きます（BE-0283、§6）。
`mocks` は追随の課題です。それ以外の以下の記述は、Android の 4 プロダクトすべてに当てはまります。

## 3. 起動環境フック

`launchEnv`（[DESIGN §6.1](../../DESIGN.md)）で注入します。すべて起動時に `ProcessInfo` から一度だけ読みます。接頭辞は `SHOWCASE_`。

| 変数 | 効果 | 既定 |
|---|---|---|
| `SHOWCASE_UITEST` | アニメーション無効化（条件待機を締める） | 未設定 |
| `SHOWCASE_API_URL` | カタログ GET（`/horses`）の base URL | `https://example.com` |
| `SHOWCASE_HTTP_BASE` | エコー用 POST/DELETE エンドポイントの base | `https://httpbin.org` |

> **認証ゲートはありません**。アプリは起動直後からタブ UI に入り、つねに Stable タブに着地します。
> ほかのタブへは、ネイティブのタブバーをタップして移動します（XCUITest バックエンドがラベルで
> タップします。BE-0107 で `SHOWCASE_TAB` の起動時ショートカットを廃止しました。このショートカットは、
> タブバーをタップできなかった、廃止済みの idb バックエンド（BE-0290）が必要としていたものです）。
> カタログは**固定**（馬 5 頭）で、シードを注入する launch-env の口はありません
> （BE-0079）。シナリオはアプリ自身のデータを観測するだけで、データ状態を注入できません。同様に、
> push で開く画面へ起動時に直行する近道もありません（§4 を参照）。

## 4. Deeplink

scheme は変種ごと（§2）、host 文法は共通です。deeplink は**タブを選択**します（あわせてモーダルを閉じ、
そのタブをルートまで戻します）。詳細画面を push することはありません（BE-0079）。詳細はカタログの行を
タップしてのみ到達するので、push で開く画面へ直行する近道はありません。

| Deeplink（host） | 効果 |
|---|---|
| `…://stable` / `search` / `log` / `notices` / `permissions` | そのタブを選択 |

## 5. 画面別仕様

**5 タブのメイン UI、認証ゲートなし**。アプリは起動直後からタブに入ります。操作可能
要素の識別子をすべて列挙します。`-noax` 変種はそれらをすべて省きます。識別子は
[DESIGN §7.3](../../DESIGN.md) に従い `<namespace>.<element>`、小文字、データ由来、画面内一意です。
状態は（`-a11y` では）`accessibilityValue` にミラーし、アサーションが読めるようにします。

### 画面一覧

| # | 画面 | 到達方法 | 種別 | 名前空間 | 仕様 |
|---|---|---|---|---|---|
| 1 | Stable（カタログ一覧） | `stable` タブ | タブ・リスト | `stable` | §5.1 |
| 2 | Horse Detail | Stable 行 | push | `horse` | §5.1 |
| 3 | Search | `search` タブ | タブ・フィルタ一覧 | `search` | §5.2 |
| 4 | Log | `log` タブ | タブ・フォーム＋モーダル | `log` | §5.3 |
| 5 | — Filter シート | `log.openFilter` | sheet（detent） | `log` | §5.3 |
| 6 | — Gallery カバー | `log.openGallery` | フルスクリーンカバー | `log` | §5.3 |
| 7 | — Delete ダイアログ | `log.openDelete` | アクションシート | `log` | §5.3 |
| 8 | Notices（一覧） | `notices` タブ | タブ・長いリスト（スクロール） | `notice` | §5.5 |
| 9 | Notice Detail | Notices 行 | push | `notice` | §5.5 |
| 10 | Permissions | `permissions` タブ / `…://permissions` | タブ・**OS アラート** + ペーストボード往復 | `perm`、`sys` | §5.4 |

タブの並び（左から）：**Stable・Search・Log・Notices・Permissions**。

各タブ項目には、それ自体の名前空間のルート（`stable`、`search`、`log`、`notice`、`perm`）が識別子
として付いています。ドット（`.`）やハイフン（`-`）を含まないため、iOS、Compose、Views のいずれでも
同じ一つの `id` で選択できます（`scenarios/tabs.yaml`）。`-noax` 変種は、ほかの識別子と同様にこれも
持ちません。

### 5.1 タブ：Stable（`stable` / `horse` 名前空間）

`NavigationStack`（SwiftUI）/ `UINavigationController`（UIKit）。非同期ロード付きのカタログ一覧。

> **画面タイトルに id は付けません**。iOS 26 ではナビゲーションバーのタイトルがアクセシビリティ要素
> として参照できず（参照できるのはボタンだけです）、タブの画面は `<ns>.title` 識別子を**持ちません**。
> タイトルは `navigationTitle` / `titleView` で表示するだけです。シナリオは、画面固有の**コンテンツの
> リーフ**でその画面にいることを確認します（Stable なら `stable.row.1` や `stable.status`、Search なら
> `search.field`、Permissions なら `perm.requestNotif`）。詳細画面の `horse.title` / `notice.detail.title`
> は残ります。これらはナビタイトルではなく、本文に表示される実体の名前（実コンテンツ）だからです。

- `stable.refresh` — カタログを再取得するツールバー/ボタン（GET `SHOWCASE_API_URL` + `/horses`）。`stable.status` の value を `loading` → `done`/`error` にする
- `stable.status` — テキスト。`accessibilityValue` = `idle`/`loading`/`done`/`error`
- `stable.row.<horseId>` — カタログ行ごとに 1 つ。`<horseId>` はデータ由来（例 `stable.row.3`）。タップで Horse Detail を push。集合アサーションは `idMatches: "stable.row.*"` + `count` を使う
- `stable.empty` — カタログが空のときのみ表示。カタログは固定で空になりません（BE-0079）ので、これは防御的なマークアップで、showcase では到達しない状態です

**Horse Detail**（Stable の行をタップして push）：
- `horse.title` — 馬の名前
- `horse.id.value` — id、value にミラー
- `horse.fetch` — ボタン：詳細 GET（`/horses/<id>`）。`horse.status` value `loading`→`done`/`error`
- `horse.status` — 上記の value
- `horse.favorite` — トグル。`selected` trait が状態を反映。`horse.favorite.value`（`on`/`off`）にミラー
- **戻る** — 標準のシステム戻るボタン（ナビゲーションスタックが用意します）。バックエンドは OS 由来の id `BackButton` で引きます。アプリ定義の戻る id はありません。

### 5.2 タブ：Search（`search` 名前空間）

- `search.field` — 検索フィールド。同じカタログを name で大小無視フィルタ
- `search.row.<horseId>` — フィルタ後の行（Stable 行と同じ id 方式だが `search.` 名前空間）
- `search.count` — テキスト。`accessibilityValue` = マッチ数
- `search.empty` — `search.results-empty`。クエリが何にも一致しないとき表示
- `search.clear` — フィールドをクリア

### 5.3 タブ：Log（`log` 名前空間、フォーム＋モーダル）

入力コントロールとモーダル各様式をすべて行使するトレーニングログ作成画面。

- `log.note` — 複数行テキストフィールド
- `log.count` — 数値ステッパー。`log.count.value` が数値をミラー
- `log.intense` — ボタン式トグル「Intense」（廃止済みの idb バックエンドは iOS 26 では素の Toggle/UISwitch を切り替えられませんでした、BE-0290）。`log.intense.value` = `on`/`off`
- `log.segment.<one|two|three>` — ボタン式のセグメントコントロール（廃止済みの idb バックエンドは iOS 26 では native の `Picker(.segmented)` / `UISegmentedControl` を切り替えられませんでした、BE-0290）。選択中のボタンが `selected` トレイトを持ち、選択内容は `log.segment.value`（`one`/`two`/`three`、既定は `one`）にミラーされます
- `log.submit` — ボタン：note/count を JSON にして `SHOWCASE_HTTP_BASE` + `/post` へ POST。成功で `log.toast` を表示（約 1.2 秒で自動消滅 → `wait until gone` を行使）し、行を追加
- `log.status` — value `idle`/`loading`/`done`/`error`
- `log.row.<n>` — 投入済みエントリ

専用のジェスチャ標的（長押しとダブルタップ。結果をミラーするので、ジェスチャが届いたことをシナリオが
アサートできます。どちらもフォームの折り返しより下にあるので、実行時はまず画面内へスクロールします）：
- `log.longpress` — 長押し標的。`log.longpress.value` = `idle`/`pressed`
- `log.doubletap` — ダブルタップ標的。`log.doubletap.value` がタップ回数をミラー（`0`、`1`、…）

Log から到達するモーダル（4 つの提示様式）：
- `log.openFilter` → detent 付き **sheet**：`log.sheet.title`、`log.sheet.apply`、`log.sheet.close`
- `log.openGallery` → **fullScreenCover**：`log.cover.title`、`log.cover.close`
- `log.openDelete` → **アクションシート**（confirmationDialog / UIAlertController ではなく、素のボタンによる自前のオーバーレイ。廃止済みの idb バックエンドは iOS 26 ではアラートのアクションを駆動できませんでした、BE-0290）：選択肢 `log.dialog.archive`、`log.dialog.delete`（破壊的）、`log.dialog.cancel`。結果は `log.dialog.value`（`none`/`archive`/`delete`）にミラー
- `log.toast` — 上記の一過性トースト

### 5.4 タブ：Permissions（`perm` / `sys` 名前空間、**OS 連携画面**）

`NavigationStack`（SwiftUI）/ `UINavigationController`（UIKit）。**OS レベルのアラートを意図的に上げる唯一の
画面**（§7）です。alert-guard のフローへ直接到達できるよう、トップレベルのタブに昇格しました。加えて、アプリ内で
完結するペーストボードの往復を行う System セクションを持ちます。

- `perm.requestNotif` — ボタン → `UNUserNotificationCenter.requestAuthorization`。**SpringBoard の通知プロンプト**を上げる（プロセス外で、アプリ内のアクセシビリティ照会には見えず、run の vision alert guard が消すか、`dismissAlerts` で「Allow」を叩く）。
- `perm.notif.value` — `notDetermined`/`authorized`/`denied`
- `perm.notif.authorized` — 許可後にのみ表示される要素（run が待てる肯定条件を与える）
- `perm.requestLocation` — ボタン → `CLLocationManager.requestWhenInUseAuthorization`。**システムの位置情報プロンプト**を上げる（同じく SpringBoard）。
- `perm.location.value` — `notDetermined`/`authorizedWhenInUse`/`denied`

**System** — アプリ内で完結するペーストボードの往復で、バックエンドのアプリ内クエリでは本来観測できない状態をミラーします。
別プロセスが仕込んだペーストボードを読むと iOS のペースト許可プロンプトが出るため、このアプリ自身が書いた値を読み戻す
ことで往復をアプリ内に閉じています。
- `sys.copy` — 既知の文字列（`bajutsu-clip`）をペーストボードへ書くボタン
- `sys.paste` — ペーストボードを読み戻して `sys.paste.value` に入れるボタン
- `sys.paste.value` — ペーストされたテキスト。シナリオは `sys.copy` に続けて `sys.paste` を叩き、この値をアサートします

### 5.5 タブ：Notices（`notice` 名前空間。長いリスト → 詳細、スクロール先の要素）

`NavigationStack`（SwiftUI）/ `UINavigationController`（UIKit）が、**20 件**の静的な notice の縦リストを
持ちます。両アプリで同一にシードします（id は `1…20`、タイトルは「Notice `<id>`」）。リストは
**意図的に 1 画面より長く**してあり、下端の行は最初 *画面外* にあります。行は遅延描画されるため、画面外の
行は**スクロールして表示されるまでアクセシビリティツリーに現れません**。したがって `notice.row.20` は典型的な
**スクロール先の要素（scroll-to-element）**の対象になります。シナリオはリストを `swipe`（1 回の swipe で
画面に対する割合分スクロール、§6.2）して対象行が現れるまでスクロールし、それから `tap` します。データロードを伴う Stable
カタログとは別の、素朴なリスト → 詳細フローで、ナビゲーションやスクロール、crawl のきれいな対象です。

- `notice.row.<id>` — *表示中*の notice ごとに 1 つ（`notice.row.1` …。画面外の末尾はスクロール後にのみ現れる）。`<id>` はデータ由来。タップで Notice Detail を push。（`notice.row.*` に対する固定 `count` はアサートしないこと。ツリーにあるのは画面内の行だけで、端末依存です。）

**Notice Detail**（Notices の行をタップして push）：
- `notice.detail.title` — notice のタイトル（画面の識別要素。ナビタイトルには id を付けない）
- `notice.detail.body` — notice の本文
- **戻る** — 標準のシステム戻るボタン。バックエンドは OS 由来の id `BackButton` で引きます（§5.1 参照）。

## 6. 通信

アプリ内コレクタ連携を用います（iOS は BajutsuKit、Android は BajutsuAndroid）。

- **iOS** — アプリは **BajutsuKit** をリンクし、起動時に `BajutsuNet.startIfEnabled()` を呼ぶ
  （`BAJUTSU_COLLECTOR` が注入されない限り no-op）。以後すべてのリクエストはインターセプタを通るので、
  `network` 証跡と `mocks` がアプリ無改変で効く（[DESIGN §3.2](../../DESIGN.md)）。
- **Android** — アプリは **BajutsuAndroid** をリンクし、起動時に `BajutsuNet.configure(launchEnv)` を
  呼び、OkHttp クライアントに `BajutsuNet.interceptor()` を足す（BE-0283）。`network` 証跡は同じように
  効く。`mocks` は追随の課題で、捕捉は OkHttp 由来に限られる（`URLSession` 限定という制約の Android 版）。
- エンドポイント：カタログ GET `SHOWCASE_API_URL`（既定 `https://example.com`）、詳細 GET
  `<base>/horses/<id>`、ログ POST `SHOWCASE_HTTP_BASE/post`。各リクエストは状態を該当 `*.status` の
  `accessibilityValue` にミラーするので、シナリオはレスポンスを `wait` してからアサートできる。
- あるリクエストは秘密のヘッダ（`Authorization: Bearer …`）とボディフィールド（`password`）を意図的に
  載せ、redaction にマスク対象を与える（[DESIGN §9](../../DESIGN.md)）。

## 7. OS アラート方針（意図的で限定的）

> 要件：OS アラート（プッシュ通知許可、位置情報）は **既定で出さない**、そして **特定の画面でのみ** 出す。

- **起動時プロンプトなし。** 起動時に通知/位置情報の許可を要求せず、**Permissions** タブ（§5.4）の明示
  タップでのみ要求する。
- **意図的なアラート**は **Permissions** タブにのみ存在：通知プロンプトと位置情報プロンプト。どちらも
  SpringBoard（プロセス外）で、アプリ内のどのアクセシビリティ照会にも見えないので、run の **vision alert guard**
  / `dismissAlerts` の典型フィクスチャになります（このフィクスチャのシナリオは
  [`permission.yaml`](scenarios/permission.yaml)）。

## 8. `ACCESSIBLE` ビルドフラグ（変種が 1 コードベースを共有する仕組み）

Swift のアクティブコンパイル条件 `ACCESSIBLE` ただ 1 つを、`-a11y` ターゲットにのみ設定します。

ヘルパは Apple 自身の API（`accessibilityIdentifier` / `accessibilityValue`）を踏襲した名前にし、
それらをシャドウしません：`accessibilityID(_:)` が識別子を付け、`accessibilityStateValue(_:)` が状態を
`accessibilityValue` にミラーします。

**SwiftUI** — a11y ビルドでのみ識別子／値を付ける `View` ヘルパ：

```swift
extension View {
    /// a11y ビルドでは安定識別子を付け、それ以外では no-op。
    func accessibilityID(_ id: String) -> some View {
        #if ACCESSIBLE
        return AnyView(self.accessibilityIdentifier(id))
        #else
        return AnyView(self)
        #endif
    }

    /// 状態を accessibilityValue にミラー（a11y ビルドのみ）。アサーションが読めるように。
    func accessibilityStateValue(_ value: String) -> some View {
        #if ACCESSIBLE
        return AnyView(self.accessibilityValue(value))
        #else
        return AnyView(self)
        #endif
    }
}
```

**UIKit** — `UIAccessibilityIdentification`（`UIView`/`UIBarItem` が準拠）の拡張と、
`UIView`/`UIBarItem` への `accessibilityStateValue(_:)`：

```swift
extension UIAccessibilityIdentification {
    /// a11y ビルドでは安定識別子を付け、それ以外では no-op。
    @discardableResult func accessibilityID(_ id: String) -> Self {
        #if ACCESSIBLE
        accessibilityIdentifier = id
        #endif
        return self
    }
}
```

§5 のすべての識別子は `accessibilityID(...)` を通して付けます。状態をミラーする
`accessibilityStateValue(...)`（および**アサーション用**の `accessibilityLabel`）も同様に
`#if ACCESSIBLE` で囲みます。純粋に VoiceOver の意味付けのためだけの label は無条件のままでよいです。したがって `-noax` ビルドは、識別子もミラー値も
持たないツリーを提示します。アクセシビリティを省いたチームが出荷するアプリそのものであり、`record` が
立ち向かい、`doctor` が指摘すべき対象そのものです。

## 9. 識別子名前空間の一覧（`idNamespaces`）

`-a11y` アプリの `targets.<name>.idNamespaces`（[DESIGN §7.3](../../DESIGN.md)）用。予約（画面横断で
共有する）名前空間はありません。戻るは OS 由来のシステム戻るボタン（id `BackButton`）で、アプリの
名前空間の外です。

```
stable, horse, search, log, notice, perm, sys, net
```

`-noax` アプリは **空の** `idNamespaces: []` を宣言します。そのビルドが識別子を一切露出しないという
正直な宣言であり、これが `doctor --target showcase-…-noax` を `idCoverage` で **Blocked** にする（「通った
ように見える」のを防ぐ）所以です。

## 10. 各 Bajutsu コマンドがこのフィクスチャで示すもの

| コマンド | 変種 | ストーリー |
|---|---|---|
| `run` | `-a11y` | `scenarios/` の全シナリオの決定的再実行。タブ、push ナビ、4 つのモーダル様式すべて、通信（実＋モック）、alert-guard 付き Permissions フロー。 |
| `doctor --target` | 両方 | `-a11y` → **Ready**、`-noax` → **Blocked**（`idCoverage` ≈ 0）。この対がアクセシビリティ負債を定量化する。 |
| `record` | `-noax` | 識別子のないアプリに対し、自然言語ゴールから AI がシナリオを起こし、label/traits/座標へ落ちて、stability ladder の代償を可視化する。`-a11y` の双子は同じゴールのクリーンな id ベース出力を示す。 |
| `crawl`（[BE-0038](../../roadmaps/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)） | `-a11y` | 本当に枝分かれの多いアプリ（5 タブ × push × 4 モーダル様式）を幅優先探索 → 画面マップ。§5 の識別子が安定なので、id ベースの状態フィンガープリントも安定。（先行き：BE-0038 が入った時点で有効。） |
