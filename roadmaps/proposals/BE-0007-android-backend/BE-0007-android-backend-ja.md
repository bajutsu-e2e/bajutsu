[English](BE-0007-android-backend.md) · **日本語**

# BE-0007 — Android backend

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0007](BE-0007-android-backend-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | プラットフォーム拡張（Android / Web / Flutter） |
<!-- /BE-METADATA -->

## はじめに

Android エミュレータ向けの driver です。`adb` + UI Automator で UI を操作し、`resource-id` /
`content-desc` セレクタを id ファーストで対応づけます。構造的には既存の iOS `idb` バックエンドの双子で、
subprocess 駆動、座標ベースの actuation、semantic tap なしです。これを追加するとは、決定的コアを 1 バイトも
変えずに、新しい三つ組（actuator + 環境マネージャ + id 規約）を追加することです。

## 動機

Android は **idb の構造的双子**です。subprocess 駆動、座標 actuation、遷移中の一過性に空なツリーという
共通の形ゆえに、idb の *retry 付き解決、曖昧は即失敗* パターン（[drivers](../../../docs/ja/drivers.md) を参照）を
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
| **id 規約** | `resource-id`（XML の `android:id`、Jetpack Compose の `Modifier.testTag` を `testTagsAsResourceId` で `resource-id` として公開）。`content-desc`/`text` → `label`、widget class → `traits` |
| **証跡 provider** | screenshot = `adb exec-out screencap`、video = `adb shell screenrecord`、`deviceLog` = `adb logcat`（tag/pid で絞る）、`network` = ネイティブ監視なし → iOS と同じモック方式 |
| **codegen 変換先** | Espresso または UI Automator（Kotlin/Java） |

### idb の構造的双子

Android は **idb の構造的双子**です。subprocess 駆動、座標 actuation、画面遷移中の一過性に空なツリー。
この共通の形ゆえに、idb の *retry 付き解決、曖昧は即失敗* パターンをそのまま再利用します。すなわち、ツリーを
ポーリングし、一過性に空な結果なら retry し、セレクタが 2 件以上に解決したら「最初に一致したものを tap する」
のではなく即座に失敗します（[drivers](../../../docs/ja/drivers.md) を参照）。これを実証すれば、iOS 固有の部分が
本当に 3 つの継ぎ目に閉じていたこと、そしてシステムの残りからほぼ新しい形を要しないことが裏づけられます。

### セレクタの対応づけ

YAML のセレクタ（`{ id: settings.reindex }`）はすでにプラットフォーム中立です。変わるのは *バックエンドが
それを満たすためにアプリ側のどの属性を読むか* だけで、それは新しい Driver の内側に完全に閉じます。Android では
`Selector` フィールドは次のように対応します。

| `Selector` フィールド | iOS | Android |
|---|---|---|
| `id`（第一候補） | `accessibilityIdentifier` | `resource-id`（Compose: `Modifier.testTag` + `testTagsAsResourceId`） |
| `label`（補助） | `accessibilityLabel` | `content-desc` / `text` |
| `traits`（役割で絞る） | UI traits（`button`、`link`…） | widget class（`android.widget.Button`） |
| `value` | accessibility value | `text` / チェック状態 |

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

### 展開順：Web の後の第 2 段階

Android は **第 2 段階**で、Web（Playwright）バックエンド（[BE-0041](../../in-progress/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）の
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
  エミュレータも不要で、現行 Linux ゲートに最低コストで収まります。[BE-0041](../../in-progress/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) を参照。

## 参考

[DESIGN](../../../DESIGN.md)、`bajutsu/drivers/`、`bajutsu/backends.py`、
[drivers.md](../../../docs/ja/drivers.md)、
[BE-0041 — Web (Playwright) backend](../../in-progress/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)、
[BE-0008 — Flutter support](../../proposals/BE-0008-flutter-support/BE-0008-flutter-support-ja.md)
