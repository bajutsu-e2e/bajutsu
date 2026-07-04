[English](BE-0138-serve-lint.md) · **日本語**

# BE-0138 — serve エディタでのシナリオ検証（lint / schema）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0138](BE-0138-serve-lint-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0138") |
| 実装 PR | _pending_ |
| トピック | serve Web UI への CLI 機能の取り込み |
<!-- /BE-METADATA -->

## はじめに

`serve` Web UI の YAML シナリオエディタに、その場の検証を与えます。保存時にだけ失敗するのではなく、*編集
しながら* lint の指摘と JSON Schema のチェックを出します。決定的で AI を使わず、素の textarea を案内付きの
エディタに変えます。しかもいまの textarea のままで出荷でき、より高機能な構造化エディタ
（[BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)）に先んじて、
かつそれを補完します。

## 動機

いまの serve エディタは素の YAML textarea です。検証は保存時にだけ走ります。YAML が不正なら
`load_scenario_file` が例外を投げるので、壊れたシナリオは書き込めませんが、作者は何がどこで間違っているかの
その場のフィードバックを得られず、CLI がすでに走らせている lint レベルのチェックも得られません。Bajutsu には
その両方があります。`bajutsu lint` はシナリオを実行せずに検証し（`bajutsu/lint.py`）、`bajutsu schema` は
エディタ統合のためにシナリオの JSON Schema を出力します（`lint.scenario_json_schema`）。そのどちらもブラウザの
エディタには届いていません。だから UI で手編集する作者は、Save を押して 1 つの例外を受け取るまで何も見えない
飛行をします。CLI なら正確な行と規則を示せたのに、です。編集が起きるその場に lint と schema を出すことは、
最も安価で最も頻度の高いオーサリングの改善です。

## 詳細設計

Tier 1 で決定的です。UI は既存の検証器を起動するだけです。

- **編集しながら（デバウンスして）、また要求に応じて検証する。** lint の指摘は `POST /api/lint`（`{yaml}`）
  から得ます。`bajutsu/lint.py` を実行し、行に紐づいた診断を返します。JSON Schema の検証はシナリオスキーマ
  （`lint.scenario_json_schema`。`bajutsu schema` が出力するのと同じもの）で駆動し、クライアントに渡します。
  診断はその場に出します。溝のマーカーと問題一覧を、行に紐づけて表示します。
- **スキーマ駆動の補助。** 同じ JSON Schema で、シナリオ文法の軽い補完／ホバーを支えられます。UI の外で
  エディタが取り込むのとまさに同じスキーマです。
- **決定的で AI を使わない。** lint と schema は静的検証です。デバイスもモデルも動かしません。保存は既存の
  `load_scenario_file` のガードを保ちます。その場の検証は失敗を早く見せるだけで、保存時のチェックを置き換え
  ません。
- **アプリ非依存。** 検証はシナリオ文法に対するもので、特定のアプリには依存しません。

## 検討した代替案

* **保存時検証だけのままにする（現状）。** 不採用です。場所のない保存時の例外 1 つは、エディタにとって
  考えうる最も貧しいフィードバックで、行に紐づく lint はすでに存在します。
* **構造化 GUI エディタ
  （[BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)）が検証を
  担うのを待つ。** それを前提条件にするのは不採用です。BE-0013（要素ピッカー＋構造化フィールド＋`doctor`
  スコア）はより大きな構築です。その場の lint／schema は分離可能で、いまの textarea で動き、その背後で待つ
  べきではありません。両者は補完関係にあり、こちらは検証の層、BE-0013 は構造化編集の層です。
* **検証をクライアント側 JS で作り直す。** 不採用です。lint 規則とスキーマは Python に一度だけ定義されて
  います。サーバ側で再利用すれば単一の真実の源を保て、ずれを避けられます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `bajutsu/lint.py` から行に紐づいた診断を返す `POST /api/lint`（`{yaml}`）エンドポイントを
      追加する
- [x] シナリオの JSON Schema（`lint.scenario_json_schema`）をクライアントに渡し、それで検証する
- [x] 診断をその場に表示する（溝のマーカーと問題一覧）。編集しながらデバウンスし、要求に応じても出す
- [x] シナリオ文法のスキーマ駆動の補完／ホバーを追加する

- _pending_ — 項目全体を出荷します。`lint_diagnostics`（行に紐づく診断。パースエラーは正確なマークを、
  検証エラーは `loc` を YAML ノード木に対して可能な限り解決します）、stdlib ハンドラと FastAPI 制御プレーンの
  両方に載せた `POST /api/lint` ＋ `GET /api/schema`、そして Author エディタの溝マーカー／問題一覧／補完／
  ホバーです。

## 参考

* `bajutsu/lint.py`（`lint` ＋ `scenario_json_schema`）、`bajutsu/cli/commands/lint.py`、
  `bajutsu/cli/commands/schema.py`（ここで露出する検証器）。
* `bajutsu/serve/`（早期フィードバックを足す、シナリオの読み込み／保存経路、`load_scenario_file`）。
* [BE-0013 — シナリオ GUI エディタ](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)
  （この検証層が補完し、先んじる、より高機能な構造化エディタ）、
  [BE-0011 — ローカル Web UI（`bajutsu serve`）](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)、
  [BE-0072 — serve Web UI のレスポンシブ対応](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui-ja.md)
  （拡張する UI と、引き継ぐ小さい画面向けレイアウト）。
* [scenarios.md](../../../docs/ja/scenarios.md)（lint と schema が検証の基準にするシナリオ文法）。
  [CLAUDE.md](../../../CLAUDE.md)、[DESIGN §2](../../../DESIGN.md)（検証は静的で AI を使わず、合否には
  なりません）。
