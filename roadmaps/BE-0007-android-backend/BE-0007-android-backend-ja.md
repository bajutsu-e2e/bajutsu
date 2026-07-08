[English](BE-0007-android-backend.md) · **日本語**

# BE-0007 — Android backend

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0007](BE-0007-android-backend-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0007") |
| 実装 PR | [#658](https://github.com/bajutsu-e2e/bajutsu/pull/658), [#821](https://github.com/bajutsu-e2e/bajutsu/pull/821) |
| トピック | プラットフォーム拡張（Android / Web / Flutter） |
<!-- /BE-METADATA -->

## はじめに

Android エミュレータ向けの driver です。`adb` + UI Automator で UI を操作し、`resource-id` /
`content-desc` セレクタを id ファーストで対応づけます。構造的には既存の iOS `idb` バックエンドの双子で、
subprocess 駆動、座標ベースの actuation、semantic tap なしです。これを追加するとは、決定的コアを 1 バイトも
変えずに、新しい三つ組（actuator + 環境マネージャ + id 規約）を追加することです。

## 動機

Android は **idb の構造的双子**です。subprocess 駆動、座標 actuation、遷移中の一過性に空なツリーという
共通の形ゆえに、idb の *retry 付き解決、曖昧は即失敗* パターン（[drivers](../../docs/ja/drivers.md) を参照）を
ほぼそのまま再利用します。これを作ることで、iOS 固有の部分が本当に 3 つの継ぎ目（actuator、環境マネージャ、
安定 id 規約）に隔離されていたことを、「不変」なコアにほぼ新しい形を持ち込まずに実証できます。同時に、
シナリオ DSL、セレクタモデル、機械アサーション、オーケストレータループ、レポーターに一切触れることなく、
2 番目に一般的なモバイルターゲットへ到達範囲を広げます。

## 詳細設計

### 継ぎ目の表

| 継ぎ目 | 選択 |
|---|---|
| **actuator** | **`adb` + `uiautomator dump`**：`uiautomator dump` が XML ツリーを返し、操作は要素の bounds 中心に `adb shell input tap x y`。**座標ベースで semantic tap なし、idb のほぼ完全な双子です。**（より高機能な Appium UiAutomator2 経路で後から意味的操作を追加することも可能） |
| **環境** | `adb`: クリーン状態 = `pm clear <package>`（`erase` 相当）、boot は emulator/AVD（Android Virtual Device）、launch = `am start`、deeplink = `am start -a android.intent.action.VIEW -d <url>`、launch args = intent extras |
| **id 規約** | `resource-id`（XML の `android:id`、Jetpack Compose の `Modifier.testTag` を `testTagsAsResourceId` で `resource-id` として公開）。`text` → `label`（`content-desc` へフォールバック）、`content-desc` → `value`（状態値のミラー。SPEC §2.1）、widget class → `traits` |
| **証跡 provider** | screenshot = `adb exec-out screencap`、video = `adb shell screenrecord`、`deviceLog` = `adb logcat`（tag/pid で絞る）、`network` = ネイティブ監視なし → iOS と同じモック方式 |
| **codegen 変換先** | Espresso または UI Automator（Kotlin/Java） |

**環境**の行は、[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md) のクロスプラットフォーム `Environment` プロトコルを実装する `AndroidEnvironment` として着地します。その `start` は adb 列（クリーン状態のための `pm clear` → 起動の `am start` → deeplink の `am start -a android.intent.action.VIEW -d <url>`）を実行し、`adb` driver を返します。アクチュエータと環境は、iOS が idb と simctl で埋める 2 つの継ぎ目の Android 版にあたります。したがって Android は runner に新しい形を持ち込まず、BE-0009 が抽出する継ぎ目に差し込まれます。

### idb の構造的双子

Android は **idb の構造的双子**です。subprocess 駆動、座標 actuation、画面遷移中の一過性に空なツリー。
この共通の形ゆえに、idb の *retry 付き解決、曖昧は即失敗* パターンをそのまま再利用します。すなわち、ツリーを
ポーリングし、一過性に空な結果なら retry し、セレクタが 2 件以上に解決したら「最初に一致したものを tap する」
のではなく即座に失敗します（[drivers](../../docs/ja/drivers.md) を参照）。これを実証すれば、iOS 固有の部分が
本当に 3 つの継ぎ目に閉じていたこと、そしてシステムの残りからほぼ新しい形を要しないことが裏づけられます。

### セレクタの対応づけ

YAML のセレクタ（`{ id: settings.reindex }`）はすでにプラットフォーム中立です。変わるのは *バックエンドが
それを満たすためにアプリ側のどの属性を読むか* だけで、それは新しい Driver の内側に完全に閉じます。Android では
`Selector` フィールドは次のように対応します。

| `Selector` フィールド | iOS | Android |
|---|---|---|
| `id`（第一候補） | `accessibilityIdentifier` | `resource-id`（Compose: `Modifier.testTag` + `testTagsAsResourceId`） |
| `label`（補助） | `accessibilityLabel` | `text`（可視テキスト、`content-desc` へフォールバック） |
| `traits`（役割で絞る） | UI traits（`button`、`link`…） | widget class（`android.widget.Button`） |
| `value` | accessibility value | `content-desc`（状態値のミラー。SPEC §2.1） |

### 能力マトリクスでの位置

能力トークンはすでにこの幅を表現できます。Android は概念を増やさずに小さい端へ収まります。

| 能力 | idb (iOS) | adb (Android) | Playwright (Web) | fake |
|---|:--:|:--:|:--:|:--:|
| `query` / `elements` / `screenshot` | ✅ | ✅ | ✅ | ✅ |
| `semanticTap` | — | — | ✅ | ✅ |
| `conditionWait`（ネイティブ） | — | — | ✅ | ✅ |
| `network`（ネイティブ） | — | — | ✅ | — |
| `multiTouch` | — | — | ✅（エミュレート） | ✅ |

idb と Android は小さい端（座標 actuation とモックネットワーク）、Playwright は大きい端（意味的操作とネイティブ
ネットワーク）に位置します。無改造の能力モデルがこの両端をまたぐことは、抽象が成立している証拠です。

### 具体的なツール

このバックエンドが必要とするのは `adb`（起動には `emulator`）への subprocess 呼び出しだけです。これが
Android を idb の双子たらしめています。

- **ツリー**：`adb -s <serial> exec-out uiautomator dump /dev/tty` がウィンドウの XML をストリームします。
  各 `<node>` は `resource-id`、`content-desc`、`text`、`class`、`bounds="[x1,y1][x2,y2]"` を持ち、driver は
  `bounds` を `Frame`（x, y, w, h）に解析してその中心を tap します。idb と同じ frame 中心の往復です。
- **操作**：`adb shell input tap x y` / `input swipe x1 y1 x2 y2 <ms>` / `input text <s>`。座標ベースかつ
  単一タッチで、semantic tap はありません。idb と同じです。
- **一過性に空なツリー**：遷移の途中で `uiautomator dump` は「null root node returned by
  UiTestAutomationBridge」を返して間欠的に失敗します（アニメーションが落ち着いていない状態です）。これは
  SwiftUI 遷移中に idb が返すほぼ空のツリーの正確な相似形なので、driver は idb の *retry 付き解決、曖昧は
  即失敗* の規律をそのまま再利用します。すなわち dump を有限回 retry し、2 件以上に一致したときは最初に
  一致したものを tap せず即座に失敗します。
- **Compose の id**：`Modifier.testTag("…")` が `resource-id` として公開されるのは、サブツリーの根が
  `Modifier.semantics { testTagsAsResourceId = true }` を設定したときだけです（Compose 1.2.0-alpha08 以降）。
  これが Compose アプリ向けにドキュメントで示す id 規約です。
- **起動の準備完了**：AVD の boot の後に `adb wait-for-device` を実行し、続いて `getprop
  sys.boot_completed` が `1` になるまでポーリングします（固定 sleep のない有限の条件待ちです）。その後に
  アプリを launch します。

### 作業分解（MECE）

1. **レジストリ配線**（`bajutsu/backends.py`）。`PLATFORMS["android"] = ("adb",)` と `_EXECUTABLE["adb"]`
   はすでにあるので、残りの編集は計画済みトークンを有効にするだけです。`adb` を `IMPLEMENTED` に加え、
   `AdbDriver.CAPABILITIES`（デバイス不要で読めるクラス定数、BE-0082 の preflight 用）を返す
   `capabilities_for("adb")` 分岐と、`make_driver("adb", serial)` 分岐を追加します。
2. **`AdbDriver` actuator**（`bajutsu/drivers/adb.py`）。`uiautomator dump` の XML を正規化された `Element`
   に解析し（上記のセレクタ対応）、`input tap/swipe/text` による座標操作、`screencap` による `screenshot`、
   一過性に空なツリーの retry、`CAPABILITIES = {QUERY, ELEMENTS, SCREENSHOT}` を実装します。`pinch`/`rotate`
   は idb と同様、単一タッチのため `UnsupportedAction` を送出します。
3. **`AndroidEnvironment`**（`bajutsu/environment.py`、`environment_for`）。`start` は adb 列（クリーン状態の
   ための `pm clear <package>`（`erase` 相当）→ 任意の AVD boot と起動準備完了待ち → 起動の
   `am start -n <pkg>/<activity>` → deeplink の `am start -a android.intent.action.VIEW -d <url>`）を実行し、
   `adb` driver を返します。リース整形メソッドは `_DeviceEnvironment` と同じ要領で埋めます。`adb devices` +
   `getprop` からのデバイスカタログ、relauncher（`am force-stop` の後 `am start`）、teardown（`am force-stop`）、
   そして `has_devices() = True` と serial をまたぐ `plan_lanes` を持つ crawl レーンのメソッドです。
4. **証跡とデバイス制御**。`deviceLog` は `adb logcat`（tag/pid で絞る）、video は `adb shell screenrecord`
   （デバイス側で録画し停止時に pull）で供給します。`network` はネイティブ監視がないため iOS のモック方式を
   再利用します（`NETWORK` 能力なし）。エミュレータが対応するデバイス状態のステップ（`emu geo fix` による
   `setLocation`、clipboard）を対応づけ、それ以外は `UnsupportedAction` を送出します。
5. **doctor と開示**。`doctor --target` が idb の隣で `adb`/エミュレータの可用性を報告します。実行マニフェスト
   は `backend: "adb"` を記録するので、選ばれた actuator が開示されます。
6. **codegen 変換先**。Playwright/XCUITest のジェネレータと並ぶ `to_espresso`（または UI Automator）
   ジェネレータ（`bajutsu/codegen_espresso.py`）です。構造的な対応づけのみで LLM を使わないので、実行ゲート
   には触れません。driver の後続スライスとして着地できます。
7. **検証**。高速ゲート（デバイス不要）：レジストリの切り替えは `select_actuator`/`capabilities_for` を駆動し、
   driver は取得済みの `uiautomator dump` XML フィクスチャに対し**注入した fake `run`** で駆動します（セレクタ
   対応、frame 中心の tap、一過性に空なツリーの retry、曖昧は即失敗を確認します）。あわせて注入した runner で
   環境列も検証します。実機（e2e）：KVM を使う Linux CI 上の Android エミュレータ（`android-emulator-runner`
   アクション）で `--backend android` のシナリオを実行します。idb の e2e 経路と同様、高速な `make check`
   ゲートからは外します。

### 展開順：Web の後の第 2 段階

Android は **第 2 段階**で、Web（Playwright）バックエンド（[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）の
*後*に着手します。Web を先にするのは、macOS もデバイスエミュレータも不要な唯一のプラットフォームであり、
動いた日から既存の Linux `make check` / CI ゲートに収まって、コアがプラットフォーム中立であることを最低コストで
証明できるからです。Android はその後、すでに一般化されたコアの上で *小さい / 座標* 経路を確認します。座標
モデルが idb をほぼ完全に映すので新しい形が少なく、エミュレータは KVM（Linux Kernel Virtual Machine）を使う
Linux CI で動き、`capabilities()` の **小さい端**を行使します。

## 検討した代替案

- **主 actuator として Appium UiAutomator2 を採用する。** 意味的操作（bounds 中心の座標を計算せず
  resource-id で選択して操作）を追加できる高機能な経路です。見送り: `adb` + `uiautomator dump` 経路が idb に
  最も近い双子なので、最も新しい形を持ち込まずに抽象を実証できます。意味的な Android actuator は、iOS で idb と
  並んで XCUITest が見込まれているのと同様、後から 2 つ目のバックエンドとして追加できます。
- **Web より先に Android を作る。** 展開順の理由で却下: Android が構造的に近い双子であっても、Web は macOS も
  エミュレータも不要で、現行 Linux ゲートに最低コストで収まります。[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) を参照。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] レジストリ配線：`adb` を `IMPLEMENTED` に、`capabilities_for`/`make_driver` の分岐（`bajutsu/backends.py`）。
- [x] `AdbDriver` actuator：`uiautomator dump` の解析、座標操作、一過性に空なツリーの retry、能力（`bajutsu/drivers/adb.py`）。
- [x] `AndroidEnvironment`：起動完了待ち → `pm clear` → `am start` → deeplink の列とリース整形メソッド（`bajutsu/platform_lifecycle.py`、新設の `bajutsu/adb.py` コマンド層の上に）。
- [ ] 証跡とデバイス制御：`logcat` の deviceLog、`screenrecord` の video、モックネットワーク**（完了、2026-07-08）**。対応するデバイス状態のステップ（`DeviceControl` 一族）は後続に残します。Android は粗い `deviceControl` 能力のうち一部しか対応できないため見送りました。
- [x] doctor と開示：idb の隣に `doctor --target` の可用性、マニフェストに `backend: "adb"` を記録。
- [ ] codegen 変換先：Espresso / UI Automator ジェネレータ（後続スライス）。
- [ ] 検証：dump フィクスチャに対する高速ゲートの driver/レジストリテスト**（完了）**、コアシナリオをローカルの arm64 エミュレータで駆動**（完了、2026-07-07）**、実機 e2e の KVM CI への組み込み（後続）。

ログ：

- 2026-07-08（[#821](https://github.com/bajutsu-e2e/bajutsu/pull/821)）：区間証跡スライス（Unit 4 の証跡側）。`video` は `adb shell screenrecord`、`deviceLog` は `adb logcat` で取得するようにしました。simctl の provider の双子です（`bajutsu/adb.py` のコマンドビルダと、`bajutsu/intervals.py` の `start_screenrecord` / `start_logcat`）。`screenrecord` はデバイス側に録画するので、その `Interval` は SIGINT で確定させたあと mp4 をデバイスから `adb pull` で回収し、デバイス側のコピーを削除します。`logcat` はファイルへストリームして SIGTERM で停止します。Android は driver 供給の区間証跡 seam（`AdbDriver.driver_interval`）を通り、FileSink は simctl 以外のバックエンドをこの seam へ振り分けます。従来の `web_interval` フィールドを `driver_interval` に一般化し、Playwright と adb の両ドライバで共有します。モックネットワークは新しいコードを要しませんでした。アプリ側 collector の URL は launch env 経由で intent extra として既にアプリへ届いており（テストで固定しました）、iOS と同じ経路です。`screenrecord` の pull が失敗したときは、確定処理のループを中断（logcat のサブプロセスを取り残す）したり、通過するはずのシナリオを証跡の I/O で失敗させたりせず、その 1 件だけを警告つきで捨てます。高速ゲートのユニットテストで、コマンドビルダ、両方の区間 starter（spawn / run を注入。pull は surface / cleanup は抑制という非対称も含む）、`driver_interval` の振り分け、FileSink と `AdbDriver` のエンドツーエンドの振り分け、stop 失敗時の drop、collector env の受け渡しを検証します。ドキュメントを更新しました（`docs/drivers.md`、`docs/evidence.md`、`docs/architecture.md` と各 ja ミラー）。実機に関わる 2 点は e2e スライスに送りました。`adb screenrecord` は 1 回の録画を約 180 秒で打ち切ること（ドキュメント化済み）と、SIGINT による確定は標準的な手法ながらデバイスや adb のバージョンに依存するため、そこで検証と調整を行うことです。デバイス制御（setLocation / clipboard など）と codegen は後続に残るため、引き続き **In progress** です。
- 2026-07-07：arm64 API 34 のエミュレータで初めて実機検証を行いました。ここから 2 つの修正が生じました。1 つは Android showcase がビルドできなかった点です。各モジュールの Gradle `namespace` に、Kotlin ソースのパッケージではなく applicationId の `.android.` を使っていたため、修飾なしの `BuildConfig` 参照とマニフェストの相対クラス名 `.MainActivity` が解決できませんでした。`namespace` をソースのパッケージに合わせると（applicationId はそのまま）両方が直ります。もう 1 つはドライバのセレクタ対応です。`value` を `text`（可視文字列）から読んでいたため、`value` アサーションが本来の「5」や「off」ではなく「Matches: 5」や「Not favorited」を見ていました。showcase は状態値を `content-desc` にミラーします（SPEC §2.1）から、`value` は `content-desc` を、`label` は `text` を読むようにしました。両者を直した結果、id、タップ、入力、値のコアシナリオが実機で通ります（smoke、firstlook、search、components、data_driven、modals、relaunch、system、evidence の capture）。残るシナリオは後続スライスにあたります。デバイス制御（capability で正直に gate）、マルチタッチ（`UnsupportedAction`）、スキーム deeplink とシステム back（`BackButton`）、モックネットワーク、実行時権限のアラート、visual/golden のベースラインです。加えて、境界の 3 本は後続に向けて原因を特定しました。`gestures` は long-press が通る一方、double-tap が登録されません。`adb shell input tap` を 2 回発行する間隔が、プラットフォームの double-tap の受付時間を超えるためです（`input` バイナリ自体の起動時間が支配的で、1 回のシェル往復にまとめても足りません）。`controls` は `log.segment.one` までは到達しますが、`log.segment.value` がスクロール後の表示範囲のすぐ外に残ります。`notices` はシステム back とリストのスクロールが要ります。いずれも実機でのアクチュエーションとスクロールの微調整です。引き続き **In progress** です。
- 2026-07-04：コアドライバのスライスが着地しました。`adb` コマンド層（`bajutsu/adb.py`、`simctl.py` の双子）、
  `AdbDriver` 座標アクチュエータ（`bajutsu/drivers/adb.py` — `uiautomator dump` の XML → `Element` セレクタ対応、
  frame 中心タップ、transient-empty のリトライ、ambiguity 即失敗、`CAPABILITIES = {query, elements, screenshot}`）、
  `AndroidEnvironment` の起動シーケンス（`platform_lifecycle.py`）、レジストリの切り替え（`backends.py`）、
  `doctor`/preflight の報告（`preflight.py`、`cli/commands/doctor.py`）です。取得済みの dump XML フィクスチャに対する
  高速ゲートのユニットテストが、セレクタ対応、frame 中心タップ、transient-empty のリトライ、ambiguity 即失敗を
  カバーします。ドキュメントも更新しました（`docs/drivers.md`、`docs/architecture.md`、`DESIGN.md`、日本語ミラー各種）。
  interval 証跡（`screenrecord`/`logcat`）、デバイス制御、codegen、実機エミュレータ e2e は後続スライスとして残るため、
  項目は **In progress** のままです。
- 2026-07-03：着手しました。driver が駆動するための showcase の Android 版フィクスチャが先に
  着地しました（[#552](https://github.com/bajutsu-e2e/bajutsu/pull/552)）。`demos/showcase` の
  Compose + Views 双子（a11y/noax の flavor）で、上記の `testTag`/`android:id` → `resource-id` 規約と
  セレクタ対応をアプリ側から検証し、`showcase.config.yaml` に `backend: [android]` の 4 ターゲットを
  配線しました。これは準備であり、上記の作業分解のボックスはまだ 1 つも消化していません。driver の
  スライス（レジストリ配線以降）が次です。

## 参考

[DESIGN](../../DESIGN.md)、`bajutsu/drivers/`、`bajutsu/backends.py`、
[drivers.md](../../docs/ja/drivers.md)、
[BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)、
[BE-0008 — Flutter support](../BE-0008-flutter-support/BE-0008-flutter-support-ja.md)
