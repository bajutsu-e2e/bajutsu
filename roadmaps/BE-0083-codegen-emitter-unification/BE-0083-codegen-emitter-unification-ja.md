[English](BE-0083-codegen-emitter-unification.md) · **日本語**

# BE-0083 — codegen の emitter を共通のシナリオ走査へ統一する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0083](BE-0083-codegen-emitter-unification-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0083") |
| 実装 PR | [#228](https://github.com/bajutsu-e2e/bajutsu/pull/228) |
| トピック | codegen 網羅性 |
<!-- /BE-METADATA -->

## はじめに

Bajutsu は現在、シナリオを 2 つの対象のネイティブテストに変換します。XCUITest 向けの
`bajutsu/codegen.py`（[BE-0003](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci-ja.md)
で実装）と、Playwright 向けの `bajutsu/codegen_playwright.py`（[BE-0062](../BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md)
で実装）です。この 2 つのモジュールは制御フローがまったく同じです。シナリオを走査し、各シナリオで起動環境をマージします。
そのうえで起動行を出力し、各ステップを出力し、最後に `expect` ブロックを出力します。

違うのは 1 行ごとの出力構文だけです。ところがその骨格は、いまは 2 つのファイルにコピーして貼り付けられています。

本項目では、2 つの emitter に共通するシナリオの走査を 1 か所に抽出します。抽出先は、小さな中間表現か、対象ごとの emitter が実装する
`CodeGenerator` プロトコルです。こうすれば走査は 1 か所だけになり、各対象は自分の行構文だけを与えればよくなります。

この変更は、内部の、決定論的で、AI を含まない経路に対する、挙動を変えないリファクタです。生成される出力はバイト単位で同一なので、
ツールの挙動は変わらず、prime directive の範囲に収まります。

## 動機

- **制御フローが重複している。** 2 つのモジュールの `_emit_scenario` と `to_xcuitest` /
  `to_playwright` は、構造としては同じループ（環境マージ → 起動行 → `for step` → `expect`
  ブロック → 閉じ）です。「シナリオをどう走査するか」を変える場合を考えます。変更の例は、新しいトップレベルの構文の出力や、新しいシナリオの節の扱いです。
この場合は 2 か所を直して手作業で同期を保つしかなく、両者がずれていないことをゲートは何も保証しません。
- **3 つ目の emitter を足せば 3 つ目のコピーになる。** Android backend が入ったあとの自然な次の一歩は、Android の codegen 対象の追加です。
  [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)
  のプラットフォームごとの表は、すでにプラットフォームごとに新しい `codegen.py` emitter を挙げています。統一しないままでは、同じ骨格の
  3 つ目のコピーが生まれます。いま走査を統一しておけば、新しい対象を足すコストは、本当に異なる部分である行構文だけになります。
- **行ごとの補助関数はすでにきれいに分かれている。** どちらのモジュールも、対象固有の処理を小さな補助関数（`_emit_step`、`_emit_assertion`、selector や locator の構築）に切り出しています。重複しているのは外側の走査だけなので、リファクタの範囲は狭いです。ループを引き上げ、補助関数はそのまま残します。

## 詳細設計

実現の形は 2 つあり、実装の中で 1 つを選びます。

1. **`CodeGenerator` プロトコル。** 共通の `walk_scenarios(scenarios, env, gen)` がループを持ち、可変部分を与える対象オブジェクトを呼び出します。`file_header()`、`scenario_open(name)`、`launch_lines(env)`、`step_lines(step)`、`assertion_lines(a)`、`scenario_close()`
   です。`codegen.py` と `codegen_playwright.py` はこのプロトコルの実装になり、`to_xcuitest`
   / `to_playwright` は自分の generator を生成して共通の走査を呼ぶだけの薄い公開エントリポイントとして残ります。
2. **小さな中間表現。** 走査がシナリオを中立的な出力命令のリスト（シナリオ開始、起動環境、ステップ、アサーション、閉じ）へ落とし込み、各対象がそのリストをテキストに描画します。この形では、「何を出力するか」と「どう描画するか」をより厳密に切り離せますが、層が 1 つ増えます。

どちらの形でも、対象ごとの行構築は変わりません。`_emit_step`、`_emit_assertion`、locator や selector の補助関数が対象です。
そこはすでに正しい切れ目になっているからです。

受け入れ基準は、生成される XCUITest と Playwright の出力が現状と**バイト単位で同一**であることです。既存の codegen テストで確かめます。`tests/` がすでに両方の
emitter を覆っているからです。プロトコルの形が必要とするなら、共通の走査向けに的を絞ったテストを 1 つ加えます。

範囲は 2 つの codegen モジュールとそのテストに限ります。runner、シナリオモデル、driver には手を入れません。

## 検討した代替案

- **重複をそのままにする。** emitter が 2 つというのは、いまは小さく許容できる量のコピーです。しかし 3 つ目の対象（Android）を足した瞬間に、この重複は効いてきます。
しかも、両者がずれてもゲートは捕まえられません。いま統一しておくのは、それに対する安い保険です。
- **プロトコルや中間表現を使わず関数の共有だけで済ませる。** コールバックの束を共通ループに渡す方法でも動きますが、名前のついたプロトコルより読みにくく、走査と対象のあいだの契約が暗黙になります。プロトコルが、取り除く重複よりも重いと分かったときにだけ選びます。
- **Android の emitter を実際に書くときまで先送りする。** 妥当ではあります。ただし、Simulator を必要としないこの統一をいまデバイスなしで済ませておけば、Android の codegen 作業のリスクを下げられます。
それまでのあいだ、既存の 2 つの emitter がずれるのも防げます。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- `bajutsu/codegen.py`（XCUITest）、`bajutsu/codegen_playwright.py`（Playwright）：本項目が統一する 2 つの emitter です。
- [BE-0003 — M3 codegen / traces / network / CI](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci-ja.md)（XCUITest codegen）、[BE-0062 — Playwright codegen](../BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md)（Playwright codegen）：実装済みの 2 つの対象です。
- [BE-0009 — Cross-platform abstractions](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)：プラットフォームごとの表に、プラットフォームごとの新しい `codegen.py` emitter が挙がっています。この統一が役立つ将来の呼び出し側です。
