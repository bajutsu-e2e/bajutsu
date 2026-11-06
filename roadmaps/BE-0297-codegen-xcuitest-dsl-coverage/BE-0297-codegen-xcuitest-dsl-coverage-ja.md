[English](BE-0297-codegen-xcuitest-dsl-coverage.md) · **日本語**

# BE-0297 — XCUITest codegen の実コンパイルカバレッジを DSL 全体へ拡張する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0297](BE-0297-codegen-xcuitest-dsl-coverage-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0297") |
| 実装 PR | [#1278](https://github.com/bajutsu-e2e/bajutsu/pull/1278)、[#1315](https://github.com/bajutsu-e2e/bajutsu/pull/1315)、[#1332](https://github.com/bajutsu-e2e/bajutsu/pull/1332) |
| トピック | codegen 網羅性 |
<!-- /BE-METADATA -->

## はじめに

XCUITest 向け codegen は、3つの codegen エミッタのなかで唯一、実コンパイル検証に成功している例です。
`ios-e2e.yml` の `xcuitest (codegen)` ジョブは、`demos/showcase/scenarios/components.yaml` から
Swift ファイルを生成します。生成したファイルを `xcodegen` でビルドし、実際の `xcodebuild test` で
実行します。

ただし、このシナリオはヘッダーのコメント自身が認めているとおり、意図的に狭い範囲しかカバーして
いません。label / traits / id による `tap`、`wait`、`type`、基本的なアサーションです。
`bajutsu/codegen/xcuitest.py` が実装する内容の大半は、どのコンパイラにも届きません。本項目は
コンパイル対象のフィクスチャを広げます。これにより、このゲートはエミッタのもっとも単純な一角だけ
でなく、実際に実装されている範囲を証明するようになります。

## 動機

`tests/test_codegen.py` は、CI のコンパイル工程よりはるかに広くエミッタを検査しています。
次の4グループの構文は、生成された Swift の部分文字列としてしか検査されておらず、一度もコンパイル
されていません。

- テキスト編集ステップの `clear` / `delete` / `select` / `copy`
  （[BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md)）
- `longPress` / `swipe` / `drag` のジェスチャ
- 座標指定のスワイプ
- `traits` と `index` を組み合わせた複合セレクタ

`within` は幾何学的な包含制約であり、設計上ずっと非対応のままです。`_query()` は実際の Swift ではなく
`UNSUPPORTED_SELECTOR` を返します。追加できるコンパイル対象そのものがないので、本項目の対象外です。

さらに鋭いギャップが2つあります。1つめは `pinch` / `rotate` のマルチタッチです。実際の
`.pinch(withScale:)` / `.rotate(...)` という XCTest 呼び出しを生成しますが（`xcuitest.py:169-180`）、
リポジトリのどこにも、この呼び出しをコンパイルしてデバイス上で実行する工程はありません。実機上の conformance
suite が検証するのはドライバ自身の呼び出しであり、codegen エミッタが生成する版ではありません。

2つめは `forEach` / `if` の制御構文と `extract` です。エミッタの処理が一切なく、汎用の
`// TODO: unsupported step` というコメントへ素通りします（`xcuitest.py:284`）。これらを使う
シナリオは何もしないスタブを生成します。コンパイラもテストも、この欠落を指摘しません。

このリスクは具体的です。これらのどの経路に対するエミッタの変更も、生成された Swift の構文や、実際の
XCTest API の使い方を壊しうるからです。既存のテストはすべて文字列ベースで作られているので、
壊れていてもそのまま通ってしまいます。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **コンパイル対象のシナリオを拡張する**：次のステップを `components.yaml`、または同じ
  `xcuitest (codegen)` ジョブでコンパイルされる姉妹シナリオに追加します。
  - テキスト編集（`clear` / `delete` / `select` / `copy`）
  - ジェスチャ（`longPress`、`swipe` の両形式である方向指定と `from`/`to` の座標指定、`drag`）
  - 複合セレクタ（`traits` + `index`）

  各構文に対応する生成 Swift コードが、実際にビルドされて実行されるようになります。
- **`pinch` / `rotate` をコンパイルして実行する**：マルチタッチのシナリオをコンパイル対象に加えます。
  ドライバレベルの `xcuitest (multi-touch)` ジョブがすでに使っているショーケースのジェスチャ画面を
  再利用します。これにより、ドライバ自身の呼び出しだけでなく、*生成された* `.pinch(withScale:)` /
  `.rotate(...)` 呼び出し自体がコンパイルされ、実行されます。
- **`forEach` / `if` / `extract` の codegen を決める：実装するか、明示的に非対応と宣言するか**。
  現状では、これらは何の警告もなく TODO コメントへ縮退します。解決策の1つは、実際の Swift 制御構文を
  生成してコンパイルし、実行することです。もう1つは、生成時にエミッタが明確な「codegen 非対応」
  エラーを送出し、静かなスタブをやめることです。どちらの解決でもギャップは埋まります。静かな縮退を
  続けることは、解決になりません。
- **段階的に着地させ、新規部分はまずゲート対象外とする**：既存の `components.yaml` の範囲は必須の
  ままにします。追加分（または姉妹シナリオ）は、新たにコンパイルする構文が安定するまで、ゲート対象外の
  シグナルとして拡張します。安定を確認してから、必須ジョブへ組み込みます。

## 検討した代替案

- **現行の狭いシナリオをそのまま残し、残りは文字列ベースのユニットテストに任せる**：これは本項目が
  対処しようとしている現状です。部分一致では、実際の Swift コンパイルエラーや実際の XCTest API の
  誤用を検出できません。それらを捕まえることが、コンパイル済みシナリオの存在する目的です。
- **カバーされていない構文ごとに、完全に独立したコンパイル済みシナリオを追加する**：粒度は細かく
  なります。しかし、従量制の macOS ランナー上で実機 Simulator ジョブを新設するたびに、コストが
  かさみます。追加分を既存の `components.yaml`（または1つの姉妹ファイル）にまとめれば、増加する
  CI コストを相応の範囲に抑えられます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] コンパイル対象のシナリオにテキスト編集、ジェスチャ、複合セレクタのステップを追加する。
