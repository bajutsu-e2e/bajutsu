[English](BE-0204-server-storage-gcs-support.md) · **日本語**

# BE-0204 — GCS support for server-side object storage

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0204](BE-0204-server-storage-gcs-support-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0204") |
| 実装 PR | [#838](https://github.com/bajutsu-e2e/bajutsu/pull/838) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)、[BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu serve --backend=server` の `ArtifactStore`（実行成果物の読み取り）、`ScenarioStore`
（シナリオの保存）、visual baseline（画面比較の基準画像）ストアを支える**サーバ側**のオブジェクトストレージの
seam（`bajutsu/serve/server/object_store.py`）に、GCS（Google Cloud Storage）バックエンドを追加します。
現状この seam は `S3ObjectStore` しか構築しません。[BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri-ja.md)
はこれとは別の evidence アップロード用の経路に、バックエンドを問わない `ObjectStore` プロトコルと
`GCSObjectStore`、単一 URI（`object_store_from_uri`、`s3://…` / `gs://…`）で選択するファクトリをすでに実装済みです。
本提案はその同じ実装をサーバストレージ側でも再利用し、Google Cloud 上でセルフホストする場合に
S3 互換バケットを別途用意しなくても済むようにします。

## 動機

[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)（Bajutsu 自身のホスティングサービス）は
Cloudflare R2 を、[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)（セルフホスティング）は
MinIO を、それぞれサーバ側の成果物ストレージとして選定し、いずれも GCS を代替案として明示的に却下していました。
これは Bajutsu 自身が推奨する構成としては妥当な既定値でしたが、実装側ではその選択がそのまま固定化されています。
`object_store_from_env()`（`bajutsu/serve/server/object_store.py`）は `S3ObjectStore` しか構築せず、
`_build_server_state`（`bajutsu/serve/__init__.py:268-270`）は `BAJUTSU_S3_BUCKET` が設定されていなければ
`ValueError` を送出します。すでに他の構成要素（GKE 上の control plane、Cloud SQL など）を Google Cloud 上で
運用しているセルフホスト運用者には、サーバ自身のストレージを GCS に向ける手段がありません。ストレージを
読み書きする各 seam は、S3 に依存する実装を持っているわけではないにもかかわらずです。

BE-0110 は evidence アップロードという別の seam で、まったく同じ課題をすでに解決しています。`run` と
`serve` が一つの seam を共有できるよう `ObjectStore` をバックエンドを問わないトップレベルのプロトコルへ
格上げし、`StoreURI` パーサーと、単一の URI から `S3ObjectStore` と検証済みの `GCSObjectStore` のどちらも
構築できる `object_store_from_uri` ファクトリを追加し、それらを `--evidence-store` /
`BAJUTSU_EVIDENCE_STORE` に配線しました。サーバ側の利用箇所である `ObjectStorageArtifactStore`
（`bajutsu/serve/server/artifacts.py`）、`ObjectScenarioStorage`（`bajutsu/serve/server/scenarios.py`）、
`ObjectBaselineStore`（`bajutsu/serve/server/baselines.py`）は、すでに `S3ObjectStore` ではなくこの
`ObjectStore` プロトコルに依存しています。それでも `S3ObjectStore` しか渡されてこないのは、サーバ側の
ファクトリが BE-0110 の GCS 対応より前から存在し、その後も取り込まれてこなかったためです。

その結果、一つのデプロイの中に不整合が生じています。evidence はすでに `gs://…` へアップロードできるのに、
同じサーバが実行成果物、シナリオ、visual baseline については S3 互換バケットに縛られたままです。この差を
埋めれば、GCS を使うセルフホスト運用者に対して不要な「もう一つのクラウドベンダーを用意する」という制約を
取り除けます。しかも実装は BE-0110 ですでに存在し、テストも済んでおり、`gcs` extra として配布済みのコードを
再利用するだけです。

## 詳細設計

### 1. サーバストレージ用の URI ベースの設定

サーバストレージも `--evidence-store` と同じ形式、つまりバックエンド、バケット、プレフィックスを 1 本の
URI（`gs://bucket/prefix` や `s3://bucket/prefix`）で表す設定として構成します。ただし独立した設定項目とします
（環境変数名は `BAJUTSU_SERVER_STORE` のような形を想定していますが、正式名称は実装時に決めます）。これは
`BAJUTSU_EVIDENCE_STORE` とはあえて別の設定にする、という意味です。両者を独立させたまま、どちらも
`s3://` と `gs://` の両方を受け付けるようにします（別設定のままにする理由は「検討した代替案」を参照してください）。

### 2. サーバ側ファクトリを既存の URI 実装の上に再構築する

`bajutsu/serve/server/object_store.py` の `object_store_from_env()` を、`--evidence-store` がすでに使っている
`bajutsu.object_store.parse_store_uri` / `object_store_from_uri` の上に再構築します。`BAJUTSU_S3_BUCKET` /
`BAJUTSU_S3_ENDPOINT` / `BAJUTSU_S3_REGION` から `boto3` クライアントを手組みする現在の実装は廃止します。
このファクトリは URI のスキームに応じて `S3ObjectStore` と `GCSObjectStore` のいずれかを返し、対応する SDK が
未インストールの場合は `object_store_from_uri` が evidence ストレージ側ですでに送出しているのと同じ
「不足している extra をインストールしてください」というエラーを送出します。

### 3. ファクトリより下流の変更は不要

利用側はすでに `S3ObjectStore` ではなく `ObjectStore` プロトコルに依存しています。`ObjectStorageArtifactStore`、
`ObjectScenarioStorage`、`ObjectBaselineStore` が呼び出すのは `exists` / `get_bytes` / `put_bytes` /
`presigned_url` / `list_keys` のみで、いずれもバックエンドに依存しません。キーのプレフィックスを組み立てる
`artifact_prefix`、`scenario_prefix`、`baseline_prefix`、`org_prefix` も、ストレージへの依存を持たない
単なる文字列処理です。`object_store_from_env()` 内のファクトリを差し替えることが変更のすべてであり、
それ以外のコードはどちらのバックエンドが使われているかを意識する必要がありません。

### 4. 配線とエラーメッセージ

`_build_server_state`（`bajutsu/serve/__init__.py`）は、S3 専用のエラーメッセージ
（`"BAJUTSU_S3_BUCKET is required for --backend=server"`）を、新しい URI ベースの設定名を示すメッセージに
置き換えます。`s3_prefix()` は、パース済みの `StoreURI`（末尾の `/` はすでに正規化済み）からプレフィックスを
読み取る形に置き換わるため、`BAJUTSU_S3_PREFIX` という独立した環境変数は不要になります。

### 5. ドキュメント

`docs/self-hosting.md`（および日本語版）に、既存の S3 / R2 / MinIO の例と並べてサーバストレージ用の GCS の例を
追加します。BE-0110 が `--evidence-store` 向けに GCS の選択肢を文書化したのと同じ形にします。

## 検討した代替案

### A. `BAJUTSU_EVIDENCE_STORE` が未設定のときにサーバストレージのデフォルトとして使う

却下します。evidence の保持期間は実行パスごとに変わることを前提としており、フィーチャーブランチの evidence を
クラウドのライフサイクルルールでプレフィックスごと自動削除できるようにするのが BE-0110 のねらいそのものです。
一方でサーバストレージ（シナリオ、visual baseline、レポート閲覧画面でユーザーが参照する成果物）は長期保存が前提で、
組織単位のスコープを持ちます。片方の設定をもう片方の既定値にしてしまうと、evidence の保持ポリシーが、
本来は失効させるつもりのないデータにも意図せず適用されかねません。同じバケットを両方に使いたい運用者は、
両方の設定に同じ URI を明示的に渡せばすでに実現できます。

### B. `BAJUTSU_GCS_BUCKET` 系の環境変数をもう一系統追加する

却下します。この方式では `BAJUTSU_S3_*`（バケット、エンドポイント、リージョン、プレフィックス）の並びを
もう一つのバックエンド向けに複製することになり、BE-0110 が evidence ストレージですでに実証した単一 URI の
形式を再利用できません。URI に統一すれば、二つのストレージ設定の見た目と運用も揃います。

### C. サーバストレージは S3 専用のままとし、GCS 対応をスコープ外とする

却下します。`ObjectStore` を利用する各 seam は、実際には S3 に依存していません。BE-0110 がすでに
バックエンドを問わない抽象化を実現し、テスト済みの `GCSObjectStore` を実装済みだからです。S3 に固定されているのは
`object_store_from_env` という小さなファクトリだけであり、ここに GCS 対応を追加するコストは、Google Cloud
上でセルフホストする運用者にとっての実際の制約を取り除く効果に比べて小さいといえます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] サーバストレージ用の環境変数 / URI の正式名称を決める（`BAJUTSU_S3_BUCKET` /
      `BAJUTSU_S3_ENDPOINT` / `BAJUTSU_S3_REGION` / `BAJUTSU_S3_PREFIX` を置き換えるか併存させるか）
- [x] `object_store_from_env()` を `parse_store_uri` / `object_store_from_uri` の上に再構築する
      （`bajutsu/serve/server/object_store.py`）
- [x] `_build_server_state` の配線とエラーメッセージを更新する（`bajutsu/serve/__init__.py`）
- [x] テスト：`gs://` URI からのサーババックエンド構築（フェイクの `GCSObjectStore`）、extra 不足時の
      エラーメッセージ、既存の S3 経路が引き続き通ること
- [x] ドキュメント：`docs/self-hosting.md` と日本語版にサーバストレージ用の GCS の例を追加する

[#838](https://github.com/bajutsu-e2e/bajutsu/pull/838) — `BAJUTSU_SERVER_STORE`（`s3://bucket/prefix`
または `gs://bucket/prefix`）が `BAJUTSU_S3_BUCKET` / `BAJUTSU_S3_PREFIX` を置き換えます。
`BAJUTSU_S3_ENDPOINT` / `BAJUTSU_S3_REGION` は変更せず、引き続き
`object_store_from_uri` が S3 互換クライアントの構築時に読み込みます。`bajutsu.object_store` に中立な
名前の `store_target_from_uri`（`(ObjectStore, prefix)` を返す）を新設し、`evidence_target_from_uri`
（`--evidence-store` 用）とサーバー側の `object_store_from_env()` の両方がこれを利用する形にしました。
レビューで指摘されたとおり、evidence 専用の `EvidenceTarget` という名前を無関係な設定にまで被せることなく、
URI のパース処理だけを共有する構成です。下流の利用側（`ObjectStorageArtifactStore`、
`ObjectScenarioStorage`、`ObjectBaselineStore`）は提案どおり変更不要でした。
`deploy/self-host/.env.example` と `docker-compose.yml` の `minio-init`（URI からのバケット名の取り出しに
スキームとバケット名の検証を追加）も、日英両方のドキュメントとあわせて更新しています。

## 参考

- `bajutsu/serve/server/object_store.py` — 本提案が拡張するサーバ側のファクトリ
- `bajutsu/serve/__init__.py` — このファクトリを `--backend=server` に配線する `_build_server_state`
- `bajutsu/serve/server/artifacts.py`、`bajutsu/serve/server/scenarios.py`、
  `bajutsu/serve/server/baselines.py` — `ObjectStore` プロトコルの利用側で、本提案による変更はない
- `bajutsu/object_store.py` — 本提案が再利用する、バックエンドを問わない `ObjectStore` プロトコル、
  `StoreURI`、`object_store_from_uri`、`GCSObjectStore`
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) —
  ホスティングサービスの成果物ストレージに S3 互換バケット（Cloudflare R2）を選定
- [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) —
  セルフホストサーバの成果物ストレージに S3 互換バケット（MinIO）を選定
- [BE-0110](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri-ja.md) —
  本提案が再利用する、バックエンドを問わない `ObjectStore` / `StoreURI` / `GCSObjectStore` を実装
