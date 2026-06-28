[English](../multi-platform.md) · **日本語**

# Android への拡張（マルチプラットフォーム）の概要

> 将来構想の概要です。Bajutsu は **backend 非依存のドライバを土台とするマルチプラットフォーム**で、
> **iOS Simulator**（idb）と **Web（Playwright）backend** はどちらも実装済みです
> （[DESIGN §1](../../DESIGN.md)、[README](../../README.md)）。web backend は Linux ゲート上でブラウザに対する
> 決定的な `run` を実行します（[drivers](drivers.md#playwrightweb) と `demos/web` を参照）。**Android**
> （エミュレータ）と **Flutter** は計画段階のままです。本ページは、既存の抽象を新しいプラットフォームへ
> どう広げるかの **全体像（大枠）** を示します。すなわち、何が変わらず、各プラットフォームが何を足し、
> どの順で作るかです。**プラットフォーム別の具体的な設計と実装計画はロードマップ**にあり、各項目を下に
> リンクしています。方向性は本ページで、具体はロードマップ項目で把握してください。

関連: [drivers](drivers.md) · [selectors](selectors.md) · [concepts](concepts.md) · [vision](vision.md) · [roadmap → プラットフォーム拡張](../../roadmaps/README-ja.md#プラットフォーム拡張android--web--flutter)

---

## 抽象はすでにプラットフォーム形状

Bajutsu のコアは、意図的に backend 非依存の `Driver` インターフェースの背後に作られています
（[drivers](drivers.md)、[DESIGN §5](../../DESIGN.md)）。決定的な背骨（シナリオ DSL、セレクタ解決、
機械的アサーション、オーケストレータループ、証跡サブシステム、レポータ）は一切 iOS を名指ししません。
今日 iOS 固有なのは **3 つの継ぎ目** だけです。

1. **actuator**（`drivers/idb.py`）。`idb` とフレーム中心の座標タップで UI を駆動します。
2. **environment manager**（`env.py`）。`simctl` の boot / erase / launch / openurl を担います。
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

## 方向性と段階（実装予定の内容）

決定的コアは変わらず、各プラットフォームは自分の三点セットを足すだけです。最初のスライスはすでに着手済み
で、残りは一般化コストを最も安く払える順に並べています。

| ステップ | 範囲 | 状態 / ロードマップ項目 |
|---|---|---|
| **着手済み** | プラットフォーム対応の backend レジストリ（`--backend` / `backend:` が `ios`/`android`/`web`/`fake` を受理） | 実装済み（[BE-0042](../../roadmaps/implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)） |
| **共有抽象** | `Environment` Protocol の抽出、iOS 固有の漏れの点検、セレクタ / config / 決定性の設計 | 計画（[BE-0009](../../roadmaps/proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)） |
| **段階 1（Web）** | Playwright。**既存の Linux ゲートで動き、Mac もエミュレータも不要**。能力モデルの豊かな端を行使。最初に推奨 | 実装済み（決定的 `run` ＋ `demos/web`）（[BE-0041](../../roadmaps/implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）。豊かな端の capability（network / video / multi-touch / 並列）は [BE-0054](../../roadmaps/implemented/BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md) で追跡 |
| **段階 2（Android）** | adb + UI Automator。座標駆動の idb の双子 | 計画（[BE-0007](../../roadmaps/proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md)） |
| **段階 3（Flutter / ハイブリッド）** | 自前描画 UI は新しい OS actuator ではなく semantics ブリッジが必要 | 計画（[BE-0008](../../roadmaps/proposals/BE-0008-flutter-support/BE-0008-flutter-support-ja.md)） |
| **横断** | マルチプラットフォーム化は戦略的なスコープ変更（DESIGN / README / docs） | 実装済み（[BE-0010](../../roadmaps/implemented/BE-0010-update-scope-statement/BE-0010-update-scope-statement-ja.md)） |

**なぜ Android より先に Web か**（Android のほうが idb の近い双子であるにもかかわらず）。Web は macOS も
デバイスエミュレータも不要な唯一のプラットフォームで、初日から現在の [`make check`](../../CLAUDE.md) /
[CI](ci.md) ゲートの内側に収まります。コアがプラットフォーム中立であることを最小コストで証明できるのです。
その後 Android が、一般化済みのコアの上で lean / 座標パスを裏づけます。

## どのプラットフォームでも変わらないもの

共有の決定的コアは、プラットフォームを足しても **分岐しません**。シナリオ DSL と文法
（[scenarios](scenarios.md) / [dsl-grammar](dsl-grammar.md)）、セレクタ解決（[selectors](selectors.md)）、
機械的アサーション、observe → act → verify オーケストレータ（[run-loop](run-loop.md)）、証跡サブシステム
（[evidence](evidence.md)）、レポータ（[reporting](reporting.md)）がそれにあたります。**プライムディレクティブ**は Android でも
Web でも同一に効きます。決定性ファースト、app 非依存、そして **AI は判定者にならない**ことです。いかなる新規
プラットフォームも Tier-2 の `run` / CI ゲートに LLM を入れてはなりません。

> **ロードマップとの関係。** 本ページは概要で、優先度付きの具体的な計画は上記の
> [プラットフォーム拡張](../../roadmaps/README-ja.md#プラットフォーム拡張android--web--flutter)の各項目です。
> プラットフォームが出荷されると、[architecture の実装状況表](architecture.md#実装状況)にも移ります。
