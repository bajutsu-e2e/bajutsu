[English](../../getting-started/ios.md) · **日本語**

# iOS Simulator でシナリオを実行する（Getting started の iOS トラック）

> このページでは、[Getting started](index.md) のループを **iOS Simulator** 上で完結させます。
> showcase アプリをビルドし、シナリオを1つ実行して、合格したことを確かめます。Bajutsu は
> XCUITest [backend](../glossary.md#driver-backend-actuator-platform) を通して Simulator を操作します。
> XCUITest は、[BE-0290](../../../roadmaps/BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md)
> で idb を撤去して以来、iOS で唯一の backend です。Mac がないマシンでは、代わりに
> [web トラック](web.md)を辿ってください。同じループをブラウザに対して実行できます。

関連: [Getting started](index.md) · [web トラック](web.md) · [showcase](../showcase.md) · [drivers](../drivers.md)

---

## 前提条件

このページは、共通ウォークスルーのステップ 4 と 5 を扱います。始める前に、次の表の条件をすべて
満たしていることを確かめてください。

| 条件 | 内容 |
|---|---|
| [共通ウォークスルー](index.md)のステップ 1〜3 | インストール、ユニットテストの実行、シナリオの読み方を済ませます。この3つのステップは macOS を必要としません。 |
| **Xcode** の入った macOS | Xcode が、iOS Simulator と `xcodebuild` を提供します。XCUITest backend は `xcodebuild` を通して Simulator を操作します。 |
| [XcodeGen](https://github.com/yonaskolb/XcodeGen) | showcase のビルドは、XcodeGen で Xcode プロジェクトを生成します。 |

XCUITest backend には、`brew` の追加パッケージも pip の extra も要りません。Xcode だけで動きます。

## ステップ 4：showcase アプリと runner をビルドする

showcase アプリは、このウォークスルーで操作する対象のアプリです。リポジトリには、同じアプリを
SwiftUI と UIKit の2通りで書いたものが入っています。どちらにも、アクセシビリティ識別子を付けた版と
付けていない版があります。showcase アプリは、Bajutsu が扱えるすべての操作とアサーションを備えています。
SwiftUI で書いた識別子付きの版を、Simulator 向けにビルドします。

```bash
make -C demos/showcase swiftui-build         # xcodegen generate -> iOS Simulator 向けに xcodebuild
```

次のパスにアプリのバンドルがあれば、ビルドは成功しています。

```bash
ls -d demos/showcase/ios/swiftui/build/dd/Build/Products/Debug-iphonesimulator/BajutsuShowcaseSwiftUI.app
```

生成された `.xcodeproj` と `build/` は、Git の管理から外してあります。プロジェクトの定義は、
リポジトリに含まれる `project.yml` が持ちます。起動時の環境変数と識別子の一覧は
[showcase](../showcase.md) のページにあります。

XCUITest backend は、**事前にビルドしたオンデバイスの runner** を通してアプリを操作します。runner の
場所は、ターゲットの `driver.runner.testRunner` が指定します。runner を一度ビルドします。

```bash
make -C demos/showcase runner-build
```

runner のテスト実行ファイルがあれば、ビルドは成功しています。

```bash
ls BajutsuKit/Runner/build/dd/Build/Products/BajutsuRunner.xctestrun
```

## ステップ 5：Simulator でシナリオを実行する

ステップ 5 では、Simulator を起動し、smoke シナリオを実行して、その判定を読みます。

### Simulator を起動する

コマンドラインから Simulator を1台起動します。

```bash
xcrun simctl boot "iPhone 15"                 # または Xcode > Open Developer Tool > Simulator から起動
```

次のコマンドの出力に `(Booted)` 付きで表示されれば、Simulator は使える状態です。

```bash
xcrun simctl list devices booted
```

### smoke シナリオを実行する

起動した Simulator で smoke シナリオを実行します。

```bash
uv run bajutsu run --scenario demos/showcase/scenarios/smoke.yaml --target showcase-swiftui --backend ios --udid booted --no-erase --config demos/showcase/showcase.config.yaml
```

各フラグの意味は次のとおりです。

- `--config demos/showcase/showcase.config.yaml` は、showcase の config を読みます。このフラグがないと、
  `run` はカレントディレクトリの `bajutsu.config.yaml` を読みます。リポジトリのルートにあるこのファイルは、
  ドキュメントサイト用のターゲットしか定めていません。
- `--target showcase-swiftui` は、ターゲットの定義 `targets.showcase-swiftui` を選びます。この定義は
  [`demos/showcase/showcase.config.yaml`](../../../demos/showcase/showcase.config.yaml) にあります。
  アプリの bundle id、起動時の環境変数、許可する id の名前空間を定めています。ツール自体はどのアプリでも
  同じで、アプリごとの違いは config に置きます（[configuration](../configuration.md)）。
- `--backend ios` は backend を確かめます。ターゲットの `platform: ios` が、すでに XCUITest の actuator
  を選んでいます。`--backend xcuitest` と書けば、同じ actuator を明示できます。ターゲットと食い違う値を
  渡すと、終了コード 2 で終わります。
- `--udid booted` は、上で起動した Simulator を対象にします。
- `--no-erase` は、最初に `simctl erase` をかけず、インストール済みのアプリをそのまま使います。

### 実行結果を確かめる

最後の行が `PASS` で始まれば、実行は合格です。

```
PASS  runs/20260610-120000/manifest.json
```

`PASS` の後ろのパスは、その実行のマニフェストです。コマンドは、すべてのシナリオが合格すると終了コード 0
で、いずれかが失敗すると終了コード 1 で終わります。CI（継続的インテグレーション）のゲートは、この
終了コードで合否を決めます（[run-loop](../run-loop.md)）。

`FAIL` で始まる行も、同じようにマニフェストを示します。失敗の原因が環境にあることもあります。
起動中の Simulator がない場合や、Xcode のコマンドラインツールがない場合です。`doctor` で環境を確かめます。

```bash
uv run bajutsu doctor --target showcase-swiftui --config demos/showcase/showcase.config.yaml
```

`doctor` は、必要なコマンドラインツールと起動中のデバイスについて、✓/✗ のチェックリストを表示します。
続けて、いま表示中の画面が識別子の規約にどれだけ従っているかを採点します
（[configuration](../configuration.md#doctor規約充足度スコア)）。

ここまで済んだら、共通ウォークスルーの[ステップ 6：レポートを読む](index.md#ステップ-6レポートを読む)
へ進んでください。

## iOS でできるその他のこと

以下の節は、上の実行を土台にした発展的な使い方です。共通ウォークスルーには含まれません。

### showcase のシナリオ一式を実行する

`make` ターゲットを1つ実行すると、SwiftUI 版アプリに対する showcase のシナリオ一式を実行できます。
このターゲットは、アプリと runner をビルドし、ビルドしたアプリをインストールします。続けて、
最初に見つかった起動中の Simulator でシナリオ一式を実行します。

```bash
make -C demos/showcase run-swiftui
```

このターゲットは、専用の `make` ターゲットを持つシナリオを実行しません。別のプラットフォームの
デバイス、あらかじめ起動したフィクスチャのページ、ピクセル比較の基準画像、整えたデバイスの状態など、
それぞれが固有の前提を必要とするからです。これらのシナリオのタグを `run` に渡して除外します。

```bash
--exclude xcuitest,systemalert,visual,android,browser,app,multi-target
```

除外するタグの一覧は、[`demos/showcase/Makefile`](../../../demos/showcase/Makefile) の `run-swiftui`
レシピにあります。

### AI でシナリオを書く

Claude に showcase アプリをゴールへ向けて探索させ、シナリオを書かせることができます。AI によるオーサリングは
[Tier 1](../glossary.md#2-つの層) に属し、合否を決めることはありません。
`.env` ファイルに `ANTHROPIC_API_KEY=sk-ant-…` を書いてから、次のコマンドを実行します。

```bash
uv run bajutsu record --target showcase-swiftui --goal "log in and increment the counter to 3" --config demos/showcase/showcase.config.yaml   # アプリのシナリオディレクトリへ書く
```

### ネイティブの XCUITest を出力する

`codegen` は、シナリオをネイティブの XCUITest のソースファイルに変換します。

```bash
uv run bajutsu codegen demos/showcase/scenarios/smoke.yaml --target showcase-swiftui -o UITests/Smoke.swift --config demos/showcase/showcase.config.yaml
```

生成したコードが実際にコンパイルできることは、`make -C demos/showcase ui-test` で確かめられます。
このターゲットは、上とは別のシナリオ `components.yaml` を使います。そのシナリオから、リポジトリに
含まれるテストファイルを生成し直します。続けて、そのテストファイルを `xcodebuild test` でビルドして
実行します。上で生成した `Smoke.swift` を実行するわけではありません。シナリオの各ステップが
XCUITest のどのコードに対応するかは、[codegen](../codegen.md) のページにあります。
