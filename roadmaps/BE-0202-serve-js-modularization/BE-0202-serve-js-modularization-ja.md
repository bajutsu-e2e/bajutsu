[English](BE-0202-serve-js-modularization.md) · **日本語**

# BE-0202 — serve.js をビルドなしのままセクション別ファイルに分割する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0202](BE-0202-serve-js-modularization-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0202") |
| 実装 PR | [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811) |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/templates/serve.js` は、ビルド工程を持たないブラウザ JavaScript の 1 ファイルとして
約 2,500 行、トップレベル宣言約 150 個にまで育ち、セクションコメント（login、config ブラウザ、
settings、record、replay、triage、stats、coverage、約 400 行のグラフを含む crawl、codegen、
タイル配置）で区切られています。本項目では、サーバが複数アセットの配信にすでに使っている
テンプレート機構を通じて、これをいくつかのセクション別ファイルに分割し、コピーされた 3 つの
ジョブ開始ハンドラを統一します。bundler を使わない方針は変えません。

## 動機

このファイルは一貫性を保っているものの、テストのない 1 ファイルが支えられる規模を超えています。
ESLint のガードレール（BE-0129）は約 1,500 行の時点の規模に合わせて調整されたもので、その後
約 65% 成長しました。状態は機能領域ごとのモジュールレベル `let` グローバルで、DOM が真実の
所在です。セクション単位では成り立っていますが、2,500 行を横断して読むのは困難です。

分割に新しいツールは要りません。`serve/handler.py` は `serve.css`、`serve.themes.css`、
`serve.js` をすでに別々の Jinja レンダリング済みアセットとして配信しているので、1 ファイルの
代わりに 2〜4 ファイルの JS を配信するのは、ビルド機構ゼロの純粋なファイル整理です。

ファイル内の具体的な重複も 1 件、本項目の範囲に含めます。run（`#go`）、record（`#rec-go`）、
crawl（`#crawl-go`）のジョブ開始ハンドラは同一の骨格を共有しています（古いストリームを閉じ、
`setBusy(true)`、ペインをクリア、`/api/{run,record,crawl}` に POST、`{jobId,error}` を分解、
エラーなら `setStatus` と `setBusy(false)`、成功なら id を保存して `streamJob(…)`）。
`startJob(…)` ヘルパで骨格を統一し、パネルごとのペインのクリアは各呼び出し側に残します。

## 詳細設計

1. `serve.js` をセクション別ファイル（2〜4 個。例: 共通ヘルパと共有状態、パネルのハンドラ、
   crawl のグラフ）に分割し、それぞれを `serve/handler.py` の既存テンプレート機構で独立した
   アセットとして配信します。bundler なし、import なしで、グローバルスコープの意味論は現状の
   まま、読み込み順はテンプレートが固定します。
2. `startJob(…)` ヘルパを追加し、run、record、crawl の開始ハンドラを移行します。
3. `eslint.config.mjs` の `files` リストを新しいファイル群に広げます（ルールは同一）。
4. 分割をまたぐ挙動の固定は serve UI の dogfood ゲート（BE-0189）に任せ、隙間が見つかった
   場合に限り dogfood シナリオを拡張します。

## 検討した代替案

- **bundler やフレームワークの導入。** 設計として不採用です（`eslint.config.mjs` の理由書きを
  参照）。ここは Python のリポジトリで、この UI の価値はツールチェーン不要の単純さにあります。
  配信機構は bundler なしで複数ファイルにすでに対応しています。
- **1 ファイルのまま維持。** 成長は着実で（serve の機能追加のたびに UI コードがここに増えます）、
  現在の軌道では読解の負荷と単一の共有グローバルスコープの問題が悪化し続けます。
- **JS のユニットテストハーネスを今導入する。** ESLint の設定は、分岐ロジックが必要とするまで
  ハーネスを先送りすると明記しています。crawl のグラフはその線を越えつつありますが、ハーネスは
  別個の大きな判断です。分割を純粋な再編成にとどめるため、本項目からは意図的に除外します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] serve.js を既存テンプレート機構で配信するセクション別ファイルに分割
- [x] `startJob(…)` ヘルパを追加し、run / record / crawl のハンドラを移行
- [x] `eslint.config.mjs` が新しいファイル群を対象に含む
- [x] 分割をまたいで dogfood ゲートが緑（隙間が見つかった場合のみシナリオを拡張）

ログ:

- [#811](https://github.com/bajutsu-e2e/bajutsu/pull/811) — `serve.js`（2544 行）を 4 つのセクション別ファイルに分割しました。`serve.core.js`
  （共通ヘルパと共有状態、config、Settings）、`serve.panels.js`（Record、Replay、Triage、
  アップロード）、`serve.crawl.js`（Crawl とグラフ、ライトボックス）、`serve.author.js`
  （レイアウトの配線、Author タブ、起動処理）です。`handler.py` がこれらを固定順で連結し、
  1 つのインライン `<script>` に埋め込みます。2 つの CSS アセットをすでに連結しているのと同じ
  方式なので、配信される出力は変わりません。共通の `startJob(…)` 骨格を追加し、run、record、
  crawl の開始ハンドラを移行しました。`eslint.config.mjs` と `make lint-js` の対象を `serve.*.js`
  群に広げました。レビューでの追随対応として、共通化した `startJob(…)` が 3 つの開始ボタンすべての
  入口になったため、ネットワーク切断・非 JSON 応答・`jobId` 欠如のときにボタンを回転させたまま
  固まらせず、明示的に失敗を示してボタンを元に戻すよう堅牢化しました。

## 参考

- [`bajutsu/templates/serve.core.js`](../../bajutsu/templates/serve.core.js) · [`bajutsu/serve/handler.py`](../../bajutsu/serve/handler.py) · [`eslint.config.mjs`](../../eslint.config.mjs)
- [BE-0129](../BE-0129-serve-scope-boundary/BE-0129-serve-scope-boundary-ja.md) — このファイルが調整時の規模を超えたガードレール
- [BE-0189](../BE-0189-serve-ui-dogfood-ci-gate/BE-0189-serve-ui-dogfood-ci-gate-ja.md) — 分割をまたいで UI の挙動を固定する dogfood ゲート
