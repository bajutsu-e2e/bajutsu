[English](BE-XXXX-codegen-xcuitest-dsl-coverage.md) · **日本語**

# BE-XXXX — XCUITest codegen の実コンパイルカバレッジを DSL 全体へ拡張する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-codegen-xcuitest-dsl-coverage-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | codegen 網羅性 |
<!-- /BE-METADATA -->

## はじめに

XCUITest 向け codegen は、3つの codegen エミッタのなかで唯一、実コンパイル検証に成功している例です。
`ios-e2e.yml` の `xcuitest (codegen)` ジョブは `demos/showcase/scenarios/components.yaml` から Swift
ファイルを生成し、`xcodegen` でビルドし、実際の `xcodebuild test` で実行します。ただしこのシナリオは、
ヘッダーのコメント自身が認めているとおり、意図的に狭い範囲（label / traits / id による `tap`、`wait`、
`type`、基本的なアサーション）しかカバーしておらず、`bajutsu/codegen/xcuitest.py` が実装する内容の
大半はどのコンパイラにも届きません。本項目はコンパイル対象のフィクスチャを広げ、このゲートが
エミッタのもっとも単純な一角だけでなく、実際に実装されている範囲を証明するようにします。

## 動機

`tests/test_codegen.py` は、CI のコンパイル工程よりはるかに広くエミッタを検査しています。
テキスト編集ステップの `clear` / `delete` / `select` / `copy`
（[BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md)）、`longPress` /
`swipe` / `drag` のジェスチャ、座標指定のスワイプ、`traits` と `index` を組み合わせた複合セレクタは、
いずれも生成された Swift の部分文字列としてしか検査されておらず、一度もコンパイルされていません
（`within` は幾何学的な包含制約であり、設計上ずっと非対応のままです。`_query()` は実際の Swift
ではなく `UNSUPPORTED_SELECTOR` を返すため、追加できるコンパイル対象そのものがなく、本項目の
対象外です）。さらに鋭いギャップが2つあります。`pinch` / `rotate` のマルチタッチは実際の
`.pinch(withScale:)` / `.rotate(...)` という XCTest 呼び出しを生成しますが（`xcuitest.py:169-180`）、
これがリポジトリのどこでもデバイス上でコンパイルされて実行されることはありません。実機上の conformance
suite でさえ、ドライバ自身の呼び出しは検証しても、codegen エミッタが生成する版のこれらの呼び出しは
検証していません。そして `forEach` / `if` の制御構文と `extract` にはエミッタの処理が一切なく、汎用
の `// TODO: unsupported step` というコメントへ素通りするだけなので（`xcuitest.py:284`）、これらを
使うシナリオは何もしないスタブを静かに生成し、コンパイラとテストのどちらもこの欠落を一切指摘しません。

このリスクは具体的です。これらのどの経路に対するエミッタの変更も、生成された Swift の構文や実際の
XCTest API の使い方を壊しうるのに、既存のテストはすべて文字列ベースで作られているため、そのまま
通ってしまいます。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **コンパイル対象のシナリオを拡張する**：テキスト編集（`clear` / `delete` / `select` / `copy`）、
  ジェスチャ（`longPress`、`swipe` の両形式（方向指定と `from`/`to` の座標指定）、`drag`）、
  複合セレクタ（`traits` + `index`）の各
  ステップを `components.yaml`、または同じ `xcuitest (codegen)` ジョブでコンパイルされる姉妹
  シナリオに追加し、各構文に対応する生成 Swift コードが実際にビルドされて実行されるようにします。
- **`pinch` / `rotate` をコンパイルして実行する**：ドライバレベルの `xcuitest (multi-touch)` ジョブ
  がすでに使っているショーケースのジェスチャ画面を再利用し、マルチタッチのシナリオをコンパイル対象
  に加えます。これにより、ドライバ自身の呼び出しだけでなく、*生成された* `.pinch(withScale:)` /
  `.rotate(...)` 呼び出し自体がコンパイルされ、実行されます。
- **`forEach` / `if` / `extract` の codegen を決める：実装するか、明示的に非対応と宣言するか**。
  現状ではこれらは静かに TODO コメントへ縮退しています。実際の Swift 制御構文を生成してコンパイルし、
  実行するか、あるいは生成時にエミッタが明確な「codegen 非対応」エラーを送出するようにするか、
  どちらの解決でもこのギャップは埋まります。今のまま静かに縮退させ続けることだけは解決になりません。
- **段階的に着地させ、新規部分はまずゲート対象外とする**：既存の `components.yaml` の範囲は必須の
  ままとし、追加分（または姉妹シナリオ）は新たにコンパイルする構文が安定するまでゲート対象外の
  シグナルとして拡張し、安定を確認してから必須ジョブへ組み込みます。

## 検討した代替案

- **現行の狭いシナリオをそのまま残し、残りは文字列ベースのユニットテストに任せる**：これはまさに
  本項目が対処しようとしている現状であり、部分一致では実際の Swift コンパイルエラーや実際の
  XCTest API の誤用を検出できません。これはコンパイル済みシナリオが存在する目的そのものである
  失敗モードです。
- **カバーされていない構文ごとに、完全に独立したコンパイル済みシナリオを追加する**：粒度は細かく
  なりますが、従量制の macOS ランナー上で実機 Simulator ジョブを新設するたびにコストがかさみます。
  追加分を既存の `components.yaml`（または1つの姉妹ファイル）にまとめることで、増加する CI コストを
  相応の範囲に抑えられます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] コンパイル対象のシナリオにテキスト編集、ジェスチャ、複合セレクタのステップを追加する。
- [ ] `pinch` / `rotate` のマルチタッチ codegen 出力をコンパイルして実行する。
- [ ] `forEach` / `if` / `extract` の codegen を、実装してコンパイルするか生成時に明示的に失敗させるかで解決する。
- [ ] 新規部分をまずゲート対象外として着地させ、安定後に必須化する。

## 参考

- [BE-0265 — テキスト編集ステップ: select・clear・delete・copy](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md)
- [BE-0083 — codegen の emitter を共通のシナリオ走査へ統一する](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/codegen/xcuitest.py`、`tests/test_codegen.py`、`tests/test_gestures.py`、
  `demos/showcase/scenarios/components.yaml`、`.github/workflows/ios-e2e.yml`
  （`xcuitest (codegen)` と `xcuitest (multi-touch)` の各ジョブ）
