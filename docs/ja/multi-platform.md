[English](../multi-platform.md) · **日本語**

# Android / Web への拡張（マルチプラットフォーム）

> 将来構想 —— **未実装**。現状の Bajutsu は **iOS Simulator 限定**にスコープを切っています
> （[DESIGN §1](../../DESIGN.md)、[README](../../README.md)）。本ページは、既存の抽象をそのまま活かして
> **Android**（エミュレータ）と **Web**（ブラウザ）も操作対象に広げるための、具体的な方針と設計を説明します
> —— 何が不変で、各バックエンドが何を追加し、どの順で作るか。[roadmap → プラットフォーム拡張](../roadmap/README-ja.md#プラットフォーム拡張android--flutter) の詳細版です。

関連: [drivers](drivers.md) · [selectors](selectors.md) · [concepts](concepts.md) · [configuration](configuration.md) · [vision](vision.md)

---

## 抽象はすでにプラットフォーム形をしている

Bajutsu のコアは、意図的にバックエンド非依存の `Driver` インターフェースの背後に構築されています
（[drivers](drivers.md)、[DESIGN §5](../../DESIGN.md)）。決定的なコア —— シナリオ DSL・セレクタ解決・
機械アサーション・オーケストレータループ・証跡サブシステム・レポーター —— のどこにも iOS は出てきません。
今日 iOS 固有なのは **3 つの継ぎ目**だけです。

1. **actuator**（`drivers/idb.py`）—— `idb` + frame 中心の座標 tap で UI を操作します。
2. **環境マネージャ**（`env.py`）—— `simctl` の boot / erase / launch / openurl を担います。
3. **安定 id 規約**（`accessibilityIdentifier`、[§7](../../DESIGN.md)）—— `Selector.id` の解決を
   決定的にする、アプリ側の供給源です。

マルチプラットフォーム化とは、決定的コアを 1 バイトも変えずに、プラットフォームごとに
**この 3 点組（actuator + 環境 + id 規約）を追加する**ことです。これは設計がすでに 2 つ目の iOS actuator
（XCUITest）について見込んでいる動きを、OS 横断に一般化したものです。

### 不変なもの vs プラットフォームごとに追加するもの

| 層 | プラットフォーム追加時の扱い |
|---|---|
| シナリオ DSL・文法（[scenarios](scenarios.md) / [dsl-grammar](dsl-grammar.md)） | **不変。** ステップ・待機・アサーション・証跡トークンはプラットフォーム中立 |
| セレクタモデルと解決（`drivers/base.py` `resolve_unique`） | **不変。** 0/1/2+ 件のセマンティクスと「曖昧なら即失敗」はバックエンド非依存 |
| 機械アサーション（`assertions.py`） | **不変。** `exists`/`value`/`label`/`count`/`enabled`/… は正規化済み `Element` ツリーを評価 |
| オーケストレータループ（`orchestrator.py`） | **不変。** observe → act → verify。条件待機は `query()` をポーリング |
| 証跡サブシステム（`evidence.py`・capturePolicy・`manifest.json`） | **ほぼ不変。** capture *トークン*は据え置き、*provider* がプラットフォーム別の取得元を追加（後述） |
| レポーター（`report.py`） | **不変。** manifest / JUnit / HTML はプラットフォーム中立 |
| 設定の階層（`config.py`・`defaults × apps`） | **拡張。** `platform` フィールド + プラットフォーム別ターゲットフィールド（後述） |
| **Driver バックエンド**（`drivers/*.py`・`capabilities()`） | **プラットフォームごとに新規** —— actuator |
| **環境/ライフサイクルマネージャ**（`env.py` の同位） | **プラットフォームごとに新規** —— boot / clean / launch / deeplink |
| **doctor 規約チェック**（`doctor.py`） | **プラットフォームごとに新規** —— §7 相当の充足度スコア |
| **codegen エミッタ**（`codegen.py`） | **プラットフォームごとに新規** —— 変換先のネイティブテスト |

最初の新プラットフォームが最もコストが高くなります。「不変」列に潜む iOS 固有の前提をすべて表に出させるためです。2 つ目は安くなります。展開順はそのコストを最も小さく払える場所に合わせています —— [展開順](#展開順-web-を最初に) を参照してください。

---

## 核心: セレクタの可搬性

シナリオがプラットフォーム横断で可搬なのは、**そのセレクタが `id` で書かれている範囲に限ります**
（[concepts §4–5](concepts.md#4-安定セレクタaccessibilityidentifier-優先)）。各プラットフォームには
`accessibilityIdentifier` のネイティブ相当 —— **非ローカライズ・開発者付与・データ由来**のハンドル ——
があり、プラットフォーム別 id 規約（[§7.3](../../DESIGN.md)）はそこへ対応付けます。

| `Selector` フィールド | iOS | Android | Web |
|---|---|---|---|
| `id`（第一候補） | `accessibilityIdentifier` | `resource-id`（Compose: `Modifier.testTag` + `testTagsAsResourceId`） | `data-testid` |
| `label`（補助） | `accessibilityLabel` | `content-desc` / `text` | accessible name / `aria-label` / テキスト |
| `traits`（役割で絞る） | UI traits（`button`、`link`…） | widget class（`android.widget.Button`） | ARIA `role`（`button`、`link`、`textbox`） |
| `value` | accessibility value | `text` / チェック状態 | input `value` / `aria-*` |

要点: **YAML のセレクタ `{ id: settings.reindex }` はすでにプラットフォーム中立です。**
変わるのは *バックエンドがそれを満たすためにアプリ側のどの属性を読むか* だけで、それは新しい Driver の
内側に完全に閉じます —— シナリオには出てきません。

**共有シナリオについての見解。** 同一プロダクトの 3 アプリが画面まで完全一致することは稀です。現実的なモデルは **1 つの DSL・1 つのランナー・1 つのツールチェーンを共有する、プラットフォーム別シナリオ**
であって、1 つの YAML を 3 回実行することではありません。横断的な*再利用*は、本当に一致する範囲についての **opt-in** とし、
既存の **予約/共有 id 名前空間**（`auth.*`、`nav.*`、[§7.3](../../DESIGN.md)）で表現します。
ログインの `setup:` コンポーネント（[scenarios](scenarios.md)）は、それらの id が各プラットフォームで
parity を保たれている限り、3 つすべてで動きます。このツールが提供するのは *可搬なツール* であり、*可搬なシナリオ* は
チームが id 契約を維持する範囲でのみ提供します —— 1 つの YAML が自動的に 3 プラットフォームのテストになるわけではありません。

---

## プラットフォーム別の設計

### Web —— Playwright（最初に推奨）

| 継ぎ目 | 選択 |
|---|---|
| **actuator** | **Playwright（Python）** —— `playwright` は Python パッケージ、ヘッドレス、クロスブラウザです。`getByTestId` / `getByRole` で選択し、**意味的にクリック**（座標なし） |
| **環境** | デバイスではなく **`BrowserContext`**。クリーン状態 = 新規 incognito `browser.new_context()`（`erase` 相当ですがほぼ無コスト）。「launch」= `page.goto(url)`、「deeplink」= URL、launch env = クエリパラメータ / 注入した `localStorage` / cookie |
| **id 規約** | `data-testid`（非ローカライズ・開発者付与）。ARIA `role` → `traits`、accessible name → `label` |
| **証跡 provider** | screenshot = `page.screenshot`、video = context の録画、**`network` = ネイティブの route インターセプト**（これを持つ最初のバックエンド）、`deviceLog` ≈ console ログ / page error |
| **codegen 変換先** | Playwright test（TypeScript）または `pytest-playwright` |

Playwright が能力グラデーションを先導する理由は、`semanticTap`、ネイティブ `conditionWait`（自動待機）、
`network`（リクエストのスタブ化 **と** 観測を 1 つの API で）、エミュレートの `multiTouch` を提供するためです。
これは能力モデルの上限を引き上げると同時に、Web を抽象を実証する最低コストの場所にします。

### Android —— adb + UI Automator

| 継ぎ目 | 選択 |
|---|---|
| **actuator** | **`adb` + `uiautomator dump`** —— `uiautomator dump` が XML ツリーを返し、操作は要素の bounds 中心に `adb shell input tap x y`。**座標ベース・semantic tap なし —— idb のほぼ完全な双子。**（より高機能な Appium UiAutomator2 経路で後から意味的操作を追加することも可能） |
| **環境** | `adb`: クリーン状態 = `pm clear <package>`（`erase` 相当）、boot は emulator/AVD（Android Virtual Device）、launch = `am start`、deeplink = `am start -a android.intent.action.VIEW -d <url>`、launch args = intent extras |
| **id 規約** | `resource-id`（XML の `android:id`、Jetpack Compose の `Modifier.testTag` を `testTagsAsResourceId` で `resource-id` として公開）。`content-desc`/`text` → `label`、widget class → `traits` |
| **証跡 provider** | screenshot = `adb exec-out screencap`、video = `adb shell screenrecord`、`deviceLog` = `adb logcat`（tag/pid で絞る）、`network` = ネイティブ監視なし → iOS と同じモック方式 |
| **codegen 変換先** | Espresso または UI Automator（Kotlin/Java） |

Android は **idb の構造的双子**です。subprocess 駆動、座標 actuation、遷移中の一過性に空なツリー
（そのため idb の *retry 付き解決・曖昧は即失敗* パターンをそのまま再利用します。[drivers](drivers.md#idb)）。
iOS 固有の部分が本当に 3 つの継ぎ目に隔離されていたことを、ほぼ新しい形なしに実証します。

### Flutter / React Native / WebView ハイブリッド（後段）

クロスレンダリングな UI（Flutter は自前でピクセルを描く、ハイブリッドは WebView を埋め込む）は、
OS の a11y（アクセシビリティ）ツリーに要素を出さないことが多いです。これらは新しい OS actuator ではなく **semantics ブリッジ**を必要とします。
Flutter の semantics ツリー（`integration_test` / VM Service / Flutter Driver）、または埋め込み Web 向けの
WebView→DOM（Document Object Model）ブリッジです。2 つのネイティブツリーが抽象を実証した後の **第 3 段階**として扱います。

### 拡張した能力マトリクス

能力トークン（[drivers](drivers.md#能力capability)）はすでにこの幅を表現できます —— 新バックエンドは
概念を増やさずに収まります。

| 能力 | idb (iOS) | adb (Android) | Playwright (Web) | fake |
|---|:--:|:--:|:--:|:--:|
| `query` / `elements` / `screenshot` | ✅ | ✅ | ✅ | ✅ |
| `semanticTap` | — | — | ✅ | ✅ |
| `conditionWait`（ネイティブ） | — | — | ✅ | ✅ |
| `network`（ネイティブ） | — | — | ✅ | — |
| `multiTouch` | — | — | ✅（エミュレート） | ✅ |

idb と Android は小さい端（座標 actuation・モックネットワーク）、Playwright は大きい端（意味的・
ネイティブネットワーク）に位置します。無改造の能力モデルがこの両端をまたぐことは、抽象が成立している証拠です。

---

## 設定の変更

`apps.<name>`（[configuration](configuration.md)）に **`platform`** の判別子とプラットフォーム別
ターゲットフィールドを追加します。決定的な解決順（`defaults < app < scenario`）は不変です。

```yaml
defaults:
  platform: ios                 # 既定。アプリ別に下で上書き
  locale:  ja_JP

apps:
  sample-ios:
    platform:       ios
    backend:        [idb]
    bundleId:       com.bajutsu.sample
    deeplinkScheme: bajutsusample
    idNamespaces:   [home, settings]

  sample-android:
    platform:       android
    backend:        [adb]
    package:        com.bajutsu.sample          # ← bundleId の同位
    deeplinkScheme: bajutsusample
    idNamespaces:   [home, settings]

  sample-web:
    platform:       web
    backend:        [playwright]
    baseUrl:        https://app.example.test     # ← bundleId の同位
    idNamespaces:   [home, settings]
```

`platform` がどの **環境マネージャ**と **backend レジストリ**を使うかを決めます。スキーマの残り
（名前空間・redact・setup・capture）は共有のままです。`backends.KNOWN` はプラットフォームスコープになり
（`{ios: ("idb","xcuitest"), android: ("adb",), web: ("playwright",)}`）、`make_driver` /
`select_actuator` は解決済みの platform で分岐します。

---

## 決定性はプラットフォームごとに保たれる

4 つの機構（[concepts §3](concepts.md#3-決定性ファースト4-つの具体策)）はどのバックエンドでも成立します
—— 変わるのは *実装* だけです。

| 原則 | iOS | Android | Web |
|---|---|---|---|
| 曖昧なセレクタは即失敗 | `resolve_unique`（共有） | `resolve_unique`（共有） | `resolve_unique`（共有） |
| 条件待機のみ・固定 sleep なし | `query()` をポーリング | `uiautomator dump` をポーリング | Playwright 自動待機 + ポーリング |
| テストごとにクリーン環境 | `simctl erase` | `pm clear` | 新規 `new_context()` |
| 合否は機械チェックのみ | 正規化 `Element` | 正規化 `Element` | 正規化 `Element` |

`resolve_unique` と `assertions.py` は文字どおり共有コードです。決定性保証はプラットフォームごとに
再実装しません。これは各バックエンドのツリーを共通 `Element` に正規化することの主目的です。

---

## 展開順（Web を最初に）

| 段階 | 範囲 | この順の理由 |
|---|---|---|
| **0 — 継ぎ目を抽象化** | `Environment` Protocol を抽出（今は `simctl` が具体）、config に `platform` + プラットフォームスコープの backend レジストリを追加、`runner.py`/`orchestrator.py` の iOS 固有部分を監査 | 一般化コストをデバイスなしで一度だけ払います |
| **1 — Web（Playwright）** | 最初の本物の 2 つ目のプラットフォーム | **Linux で動きます —— Mac 不要**。つまり *既存の* `make check` / CI（継続的インテグレーション）ゲート（[ci](ci.md)）にその日から収まります。ネイティブ network + video + 意味的操作が `capabilities()` の **大きい端**を行使します。摩擦最小・到達範囲最大・コアがプラットフォーム中立であることの最低コストの証明 |
| **2 — Android（adb + UI Automator）** | idb の双子 | 座標モデルが idb をほぼ完全に映すので新しい形が少ない。エミュレータは Linux CI（KVM: Linux Kernel Virtual Machine）で動きます。`capabilities()` の **小さい端**を行使 |
| **3 — ハイブリッド/クロスレンダ** | Flutter / React Native / WebView | 新 OS actuator ではなく semantics ブリッジが必要 —— 2 つのネイティブツリーが固まるまで後回し |
| **横断** | プラットフォーム別 `doctor` スコア、プラットフォーム別 codegen エミッタ、**スコープ文の更新** | 各段階と並走 |

**Android が構造的に近い双子なのに、なぜ Web を先にするか**: Web は **macOS もデバイスエミュレータも不要**な
唯一のプラットフォームなので、動いた日に現行 Linux ゲートの内側に着地します。「コアは本当に
プラットフォーム中立か？」という問いのリスクを最低コストで下げられます。Android はその後、すでに一般化された
コアの上で *小さい/座標* 経路を確認します。

---

## これが引き起こすスコープ文の更新

マルチプラットフォーム化はコードだけでなく**戦略的なスコープ変更**です。段階 1 が着地したら、同じ変更で更新します。

- **[DESIGN §1](../../DESIGN.md)** の「やること / やらないこと」—— iOS Simulator 限定 → マルチプラットフォーム。
  「実機 / クラウドデバイスファーム」の論拠を該当箇所へ移します。
- **[README](../../README.md) / [README.ja](../../README.ja.md)** のプロダクト一文と中核原則。
- **[architecture 実装状況](architecture.md#実装状況)** —— 新バックエンドを登録します。
- **docs ナビ** —— [`docs/README.md`](../README.md) と [`docs/ja/README.md`](README.md) の両方。

**Prime directive は不変に保ちます**: 決定性ファースト・app-agnostic・*AI は判定者にならない* は Android でも
Web でも同一に適用されます。どの新プラットフォームも Tier-2 ゲートに LLM（大規模言語モデル）を持ち込んではなりません。

---

## 未解決論点 / リスク

- **環境抽象の形。** `simctl` は iOS 固有です。正しい `Environment` Protocol（erase/boot/launch/deeplink/
  screenshot）は、ブラウザコンテキスト（「erase」「boot」がほぼ no-op）にも、デバイス前提を漏らさずに
  収まる必要があります。
- **Compose / クロスレンダの id 公開。** `testTagsAsResourceId` をアプリ側で有効化する必要があり、
  iOS が `accessibilityIdentifier` を要するのと同じです。§7 規約と `doctor` スコアがこれをプラットフォーム別に
  扱います。
- **CI コスト。** Web は Linux でほぼ無料ですが、Android エミュレータは CI で KVM を要し重くなります。iOS が
  Mac を要する唯一のデバイスでなくなると、ホスティングプール経済（[cloud-hosting](cloud-hosting.md)）が変わります。
- **codegen の網羅性。** 各プラットフォームに独自エミッタが必要です。共通サブセットから始め、未対応構文は
  XCUITest エミッタが既にやっているように `// TODO` に落とします（[codegen](codegen.md)）。
- **セレクタ parity のずれ。** 共有 `auth.*`/`nav.*` 名前空間は、各プラットフォームのアプリが同期を
  保つ限りでのみ可搬です。`doctor` がプラットフォーム別に id 契約を検査する必要があります。
