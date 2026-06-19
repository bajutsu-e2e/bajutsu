[English](SPEC.md) · **日本語**

# Bajutsu Showcase — フィクスチャ仕様書

> showcase dogfood アプリ群の唯一の真実（single source of truth）です。UIKit 版と SwiftUI 版は
> 同じ識別子のまま *この仕様* を実装するので、1 つのシナリオ集がすべての変種を駆動します。アプリの
> ソースを変える前にこれを読んでください。設計の根拠は [`DESIGN.md`](../../DESIGN.md)、ロードマップ項目は
> `dogfood-showcase-apps` の BE 項目です。

## 1. 目的

showcase は Bajutsu の **次世代 dogfood 対象** です。`record`（Tier 1 オーサリング）、
`crawl`（Tier 1 探索・[BE-0038](../../roadmaps/proposals/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)）、
`run`（Tier 2 決定的ゲート）を実践するための土台です。実アプリが持つ操作面——タブ＋ナビゲーション＋
モーダルによる画面遷移、テキスト入力、ジェスチャ、非同期ロード、通信（実通信＋モック可能）、そして
意図的に OS レベルのアラートを上げる画面——を、その全体を語れる最小のアプリに収めています。

これは旧 `sample` フィクスチャ（[`demos/features/app`](../features/app)）を置き換えます。`sample` が
SwiftUI 1 本だったのに対し、showcase は **同じアプリを 2 回書き**（UIKit と SwiftUI）、**さらに各々を
アクセシビリティの 2 変種**で出します。これは Bajutsu の設計全体が依って立つ要点を可視化するためです。

| 変種 | アクセシビリティ識別子 | 何を示すか |
|---|---|---|
| **`*-a11y`**（識別子 ON） | すべての操作可能要素に安定した `accessibilityIdentifier` | `run` — id ベースのセレクタが一意に解決し、シナリオが決定的に再実行できる。`doctor --app` は **Ready** |
| **`*-noax`**（識別子 OFF） | 識別子を一切持たない | `record` — AI 著者は stability ladder（[DESIGN §5](../../DESIGN.md)）を下って `label`/`traits`/座標へ落ちる。`doctor --app` は **Blocked**。アクセシビリティを省いた代償を具体化する |

> **対比が肝心な理由**。セレクタの安定性が決定性のレバー（[DESIGN §2](../../DESIGN.md)）です。
> `-a11y` ↔ `-noax` の双子は対照実験です。同じアプリ・同じフロー、識別子だけが違う。同じゴールを
> `record` に両方へ通せば、その差分こそがアクセシビリティ作業の価値です。

## 2. アプリ・マトリクス（2 コードベース × 2 ビルド変種 = 4 プロダクト）

各ツールキットは **1 コードベース・2 ビルドターゲット** です。変種の差は Swift のアクティブコンパイル
条件 `ACCESSIBLE` ただ 1 つで、ソースの分岐はありません（§8）。

| アプリ名（`apps.<name>`） | ツールキット | `ACCESSIBLE` | Bundle id | Deeplink scheme | 表示名 |
|---|---|---|---|---|---|
| `showcase-swiftui` | SwiftUI | 定義 | `com.bajutsu.showcase.swiftui` | `showcaseswiftui` | Showcase SwiftUI |
| `showcase-swiftui-noax` | SwiftUI | — | `com.bajutsu.showcase.swiftui.noax` | `showcaseswiftuinoax` | Showcase SwiftUI (no a11y) |
| `showcase-uikit` | UIKit | 定義 | `com.bajutsu.showcase.uikit` | `showcaseuikit` | Showcase UIKit |
| `showcase-uikit-noax` | UIKit | — | `com.bajutsu.showcase.uikit.noax` | `showcaseuikitnoax` | Showcase UIKit (no a11y) |

2 つの `-a11y` アプリは、識別子集合・launch-env フック・deeplink を **完全に同一**（byte-for-byte）に
露出しなければなりません。これにより `demos/showcase/scenarios/*.yaml` がどちらにも無改変で通ります。
UIKit と SwiftUI のビュー構築は違ってよいですが、以下の契約は違えてはいけません。

## 3. 起動環境フック

`launchEnv`（[DESIGN §6.1](../../DESIGN.md)）で注入します。すべて起動時に `ProcessInfo` から一度だけ読みます。接頭辞は `SHOWCASE_`。

