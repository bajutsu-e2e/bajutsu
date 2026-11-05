[English](BE-0263-serve-author-tiling-layout.md) · **日本語**

# BE-0263 — Author ビューをタイリングレイアウトに組み込む

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0263](BE-0263-serve-author-tiling-layout-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0263") |
| 実装 PR | [#1164](https://github.com/bajutsu-e2e/bajutsu/pull/1164) |
| トピック | オーサリング体験 |
<!-- /BE-METADATA -->

## はじめに

serve の Web UI は、対話的な 3 つのビューにタイリングレイアウトを与えています。3 つのビューは、Replay、Record、Crawl です。`bajutsu/templates/serve.author.js` の `initTiling` が、各ビューを `SPECS` 一覧に登録します。各ビューは、リサイズ可能で、ドラッグによる分割と入れ替えができるペインの木になり、レイアウトは localStorage に永続化されます。

Author ビュー（[BE-0098](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface-ja.md)）は、`SPECS` に**入っていません**。固定の CSS グリッド `#view-author{grid-template-columns:var(--rec-left,340px) var(--gw) 1fr}` のままです。このグリッドは、次の 6 つを、約 340px 幅の 1 本の列に積み上げます。

- コントロール
- ステップ一覧
- YAML エディタ
- 決定性バッジ
- codegen パネル
- enrich パネル

多くの場合は空のスクリーンショットのペインが、残りの幅全体を占めます。

その結果、ビューは壊れて見えます。主作業面である YAML エディタが、横スクロールバー付きの狭い列に押し込まれています。領域の割り当ては、オーサリングの必要と逆になっています。

本項目は、Author を他のエディタと同じタイリングに組み込みます。これにより、ペインがリサイズと再配置でき、エディタが本来の広さを得ます。

## 動機

Author は、serve で最も編集の重いビューです。画面を見て YAML を書くことが、目的そのものです。それでも、他のビューをウィンドウサイズや作業の焦点に応じて使いやすくしているタイリングを、唯一与えられていない対話的ビューです。

現状の固定グリッドは、最初の一手（「左にフォーム、右に画面」）としては妥当でした。しかし、オーサリングで最もよく扱う 2 つ、つまり YAML と画面を、調整できない 340px 対残りの分割に置いています。さらに、ステップ、audit、codegen、enrich のパネルを、同じ狭い列に詰め込んでいます。

目先の「壊れて見える」症状の先に、これは**親和性のギャップ**です。Record と Replay は、まさにこの問題を `initTiling` で解決済みです。Author は、それに加わる代わりに、劣ったレイアウトを再実装しています。Author を `SPECS` に登録すれば、タイラーがすでに備えるドラッグ分割、リサイズ、永続化、reduced-motion の扱いを再利用できます。BE-0072 の小画面向け経路と、BE-0191 のテーマ対応トランジションも、無償で付いてきます。2 つ目の弱いレイアウト経路を、コードベースに育てずに済みます。

プライムディレクティブには触れません。変更は表示だけだからです。タイラーはすでに、決定性に関わる reduced-motion ガード（BE-0191、BE-0072）を守っており、Author はそれを継承します。そのため、実機でのオーサリングは、他のビューと同じく決定的なままです。

## 詳細設計

1. **`view-author` を `SPECS` に登録します。** Author のペインと、妥当な既定レイアウト木を記述した spec を追加します。ペインの候補は次のとおりです。
   - コントロール（target、シナリオ、モード）
   - ステップ一覧
   - YAML エディタ（バッジ、problems、audit、codegen、enrich の機能つき）
   - 画面（スクリーンショットまたはピッカー）

   出入りするペインは、Record の run 結果ペインと同様に `optional` とします。たとえば、セッションも run もないときの画面です。こうしておくと、ペインを隠しても、木は有効なままです。
2. **YAML エディタを第一級のペインにします。** エディタに専用のリサイズ可能なペインを与えます。これにより、エディタは、340px の積み重ねの 3 枚目ではなく、主役の面になれます。
3. **マークアップと CSS を整合させます。** Author セクションの構造と `#view-author` のグリッド規則を調整し、タイラーがレイアウトを所有するようにします。Record と Replay では、ブロックレベルの `<main>` と `.tile-root` によって、すでにそうなっています。この変更で、冗長になる固定列グリッドを外します。非タイリングの狭い階層のスタック（BE-0072）は、動くまま保ちます。
4. **他のビューと同じように永続化し、検証します。** Author の木を、同じ `bajutsu-tiles` キーで、同じ有効性チェックを使って永続化します。チェックの内容は、リーフが一意で既知であり、必須のリーフがすべて存在することです。これにより、古い保存レイアウトは、既定へ穏当に劣化します。
5. **検証します。** レイアウトは単体テストが難しいため、プロジェクトの serve-UI の規範に従い、ブラウザで検証します。
   - エディタペインが主役で、リサイズできます。
   - ペインが、分割と入れ替えをできます。
   - レイアウトが永続化されます。
   - 狭い階層のスタックが、影響を受けません。

   `SPECS` を列挙する既存のタイラーテストがあれば、追加または調整します。

## 検討した代替案

- **固定グリッドのまま、左列を広げる、または分割を反転する。** 手早い見た目のパッチです。Author に、独自の調整できないレイアウトと、親和性のギャップが残ります。そのため、次のウィンドウサイズや作業の焦点で、再び不適切になります。タイラーの再利用が、恒久的な修正です。
- **Author 専用のリサイズ可能レイアウト。** `initTiling` と並んで保守すべき、2 つ目のレイアウトエンジンになります。共有タイラーがすでに提供する以上の利点はありません。
- **ES モジュール化（BE-0247）を待つ。** BE-0247 は、JavaScript の配信方法を作り替えるもので、タイラーの挙動を変えるものではありません。Author は、今 `SPECS` に加われます。後から来るモジュール化にも、そのまま乗れます。

## 進捗

> 作業の進行に合わせて最新に保ってください。チェックリストは *詳細設計* の MECE な作業分解を反映し
> （作業単位ごとに 1 ボックス）、ログは変更内容と時期を（古い順に）記録し PR をリンクします。

- [x] Unit 1 — 既定木つきで `view-author` を `SPECS` に登録。4 ペインは常に存在するため `optional` は
  付けない（Record の Run 結果ペインのような show/hide がない）。
- [x] Unit 2 — YAML エディタを第一級のリサイズ可能ペインに（専用の `yaml` リーフ。既定木で 3/6 と支配的）。
- [x] Unit 3 — マークアップを整合（steps / YAML / screen を `.rec-stack` へ移動）し、冗長な
  `#view-author` 固定グリッドを外す。`#view-author` は他のタイル化ビューと同じくブロックレベルに。
- [x] Unit 4 — `bajutsu-tiles` 下での永続化・有効性の再利用（タイラーからそのまま継承）。
- [x] Unit 5 — ブラウザ検証（狭い階層の pane 切り替えと spec 解決）と
  `tests/serve/test_http_author_ui.py` でのタイラー spec テスト網羅。

**ログ**

- Author を `initTiling` の `SPECS` に登録しました。マークアップと CSS を整合させて、固定グリッドを外しました。YAML エディタ、ステップ、画面が、第一級のリサイズ可能ペインになりました。
  （[#1164](https://github.com/bajutsu-e2e/bajutsu/pull/1164)）

## 参考

- [BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface-ja.md)（Author ビュー）
- [BE-0072 — Responsive serve Web UI](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui-ja.md)（タイラーが委ねる狭い階層のスタック）
- [BE-0191 — Pluggable theme system for the serve Web UI](../BE-0191-pluggable-theme-system-serve-ui/BE-0191-pluggable-theme-system-serve-ui-ja.md)（タイラーのテーマ対応・reduced-motion 対応トランジション）
- [BE-0202 — Split serve.js into section files](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization-ja.md)（`initTiling` の在り処）
- `bajutsu/templates/serve.author.js`（`initTiling` / `SPECS`）、`bajutsu/templates/serve.html.j2`（`#view-author`）、`bajutsu/templates/serve.css`（`#view-author` グリッド）
