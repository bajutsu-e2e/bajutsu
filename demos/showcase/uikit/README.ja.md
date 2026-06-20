[English](README.md) · **日本語**

# Showcase：UIKit

Bajutsu showcase ドッグフード一式の UIKit 版です。挙動、identifier、起動時の環境変数フック、
ディープリンク、OS アラートを出す画面は、すべて [`../SPEC.md`](../SPEC.ja.md) に一度だけ定義され、
ここでは identifier 単位でそのまま実装しています（SwiftUI 版も同じ仕様を実装します）。そのため、
1 つのシナリオ集ですべての variant を駆動できます。画面はストーリーボードを使わずコードで組み立てて
いますが、公開する accessibility identifier、起動時の環境変数フック、ディープリンクは SwiftUI 版と
まったく同じです。

## 1 つのコードベース、2 つのビルド variant

variant の違いは Swift のコンパイル条件 `ACCESSIBLE` 1 つだけで、ソースを分岐させてはいません。
`aid(_:)` ヘルパー（[`Sources/Accessibility.swift`](Sources/Accessibility.swift)、SPEC §8）は
`UIAccessibilityIdentification` の拡張で、このフラグが定義されているときだけ identifier を付与し、
`mirror(value:)` ヘルパーが状態を `accessibilityValue` に反映します。

| ターゲット | `ACCESSIBLE` | Bundle id | 表示名 | ディープリンクのスキーム |
|---|---|---|---|---|
| `BajutsuShowcaseUIKit` | 定義あり | `com.bajutsu.showcase.uikit` | Showcase UIKit | `showcaseuikit` |
| `BajutsuShowcaseUIKitNoAx` | — | `com.bajutsu.showcase.uikit.noax` | Showcase UIKit (no a11y) | `showcaseuikitnoax` |

`-a11y` ビルドは SPEC §5 のすべての identifier を公開し（`doctor --app` は **Ready** と判定）、
no-a11y ビルドは identifier の無いツリーになります（**Blocked** と判定）。アクセシビリティ対応を
省いたときのコストを具体的に示すものです。

## ビルド

Xcode と [XcodeGen](https://github.com/yonyz/XcodeGen)（`brew install xcodegen`）が必要です。
2 つのターゲットは同じ `Sources/` ディレクトリを共有します。

```bash
cd demos/showcase/uikit
xcodegen generate          # -> BajutsuShowcaseUIKit.xcodeproj
xcodebuild -scheme BajutsuShowcaseUIKit \
  -destination 'generic/platform=iOS Simulator' build        # a11y ビルド
xcodebuild -scheme BajutsuShowcaseUIKitNoAx \
  -destination 'generic/platform=iOS Simulator' build        # no-a11y 版
```

生成される `.xcodeproj` と `build/` 出力は gitignore 済みで、`project.yml` が正本です。

## 起動時の環境変数フックとディープリンク

`SHOWCASE_` プレフィックスで起動時に `ProcessInfo` から一度だけ読み込みます。ディープリンクは上記の
variant ごとのスキームを使います。いずれも [`../SPEC.md`](../SPEC.ja.md) §3〜§4 に定義しています。
