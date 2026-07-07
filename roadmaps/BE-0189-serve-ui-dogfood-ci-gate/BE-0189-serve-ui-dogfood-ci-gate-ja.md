[English](BE-0189-serve-ui-dogfood-ci-gate.md) · **日本語**

# BE-0189 — serve Web UI のドッグフードを CI でゲートする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0189](BE-0189-serve-ui-dogfood-ci-gate-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0189") |
| 実装 PR | [#742](https://github.com/bajutsu-e2e/bajutsu/pull/742) |
| トピック | Dogfood フィクスチャ（Web UI） |
| 関連 | [BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui-ja.md), [BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config-ja.md) |
| 由来 | Dogfooding |
<!-- /BE-METADATA -->

## はじめに

[BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui-ja.md) は、Web (Playwright) バックエンドで
Bajutsu 自身の `serve` Web UI を駆動する `demos/serve-ui` ハーネスを出荷しました。ただし、その設計のうち
一点が今後の課題として残されています。**この target を `demos/web` と並べて CI ジョブに配線する**ことです。
配線がなければ、ネットは存在しても誰も走らせないため、配信される single-page application の
リグレッションに気付けません。本項目は、CI ジョブを追加してこの穴を塞ぎ、誰も走らせないあいだに緑から
ずれてしまった 2 本のシナリオを修復することを提案します。

## 動機

**1. ゲートされないネットは何も捕まえません。** BE-0058 の価値は、Web UI に対する決定的なリグレッション
ネットにあります。しかし `demos/serve-ui` はどのワークフローにも入っていないため、その状態にかかわらず
`make check` もすべての PR も緑のままです。今日 `main` でハーネスを走らせると、すでに失敗します。9 本の
シナリオのうち 2 本が赤で、どのゲートもそれを報告しません。どのジョブも走らせないネットは、保護ではなく
ドキュメントです。

**2. このずれこそネットが捕まえるべきもので、ゲートすべき理由を示します。** 2 件の失敗は不安定さではなく、
本物の信号です。

- `record-form` は、Record 画面の **Generate** ボタンが *有効* であることをアサートしています。
  [BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config-ja.md)（AI なしでの graceful
  degradation）以降、Record 面は、到達可能な AI プロバイダがないときは Generate を *無効化* し、
  「Claude が必要」というゲートを表示します。キーなしで走るこのドッグフードでは、この条件が常に成り立ちます。
  シナリオは BE-0101 以前の前提を抱えたまま陳腐化しています。ゲートされたネットであれば、BE-0101 は同じ変更の
  なかでこのシナリオを更新せざるを得なかったはずです。
- `panel-resize` は `swipe { on, direction }` でリサイズ divider を駆動しますが、ジェスチャはパネルを
  まったく動かしません。divider は幅 10px のハンドルであり、方向スワイプは移動区間を要素の *中央をまたぐ*
  ように置くため、`down` がおよそ 50px 離れた位置、つまりハンドルの脇の隣接パネル上に落ち、何もドラッグ
  しません。これはこのシナリオに限らず、swipe プリミティブの実際の限界です。

**3. すべてのプライム原則を守ります**（[CLAUDE.md](../../CLAUDE.md)）。ジョブは純粋な Tier-2 のままです。
キーなし、モデルなし、判定は機械アサーションのみで、Mac も Simulator もない Linux 上で走ります。提案する
ジェスチャの変更も swipe を決定的に保ちます（解決した要素からの固定距離の移動であり、`sleep` もあいまいさも
ありません）。ここには `run`/CI 経路に LLM を置くものは一つもありません。

## 詳細設計

### 1. CI ジョブ `dogfood (serve UI)` を `web-e2e.yml` に追加

[`.github/workflows/web-e2e.yml`](../../.github/workflows/web-e2e.yml) に、既存の
`smoke (playwright)` ジョブを写した 3 本目のジョブを置きます。チェックアウト、uv のインストール、
Chromium のキャッシュ、`uv sync --extra web` と `playwright install`、そして `make -C demos/serve-ui e2e`
です。ドッグフードは `web` extra だけを必要とし dev グループは要らないため、smoke と同じく `UV_NO_DEV: "1"`
を付けます。駆動する内側の `serve` は config の `launchServer` が起動と停止を担うので、ジョブ側で手動起動
するサーバはありません。

ワークフローの positive-list 方式の path フィルタには、ドッグフードが対象とするが smoke は対象としない
パス、すなわち `bajutsu/serve/**`、`bajutsu/templates/**`、`demos/serve-ui/**` を加えます。これにより、
配信される SPA への変更（smoke のフィルタが意図的に除外しているもの）が web ワークフローを起動します。
ほかの web ジョブと同様、これは必須チェックにはしません。フィルタが web 経路に影響し得ない PR では
走らせず、無関係な PR をブロックすることもありません。

### 2. ジェスチャ修正 — 方向スワイプは要素の*上で*始める

[`_scroll_gesture`](../../bajutsu/orchestrator/actions/handlers/gestures.py) は、`{ on, direction }`
スワイプの `(from, to)` を、移動区間を要素の中央に対して対称に置く形で計算します。そのため `down` の点は
要素から移動距離の半分だけ離れた位置に来ます。ジェスチャが単に通過するだけの広い領域をスクロールするときは
これは見えませんが、小さなハンドルを掴めなくします。幅 10px のリサイズ divider では、`down` が隣接パネル上に
落ち、ドラッグが空振りになります。

提案する修正はこうです。ジェスチャは**要素の中央から始まり**、要求された方向へ同じ距離だけ外側に移動します。
移動が画面端をはみ出すときに限って区間を画面内へ滑らせ戻し、移動距離は保ちます。スクロールの変位は変わらない
ため（既存の `amount` スケーリングのテストはそのまま成り立ちます）、スワイプがハンドルの上に落ちて掴める
ようになります。新しい単体テストがこの不変条件を固定します。両方向に余裕のある要素では、`down` はちょうど
その中央になります。

### 3. シナリオの修復

- **`record-form`** → BE-0101 のもとであるべき姿、つまりキーなしでの Record デグレードのネットとして
  組み直します。Claude に到達できないとき、`record.ai-gate` バナーが表示され、`record.generate` と
  `record.save` は無効で、`record.goal` テキストエリアは入力した文字を受け取ります。これにより、陳腐化した
  アサーションが BE-0101 の Record 面に対する生きたガードに変わります。
- **`panel-resize`** → 意図は変えません。ジェスチャ修正（§2）によって divider のドラッグが隣接する 2 パネルを
  再配分し、隣接しない report パネルを 50% のまま残すようになれば、通ります。

### 機械的に検査できる帰結

`make -C demos/serve-ui e2e` は修正ありで緑（9/9）、修正なしで赤になり、新しい CI ジョブがそれをすべての
web 経路の PR で走らせます。`make check` は、新しい `_scroll_gesture` の単体テストを含めて緑のままです。
どのアサーションもモデルを参照しません。

## 検討した代替案

- **`panel-resize` をジェスチャではなく生の `{ from, to }` 座標で直す。** 却下しました。生の座標は
  ビューポートに対して脆く、[`audit`](../../bajutsu/audit.py) はすでに作者を生座標から安定した id 上の
  `{ on, direction }` へ誘導しています。正直な修正は、安定セレクタの形でハンドルを掴めるようにすることで、
  これはこのシナリオだけでなくすべてのバックエンドに効きます。
- **swipe の `amount` を小さくして `down` を divider の近くに落とす。** 却下しました。`amount` は*画面*に
  対する比率なので、必要な値は極端に小さく、ビューポート依存で、丸め誤差に脆く、レイアウト変更で壊れる
  マジックナンバーを埋め込むことになります。
- **`panel-resize` をネットから外す。** 却下しました。これは特定の tiling バグ（リサイズが隣接しない
  パネルを誤って動かす）をガードしており、ほかにテストがありません（JS のテスト基盤がないためです）。外すと
  実際のカバレッジを失います。ジェスチャを直せば、ガードを保ったままプリミティブも良くなります。
- **ドッグフードの CI ジョブに API キーを与えて `record-form` の元のアサーションを成り立たせる。**
  却下しました。ドッグフードの価値は、シークレットもモデルもなく Linux 上でキーなしに走ることにあります。
  キーを要求すると、その設計とも BE-0101 の AI なしの姿勢とも衝突します。*デグレードした*状態をアサートする
  ほうが、より正直で有用な検査です。
- **BE-0058 に取り込む。** BE-0058 の ID は不変で、そのスコープはハーネスそのものです。本項目はその今後の
  課題であった CI スライスと、ゲートされたネットが表面化させる修復であり、独立した項目として追跡し、`関連` で
  相互にリンクします。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] serve-UI ドッグフードを CI でゲートし、serve/templates/serve-ui の path フィルタを追加
