[English](BE-0098-unified-authoring-surface.md) · **日本語**

# BE-0098 — serve の統合オーサリングサーフェス

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0098](BE-0098-unified-authoring-surface-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0098") |
| 実装 PR | [#651](https://github.com/bajutsu-e2e/bajutsu/pull/651) |
| トピック | オーサリング体験 |
| 由来 | [BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`serve` UI 上の 3 つのオーサリングサーフェス（Capture、Editor、Enrichment）を、独立したタブではなく、1 つのシナリオに対して切り替え可能なモードとして統合します。

## 動機

BE-0014 はオーサリングサーフェス間の棲み分けを定義し、enrichment ループを出荷しました。現状では Capture（BE-0012）、Editor（BE-0013）、Enrichment（BE-0014）はそれぞれ独立したタブとして `serve` に存在し、タブごとに target やシナリオの選択を個別に管理しています。タブを切り替えるたびにシナリオを再選択する必要があり、ワークフローに摩擦が生じます。

典型的なフローは「Capture でフローを記録し、Editor でセレクタを修正し、Enrich でアサーションを追加する」というものですが、現状では 3 回のタブ切り替えと 3 回の Load 操作が必要です。統合サーフェスはこのオーバーヘッドを解消し、サーフェス間の合成可能性をファイル形式だけでなく UI 上でも実現します。

BE-0014 の設計書は、著者が 1 つのシナリオを開き、そのままモードを切り替える操作を想定していました。ステップを追加するには demonstrate モードへ、セレクタを修正するには pick モードへ、アサーションを提案するには propose モードへ切り替えるという形です。

## 詳細設計

### 1 つのシナリオに対する 3 つのモード

独立した Capture タブ、Editor タブ、Enrichment ボタンを、1 つの「Author」タブに統合します。このタブは 1 つのシナリオ（target + ファイル + 名前）を開き、モードスイッチャーを通じて 3 つのモードを提供します。

- **Capture** モード：現在の Capture タブの機能です。ライブセッションを開始し、スクリーンショット上のクリックでステップを記録します。ステップは開いているシナリオにストリーミングされます。
- **Edit** モード：現在の Editor タブの機能です。ステップ間のナビゲーション、スクリーンショット上のクリックによるセレクタ解決、YAML の直接編集を行います。
- **Enrich** モード：現在の Enrich ボタンの機能を独立したモードに昇格させたものです。アサーションを提案し、レビューし、受け入れるか却下します。

モードスイッチャーはトップレベルのナビゲーションとは視覚的に区別されたボタン行です。モードを切り替えてもシナリオの再読み込みは発生せず、未保存の編集も失われません。

### 共有される状態

3 つのモードは以下を共有します。

- 選択された target、シナリオファイル、シナリオ名
- YAML テキストエリア（シナリオテキストの単一の信頼できる情報源）
- Save ボタン（`ScenarioScope.save()` を通じた書き込み）
- ステップリスト（YAML からレンダリングされ、Capture と Enrich によって更新される）

モード固有の状態（Capture のライブドライバセッション、Editor の現在のステップインデックス、Enrichment の提案）は個別に保持されますが、同じシナリオにスコープされます。

### 移行パス

既存の Capture タブと Editor タブは移行期間中も機能し続けます。統合された Author タブはそれらと並行して追加されます。検証が完了したら個別のタブを削除し、トップレベルのナビゲーションボタンを 1 つの Author ボタンに置き換えます。

## 検討した代替案

* **タブを維持し、タブ間の状態同期を追加する案。** 棄却しました。3 つの独立したタブ間でシナリオ選択と未保存の編集を同期させると、根本的な UX の問題（著者が最初にタブを選ぶ必要がある）を解消せずに複雑さだけが増します。
* **モードスイッチャーなしで 3 つすべてを Editor タブにマージする案。** 棄却しました。1 つのタブにすべてのコントロールを詰め込むとインターフェースが煩雑になります。モードスイッチャーによって各モードのコントロールを集中させることができます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] **テンプレート** — Capture タブと Editor タブのナビゲーションボタン、および両方の
  `<main>` ビューを、Capture / Edit / Enrich のモードスイッチャーを持つ 1 つの `#view-author`
  タブに置き換えました。target・シナリオ・run・YAML・ステップ・Save を共有します
  （`bajutsu/templates/serve.html.j2`）。
- [x] **スタイル** — モードスイッチャーの `.modeswitch` / `.modetab` を追加し、従来の
  `.cap-*` / `.edt-*` クラスを共通の `.au-*` クラスに統合しました。`[hidden]` がモードグループの
  表示ルールに勝つようにしています（`bajutsu/templates/serve.css`）。
- [x] **挙動** — 2 つのフロントエンドモジュールを 1 つの Author モジュールに統合しました。
  状態を共有し、`setMode()` はモードを切り替えても開いているシナリオと未保存の YAML を保持します。
  スクリーンショットのクリックはモードに応じて振り分け、Capture の Finish 後は保存したシナリオを
  Edit モードへ引き継ぎます（`bajutsu/templates/serve.js`）。
- [x] **移行** — Capture タブと Editor タブを直接削除しました（並存する不要コードは残しません）。
  再利用するバックエンド（`/api/capture/*`・`/api/scenario`・`/api/scenario/resolve`・
  `/api/enrich`）は変更していません。
- [x] **テスト** — `test_http_editor_ui.py` を `test_http_author_ui.py` に置き換え、統合後の
  マークアップ、旧タブの削除、モードスイッチャー、共有コントロールとモード別コントロール、
  各エンドポイントの結線、そして要となる `[hidden]` の表示ルールを検証します。あわせて
  `test_http_static.py` の `viewswitch` の数を更新しました。

ログ：

- 3 つのオーサリングサーフェスを 1 つの Author タブとモードスイッチャーに統合しました。
  Capture / Edit / Enrich のハンドラを既存エンドポイントを再利用したまま 1 つのモジュールへ移し、
  統合したフローで Enrich モードのクリック・Capture から Edit への引き継ぎ失敗・パス未設定の
  Save を握りつぶさず通知するように強化しました。`make check` は green です。

## 参考

[BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md)（この統合を想定した棲み分け設計）、[BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md)（Capture）、[BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)（Editor）。
