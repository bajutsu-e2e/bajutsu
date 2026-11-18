[English](BE-0377-dynamic-value-generation.md) · **日本語**

# BE-0377 — 乱数・日時生成ステップ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0377](BE-0377-dynamic-value-generation-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0377") |
| 実装 PR | [#1649](https://github.com/bajutsu-e2e/bajutsu/pull/1649) |
| トピック | シナリオ記述機能 |
<!-- /BE-METADATA -->

## はじめに

`generate` ステップは、実行時に乱数または現在日時の値を計算し、ランタイム変数として保存します。
これにより、シナリオは新しい入力値をその場で作り出せます。このステップがなければ、シナリオが参照できるのは、リテラル値、データ駆動の行、UI から取得した値だけでした。

## 動機

著者がリテラルとして書けない入力値を必要とするフローがあります。サインアップフォームは、以前の run がすでに使ったユーザー名を拒否します。予約フォームは、当日の日付を受け取ります。レコードは、他のシナリオの値と衝突しない値を必要とします。

既存のシナリオの仕組みは、こうした値を生み出しません。データ駆動の行（[BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios-ja.md)）は、事前に用意した固定の表を供給するだけです。`extract`（[BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)）は、UI がすでに表示している値を取り込むだけです。どちらも、シナリオが持っていなかった値を作り出しません。

値を生成する手段がなければ、著者は次のいずれかでこの欠落を回避するほかありません。

- いずれ衝突するリテラル
- 執筆時点で固定され、古くなっていく日付
- run の開始前にシナリオの外側で仕込んだ値

その場で値を生成するステップがあれば、欠落を埋められます。生成した値は、`extract`・`http`・`totp` がすでに使っているのと同じ `vars.*` バインディングを通じて、後続ステップへ渡します。新しい補間の仕組みは要りません。

## 詳細設計

`generate` は、2種類の生成カテゴリのいずれかから値を計算し、`vars.<var>` に保存します。保存先は、`http` の `saveBody` と同じ実行時の `vars.*` バインディングです。`into: { var }` という形は `totp` の `into` に沿います。

```yaml
- generate: { random: { string: { length: 8, charset: alnum } }, into: { var: username } }
- type: { text: "${vars.username}", into: { id: signup.username } }

- generate: { random: { uuid: {} }, into: { var: orderRef } }
- generate: { random: { int: { min: 1, max: 100 } }, into: { var: quantity } }

- generate: { datetime: { format: "%Y-%m-%d", offsetDays: 1 }, into: { var: tomorrow } }
- type: { text: "${vars.tomorrow}", into: { id: booking.date } }
```

**`random`** は、次のいずれかを生成します。

- **`string`**：`length` と、任意の `charset`（`alnum` がデフォルトで、ほかに `alpha`、`numeric`、`hex` を選べます）。
- **`int`**：`[min, max]` の範囲の整数。
- **`float`**：`[min, max]` の範囲の数値。任意の `precision`（小数点以下の桁数）を指定できます。
- **`uuid`**：バージョン4の Universally Unique Identifier（UUID）。

**`datetime`** は、現在時刻をテキストとして生成します。任意のフィールドは次のとおりです。

- **`format`**：`strftime` のパターン。デフォルトは、International Organization for Standardization（ISO）が定める ISO 8601 形式です。
- **`offsetSeconds`、`offsetMinutes`、`offsetHours`、`offsetDays`**：符号付きのオフセット。時刻をずらして、明日や1時間後のような相対的な値を作ります。
- **`timezone`**：`America/Los_Angeles` のような Internet Assigned Numbers Authority（IANA）の名前。デフォルトの協定世界時（UTC）ではなく、そのタイムゾーンで値を計算します。

アプリがデバイスのローカルなタイムゾーンで表示する日付にシナリオの入力を一致させたい場合は、そのタイムゾーンを明示的に渡してください。デフォルトの UTC では一致しません。デバイス自身のタイムゾーンを固定する部分は別の関心事であり、[BE-0158](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive-ja.md) が別途扱います。

生成した値は run の証跡とレポートに記録されるので、後で失敗したときにどの値が使われたかがわかります。開発者が run を事後に調査するのに、固定シードは要りません。

`generate` は `extract` 修飾子を持ちません。代わりに自身の `into` フィールドを通じて `vars.*` へ書き込みます。配置は `totp` と同じなので、読み手はどの値生成ステップも同じ形で見つけられます。

prime directive の保持：

- **run パス上に大規模言語モデル（LLM）を置かない。** どちらの生成カテゴリも、決定的なローカル計算です。pseudorandom-number generator（PRNG）からの1回の抽出か、クロックの読み取りのいずれかです。合否判定は引き続き、機械チェック可能なアサーションだけから得られます。
- **フローの決定性であり、値の決定性ではない。** バリデータはシナリオのロード時にフィールドを検査します。受理されたフィールドを持つステップは、常に実行され、常に成功します。run ごとに変わるのは生成される値だけで、これは `totp` の時刻由来のコードがすでにそうであるのと同じです。解決できない `timezone` や不正な `format` は、ロード時に拒否されます。実行時に黙って別の値に置き換わることはありません。特定の値を検証しなければならないシナリオは、その値を `vars.*` に取り込んでから比較するべきです。事前には知り得なかったリテラルと比較するわけにはいきません。
- **アプリ非依存。** ステップとそのフィールドはどのターゲットでも同一で、特定のアプリに固有の部分はありません。
- **codegen。** `generate` はアプリではなく bajutsu ランナーで動きます。XCUITest、Playwright、UI Automator のいずれにも対応するものがないため、codegen はラベル付き `// TODO` を代わりに出力します。これは `http` や `totp` と同じです（[BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md)）。

## 検討した代替案

- **`${...}` トークン内での関数呼び出し構文（たとえば `${random.int(1, 100)}`）。** 却下します。`interp.py` のトークンは、事前計算済みの bindings map への平坦な参照です。トークンは、名前空間によらず同じ方法で置換されます。トークンから引数を解析するようにすると、このプリミティブは小さな式言語になってしまいます。これは、BE-0033 が制御フローについてすでに却下したのと同じ代替案です。ステップという形にすれば、他のあらゆる値生成ステップが使っている、ロード時と実行時の分離を保てます。
- **完全に再現可能な乱数値のための任意の `seed` フィールド。** v1では却下します。生成した値をレポートに記録するだけで、開発者は何が実行されたかを確認できます。特定の予測可能な値を必要とするシナリオは、シードから予測するのではなく、`vars.*` を通じてその値を取り込み、比較するべきです。ビット単位で再現する具体的な事例が出てきたら、再検討する価値があります。
- **シナリオ内のリストからのランダム選択、および run 内で増分するカウンタ。** どちらも妥当な追加の生成カテゴリです。ただし、この項目が埋めようとしている欠落は、事前に知り得ない、衝突しない値です。その欠落を埋めるのに、どちらも要りません。この提案の範囲には含めず、同じ `generate` ステップの下で、将来の生成カテゴリとして残します。

## 進捗

- [x] `generate` ステップのスキーマ（`random`・`datetime` の生成カテゴリ）をシナリオ文法とそのバリデータに追加する。
- [x] ランナーのアクションハンドラを実装し、生成した値を `vars.<var>` へ書き込む。
- [x] 生成した値を証跡・レポート出力に記録する。
- [x] 各バックエンド向けに、ラベル付き codegen の `// TODO` を出力する。
- [x] `scenarios.md` と `dsl-grammar.md` に `generate` を文書化する（英語・日本語の両方）。

## 参考

[scenarios.md](../../docs/ja/scenarios.md)、
[BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps-ja.md)（`http`／`totp` という、同じ実行時計算値のパターン）、
[BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)（`vars.*`、そこですでに却下されている式言語という代替案）