| 変数 | 効果 | 既定 |
|---|---|---|
| `SHOWCASE_UITEST` | アニメーション無効化（条件待機を締める） | 未設定 |
| `SHOWCASE_SKIP_ONBOARDING` | オンボーディングを飛ばしログインから開始 | 未設定 |
| `SHOWCASE_LOGGED_IN` | ログイン済み・Home から開始 | 未設定 |
| `SHOWCASE_TAB` | 初期タブ：`stable`/`search`/`log`/`profile` | `stable` |
| `SHOWCASE_SEED` | シードするカタログ行数（オフライン） | `5` |
| `SHOWCASE_API_URL` | カタログ GET（`/horses`）の base URL | `https://example.com` |
| `SHOWCASE_HTTP_BASE` | エコー用 POST/DELETE エンドポイントの base | `https://httpbin.org` |

> オンボーディングとログインは **抑止可能** なので、多くのシナリオは対象画面から直接始められます。
> `sample` フィクスチャと同じクリーン状態注入です。

## 4. Deeplink

scheme は変種ごと（§2）、host 文法は共通です。どの deeplink を開いてもモーダルは閉じ、ナビゲーションは
タブのルートまで戻ります。

| Deeplink（host） | 効果 |
|---|---|
| `…://stable` / `search` / `log` / `profile` | そのタブを選択 |
| `…://horse/<id>` | Stable タブを選択し、`<id>` の Horse Detail を push |
| `…://permissions` | Profile タブを選択し、Permissions 画面（OS アラート画面）を push |

## 5. 画面別仕様

5 つのフロー：**オンボーディング → ログイン**（モーダルのゲート）、その後 4 タブのメイン UI。操作可能
要素の識別子をすべて列挙します。`-noax` 変種はそれらをすべて省きます。識別子は
[DESIGN §7.3](../../DESIGN.md) に従い `<namespace>.<element>`、小文字、データ由来、画面内一意です。
状態は（`-a11y` では）`accessibilityValue` にミラーし、アサーションが読めるようにします。

### 5.0 認証ゲート — `onboarding` / `auth` 名前空間

`screen != home` の間、メイン UI の上に被さるモーダル（`fullScreenCover` 相当）です。`sample` アプリの
`AuthFlowView` と同じ構造です。

**オンボーディング**（`SHOWCASE_SKIP_ONBOARDING`/`SHOWCASE_LOGGED_IN` 設定時は飛ばす）：
- `onboarding.title` — 見出し「Welcome」
- `onboarding.continue` — ボタン → ログインへ進む

**ログイン：**
- `auth.email` — email テキストフィールド
- `auth.password` — セキュアフィールド。**`textContentType = .password`/`.newPassword` を設定しない**こと。これが iOS の「パスワードを保存しますか？」システムシートを抑止します（§7）。
- `auth.submit` — ボタン。email か password が空 → `auth.error` を表示。そうでなければキーボードを閉じて Home へ。
- `auth.error` — バリデーションメッセージ（存在時のみ）

### 5.1 タブ：Stable — `stable` / `horse` 名前空間

`NavigationStack`（SwiftUI）/ `UINavigationController`（UIKit）。非同期ロード付きのカタログ一覧。

- `stable.title` — ナビタイトル「Stable」
- `stable.refresh` — カタログを再取得するツールバー/ボタン（GET `SHOWCASE_API_URL` + `/horses`）。`stable.status` の value を `loading` → `done`/`error` にする
- `stable.status` — テキスト。`accessibilityValue` = `idle`/`loading`/`done`/`error`
- `stable.row.<horseId>` — カタログ行ごとに 1 つ。`<horseId>` はデータ由来（例 `stable.row.3`）。タップで Horse Detail を push。集合アサーションは `idMatches: "stable.row.*"` + `count` を使う
- `stable.empty` — カタログが空のときのみ表示（`SHOWCASE_SEED=0` かつネットワーク行なし）

**Horse Detail**（push。`…://horse/<id>` でも到達可能）：
- `horse.title` — 馬の名前
- `horse.id.value` — id、value にミラー
- `horse.fetch` — ボタン：詳細 GET（`/horses/<id>`）。`horse.status` value `loading`→`done`/`error`
- `horse.status` — 上記の value
- `horse.favorite` — トグル。`selected` trait が状態を反映。`horse.favorite.value`（`on`/`off`）にミラー
- `nav.back` — 戻るボタン（予約名前空間 `nav`。システム戻るボタンに明示的にこの id を付ける）

### 5.2 タブ：Search — `search` 名前空間

