[English](BE-0137-serve-codegen.md) · **日本語**

# BE-0137 — serve Web UI からネイティブテストコードを生成する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0137](BE-0137-serve-codegen-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0137") |
| 実装 PR | [#692](https://github.com/bajutsu-e2e/bajutsu/pull/692), [#696](https://github.com/bajutsu-e2e/bajutsu/pull/696) |
| トピック | serve Web UI への CLI 機能の取り込み |
<!-- /BE-METADATA -->

## はじめに

`codegen` を `serve` Web UI に出します。たった今オーサリングした、あるいは実行したシナリオから、等価な
ネイティブテスト（Swift の XCUITest、または Playwright）を生成します。利用者は、そのテストをブラウザで
コピーまたはダウンロードできます。変換は決定的で構造的です。AI も、デバイスも、合否もありません。

## 動機

`codegen` は、Bajutsu のシナリオを、出力先フレームワークの流儀に沿ったネイティブテストに変えます。
コマンドは `bajutsu codegen --emit xcuitest|playwright` です。実装は `bajutsu/codegen.py` にあり、
[BE-0062](../BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md) の Playwright ターゲットは
`bajutsu/codegen_playwright.py` にあります。正典のスイートが XCUITest や Playwright でありながら、
オーサリングは Bajutsu で行いたいチームへの橋渡しです。

ところが現状では、その出力を得る方法は端末しかありません。シナリオをオーサリングした（Record）のも、
合格を確認した（Replay）のも、ブラウザだというのにです。「これを Playwright のテストでくれ」と思った
利用者は、UI を離れ、config、ターゲット、シナリオのパスを組み立て直し、コマンドを実行するしか
ありません。合格したシナリオからワンクリックの距離に codegen を置けば、その差が埋まります。橋渡しの
存在にも気づけるようになります。

## 詳細設計

この機能は Tier 1 で決定的です。UI は既存のコマンドを起動するだけです。

- **「Generate code」操作**を、エディタと Replay ビューに置き、emit のセレクタを添えます。選択肢は
  XCUITest と Playwright で、`codegen` がすでに対応している出力先です。この操作は
  `POST /api/codegen` に `{target, scenario, emit}` を送ります。サーバーは codegen を実行し、生成された
  ソースを返します。
- **結果**は読み取り専用のコードビューアに表示し、クリップボードへのコピーとダウンロードを添えます。
  ファイル名は、シナリオと出力先から導きます。例は `LoginTest.swift` や `login.spec.ts` です。
- **決定的で AI を使わない。** codegen は、シナリオモデルから出力先の構文への構造的な対応づけです。
  ここではデバイスもモデルも動かさず、合否にも触れません。
- **限界に正直。** 提示する emit の選択肢は、選択中の backend で使える codegen のターゲットに従います
  （iOS は XCUITest、web は Playwright）。`--emit` と同じです。未対応構文の限界は、codegen 自身の
  ものです（[BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md)）。
  UI は、codegen の既存の診断を隠さずに出します。
- **アプリ非依存。** ターゲットとシナリオのパスは、config（`targets.<name>`）から解決します。

## 検討した代替案

* **codegen を CLI 専用のままにする。** 不採用です。利用者がネイティブコードを最も欲しがる瞬間は、
  シナリオがブラウザで緑になった直後です。端末へ切り替えさせれば、その瞬間を逃します。
* **合格した run のたびにコードを自動生成する。** ノイズなので不採用です。codegen は時折の
  エクスポートであって、run ごとのアーティファクトではありません。明示的な操作にすれば、run は軽い
  ままです。レポートと `--zip` が、run のアーティファクトのままでよいからです。
* **生成したファイルを UI からリポジトリへ書き込む。** 先送りにします。最初の一歩では、コピーと
  ダウンロード用にコードを返すにとどめます。出力先ツリーへの書き込みはファイル配置の判断に触れる
  ので、明示的に決めるほうがよく、後から足せます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] codegen を実行して生成したソースを返す `POST /api/codegen`（`{target, scenario, emit}`）
      エンドポイントを追加する
- [x] エディタと Replay ビューに「Generate code」操作を、XCUITest／Playwright の emit セレクタ付きで
      追加する
- [x] 結果を読み取り専用のコードビューアに表示し、クリップボードへのコピーとダウンロードを添える
      （ファイル名はシナリオと出力先から導く）
- [x] 選択中の backend で有効な emit ターゲットだけを提示し、codegen の既存の診断を出す

- [#692](https://github.com/bajutsu-e2e/bajutsu/pull/692)：`POST /api/codegen` エンドポイントと、
  エディタ側の「Generate code」操作（emit セレクタ、コピー／ダウンロード付きの読み取り専用コード
  ビューア）です。emit のディスパッチは CLI と共有します（`bajutsu/codegen_emit.py`）。
- [#696](https://github.com/bajutsu-e2e/bajutsu/pull/696)：Replay ビューの「Generate code」操作です。
  共有のクライアント側ファクトリ `makeCodegen` を通じてエンドポイントを再利用し、項目を完了させます
  （状態を実装済みへ）。

## 参考

* `bajutsu/codegen.py`、`bajutsu/codegen_playwright.py`、`bajutsu/cli/commands/codegen.py`（ここで
  露出する生成器）。
* [BE-0062：Playwright codegen ターゲット](../BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md)、
  [BE-0026：未対応構文の縮小](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md)、
  [BE-0025：座標スワイプの生成](../BE-0025-coordinate-swipe-generation/BE-0025-coordinate-swipe-generation-ja.md)
  （ここで露出する codegen のカバレッジと、その既知の限界）。
* [BE-0011：ローカル Web UI（`bajutsu serve`）](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)、
  [BE-0072：serve Web UI のレスポンシブ対応](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui-ja.md)
  （拡張する UI と、引き継ぐ小さい画面向けレイアウト）。
* [codegen.md](../../docs/ja/codegen.md)。[CLAUDE.md](../../CLAUDE.md)、[DESIGN §2](../../DESIGN.md)
  （codegen は構造的で AI を使わないので、この面も LLM を足さず、合否を計算しません）。
