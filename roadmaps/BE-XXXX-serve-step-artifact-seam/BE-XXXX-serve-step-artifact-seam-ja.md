[English](BE-XXXX-serve-step-artifact-seam.md) · **日本語**

# BE-XXXX — Route serve step-artifact reads through the ArtifactStore seam

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-step-artifact-seam-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

`ServeState.artifacts`（`bajutsu/serve/state.py:346-348`）は、run のアーティファクトをどちらの
backend からも同じ方法で読み戻せるように BE-0015 が用意した seam です。`LocalArtifactStore`
（`bajutsu/serve/artifacts.py`）は `runs_dir` に閉じたファイルを読み、`ObjectStorageArtifactStore`
（`bajutsu/serve/server/artifacts.py`）は同じ run 相対パスを S3 互換のオブジェクトストレージから
取得し、バイト列をそのまま返す代わりに署名付き URL へのリダイレクトを返します。
`_persist_run`/`_read_manifest`（`bajutsu/serve/jobs.py:338-343`）と
`_run_manifests`/`run_set_manifests`（`bajutsu/serve/operations/reads.py:183-227`）は、すでにこの
seam を正しく経由しています。`_read_manifest` は run の `manifest.json` を `runs_dir` のパスでは
なく `state.artifacts.open_bytes(...)` で読んでいます。

ところが、同じモジュール内の複数の読み取り経路は、この seam を経由せずローカルのファイルシステムへ
直接手を伸ばしています。

- `_step_artifacts`（`bajutsu/serve/operations/reads.py:289-342`）は、シナリオエディタの
  `/api/scenario?runId=...` レスポンスに埋め込まれるステップごとのアーティファクト一覧を組み立てる
  関数で、`state.runs_dir / run_id / "manifest.json"`（`reads.py:301`）を読み、
  `state.runs_dir / run_id / step_id / "after.png"` の存在（`reads.py:327`）を直接確認しています。
- `resolve_scenario_pick`（`bajutsu/serve/operations/reads.py:461-520`）は
  `POST /api/scenario/resolve`（ライブの driver なしでシナリオを編集する際、保存済みスクリーン
  ショットから要素をピックする機能）を支える関数で、
  `state.runs_dir / run_id / step_id / "elements.json"` を直接読んでいます（`reads.py:497`）。
- `coverage_view`（`bajutsu/serve/operations/coverage.py:66-72`）は `state.runs_dir` をそのまま
  `bajutsu.coverage.read_exchanges`/`read_observed_ids` に渡しており、これらの関数はその配下の
  `network.json` と `elements.json` を glob で探索します（`bajutsu/coverage.py:341-380`）。
- `start_capture`（`bajutsu/serve/operations/capture.py:62-65`）は、キャプチャセッションのライブ
  スクリーンショットを `state.runs_dir / "_capture"` へ書き込み（`bajutsu/serve/handler.py:605-611`
  が後でこれを読み戻します）。

`read_scenario`（`reads.py:265-286`。内部で `_step_artifacts` を呼びます）は `app.py:222-240` で
`/api/scenario` に、`resolve_scenario_pick` は `app.py:438-440` で `/api/scenario/resolve` に、
それぞれ配線されています。どちらもホスト型 `server` backend（`bajutsu/serve/server/app.py`）から
到達可能であり、そこでは `state.hosted` が `True` で `state.artifacts` は
`ObjectStorageArtifactStore` になっているため、読みに行けるローカルの `runs_dir` ツリーはそもそも
存在しません。本提案は、これらの読み取りを `_read_manifest` がすでに実践している方法と同じやり方
で seam 経由に切り替え、BE-0015 が構築した抽象化を、後から追加された経路だけでなく、ステップ
アーティファクトを読むすべての経路で成り立たせます。

## 動機

アーティファクトをオブジェクトストレージに置くホスト型デプロイでは、`_step_artifacts` と
`resolve_scenario_pick` が `state.runs_dir` を直接読むコードは例外を送出しません。空か存在しない
ローカルディレクトリを静かに読み過ごすだけです。`_step_artifacts` は空のステップ一覧を返し（実際
にはステップごとのアーティファクトが存在する run でも、エディタにはそのハンドルが一つも表示されま
せん）、`resolve_scenario_pick` の `elements_path.is_file()` チェックは常に `False` になるため、
run の `elements.json` がオブジェクトストレージ上に実在していても、要素ピックのリクエストは
`{"error": "elements.json not found for this step"}, 404` を返し続けます。どちらの不具合も、外部
からはデータが存在しないように見えるため、原因の切り分けに時間がかかります。これはまさに
`ArtifactStore` seam が防ぐはずだった種類のずれです。ホスト型 backend が大きくエラーになるでも
正しく動くでもなく、空の結果へ静かにフォールバックしてしまう状態です。しかも
`_persist_run`/`_run_manifests` は同じパッケージの中ですでに `state.artifacts` を正しく経由して
おり、この不整合が seam そのものの限界ではなく、このモジュール内部に閉じた問題であることを示して
います。

