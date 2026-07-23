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

serve の Web UI は、対話的な 3 ビュー — Replay・Record・Crawl — にタイリングレイアウトを与えています。
`bajutsu/templates/serve.author.js` の `initTiling` が各ビューを `SPECS` 一覧に登録し、それぞれがリサイズ可能で
ドラッグによる分割・入れ替えができるペインの木になり、レイアウトは localStorage に永続化されます。Author ビュー
（[BE-0098](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface-ja.md)）は `SPECS` に**入っていません**。
固定の CSS グリッド — `#view-author{grid-template-columns:var(--rec-left,340px) var(--gw) 1fr}` — のままで、コントロール・
ステップ一覧・YAML エディタ・決定性バッジ・codegen パネル・enrich パネルを 1 本の約 340px 幅の列に積み上げ、一方で
（多くの場合空の）スクリーンショットのペインが残り幅全体を占めます。

その結果、壊れて見えるビューになっています。主作業面である YAML エディタが横スクロールバー付きの狭い列に押し込まれ、
領域の割り当てがオーサリングの必要と逆転しています。本項目は Author を他のエディタと同じタイリングに組み込み、ペインが
リサイズ・再配置でき、エディタが本来の広さを得るようにします。

## 動機

Author は serve で最も編集の重いビューで — 画面を見て YAML を書くのが目的そのもの — でありながら、他のビューを
ウィンドウサイズや作業の焦点に応じて使いやすくしているタイリングを唯一与えられていません。現状の固定グリッドは最初の
一手（「左にフォーム、右に画面」）としては妥当でしたが、オーサリングで最もよく扱う 2 つ（YAML と画面）を調整不能な
340px 対残りの分割に置き、ステップ・audit・codegen・enrich のパネルを同じ狭い列に詰め込みます。

目先の「壊れて見える」症状の先に、これは**親和性のギャップ**です。Record と Replay はまさにこれを `initTiling` で
解決済みで、Author は仲間に加わる代わりに劣ったレイアウトを再実装しています。Author を `SPECS` に登録すれば、タイラーが
すでに備えるドラッグ分割・リサイズ・永続化・reduced-motion の扱いを再利用できます（BE-0072 の小画面向け経路と BE-0191 の
テーマ対応トランジションも無償で付いてきます）。2 つ目の弱いレイアウト経路を育てずに済みます。

prime directive には触れません。これは表示だけの変更です。タイラーがすでに守る決定性に関わる reduced-motion ガード
（BE-0191/BE-0072）を継承するので、実機でのオーサリングは他のビューと同じく決定的なままです。

## 詳細設計

1. **`view-author` を `SPECS` に登録する。** Author のペインと妥当な既定レイアウト木を記述した spec を追加します。ペイン候補は、
   コントロール（target/シナリオ/モード）・ステップ一覧・YAML エディタ（バッジ/problems/audit/codegen/enrich の機能つき）・
   画面（スクリーンショット/ピッカー）です。出入りするペイン（セッションや run がないときの画面など）は Record の run 結果
   ペインと同様に `optional` とし、隠しても木が有効なままになるようにします。
2. **YAML エディタを第一級のペインにする。** エディタに専用のリサイズ可能なペインを与え、340px の積み重ねの 3 枚目ではなく
   主役の面になれるようにします。
3. **マークアップと CSS を整合させる。** Author セクションの構造と `#view-author` のグリッド規則を調整し、Record/Replay と同様に
   （ブロックレベルの `<main>` ＋ `.tile-root` で）タイラーがレイアウトを所有するようにします。冗長になる固定列グリッドを外し、
   一方で非タイリングの狭い階層のスタック（BE-0072）は動くまま保ちます。
4. **他と同じように永続化・検証する。** Author の木を同じ `bajutsu-tiles` キーで、同じ有効性チェック（一意・既知・必須ペインが
   すべて存在）を使って永続化し、古い保存レイアウトは既定へ穏当に劣化します。
5. **検証。** レイアウトは単体テストが難しいため、プロジェクトの serve-UI の規範に従いブラウザで検証します。エディタペインが主役で
   リサイズでき、ペインが分割・入れ替えでき、レイアウトが永続化され、狭い階層のスタックが影響を受けないことを確認します。
   `SPECS` を列挙する既存のタイラーテストがあれば追加・調整します。

## 検討した代替案

- **固定グリッドのまま左列を広げる/分割を反転する。** 手早い見た目のパッチですが、Author に独自の調整不能なレイアウトと親和性
   ギャップを残します。次のウィンドウサイズや作業焦点で再び不適切になります。タイラーの再利用が恒久的な修正です。
- **Author 専用のリサイズ可能レイアウト。** `initTiling` と並んで保守すべき 2 つ目のレイアウトエンジンで、共有タイラーがすでに
   提供する以上の利点はありません。
- **ES モジュール化（BE-0247）を待つ。** BE-0247 は JS の配信方法を作り替えるもので、タイラーの挙動ではありません。Author は今
   `SPECS` に加われ、後から来るモジュール化に乗れます。

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

- Author を `initTiling` の `SPECS` に登録し、マークアップと CSS を整合させて固定グリッドを外しました。
  YAML エディタ・ステップ・画面が第一級のリサイズ可能ペインになりました。
  （[#1164](https://github.com/bajutsu-e2e/bajutsu/pull/1164)）

## 参考

- [BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface-ja.md)（Author ビュー）
- [BE-0072 — Responsive serve Web UI](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui-ja.md)（タイラーが委ねる狭い階層のスタック）
- [BE-0191 — Pluggable theme system for the serve Web UI](../BE-0191-pluggable-theme-system-serve-ui/BE-0191-pluggable-theme-system-serve-ui-ja.md)（タイラーのテーマ対応・reduced-motion 対応トランジション）
- [BE-0202 — Split serve.js into section files](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization-ja.md)（`initTiling` の在り処）
- `bajutsu/templates/serve.author.js`（`initTiling` / `SPECS`）、`bajutsu/templates/serve.html.j2`（`#view-author`）、`bajutsu/templates/serve.css`（`#view-author` グリッド）
