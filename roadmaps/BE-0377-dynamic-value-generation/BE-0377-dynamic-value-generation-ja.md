[English](BE-0377-dynamic-value-generation.md) · **日本語**

# BE-0377 — 乱数・日時生成ステップ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0377](BE-0377-dynamic-value-generation-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0377") |
| トピック | シナリオ記述機能 |
<!-- /BE-METADATA -->

## はじめに

`generate` ステップは、実行時に乱数または現在日時の値を計算し、ランタイム変数として保存します。
これにより、シナリオはリテラル値やデータ駆動の行、UI から取得した値だけでなく、その場で作り出し
た入力値も参照できるようになります。

## 動機

著者がリテラルとして書けない入力値を必要とするフローがあります。サインアップフォームは、以前の
run がすでに使ったユーザー名を拒否します。予約フォームは、当日の日付を受け取ります。レコードは、他
のシナリオの値と衝突しない値を必要とします。

既存のシナリオの仕組みは、こうした値を生み出しません。データ駆動の行（[BE-0031](../BE-0031-data-driven-scenarios/BE-0031-data-driven-scenarios-ja.md)）は、事前に用意した固定の表を供給するだけです。

`extract`（[BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)）も、UI がすでに表示している値を取り込むだけです。どちらの仕組みも、シナリオ自身が値を作り出すわけではありません。値を生成する手段がなければ、著者はいずれ衝突するリテラル、執筆時点で固定して古くなっていく日付、run の開始前にシナリオの外側で仕込んだ値のいずれかで、この欠落を回避するほかありません。

その場で値を生成し、`extract`・`http`・`totp` がすでに使っているのと同じ `vars.*` バインディングを
通じて後続ステップへ渡すステップがあれば、新しい補間の仕組みを追加せずにこの欠落を埋められます。

## 詳細設計

`generate` は、2種類の生成カテゴリのいずれかから値を計算し、`vars.<var>` に保存します。実行時に
`vars.*` へ保存する点は `http` の `saveBody` と同じで、`into: { var }` という形は `totp` の `into`
に沿います。

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
- **`uuid`**：バージョン4の UUID。

**`datetime`** は、現在時刻をテキストとして生成します。任意の `format` フィールドは `strftime` の
パターンを取り、デフォルトでは ISO 8601 形式になります。任意の符号付きオフセット（`offsetSeconds`、
`offsetMinutes`、`offsetHours`、`offsetDays`）を指定すると、明日や1時間後のような相対的な値に
ずらせます。任意の `timezone` フィールド（`America/Los_Angeles` のような IANA 名）を指定すると、
デフォルトの UTC ではなくそのタイムゾーンで値を計算します。アプリがデバイスのローカルなタイムゾーンで
表示する日付にシナリオの入力を一致させたい場合は、デフォルトの UTC に頼るのではなく、そのタイ
ムゾーンを明示的に渡してください。デバイス自身のタイムゾーンをそ
れに合わせて固定する部分は別の関心事であり、
[BE-0158](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive-ja.md) が別途
扱います。

生成した値は run の証跡とレポートに記録されるので、後で失敗したときにどの値が使われたかがわか
ります。開発者が run を事後に調査するのに、固定シードは要りません。

`generate` は `extract` 修飾子を持ちません。代わりに自身の `into` フィールドを通じて `vars.*` へ
書き込みます。これは `totp` と同じ配置で、読み手はどの値生成ステップも同じ形で見つけら
れます。

prime directive の保持：

- **run パス上に LLM を置かない。** どちらの生成カテゴリも、決定的なローカル計算です（
  乱数生成器（PRNG）からの1回の抽出、またはクロックの読み取り）。合否判定は引き続き機械チェック
  可能なアサーションのみから得られます。
- **フローの決定性であり、値の決定性ではない。** バリデータがロード時に受理したフィールドでは、
  ステップは常に実行され、常に成功します。変わるのは生成される値だけで、これは `totp` の時
  刻由来のコードがすでにそうであるのと同じです。解決できない `timezone` や不正な `format` は、シ
  ナリオのロード時に拒否され、実行時に黙って別の値に置き換わることはありません。特定の値を検証し
  なければならないシナリオは、その値を `vars.*` に取り込んでから比較するべきであり、事前には知り
  得なかったリテラルと比較するわけにはいきません。
- **アプリ非依存。** ステップとそのフィールドはどのターゲットでも同一で、特定のアプリに固有の部
  分はありません。
- **codegen。** `generate` はアプリではなく bajutsu ランナーで動きます。XCUITest、Playwright、
  UI Automator のいずれにも等価物がないため、codegen はラベル付き `// TODO` を代わりに出力します。
  これは `http` や `totp` と同じです
  （[BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md)）。

## 検討した代替案

- **`${...}` トークン内での関数呼び出し構文（たとえば `${random.int(1, 100)}`）。** 却下しま
  す。`interp.py` のトークンは、事前計算済みの bindings map への平坦な参照であり、名前空間によ
  らず同じ方法で置換されます。トークンから引数を解析するようにすると、このプリミティブは小さな
  式言語になってしまいます。これは、BE-0033 が制御フローについてすでに却下したのと同じ代替案で
  す。ステップという形にすれば、他のあらゆる値生成プリミティブがすでに使っているのと同じ、ロー
  ド時・実行時の分離を保てます。
- **完全に再現可能な乱数値のための任意の `seed` フィールド。** v1では却下します。生成した値をレ
  ポートに記録するだけで、開発者は何が実行されたかを確認できます。特定の予測可能な値を必要とす
  るシナリオは、シードから予測するのではなく、`vars.*` を通じてその値を取り込み比較するべきで
  す。ビット単位で再現する具体的な事例が出てきたら、再検討する価値があります。
- **シナリオ内のリストからのランダム選択、および run 内で増分するカウンタ。** どちらも妥当な追
  加の生成カテゴリですが、この項目が埋めようとしている欠落（事前に知り得ない、衝突しない値）を
  埋めるには不要です。この提案の範囲を広げるのではなく、同じ `generate` ステップの下で、将来の
  生成カテゴリとして残します。

## 進捗

- [ ] `generate` ステップのスキーマ（`random`・`datetime` の生成カテゴリ）をシナリオ文法とその
  バリデータに追加する。
- [ ] ランナーのアクションハンドラを実装し、生成した値を `vars.<var>` へ書き込む。
- [ ] 生成した値を証跡・レポート出力に記録する。
- [ ] 各バックエンド向けに、ラベル付き codegen の `// TODO` を出力する。
- [ ] `scenarios.md` と `dsl-grammar.md` に `generate` を文書化する（英語・日本語の両方）。

## 参考

[scenarios.md](../../docs/ja/scenarios.md)、
[BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps-ja.md)（`http`／`totp` という、同じ実行
時計算値のパターン）、
[BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)
（`vars.*`、そこですでに却下されている式言語という代替案）