`coverage_view` の直接的な `runs_dir` 読み取りと、`start_capture` のスクラッチスクリーンショット
も同じ `runs_dir` 直読みの形をしています。見た目にわかりやすい 2 つのエンドポイントだけを直しても、
モジュールの残りは同じ不整合を抱えたままになり、次に手を入れる人がそこから同じパターンを真似て
しまいかねません。

## 詳細設計

作業は、ホスト型 backend からどれだけ直接到達しやすいかの順に、4 つの単位へ MECE に分解できます。

### 1. `_step_artifacts` を `state.for_org(org).artifacts` 経由にする

`_step_artifacts`（`reads.py:289-342`）はすでに `ServeState` をスコープ内に持っています。マニ
フェストの読み取りは、`manifest_path.is_file()` と `.read_text()` の組を、
`state.for_org(org).artifacts.open_bytes(f"{run_id}/manifest.json")` に置き換え、`_read_manifest`
が同じ内容をパースしているのと同じ方法（`json.JSONDecodeError`/`OSError` を捕捉した
`json.loads`）で扱います。`_step_artifacts` の唯一の呼び出し元である `read_scenario`
（`reads.py:265-286`）は、`_step_artifacts` を呼ぶ数行前ですでに `state.org_of(actor)` で `org`
を解決しているため、`org` を引き渡すのに新たなルックアップは不要で、引数を 1 つ増やすだけで済み
ます。

ステップごとの存在確認（`reads.py:326-339` の `elements_file.is_file()`、
`screenshot_file.is_file()`）には、新しい protocol メソッドは必要ありません。
`ArtifactStore.get(rel)`（`bajutsu/serve/artifacts.py:46-47`）は、両方の実装で存在しないパスに
対してすでに `None` を返しており、`ObjectStorageArtifactStore.get`（`server/artifacts.py:56-61`）
はこれを本体の取得ではなく `store.exists(key)` という HEAD 相当の確認だけで解決しています。つまり
`store.get(f"{run_id}/{step_id}/after.png") is not None` は、どちらの backend でも従来の
`is_file()` と同じくらい軽い処理のままです。`elementsUrl`/`screenshotUrl` は、これまでどおり
`/runs/<run_id>/<step_id>/...` という HTTP パス（`handler.py` 自身の `GET /runs/...` ルートは
すでに `state.artifacts.get` 経由で配信しています）を指し続けるため、変わるのはこの URL を出すか
どうかを決める存在確認だけで、URL の形そのものは変わりません。

### 2. `resolve_scenario_pick` も同じ seam 経由にする

`resolve_scenario_pick`（`reads.py:461-520`）は、`elements.json` を読む前（`reads.py:487-499`）
にすでに `_resolve_org_or_forbid` で `_org` を解決しています。修正では、`elements_path.is_file()`
と `.read_text()` の組を、その `_org` を再利用した
`state.for_org(_org).artifacts.open_bytes(f"{run_id}/{step_id}/elements.json")` に置き換えます。
`json.loads` や `isinstance(raw, list)` による既存の検証はそのままです。

### 3. coverage の evidence 読み取りも seam 経由にする

`coverage_view`（`operations/coverage.py:66-72`）は `state.runs_dir` を
`read_exchanges`/`read_observed_ids`（`bajutsu/coverage.py:341-380`）にそのまま渡しており、両関数
は指定された run id 集合の配下で `network.json`/`elements.json` を glob します（共通の
`_evidence_files`、`coverage.py:330-338` 経由）。`ArtifactStore` には今のところ glob やディレクト
リ列挙の手段がなく、あるのは決まった 1 つのパスを読む `open_bytes(rel)` と、run の概要を返す
`list_runs()` だけです。したがって、この単位だけは既存メソッドの使い回しではなく、実際に新しい
機能が必要になる唯一の箇所です。protocol に汎用的な glob を追加するのではなく（それは store の
内部レイアウトを seam の外へ漏らしてしまいます）、この単位ではモジュールがすでに持っているデータ
から、確認すべきステップ id を導出します。すでに seam 経由になっている `run_set_manifests`
（`reads.py:202-227`）は各 run のマニフェストを返し、その `scenarios[].sid` と各シナリオのステップ
名を組み合わせれば、`_evidence_files` が glob で探していたのと同じ `sid/step` のパスがちょうど
得られます。`read_exchanges`/`read_observed_ids` には、この明示的なステップ id リストを受け取り、
`runs_dir` を glob する代わりにパスごとに `open_bytes` を呼ぶ `ArtifactStore` ベースの版（あるいは
オーバーロード）を追加し、`ServeState` の外で正当にローカルの `runs_dir` を持つ呼び出し元（CLI
コマンドやテストなど）向けには、既存の glob ベースのシグネチャをそのまま残します。

### 4. capture のセッション限りのスクラッチスクリーンショット

