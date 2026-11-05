[English](BE-0258-serve-step-artifact-seam.md) · **日本語**

# BE-0258 — serve のステップアーティファクトの読み取りを ArtifactStore seam 経由に統一

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0258](BE-0258-serve-step-artifact-seam-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0258") |
| 実装 PR | [#1075](https://github.com/bajutsu-e2e/bajutsu/pull/1075) |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

`ServeState.artifacts`（`bajutsu/serve/state.py:346-348`）は、BE-0015 が用意した seam です。run の
アーティファクトを、どちらの backend からも同じ方法で読み戻せます。

- `LocalArtifactStore`（`bajutsu/serve/artifacts.py`）は、`runs_dir` に閉じたファイルを読みます。
- `ObjectStorageArtifactStore`（`bajutsu/serve/server/artifacts.py`）は、同じ run 相対パスを S3 互換の
  オブジェクトストレージから取得します。バイト列をそのまま返さず、署名付き URL へのリダイレクトを
  返します。

`_persist_run` と `_read_manifest`（`bajutsu/serve/jobs.py:338-343`）は、すでにこの seam を正しく
経由しています。`_run_manifests` と `run_set_manifests`（`bajutsu/serve/operations/reads.py:183-227`）
も同様です。`_read_manifest` は、run の `manifest.json` を `runs_dir` のパスではなく
`state.artifacts.open_bytes(...)` で読みます。

ところが、同じパッケージ内の複数の読み取り経路は、seam を経由せず、ローカルのファイルシステムへ
直接手を伸ばしています。

- `_step_artifacts`（`bajutsu/serve/operations/reads.py:289-342`）は、シナリオエディタの
  `/api/scenario?runId=...` レスポンスに埋め込む、ステップごとのアーティファクト一覧を組み立てます。
  `state.runs_dir / run_id / "manifest.json"`（`reads.py:301`）を直接読みます。
  `state.runs_dir / run_id / step_id / "after.png"` の存在（`reads.py:327`）も、直接確認します。
- `resolve_scenario_pick`（`bajutsu/serve/operations/reads.py:461-520`）は、
  `POST /api/scenario/resolve` を支えます。このエンドポイントは、ライブの driver なしでシナリオを
  編集するときに使う、「保存済みスクリーンショットから要素をピックする」機能を担います。関数は
  `state.runs_dir / run_id / step_id / "elements.json"` を直接読みます（`reads.py:497`）。
- `coverage_view`（`bajutsu/serve/operations/coverage.py:66-72`）は、`state.runs_dir` をそのまま
  `bajutsu.coverage.read_exchanges` と `read_observed_ids` に渡します。これらの関数は、配下の
  `network.json` と `elements.json` を glob で探索します（`bajutsu/coverage.py:341-380`）。
- `start_capture`（`bajutsu/serve/operations/capture.py:62-65`）は、キャプチャセッションのライブ
  スクリーンショットを `state.runs_dir / "_capture"` へ書き込みます。`bajutsu/serve/handler.py:605-611`
  が、後でこれを読み戻します。

ホスト型 `server` backend（`bajutsu/serve/server/app.py`）は、このうち 2 つの読み取りに最も直接に到達します。

- `read_scenario`（`reads.py:265-286`。内部で `_step_artifacts` を呼びます）は、`app.py:222-240` で
  `/api/scenario` に配線されています。
- `resolve_scenario_pick` は、`app.py:438-440` で `/api/scenario/resolve` に配線されています。

ホスト型 backend では、`state.hosted` が `True` です。`state.artifacts` は
`ObjectStorageArtifactStore` で、読みに行けるローカルの `runs_dir` ツリーは存在しません。

本提案は、これらの読み取りを、`_read_manifest` がすでに実践している方法と同じやり方で seam 経由に
切り替えます。BE-0015 が構築した抽象化は、後から追加された経路を含む、ステップアーティファクトを
読むすべての経路で成り立つようになります。

## 動機

アーティファクトをオブジェクトストレージに置く、ホスト型デプロイを考えます。このデプロイでは、
`_step_artifacts` と `resolve_scenario_pick` が `state.runs_dir` を直接読みます。例外は送出されません。
空か存在しないローカルディレクトリを、静かに読み過ごします。

- `_step_artifacts` は、空のステップ一覧を返します。実際にはステップごとのアーティファクトが存在する
  run でも、エディタにはそのハンドルが一つも表示されません。
- `resolve_scenario_pick` の `elements_path.is_file()` チェックは、常に `False` になります。run の
  `elements.json` がオブジェクトストレージ上に実在していても、要素ピックのリクエストは
  `{"error": "elements.json not found for this step"}, 404` を返し続けます。

どちらの不具合も、配線のバグには見えず、データが存在しないように見えます。そのため、外部からは原因の切り分けに
時間がかかります。

これは、`ArtifactStore` seam が防ぐはずだった種類のずれです。ホスト型 backend が、エラーにも正しい動作にも
ならず、空の結果へ静かにフォールバックします。`_persist_run` と `_run_manifests` は、同じパッケージの中で
すでに `state.artifacts` を経由しています。したがって、この不整合は seam そのものの限界ではなく、この
モジュール内部に閉じた問題です。

`coverage_view` は `runs_dir` を直接読みます。`start_capture` のスクラッチスクリーンショットも、同じ
`runs_dir` 直読みの形をしています。目につきやすい 2 つのエンドポイントだけを直すと、モジュールの残りは
不整合を抱えたままになります。次に手を入れる人が、そこから同じパターンを真似しかねません。

## 詳細設計

作業は、4 つの単位に MECE に分解できます。並び順は、ホスト型 backend から現在どれだけ直接到達できるかの
順です。

### 1. `_step_artifacts` を `state.for_org(org).artifacts` 経由にする

`_step_artifacts`（`reads.py:289-342`）は、すでに `ServeState` をスコープ内に持っています。マニフェストの
読み取りは、`manifest_path.is_file()` と `.read_text()` の組を、
`state.for_org(org).artifacts.open_bytes(f"{run_id}/manifest.json")` に置き換えます。内容のパースは、
`_read_manifest` と同じ方法で行います。`json.JSONDecodeError` と `OSError` を捕捉した `json.loads` です。

`_step_artifacts` を呼ぶのは `read_scenario`（`reads.py:265-286`）だけです。`read_scenario` は、呼び出しの
数行前で、すでに `state.org_of(actor)` により `org` を解決しています。`org` を引き渡すために必要なのは、
引数 1 つの追加だけで、新たなルックアップは要りません。

ステップごとの存在確認（`reads.py:326-339` の `elements_file.is_file()` と `screenshot_file.is_file()`）
には、seam に軽量な存在確認メソッドが必要です。`get()` はそのメソッドになりません。

- ローカル backend の `LocalArtifactStore.get`（`bajutsu/serve/artifacts.py:125-130`）は、
  `Artifact(body=target.read_bytes())` を組み立てます。ファイル本文を丸ごと読みます。
- `store.get(...) is not None` を存在確認に使うと、URL を出すかどうかを決めるためだけに、各 `after.png`
  の本文をメモリに読み込みます。置き換え対象の `is_file()` より、厳密に高コストです。
- 2 つの `get` 実装は、挙動も分かれます。`ObjectStorageArtifactStore.get`（`server/artifacts.py:56-61`）は、
  安価な `exists` と presigned リダイレクトです。`get()` を存在確認に使うと、一方の backend では無料、
  もう一方では全読み込みになります。seam が抱え込むべきでない、隠れた非対称です。

そこで修正では、`ArtifactStore` protocol に `exists(rel) -> bool`（HEAD 相当）を追加します。ローカルは
`Path.is_file`、リモートはオブジェクトの `exists` で実装します。存在確認は
`state.for_org(org).artifacts.exists(...)` 経由に通します。

`elementsUrl` と `screenshotUrl` は、これまでどおり `/runs/<run_id>/<step_id>/...` という HTTP パスを
指します。`handler.py` の `GET /runs/...` ルートは、すでに `state.artifacts.get` 経由で配信しています。
変わるのは、URL を出すかどうかを決める存在確認だけです。URL の形は変わりません。

### 2. `resolve_scenario_pick` も同じ seam 経由にする

`resolve_scenario_pick`（`reads.py:461-520`）は、`elements.json` を読む前（`reads.py:487-499`）に、すでに
`_resolve_org_or_forbid` で `_org` を解決しています。修正では、その `_org` を再利用します。
`elements_path.is_file()` と `.read_text()` の組を、
`state.for_org(_org).artifacts.open_bytes(f"{run_id}/{step_id}/elements.json")` に置き換えます。
`json.loads` と `isinstance(raw, list)` による既存の検証は、そのままです。

### 3. coverage の evidence 読み取りも seam 経由にする

`coverage_view`（`operations/coverage.py:66-72`）は、`state.runs_dir` を `read_exchanges` と
`read_observed_ids`（`bajutsu/coverage.py:341-380`）にそのまま渡します。両関数は、指定された run id 集合の
配下で `network.json` と `elements.json` を glob します。glob は `runs_dir.glob(f"*/{name}")` で、共通の
`_evidence_files`（`coverage.py:330-338`）経由で行われます。

`ArtifactStore` には、今のところ glob やディレクトリ列挙の手段がありません。あるのは、決まった 1 つの
パスを読む `open_bytes(rel)` と、run の概要を返す `list_runs()` だけです。したがって、この単位は、新しい
機能が必要になる唯一の箇所であり、既存メソッドを使い回せません。

この単位は、汎用的な glob を protocol に追加しません。glob を追加すると、store の内部レイアウトが seam の
外へ漏れるからです。代わりに、モジュールがすでに持つデータから、確認すべきステップ id を導出します。

- すでに seam 経由になっている `run_set_manifests`（`reads.py:202-227`）が、各 run のマニフェストを返します。
- マニフェストの `scenarios[].sid` と、各シナリオのステップ名を組み合わせると、`_evidence_files` が glob で
  探していたのと同じ `sid/step` のパスが、ちょうど得られます。
- `read_exchanges` と `read_observed_ids` には、`ArtifactStore` ベースの版（あるいはオーバーロード）を
  追加します。この版は、明示的なステップ id リストを受け取り、パスごとに `open_bytes` を呼びます。
  `runs_dir` は glob しません。
- `ServeState` の外で、ローカルの `runs_dir` を正当に持つ呼び出し元（CLI コマンドやテスト）向けには、
  既存の glob ベースのシグネチャを残します。

### 4. capture のセッション限りのスクラッチスクリーンショット

`start_capture` は、`shot_dir = state.runs_dir / "_capture"` を設定します（`operations/capture.py:62-65`）。
後で `handler.py` が、`session.screenshot_path.read_bytes()` を呼びます（`handler.py:605-611`）。どちらも、
他の 3 つの単位と同じ `runs_dir` 相対の形をしています。

しかし、この経路は、永続化された run のアーティファクトではありません。キャプチャセッションは、ライブで
プロセス内に存在するオブジェクト（`state.capture: CaptureSession | None`）です。その driver と HTTP handler
は、セッションが続くあいだ、常に同じプロセス上で動きます。`run` や `record` の job は異なります。BE-0015
が、別の worker での実行をすでに許しているためです。

この経路を `state.artifacts` 経由にするには、2 つの大きな変更のどちらかが必要です。

- キャプチャのスクラッチファイルを、合成の run id で同じ store に紐づけます。seam が言う「run」の意味が
  広がります。
- セッション限りの store を、もう一つ追加します。

どちらも、本提案のスコープを超えます。この単位でやることは狭く、2 つあります。

- `_capture` のスクラッチディレクトリを、ホスト型でも書き込み可能なパスのまま保ちます。これはすでに
  成り立っています。`state.runs_dir` は、ライブの driver を持つホストまたは worker 上の、ローカルな
  書き込み可能ディレクトリです。
- `capture.py:62` にコメントを添えて、このパスが意図的に `ArtifactStore` seam の外側にとどまる理由を
  残します。後から読む人が、この `runs_dir` の使い方を、単位 1〜3 が直す見落としと誤解しなくなります。

### パス安全性のチェックは seam 呼び出しの脇に残す

`valid_run_id` と `_valid_step_id`（`reads.py:358-363`）は、`run_id` と `step_id` が `open_bytes` や `get`
のキーの一部になる前に検証する役割を、引き続き担います。`ArtifactStore` の各実装も、境界で独自に同じ封じ
込めの規律を適用しています（`LocalArtifactStore._resolve`、`ObjectStorageArtifactStore._key`）。

同じ形のチェックを 2 か所で行うのは、意図的な多層防御です。重複として取り除くべきではありません。

- 呼び出し側のチェックは、細工された id を、store に届く前に `400` や `404` に変えます。
- store 側のチェックは、将来の呼び出し元がチェックを書き忘れても、seam を使う他のすべての利用者を安全に
  保ちます。

## 検討した代替案

- **`runs_dir` の直読みをそのまま残し、ローカル限定として文書化する。** 却下します。
  `_step_artifacts` と `resolve_scenario_pick` は、どちらも現時点でホスト型 `server` backend
  （`app.py:222-240`、`app.py:438-440`）に配線されています。`state.hosted` が `True` のときに無効化する
  ゲートはありません。そのような文書化は、到達可能で、しかも注意書きが警告するまさにその backend 上で
  静かに誤動作するコード経路を、「ローカル限定」と呼ぶことになります。これは、注意書きをまとった不具合で
  あり、スコープの選択ではありません。
- **`ArtifactStore` に汎用の `list_files(prefix) -> list[str]`（あるいは glob）メソッドを追加する。**
  単位 3 のために検討しました。`run_set_manifests` がすでに読んでいるマニフェストから、ステップ id を導出
  する方法を採るため、却下します。glob 相当のメソッドは、ローカル store では自然に動作します。しかし、
  `ObjectStorageArtifactStore` に、`Path.glob` と同じトラバーサル意味論を持つキープレフィックス列挙を実装
  させることになります。seam が他に約束する面より、広く、漏れやすい面です。他のすべてのメソッドは、呼び
  出し元が名指しした 1 つのパスを読むだけで、何が存在するかは列挙しません。
- **capture のスクラッチスクリーンショットも、単位 1〜3 と一緒に `ArtifactStore` seam へ組み込む、第 4 の
  単位として出荷する。** 本提案のスコープでは却下します。キャプチャセッションは永続化された run ではなく、
  その driver と handler は常に同じプロセスを共有します。単位 1〜3 を動機づけた、ホスト型 backend での
  正しさの議論は、同じ形では当てはまりません。黙って組み込んだり、黙って説明なしに放置したりせず、
  意図的な例外として明文化するにとどめます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 1 — `_step_artifacts`（`reads.py`）を `state.for_org(org).artifacts` 経由にし、直接の
      `runs_dir` マニフェスト読み取りとステップごとの存在確認を `open_bytes`/`get` に置き換える。
- [x] 2 — `resolve_scenario_pick`（`reads.py`）の `elements.json` 読み取りを
      `state.for_org(_org).artifacts` 経由にする。
- [x] 3 — `coverage_view`（`operations/coverage.py`）に、`read_exchanges`/`read_observed_ids`
      （`bajutsu/coverage.py`）が現在 `runs_dir` を直接 glob している部分の代わりとなる seam 経由の
      経路を用意する。`ArtifactStore` に glob 相当の手段を追加するのではなく、すでに seam 経由に
      なっているマニフェスト読み取りからステップ id を導出する。
- [x] 4 — `operations/capture.py:62` に、ライブのキャプチャセッションのスクラッチスクリーン
      ショットが意図的に `ArtifactStore` seam の外側にとどまっている理由を文書化する。

## 参考

- [BE-0015 — Web UI の公開ホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) —
  本提案が残りの読み取り経路を通す `ArtifactStore` seam（ローカルファイルシステムとオブジェクト
  ストレージ）を導入した項目です。
- `bajutsu/serve/state.py`（`ServeState.artifacts`、`for_org`） — 単位 1・単位 2 が呼び出す、
  seam のフィールドと org スコープ用のヘルパーです。
- `bajutsu/serve/artifacts.py`（`ArtifactStore`、`LocalArtifactStore`） — protocol と、その
  ファイルシステムに閉じたデフォルト実装です。
- `bajutsu/serve/server/artifacts.py`（`ObjectStorageArtifactStore`） — `get`/`open_bytes` が
  `runs_dir` に一切触れないホスト型の実装であり、直読みが今日、空や 404 を静かに返す具体的な理由
  です。
- `bajutsu/serve/operations/reads.py`（`_step_artifacts`、`resolve_scenario_pick`、
  `run_set_manifests`、`_run_manifests`） — すでに seam 経由になっている読み取りと、本提案が
  修正する読み取りの両方を抱えるモジュールです。
- `bajutsu/serve/jobs.py`（`_persist_run`、`_read_manifest`） — 本提案の単位 1・単位 2 が倣う、
  既存の正しい先例です。
- `bajutsu/coverage.py`（`read_exchanges`、`read_observed_ids`、`_evidence_files`） — 単位 3 が
  seam 経由の代替を用意する、glob ベースの evidence 読み取り関数です。
- `bajutsu/serve/operations/capture.py`、`bajutsu/serve/handler.py` — 単位 4 が意図的な例外として
  文書化する、キャプチャセッションのスクラッチ読み書きです。
- 本提案はローカルの `serve` backend では挙動を変えず（どの読み取りも同じバイト列に解決します）、
  ホスト型 backend の正しさだけを修正します。serve は決定的な `run`/CI の判定経路（prime directive
  1、[CLAUDE.md](../../CLAUDE.md)）の周辺であり、本提案はその経路には触れません。
