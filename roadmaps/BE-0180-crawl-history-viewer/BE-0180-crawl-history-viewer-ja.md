[English](BE-0180-crawl-history-viewer.md) · **日本語**

# BE-0180 — Web UI へのクロール履歴ビューア

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0180](BE-0180-crawl-history-viewer-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0180") |
| 実装 PR | [#750](https://github.com/bajutsu-e2e/bajutsu/pull/750) |
| トピック | オーサリング体験 |
<!-- /BE-METADATA -->

## はじめに

Web UI の Crawl タブに、**過去のクロールの履歴一覧**を追加します。`bajutsu crawl` は実行中に
`runs/<id>/screenmap.json` を逐次書き込み、完了時には自己完結の `runs/<id>/screenmap.html`
も生成しています
（[BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）。
Crawl タブはクロールの実行中、この JSON をインタラクティブなグラフ（ズーム・パン・ノードの
ドラッグに対応）として描画します
（[BE-0072](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui-ja.md)、
[BE-0095](../BE-0095-interactive-crawl-graph/BE-0095-interactive-crawl-graph-ja.md)）。
ただし、クロールが終わってブラウザのタブを離れたり閉じたりしたあと、そのグラフに戻る手段が
ありません。今のところ過去のクロールのグラフを見る唯一の方法は、run ディレクトリから
`screenmap.html` を直接開くことです。本項目は、Crawl タブに過去のクロール run を一覧表示し、
どの run についても同じインタラクティブグラフを読み取り専用で再表示できるようにすることで、
このギャップを埋めます。

## 動機

Web UI にはすでに run の履歴があります。Replay タブの History リストで、
`list_runs()`（[`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py)）が支えています。
このリストは各 run ディレクトリの `manifest.json` を読み、pass/fail のシナリオレポートとして
要約するものです。一方、`bajutsu crawl` の run は `manifest.json` を書きません。生成する
成果物は `screenmap.json` であり、画面と遷移のマップであって pass/fail という概念を持たない
ため、クロール run はこのリストに現れません。完了したクロールを見返したい利用者には UI 内の
経路がなく、run id を把握したうえでファイルシステム上の `screenmap.html` を探すしかありません。
ブラウザからクロールを操作できるようにした狙いが、この一点で損なわれています。

同じギャップは、クロールがマップと一緒に生成する二つの成果物にも及びます。忠実に再現できる
クラッシュ一件につき一つの `crashes/crash-NNN.yaml`
（[`crawl_repro.py`](../../bajutsu/crawl_repro.py)）と、忠実に到達できる画面一件につき一つの
`flows/flow-NNN.yaml`（[`crawl_flows.py`](../../bajutsu/crawl_flows.py)）で、どちらも
`bajutsu run` でそのまま実行できます。現状、どちらも Web UI のどこからもリンクされておらず、
見つけるには run ディレクトリを手動でたどるしかありません。

## 詳細設計

**1. クロール専用の run 一覧。** [`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py)
の `list_runs()` の隣に `list_crawl_runs(runs_dir)` を追加します。`manifest.json` を前提と
する `list_runs()` とは異なり、各 run ディレクトリの `screenmap.json`（結果を問わずクロール
run が必ず書く唯一のファイル）を走査し、`manifest.json` の有無には依存しません。一致した
run ごとに JSON を一度読み、ノード数・エッジ数・クラッシュ数（Crawl タブが `renderGraph`
の中でフェッチしたマップから今すでにクライアント側で計算している項目と同じもの）を要約し、
`crashes/` と `flows/` にファイルがあるかどうかも記録します。並び順は `list_runs()` と同じく、
run id の新しい順です。

**2. Read エンドポイント。** この一覧を `/api/crawl/runs` として公開します（既存の
`/api/runs` と並べて、
[`bajutsu/serve/operations/reads.py`](../../bajutsu/serve/operations/reads.py) に置きます）。
クラッシュ/フローのファイル自体には新規の配信経路は不要です。`/runs/<id>/...` は run
ディレクトリの静的な内容をすでに配信しており（`screenmap.json` やスクリーンショットが
ブラウザに届いているのもこの経路によるものです）、`crashes/crash-001.yaml` へのリンクは
この既存マウント下の一パスにすぎません。

**3. Crawl タブ内の履歴リスト。**
[`bajutsu/templates/serve.js`](../../bajutsu/templates/serve.js) /
[`serve.html.j2`](../../bajutsu/templates/serve.html.j2) の Crawl ビューに、
`/api/crawl/runs` から値を取る専用のリストを追加します。Replay タブの `#history` とは別に
用意するのは、クロール run の要約（画面数・遷移数・クラッシュ数）が、シナリオレポートの
pass/fail という形状と共有できないためです。項目を選ぶと、既存の `loadGraph(runId)` を
呼び出すだけで済みます。`loadGraph` はすでに `/runs/<runId>/screenmap.json` を取得し、
`renderGraph` と `renderPlan` で描画しており、描画の経路自体には変更を加えません。

**4. 読み取り専用の見せ方。** 選択した過去の run を、実行中のクロールフォームと混同させては
なりません。過去の run を選んでいる間は、開始/停止ボタン、ターゲット/シミュレータの選択、
最大画面数・最大ステップ数の予算フィールドを無効化（または非表示に）し、表示している
グラフが過去の run であることをステータス行の run id や「過去のクロール」といったバッジで
明示します。**開始** を改めて押すとライブのフォームに戻り、過去の run の選択は解除されます。
これは、今日の `crawlDone` が次の run に備えてタブを開いたままにする挙動と同じです。

**5. クラッシュ/フローへのリンク。** 再表示したグラフの脇に、選択中の run の
`crashes/*.yaml` と `flows/*.yaml` のファイル名を（手順 1 ですでに集めた件数から）列挙し、
それぞれを既存の `/runs/<id>/...` 静的マウントへの単純なリンクとして出します。開くと
シナリオの YAML がそのまま表示されます。ターゲットの `scenarios/` ディレクトリや Author
タブへの取り込みは、本項目の範囲外です（検討した代替案を参照してください）。

## 検討した代替案

- **クロール run を Replay タブの既存 History リストに統合する。** 見送りました。
  `list_runs()` とそのリスト項目は、pass/fail のシナリオレポート（`ok`、`passed`/`total`、
  `scenarios`）を前提にした形状であり、クロール run はそのいずれも持ちません。同じリストを
  使い回すと、クロール項目にその形状を偽装させるか、`list_runs()` の利用側すべてに run の
  種別分岐を持たせるかのどちらかになります。Crawl タブ専用のリストにすることで、それぞれの
  履歴リストが何を要約しているかを正直に保てます。
- **読み込んだ過去の画面マップからクロールを再開する。** 本項目では見送りました。再開すると
  いうことは、クロールエンジン（[`bajutsu/crawl.py`](../../bajutsu/crawl.py)）が既存のマップ
  を出発点として受け取り、その探索フロンティアから探索を続けられるようにする変更であり、
  レポートビューアだけでなく決定的なクロールエンジン自体への変更になります。本提案は読み取り
  専用のビューアの範囲にとどめ、再開は別の、より大きなアイデアとして扱います。
- **クラッシュ/フローのシナリオをターゲットの `scenarios/` ディレクトリへワンクリックで
  取り込み、Author タブへそのまま開く。** 本項目では見送りました。ビューア自体の範囲に
  留めるためです。既存の静的ファイルへの単純なリンクだけでも、利用者はシナリオを読んで
  コピーできます。案内付きの取り込みは、履歴リストができたあとの自然な発展であり、その
  前提条件ではありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `list_crawl_runs()` helper（`screenmap.json` を目印に走査し、件数を要約し、
      `crashes/`/`flows/` の有無を検出する）
- [x] `/api/crawl/runs` read エンドポイント
- [x] Crawl タブ内の履歴リスト（既存の `loadGraph()` に接続する）
- [x] 読み取り専用の見せ方（ライブ操作の無効化、過去の run であることの明示）
- [x] 選択中の run のクラッシュ/フローファイルへのリンク

**ログ**

- [#750](https://github.com/bajutsu-e2e/bajutsu/pull/750) — `list_crawl_runs()` が runs ディレクトリを `screenmap.json` で走査し、各 crawl の画面・
  遷移・クラッシュの件数と、`crashes/*.yaml`・`flows/*.yaml` のファイル名を要約します。これを
  `/api/crawl/runs` として読み取り専用で公開しました（stdlib ハンドラと FastAPI アプリの両方）。Crawl
  タブには Form/History のサブタブを設け、過去の run を選ぶと既存の `loadGraph()` で画面マップを開き、
  「past crawl」バッジを付けて表示中はライブフォームを無効化し、その run のクラッシュ/フローシナリオ
  ファイルを既存の `/runs/<id>/...` 静的マウントへリンクします。

## 参考

- [`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py) — 本項目がクロール run
  向けに写し取る `list_runs()` のパターン。
- [`bajutsu/serve/operations/reads.py`](../../bajutsu/serve/operations/reads.py) — 現在
  `/api/runs` が置かれている場所で、提案する `/api/crawl/runs` もここに並びます。
- [`bajutsu/templates/serve.js`](../../bajutsu/templates/serve.js) — `loadGraph`、
  `renderGraph`、`renderPlan`、および Crawl タブの既存コントロール。
- [`bajutsu/crawl_repro.py`](../../bajutsu/crawl_repro.py) と
  [`bajutsu/crawl_flows.py`](../../bajutsu/crawl_flows.py) — 本項目がリンクする
  クラッシュ/フローシナリオの書き込み処理。
- [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)
  — 本項目のビューアが再表示するクロールエンジンと画面マップで、本提案では変更しません。
- [BE-0095](../BE-0095-interactive-crawl-graph/BE-0095-interactive-crawl-graph-ja.md) —
  過去の run に対しても本項目がそのまま再利用するインタラクティブグラフの描画（ドラッグ/
  リアライン）。
