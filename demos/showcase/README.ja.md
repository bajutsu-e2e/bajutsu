# Showcase：dogfood アプリ群

[English](README.md) · **日本語**

showcase は Bajutsu の次世代 dogfood 対象です。**同じアプリを 2 回書き**（UIKit と SwiftUI）、**各々を
アクセシビリティの 2 変種**（識別子の有/無）で出すので、2 コードベースから 4 つのインストール可能な
プロダクトができます。実アプリが持つ操作面（5 タブ、ナビゲーションスタックの push、4 つのモーダル様式、
テキスト入力、非同期ロード、通信（実通信＋モック可能）、そして意図的に OS レベルのアラートを上げる
画面）を、`record`、`crawl`、`run` を一度に行使できる最小のアプリに収めています。

- **画面：** 全 10 画面の一覧（5 タブ＋push＋3 モーダル）は [`SPEC.md` §5](SPEC.ja.md#画面一覧) にあります。
  タブは **Stable・Search・Log・Notices・Permissions** です。
- **契約：** [`SPEC.md`](SPEC.md)（[en](SPEC.md)）に、すべての画面、識別子、launch-env フック、deeplink、
  OS アラートの配置を記します。2 つの `-a11y` アプリは同一の識別子契約を露出するので、1 つの
  [`scenarios/`](scenarios) 集が両方を駆動します。
- **ロードマップ項目：** [`roadmaps`](../../roadmaps/README-ja.md) の `dogfood-showcase-apps`
  BE 項目が根拠を記録します。

## 4 つのプロダクト

| `apps.<name>` | ツールキット | アクセシビリティ | 示すもの |
|---|---|---|---|
| `showcase-swiftui` | SwiftUI | 有 | `run`（id ベース）、`doctor` → Ready |
| `showcase-uikit` | UIKit | 有 | 同じシナリオ、別ツールキット |
| `showcase-swiftui-noax` | SwiftUI | 無 | `record`（ladder フォールバック）、`doctor` → Blocked |
| `showcase-uikit-noax` | UIKit | 無 | 同上、別ツールキット |

変種の差は Swift のコンパイルフラグ `ACCESSIBLE` ただ 1 つ（SPEC §8）で、ソースの分岐はありません。
`-noax` ビルドは識別子を **持たない** ツリーにコンパイルされます。アクセシビリティを省いたチームが
出荷するアプリを、そのままテスト可能にしたものです。

## ビルド

[XcodeGen](https://github.com/yonsm/XcodeGen)（`brew install xcodegen`）と Xcode が必要です。

```bash
make -C demos/showcase build-all          # 4 プロダクトすべて
make -C demos/showcase swiftui-build      # SwiftUI a11y プロダクトのみ
make -C demos/showcase uikit-noax-build   # UIKit no-a11y プロダクトのみ
```

各ビルドは `…/build/dd/Build/Products/Debug-iphonesimulator/<Scheme>.app` に出力され、これは
`showcase.config.yaml` の `appPath` が期待する場所そのものです。生成される `*.xcodeproj` と `build/` は
gitignore 済みです。

## 実行（起動済み Simulator 上）

前提：起動済み Simulator、`brew install facebook/fb/idb-companion`、`uv sync --extra idb`。

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
bajutsu run --app showcase-swiftui --backend idb --config demos/showcase/showcase.config.yaml
bajutsu run --app showcase-swiftui --scenario demos/showcase/scenarios/modals.yaml \
    --backend idb --config demos/showcase/showcase.config.yaml
```

## ここにあるもの

| パス | 内容 |
|---|---|
| [`SPEC.md`](SPEC.md) | 画面ごとの契約（仕様書） |
| [`swiftui/`](swiftui)、[`uikit/`](uikit) | 2 つのコードベース（xcodegen `project.yml`、各 2 ターゲット） |
| [`showcase.config.yaml`](showcase.config.yaml) | 4 つの `apps.<name>` エントリ |
| [`scenarios/`](scenarios) | 共有の id ベース `run` シナリオ（両 a11y アプリを駆動） |
| [`record/goals.txt`](record/goals.txt) | `record` A/B デモ用の自然言語ゴール |
| [`crawl/`](crawl/expected-screen-map.ja.md) | 将来の `crawl` が生成すべき画面マップ（テストデータ） |

## Deeplink

deeplink scheme はプロダクトごとに分けています（2 つのインストール済みアプリが衝突しないようにするため）：`showcaseswiftui`、
`showcaseuikit`、および `…noax` 変種です。`bajutsu` は URL をリテラルに開くので、共有シナリオは deeplink では
なく `launchEnv` ＋ タップ（scheme 非依存）を使います。deeplink を直接行使するには次のようにします:

```bash
xcrun simctl openurl booted showcaseswiftui://horse/2
xcrun simctl openurl booted showcaseuikit://permissions
```

## `sample` との関係

showcase は旧来の単一アプリ [`sample` フィクスチャ](../features/app/README.ja.md)を置き換えます。
`sample` は、showcase がその実機 CI と Web UI ツアーもカバーするまで残します。移行の注記は BE 項目を
参照してください。
