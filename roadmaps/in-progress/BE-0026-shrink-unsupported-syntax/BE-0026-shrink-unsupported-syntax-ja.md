[English](BE-0026-shrink-unsupported-syntax.md) · **日本語**

# BE-0026 — 未対応構文の縮小

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0026](BE-0026-shrink-unsupported-syntax-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| 実装 PR | [#210](https://github.com/bajutsu-e2e/bajutsu/pull/210), [#280](https://github.com/bajutsu-e2e/bajutsu/pull/280), [#316](https://github.com/bajutsu-e2e/bajutsu/pull/316) |
| トピック | codegen 網羅性 |
<!-- /BE-METADATA -->

## はじめに

未知セレクタ等が `// TODO` に落ちる範囲を減らします。

## 動機

`codegen` の契約は、翻訳できない構文は失敗させず `// TODO` コメントを出力する、というものです。
これにより出力は常にレビュー可能で、ビルドを壊しません。安全網としては正しいのですが、`// TODO`
の 1 行ごとに人手の移植が発生し、それがいくつも並ぶフローは事実上まったく生成されていないのと
同じです。シナリオ文法のうち構造的にマッピングできる範囲が広いほど、成功したシナリオはチームの
自前 Xcode CI へそのまま引き継がれます。それこそが codegen の目的です。現状の残りの欠落には、
未知のセレクタ形式（`_element` のフォールバックに到達したセレクタは `el("UNSUPPORTED_SELECTOR")`
を出力します）や、まっすぐ `// TODO: unsupported step` に落ちるいくつかのステップ種別
（`setLocation`、`push`、既存の settle コメントを超える `gone` 以外の `until` 待機、`request`
アサーション）が含まれます。フォールバック集合に残っている一つひとつが、codegen が完全に再現
できないフローです。

## 詳細設計

これは複数の改善を束ねる傘の提案です。フォールバック集合を一度に 1 構文ずつ縮小し、各マッピングは
純粋に構造的（AI なし、run／CI ゲートに変更なし）なまま進めます。価値の高いと見込まれる順に候補を
挙げます。

- **セレクタ。** `_element` はすでに `id` / `label` / `idMatches` / `labelMatches` を扱います。
  セレクタが持ちうる複合形式（`traits`、`value`、コンテナのサブツリーへスコープする `within`、
  k 番目の一致を選ぶ `index`）を、`el("UNSUPPORTED_SELECTOR")` に落とすのではなく `NSPredicate`
  と `XCUIElementQuery` の合成で追加します。`within` は `descendants(matching:)` に対する入れ子の
  クエリへ、`index` は `element(boundBy:)` へマッピングします。
- **デバイス制御ステップ。** `setLocation` と `push`（および
  [BE-0035](../../implemented/BE-0035-device-control-primitives/BE-0035-device-control-primitives-ja.md) の
  プリミティブ）には、アプリレベルで対応する XCUITest API がありません。これらは `simctl` を
  通じてシミュレータを操作します。よって誠実なマッピングは、素っ気ない「unsupported step」では
  なく、レビュアが実行すべき `simctl` コマンドを明記した、ラベル付きの `// TODO` です。黙って
  落とすのではなく、対象外であることを明示します。
- **待機とアサーション。** `request` アサーションと `until: { request: ... }` 待機は、構造的な
  等価物が存在する場合に限りマッピングします。ネットワーク観測には概して XCUITest 側の対応物が
  ないため、これらも明示的な `// TODO` のままにします。

支配的なルールは不変であり、要です。**構文がフォールバック集合から抜けるのは、忠実で決定的かつ
AI 非依存な構造マッピングが存在するときに限ります。** 生成時に意図の推測を要するものは `// TODO` の
ままにします。誤った翻訳よりも、レビュー可能で誠実な欠落の方が良いからです。これにより codegen の
「純粋に構造的で AI 非依存」という保証を保ったまま、チームに必要な手作業移植を着実に減らします。

### 実装状況

**複合セレクタ**のスライスを提供しました（`bajutsu/codegen.py`）。単一の `id` / `label` / `idMatches` は
読みやすいヘルパをそのまま使い、`value`・`traits`・`index`（単独でも組み合わせでも）は
`el("UNSUPPORTED_SELECTOR")` に落とさず 1 つの `NSPredicate` クエリに合成します。trait は小さな語彙の上で忠実に
写します（`button` / `link` → `elementType`、`notEnabled` → `enabled == NO`、`selected` → `selected == YES`）。
メタ文字を含まない `labelMatches` は部分文字列（`label CONTAINS`）になります。`index` は `element(boundBy:)`
に写します。非負の `index` はそのままリテラルになり、**負の `index`**（`drivers/base.py` の `candidates[i]`
と同じく末尾から数える）は `element(boundBy: query.count - k)` に忠実に写ります。`boundBy:` は負のリテラルを
取らないため、クエリの実行時の `count` から差し引きます。
**デバイス制御**ステップ（`setLocation` / `push`）は、素の「unsupported step」ではなく `simctl` コマンド名を
明記したラベル付き `// TODO` を出力するようにしました。

**ネットワークのアサーション / 待機**もラベル付きにしました。`request` / `requestSequence` /
`responseSchema` アサーションと `until: { request }` 待機は、素の「unsupported assertion」や（待機では）誤解を
招く汎用の「settle wait」コメントではなく、一致させるエンドポイント（`METHOD path`）と理由
—*XCUITest にはネットワーク傍受の口がないので mock/proxy で検証する*—を明記した `// TODO` を出力します。
忠実な XCUITest 形式は無いので TODO のままですが、デバイス制御と同じくレビュー可能で正直なものになりました。

*忠実な*構造写像が無いものは正直に `el("UNSUPPORTED_SELECTOR")` のままにしました（統制ルール:
決定的で AI 非依存の写像が存在するときだけフォールバック集合から外す）: 正規表現の `labelMatches`（`re.search`
であり、NSPredicate の全体一致 `MATCHES` とは異なる）、`within`（ツリークエリでなく幾何的なフレーム包含）、
未知の trait。

## 検討した代替案

- **未対応構文で `// TODO` を出す代わりに生成を失敗させる。** 完全性を強制できますが、出力は常に
  コンパイルでき常にレビュー可能、という codegen の約束を壊します。1 つの未マッピングステップが、
  長いフローの残り全体の出力をブロックしてしまいます。却下：わずかな手編集を、ハードな停止と
  引き換えにしてしまいます。
- **すべての欠落をベストエフォートの推測で埋める。** たとえば未知のセレクタを「最初に一致した
  もの」に翻訳したり、`simctl` ステップをアプリ内ジェスチャで近似したりすることです。これは
  決定性と「曖昧なセレクタは、最初に一致したものを叩くのではなく失敗させる」という directive に
  反し、誤った理由で成功するテストを生みます。即座に却下。
- **何もせず `// TODO` の網に頼る。** ほとんど使われない構文には妥当ですが、よく使われるもの
  （とくに複合セレクタ）は十分な頻度で現れるため、未マッピングのままでは codegen を実質的に
  弱めます。価値の高いケースについては却下し、本当にマッピング不能なものについては維持します。

## 参考

[codegen.md](../../../docs/ja/codegen.md)