- [x] `pinch` / `rotate` のマルチタッチ codegen 出力をコンパイルして実行する。
- [x] `forEach` / `if` / `extract` の codegen を、実装してコンパイルするか生成時に明示的に失敗させるかで解決する。
- [x] 新規部分をまずゲート対象外として着地させ、安定後に必須化する。

**ログ**

- `forEach` / `if` / `extract` の codegen を、上記の第二案（生成時に明示的に失敗させる）で解決しました。
  共有のシナリオ走査（`bajutsu/codegen/common.py`、BE-0083）が、これらの実行時専用の構文に対して生成時に
  `CodegenError` を送出するようになり、3つの emitter（XCUITest / Playwright / UI Automator）が
  何もしない `// TODO` スタブを静かに生成する代わりに、一貫して拒否します。`web` コンテキストステップ
  （3つめの制御構文アクション）は本項目が名指しした対象の外にあり、従来どおり未対応の `// TODO` 経路に
  意図的に残します。
- 残る構文のコンパイル済みカバレッジを、**ゲート対象外**の codegen ステップとして着地させました。新しい
  `ui-test-coverage` ターゲット（`demos/showcase/Makefile`）が、すでに `bajutsu run` で実機を通過している
  シナリオ――`text_editing.yaml`（`select` / `copy` / `clear` / `delete`）、`gestures.yaml`（`longPress`・
  方向指定 `swipe`・`drag`）、`gestures_multitouch.yaml`（`pinch` / `rotate`）――に加え、どの実証済み
  シナリオも触れていない3構文（座標指定の `swipe`、要素アンカーの `drag`、`traits` + `index` の複合
  セレクタ）を集めた小さな `codegen_extra.yaml` から、ネイティブ XCUITest を生成します。これにより、各構文の
  *生成された* Swift が、文字列一致だけでなく実際にコンパイルされ実行されます。`xcuitest (codegen)` ジョブは
  必須の `components.yaml` ステップの後段でこれを `continue-on-error`（`actuation` / `golden` と同じ非ゲート
  思想）で実行し、コミット済みの Components クラスを除外して新規スライスだけを走らせます。生成ファイルは
  gitignore 対象です。`tests/test_codegen.py` が Linux で検証できる半分――各カバレッジ・フィクスチャが
  codegen 可能な状態を保ち、その構文のプリミティブを出力すること――を固定します。最後のボックス、すなわち
  従量制の Simulator レーンでスライスの安定を確認してから必須ゲートへ昇格させることが残りの後続作業のため、
  項目は `In progress` のままとします。
- カバレッジ・スライスを必須ゲートへ昇格させ、最後の作業単位を完了しました。`.github/workflows/ios-e2e.yml`
  の `Codegen coverage` ステップから `continue-on-error`（および警告のみを出すフラグステップ）を外したので、
  拡張 DSL 面のコンパイルや実行が失敗すると、必須の `components.yaml` ステップと並んで `xcuitest (codegen)`
  ジョブと `E2E` ゲートが落ちるようになりました。このスライスは着地以降、実行されたすべての run で
  ゲート対象外のシグナルとして緑を保ち、ゲート入りに足る安定を示しました。これで項目は `実装済み` です。

## 参考

- [BE-0265 — テキスト編集ステップ: select・clear・delete・copy](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md)
- [BE-0083 — codegen の emitter を共通のシナリオ走査へ統一する](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/codegen/xcuitest.py`、`tests/test_codegen.py`、`tests/test_gestures.py`、
  `demos/showcase/scenarios/components.yaml`、`demos/showcase/scenarios/codegen_extra.yaml`、
  `demos/showcase/Makefile`（`ui-test-coverage`）、`.github/workflows/ios-e2e.yml`
  （`xcuitest (codegen)` と `xcuitest (multi-touch)` の各ジョブ）
