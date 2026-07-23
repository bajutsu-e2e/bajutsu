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

`serve` の Web UI から、run（とそのレポート）を一件ずつ削除できるようにします。対象はローカル実行
（stdlib ハンドラ、ファイルシステム保存）とホスト型 serve（FastAPI、DB とオブジェクトストレージ、
BE-0015 系）の両方です。削除は即座に完全消去するのではなく、ソフトデリート／ゴミ箱の猶予期間を挟み、
本当に消し去る完全削除（purge）には監査ログを残します。crawl run（BE-0190）にも同じ個別削除を提供します。

現状の `serve` には、run を削除する手段が一切ありません。API 上の唯一の `DELETE` エンドポイントは
プロジェクトの登録解除（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）
だけであり、`runs/<id>/` 配下のツリー（`report.html`、`manifest.json`、スクリーンショット、動画、
ネットワークキャプチャ）を消すには、ホストにシェルで入って `rm -rf` するしかありません。ホスト型の
serve では run がオブジェクトストレージと DB の行（`bajutsu/serve/server/db.py`）に保存されており、
ユーザーが触れられるファイルシステムはそもそも存在しないため、この手段は使えません。

## 動機

run は放っておくと際限なく積み上がります。`serve` には `runs/` ツリーやホスト側オブジェクトストレージの
org プレフィックスを縮める仕組みが今のところ何もありません。デモ用途の小さなプロジェクトであれば
問題になりませんが、この仕組みの欠如は次の点で実害につながります。

1つ目に、**問題のある run に対して何もできません**。誤ったターゲットに対して記録してしまった run、
1回きりの flaky な run、あるいは記録後になって不適切な内容を含んでいたと分かった証跡（画面録画や、
BE-0151・BE-0152 が扱うような漏えいに近い事例）があっても、ファイルシステムやバケットに直接触れる
運用担当者以外に削除する手段がありません。その run が不要だと一番よく分かっている viewer や editor が、
Web UI 上では何も行動できないのです。

2つ目に、**ホスト型 serve では無制限の増加がそのままコストに直結します**。BE-0110 で証跡を
オブジェクトストレージへ移したのも、BE-0204 で GCS 対応を加えたのも、run の証跡（動画・
スクリーンショット・ネットワークキャプチャ）が小さくないからです。それにもかかわらず、残しておく
run が増えるほど課金対象のバイト数も際限なく増え、それを減らすレバーが製品側に何一つありません。

3つ目に、**一覧画面は run が増えるほど見づらくなる一方です**。`GET /api/runs` と
`/api/crawl/runs`（`bajutsu/serve/server/app.py`）は、flaky 判定（BE-0220）やメトリクス表示
（`project_metrics_view`）のためにすでに履歴をページングして扱っていますが、1年間 CI を回した
プロジェクトでは数千件が溜まり、不要な run を間引く手段はなく、より多く表示する手段しかありません。

4つ目に、**削除という操作は、このプロジェクトが慎重さを求める操作の典型でありながら、その慎重さを
受け止める場所が今の serve にはありません**。RBAC のはしご（`bajutsu/serve/authz.py` の
`viewer < editor < admin`）と監査ログ（`state.repository.record_audit`。OAuth ログインなど
既存の変更操作ですでに使われています）はすでに存在しており、削除・完全削除機能はその自然な次の
利用先であって、新たな仕組みを一から作る話ではありません。

これはあくまで、すでに記録済みのデータに対するライフサイクル管理の機能です。run の実行方法や、
`assertions`・`network` が下す判定、[BE-0068](../BE-0068-regenerable-reports/BE-0068-regenerable-reports-ja.md)
のレポートデータ契約には一切触れません。削除・復元・完全削除のどの経路にも LLM は関与しないため、
Tier-2 の run/CI ゲートの外側に完全に位置します（prime directive 1）。純粋に serve モード側の配線
であり、構造上決定的です。

## 詳細設計

作業は6つの単位に分かれます。3（ゴミ箱と保持期間）は1（ストアの仕組み）に依存し、5（Web UI）は
4（API）に依存しますが、各バックエンドのストア実装（1）とこの項目のスコープ外にある CLI 対応は、
それぞれ別の PR として進められます。

