[English](BE-0009-cross-platform-abstractions.md) · **日本語**

# BE-0009 — 抽象のクロスプラットフォーム化

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0009](BE-0009-cross-platform-abstractions-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | プラットフォーム拡張（Android / Web / Flutter） |
<!-- /BE-METADATA -->

## はじめに

現状の Bajutsu は iOS Simulator 限定にスコープを切っています（[DESIGN §1](../../../DESIGN.md)、[README](../../../README.md)）が、そのコアは意図的にバックエンド非依存の `Driver` インターフェースの背後に構築されています。本項目は、同じ決定的コアで Android（エミュレータ）と Web（ブラウザ）も操作できるようにするための横断的な抽象化作業です。iOS 固有の継ぎ目をプラットフォーム中立なプロトコルの背後に抽出し、config スキーマを `platform` 判別子で一般化し、runner と orchestrator に漏れた iOS 前提を監査します。プラットフォーム別バックエンドそのものは別項目です。Android は [BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md)、Web は [BE-0041](../../implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)（web-playwright-backend）、すでに実装済みのセレクタ/レジストリのスライスは [BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)（platform-backend-registry）です。本項目は、それらすべてが乗る共有の土台にあたります。

## 動機

### 抽象はすでにプラットフォーム形をしている

決定的なコア、すなわちシナリオ DSL（domain-specific language）、セレクタ解決、機械アサーション、オーケストレータループ、証跡サブシステム、レポーターのどこにも iOS は出てきません。今日 iOS 固有なのは **3 つの継ぎ目**だけです。

1. **actuator**（`drivers/idb.py`）。`idb` + frame 中心の座標 tap で UI を操作します。
2. **環境マネージャ**（`env.py`）。`simctl` の boot / erase / launch / openurl を担います。
3. **安定 id 規約**（`accessibilityIdentifier`、[DESIGN §7](../../../DESIGN.md)）。`Selector.id` の解決を決定的にする、アプリ側の供給源です。

マルチプラットフォーム化とは、決定的コアを 1 バイトも変えずに、プラットフォームごとに**この 3 点組（actuator + 環境 + id 規約）を追加する**ことです。これは設計がすでに 2 つ目の iOS actuator（XCUITest、[BE-0019](../../proposals/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)）について見込んでいる動きを、OS 横断に一般化したものです。

#### 不変なもの vs プラットフォームごとに追加するもの

| 層 | プラットフォーム追加時の扱い |
|---|---|
| シナリオ DSL・文法 | **不変。** ステップ・待機・アサーション・証跡トークンはプラットフォーム中立 |
| セレクタモデルと解決（`drivers/base.py` `resolve_unique`） | **不変。** 0/1/2+ 件のセマンティクスと「曖昧なら即失敗」はバックエンド非依存 |
| 機械アサーション（`assertions.py`） | **不変。** `exists`/`value`/`label`/`count`/`enabled`/… は正規化済み `Element` ツリーを評価 |
| オーケストレータループ（`orchestrator.py`） | **不変。** observe → act → verify。条件待機は `query()` をポーリング |
| 証跡サブシステム（`evidence.py`・capturePolicy・`manifest.json`） | **ほぼ不変。** capture *トークン*は据え置き、*provider* がプラットフォーム別の取得元を追加 |
| レポーター（`report.py`） | **不変。** manifest / JUnit / HTML はプラットフォーム中立 |
| 設定の階層（`config.py`・`defaults × apps`） | **拡張。** `platform` フィールド + プラットフォーム別ターゲットフィールド（後述） |
| **Driver バックエンド**（`drivers/*.py`・`capabilities()`） | **プラットフォームごとに新規**（actuator） |
| **環境/ライフサイクルマネージャ**（`env.py` の同位） | **プラットフォームごとに新規**（boot / clean / launch / deeplink） |
| **doctor 規約チェック**（`doctor.py`） | **プラットフォームごとに新規**（§7 相当の充足度スコア） |
| **codegen エミッタ**（`codegen.py`） | **プラットフォームごとに新規**（変換先のネイティブテスト） |

最初の新プラットフォームが最もコストが高くなります。「不変」列に潜む iOS 固有の前提をすべて表に出させるためです。2 つ目は安くなります。この抽象化作業を独立した項目として行うことで、そのコストをデバイスなしで一度だけ払います。

## 詳細設計

### 核心: セレクタの可搬性

シナリオがプラットフォーム横断で可搬なのは、**そのセレクタが `id` で書かれている範囲に限ります**。各プラットフォームには `accessibilityIdentifier` のネイティブ相当（**非ローカライズで、開発者が付与し、データ由来**のハンドル）があり、プラットフォーム別 id 規約（[DESIGN §7.3](../../../DESIGN.md)）はそこへ対応付けます。

| `Selector` フィールド | iOS | Android | Web |
|---|---|---|---|
| `id`（第一候補） | `accessibilityIdentifier` | `resource-id`（Compose: `Modifier.testTag` + `testTagsAsResourceId`） | `data-testid` |
| `label`（補助） | `accessibilityLabel` | `content-desc` / `text` | accessible name / `aria-label` / テキスト |
| `traits`（役割で絞る） | UI traits（`button`、`link`…） | widget class（`android.widget.Button`） | ARIA `role`（`button`、`link`、`textbox`） |
| `value` | accessibility value | `text` / チェック状態 | input `value` / `aria-*` |

要点は、**YAML のセレクタ `{ id: settings.reindex }` はすでにプラットフォーム中立だ**ということです。変わるのは *バックエンドがそれを満たすためにアプリ側のどの属性を読むか* だけで、それは新しい Driver の内側に完全に閉じます。シナリオには出てきません。

**共有シナリオについての見解。** 同一プロダクトの 3 アプリが画面まで完全一致することは稀です。現実的なモデルは **1 つの DSL、1 つのランナー、1 つのツールチェーンを共有する、プラットフォーム別シナリオ**であって、1 つの YAML を 3 回実行することではありません。横断的な*再利用*は、本当に一致する範囲についての **opt-in** とし、既存の **予約/共有 id 名前空間**（`auth.*`、`nav.*`、[DESIGN §7.3](../../../DESIGN.md)）で表現します。ログインの `setup:` コンポーネントは、それらの id が各プラットフォームで parity を保たれている限り、3 つすべてで動きます。このツールが提供するのは *可搬なツール* であり、*可搬なシナリオ* はチームが id 契約を維持する範囲でのみ提供します。1 つの YAML が自動的に 3 プラットフォームのテストになるわけではありません。

### 設定の変更

`apps.<name>` に **`platform`** の判別子とプラットフォーム別ターゲットフィールドを追加します。決定的な解決順（`defaults < app < scenario`）は不変です。

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

`platform` がどの **環境マネージャ**と **backend レジストリ**を使うかを決めます。スキーマの残り（名前空間、redact、setup、capture）は共有のままです。このレジストリの選択層のスライスはすでに実装済みです（`bajutsu/backends.py` はプラットフォームレジストリで分岐し、`--backend` / `backend:` はプラットフォームトークンを受け付けます）。[BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md) を参照してください。本項目が扱うのは残りの横断的作業で、`platform` 設定フィールドと、レジストリが引き渡す `Environment` プロトコルです。

> **Web backend の v1 はここで近道を取りました。** 最初の Web（Playwright）スライス（[BE-0041](../../implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）は対象 URL を必要としましたが、まだ `platform` 判別子は要りませんでした。そこで `apps.<name>` に単一の `baseUrl` フィールドを追加し、環境のライフサイクル（新しい `BrowserContext` = `erase`、`goto(baseUrl)` = `launch`）を **driver の内側**に置き、中立な `Environment` プロトコルを経由せず runner を `actuator == "playwright"` で分岐させました。これは動く web `run` を出荷するための最小の正しい変更でした。本項目はこれを一般化します。`platform` フィールドが `bundleId` か `baseUrl` かの選択を吸収し、プラットフォーム別のライフサイクルが `Environment` プロトコルの背後に移ることで、runner は actuator 名での分岐をやめます。

### 決定性はプラットフォームごとに保たれる

4 つの機構はどのバックエンドでも成立します。変わるのは *実装* だけです。

| 原則 | iOS | Android | Web |
|---|---|---|---|
| 曖昧なセレクタは即失敗 | `resolve_unique`（共有） | `resolve_unique`（共有） | `resolve_unique`（共有） |
| 条件待機のみ・固定 sleep なし | `query()` をポーリング | `uiautomator dump` をポーリング | Playwright 自動待機 + ポーリング |
| テストごとにクリーン環境 | `simctl erase` | `pm clear` | 新規 `new_context()` |
| 合否は機械チェックのみ | 正規化 `Element` | 正規化 `Element` | 正規化 `Element` |

`resolve_unique` と `assertions.py` は文字どおり共有コードです。決定性保証はプラットフォームごとに再実装しません。これは各バックエンドのツリーを共通 `Element` に正規化することの主目的です。

### 段階 0：継ぎ目を抽象化

本項目はプラットフォーム拡張の展開における **段階 0** で、どの第 2 プラットフォームも着地する前に行わなければならない一般化です。

| 段階 | 範囲 | この順の理由 |
|---|---|---|
| **0：継ぎ目を抽象化** | `Environment` Protocol を抽出（今は `simctl` が具体）、config に `platform` + プラットフォームスコープの backend レジストリを追加、`runner.py` / `orchestrator.py` の iOS 固有部分を監査 | 一般化コストをデバイスなしで一度だけ払います |

具体的には、段階 0 は 3 つの作業です。

- **`Environment` Protocol を抽出する。** 今日の `simctl` は具体かつ iOS 固有です。プロトコル（erase / boot / launch / deeplink / screenshot）は、「erase」「boot」がほぼ no-op になるブラウザコンテキストにも、デバイス前提を漏らさずに収まる必要があります。
- **config に `platform` + プラットフォームスコープの backend レジストリを追加する。** レジストリのスライス（[BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)）はすでに `--backend` / `backend:` をプラットフォームトークン経由でルーティングします。残る作業は明示的な `platform` 設定フィールドと、それを環境マネージャ選択へ結線することです。
- **`runner.py` / `orchestrator.py` の漏れた iOS 固有部分を監査する。** 上表の「不変」列は検証すべき主張です。最初の抽象化パスこそが、潜在する iOS 固有の前提を表に出させます。

これに乗るプラットフォーム別バックエンド（まず Web、次に Android）は別項目で扱います。Web は [BE-0041](../../implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)、Android は [BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md) です。

## 検討した代替案

- **runner をプラットフォームごとに分岐させる。** コアの前提に反するため却下。`resolve_unique` / `assertions.py` をプラットフォームごとに再実装すると、共通 `Element` ツリーへの正規化が共有するために存在する、まさにその決定性保証を重複させてしまいます。抽象の価値の全体は、決定的なコアを単一の出所に保つことにあります。
- **「1 つの YAML を 3 回実行する」を可搬性モデルとする。** 実アプリに対して不誠実なため却下。同一プロダクトの 3 アプリが画面を共有することは稀です。選んだモデルは、共有する 1 つの DSL / ランナー / ツールチェーン上のプラットフォーム別シナリオで、横断的再利用は共有 id 名前空間による opt-in です。
- **第 2 プラットフォームが実際に作られるまで抽象化を後回しにする。** 却下。最初のプラットフォームはいずれにせよ抽象化コストを払います。デバイス不要の段階 0 として行うことで、漏れた iOS 固有部分を最低コストで表に出し、第 2 プラットフォーム作業のリスクを下げられます。

## 参考

- [DESIGN §5](../../../DESIGN.md)（バックエンド非依存の `Driver` インターフェース）、[DESIGN §7 / §7.3](../../../DESIGN.md)（安定 id 規約）
- `bajutsu/drivers/`（`base.py` `resolve_unique`、`idb.py`）、`bajutsu/backends.py`（プラットフォームレジストリ）
- [architecture.md](../../../docs/ja/architecture.md)
- 関連項目: [BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md)（Android バックエンド）、[BE-0041](../../implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)（Web Playwright バックエンド）、[BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)（プラットフォーム backend レジストリ）、[BE-0010](../../proposals/BE-0010-update-scope-statement/BE-0010-update-scope-statement-ja.md)（スコープ文の更新）、[BE-0019](../../proposals/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)（XCUITest バックエンド）
