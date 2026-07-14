[English](../multi-platform.md) · **日本語**

# マルチプラットフォームの概要

> Bajutsu は **backend 非依存のドライバを土台とするマルチプラットフォーム**で、**iOS**
> （idb / XCUITest）backend、**web**（Playwright）backend、**Android**（adb）backend はいずれも
> 実装済みです（[DESIGN §1](../../DESIGN.md)、[README](../../README.md)）。web backend は Linux
> ゲート上でブラウザに対する決定的な `run` を実行し（[drivers](drivers.md#playwrightweb) と
> `demos/web` を参照）、Android backend も同種のゲートの上でエミュレータに対して検証済みです
> （[drivers](drivers.md#adb-android) と
> [architecture → 実装状況](architecture.md#実装状況) を参照）。残るのは **Flutter** で、
> こちらは計画段階のままです。本ページは、既存の抽象を新しいプラットフォームへ
> どう広げるかの **全体像（大枠）** を示します。すなわち、何が変わらず、各プラットフォームが何を足し、
> どの順で作るかです。**プラットフォーム別の具体的な設計と実装計画はロードマップ**にあり、各項目を下に
> リンクしています。方向性は本ページで、具体はロードマップ項目で把握してください。

関連: [drivers](drivers.md) · [selectors](selectors.md) · [concepts](concepts.md) · [vision](vision.md) · [roadmap → プラットフォーム対応](../../roadmaps/README-ja.md#プラットフォーム対応)

---

## 抽象はすでにプラットフォーム形状

Bajutsu のコアは、意図的に backend 非依存の `Driver` インターフェースの背後に作られています
（[drivers](drivers.md)、[DESIGN §5](../../DESIGN.md)）。決定的な背骨（シナリオ DSL、セレクタ解決、
機械的アサーション、オーケストレータループ、証跡サブシステム、レポータ）は一切 iOS を名指ししません。
今日 iOS 固有なのは **3 つの継ぎ目** だけです。

1. **actuator**（`drivers/idb.py`）。`idb` とフレーム中心の座標タップで UI を駆動します。
2. **environment manager**（`simctl.py`）。`simctl` の boot / erase / launch / openurl を担います。
3. **安定 id の規約**（`accessibilityIdentifier`、[DESIGN §7](../../DESIGN.md)）。`Selector.id` の解決を
   決定的にする、アプリ側の供給源です。

マルチプラットフォーム対応とは、決定的コアをバイト単位で同一に保ったまま、プラットフォームごとに
**新しい三点セット**（actuator + environment + id 規約）を**足す**ことです。これは設計がすでに想定する
2 つ目の iOS actuator（XCUITest）と同じ動きを、OS をまたいで一般化したものです。

## 核心: セレクタの可搬性

シナリオがプラットフォーム間で可搬なのは、**そのセレクタが `id` による範囲に限ります**
（[concepts §4–5](concepts.md#4-安定セレクタaccessibilityidentifier-優先)）。各プラットフォームには
`accessibilityIdentifier` の native な等価物（非ローカライズで開発者が割り当てるハンドル）があり、
プラットフォーム別の id 規約がそこへ写像します。

| `Selector` フィールド | iOS | Android | Web |
|---|---|---|---|
| `id`（主） | `accessibilityIdentifier` | `resource-id`（Compose: `testTag`） | `data-testid` |
| `label`（補助） | `accessibilityLabel` | `content-desc` / `text` | アクセシブル名 / `aria-label` |
| `traits`（role フィルタ） | UI traits | widget クラス | ARIA `role` |

肝心な性質は、**YAML のセレクタ `{ id: settings.reindex }` がすでにプラットフォーム中立**だということです。異なるのは
*backend がそれを満たすためにアプリ側のどの属性を読むか* だけで、それは新しい Driver の内部に閉じ、
シナリオには出てきません。現実的なモデルは **1 つの DSL、1 つのランナー、1 つのツールチェーンを共有する
プラットフォーム別シナリオ**で、クロスプラットフォームの *再利用* は本当に一致するスライスに対する
**opt-in**（共有 id 名前空間 `auth.*`、`nav.*` を揃えて表現）です。

一点だけ事情があります。プラットフォーム本来の id 構文が SPEC の id を**そのまま**再現できないことがあります。Android の `android:id`（Views toolkit）は `.` も `-` も許さないので、`stable.refresh` は `stable_refresh` として現れます。ドライバ側で暗黙に `.`↔`_` を書き換えると別々の id を取り違えて決定性を損なうため、そうはせず、シナリオが `id` / `idMatches` に**候補のリスト**（`id: [stable.refresh, stable_refresh]`）を持って差異を**明示的に**保ち、OR として照合します。あるアプリの画面に現れる形は常に一方だけなので、決定論的なままです。これにより showcase の共有シナリオが両 Android UI toolkit でそのまま走ります（BE-0221）。[scenarios](scenarios.md#プラットフォームをまたぐ-id候補のリストbe-0221) を参照してください。

## 方向性と段階

決定的コアは、プラットフォームを足しても変わりません。追加されたのはそれぞれの三点セットだけです。
Web と Android はすでに着地しており、残るのは Flutter の段階です。

| ステップ | 範囲 | 状態 / ロードマップ項目 |
|---|---|---|
| **着手済み** | プラットフォーム対応の backend レジストリ（`--backend` / `backend:` が `ios`/`android`/`web`/`fake` を受理） | 実装済み（[BE-0042](../../roadmaps/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)） |
| **共有抽象** | `Environment` Protocol の抽出、iOS 固有の漏れの点検、セレクタ / config / 決定性の設計 | 実装済み（[BE-0009](../../roadmaps/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)） |
| **段階 1（Web）** | Playwright。既存の Linux ゲートで動き、Mac もエミュレータも不要。能力モデルの豊かな端を行使 | 実装済み（決定的 `run` ＋ `demos/web`）（[BE-0041](../../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）。豊かな端の capability（network / video / multi-touch / 並列）は [BE-0054](../../roadmaps/BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md) |
| **段階 2（Android）** | adb + UI Automator。座標駆動の idb の双子 | 実装済み（[BE-0007](../../roadmaps/BE-0007-android-backend/BE-0007-android-backend-ja.md)）。エミュレータ e2e CI は [BE-0208](../../roadmaps/BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)、UI Automator codegen は [BE-0209](../../roadmaps/BE-0209-android-codegen-emitter/BE-0209-android-codegen-emitter-ja.md)、id の可搬性保証は [BE-0221](../../roadmaps/BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee-ja.md) |
| **段階 3（Flutter / ハイブリッド）** | 自前描画 UI は新しい OS actuator ではなく semantics ブリッジが必要 | 計画（[BE-0008](../../roadmaps/BE-0008-flutter-support/BE-0008-flutter-support-ja.md)） |
| **横断** | マルチプラットフォーム化は戦略的なスコープ変更（DESIGN / README / docs） | 実装済み（[BE-0010](../../roadmaps/BE-0010-update-scope-statement/BE-0010-update-scope-statement-ja.md)） |

**なぜ Android より先に Web か**（Android のほうが idb の近い双子であるにもかかわらず）。Web は macOS も
デバイスエミュレータも不要な唯一のプラットフォームで、初日から [`make check`](../../CLAUDE.md) /
[CI](ci.md) ゲートの内側に収まりました。コアがプラットフォーム中立であることを最小コストで証明できたのです。
その後 Android が、一般化済みのコアの上で lean / 座標パスを、自前のエミュレータ付きゲートで裏づけました
（[BE-0208](../../roadmaps/BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)）。
残る Android 固有のギャップ（ネイティブのタブバー操作など一部のデバイス制御操作）は、プラットフォーム
単位ではなく項目単位で [architecture → 実装状況](architecture.md#実装状況) に追跡されています。

## どのプラットフォームでも変わらないもの

共有の決定的コアは、プラットフォームを足しても **分岐しません**。シナリオ DSL と文法
（[scenarios](scenarios.md) / [dsl-grammar](dsl-grammar.md)）、セレクタ解決（[selectors](selectors.md)）、
機械的アサーション、observe → act → verify オーケストレータ（[run-loop](run-loop.md)）、証跡サブシステム
（[evidence](evidence.md)）、レポータ（[reporting](reporting.md)）がそれにあたります。**prime directive** は Android でも
Web でも同一に効きます。決定性ファースト、app 非依存、そして **AI は判定者にならない**ことです。いかなる新規
プラットフォームも Tier-2 の `run` / CI ゲートに LLM を入れてはなりません。

> **ロードマップとの関係。** 本ページは概要で、優先度付きの具体的な計画は上記の
> [プラットフォーム対応](../../roadmaps/README-ja.md#プラットフォーム対応)の各項目です。
> プラットフォームが出荷されると、[architecture の実装状況表](architecture.md#実装状況)にも移ります。
