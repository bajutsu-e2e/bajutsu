[English](BE-0026-shrink-unsupported-syntax.md) · **日本語**

# BE-0026 — 未対応構文の縮小

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0026](BE-0026-shrink-unsupported-syntax-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0026") |
| 実装 PR | [#210](https://github.com/bajutsu-e2e/bajutsu/pull/210), [#280](https://github.com/bajutsu-e2e/bajutsu/pull/280), [#316](https://github.com/bajutsu-e2e/bajutsu/pull/316) |
| トピック | codegen 網羅性 |
<!-- /BE-METADATA -->

## はじめに

未知のセレクタなどが `// TODO` に落ちる範囲を減らします。

## 動機

`codegen` は、翻訳できない構文を失敗させず、`// TODO` コメントとして出力する契約です。
出力は常にレビューでき、ビルドを壊しません。安全網としては正しい設計です。
ただし、`// TODO` が 1 行あるたびに人手の移植が発生します。それがいくつも並ぶフローは、事実上まったく生成されていないのと同じです。

シナリオ文法のうち構造的にマッピングできる範囲が広いほど、成功したシナリオはチームの自前 Xcode CI へそのまま引き継がれます。それこそが codegen の目的です。現状の残りの欠落は、次の 2 種類です。

- **未知のセレクタ形式。** `_element` のフォールバックに到達した[セレクタ](../../docs/ja/glossary.md#scenario-authoring)は、`el("UNSUPPORTED_SELECTOR")` を出力します。
- **まっすぐ `// TODO: unsupported step` に落ちるステップ種別。** `setLocation`、`push`、既存の settle コメントを超える `gone` 以外の `until` 待機、`request` アサーションが該当します。

フォールバック集合に残る構文の 1 つひとつが、codegen で完全には再現できないフローです。

## 詳細設計

これは複数の改善を束ねる傘の提案です。フォールバック集合を一度に 1 構文ずつ縮小します。各マッピングは純粋に構造的なものにとどめ、AI は使わず、run／CI ゲートも変更しません。価値が高いと見込まれる順に、候補を挙げます。

- **セレクタ。** `_element` はすでに `id` / `label` / `idMatches` / `labelMatches` を扱います。ここに、セレクタが持ちうる複合形式を追加します。対象は `traits`、`value`、コンテナのサブツリーへスコープする `within`、k 番目の一致を選ぶ `index` です。`el("UNSUPPORTED_SELECTOR")` に落とさず、`NSPredicate` と `XCUIElementQuery` を合成して表現します。`within` は `descendants(matching:)` に対する入れ子のクエリへ、`index` は `element(boundBy:)` へマッピングします。
- **デバイス制御ステップ。** `setLocation` と `push` には、アプリレベルで対応する XCUITest API がありません。
  [BE-0035](../BE-0035-device-control-primitives/BE-0035-device-control-primitives-ja.md) のプリミティブも同様です。これらは `simctl` を通じてシミュレータを操作します。したがって誠実なマッピングは、素っ気ない「unsupported step」ではなく、レビュアが実行すべき `simctl` コマンドを明記したラベル付きの `// TODO` です。黙って落とすのではなく、対象外であることを明示します。
- **待機とアサーション。** `request` アサーションと `until: { request: ... }` 待機は、構造的な等価物が存在する場合に限りマッピングします。ネットワーク観測には概して XCUITest 側の対応物がありません。そのため、これらも明示的な `// TODO` のままにします。

支配的なルールは不変であり、要です。**構文がフォールバック集合から抜けるのは、忠実で決定的かつ AI 非依存な構造マッピングが存在するときに限ります。** 生成時に意図の推測を要するものは、`// TODO` のままにします。誤った翻訳よりも、レビューできる誠実な欠落のほうがよいからです。このルールにより、codegen の「純粋に構造的で AI 非依存」という保証を保ったまま、チームに必要な手作業の移植を着実に減らします。

## 検討した代替案

- **未対応構文で `// TODO` を出す代わりに生成を失敗させる。** 完全性は強制できます。しかし、出力は常にコンパイルでき、常にレビューできるという codegen の約束を壊します。1 つの未マッピングステップが、長いフローの残りの出力まで止めてしまいます。却下：わずかな手編集を、完全な停止と引き換えにしてしまうためです。
- **すべての欠落をベストエフォートの推測で埋める。** たとえば、未知のセレクタを「最初に一致したもの」に翻訳します。あるいは、`simctl` ステップをアプリ内ジェスチャで近似します。どちらも決定性に反し、「曖昧なセレクタは、最初に一致したものを叩くのではなく失敗させる」という directive にも反します。誤った理由で成功するテストを生むため、即座に却下します。
- **何もせず `// TODO` の網に頼る。** ほとんど使われない構文には妥当です。ただし、よく使われる構文（とくに複合セレクタ）は十分な頻度で現れます。未マッピングのままでは、codegen を実質的に弱めます。価値の高いケースについては却下し、本当にマッピングできないものについては維持します。

## 進捗

- [x] 複合セレクターのマッピング。`value` / `traits` / `index`（負のインデックスを含み、`element(boundBy: count - k)` で表現します）を `el("UNSUPPORTED_SELECTOR")` に落とさず、一つの `NSPredicate` クエリに合成します。
- [x] デバイス制御ステップ（`setLocation` / `push`）は、`simctl` コマンド名を記したラベル付き `// TODO` を出力します。
- [x] ネットワークアサーションと `until: { request }` の待機は、エンドポイントと「インターセプト不可」の理由を記したラベル付き `// TODO` を出力します。
- [x] 忠実な構造表現が存在しない箇所（`labelMatches` の正規表現、`within`、未知のトレイト）だけは、正直に `el("UNSUPPORTED_SELECTOR")` を残します。

## 参考

[codegen.md](../../docs/ja/codegen.md)
