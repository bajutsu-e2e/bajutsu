[English](BE-0025-coordinate-swipe-generation.md) · **日本語**

# BE-0025 — 座標 swipe の生成

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0025](BE-0025-coordinate-swipe-generation-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| 実装 PR | [#217](https://github.com/bajutsu-e2e/bajutsu/pull/217) |
| トラック | [承認済み](../../README-ja.md#承認済み) |
| トピック | codegen 網羅性 |
<!-- /BE-METADATA -->

## はじめに

現状 `swipe { from, to }` は `// TODO` にフォールバックします。

## 動機

`codegen` は、成功したシナリオをネイティブの XCUITest へマッピングします。このマッピングは純粋に構造的なので、シナリオが表現できるすべてのステップには、生成側にも対応物があるべきです。そうでなければ、出力されるテストは黙ったまま不完全になります。現状、セレクタ形式の `swipe`（`{ on, direction }`）は `swipeUp/Down/Left/Right()` にマッピングされますが、座標形式（`{ from, to }`）は `// TODO` コメントしか出力しません。そのため、座標で swipe するシナリオは `run` では実行できても、チームが生成した XCUITest を自前の Xcode CI に持ち込むとそのジェスチャを失います。差は小さく見えますが実害があります。座標 swipe は、セレクタ形式では届かないケース（地図のパン、独自描画のキャンバス、アドレス可能な要素のないドラッグ）のために存在するもので、それはまさにチームが最も残したいと望むケースだからです。

## 詳細設計

XCUITest は任意のドラッグを `XCUICoordinate.press(forDuration:thenDragTo:)` で表現します。生成されるヘルパは、安定したアンカーを基準に 2 つの座標を組み立て、その間をドラッグします。

```swift
private func coord(_ x: CGFloat, _ y: CGFloat) -> XCUICoordinate {
  let origin = app.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
  return origin.withOffset(CGVector(dx: x, dy: y))
}
```

`swipe: { from: [x1, y1], to: [x2, y2] }` は `coord(x1, y1).press(forDuration: 0.1, thenDragTo: coord(x2, y2))` を出力します。これは idb バックエンドと同じ挙動です。idb 側も、SwiftUI が瞬間的なフリックではなくパンとしてドラッグを認識できるよう、すでに短い duration を加えています。

これは prime directive の範囲に収まります。

- **決定性。** シナリオ内の座標は固定された数値なので、生成されるドラッグは再現可能です。マッピングは純粋に構造的なままで、生成時に AI を参照せず、run／CI ゲートにも影響しません。
- **可搬性。** 座標は `withNormalizedOffset` でアプリウィンドウの左上を基準にします。これは bajutsu がすでに用いている原点の取り方と同じなので、生成テストは `run` と同じ意味で数値を読みます。座標形式は引き続き、ドキュメント上の最終手段です（[scenarios](../../../docs/ja/scenarios.md#swipe)）。セレクタ形式が優先されるのは、まさにレイアウト変更に耐えるからです。
- **アプリ非依存。** 既存の `el` / `byLabel` / `matchingId` ヘルパと同様、アプリごとの設定は導入せず、出力されるヘルパはアプリをまたいで同一です。

`// TODO` フォールバック自体は残ります。本当に未対応の構文は引き続きここで捕捉します（[BE-0026](../../in-progress/BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) を参照）。本提案はその集合から座標 swipe を取り除くだけです。

## 検討した代替案

- **座標を要素へ逆解決し、方向 swipe を出力する。** これは、開始点の下にどの要素があり、ドラッグがどの方向を近似するかを推測する必要があり、まさに座標 swipe が避けるために存在する曖昧さを再導入します。生成時の画面に答えが依存するため、非決定性も持ち込みます。却下：ジェスチャの意味を変えてしまいます。
- **座標 swipe を `// TODO` のまま残し、欠落をドキュメント化する。** 生成器は単純なままですが、チームが最も残したいであろうジェスチャ（独自キャンバス、地図のパン）について構造マッピングが不完全なままになります。却下：マッピングの実装コストは低く、欠落はユーザに見えるからです。

## 参考

[codegen.md](../../../docs/ja/codegen.md)
