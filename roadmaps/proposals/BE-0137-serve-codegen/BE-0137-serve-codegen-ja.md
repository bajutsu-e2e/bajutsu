[English](BE-0137-serve-codegen.md) · **日本語**

# BE-0137 — serve Web UI からネイティブテストコードを生成する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0137](BE-0137-serve-codegen-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラック | [提案](../../README-ja.md#提案) |
| トピック | serve Web UI への CLI 機能の取り込み |
<!-- /BE-METADATA -->

## はじめに

`codegen` を `serve` Web UI に出します。たった今オーサリングした、あるいは実行したシナリオから、等価な
ネイティブテスト（Swift の XCUITest、または Playwright）を生成し、ブラウザでコピーまたはダウンロード
できるようにします。変換は決定的で構造的です。AI も、デバイスも、合否もありません。

## 動機

`codegen` は Bajutsu のシナリオを、出力先フレームワークの流儀に沿ったネイティブテストに変えます
（`bajutsu codegen --emit xcuitest|playwright`。`bajutsu/codegen.py` と
[BE-0062](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md) の Playwright
ターゲット `bajutsu/codegen_playwright.py`）。正典のスイートが XCUITest や Playwright でありながら、
オーサリングは Bajutsu で行いたいチームへの橋渡しです。ところがその出力を得る唯一の方法は端末で、
シナリオをオーサリングした（Record）のも、合格を確認した（Replay）のもブラウザだというのに、です。「これを
Playwright のテストでくれ」と思った利用者は、UI を離れ、config／ターゲット／シナリオのパスを組み立て直し、
コマンドを実行するしかありません。合格したシナリオからワンクリックの距離に codegen を置けば、その差が埋まり、
橋渡しの存在に気づけるようになります。

## 詳細設計

Tier 1 で決定的です。UI は既存のコマンドを起動するだけです。

- **「Generate code」操作**を、エディタと Replay ビューに置き、emit のセレクタ（XCUITest／Playwright。
  `codegen` がすでに対応している出力先）を添えます。`POST /api/codegen`（`{target, path, emit}`）を叩き、
  codegen を実行して生成されたソースを返します。
- **結果**は読み取り専用のコードビューアに表示し、クリップボードへのコピーとダウンロードを添えます
  （ファイル名はシナリオと出力先から導きます。例: `LoginTest.swift` / `login.spec.ts`）。
- **決定的で AI を使わない。** codegen はシナリオモデルから出力先の構文への構造的な対応づけです。ここでは
  デバイスもモデルも動かさず、合否にも触れません。
- **限界に正直。** 提示する emit の選択肢は、選択中の backend で使える codegen のターゲットに従います
  （iOS は XCUITest、web は Playwright）。`--emit` と同じです。未対応構文の限界は codegen 自身のもので
  （[BE-0026](../../implemented/BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md)）、
  UI は codegen の既存の診断を隠さずに出します。
- **アプリ非依存。** ターゲットとシナリオのパスは config（`targets.<name>`）から解決します。

## 検討した代替案

* **codegen を CLI 専用のままにする。** 不採用です。利用者がネイティブコードを最も欲しがる瞬間は、シナリオが
  ブラウザで緑になった直後で、端末へ切り替えさせればその瞬間を逃します。
* **合格した run のたびにコードを自動生成する。** ノイズなので不採用です。codegen は時折のエクスポートで
  あって、run ごとのアーティファクトではありません。明示的な操作にすれば run は軽いままです（レポートと
  `--zip` が run のアーティファクトのままでよいのです）。
* **生成したファイルを UI からリポジトリへ書き込む。** 先送りにします。最初の一歩はコピー／ダウンロード用に
  コードを返すにとどめます。出力先ツリーへの書き込みはファイル配置の判断に触れるので、明示的に決めるほうが
  よく、後から足せます。

## 参考

* `bajutsu/codegen.py`、`bajutsu/codegen_playwright.py`、`bajutsu/cli/commands/codegen.py`（ここで
  露出する生成器）。
* [BE-0062 — Playwright codegen ターゲット](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md)、
  [BE-0026 — 未対応構文の縮小](../../implemented/BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md)、
  [BE-0025 — 座標スワイプの生成](../../implemented/BE-0025-coordinate-swipe-generation/BE-0025-coordinate-swipe-generation-ja.md)
  （ここで露出する codegen のカバレッジと、その既知の限界）。
* [BE-0011 — ローカル Web UI（`bajutsu serve`）](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)、
  [BE-0072 — serve Web UI のレスポンシブ対応](../../implemented/BE-0072-responsive-web-ui/BE-0072-responsive-web-ui-ja.md)
  （拡張する UI と、引き継ぐ小さい画面向けレイアウト）。
* [codegen.md](../../../docs/ja/codegen.md)。[CLAUDE.md](../../../CLAUDE.md)、[DESIGN §2](../../../DESIGN.md)
  （codegen は構造的で AI を使わないので、この面も LLM を足さず、合否を計算しません）。
