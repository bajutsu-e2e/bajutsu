[English](BE-0160-worker-credential-free-uploads.md) · **日本語**

# BE-0160 — presigned URL による Worker の認証情報レスなアップロード

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0160](BE-0160-worker-credential-free-uploads-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0160") |
| 実装 PR | [#655](https://github.com/bajutsu-e2e/bajutsu/pull/655) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri-ja.md), [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu worker` がクラウドオブジェクトストレージの認証情報を**一切持たない**ようにします。現在、ホスティング構成（`serve --backend=server` + `bajutsu worker`）の Worker は、自分の認証情報でオブジェクトストレージを直接読み書きしています。用途は次の 3 つです。

- 各 run のアーティファクトツリーのアップロード
- run 前の org のビジュアルベースラインのダウンロード
- `record` 後の生成シナリオの保存

この提案は、3 つすべてを [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri-ja.md) が証跡アップロード向けに導入した **presigned URL の仲介**へ移します。**コントロールプレーンが認証情報を保持**し、短命の presigned GET/PUT URL とベースラインのキー一覧を発行します。Worker は平文 HTTP でアップロードとダウンロードを行うので、必要なのは HTTP クライアントだけです。クラウド SDK も認証情報も要りません。

## 動機

BE-0110 は「Worker はクラウド認証情報を必要としない」というセキュリティ上の利点を確立しました。ただし対象は、新しい**証跡**の保存先だけでした。既存のアーティファクト、ベースライン、シナリオの面では、Worker はいまも `object_store_from_env()`（`BAJUTSU_S3_BUCKET` や `AWS_ACCESS_KEY_ID` など）からオブジェクトストレージのクライアントを組み立てています。エフェメラルな Mac の Worker は、いまだにオブジェクトストレージのシークレットを抱えています。

ここが本来より大きな露出です。Worker は台数が多く、使い捨てで、GUI セッションを持つマシンです。そこに長命のバケット認証情報を配布することこそ、BE-0110 の presigned 設計が避けようとしたものです。

具体的には、`bajutsu/serve/server/worker_job.py` と `bajutsu/cli/commands/worker.py` が、Worker 側のストアを次の 3 つに使っています。

- **アーティファクトアップロード**（`_upload_runs`）：完了した `runs/<id>/` ツリーをアーティファクトプレフィックスの下に書き込みます。コントロールプレーンの `ObjectStorageArtifactStore` がレポートを配信できるようにするためです。
- **ベースラインダウンロード**（`_materialize_baselines`）：run 前に org のビジュアルベースラインをワークスペースへ読み込みます（書き込みのない READ と LIST）。
- **生成シナリオの保存**（`_save_authored`）：`record` ジョブの出力を、プロジェクトごとのシナリオプレフィックスの下に書き込みます。

3 つすべてから Worker の認証情報を取り除けば、認証情報レスな Worker という目標が完成します。Worker はクラウド SDK（`boto3`）をランタイムで一切必要とせず、HTTP クライアントだけで動きます。オブジェクトストレージの認証情報は、コントロールプレーンの一か所にだけ置けます。コントロールプレーンは、より厳しい権限のバケットや別アカウントのバケットを、シークレットをフリートへ再配布せずに使えます。

## 詳細設計

コントロールプレーンは既に認証情報を保持し、org ごとに `ObjectStore` の継ぎ目を持っています（`ServeState.org_stores` / `for_org`）。Worker とストアのやり取りは、いずれも同じ形に変わります。Worker がコントロールプレーンに署名付き URL を求め、そのうえで平文 HTTP でオブジェクトストレージと通信します。

キーの組み立てと検証は、BE-0110 の `generate_upload_urls` と同じく、すべてサーバー側に残ります。Worker が自分の org のプレフィックスの外へ出ることはできません。

### 1. presigned PUT によるアーティファクトアップロード

run の後、Worker は `runs/<id>/**` を列挙します。それらのキーの presigned PUT URL を **org のアーティファクトプレフィックス**（`artifact_prefix(org_prefix(prefix, org))`）の下で要求し、各ファイルを PUT します。BE-0110 の Worker アップローダーが証跡で既に使っている形と同じです。

この設計は BE-0110 の `POST /api/runs/<run_id>/upload-urls` を一般化します。エンドポイントに「どの保存先に署名するか」（アーティファクトか証跡か）という区別を持たせるか、兄弟エンドポイントがアーティファクトプレフィックスに署名します。org はリクエストの認証やジョブからサーバーが導出し、Worker から受け取ることはありません。

### 2. presigned GET によるベースラインダウンロード

コントロールプレーンは lease の時点で run の org を知っています。その org のベースラインを一覧し（認証情報を持つ LIST が可能）、`{name: presigned GET URL}` を返します。返し方は、lease 応答（`/api/worker/lease`）への同梱か、小さな `GET /api/runs/<job>/baseline-urls` です。

Worker は run 前に、各ベースラインを平文 HTTP でワークスペースへダウンロードします。`_materialize_baselines` が `ObjectBaselineStore` を直接読んでいた処理を置き換えます。GET の署名を行う `presigned_url` は、既に `ObjectStore` プロトコルにあります。

### 3. presigned PUT による生成シナリオの保存

`record` ジョブでは、Worker が生成シナリオのキー（org のシナリオプレフィックスの下）の presigned PUT URL を 1 本要求し、ファイルを PUT します。`_save_authored` が `ObjectScenarioStorage.save` を直接呼んでいた処理を置き換えます。

### 4. Worker のクラウドクライアントを外す

1〜3 が入れば、Worker のパス（`worker.py`、`worker_job.py`）から `_object_store()` と `object_store_from_env()` を取り除きます。Worker はクラウドクライアントを組み立てず、`BAJUTSU_S3_*` や AWS の認証情報も読みません。

Worker はランタイムでクラウド SDK（`boto3`）を必要としなくなります。`worker` extra は現状すでに空なので、この変更は実態をそれに合わせるだけです。ネットワーク依存は、既存の HTTP クライアントだけになります。**コントロールプレーン**は今までどおり `server` extra（boto3 / GCS）を保ちます。

### 5. エンドポイントとキー組み立ての一般化

BE-0110 のサーバー側キービルダーと検証（`generate_upload_urls`）を、複数の保存先（証跡、アーティファクト、シナリオ）に対応するよう切り出します。重複を避けるためです。各保存先はサーバー側で自分のベースプレフィックスを固定し、Worker は相対キーだけを渡します。サーバーが相対キーを再検証する（`valid_relative_key`）ので、org とプレフィックスの境界は保たれます。ベースラインの presigned GET も、返す名前に同じ検証を再利用します。

### 6. 認証と org の解決

Worker は既に送っている操作トークン（`Authorization: Bearer`）でコントロールプレーンに認証します。コントロールプレーンは、org を leased job から解決します。Worker が渡す値は使わないので、マルチテナントの分離は変わりません。クラウド認証情報がコントロールプレーンの外へ出ることはありません。

## 検討した代替案

### A. 短命のスコープ付き認証情報（STS / GCS の token downscoping）

コントロールプレーンが、プレフィックスにスコープした短命認証情報を発行し、Worker が SDK でそれを使います。発行の手段は、AWS STS の `AssumeRole` + session policy か、GCS の credential downscoping です。

LIST/GET/PUT を一様に扱えるクラウドネイティブな最小権限の答えですが、次の欠点があります。

- 設定が重くなります（IAM ロールと STS の設定が要ります）。
- Worker に SDK 依存が残ります。
- 短命とはいえ、**認証情報**が Worker に置かれます。この項目が目指すゼロ認証情報の終着点ではありません。

presigned URL は、コントロールプレーンが既に持つ認証情報のほかに IAM の配線を必要としません。

### B. バイト列をコントロールプレーン経由で流す

Worker がアーティファクトのバイト列をコントロールプレーンに POST し、コントロールプレーンがストレージへ書き込みます。Worker は簡単に認証情報レスになります。ただしコントロールプレーンの帯域と負荷が倍になり、直接アップロードの利点を捨てます。各 run の動画やスクリーンショットが、すべてコントロールプレーンを通ることになるからです。

BE-0110 も同じ理由で、類似の「直接書き込みシンク」を退けました。署名付き URL を仲介すれば、バイト列は Worker からストレージへ直接流れます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] presigned のキービルダーと検証を、複数の保存先（証跡 / アーティファクト / シナリオ）に対応するようサーバー側で一般化する
- [x] presigned PUT によるアーティファクトアップロード（Worker が org アーティファクトプレフィックスの下で URL を要求し PUT）
- [x] presigned GET によるベースラインダウンロード（コントロールプレーンが一覧し署名、Worker が run 前に GET）
- [x] presigned PUT による生成シナリオの保存（`record` ジョブ）
- [x] Worker から `object_store_from_env()` / `_object_store()` を取り除き、ランタイムでクラウド SDK（`boto3`）を不要にする（`worker` extra は依存なしのまま）
- [x] テスト — アーティファクト / ベースライン / シナリオの presigned パスを実 HTTP サーバーに対して（Worker の認証情報なしで）検証、org 境界の再検証
- [x] ドキュメント — `docs/self-hosting.md`（Worker は `BAJUTSU_S3_*` や AWS 認証情報を必要としない）とその日本語版を更新

### ログ

- 一つの変更で出荷しました。
  - presigned PUT の署名処理を `operations/presign.py` に切り出しました（証跡とアーティファクトで共有）。
  - `worker_artifact_urls` / `worker_scenario_url` の各操作とルートを追加し、org はリースしたジョブから解決するようにしました。
  - ベースラインの GET URL を `/api/worker/lease` に同梱しました。
  - `execute_job_spec` のオブジェクトストアクライアントを、注入する `WorkerIO` シームと `bajutsu worker` 側の presigned URL 実装 `PresignedWorkerIO` に置き換えました。Worker 経路から `object_store_from_env()` と `_object_store()` も取り除きました。
  - `docs/self-hosting.md` とその日本語版も更新しました。

## 参考

- [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri-ja.md)：presigned URL による証跡アップロード（本項目が一般化するパターン、そして始めた認証情報レス Worker の目標）
- [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md)：完了後 Worker モデル（本項目が拡張する Worker とコントロールプレーンの HTTP ループ）
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)：公開ホスティング（マルチテナントのサーバー構成と org ごとのオブジェクトストアプレフィックス）
- `bajutsu/serve/server/worker_job.py`：`_upload_runs` / `_materialize_baselines` / `_save_authored`（本項目が取り除く、認証情報を使う 3 つの Worker↔ストアのパス）
- `bajutsu/cli/commands/worker.py`：`_object_store()`（落とす Worker のクラウドクライアント）と BE-0110 が追加した presigned アップローダー
- `bajutsu/object_store.py`：`presigned_url`（GET）と `presigned_put_url`（PUT）を持つ `ObjectStore` プロトコル
