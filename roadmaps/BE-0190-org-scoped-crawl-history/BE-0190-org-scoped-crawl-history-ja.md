[English](BE-0190-org-scoped-crawl-history.md) · **日本語**

# BE-0190 — サーバーバックエンドでの org スコープ付き crawl 履歴

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0190](BE-0190-org-scoped-crawl-history-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0190") |
| 実装 PR | [#781](https://github.com/bajutsu-e2e/bajutsu/pull/781) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0180](../BE-0180-crawl-history-viewer/BE-0180-crawl-history-viewer-ja.md) |
| 由来 | [BE-0180](../BE-0180-crawl-history-viewer/BE-0180-crawl-history-viewer-ja.md) のレビュー |
<!-- /BE-METADATA -->

## はじめに

Crawl タブの履歴リスト（[BE-0180](../BE-0180-crawl-history-viewer/BE-0180-crawl-history-viewer-ja.md)）
を、ローカルと同じように**サーバーバックエンド**でも動くようにします。BE-0180 は、各 run の
`screenmap.json` を手がかりにした読み取り専用の crawl 履歴ビューアを出荷しましたが、その一覧はローカルの
`runs_dir` を直接走査します。サーバーバックエンドでは、run の成果物はローカルファイルシステムではなく org
スコープ付きのオブジェクトストアに置かれるため、BE-0180 はそこでは意図的に空リストを返しています
（[`crawl_runs_payload`](../../bajutsu/serve/operations/reads.py) が `state.repository` で分岐します）。
本項目はこの隔たりを埋めます。crawl 履歴の一覧を、すでに `/api/runs` や `/runs/<id>/...` を支えているのと
同じ org スコープ付きの `ArtifactStore` の接続点へ移し、ホスティング環境でも同じ履歴を、テナントごとに
正しく分離した形で得られるようにします。

## 動機

サーバーバックエンド（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) のマルチテナント）では、run 履歴もすべての run 成果物も、操作者の org に
スコープされたストアから配信されます。`runs_payload` はその org に記録された run を読み、`/runs/<id>/...`
は `state.for_org(state.org_of(actor)).artifacts` を通じてバイト列を配信します。crawl 履歴だけが、この
パターンに従っていません。ローカルパスである `state.runs_dir` を走査しているからです。

このため、ホスティングされたバックエンドでは、BE-0180 のレビューで指摘された二つの問題が残ります。

1. **機能しない**。crawl run の成果物（`screenmap.json`、`crashes/*.yaml`、`flows/*.yaml`）はオブジェクト
   ストアに書き込まれ、`runs_dir` には置かれません。そのためローカル走査は何も見つけられず、ホスティング
   環境の利用者にとって Crawl タブの History は、crawl がマップを生成していても常に空になります。
2. **テナントの安全性**。仮に `runs_dir` が複数 org のスクラッチデータを保持していた場合、ローカル走査は
   org の境界を越えて run の ID を露出させます。これは、org スコープ付きのストアが防ぐために存在している、
   まさにそのテナント越えの漏洩です。

BE-0180 は安全な暫定策を選びました。repository が接続されているときはエンドポイントを無効化し、漏洩や
誤解を招くよりも空を返す、というものです。本項目は、ストア自身に crawl 一覧の機能を持たせることで、この
制約を取り除きます。これにより、履歴は特別扱いなしにどのバックエンドでも動くようになります。

## 詳細設計

**1. `ArtifactStore` の接続点に crawl 一覧を追加する**。[`ArtifactStore`](../../bajutsu/serve/artifacts.py)
の Protocol に `list_crawl_runs()` を追加します。既存の `list_runs()` に倣いつつ、手がかりを
`manifest.json` ではなく `screenmap.json` にします。返す要約の形は BE-0180 のヘルパーがすでに生成している
ものと同じで、`id`・`screens`・`transitions`・`crashes`・`crashFiles`・`flowFiles` です。そのため
`/api/crawl/runs` のペイロードも Crawl タブの JavaScript も変わりません。

**2. 両方のストアに実装する**。

- [`LocalArtifactStore`](../../bajutsu/serve/artifacts.py) は既存の `list_crawl_runs(runs_dir)` ヘルパー
  （BE-0180）へ委譲します。ローカルパスは変わらず、すでにテスト済みです。
- [`ObjectStorageArtifactStore`](../../bajutsu/serve/server/artifacts.py) は、`<runId>/screenmap.json`
  というオブジェクトを含む run の prefix を列挙し、各マップを一度読んで件数を求め、
  `<runId>/crashes/*.yaml` と `<runId>/flows/*.yaml` のオブジェクトキーからファイル名を一覧します。この
  ストアはすでに org スコープ付きです（org の prefix がストアのインスタンスに組み込まれています）。その
  ため一覧は構成上テナントセーフで、別の org の run ID には到達できません。

**3. `crawl_runs_payload` を org スコープにする**。[`reads.py`](../../bajutsu/serve/operations/reads.py)
にある `state.repository` で分岐するスタブを、`runs_payload` と同じパターンに置き換えます。操作者の org を
解決し、`state.for_org(org).artifacts.list_crawl_runs()` を呼びます。ローカルバックエンド（repository なし）
はデフォルトの org とその `LocalArtifactStore` に解決されるため、現在の挙動を保ちます。

**4. 二つのトランスポートに actor を通す**。`crawl_runs_payload` は `actor` を受け取り、stdlib ハンドラ
（[`handler.py`](../../bajutsu/serve/handler.py)）と FastAPI アプリ（[`server/app.py`](../../bajutsu/serve/server/app.py)）
は `/api/runs` と同じように `self._actor()` / `_actor(request)` を転送します。ローカルモードは actor を
無視し、サーバーバックエンドはそれを使って org のストアを選びます。

**5. テスト**。サーバーのテストがすでに使っている、インメモリ（あるいはローカル）のオブジェクトストアの
代替に対して、`ObjectStorageArtifactStore.list_crawl_runs()` の単体テストを書きます（二つの org の prefix
に screenmap を置き、各ストアが自分の分だけを見ることを確かめます）。また
`test_runs_payload_lists_from_the_repository_scoped_to_the_org` に倣い、`crawl_runs_payload` がある org の
crawl を一覧し、別の org の分を除外することを確かめる operations テストを書きます。ローカルパスは BE-0180
の既存テストで引き続きカバーされます。

一覧は最初から最後まで読み取り専用で AI を含みません。保存済みの成果物を列挙して要約するだけで、`run` や
CI の判定パスには一切触れないため、BE-0180 と同じくプライムディレクティブ 1 を満たします。

## 検討した代替案

- **サーバーバックエンドではエンドポイントを無効のままにする（BE-0180 の暫定策）**。最終形としては却下
  します。これはホスティング環境の利用者が自分の crawl 履歴を決して見られないことを意味し、マルチテナント
  の Web UI に残る恒久的な隔たりになります。暫定策としてのみ許容できます。
- **完了した replay run と同じように、crawl run を system of record（データベース）に記録する**。却下し
  ます。crawl には pass/fail の判定も manifest もないため `RunRecord` の形に収まらず、要約のもとになる
  `screenmap.json` はすでにオブジェクトストアにあります。ストアから一覧するほうが、小さく正直な変更です。
  データベースの行は、ストアがすでに保持している状態を二重に持つことになります。
- **`ArtifactStore` の接続点を通さない、別のオブジェクトストア走査**。却下します。これは run 成果物への
  二つ目のスコープなしパスを再び持ち込むことになり、ローカル走査を危険にしていた、まさにその要因です。org
  スコープ付きのストアを経由することが、一覧をテナントセーフにする根拠です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `ArtifactStore` Protocol への `list_crawl_runs()`
- [x] `LocalArtifactStore.list_crawl_runs()`（BE-0180 のヘルパーへ委譲）
- [x] `ObjectStorageArtifactStore.list_crawl_runs()`（`<runId>/screenmap.json` で走査し、crash/flow キーを一覧）
- [x] `state.for_org(org).artifacts` を介して `crawl_runs_payload` を org スコープ化
- [x] stdlib ハンドラと FastAPI アプリの route に actor を通す
- [x] テスト: オブジェクトストア一覧（org の分離）と org スコープ付きペイロード

ログ:

- [#781](https://github.com/bajutsu-e2e/bajutsu/pull/781) — org スコープ付きの crawl 一覧を実装しました。`ArtifactStore` の接続点に `list_crawl_runs()`
  を追加し（ローカルは BE-0180 のヘルパーへ委譲、オブジェクトストアは `<runId>/screenmap.json` を 1 回の走査で
  拾い、各 run 直下の `crashes/*.yaml`・`flows/*.yaml` キーを索引化）、両バックエンドが同一の要約を返すよう
  共通ヘルパー `helpers.crawl_run_summary` を切り出しました。`crawl_runs_payload` を
  `state.for_org(state.org_of(actor)).artifacts` へ載せ替え、actor を両トランスポートに通しています。

## 参考

- [BE-0180](../BE-0180-crawl-history-viewer/BE-0180-crawl-history-viewer-ja.md) — 本項目が拡張する crawl
  履歴ビューア。その `crawl_runs_payload` はサーバーバックエンドで無効化しており、本項目がそれを取り除き
  ます。
- [`bajutsu/serve/artifacts.py`](../../bajutsu/serve/artifacts.py) — `ArtifactStore` Protocol と
  `LocalArtifactStore`。`list_runs()` があり、`list_crawl_runs()` を並べる場所です。
- [`bajutsu/serve/server/artifacts.py`](../../bajutsu/serve/server/artifacts.py) — crawl 一覧を実装する
  対象のオブジェクトストレージ成果物ストア。
- [`bajutsu/serve/operations/reads.py`](../../bajutsu/serve/operations/reads.py) — 倣うべき org スコープの
  パターンである `runs_payload` と、置き換えるスタブである `crawl_runs_payload`。
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) マルチテナント — 本一覧が加わる org スコープのモデル（`state.for_org` / `state.org_of`）。