- `search.title` — ナビタイトル「Search」
- `search.field` — 検索フィールド。同じカタログを name で大小無視フィルタ
- `search.row.<horseId>` — フィルタ後の行（Stable 行と同じ id 方式だが `search.` 名前空間）
- `search.count` — テキスト。`accessibilityValue` = マッチ数
- `search.empty` — `search.results-empty`。クエリが何にも一致しないとき表示
- `search.clear` — フィールドをクリア

### 5.3 タブ：Log — `log` 名前空間（フォーム＋モーダル）

入力コントロールとモーダル各様式をすべて行使するトレーニングログ作成画面。

- `log.title` — ナビタイトル「Log」
- `log.note` — 複数行テキストフィールド
- `log.count` — 数値ステッパー。`log.count.value` が数値をミラー
- `log.intense` — トグル「Intense」。`log.intense.value` = `on`/`off`
- `log.submit` — ボタン：note/count を JSON にして `SHOWCASE_HTTP_BASE` + `/post` へ POST。成功で `log.toast` を表示（約 1.2 秒で自動消滅 → `wait until gone` を行使）し、行を追加
- `log.status` — value `idle`/`loading`/`done`/`error`
- `log.row.<n>` — 投入済みエントリ

Log から到達するモーダル（4 つの提示様式）：
- `log.openFilter` → detent 付き **sheet**：`log.sheet.title`、`log.sheet.apply`、`log.sheet.close`
- `log.openGallery` → **fullScreenCover**：`log.cover.title`、`log.cover.close`
- `log.openDelete` → **confirmationDialog / アクションシート**：選択肢 `log.dialog.archive`、`log.dialog.delete`（破壊的）、`log.dialog.cancel`。結果は `log.dialog.value`（`none`/`archive`/`delete`）にミラー
- `log.toast` — 上記の一過性トースト

### 5.4 タブ：Profile — `profile` / `account` / `perm` / `about` 名前空間（ナビゲーション＋OS アラート）

サブ画面を push する `Form`/グループドリスト——ナビゲーションの深さのショーケース。

- `profile.title` — ナビタイトル「Profile」
- `profile.normalize` — トグル「Normalize」。`profile.normalize.value` = `on`/`off`。切り替えると `profile.changed` をセット
- `profile.changed` — 設定変更後に表示されるテキスト
- `profile.openAccount` → **Account** を push
- `profile.openPermissions` → **Permissions**（OS アラート画面）を push
- `profile.openAbout` → **About** を push

**Account**（`account`）：
- `account.title`、`account.email.value`（ログイン中の email をミラー）、`account.logout`（→ ログインゲートへ戻る）

**Permissions**（`perm`）— **OS レベルのアラートを意図的に上げる唯一の画面**（§7）：
- `perm.title`
- `perm.requestNotif` — ボタン → `UNUserNotificationCenter.requestAuthorization`。**SpringBoard の通知プロンプト**を上げる（プロセス外。idb は見えない——run の vision alert guard が消すか、`dismissAlerts` で「Allow」を叩く）。
- `perm.notif.value` — `notDetermined`/`authorized`/`denied`
- `perm.notif.authorized` — 許可後にのみ表示される要素（run が待てる肯定条件を与える）
- `perm.requestLocation` — ボタン → `CLLocationManager.requestWhenInUseAuthorization`。**システムの位置情報プロンプト**を上げる（同じく SpringBoard）。
- `perm.location.value` — `notDetermined`/`authorizedWhenInUse`/`denied`

**About**（`about`）：
- `about.title`、`about.version.value`、`nav.back`

## 6. 通信

`sample` フィクスチャの BajutsuKit 連携をそのまま踏襲します。

- アプリは **BajutsuKit** をリンクし、起動時に `BajutsuNet.startIfEnabled()` を呼ぶ（`BAJUTSU_COLLECTOR`
  が注入されない限り no-op）。以後すべてのリクエストはインターセプタを通るので、`network` 証跡と `mocks`
  がアプリ無改変で効く（[DESIGN §3.2](../../DESIGN.md)）。
- エンドポイント：カタログ GET `SHOWCASE_API_URL`（既定 `https://example.com`）、詳細 GET
  `<base>/horses/<id>`、ログ POST `SHOWCASE_HTTP_BASE/post`。各リクエストは状態を該当 `*.status` の
  `accessibilityValue` にミラーするので、シナリオはレスポンスを `wait` してからアサートできる。
- あるリクエストは秘密のヘッダ（`Authorization: Bearer …`）とボディフィールド（`password`）を意図的に
  載せ、redaction にマスク対象を与える（[DESIGN §9](../../DESIGN.md)）。

