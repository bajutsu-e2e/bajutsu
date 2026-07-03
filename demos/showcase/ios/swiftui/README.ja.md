[English](README.md) · **日本語**

# Showcase：SwiftUI

Bajutsu showcase ドッグフード一式の SwiftUI 版です。挙動、identifier、起動時の環境変数フック、
ディープリンク、OS アラートを出す画面は、すべて [`../SPEC.md`](../SPEC.ja.md) に一度だけ定義され、
ここでは identifier 単位でそのまま実装しています（UIKit 版も同じ仕様を実装します）。そのため、
1 つのシナリオ集ですべての variant を駆動できます。

## 1 つのコードベース、2 つのビルド variant

variant の違いは Swift のコンパイル条件 `ACCESSIBLE` 1 つだけで、ソースを分岐させてはいません。
`aid(_:)` ヘルパー（[`Sources/AID.swift`](Sources/AID.swift)、SPEC §8）は、このフラグが定義されて
いるときだけ identifier を付与し、状態を `accessibilityValue` に反映します。

| ターゲット | `ACCESSIBLE` | Bundle id | 表示名 | ディープリンクのスキーム |
|---|---|---|---|---|
| `BajutsuShowcaseSwiftUI` | 定義あり | `com.bajutsu.showcase.ios.swiftui` | Showcase SwiftUI | `showcaseswiftui` |
| `BajutsuShowcaseSwiftUINoAx` | — | `com.bajutsu.showcase.ios.swiftui.noax` | Showcase SwiftUI (no a11y) | `showcaseswiftuinoax` |

`-a11y` ビルドは SPEC §5 のすべての identifier を公開し（`doctor --target` は **Ready** と判定）、
no-a11y ビルドは identifier の無いツリーになります（**Blocked** と判定）。アクセシビリティ対応を
省いたときのコストを具体的に示すものです。

## ビルド

Xcode と [XcodeGen](https://github.com/yonyz/XcodeGen)（`brew install xcodegen`）が必要です。
2 つのターゲットは同じ `Sources/` ディレクトリを共有します。

```bash
cd demos/showcase/ios/swiftui
xcodegen generate          # -> BajutsuShowcaseSwiftUI.xcodeproj
xcodebuild -scheme BajutsuShowcaseSwiftUI \
  -destination 'generic/platform=iOS Simulator' build        # a11y ビルド
xcodebuild -scheme BajutsuShowcaseSwiftUINoAx \
  -destination 'generic/platform=iOS Simulator' build        # no-a11y 版
```

生成される `.xcodeproj` と `build/` 出力は gitignore 済みで、`project.yml` が正本です。

## 起動時の環境変数フックとディープリンク

`SHOWCASE_` プレフィックスで起動時に `ProcessInfo` から一度だけ読み込みます。ディープリンクは上記の
variant ごとのスキームを使います。いずれも [`../SPEC.md`](../SPEC.ja.md) §3〜§4 に定義しています。
