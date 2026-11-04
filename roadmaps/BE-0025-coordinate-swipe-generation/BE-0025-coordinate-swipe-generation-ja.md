[English](BE-0025-coordinate-swipe-generation.md) · **日本語**

# BE-0025 — 座標 swipe の生成

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0025](BE-0025-coordinate-swipe-generation-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0025") |
| 実装 PR | [#217](https://github.com/bajutsu-e2e/bajutsu/pull/217) |
| トピック | codegen 網羅性 |
<!-- /BE-METADATA -->

## はじめに

現状では、`swipe { from, to }` は生成テストで `// TODO` コメントにフォールバックします。

## 動機

`codegen` は、成功したシナリオをネイティブの XCUITest にマッピングします。このマッピングは純粋に構造的です。そのため、シナリオが表現できるすべてのステップには、生成側にも対応物があるべきです。そうでなければ、出力されるテストは黙ったまま不完全になります。

現状、セレクタ形式の `swipe`（`{ on, direction }`）は `swipeUp/Down/Left/Right()` にマッピングされます。座標形式（`{ from, to }`）は、`// TODO` コメントしか出力しません。そのため、座標で swipe するシナリオは `run` では実行できます。ところが、チームが生成した XCUITest を自前の Xcode CI に持ち込むと、そのジェスチャが失われます。

差は小さく見えますが、実害があります。座標 swipe は、セレクタ形式では届かないケースのために存在します。地図のパン、独自描画のキャンバス、アドレスできる要素のないドラッグです。チームが最も残したいと望むのは、まさにこれらのケースです。

## 詳細設計

XCUITest は、任意のドラッグを `XCUICoordinate.press(forDuration:thenDragTo:)` で表現します。生成されるヘルパは、安定したアンカーを基準に 2 つの座標を組み立て、その間をドラッグします。

```swift
private func coord(_ x: CGFloat, _ y: CGFloat) -> XCUICoordinate {
  let origin = app.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
  return origin.withOffset(CGVector(dx: x, dy: y))
}
```

`swipe: { from: [x1, y1], to: [x2, y2] }` は、`coord(x1, y1).press(forDuration: 0.1, thenDragTo: coord(x2, y2))` を出力します。これは idb バックエンドと同じ挙動です。idb 側もすでに短い duration を加えています。この duration により、SwiftUI は瞬間的なフリックではなくパンとしてドラッグを認識します。

この設計は、プライムディレクティブの範囲に収まります。

- **決定性。** シナリオ内の座標は固定された数値なので、生成されるドラッグは再現できます。マッピングは純粋に構造的なままです。生成時に AI を参照せず、run／CI ゲートにも影響しません。
- **可搬性。** 座標は `withNormalizedOffset` で、アプリウィンドウの左上を基準にします。Bajutsu がすでに用いている原点の取り方と同じです。そのため、生成されたテストは `run` と同じ意味で数値を読みます。座標形式は、引き続きドキュメント上の最終手段です（[scenarios](../../docs/ja/scenarios.md#swipe)）。セレクタ形式が優先されるのは、レイアウトの変更に耐えるからです。
- **アプリ非依存。** アプリごとの設定は導入しません。出力されるヘルパは、既存の `el`、`byLabel`、`matchingId` のヘルパと同様に、アプリをまたいで同一です。

`// TODO` フォールバック自体は残ります。本当に未対応の構文は、引き続きここで捕捉します（[BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) を参照）。本提案が取り除くのは、その集合のうち座標 swipe だけです。

## 検討した代替案

- **座標を要素に逆解決し、方向 swipe を出力する案。** 開始点の下にどの要素があり、ドラッグがどの方向を近似するかを推測する必要があります。この推測は、座標 swipe が避けるために存在する曖昧さそのものです。生成時の画面に答えが依存するため、非決定性も持ち込みます。却下しました。ジェスチャの意味を変えてしまうからです。
- **座標 swipe を `// TODO` のまま残し、欠落をドキュメント化する案。** 生成器は単純なままです。しかし、チームが最も残したいジェスチャ（独自のキャンバス、地図のパン）について、構造マッピングが不完全なままになります。却下しました。マッピングのコストは低く、欠落はユーザに見えるからです。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[codegen.md](../../docs/ja/codegen.md)