- [x] `_scroll_gesture` が方向スワイプを要素の上で開始するよう修正し、単体テストで不変条件を固定
- [x] `record-form` を BE-0101 のキーなし Record デグレードのネットとして組み直し
- [x] `panel-resize` をジェスチャ修正により緑に（意図は不変）

ログ:

- [#742](https://github.com/bajutsu-e2e/bajutsu/pull/742) — 実装しました。CI の手段は §1 の設計から
  発展しています。`web-e2e.yml` に `make -C demos/serve-ui e2e`（bajutsu ランナー）のジョブを足すのではなく、
  専用の [`serve-ui-e2e.yml`](../../.github/workflows/serve-ui-e2e.yml) が各シナリオをネイティブの
  `@playwright/test` スペックとして書き出し（`bajutsu codegen --emit playwright`、BE-0137）、それを
  走らせてドッグフードをゲートします。path フィルタは `bajutsu/serve/**`、`bajutsu/templates/**`、
  `demos/serve-ui/**`。必須チェックではなく、Linux のみです。bajutsu ランナーのネットはローカルの
  `make -C demos/serve-ui e2e` として残ります。本 PR は BE-0058 が部分的に残していたカバレッジ
  （Author / Stats / Coverage のビュー、新しい Replay のツール）も完成させ、文書化された Web UI の機能を
  すべてシナリオに対応づけました。ジェスチャ修正（§2）と 2 本のシナリオ修復（§3）は設計どおりに着地し、
  `record-form` はデグレードした Record 面（Generate は表示されるがフォームはゲートされ、Save と ▶ Run は
  無効、goal テキストエリアは入力を受け取る）をアサートします。`make -C demos/serve-ui e2e` は緑（23/23）で、
  生成したスペックも CI で通ります。

## 参考

- [BE-0058 — serve Web UI のドッグフード](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui-ja.md) — 本項目が完成させるハーネス
- [BE-0101 — AI なしのゼロコンフィグ](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config-ja.md) — `record-form` がガードする graceful degradation
- [BE-0041 — Web (Playwright) バックエンド](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) — テスト対象のバックエンド
- [`.github/workflows/web-e2e.yml`](../../.github/workflows/web-e2e.yml) — ジョブが加わるワークフロー
- [`demos/serve-ui`](../../demos/serve-ui) — ハーネス · [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py) — swipe ジェスチャ