## 7. OS アラート方針（意図的・限定的）

> 要件：OS アラート（プッシュ通知許可、パスワード保存）は **既定で出さない**、そして **特定の画面でのみ** 出す。

- **起動時プロンプトなし。** 起動時に通知/位置情報の許可を要求しない——**Permissions** 画面（§5.4）の明示
  タップでのみ要求する。
- **パスワード保存シートなし。** ログインのセキュアフィールドは `textContentType = .password`/
  `.newPassword` を **設定せず**、フォームを AutoFill 認識のログインにしないので、iOS は「パスワードを
  保存しますか？」を出さない。これが意図的な *抑止* です。
- **意図的なアラート**は **Permissions** にのみ存在：通知プロンプトと位置情報プロンプト。どちらも
  SpringBoard（プロセス外）で idb のアプリスコープな query には見えないので、run の **vision alert guard**
  / `dismissAlerts` の典型フィクスチャになります（既存の前例は
  [`permission.yaml`](../features/app/scenarios/permission.yaml)）。

## 8. `ACCESSIBLE` ビルドフラグ（変種が 1 コードベースを共有する仕組み）

Swift のアクティブコンパイル条件 `ACCESSIBLE` ただ 1 つを、`-a11y` ターゲットにのみ設定します。

**SwiftUI** — a11y ビルドでのみ識別子を付ける `View` ヘルパ：

```swift
extension View {
    /// a11y ビルドでは安定識別子を付け、それ以外では no-op。
    func aid(_ id: String) -> some View {
        #if ACCESSIBLE
        return AnyView(self.accessibilityIdentifier(id))
        #else
        return AnyView(self)
        #endif
    }
}
```

**UIKit** — `UIAccessibilityIdentification`（`UIView`/`UIBarItem` が準拠）の拡張：

```swift
extension UIAccessibilityIdentification {
    /// a11y ビルドでは安定識別子を付け、それ以外では no-op。
    @discardableResult func aid(_ id: String) -> Self {
        #if ACCESSIBLE
        accessibilityIdentifier = id
        #endif
        return self
    }
}
```

§5 のすべての識別子は `aid(...)` を通して付けます。**アサーション用に状態をミラーする**
`accessibilityValue`/`accessibilityLabel` も同様に `#if ACCESSIBLE` で囲みます。純粋に VoiceOver の
意味付けのためだけの label は無条件のままでよいです。したがって `-noax` ビルドは識別子もミラー値も
持たないツリーを提示します——アクセシビリティを省いたチームが出荷するアプリそのものであり、`record` が
立ち向かい、`doctor` が指摘すべき対象そのものです。

## 9. 識別子名前空間の一覧（`idNamespaces`）

`-a11y` アプリの `apps.<name>.idNamespaces`（[DESIGN §7.3](../../DESIGN.md)）用。予約共有名前空間
`auth` と `nav` は `defaults.reservedNamespaces` から来ます。

```
onboarding, auth, nav, stable, horse, search, log, profile, account, perm, about, net
```

`-noax` アプリは **空の** `idNamespaces: []` を宣言します——そのビルドが識別子を一切露出しないという
正直な宣言であり、これが `doctor --app showcase-…-noax` を `idCoverage` で **Blocked** にする（「通った
ように見える」のを防ぐ）所以です。

## 10. 各 Bajutsu コマンドがこのフィクスチャで示すもの

| コマンド | 変種 | ストーリー |
|---|---|---|
| `run` | `-a11y` | `scenarios/` の全シナリオの決定的再実行——タブ、push ナビ、4 つのモーダル様式すべて、通信（実＋モック）、alert-guard 付き Permissions フロー。 |
| `doctor --app` | 両方 | `-a11y` → **Ready**、`-noax` → **Blocked**（`idCoverage` ≈ 0）。この対がアクセシビリティ負債を定量化する。 |
| `record` | `-noax` | 識別子のないアプリに対し、自然言語ゴールから AI がシナリオを起こし、label/traits/座標へ落ちる——stability ladder の代償を可視化。`-a11y` の双子は同じゴールのクリーンな id ベース出力を示す。 |
| `crawl`（[BE-0038](../../roadmaps/proposals/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)） | `-a11y` | 本当に枝分かれの多いアプリ（4 タブ × push × 4 モーダル様式）を幅優先探索 → 画面マップ。§5 の識別子が安定なので、id ベースの状態フィンガープリントも安定。（先行き：BE-0038 が入った時点で有効。） |
