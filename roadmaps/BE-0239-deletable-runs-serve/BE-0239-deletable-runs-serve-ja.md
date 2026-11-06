[English](BE-0239-deletable-runs-serve.md) · **日本語**

# BE-0239 — serve の Web UI から run（レポート）を削除できるようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0239](BE-0239-deletable-runs-serve-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0239") |
| 実装 PR | [#985](https://github.com/bajutsu-e2e/bajutsu/pull/985) _（バックエンド）_、[#1170](https://github.com/bajutsu-e2e/bajutsu/pull/1170) _（Web UI）_ |
| トピック | Web UI のホスティング |
<!-- /BE-METADATA -->

## はじめに

`serve` の Web UI から、run とそのレポートを一件ずつ削除できるようにします。対象は、ローカル実行（stdlib ハンドラ、ファイルシステム保存）と、ホスト型 serve（FastAPI、データベースとオブジェクトストレージ、BE-0015）の両方です。

削除は、即座の完全消去にはしません。ソフトデリートとゴミ箱の猶予期間を挟みます。本当に消し去る完全削除（purge）には、監査ログを残します。crawl run（BE-0190）にも、同じ個別削除を提供します。

現状の `serve` には、run を削除する手段が一切ありません。API 上の唯一の `DELETE` エンドポイントは、プロジェクトの登録解除（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）です。`runs/<id>/` 配下のツリー（`report.html`、`manifest.json`、スクリーンショット、動画、ネットワークキャプチャ）を消すには、ホストにシェルで入って `rm -rf` するしかありません。ホスト型 serve では、run がオブジェクトストレージとデータベースの行（`bajutsu/serve/server/db.py`）に保存されています。ユーザーが触れられるファイルシステムがないため、この手段は使えません。

## 動機

run は放っておくと、際限なく積み上がります。`serve` には、`runs/` ツリーやホスト側オブジェクトストレージの org プレフィックスを縮める仕組みが何もありません。デモ用途の小さなプロジェクトなら問題になりませんが、次の四点で実害につながります。

1. **問題のある run に対して、何もできません。** 誤ったターゲットに記録した run、1 回きりの flaky な run、記録後に不適切な内容を含むと分かった証跡があります。証跡の例は、画面録画や、BE-0151・BE-0152 が扱う漏えいに近い事例です。これらを削除できるのは、ファイルシステムやバケットに直接触れる運用担当者だけです。その run が不要だと一番よく分かっている viewer や editor は、Web UI 上で何もできません。
2. **ホスト型 serve では、無制限の増加がそのままコストに直結します。** BE-0110 が証跡をオブジェクトストレージへ移し、BE-0204 が Google Cloud Storage（GCS）対応を加えたのは、run の証跡（動画、スクリーンショット、ネットワークキャプチャ）が小さくないからです。残す run が増えるほど、課金対象のバイト数も増え続けます。それを減らすレバーが、製品側にありません。
3. **一覧画面は、run が増えるほど見づらくなります。** `GET /api/runs` と `/api/crawl/runs`（`bajutsu/serve/server/app.py`）は、flaky 判定（BE-0220）とメトリクス表示（`project_metrics_view`）のために、すでに履歴をページングして扱っています。1 年間、継続的インテグレーション（CI）を回したプロジェクトでは、数千件が溜まります。不要な run を間引く手段はなく、より多く表示する手段しかありません。
4. **削除は、このプロジェクトが慎重さを求める操作の典型です。しかし、その慎重さを受け止める場所が、今の serve にはありません。** ロールベースアクセス制御（RBAC）の階層（`bajutsu/serve/authz.py` の `viewer < editor < admin`）と、監査ログ（`state.repository.record_audit`）は、すでに存在します。監査ログは、OAuth ログインなど既存の変更操作で使われています。削除と完全削除の機能は、その自然な次の利用先であり、新しい仕組みを一から作る話ではありません。

この機能は、記録済みのデータに対するライフサイクル管理に徹します。run の実行方法にも、`assertions` や `network` が下す判定にも、[BE-0068](../BE-0068-regenerable-reports/BE-0068-regenerable-reports-ja.md) のレポートデータ契約にも触れません。削除、復元、完全削除のどの経路にも LLM は関与しません。したがって、Tier-2 の run/CI ゲートの完全に外側にあります（prime directive 1）。純粋に serve モード側の配線であり、構造上決定的です。

## 詳細設計

作業は 6 つの単位に分かれます。

- 3（ゴミ箱と保持期間）は、1（ストアの仕組み）に依存します。
- 5（Web UI）は、4（API）に依存します。
- 各バックエンドのストア実装（1）は、別の PR として進められます。
- CLI 対応は、この項目のスコープ外です。

1. **`ArtifactStore`（`bajutsu/serve/artifacts.py`）にソフトデリートの仕組みを追加します。**
   プロトコルに `soft_delete_run(run_id) -> bool`、`restore_run(run_id) -> bool`、`purge_run(run_id) -> bool` を追加します。`LocalArtifactStore` と `ObjectStorageArtifactStore`（`bajutsu/serve/server/artifacts.py`）は、既存の `get`・`list_runs`・`archive` と同様に、実装を分けます。データベースを持つのはホスト型バックエンドだけなので（ローカル・ループバック側では `ServeState.repository` が `None` のままです。`bajutsu/serve/state.py` を参照）、2 つのバックエンドは異なる仕組みでソフトデリートを実現するほかありません。
   - `LocalArtifactStore`：ソフトデリートは、`runs/<id>/` を `runs/.trash/<id>/` へ移動します。移動先は `runs_dir` に閉じたままなので、`_resolve`・`_confined` の既存のパス閉じ込め保証を変更せずに使えます。`list_runs`・`list_crawl_runs` は `.trash/` 配下を見ません。完全削除はゴミ箱内のディレクトリへの `shutil.rmtree`、復元は元の場所への移動です。
   - `ObjectStorageArtifactStore`：ソフトデリートは、墓標オブジェクト（`<run_id>/.deleted`）を書き込みます。`list_runs` がすでに読んでいる `manifest.json`・`screenmap.json` と並ぶキーです。`list_runs`・`list_crawl_runs` は、墓標キーが存在する `run_id` を読み飛ばします。完全削除は、`archive` がすでに収集しているのと同じキー集合を、その run のプレフィックス配下からまるごと削除します。復元は、墓標キーの削除です。
2. **DB 側の run 行にも、ソフトデリート用の列を持たせます（ホスト型のみ）。**
   - `Repository.list_runs`（`bajutsu/serve/server/db.py`）は、`deleted_at` が設定された行を除外して返します。ホスト型の一覧はデータベース主導であり、2 つの `ArtifactStore` が自身の一覧で行うファイルシステム／オブジェクトストレージのスキャンとは方式が異なります（`db.py:108` と `:297`）。
   - 削除は `deleted_at`・`deleted_by` を設定し、復元はそれらをクリアします。
   - 完全削除は行を削除します。監査履歴を run の実体データと切り離して残したい場合は、行を墓標行に変換する選択肢もあります（*検討した代替案* を参照してください）。
3. **保持期間と、完全削除の掃除処理を設けます。**
   - ソフトデリートされた run は、設定可能な保持期間を過ぎると、完全削除の対象になります。既定値の例は 30 日で、他のホスティング設定と並ぶ `serve` の設定項目で指定します。
   - `serve` には現状、定期実行するジョブランナーがありません。この項目でも、新しいデーモンは導入しません。完全削除は、次回の `list_runs` やログインの呼び出し時に日和見的に確認する**遅延掃除**として実装します。
   - 先例は、`bajutsu/serve/server/sessions.py` の `SqlSessionStore` が読み取り時に有効期限を確認する方式です。`valid` と `identity` が `expires_at` を現在時刻と比べます（docstring に「Expiry is enforced on read」とあります）。固定間隔のバックグラウンドスレッドは使いません。
4. **API 面。** 既存の `DELETE /api/projects/{name}`（`bajutsu/serve/server/app.py:418`）と、その stdlib ハンドラ側（`handler.py:426` の `do_DELETE`）に並べて追加します。
   - `DELETE /api/runs/{run_id}` と `DELETE /api/crawl/runs/{run_id}`：ソフトデリートです。どちらも、`/runs/{rel:path}` の読み取りがすでに使っている `state.for_org(state.org_of(_actor(request)))` と同じパターンで、org スコープを効かせます。別 org のプレフィックスにある run は、読み取りと同様に 404 になります。BE-0015 のマルチテナンシーは、削除にもそのまま及びます。
   - `POST /api/runs/{run_id}/restore`（crawl run 側にも同様のもの）：保持期間内であれば、ソフトデリートを取り消せます。
   - `DELETE /api/runs/{run_id}?purge=true`（admin 限定）：ゴミ箱の猶予期間を飛ばして、即座に完全削除します。
   - `POST /api/runs/bulk-delete`：id のリストを渡す一括削除です。「一括削除」の要望に応え、1 件ずつではなく、まとめて消せるようにします。
   - これらのルートはすべて、`POST`／`DELETE` にすでに適用されている無条件の CSRF Origin チェックと Host 許可リストを通ります（`app.py` の `request.method in ("POST", "DELETE")` のゲート、BE-0121）。削除操作は、隣にあるプロジェクト登録解除の `DELETE` と同じだけ、CSRF に敏感な操作だからです。
   - RBAC（`bajutsu/serve/authz.py` の `required_role`）では、ソフトデリートと復元を **editor** 権限のアクションとし、run の実行と同じ扱いにします。完全削除は **admin** 権限のアクションとします。プロジェクトの登録解除も、同様に取り消せない操作として admin 限定になっています。それと揃えた扱いです。
5. **Web UI。** Web UI（`bajutsu/templates/serve.*.js`）に、次の 4 つを加えます。
   - run 履歴と crawl 履歴の一覧の各行に置く、削除の導線。
   - 複数選択して一括削除するツールバー操作。
   - 何が起きるかを明記した確認ダイアログ。ゴミ箱へ移動し、N 日以内なら復元できることを示します。
   - admin 限定の「完全に削除」操作。ソフトデリートとは別に、より強い警告を伴う確認ダイアログを持ちます。

   「ゴミ箱」ビュー（または既存の履歴一覧のフィルタ切り替え）には、ソフトデリート済みの run を表示します。そこから、**復元**または**完全に削除**できます。
6. **監査と可観測性。**
   - ソフトデリート、復元、完全削除はすべて、他の変更操作ですでに使われている `record_audit`・`_record_audit` の経路を通します（`bajutsu/serve/authz.py:100`、`bajutsu/serve/server/db.py:148`）。誰が、どの run に対して、いつ行ったかを記録します。
   - あわせて、`oplog` の構造化イベントを出します（`bajutsu/serve/oplog.py` の `EVENTS`）。例は `run.soft_deleted`・`run.restored`・`run.purged` です。
   - これにより、サイト信頼性エンジニア（SRE）は、取り消せない完全削除を grep やアラートで追跡できます。BE-0055 が他の運用イベントで可能にしているのと同じです。

## 検討した代替案

- **ゴミ箱を設けず、常に即座に完全削除する案。**
  - コードパスが 1 本で済み、保持期間の設定も復元エンドポイントも不要になる分、シンプルです。
  - ただし、CI 履歴の何時間分もの証跡を表しうるデータに、取り消せない削除ボタンを置くことになります。それは、このプロジェクト自身のツールが従う「明示的な許可を要する」という方針が避けるものです。プロジェクト登録解除（BE-0225）の先例も同じ方向を指します。
  - そのため、既定はソフトデリートとし、完全削除は別途 admin 権限で明示的に切り分ける設計を採用しました。
- **両バックエンドを DB の墓標行だけで統一する案。** ファイルシステム側の `.trash/` への移動を省き、ローカル・ループバック側でも、ソフトデリートの状態を DB の行だけで管理する案です。却下しました。理由は 3 つあります。
  - ローカルの stdlib パスには、そもそもデータベースがありません（ホスト型以外では `ServeState.repository` が `None` です）。
  - ローカルの `serve` は、もともと DB を持たない軽量な動作モードとして設計されています（[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）。
  - run の削除を成立させるためだけにデータベースへの依存を持ち込むのは、この機能が求める規模に対して、変更範囲が大きすぎます。

  ファイルシステム上の `.trash/` ディレクトリなら、ローカルの `serve` を DB 不要のまま保てます。`LocalArtifactStore` と `ObjectStorageArtifactStore` で、`get`・`list_runs` の実装（契約ではなく実装）がすでに分かれているのと同じ考え方です。
- **時間・件数ベースの自動保持だけで済ませ、1 件ずつの削除ボタンは設けない案。**
  - 「run が際限なく積み上がる」という動機には応えられます。
  - しかし、ユーザーが求めた「今この特定の run を消したい」という要望には応えられません。
  - 両者は代替関係ではなく補完関係にあるので、どちらもスコープに含めます。単位 3 の遅延掃除は、人間がすでにソフトデリートした run だけを対象にします。まだ削除されていない run を、自動で選んで消すことはしません。
  - 削除されていない run を年齢や件数で自動的に間引く機能は、妥当な後続候補です。ただし、依頼された範囲を保つため、対象外とします。
- **完全削除の際に、ホスト型の DB の run 行を物理削除するか、墓標行に変換するか。**
  - 行をそのまま削除するのが最も単純で、行そのものも回収できます。ただし、監査証跡から参照で辿れる範囲から、その run が消えます。あとから「誰が run X をいつ削除したか」を照会しても、join する相手の行がありません。
  - 最小限の墓標行（id と `deleted_at`・`deleted_by` だけを残し、証跡の実体データは消す）を残せば、その履歴を保てます。代償は、行数が減らないことです。
  - 本提案は、ホスト型のデプロイでは墓標行を残す方に寄せます。そもそも DB を持つ目的が監査履歴にあるからです。監査の利用者がいない場合は、物理削除の方に寄せます。
  - これはここで固定する決定ではなく、実装時に詰める設定項目です。そのため単位 2 は、どちらか一方を強制せず、この論点を明示するにとどめています。
- **同じ項目の中で、CLI 側の対応コマンド（`bajutsu run rm <id>`）も用意する案。** 今回の依頼は「Web UI から」という具体的なものでした。CLI 側の削除コマンドは、この項目で導入する `ArtifactStore.purge_run` の仕組みをそのまま再利用できます。仕組みができたあとの、自然で低コストな後続作業になります。ただし、この項目のスコープは Web UI とその API に絞るため、ここには含めません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `LocalArtifactStore`（トラッシュディレクトリ）と `ObjectStorageArtifactStore`
      （墓標オブジェクト）への `ArtifactStore.soft_delete_run`・`restore_run`・`purge_run`
- [x] ホスト型 `Repository.list_runs` へのソフトデリート列と絞り込み（crawl run は両バックエンド
      とも artifact store 由来のため、絞り込む DB 一覧は別途ありません）
- [x] 保持期間の設定と遅延掃除処理
- [x] `DELETE`・復元・一括削除の API ルート、CSRF と RBAC（ソフトデリート／復元は editor、
      完全削除は admin）、org スコープ
- [x] Web UI：行ごとの削除、複数選択、確認ダイアログ、復元／完全削除できるゴミ箱ビュー
- [x] ソフトデリート・復元・完全削除の監査ログエントリと `oplog` イベント

**ログ**

- バックエンド（ユニット 1〜4・6）を実装しました。両バックエンドの `ArtifactStore` の
  ソフトデリート／復元／完全削除の仕組み（ファイルシステムの `.trash/` とオブジェクトストレージの
  `.deleted` 墓標）、それが必要とする `ObjectStore` の `delete_key`・`delete_keys`、ホスト型の
  `runs.deleted_at`・`deleted_by` 列（マイグレーション 0012）と一覧の絞り込み、
  `BAJUTSU_RUN_RETENTION_DAYS` の保持期間と履歴読み取り時の遅延掃除処理、両トランスポートの
  `DELETE`・復元・一括削除ルート（CSRF と editor の RBAC。完全削除の admin 判定は operation 内）、
  監査ログと `oplog`（`run.soft_deleted`・`run.restored`・`run.purged`）です。項目は
  **実装中** のままで、Web UI（ユニット 5）を後続 PR で実装し、その時点で **実装済み** に反転させます。
- Web UI（ユニット 5）を実装し、項目を **実装済み** に反転しました。Replay の run 履歴と Crawl の
  run 履歴の両方に、行ごとの削除と複数選択のツールバー（select all と Delete selected）を加え、
  ソフトデリートの確認ダイアログには保持期間を明記し、トップレベルの **Trash** ビューでソフトデリート
  済みの run を一覧して **Restore** と admin 限定の **Delete forever** を提供します。これを支える
  小さな読み取り口として、`GET /api/runs/trash`（org スコープで、先に期限切れのゴミ箱を掃除します）と、
  確認・Trash の表示に使う `/api/config` の `retentionDays` を加えました。削除 → ゴミ箱 → 復元 →
  一括削除 → 完全削除の一連を Chromium のスモークテストで確認し、`docs/web-ui.md`（と ja のミラー）に
  この画面を記載しています。

## 参考

- [BE-0068 — Regenerable reports](../BE-0068-regenerable-reports/BE-0068-regenerable-reports-ja.md)
  ：この項目が拡張する `ArtifactStore` の仕組みです。
- [BE-0225 — serve の config プロジェクトハブ](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)
  ：既存の唯一の `DELETE` ルートであり、CSRF・RBAC の先例です。
- [BE-0190 — Org-scoped crawl history](../BE-0190-org-scoped-crawl-history/BE-0190-org-scoped-crawl-history-ja.md)
  ：この項目の crawl run 削除が踏襲する一覧の仕組みです。
- [BE-0110 — Evidence store URI](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri-ja.md) と
  [BE-0204 — GCS support](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support-ja.md)
  ：無制限な run の増加がホスト側ストレージの実コストに直結する理由です。
- [BE-0055 — Operational logging](../BE-0055-operational-logging/BE-0055-operational-logging-ja.md)
  ：この項目の監査イベントが踏襲する `oplog` イベントの慣例です。
