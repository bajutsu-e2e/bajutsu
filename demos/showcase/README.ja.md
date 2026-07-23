# Showcase：dogfood アプリ群

[English](README.md) · **日本語**

showcase は Bajutsu の次世代 dogfood 対象です。**同じアプリを 2 回書き**（UIKit と SwiftUI）、**各々を
アクセシビリティの 2 変種**（識別子の有/無）で出すので、2 コードベースから 4 つのインストール可能な
プロダクトができます。実アプリが持つ操作面（5 タブ、ナビゲーションスタックの push、4 つのモーダル様式、
テキスト入力、非同期ロード、通信（実通信＋モック可能）、そして意図的に OS レベルのアラートを上げる
画面）を、`record`、`crawl`、`run` を一度に行使できる最小のアプリに収めています。

- **画面：** 全 10 画面の一覧（5 タブ＋push＋3 モーダル）は [`SPEC.md` §5](SPEC.ja.md#画面一覧) にあります。
  タブは **Stable・Search・Log・Notices・Permissions** です。
- **契約：** [`SPEC.ja.md`](SPEC.ja.md)（[en](SPEC.md)）に、すべての画面、識別子、launch-env フック、
  deeplink、OS アラートの配置を記します。2 つの `-a11y` アプリは同一の識別子契約を露出するので、1 つの
  [`scenarios/`](scenarios) 集が両方を駆動します。
- **ロードマップ項目：** [BE-0045](../../roadmaps/BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps-ja.md)
  が showcase 群の根拠を記録しています。[BE-0079](../../roadmaps/BE-0079-consolidate-demos-on-showcase/BE-0079-consolidate-demos-on-showcase-ja.md)
  が showcase への統合を完了させました（ロードマップの仕組みは [`roadmaps`](../../roadmaps/README-ja.md) を参照してください）。

## 4 つのプロダクト

| `targets.<name>` | ツールキット | アクセシビリティ | 示すもの |
|---|---|---|---|
| `showcase-swiftui` | SwiftUI | 有 | `run`（id ベース）、`doctor` → Ready |
| `showcase-uikit` | UIKit | 有 | 同じシナリオ、別ツールキット |
| `showcase-swiftui-noax` | SwiftUI | 無 | `record`（ladder フォールバック）、`doctor` → Blocked |
| `showcase-uikit-noax` | UIKit | 無 | 同上、別ツールキット |

変種の差は Swift のコンパイルフラグ `ACCESSIBLE` ただ 1 つ（SPEC §8）で、ソースの分岐はありません。
`-noax` ビルドは識別子を **持たない** ツリーにコンパイルされます。アクセシビリティを省いたチームが
出荷するアプリを、そのままテスト可能にしたものです。

**Android 版**（[`android/`](android/)、SPEC §2.1）：同じフィクスチャの Android 版も、BE-0007 の
adb バックエンドに先行して用意してあります。Jetpack Compose 版が SwiftUI に、Android Views 版が
UIKit に対応し、それぞれ同じ a11y/noax の flavor ペアでビルドします（プロダクトが 4 つ増え、
`make -C demos/showcase/android build-all` でビルドできます）。BE-0007 が実装されるまではビルドのみ
可能で、実行はできません（`--backend android` は「not implemented yet」と報告します）。

## ビルド

[XcodeGen](https://github.com/yonaskolb/XcodeGen)（`brew install xcodegen`）と Xcode が必要です。

```bash
make -C demos/showcase build-all          # 4 プロダクトすべて
make -C demos/showcase swiftui-build      # SwiftUI a11y プロダクトのみ
make -C demos/showcase uikit-noax-build   # UIKit no-a11y プロダクトのみ
```

各ビルドは `…/build/dd/Build/Products/Debug-iphonesimulator/<Scheme>.app` に出力され、これは
`showcase.config.yaml` の `appPath` が期待する場所そのものです。生成される `*.xcodeproj` と `build/` は
gitignore 済みです。

## 実行（起動済み Simulator 上）

前提：起動済み Simulator、Xcode、XCUITest ランナー（`make -C demos/showcase runner-build`）。pip の extra は不要です。

```bash
# run — 共有の id ベースシナリオを、どちらの a11y ツールキットにも（同じシナリオで）:
make -C demos/showcase run-swiftui
make -C demos/showcase run-uikit

# doctor — アクセシビリティの A/B：Ready（a11y）vs Blocked（no-a11y）:
make -C demos/showcase doctor

# record — no-a11y アプリへの AI オーサリング（ANTHROPIC_API_KEY が必要）:
make -C demos/showcase record
```

または `bajutsu` を直接駆動します（常にこの群の config を渡す）:

```bash
bajutsu run --target showcase-swiftui --backend ios --config demos/showcase/showcase.config.yaml
bajutsu run --target showcase-swiftui --scenario demos/showcase/scenarios/modals.yaml \
    --backend ios --config demos/showcase/showcase.config.yaml
```

## ここにあるもの

| パス | 内容 |
|---|---|
| [`SPEC.md`](SPEC.md) | 画面ごとの契約（仕様書） |
| [`WEBUI.ja.md`](WEBUI.ja.md) | Web UI ツアー。ブラウザから Simulator を操作し、あらゆる証跡を収集する |
| [`ios/swiftui/`](ios/swiftui)、[`ios/uikit/`](ios/uikit) | 2 つの iOS コードベース（xcodegen `project.yml`、各 2 ターゲット） |
| [`ios/scenarios-noax/`](ios/scenarios-noax) | `scenarios/` の `-noax` 版。同じ操作を可視ラベルと画面上のミラーテキストで表現し、XCUITest（`--backend ios`）で実行。ラベルで到達できる（座標ベースのバックエンドでは届かない）`-noax` ターゲットを駆動 |
| [`android/`](android/) | Android 版の 4 プロダクト（Compose × Views、BE-0007 の準備） |
| [`showcase.config.yaml`](showcase.config.yaml) | iOS と Android を合わせた 8 つの `targets.<name>` エントリ |
| [`scenarios/`](scenarios) | 共有の id ベース `run` シナリオ（iOS と Android の両方の a11y アプリを駆動） |
| [`record/goals.txt`](record/goals.txt) | `record` A/B デモ用の自然言語ゴール |
| [`crawl/`](crawl/expected-screen-map.ja.md) | `crawl`（[BE-0038](../../roadmaps/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)、実装中）が生成すべき画面マップ。検証用テストデータ |

### 対応づいた 2 つのシナリオスイート: `scenarios/`（a11y）↔ `ios/scenarios-noax/`（no-a11y）

`-noax` アプリは accessibility identifier を持たず、ミラーした a11y value も持たないので、id ベースの
共有スイートでは駆動できません。`ios/scenarios-noax/` はそのラベル・可視テキスト版の対応物です。
`scenarios/` の各操作に 1 対 1 で対応し、動作は同じですが、各ステップは要素を**可視ラベル**で指定し、
各アサーションはアプリが同時に描画する画面上の**ミラー `Text`**（たとえば `Text("Segment: \(segment)")`、
`Text(favorite ? "Favorited" : "Not favorited")`）を読みます。落ちてしまう a11y value の代わりです。
このスイートは、各 `-noax` 要素にその可視ラベルで到達できる（座標ベースのバックエンドでは届かない）
XCUITest で実行します（`make -C demos/showcase run-swiftui-noax` / `run-uikit-noax`）。可視の
ミラー文字列は `AppModel` と各 `*Controller`/`*View` のラベルで一致するので、1 つのスイートが `-noax` の
両方の toolkit を駆動します。

`scenarios/` のすべてのファイルに対応物があるわけではありません。これは意図した設計です。

- **`components.yaml`**：目的は `codegen` による *id* を鍵にした native XCUITest の生成で、`-noax`
  ビルドには id がありません。その操作（filter sheet、gallery、search）は `modals.yaml` と `search.yaml`
  の対応物ですでに覆われています。
- **`visual.yaml` / `golden/`**：ピクセルやツリーの baseline は id と画像に固有で、別の関心事です。
- **`text_editing.yaml`**：テキストを入力すると Search フィールドは placeholder を失うため、`-noax`
  実行では後続の `select`／`clear`／`delete` でフィールドを可視ラベル「Search horses」で再指定できません。
  id スイートが 4 つの編集アクチュエータを両方の a11y toolkit（および adb 経由の Android）で覆っています。

2 つの対応物は **SwiftUI 専用**です（`tags: [swiftui]`、`run-uikit-noax` が除外します）。`gestures_multitouch`
（`SHOWCASE_GESTURES` の pinch/rotate 画面は SwiftUI と Compose にはありますが UIKit にはありません。a11y の
対応物と同じ差です）と、`generated.yaml`（`showcase-swiftui-noax` で採取した `record` の出力）です。
`permission.yaml` は 1 対 1 の対応のために残していますが、a11y の対応物とまったく同様に、iOS では
**非決定的**です（SpringBoard の許可を AI の alert guard が処理します）。決定的なゲートではありません。
これらの `-noax` 実行はオンデバイス専用で（`make check` には含みません）、ラベル・可視テキストのセレクタは
id より脆いので、スクロール回数は toolkit ごとにオンデバイスで調整が要ることがあります。

## Deeplink

deeplink scheme はプロダクトごとに分けています（2 つのインストール済みアプリが衝突しないようにするため）：`showcaseswiftui`、
`showcaseuikit`、および `…noax` 変種です。`bajutsu` は URL をリテラルに開くので、共有シナリオは deeplink では
なく `launchEnv` ＋ タップ（scheme 非依存）を使います。deeplink はタブを選択するだけで、詳細画面を push する
ことはありません（BE-0079）。deeplink を直接行使するには次のようにします:

```bash
xcrun simctl openurl booted showcaseswiftui://log
xcrun simctl openurl booted showcaseuikit://permissions
```

## 唯一の iOS フィクスチャ

showcase は Bajutsu で唯一の iOS フィクスチャです。BE-0079 で showcase を同等機能まで引き上げ、すべての
デモと実機 CI ジョブの向き先を showcase に変えたうえで、旧来の単一変種アプリ（`demo`、`sample`、`sample2`）を
退役させました。
