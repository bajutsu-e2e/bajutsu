[English](BE-0189-serve-ui-dogfood-ci-gate.md) · **日本語**

# BE-0189 — serve Web UI のドッグフードを CI でゲートする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0189](BE-0189-serve-ui-dogfood-ci-gate-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0189") |
| 実装 PR | [#733](https://github.com/bajutsu-e2e/bajutsu/pull/733) |
| トピック | Dogfood フィクスチャ（Web UI） |
| 関連 | [BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui-ja.md), [BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config-ja.md) |
| 由来 | Dogfooding |
<!-- /BE-METADATA -->

## はじめに

[BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui-ja.md) は、Web (Playwright) バックエンドで
Bajutsu 自身の `serve` Web UI を駆動する `demos/serve-ui` ハーネスを出荷しました。ただし、設計のうち
1 点が今後の課題として残っています。**この target を `demos/web` と並べて CI ジョブに配線する**ことです。
配線がなければ、ネットがあっても誰も走らせないため、配信される single-page application の
リグレッションに気付けません。本項目は、CI ジョブを追加してこの穴を塞ぎます。あわせて、誰も走らせない
あいだに緑から外れてしまった 2 本のシナリオを修復します。

## 動機

**1. ゲートされないネットは何も捕まえません。** BE-0058 の価値は、Web UI に対する決定的なリグレッション
ネットにあります。しかし `demos/serve-ui` はどのワークフローにも入っていないため、ハーネスの状態にかかわらず
`make check` もすべての PR も緑のままです。今日 `main` でハーネスを走らせると、すでに失敗します。9 本の
シナリオのうち 2 本が赤ですが、どのゲートも報告しません。どのジョブも走らせないネットは、保護ではなく
ドキュメントです。

**2. このずれはネットが捕まえるべきものであり、ネットをゲートすべき理由でもあります。** 2 件の失敗は不安定さではなく、
本物の信号です。

- `record-form` は、Record 画面の **Generate** ボタンが *有効* であることをアサートしています。
  [BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config-ja.md)（AI なしでの graceful
  degradation）以降、Record 面は、到達可能な AI プロバイダがないときに Generate を *無効化* し、
  「Claude が必要」というゲートを表示します。キーなしで走るこのドッグフードでは、この条件が常に成り立ちます。
  シナリオは BE-0101 以前の前提を抱えたまま陳腐化しています。ネットがゲートされていれば、BE-0101 は同じ変更の
  なかでこのシナリオを更新せざるを得なかったはずです。
- `panel-resize` は `swipe { on, direction }` でリサイズ divider を駆動しますが、ジェスチャはパネルを
  まったく動かしません。divider は幅 10px のハンドルです。方向スワイプは移動区間を要素の *中央をまたぐ*
  ように置くため、`down` がおよそ 50px 離れた位置、つまりハンドルの脇の隣接パネル上に落ち、何もドラッグ
  しません。この限界は、このシナリオに限らず swipe プリミティブそのものにあります。

**3. この変更は、すべてのプライム原則を守ります**（[CLAUDE.md](../../CLAUDE.md)）。ジョブは純粋な Tier-2 のままです。
キーなし、モデルなしで、判定は機械アサーションだけで決まり、Mac も Simulator もない Linux 上で走ります。提案する
ジェスチャの変更も swipe を決定的に保ちます（解決した要素からの固定距離の移動であり、`sleep` もあいまいさも
ありません）。`run`/CI 経路に LLM を置くものは、ここにはありません。

## 詳細設計

### 1. CI ジョブ `dogfood (serve UI)` を `web-e2e.yml` に追加

[`.github/workflows/web-e2e.yml`](../../.github/workflows/web-e2e.yml) に、既存の
`smoke (playwright)` ジョブを写した 3 本目のジョブを置きます。手順は次の順です。

1. チェックアウトする。
2. uv をインストールする。
3. Chromium をキャッシュする。
4. `uv sync --extra web` と `playwright install` を実行する。
5. `make -C demos/serve-ui e2e` を実行する。

ドッグフードに必要なのは `web` extra だけで、dev グループは要らないため、smoke と同じく `UV_NO_DEV: "1"`
を付けます。駆動する内側の `serve` は config の `launchServer` が起動と停止を担うので、ジョブ側で手動起動
するサーバはありません。

ワークフローの positive-list 方式の path フィルタには、ドッグフードが対象とし、smoke は対象としない
パスを加えます。`bajutsu/serve/**`、`bajutsu/templates/**`、`demos/serve-ui/**` です。これにより、
配信される SPA への変更（smoke のフィルタが意図的に除外しているもの）が web ワークフローを起動します。
ほかの web ジョブと同様、このジョブは必須チェックにしません。web 経路に影響し得ない PR ではフィルタが
ジョブを走らせないため、無関係な PR をブロックすることはありません。

### 2. 方向スワイプを要素の*上で*始めるジェスチャ修正

[`_scroll_gesture`](../../bajutsu/orchestrator/actions/handlers/gestures.py) は、`{ on, direction }`
スワイプの `(from, to)` を、移動区間を要素の中央に対して対称に置く形で計算します。そのため `down` の点は
要素から移動距離の半分だけ離れた位置に来ます。ジェスチャが広い領域を通過するだけのスクロールでは、
このずれは目に見えません。しかし小さなハンドルは掴めなくなります。幅 10px のリサイズ divider では、`down` が
隣接パネル上に落ち、ドラッグが空振りになります。

提案する修正では、ジェスチャを**要素の中央から始め**、要求された方向へ同じ距離だけ外側に移動します。
移動が画面端をはみ出すときに限り、区間を画面内へ滑らせ戻します。この場合も移動距離は保たれます。
スクロールの変位は変わらないため、既存の `amount` スケーリングのテストはそのまま成り立ちます。一方、
スワイプはハンドルの上に落ちて掴めるようになります。新しい単体テストが不変条件を固定します。両方向に
余裕のある要素では、`down` はちょうどその中央になります。

### 3. シナリオの修復

- **`record-form`** は、BE-0101 のもとであるべき姿、つまりキーなしでの Record デグレードのネットとして
  組み直します。Claude に到達できないとき、`record.ai-gate` バナーが表示され、`record.generate` と
  `record.save` は無効で、`record.goal` テキストエリアは入力した文字を受け取ります。陳腐化した
  アサーションが、BE-0101 の Record 面に対する生きたガードに変わります。
- **`panel-resize`** は、意図を変えません。ジェスチャ修正（§2）によって divider のドラッグが隣接する 2 パネルを
  再配分し、隣接しない report パネルが 50% のまま残れば、通ります。

### 機械的に検査できる帰結

`make -C demos/serve-ui e2e` は、修正ありで緑（9/9）、修正なしで赤になります。新しい CI ジョブは、
web 経路のすべての PR でこれを走らせます。`make check` は、新しい `_scroll_gesture` の単体テストを含めて
緑のままです。どのアサーションもモデルを参照しません。

## 検討した代替案

- **`panel-resize` をジェスチャではなく生の `{ from, to }` 座標で直す。** 却下しました。生の座標は
  ビューポートに対して脆く、[`audit`](../../bajutsu/audit.py) はすでに作者を生座標から安定した id 上の
  `{ on, direction }` へ誘導しています。適切な修正は、安定セレクタの形でハンドルを掴めるようにすることです。
  この修正は、このシナリオだけでなくすべてのバックエンドに効きます。
- **swipe の `amount` を小さくして `down` を divider の近くに落とす。** 却下しました。`amount` は*画面*に
  対する比率なので、必要な値は極端に小さく、ビューポートに依存し、丸め誤差に脆くなります。レイアウトを
  変えると壊れるマジックナンバーを埋め込むことになります。
- **`panel-resize` をネットから外す。** 却下しました。このシナリオは、特定の tiling バグ（リサイズが隣接しない
  パネルを誤って動かす）をガードしており、ほかにテストがありません（JS のテスト基盤がないためです）。外すと
  実際のカバレッジを失います。ジェスチャを直せば、ガードを保ったままプリミティブも良くなります。
- **ドッグフードの CI ジョブに API キーを与えて `record-form` の元のアサーションを成り立たせる。**
  却下しました。ドッグフードの価値は、シークレットもモデルもなく Linux 上でキーなしに走ることにあります。
  キーを要求すると、この設計とも BE-0101 の AI なしの姿勢とも衝突します。*デグレードした*状態をアサートする
  ほうが、より正確で有用な検査です。
- **BE-0058 に取り込む。** BE-0058 の ID は不変で、そのスコープはハーネスそのものです。本項目は、BE-0058 で今後の
  課題だった CI の部分と、ゲートされたネットが表面化させる修復を扱います。そのため独立した項目として追跡し、`関連` で
  相互にリンクします。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] CI ジョブ `dogfood (serve UI)` を `web-e2e.yml` に追加し、serve/templates/serve-ui の path フィルタを追加