`start_capture` の `shot_dir = state.runs_dir / "_capture"`（`operations/capture.py:62-65`）と、
後で `handler.py` が行う `session.screenshot_path.read_bytes()`（`handler.py:605-611`）は、他の
3 つと同じ `runs_dir` 相対の形をしていますが、これらはそもそも永続化された run のアーティファクト
ではありません。キャプチャセッションはライブでプロセス内に存在するオブジェクト
（`state.capture: CaptureSession | None`）であり、その driver と HTTP handler はセッションが続く
あいだ常に同じプロセス上で動きます。これは、BE-0015 がすでに別の worker での実行を許している
`run`/`record` の job とは異なる点です。この経路を `state.artifacts` 経由にするには、キャプチャ
のスクラッチファイルを合成の run id で同じ store に紐づける（seam が言う「run」の意味を広げる
ことになります）か、セッション限りの store をもう一つ追加するかのどちらかが必要になり、いずれも
本提案のスコープを超える変更になります。この単位でやることはもっと狭く、`_capture` のスクラッチ
ディレクトリを、ホスト型でも書き込み可能なパスのまま保つこと（すでにそうなっています。
`state.runs_dir` は、ライブの driver を持つホストまたは worker 上のローカルな書き込み可能ディレ
クトリです）と、`capture.py:62` にコメントを添えて、このパスが意図的に `ArtifactStore` seam の
外側にとどまっている理由を残すことです。これにより、後から読む人がこの `runs_dir` の使い方を
単位 1〜3 と同じ見落としだと誤解しないようにします。

### パス安全性のチェックは seam 呼び出しの脇に残す

`valid_run_id` と `_valid_step_id`（`reads.py:358-363`）は、`run_id`/`step_id` が
`open_bytes`/`get` のキーの一部になる前に検証する役割を、そのまま引き続き担います。これは、
`ArtifactStore` の各実装自身が境界で独自に適用しているのと同じ封じ込めの規律です
（`LocalArtifactStore._resolve`、`ObjectStorageArtifactStore._key`）。同じ形のチェックを 2 か所で
行うのは意図的な多層防御であり、取り除くべき重複ではありません。呼び出し側のチェックは、細工され
た id を store へ届く前に `400`/`404` に変えるためのものであり、store 側のチェックは、将来の
呼び出し元がチェックを書き忘れた場合でも、seam を使う他のすべての利用者を安全に保つためのもの
です。

## 検討した代替案

- **`runs_dir` の直読みをそのまま残し、ローカル限定として文書化する。** 却下します。
  `_step_artifacts` と `resolve_scenario_pick` は、どちらも現時点でホスト型 `server` backend
  （`app.py:222-240`、`app.py:438-440`）に配線されており、`state.hosted` が `True` のときにこれら
  を無効化するゲートはありません。到達可能で、しかもその backend 上で静かに誤動作するコード経路
  を「ローカル限定」と注記するだけで済ませるのは、注意書きをまとった不具合であって、スコープの
  選択ではありません。
- **`ArtifactStore` に汎用の `list_files(prefix) -> list[str]`（あるいは glob）メソッドを追加する。**
  単位 3 のために検討しましたが、`run_set_manifests` がすでに読んでいるマニフェストからステップ id
  を導出する方法を採用し、却下しました。glob 相当のメソッドはローカル store では自然に動作します
  が、`ObjectStorageArtifactStore` に `Path.glob` と同じトラバーサル意味論を持つキープレフィックス
  列挙を実装させることになり、seam の他のすべてのメソッド（呼び出し元が名指しした 1 つのパスだけ
  を読み、何が存在するかを列挙することは一切ない）よりも広く、漏れやすい面を持ち込んでしまいます。
- **capture のスクラッチスクリーンショットも、単位 1〜3 と一緒に `ArtifactStore` seam へ組み込む
  第 4 の単位として出荷する。** 本提案のスコープでは却下します。キャプチャセッションは永続化された
  run ではなく、その driver と handler は常に同じプロセスを共有するため、単位 1〜3 を動機づけている
  ホスト型 backend での正しさの議論はそのままでは当てはまりません。黙って組み込む、あるいは黙って
  放置するのではなく、意図的な例外として明文化するにとどめます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 1 — `_step_artifacts`（`reads.py`）を `state.for_org(org).artifacts` 経由にし、直接の
      `runs_dir` マニフェスト読み取りとステップごとの存在確認を `open_bytes`/`get` に置き換える。
- [ ] 2 — `resolve_scenario_pick`（`reads.py`）の `elements.json` 読み取りを
      `state.for_org(_org).artifacts` 経由にする。
- [ ] 3 — `coverage_view`（`operations/coverage.py`）に、`read_exchanges`/`read_observed_ids`
      （`bajutsu/coverage.py`）が現在 `runs_dir` を直接 glob している部分の代わりとなる seam 経由の
      経路を用意する。`ArtifactStore` に glob 相当の手段を追加するのではなく、すでに seam 経由に
      なっているマニフェスト読み取りからステップ id を導出する。
- [ ] 4 — `operations/capture.py:62` に、ライブのキャプチャセッションのスクラッチスクリーン
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
