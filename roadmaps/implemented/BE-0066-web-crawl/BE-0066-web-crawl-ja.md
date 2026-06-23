[English](BE-0066-web-crawl.md) · **日本語**

# BE-0066 — Web crawl（Playwright backend）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0066](BE-0066-web-crawl-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| 実装 PR | [#185](https://github.com/bajutsu-e2e/bajutsu/pull/185) |
| トピック | プラットフォーム拡張（着手済みスライス） |
<!-- /BE-METADATA -->

## はじめに

自律クロール（[BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）を
Web（Playwright）backend（[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）へ広げ、
`bajutsu crawl --backend web` で web アプリを幅優先に探索できるようにします。iOS のクロールと同じ成果物、すなわち
画面マップ、クラッシュ再現シナリオ、候補シナリオ、全画面カバレッジ用のダンプを、オプションの AI ガイド
（`--guide ai`）まで含めて等価に得ます。クロールのエンジンはすでにプラットフォーム非依存です。本項目が足すのは
プラットフォーム固有のライフサイクル配線と、web に適したクラッシュ判定およびダイアログ処理で、いずれも AI を
判定に入れない原則（[prime directive #1](../../../CLAUDE.md)）を保ったまま行います。

## 動機

**エンジンはすでにプラットフォーム非依存で、iOS に縛られているのはコマンドだけです。**
クロールのエンジン（[`crawl.py`](../../../bajutsu/crawl.py)）は `Driver` 抽象（`query` / `tap` / `type_text` /
`tap_point` / `screenshot`）の上だけで書かれており、モジュールの docstring も「AI も Simulator 配線も持たない」と
明言しています。したがって web クロールは、エンジンの作り直しではなく、配線と判定の意味付けを補う作業です。
いまだ iOS に固定された唯一の Tier-1 経路は、クロールの**コマンド**
（[`cli/commands/crawl.py`](../../../bajutsu/cli/commands/crawl.py)）です。その `reset` クロージャは `bundle_id` を
鍵にした `simctl` での再起動（`_env.Env.terminate` / `launch`）を行い、`_await_ready` で待機し、クラッシュ判定は
iOS のアクセシビリティツリー崩壊（`shows_app_ui`）で、進捗表示は「preparing the simulator」と出します。

**web のライフサイクル seam はすでにあり、`run` が使っています。**
`run` は web ドライバを `driver.navigate()` で起動し（[`runner/launch.py`](../../../bajutsu/runner/launch.py)）、
シナリオ間の再起動は `_web_relauncher`（中身はもう一度の `navigate()`）で行います
（[`runner/pool.py`](../../../bajutsu/runner/pool.py)）。新しい `BrowserContext` が `erase` に相当し、そのコストは
ほぼゼロです。クロールは自前の iOS 専用 reset を抱える代わりに、この seam を再利用すべきです。

**web はクロールの効果を最も低コストで確かめられる場所です。**
web は Mac もエミュレータも要らず、既存の Linux の `make check` / CI ゲートで動きます
（[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）。そのため web クロールを CI の
中で発見の一手として走らせられます。BE-0038 が埋める三つの欠落は、web アプリにもそのまま当てはまります。
オーサリング前の発見（画面マップが「このアプリを知らない」を「これが画面一覧、ここから書くものを選ぶ」に
変える）、全画面のカバレッジ計測（[DESIGN §7.2](../../../DESIGN.md)。クロールは `doctor --from <runId>` が消費する
画面ごとのダンプを生む実行です）、そして頑健性のスモークテスト（オーサリング不要の広い探索が、ハッピーパスでは
踏まない異常を表に出す）です。

**決定性の境界は変わりません。**
クロールは本質的に非決定的なので、Tier-1 の発見ツールにとどまり、CI ゲートにはなりません。決定的な副産物、すなわち
クラッシュの再現パスと発見した各フローは素の YAML として出力し、`run` が AI なしの Tier-2 リグレッションとして
再生します。クラッシュは、ぶれるクロールとしてではなく、コミットされた再現シナリオとして CI に届きます。これは
BE-0038 がすでに述べているハブモデルそのもので、web になっても何も変わりません。

## 詳細設計

### コマンドのインターフェース

```
bajutsu crawl --app <web-app> --backend web
    [--max-screens N] [--max-steps N] [--prune-global]
    [--seed <url-or-path> ...]       # base URL 以外の追加の入口
    [--guide ai|off] [--out runs/<runId>]
```

web アプリは `bundleId` ではなく `baseUrl` で識別します。per-app config はこれをすでにモデル化しており
（[`config.py`](../../../bajutsu/config.py) では、アプリは `bundleId`（iOS）か `baseUrl`（web）のいずれかを要する）、
他のフラグは iOS のクロールからそのまま引き継ぎます。`--seed` は deeplink ではなく URL かパスになります。iOS と
同じく AI ライブ経路で、決定的ゲートには含まれません。`--guide off` は識別子由来のヒューリスティックだけで動き、
モデルを必要としません。`--guide ai` はオプションのガイドを上に重ねます。どちらでも AI は合否を決めません。

### ライフサイクル seam（`run` のものを再利用し、プラットフォームで分岐）

クロールコマンドの `reset` を iOS 分岐から括り出し、`run` と同じやり方でプラットフォームに応じて切り替えます。

| 段階 | iOS（現状） | Web（本項目） |
|---|---|---|
| 起動 | `launch_driver` → simctl の boot とアプリ起動 | `launch_driver` → `driver.navigate()`（すでに分岐済み） |
| reset / 再起動 | `_env.Env.terminate` ＋ `launch`（simctl） | `driver.navigate()`。新しい `BrowserContext` ＝ erase でほぼ無料（`_web_relauncher` に倣う） |
| 準備待ち / settle | `_await_ready` のポーリング | Playwright のネイティブ auto-wait（`conditionWait`）。同期ドライバなのでエンジンの `settle` は省く |

`launch_driver` は web に対してすでに正しく振る舞うので、クロール側の変更は `reset` クロージャだけです。この再起動を
`run` とクロールで共有する小さなファクトリに括り出せば、「クリーンな初期状態へ戻す」という定義がプラットフォームごとに
一つになり、両経路がずれなくなります。

### web でのクラッシュ判定（唯一の新しい設計面で、決定的なまま）

iOS のクラッシュ信号、すなわちアプリプロセスの死と、素のウィンドウへ崩壊したアクセシビリティツリー
（`shows_app_ui`）は、web に存在しません。web には固有の**決定的**な信号があり、いずれも LLM の判断ではありません。

- **未捕捉の JS 例外**：直前の操作以降に `page.on("pageerror")` が発火したか（イベントという事実）。
- **エラーへの遷移**：メインフレームの応答が HTTP 4xx / 5xx を返したか（数値）。
- **空または崩壊した文書**：DOM が実質的に空になったか（集合の空であり、iOS の崩壊ツリーの web 版）。

これには [`PlaywrightDriver`](../../../bajutsu/drivers/playwright.py) への小さな追加が要ります。`pageerror`
（とコンソールエラー）のハンドラを登録し、直近のメインフレーム応答ステータスを保持して、クロールが読む健全性
アクセサとして公開します。これは `is_app_alive` の web 版です。エンジンにある唯一の `is_app_alive(landed)` 呼び出しは、
プラットフォームで分岐する健全性チェックになります（iOS は `shows_app_ui`、web はドライバの信号）。クラッシュを
検出したら、既存の `result:error` 安全網で証跡一式を取得し（[DESIGN §9](../../../DESIGN.md)）、再生したパス（入口 URL と
記録した操作）を最小の再現シナリオとして出力します。これは web の `run` でそのまま実行できます。pageerror は
イベント、HTTP ステータスは数値、空 DOM は集合の空なので、[prime directive #1](../../../CLAUDE.md) は保たれ、AI は
判定の外にとどまります。

### 妨害オーバーレイのガードから web のダイアログハンドラへ

iOS ではアラートガード（`_clear_blocking`、Claude vision）が、クロールがクラッシュと読み違える予期しない OS
プロンプトを閉じます。web に OS アラートはありません。あるのは JS ダイアログ（`alert` / `confirm` /
`beforeunload`）で、Playwright が `page.on("dialog")` を通じて決定的に扱えます。位置情報や通知には `BrowserContext`
の権限モデルもあります。したがって web では `clear_blocking` は**決定的**なダイアログ自動処理（固定方針に従って受諾
または却下）になり、モデル呼び出しも vision 往復も要りません。何を探索するかを提案する AI ガイドはそのままで、
変わるのはダイアログを片付ける手順だけです。

### そのまま引き継ぐもの

- **状態の fingerprint**：主は `data-testid` の id 集合（Playwright ドライバは `data-testid` を `identifier` に
  対応づける）、id の少ないページには構造的フォールバック。`crawl.py` の同じコード経路で、web 専用の分岐は
  ありません。
- **AI ガイド（`--guide ai`）**：ガイドは要素ツリーを読んで操作や現実的な入力を提案するもので、すでにドライバ
  非依存です。iOS 固有のガイド機能である vision のタブロケータ（ツリーで指定できないネイティブのタブバーに
  `tap_point` を出す）は web ではまず不要です（DOM のコントロールは role や `data-testid` で指定できる）。
  `tap_point` は Playwright ドライバにあるので、仮にガイドが使っても破綻しません。
- **出力**：`screenmap.json` と `report.html` に描いたグラフ、クラッシュ再現シナリオ（YAML、web の `run` で実行
  可能）、候補シナリオ（人間のレビュー用の*提案*として出し、コミット済み YAML に黙って書き込まない）、そして
  doctor が web に対応した後に `doctor --from <runId>` が使う画面ごとの `elements` カバレッジダンプ。
  スクリーンショットは Playwright ドライバが備える `driver.screenshot()` を使います。

### WebUI（serve）

Crawl タブに、Simulator と並ぶターゲットとして web backend を加えます。ライブの画面マップ配信は既存のものを再利用
します（`screenmap.json` は backend によらず同じ書き方で出力されます）。

## 検討した代替案

**共有エンジンではなく Playwright のネイティブなクロールやセマンティッククリックを使う.**
却下します。マッチングを Playwright 自身のエンジン（`get_by_test_id().click()`）に通すと決定性コアから外れます。
[`drivers/playwright.py`](../../../bajutsu/drivers/playwright.py) が、すべての操作を `query()` スナップショットに対する
共有の `base.resolve_unique` で解決しているのは、まさにこの理由です。共有のクロールエンジンは web と iOS の
クロールを同一に保ち、画面マップを比較可能にします。

**AI にクラッシュを判定させる**（「ページが壊れて見えるかをモデルに尋ねる」）。
却下します。[prime directive #1](../../../CLAUDE.md) に反しますし、web にはモデルを要しない安価な決定的信号
（pageerror / HTTP ステータス / 空 DOM）がすでにあります。

**BE-0038 に統合する.**
却下します。BE-0038 は全面的に iOS を前提に書かれており、すでに着手済みです。web のクラッシュ判定とライフサイクル
seam は独立した設計面です。これは、web の `run` 経路が `run` の改修ではなく独立項目
（[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）になったのと同じ筋です。
[BE-0064（並列クロール）](../BE-0064-parallel-crawl/BE-0064-parallel-crawl-ja.md)が BE-0038 に対して持つ「同じ
エンジン、別の軸」の関係とも同型です（あちらは並行度を、本項目はプラットフォームを変えます）。

**[BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md)（web backend の完成）を待つ.**
必要ありません。クロールが要るのは `query` / `tap` / `type` / `tap_point` / `screenshot`、ライフサイクル seam、
健全性信号だけで、いずれもすでにあるか小さな追加です。BE-0054 のリッチな取得（ネイティブ network、video、並列
実行）は後の web クロールを豊かにしますが、本項目を妨げません。とくに web の並列クロールは、本項目と
parallel-crawl の軸の交点で、両者が入った後に続けられます。

## 参考

- [BE-0038 — 自律クロール探索](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)：本項目が新しい backend へ広げる、プラットフォーム非依存のエンジン。
- [BE-0041 — Web（Playwright）backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)：web ドライバと、決定的な web の `run` 経路。
- [BE-0054 — Web backend の完成](../BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md)：後の web クロールを豊かにするリッチな取得（network / video / 並列）。
- [BE-0064 — 複数シミュレータでの並列クロール](../BE-0064-parallel-crawl/BE-0064-parallel-crawl-ja.md)：別の軸（並行度）の兄弟項目。web の並列クロールは両者の交点。
- [`crawl.py`](../../../bajutsu/crawl.py)、[`cli/commands/crawl.py`](../../../bajutsu/cli/commands/crawl.py)、[`runner/launch.py`](../../../bajutsu/runner/launch.py)、[`runner/pool.py`](../../../bajutsu/runner/pool.py)、[`drivers/playwright.py`](../../../bajutsu/drivers/playwright.py)、[`config.py`](../../../bajutsu/config.py)。
- [DESIGN §2 / §3.1 / §5 / §7.2 / §9](../../../DESIGN.md)、[CLAUDE.md](../../../CLAUDE.md) の prime directive #1（AI は判定しない）と #2（決定性優先）、[multi-platform.md](../../../docs/ja/multi-platform.md)。