- [x] `_scroll_gesture` が方向スワイプを要素の上で開始するよう修正し、単体テストで不変条件を固定
- [x] `record-form` を BE-0101 のキーなし Record デグレードのネットとして組み直し
- [x] `panel-resize` をジェスチャ修正により緑に（意図は不変）

ログ：

- [#733](https://github.com/bajutsu-e2e/bajutsu/pull/733)：実装しました。`dogfood (serve UI)` ジョブ
  （と serve/templates/serve-ui の path フィルタ）を `web-e2e.yml` に追加し、`_scroll_gesture` が
  方向スワイプを要素の上で開始するよう直して `down` が中央に落ちることを単体テストで固定し、`record-form`
  を BE-0101 のキーなし Record デグレードのネットとして組み直し、`panel-resize` をジェスチャ修正で緑に
  しました。`make -C demos/serve-ui e2e` は 9/9 になりました。

## 参考

- [BE-0058：serve Web UI のドッグフード](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui-ja.md)：本項目が完成させるハーネス
- [BE-0101：AI なしのゼロコンフィグ](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config-ja.md)：`record-form` がガードする graceful degradation
- [BE-0041：Web (Playwright) バックエンド](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)：テスト対象のバックエンド
- [`.github/workflows/web-e2e.yml`](../../.github/workflows/web-e2e.yml)：ジョブが加わるワークフロー
- [`demos/serve-ui`](../../demos/serve-ui)：ハーネス · [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py)：swipe ジェスチャ
