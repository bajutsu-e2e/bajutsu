[English](../../getting-started/ios.md) · **日本語**

# Getting started（iOS トラック）

> [Getting started](index.md) のループを **iOS Simulator** 上で XCUITest [backend](../glossary.md#driver-backend-actuator-platform) を使って完結させます
> （[BE-0290](../../../roadmaps/BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md) で idb を撤去して以来、iOS の唯一の backend です）。
> macOS と Xcode が必要です。Mac がないマシンでは、代わりに[web トラック](web.md)を辿ってください。
> Xcode も Simulator も要らず、同じループをブラウザに対して辿れます。

関連: [Getting started](index.md) · [web トラック](web.md) · [showcase](../showcase.md) · [drivers](../drivers.md)

まず[共通ウォークスルーのステップ 1〜3](index.md)（インストール、ユニットテスト、シナリオを読む）
を済ませてください。Mac 固有の要素は何もありません。このページはステップ 4 から続きます。

---

## 必要なもの

| 目的 | 必要なもの |
|---|---|
| ステップ 1〜3（共通） | macOS or Linux、Python 3.13（[uv](https://github.com/astral-sh/uv) で管理） |
| 以下のステップ 4〜5 | **Xcode** 入り macOS（iOS Simulator と、XCUITest backend が駆動する `xcodebuild`）、[XcodeGen](https://github.com/yonaskolb/XcodeGen)（showcase のビルド用）。追加の `brew` インストールや pip extra は不要で、XCUITest backend は Xcode だけで動きます |

## ステップ 4：ショーケースアプリをビルドする

リポジトリには showcase フィクスチャ（同じアプリを SwiftUI と UIKit で書き、各々をアクセシビリティ有／無の
変種にしたもの）が同梱されており、Bajutsu のすべてのプリミティブを計装しています。SwiftUI のアクセシビリティ
プロダクトを Simulator 向けにビルドします。

```bash
make -C demos/showcase swiftui-build         # xcodegen generate -> iOS Simulator 向けに xcodebuild
```

`demos/showcase/ios/swiftui/build/…` の下に `BajutsuShowcaseSwiftUI.app` ができます（`.xcodeproj` と `build/` は
gitignore 済みで、`project.yml` が正です）。launch-env フックと識別子カタログは [showcase](../showcase.md) を
参照してください。

## ステップ 5：Simulator 上でシナリオを走らせる

Simulator を boot します。

```bash
xcrun simctl boot "iPhone 15"                 # または Xcode > Open Developer Tool > Simulator から
```

XCUITest backend は、**事前ビルドしたオンデバイスの runner**（target の `xcuitest.testRunner`）
を通してアプリを駆動します。showcase の config はその runner を配線し、下の一発 `make` ターゲット
の一部として（`make runner-build`）ビルドまで済ませるので、追加でインストールするものはありません。
Xcode だけで十分です。

一発で通す経路は `make` ターゲットです。runner をビルドし、ビルドしたばかりのアプリを install し、
smoke シナリオと `doctor` チェックを booted デバイスで実行します。

```bash
make -C demos/showcase run-swiftui
```

あるいは CLI を直接叩くこともできます（上と同じ手順を書き下したものです）。

```bash
uv run bajutsu run --scenario demos/showcase/scenarios/smoke.yaml --target showcase-swiftui --backend ios --udid booted --no-erase
```

各フラグの意味は次のとおりです。

- `--target showcase-swiftui` は [`demos/showcase/showcase.config.yaml`](../../../demos/showcase/showcase.config.yaml) の `targets.showcase-swiftui` を選びます
  （bundle id、launch env、許可された id 名前空間を含みます）。ツール自体はアプリ非依存で、アプリ
  ごとの差分はすべて config に置きます（[configuration](../configuration.md)）。
- `--backend ios` で iOS の actuator（XCUITest。`--backend xcuitest` と明示することもできます）を選び、`--udid booted` で現在 boot 中の Simulator を対象にします。
- `--no-erase` は最初に `simctl erase` をかけず、install 済みのアプリをそのまま使います。

成功すると、次のような行が出ます。

```
PASS  runs/20260610-120000/manifest.json
```

`run` は **全シナリオ合格で終了コード 0、いずれか失敗で 1** を返し、この終了コードが CI（継続的
インテグレーション）ゲートになります（[run-loop](../run-loop.md)）。

> 環境の問題（booted Simulator が無い、Xcode のコマンドラインツールが無いなど）に当たったら、まず
> `uv run bajutsu doctor --target showcase-swiftui` を走らせてください。必要な CLI と booted デバイスの ✓/✗
> チェックリストを表示し、続けて現在の画面が識別子規約にどれだけ従っているかを採点します
> （[configuration](../configuration.md#doctor規約充足度スコア)）。

共通ウォークスルーの[ステップ 6：レポートを読む](index.md#ステップ-6レポートを読む)へ進んでください。

## AI でオーサリングする（iOS）

Claude に showcase アプリをゴールへ向けて探索させ、シナリオを書かせます（Tier 1）。
`.env` ファイルに `ANTHROPIC_API_KEY=sk-ant-…` を置いてから実行します。

```bash
uv run bajutsu record --target showcase-swiftui --goal "log in and increment the counter to 3"   # アプリのシナリオディレクトリへ書く
```

## ネイティブ XCUITest を出力する

```bash
uv run bajutsu codegen demos/showcase/scenarios/smoke.yaml --target showcase-swiftui -o UITests/Smoke.swift
```

`make -C demos/showcase ui-test` で end-to-end に実行できます。構造のマッピングは [codegen](../codegen.md) を参照してください。