1. **`ArtifactStore`（`bajutsu/serve/artifacts.py`）にソフトデリートの仕組みを追加します。**
   プロトコルに `soft_delete_run(run_id) -> bool`、`restore_run(run_id) -> bool`、
   `purge_run(run_id) -> bool` を追加します。`LocalArtifactStore` と
   `ObjectStorageArtifactStore`（`bajutsu/serve/server/artifacts.py`）は、既存の `get`・
   `list_runs`・`archive` と同様に実装を分けます。ホスト型バックエンドだけがデータベースを
   持つため（ローカル・ループバック側では `ServeState.repository` が `None` のままです。
   `bajutsu/serve/state.py` を参照）、この2つは異なる仕組みでソフトデリートを実現するほかありません。
   - `LocalArtifactStore`：ソフトデリートは `runs/<id>/` を `runs/.trash/<id>/` へ移動するだけ
     です。`runs_dir` に閉じたままなので、`_resolve`・`_confined` の既存のパス閉じ込め保証を
     変更せずに使えます。`list_runs`・`list_crawl_runs` は `.trash/` 配下を単に見ません。
     完全削除はトラッシュ済みディレクトリへの `shutil.rmtree`、復元は元の場所への移動です。
   - `ObjectStorageArtifactStore`：ソフトデリートは墓標オブジェクト（`<run_id>/.deleted`）を
     書き込みます（`list_runs` がすでに読んでいる `manifest.json`・`screenmap.json` と並ぶ
     キーです）。`list_runs`・`list_crawl_runs` は墓標キーが存在する `run_id` を読み飛ばします。
     完全削除は `archive` がすでに収集しているのと同じキー集合を、その run のプレフィックス配下
     まるごと削除します。復元は墓標キーの削除です。
2. **DB 側の run 行にもソフトデリート用の列を持たせます（ホスト型のみ）。**
   `Repository.list_runs`（`bajutsu/serve/server/db.py`）は `deleted_at` が設定された行を
   除外して返します。ホスト型の一覧は DB 主導であり、2つの `ArtifactStore` 自身が行っている
   ファイルシステム／オブジェクトストレージのスキャンとは方式が異なります（`db.py:108` と
   `:297`）。削除は `deleted_at`・`deleted_by` を設定し、復元はそれをクリアし、完全削除は
   行そのものを削除します（監査履歴を run の実体データと切り離して残したい場合は、行を
   墓標行に変換する選択肢もあります。詳しくは *検討した代替案* を参照してください）。
3. **保持期間と完全削除の掃除処理を設けます。** ソフトデリートされた run は、設定可能な
   保持期間（既定値の例として30日）を過ぎると完全削除の対象になります。`serve` には現状、
   定期実行するジョブランナーが存在せず、この項目でも新しいデーモンは導入しません。完全削除は
   次回の `list_runs`・ログイン呼び出しの際に日和見的にチェックする**遅延掃除**として実装します。
   これは `bajutsu/serve/server/sessions.py` の `SqlSessionStore` が読み取り時に有効期限を
   チェックする方式（`valid`・`identity` が `expires_at` を現在時刻と比較する。docstring に
   「Expiry is enforced on read」とあります）と同じ考え方であり、固定間隔のバックグラウンド
   スレッドではありません。
4. **API 面。** 既存の `DELETE /api/projects/{name}`（`bajutsu/serve/server/app.py:418`）と
   その stdlib ハンドラ側（`handler.py:426` の `do_DELETE`）に並ぶ形で追加します。
   - `DELETE /api/runs/{run_id}` と `DELETE /api/crawl/runs/{run_id}`：ソフトデリートです。
     `/runs/{rel:path}` の読み取りがすでに使っている `state.for_org(state.org_of(_actor(request)))`
     と同じパターンで org スコープを効かせるため、別 org のプレフィックスにある run は読み取り
     と同様に404になります（BE-0015 のマルチテナンシーは削除にもそのまま及びます）。
   - `POST /api/runs/{run_id}/restore`（crawl run 側にも同様のもの）：保持期間内であれば
     取り消せます。
   - `DELETE /api/runs/{run_id}?purge=true`（admin 限定）：ゴミ箱の猶予期間を飛ばして即座に
     完全削除します。
   - 一括削除の形として `POST /api/runs/bulk-delete` に id のリストを渡す方式も用意し、
     「一括削除もしたい」という要望に応えます。1件ずつではなく、まとめて消せるようにします。
   - これらのルートはすべて、`POST`／`DELETE` にすでに適用されている無条件の CSRF Origin
     チェックと Host 許可リスト（`app.py` の `request.method in ("POST", "DELETE")` のゲート、
     BE-0121）を通ります。削除操作は、隣にあるプロジェクト登録解除の `DELETE` とまったく
     同じだけ CSRF に敏感な操作だからです。
   - RBAC（`bajutsu/serve/authz.py` の `required_role`）は、ソフトデリートと復元を
     **editor** 権限のアクション（run の実行と同じ扱い）とし、完全削除は **admin** 権限の
     アクションとします。これは、プロジェクトの登録解除が同様に取り消せない操作として
     admin 限定になっているのと揃えた扱いです。
