[English](../drivers.md) · **日本語**

# ドライバ抽象、バックエンド、環境管理

> ひとつの `Driver` インターフェースの裏に[バックエンド](glossary.md#driver-backend-actuator-platform)（`xcuitest`（iOS Simulator）、`adb`（Android
> エミュレータ）、`playwright`（web ブラウザ）、それにテスト用のインメモリ `fake`）を置き、能力差を
> 抽象側で吸収します。プラットフォーム対応のレジストリが `backend` リストから actuator を選びます。
> iOS ではアプリの起動（boot/launch）を `simctl` ラッパが担い、Android ではその双子である `adb`
> ラッパが担います。
>
> 実装: `bajutsu/drivers/`（`base.py` / `xcuitest.py` / `adb.py` / `playwright.py` / `fake.py`）・
> `bajutsu/backends.py` ・ `bajutsu/simctl.py` ・ `bajutsu/adb.py`。

関連: [selectors](selectors.md)（解決） · [concepts の安定度順ラダー](concepts.md#5-安定度順ラダーstability-ladder) · [run-loop](run-loop.md)

---

## Driver Protocol

すべてのバックエンドが満たす共通インターフェースです（`base.py`、`runtime_checkable` な `Protocol`）。
**操作（tap/type/swipe/wait/query）は actuator のみが行います**。

```python
class Driver(Protocol):
    def query(self) -> list[Element]: ...           # 画面の要素ツリー
    def tap(self, sel: Selector) -> None: ...
    def tap_point(self, p: Point) -> None: ...       # 生座標 tap（システムアラート等）
    def long_press(self, sel: Selector, duration: float) -> None: ...
    def swipe(self, frm: Point, to: Point) -> None: ...    # 素のポインタドラッグ（座標形）
    def scroll(self, frm: Point, to: Point) -> None: ...   # 方向指定のスクロール（BE-0227）
    def type_text(self, text: str) -> None: ...
    def wait_for(self, sel: Selector) -> bool: ...   # 単発チェック：現在の画面に一致するか
    def screenshot(self, path: str) -> None: ...
    def capabilities(self) -> set[str]: ...          # 提供能力（actuator / フォールバック解決用）
```

> **`wait_for` について**: 契約上、単発チェックです（BE-0118）。現在の画面を一度だけ確認して返し、ループはしません。締め切りまでのポーリングは共有ヘルパ `base.wait_until` に集約してあり、呼び出し側が渡す `timeout` は、どの backend でも同じ実時間を意味します。各ドライバがそれぞれのループを持つことはありません。run ループ自身の条件待機は、orchestrator が `query()` を直接ポーリングして行います（`_wait`、[run-loop](run-loop.md#待機条件待機)）。したがって `wait_until` を使うのは、このループの外側の呼び出し側（たとえば `golden_assert`）だけです。

### 能力（`Capability`）

`capabilities()` が返すトークン集合で、actuator 選択、証跡のフォールバック解決、**プリフライト能力検査**（後述）に使います。

| 能力 | 意味 | xcuitest | adb | playwright | fake |
|---|---|:--:|:--:|:--:|:--:|
| `query` | 要素ツリー取得 | ✅ | ✅ | ✅ | ✅ |
| `elements` | 要素ダンプ証跡 | ✅ | ✅ | ✅ | ✅ |
| `screenshot` | スクリーンショット | ✅ | ✅ | ✅ | ✅ |
| `semanticTap` | id/label で直接タップ（座標不要） | ✅ | — | ✅ | ✅ |
| `conditionWait` | ネイティブ条件待機 | ✅ | — | ✅ | ✅ |
| `network` | ネイティブネットワーク監視 | — | — | ✅ | — |
| `multiTouch` | 2 本指ジェスチャ（pinch / rotate） | ✅ | ✅ | ✅ | ✅ |
| `textSelection` | フォーカス中フィールドの全選択とクリップボードへのコピー | ✅ | ✅ | ✅ | ✅ |
| `deviceControl.setLocation` | 疑似 GPS 位置の設定 | ✅ | ✅ | — | — |
| `deviceControl.clipboard` | クリップボードの読み取り / 書き込み / クリア | ✅ | ✅ | — | — |
| `deviceControl.push` | プッシュ通知の配信 | ✅ | — | — | — |
| `deviceControl.clearKeychain` | キーチェーンのクリア | ✅ | — | — | — |
| `deviceControl.appLifecycle` | アプリのバックグラウンド化 / フォアグラウンド化 | ✅ | — | — | — |
| `deviceControl.statusBar` | ステータスバーの上書き / クリア | ✅ | — | — | — |

> `deviceControl.*` トークンは、`DeviceControl` 一族を操作ごとに分割したものです（BE-0212、BE-0128 の粗い `deviceControl` から分割しました）。これにより、バックエンドは自分が忠実に実現できる操作だけを公開でき、部分的にしか実現しないバックエンドも残りを誤って公開せずに済みます。XCUITest は `simctl` を通じて一族全体を実現しますが、Android エミュレータは `setLocation` と `clipboard` だけを実現します（その `push` / キーチェーン / ステータスバー / アプリライフサイクルの各操作には忠実な等価物がありません）。

> adb は**リーンな端**に位置し、**frame 中心の座標**で操作します。semantic tap を持たないため、run ループは `query()` で要素を一意に確定しその中心をタップします。一方 XCUITest は豊かな端に位置し、identifier で直接 tap し、ネイティブの条件で待機し、`pinch` / `rotate` をネイティブに実行します。adb は `query` / `elements` / `screenshot`、`multiTouch`（rooted device での `sendevent` 2 本指スイープ、BE-0232）に加えて、エミュレータが実現できるデバイス制御のサブセット `deviceControl.setLocation` と `deviceControl.clipboard` を公開します（BE-0211）。デバイス制御一族の残りにはエミュレータでの忠実な等価物がないため、公開しません。`fake` ドライバはテストでそれらのコードパスを動かすためだけに、より広い能力集合（semanticTap / conditionWait / multiTouch）を公開します。`playwright`（web）ドライバは `semanticTap` / `conditionWait`（Playwright がネイティブに持つ）に加えて `network`（アプリ側の協力なしに通信を観測しスタブできる**初めてのネイティブネットワーク対応バックエンド**）と `multiTouch`（Chromium DevTools プロトコルの `Input.dispatchTouchEvent` で pinch / rotate を合成）を公開します（BE-0054）。

### プリフライト能力検査（BE-0082）

バックエンドの能力集合は静的なので、選んだ actuator が持たない能力をシナリオが必要とするかどうかは、デバイス作業の前に分かります。run の開始時（actuator を選んだ後、最初のデバイスを lease する前）に、runner は各シナリオを actuator の能力と照合し（`bajutsu/capability_preflight.py`）、未対応のシナリオを即座に失敗させます。集約した 1 つの理由（`UnsupportedAction` 相当）を付けて、デバイスを起動して途中で失敗するのを避けます（prime directive #2：速く明確に失敗する）。検査は (シナリオ, 能力集合) の純粋関数で、デバイスも時計も使いません。シナリオ単位なので、未対応のシナリオだけが失敗し、残りは実行されます。

検査は、能力集合で明確に判定できる**真の hard requirement** だけを門にします。`pinch` / `rotate` は `multiTouch`、`select` / `copy` は `textSelection`（全選択とクリップボードへのコピー。web コンテキストはこれらについて座標専用でどちらも拒否します。`delete` / `clear` はどのバックエンドも `delete_text` を実現できるので門にしません）、`visual` アサーションは `screenshot`、そして各デバイス制御ステップは自分の操作に対応するトークンを必要とします。`setLocation` は `deviceControl.setLocation`、クリップボードのステップは `deviceControl.clipboard`、`push` は `deviceControl.push`、という具合です（この操作ごとのトークンへの分割は BE-0212、前掲）。すべての run は `query` と `elements` を必要とします。`relaunch` はここに含みません。`relaunch` は `DeviceControl` ではなく、注入される relauncher で門を通すからです。一方、`conditionWait` は門にしません（run ループはすべての待機を polling で実装するので、どのバックエンドもこのトークンを必要としません）。`network` も門にしません（XCUITest は `network` を公開しませんが、アプリ側の collector で通信を捕捉するため、`request` / `event` / `requestSequence` / `responseSchema` アサーションや `until: { request }` 待機は iOS でも動きます）。`gestures.py` の `_require_multi_touch` は、ジェスチャ実行時の多層防御の検査として残します。デバイス制御ステップについても同様に `_need_control` を残し、その run で `DeviceControl` がまったく配線されていない場合（デバイスを固定しない並行 run など）を捕捉します。トークンが操作ごとになったので、一族の一部だけを実現するバックエンド（Android エミュレータの `setLocation` と `clipboard`）は、公開した操作については preflight を通し、残りについては速く失敗します。未対応のステップは一つずつ名指しされ、一族が全か無かで扱われることはありません。

## XCUITest（iOS）

[BE-0290](../../roadmaps/BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md) で idb を撤去して以来、**iOS の唯一の backend** です。**XCTest のオートメーションスナップショット**を、**実機上に常駐する runner**（`BajutsuKit`）を loopback HTTP で駆動して読み取り、アプリ側の統合なしに任意のアプリを bundle id で駆動します。実装: `drivers/xcuitest.py`。frame 中心の座標に解決するのではなく、能力モデルの豊かな端に位置します（semantic tap、ネイティブ条件待機、multi-touch、テキスト選択）。Xcode の `xcodebuild` が必要です。

- `query()`: XCTest のオートメーションスナップショットを読み取り、各要素を `Element` に写します。このスナップショットは**グループコンテナの内側まで降りる**ので、座標系 backend のフラットな frame ダンプと違い、**完全に展開された要素ツリー**を描き出します（`AXLabel`/`AXValue`/アクセシビリティ識別子 を `label`/`value`/`id` に写します）。
- `tap(sel)`: `_resolve` で一意確定します（**not-found はリトライ、ambiguity は即失敗**: 実機ツリーは遷移中に一時的に空になり得るため）。確定後、要素を**アクセシビリティ識別子で直接** tap します（座標を経由しない semantic tap。BE-0289 はスタックしたスナップショットハンドルを再解決し、依然として一意に一致するときだけ再操作します）。
- `wait_for`: runner のネイティブな条件待機を使います。
- `pinch` / `rotate`: runner がネイティブに実行する 2 本指の multi-touch ジェスチャです。
- `select` / `copy`: フォーカス中フィールドのネイティブなテキスト選択です。
- `screenshot`: `simctl io screenshot`。

> 汎用の runner は `XCUIApplication(bundleIdentifier:)` を使うので、アプリ側の協力なしにインストール済みの任意のアプリを駆動します。target は事前ビルドの runner を `xcuitest.testRunner` で指定し、showcase の config は `make runner-build` でこれをビルドします。この backend は `make -C demos/showcase run-swiftui` ＋ `ios-e2e.yml` CI ワークフローで**実機検証済みです**（iPhone 17 Pro、最近の iOS）。XCUITest backend は pip extra を必要とせず、`xcodebuild` は Xcode が供給します。

## adb（Android）

ヘッドレスで座標ベースの、唯一の座標系バックエンドです。semantic tap を持たないため、抽象側で **id → frame 中心 → 座標 tap** に解決します。実装: `drivers/adb.py` ＋ `bajutsu/adb.py`（ロードマップ [BE-0007](../../roadmaps/BE-0007-android-backend/BE-0007-android-backend-ja.md)）。

- `query()`: ウィンドウの UI Automator XML を読み取り、純粋なパーサ（`parse_hierarchy`）が各 `<node>` を `Element` に写します。読み取りは、**常駐 UI Automator サーバ**がビルド済み（`make -C BajutsuAndroidUIAutomatorServer build`）のときはそのサーバ経由で行います。温めた 1 つの `UiAutomation` セッションが `adb forward` 越しに `GET /source` へ答えるので、1 回の読み取りは約 0.1〜0.3 秒で済み、呼び出しのたびに約 2.4 秒かかる `adb -s <serial> exec-out uiautomator dump /dev/tty` を都度起動せずに済みます（ロードマップ [BE-0245](../../roadmaps/BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)）。常駐サーバの全画面ダンプはアクティブウィンドウへ絞り込むので、ダンプ経路と同じ `Element` を返します。サーバが未ビルドのとき、またはチャネルに失敗したときは `uiautomator dump` にフォールバックし、`BAJUTSU_ADB_RESIDENT`（`0`／`1`）でどちらの経路にも固定できます。
- **セレクタの写像**: `resource-id` → `identifier`（`<package>:id/` 接頭辞を剥がしてローカル名にするので、`testTagsAsResourceId` で表出した Compose の `testTag` はそのまま、ネイティブの `android:id` は接頭辞が落ちます）、`text` → `label`（`content-desc` へフォールバック）、`content-desc` → `value`（アプリは状態値をここにミラーします。SPEC §2.1）、ウィジェットの `class`（と enabled / selected / checked の状態）→ `traits` です。
- **id 照合ポリシー**: ローカル名は**厳密一致**で照合します。ドライバは `.`↔`_` の書き換えをしません。書き換えは別々の id を取り違え、決定性を損なうためです。プラットフォーム本来の id 構文が SPEC の id をそのまま再現できない場合（Android Views の `android:id` は `.` も `-` も許さず、`stable.refresh` は `stable_refresh` として現れます）は、シナリオが 1 つのセレクタに id を**両方の形**で持ち（`id: [stable.refresh, stable_refresh]`）、候補の OR として照合します（BE-0221）。[scenarios](scenarios.md#selectors-addressing-an-element) を参照してください。
- `tap(sel)`: `_resolve` で一意確定します（**not-found はリトライ、ambiguity は即失敗**。遷移中のダンプは一時的な null-root としてリトライし、2 件以上の一致は即座に失敗させます）。確定後、frame 中心へ `adb shell input tap` を送ります。`swipe` は実際のドラッグにするため有限の duration を付け、`long_press` は同じ点を duration だけ保持する swipe、`type_text` は `input text`（空白はその `%s` エスケープで送ります）です。
- **実機アクチュエーションの忠実度**（ロードマップ [BE-0210](../../roadmaps/BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)）: `back` ステップは真のシステムバック（`input keyevent 4`、`KEYCODE_BACK`）です。Android にはタップできる画面上の戻る要素がなく、この点が iOS の OS 戻るボタンと異なります。`double_tap` は 2 回のタップを**単一の `adb shell` 往復**（`input tap … ; input tap …`）で発行するので、adb の転送往復がタップの間に挟まって double-tap ウィンドウを超えることがありません。タップの対象が**現在のビューポートにない**ときは、そちらへスクロール（既定は上方向のスワイプ）して再クエリし、回数で区切ります。固定 sleep ではなく条件待機なので、決して現れないセレクタはそれでも決定論的に失敗します。

  > [!NOTE]
  > scroll-into-view は現状 **adb 専用**の回復です。XCUITest／Playwright は対象が初期ビューポートに無いと `tap` を即座に失敗させます。そのため、同じシナリオでも fold より下の要素への `tap` が Android では（数回のスワイプののち）通り、iOS／web では失敗しうる非対称が生じます。移植可能な書き方は依然として**明示的な `swipe` ステップ**です（`demos/showcase/scenarios/notices.yaml` を参照）。adb の自動スクロールはその代替ではなく堅牢化のための安全網です。他バックエンドへの拡張は後続作業です（BE-0210 は adb に限定しました）。
- **マルチタッチ**（BE-0232）: `pinch` / `rotate` は 2 スロットの protocol-B `sendevent` スイープで駆動します（`pinch_contacts` / `rotate_contacts` が 2 接点の座標を計算し、`rotate` は両端点を結ぶ直線の弦を掃きます。円弧の線形近似で、web バックエンドの rotate と同じです）。rooted device でタッチスクリーンを検出できることが前提で、`_two_finger_gesture` はそれ以外では `UnsupportedAction` で明確に失敗します。下の double-tap 経路と異なり、単一タッチへのフォールバックはありません。`MULTI_TOUCH` は root の有無にかかわらず能力集合で静的に宣言するので、preflight は adb 上の `gestures_multitouch` を通します。root のチェックは能力集合ではなく実行時に課します。
- `screenshot` は `adb exec-out screencap -p` の PNG バイト列（バイナリを崩さない stdout）を書き出します。
- ライフサイクル（`AndroidEnvironment`、iOS の `simctl` シーケンスの双子）: 起動完了待ち（`getprop sys.boot_completed` を有界の期限まで polling する条件待機で、固定 sleep も、無期限にブロックする `adb wait-for-device` もありません）→ 必要に応じて APK インストール → `pm clear` によるクリーン状態（`erase` 相当）→ `am force-stop` → ランタイム権限の事前付与（`pm grant`、後述）→ `am start`（起動アクティビティはパッケージマネージャで解決し、launch env は intent extras として渡します）→ deeplink（`am start -a android.intent.action.VIEW`）。run の manifest は `backend: "adb"` を記録するので、選ばれた actuator が開示されます。
- **ランタイム権限**（BE-0210）: ターゲットの config `grantPermissions` に列挙した権限を、lease 時に `adb shell pm grant <package> <permission>` で事前付与します。`pm clear`（付与をリセットします）のあと、起動の前に付与するので、ランタイムの権限プロンプトがシナリオを止めることがありません。ダイアログが現れてからタップするのではなく、事前に決定論的に付与することで、タイミングを run の経路に持ち込みません。列挙する権限はアプリごとに異なるので、ドライバではなく config に置きます。
- **区間証跡**（BE-0007 の Unit 4）: `video` は `adb shell screenrecord` で録画し、`deviceLog` は `adb logcat` をストリームします。simctl の provider の双子です。`screenrecord` はデバイス側に書き込む（ホストのファイルへは流せない）ので、録画は停止時に SIGINT で確定させてから `adb pull` で回収し、`logcat` はファイルへストリームして SIGTERM で停止します。どちらも web バックエンドと同じ driver の `driver_interval` seam から供給するので、バックエンド非依存の `capture` ポリシーがそのまま両方を運びます（[evidence](evidence.md)を参照）。
- **ネットワーク**はネイティブには観測しません（`NETWORK` 能力を持ちません）。iOS と同じモックで対応し、アプリ側の collector の URL を launch env 経由で intent extra として渡すので、新しいコードパスなしに `mocks` が動きます。デバイス制御は、エミュレータが実現できるサブセットとして `setLocation`（`emu geo fix`、BE-0211）とクリップボード操作に対応します。一族の残りは未対応のままです。クリップボードは `cmd clipboard` ではなくアプリ内のレシーバ（`BajutsuAndroid`、BE-0233）を経由します。このコマンドは実機では黙って何もせず、Android 10 以降はフォアグラウンドのアプリと既定の IME しかクリップボードを触れないためです。そこで bajutsu は順序付き `am broadcast` を送り、アプリの内側のレシーバがアプリプロセスからこれを処理します（両方向を base64 で運ぶので argv に引用符付けは要らず、レシーバがなければ空のクリップを読むのではなく明確に失敗します）。XCUITest がクリップボードを simctl 経由で実現するのと同じく、協調するアプリがあればバックエンドが駆動できるので、adb は `clipboard` を公開し続けます。[`BajutsuAndroid`](../../BajutsuAndroid/README.ja.md) を参照してください。

> XML の属性名は UI Automator の `uiautomator dump` スキーマに従います。Views の `android:id` における `.`↔`_` の扱いはシナリオ側で解決します。セレクタが id を両方の形で持ち、どちらにもマッチします（BE-0221）。そのため共有の showcase シナリオが両 Android toolkit でそのまま走り、[`android-e2e.yml`](../.github/workflows/android-e2e.yml) が `showcase-compose` と `showcase-views` を同じセットで駆動して push／PR ごとに検証します。fast ゲートでは、取得済みの XML フィクスチャに対してパーサ、frame 中心タップ、transient-empty のリトライ、ambiguity 即失敗を検証します。adb は `brew install android-platform-tools` でインストールします。

## Playwright（web）

Playwright（Python）によるヘッドレス Chromium です。Mac も Simulator も要らず Linux で動くため、`make check` と同じツールチェーンに収まります。実装: `drivers/playwright.py`（ロードマップ [BE-0041](../../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）。

- `query()`: 1 本の `page.evaluate()` が、可視、操作可能、アクセシビリティ関連の DOM ノードを走査し、純粋なパーサ（`parse_dom`）が各ノードを `Element` に写像します。id 規約は iOS の accessibilityIdentifier の web 版です。`data-testid` → `Selector.id`、ARIA `role`（またはタグ）→ `traits`、accessible name / `aria-label` / テキスト → `label`、input の `value` → `value`。
- `tap(sel)`: adb バックエンドと同様、`query()` のスナップショットに対し共有の `resolve_unique`/`find_all` で要素を**一意に**確定し、**frame 中心**を座標クリック（`page.mouse.click`）します。Playwright 自身の `get_by_test_id().click()` は**あえて使いません**。これによりセレクタの意味が他のどの backend ともバイト単位で一致します。
- `type_text` は `page.keyboard` で入力します（オーケストレータが先に `into` をタップしてフィールドにフォーカスします）。`screenshot` は `page.screenshot`、`wait_for` は `find_all` による単発です（どの backend も同様で、締め切りまでのポーリングは共有ヘルパ `base.wait_until` が担います）。
- ライフサイクルは driver が所有します。新しい `BrowserContext` が `erase` 相当、`navigate()`（`page.goto(baseUrl)`）が `launch`、`close()` でブラウザを破棄します。simctl のデバイスは無いので、run はダミーのリースを使い、device control は持ちません。
- **デバイスモード**（BE-0228）: web ターゲットの `deviceMode` 設定が、各 `BrowserContext` の生成のしかたを選びます。`desktop`（既定で、素のデスクトップコンテキスト。従来と変わりません）か、Playwright のデバイスプリセット名（例 `iPhone 13`）です。プリセットは `playwright.devices` に対して解決し、その記述子（viewport / `device_scale_factor` / `is_mobile` / `has_touch` / `user_agent`）を `reduced_motion="reduce"` と並べて `new_context(**kwargs)` にマージするので、ターゲットをそのモバイル端末として駆動します。記述子は**遅延**して解決し（config 読み込みが Playwright を import することはありません）、記憶するので、`reset_context`（クロールのクリーンな起点）と `relaunch`（BE-0077）は同一のコンテキストを組み直します。モードはブラウザのライフサイクル全体を通して安定し、エンジンや `reduced_motion` が既に守っているのと同じ不変条件です。不明なプリセットはドライバー起動時に `ValueError` で明示的に失敗します。これは**デスクトップ級ブラウザでのエミュレーション**であり（デスクトップ級ブラウザの中でモバイルの viewport とタッチ入力を用いる、Chrome DevTools のデバイスツールバーが行うのと同じもの）、実機のモバイルブラウザやデバイスクラウドではありません。本物のモバイル OS が必要なら Android バックエンドが経路です。
- **方向指定の `swipe` はスクロールになる**（BE-0227）: 方向指定形式 `swipe: { on, direction }` の意味は「スクロール」であり、マウスドラッグは web ページをスクロールしません。そこで web バックエンドは、実際にスクロールを起こす入力プリミティブへ、コンテキストの入力モード（上記の `deviceMode`）に応じて振り分けます。**デスクトップ**（ポインター）コンテキストでは、ジェスチャーの起点で `page.mouse.wheel(...)` を発火します。wheel の移動量は travel の符号を反転したものなので、`up` の swipe はページを**下へ**スクロールさせ、トラックパッドやホイールとまったく同じ挙動になります。**タッチ**コンテキスト（モバイルの `deviceMode`）では、CDP による 1 本指の本物のタッチドラッグ（`pinch` / `rotate` と同じ経路）を使い、ページのタッチリスナとスクロールリスナが発火します。**座標**形式 `swipe: { from, to }` は変わりません。canvas やマップのパン、ドラッグハンドルのための素のドラッグの最終手段として、`page.mouse` のドラッグのままです。`codegen` も方向指定形式にはデスクトップの wheel スクロールを出力するので、生成された Playwright テストは、従来の何も動かないドラッグではなく、物理的に正しい向きへスクロールします（codegen には `amount` を掛ける viewport がないため、距離は既定の固定値です）。別途用意した `drag` アクション（要素アンカーのポインタドラッグ。リサイズ用の仕切りやスライダーなど）はドライバーの `swipe` に振り分けられるので、web では掴んだ要素を実際に**動かす** `page.mouse` のドラッグになります。スクロールするだけの方向指定 `swipe` とは対照的です。
- **マルチタッチ**（BE-0054）: `pinch` / `rotate` は Chromium DevTools プロトコル（`Input.dispatchTouchEvent`）で 2 本指のドラッグとして合成します。`mouse` は単一ポインタなので、ジェスチャは CDP 経由（実際のタッチと同じ経路）で送り、ページのタッチリスナが発火します。要素の中心を 2 本指の基準点とし、`scale` が指の間隔を広げ/狭め、`radians` がその中心まわりに回転させます。
- **ネイティブネットワーク**（BE-0054）: Playwright はページが出すすべてのリクエストを見られるので、`--network` はアプリ側の協力なしに web でも動きます。`network_collector()` がページの `requestfinished` イベントを iOS と同じ `NetworkExchange` に変換するため、`request` アサーションも `network.json` 証跡もそのまま使えます。シナリオの `mocks` は `page.route` でその場で fulfill します。一致したリクエストには既定のレスポンスを返し、`mocked: true` を立てて記録します。一致判定は決定論的な `request` マッチャを再利用し、モデルは一切使いません。
- **コンソール / ページエラー、動画の証跡**（BE-0054）: `deviceLog` キャプチャ種別はブラウザのコンソールと未捕捉のページエラーを `<scenario>/device.log` にストリームし、`video` はシナリオ全体を録画します。どちらも simctl ではなく Playwright ネイティブで、iOS の os_log / simctl 動画に相当します。録画はシナリオの `capture` に `video` がある時だけ有効化し（`BrowserContext` を `record_video_dir` 付きで生成）、`video` インターバルが context クローズ時に `<scenario>/scenario.mp4`（中身は webm）へ確定させます。プールがドライバの `driver_interval`（adb バックエンドと共有する、driver 供給の区間証跡 seam）を `FileSink` に注入するので、バックエンド非依存の同じ `capture` ポリシーが両方を運びます。

> `playwright` は**遅延 import** されます（実際にブラウザを起動するときだけ読み込む）。そのため既定の CLI パスには決して載りません（`tests/serve/test_import_guard.py` で固定）。インストールは `uv sync --extra web` ＋ `uv run playwright install chromium`。`demos/web` のデモ（`make -C demos/web e2e`）が小さな静的 web アプリを端から端まで駆動します。

## FakeDriver

実機なしで orchestrator / runner / record をテストするためのインメモリ実装です。実装: `drivers/fake.py`。

- `screen`（`Element` のリスト）を保持し、`query()` で返します。
- `tap` / `long_press` は本物同様 `resolve_unique` を通します（曖昧 / 不在は `SelectorError`）。
- `react` コールバックで「操作に応じて画面が変わる」動作をスクリプトできます。
- `actions` に実行した操作を記録します（検証用）。

```python
def react(driver, kind, arg):
    if kind == "tap":
        driver.screen = [...]  # タップ後の画面に差し替える
FakeDriver(screen=[...], react=react)
```

## バックエンド選択と actuator

実装: `bajutsu/backends.py`。

```python
PLATFORMS = {                              # プラットフォームトークンは actuator 列へ展開（安定度順）
    "ios":     ("xcuitest",),              #   BE-0290 で idb を撤去して以来、iOS の唯一の actuator
    "android": ("adb",),                   #   計画中
    "web":     ("playwright",),            #   実装済み（BE-0041）
    "fake":    ("fake",),                  #   メモリ上のテスト/デモ用ドライバ
}
COST_ORDER: dict[str, tuple[str, ...]] = {}  # 空。どのプラットフォームもコスト順が安定度順と食い違わない
IMPLEMENTED = {"fake", "playwright", "xcuitest"}  # 今日ドライバがある actuator

def default_available(actuator) -> bool:   # 実装済みかつ裏のツールがあるか（playwright はパッケージ import、fake は常に可）
def resolve_actuators(backends) -> list:   # 各トークン（プラットフォーム/actuator）を actuator 列へ展開
def select_actuator(backends, available) -> str:  # 安定度順で最初の「実装済み かつ 利用可能」
def select_actuator_cost_first(backends, available) -> str:  # シナリオ無しで、最も安い利用可能な actuator（BE-0267）
def select_actuator_for_scenario(backends, scenario, available, caps) -> str:  # 利用可能かつ十分な、最も安い actuator（BE-0240）
def make_driver(actuator, udid, *, base_url=None, runner_port=None) -> Driver:  # "xcuitest"→XcuitestDriver, "playwright"→PlaywrightDriver, "fake"→FakeDriver
```

- **バックエンドトークン**は、**プラットフォーム**（`ios` / `android` / `web` / `fake`）か、具体的な **actuator**（例: `xcuitest`）のどちらかです。現状はどのプラットフォームも単一の actuator に解決します。`ios` は `xcuitest`（BE-0290 で idb を撤去したので `--backend ios` と `--backend xcuitest` は等価）、`android` は `adb`、`web` は `playwright` です。**actuator を複数持つ**プラットフォームのための仕組み（シナリオごとのコスト順解決。BE-0240）は将来のプラットフォームに備えて残していますが、今それを使うプラットフォームはありません。
- 二つの順序が二つの問いに答えます。**安定度順**（`PLATFORMS`、最も高機能なものから。[concepts](concepts.md#5-安定度順ラダーstability-ladder)）は `select_actuator` を駆動します。これは、まだシナリオが手元に無くコストも問わない場面（`doctor`、プールの起動時セットアップ、明示的な単一 actuator の固定）で使う、可用性だけの選択です。**コスト順**（`COST_ORDER`、最も安いものから）は `select_actuator_for_scenario` と `select_actuator_cost_first` の両方を駆動し、両者は候補解決の前段（`_cost_ordered_available`）を共有します。`COST_ORDER` が空になった今、プラットフォームのコスト順は安定度順そのものなので、いずれも単一候補に落ち着きます。`select_actuator_for_scenario` はさらに `capability_preflight.unsupported`（BE-0082）を各候補の能力集合に対して再利用し、利用可能かつそのシナリオのステップに十分な最初の候補を返します。`select_actuator_cost_first` は同じコスト優先の選択をシナリオ無しで行うもので、能力の昇格判定なしに「立ち上げられる中で最も安い actuator」だけが必要な場面（serve の Author タブの **Capture** と **Enrich**。BE-0267）で使います。どちらも、解決した候補が 1 つに収まる場合は `select_actuator` に委譲し（その診断メッセージを保ちます）、現状はどのプラットフォームも単一 actuator なので常にそうなります。利用可能なものが無ければ `RuntimeError`（CLI は終了コード 2）。
- `web` は `playwright` に、`android` は `adb` に解決され、どちらも**実装済み**です
  （[vision → reach](vision.md#1-reachより多くのプラットフォームと面)）。本当に未知のトークンはスキップされます（前方互換: 古いビルドでも、将来のバックエンドを列挙した config を実行できます）。
- 可用性判定 `available` は注入可能です（テストで差し替え可）。既定は `shutil.which`（`fake` は実行ファイル不要で常に利用可能）。
- actuator は**シナリオごと**に 1 つ確定し、そのシナリオの実行のあいだ固定です（BE-0240）。どの瞬間もリースしたデバイスを操作する actuator はちょうど 1 つで、実行の途中で切り替わりません。これは従来の「run ごとに固定」という単位をシナリオ単位へ狭めたもので、単一 actuator の規則を緩めるものではありません。

操作は単一の actuator にとどまります。リスト内の非 actuator バックエンドは、**read-only な証跡フォールバック**として機能します（DESIGN §9、[BE-0020](../../roadmaps/BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback-ja.md)）。その種別の provider には、actuator が欠く能力（例: `Capability.NETWORK`）を `capabilities()` で表明する同一プラットフォームのバックエンドを充てます。フォールバックには狭い `EvidenceProvider` Protocol 経由でのみアクセスし、tap/type/swipe は型レベルで不可能です。gap を埋めるバックエンドが無い場合は、理由を記録して skip します（`SkippedCapture`）。なだらかな劣化であり、run の失敗にはなりません。来歴の詳細は[証跡の provider](evidence.md#アーティファクトの来歴provider)を参照してください。

## 環境管理（simctl）

実装: `bajutsu/simctl.py`。コマンドビルダは純関数（単体テスト済み）で、実行は注入可能な `RunFn` 経由です。

| メソッド | コマンド | 備考 |
|---|---|---|
| `erase()` | `simctl erase <udid>` | クリーン環境 |
| `boot()` | `simctl boot <udid>` | 既に boot 済みなら冪等（エラーを握りつぶす） |
| `launch(bundle, args, env)` | `simctl launch --terminate-running-process <udid> <bundle> <args>` | env は `SIMCTL_CHILD_*` で注入 |
| `terminate(bundle)` | `simctl terminate <udid> <bundle>` | 未起動でも無視 |
| `openurl(url)` | `simctl openurl <udid> <url>` | deeplink |
| `screenshot(path)` | `simctl io <udid> screenshot <path>` | — |

> **launch env の注入**: アプリへ渡す env 変数は、親プロセスに `SIMCTL_CHILD_<NAME>` として設定すると子（アプリ）に `<NAME>` で渡ります。`child_env()` がこの変換を行います。showcase アプリの `SHOWCASE_UITEST` 等の launch hook はこの仕組みを使います（[showcase](showcase.md#起動環境フック)）。

`video` / `deviceLog` の区間録りも `simctl io recordVideo` / `simctl spawn log stream` を使いますが、これらは証跡サブシステム側（`evidence/intervals.py`）に置かれています（[evidence](evidence.md#区間証跡video--devicelog--apptrace)）。