5. **Web UI。** run 履歴と crawl 履歴の一覧（`bajutsu/templates/serve.*.js`）の各行に削除の
   導線を置き、複数選択して一括削除するツールバー操作も加えます。確認ダイアログには、何が
   起きるか（ゴミ箱へ移動し、N日以内なら復元できること）を明記し、admin 限定の「完全に削除」
   操作はこれとは別に、より強い警告を伴う確認ダイアログにします。「ゴミ箱」ビュー（または
   既存の履歴一覧のフィルタ切り替え）から、ソフトデリート済みの run を**復元**または
   **完全に削除**できるようにします。
6. **監査と可観測性。** ソフトデリート・復元・完全削除はすべて、他の変更操作ですでに使われている
   `record_audit`・`_record_audit` の経路（`bajutsu/serve/authz.py:100`、
   `bajutsu/serve/server/db.py:148`）を通します。誰が、どの run に対して、いつ行ったかを
   記録し、あわせて `oplog` の構造化イベント（`bajutsu/serve/oplog.py` の `EVENTS`。
   例えば `run.soft_deleted`・`run.restored`・`run.purged`）を出します。BE-0055 で
   他の運用イベントを grep・アラートできるのと同じように、取り消せない完全削除も追跡できる
   ようにするためです。

## 検討した代替案

- **ゴミ箱を設けず、常に即座に完全削除する案。** コードパスが一本で済み、保持期間の設定も
  復元エンドポイントも不要になる分シンプルです。ただし、CI 履歴の何時間分もの証跡を表しうる
  データに対して、取り消せない削除ボタンを置くのは、このプロジェクト自身のツールが従っている
  「取り消しにくい操作は明示的な許可を要る」という方針や、プロジェクト登録解除（BE-0225）の
  先例が避けようとしていることそのものです。そのため、既定はソフトデリートとし、完全削除は
  別途 admin 権限で明示的に切り分ける設計を採用しました。
- **両バックエンドを DB の墓標行だけで統一する案。**（ファイルシステム側の `.trash/` への
  移動を省き、ローカル・ループバック側でもソフトデリートの状態を DB の行だけで管理する案。）
  ローカルの stdlib パスにはそもそもデータベースが存在せず（ホスト型以外では
  `ServeState.repository` が `None` です）、ローカルの `serve` はもともと DB を持たない
  軽量な動作モードとして設計されています（[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）。
  run の削除機能を成立させるためだけにデータベースへの依存を持ち込むのは、この機能が求める
  規模に対して変更範囲が大きすぎます。ファイルシステム上の `.trash/` ディレクトリであれば、
  ローカルの `serve` を DB 不要のまま保てます。これは `LocalArtifactStore` と
  `ObjectStorageArtifactStore` で `get`・`list_runs` の実装（契約ではなく実装）がすでに
  分かれているのと同じ考え方です。
- **時間・件数ベースの自動保持だけで済ませ、1件ずつの削除ボタンは設けない案。** 「run が
  際限なく積み上がる」という動機には応えられますが、ユーザーが求めている「今この特定の run を
  消したい」という要望には応えられません。両者は代替関係ではなく補完関係にあるため、どちらも
  この項目のスコープに含めます（単位3の遅延掃除は、あくまで人間がすでにソフトデリートした
  run を対象に動くものであり、まだ削除されていない run を自動で選んで消すような、完全に
  自動的な年齢・件数ベースの間引きはこの項目のスコープには含めません。これは妥当な後続候補
  ですが、今回依頼された範囲を保つため対象外とします）。
- **完全削除の際に、ホスト型の DB の run 行を物理削除するか、墓標行に変換するか。** 行を
  そのまま削除するのが最も単純で、行そのものも回収できますが、監査証跡から参照で辿れる範囲から
  その run が消えてしまいます。あとから「誰が run X を、いつ削除したか」を照会しようにも、
  join する相手の行がありません。最小限の墓標行（id と `deleted_at`・`deleted_by` だけを残し、
  証跡の実体データは消す）を残せば、行数が減らないという代償はあるものの、その履歴を保てます。
  本提案は、ホスト型のデプロイ（そもそも DB を持つ目的が監査履歴にある）では墓標行を残す方に、
  監査の利用者がいない場合は物理削除の方に寄せる立場を取りますが、これはここで固定する決定では
  なく、実装時に詰めるべきつまみです。だからこそ単位2は、どちらか一方を強制するのではなく
  この論点を明示するにとどめています。
- **同じ項目の中で CLI 側の対応コマンド（`bajutsu run rm <id>`）も用意する案。** 今回の依頼は
  「Web UI から」という具体的なものでした。CLI 側の削除コマンドは、この項目で導入する
  `ArtifactStore.purge_run` の仕組みをそのまま再利用できるため、この仕組みができたあとの
  自然で低コストな後続作業にはなりますが、この項目のスコープは Web UI とその API に絞るため
  ここには含めません。

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
