[English](BE-0110-evidence-store-uri.md) · **日本語**

# BE-0110 — URI 指定によるオブジェクトストレージへの証跡アップロード

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0110](BE-0110-evidence-store-uri-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0110") |
| 実装 PR | [#531](https://github.com/bajutsu-e2e/bajutsu/pull/531), [#636](https://github.com/bajutsu-e2e/bajutsu/pull/636), [#638](https://github.com/bajutsu-e2e/bajutsu/pull/638), [#1874](https://github.com/bajutsu-e2e/bajutsu/pull/1874) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md) |
<!-- /BE-METADATA -->

## はじめに

テスト実行後に、証跡（スクリーンショット、要素ツリー、レポート、動画、ログ）を S3 互換または GCS の
オブジェクトストレージへアップロードできるようにします。アップロード先は、単一の URI
（`s3://bucket/prefix` または `gs://bucket/prefix`）で指定します。

アップロードパスによって、クラウド側のライフサイクルポリシーが切り替わります。そのため、main
ブランチの証跡は永続保存し、feature ブランチの証跡は短期間で自動削除する運用が可能になります。

## 動機

`serve` をホスティング環境や CI から利用すると、テスト証跡はワーカーのローカルファイルシステムに
書き出されます。コンテナや VM が回収されると、証跡も失われます。監査、デバッグ、コンプライアンスの
ために証跡を永続化する必要がありますが、すべての証跡を同じ期間保持する必要はありません。

- **main ブランチへのマージ時**の証跡には、長期保存が求められます。リグレッション履歴や監査証跡と
  して使うためです。
- **feature ブランチの PR 実行時**の証跡は、レビュー期間中にだけ役立ちます。数日後に削除すれば、
  ストレージコストを抑えられます。

S3 や GCS のオブジェクトストレージは、プレフィックスに基づくライフサイクルルールを備えています。
呼び出し側がアップロードパスを制御できれば、保持ポリシーをクラウドプロバイダに委譲できます。
Bajutsu 自身には、TTL ロジックもガベージコレクションも削除コードも要りません。

現在の `serve` アーキテクチャ（BE-0015、BE-0106）には、`serve/server/object_store.py` に S3 向けの
`ObjectStore` プロトコルがあります。アーティファクトの読み出し、シナリオの保存、ビジュアル
ベースラインの書き込みに使われています。この提案では、その継ぎ目を run 完了後の証跡アップロードへ
拡張し、GCS 対応を加えます。あわせて、`serve` の設定を単一の URI フラグに統一します。

## 詳細設計

### 1. URI スキームとパース

ストレージバックエンド、バケット、プレフィックスを、一つの URI で表現します。

```
s3://my-bucket/evidence/main/
gs://my-bucket/evidence/feature/pr-123/
```

スキーム（`s3` または `gs`）がバックエンドを選びます。`gs://` は内部的に `gcs` バックエンドへ対応
づけます。スキームは `gsutil` / `gcloud` の慣例に合わせ、バックエンド名はライブラリ名に合わせて
います。最初のパスセグメントがバケット名で、残りがキープレフィックスです。パーサーは末尾の
スラッシュを正規化するので、内部では常に付いています。

パーサー（`bajutsu/object_store.py`）が `StoreURI` データクラスを生成します。

```python
@dataclasses.dataclass(frozen=True)
class StoreURI:
    backend: Literal["s3", "gcs"]
    bucket: str
    prefix: str  # 常に "/" で終わる
```

### 2. 統一 `ObjectStore` プロトコル

既存の `serve/server/object_store.py` にある `ObjectStore` プロトコルを、トップレベルモジュール
（`bajutsu/object_store.py`）へ昇格させます。`run` と `serve` の両方から使えるようにするためです。

現行のプロトコル表面（`exists`、`get_bytes`、`put_bytes`、`put_file`、`presigned_url`、
`list_keys`）は維持したまま、次の二つを追加します。

- `put_bytes` と `put_file` に `content_type` キーワード引数を追加します。アップロード時に、
  アーティファクトごとの MIME タイプを設定するためです。
- `presigned_put_url` メソッドを追加します。署名付き PUT URL を発行するためです（既存の
  `presigned_url` は GET 用）。

```python
class ObjectStore(Protocol):
    def exists(self, key: str) -> bool: ...
    def get_bytes(self, key: str) -> bytes | None: ...
    def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None: ...
    def put_file(self, key: str, path: Path, *, content_type: str = "") -> None: ...
    def presigned_url(self, key: str) -> str: ...
    def presigned_put_url(self, key: str, *, content_type: str = "", ttl: int = 3600) -> str: ...
    def list_keys(self, prefix: str) -> list[str]: ...
```

`StoreURI` から構築する実装は 2 種類です。

| 実装 | バックエンド | 依存ライブラリ |
|---|---|---|
| `S3ObjectStore` | S3 互換（AWS、MinIO、R2） | `boto3`（オプショナル） |
| `GCSObjectStore` | Google Cloud Storage | `google-cloud-storage`（オプショナル） |

ファクトリ関数（`object_store_from_uri(uri: StoreURI) -> ObjectStore`）が実装を選びます。必要な
ライブラリが未インストールなら、具体的なインストールコマンドを含むエラーを返します。

### 3. 二層アップロードアーキテクチャ

証跡のアップロードには、デプロイ構成に応じて使い分ける二つのモードがあります。設計上の要点は、
**`serve` 経由で実行する場合、Worker にクラウドの認証情報が不要**であることです。

| モード | 認証情報の保持者 | Worker の依存 | 利用場面 |
|---|---|---|---|
| **Presigned URL**（serve） | Server（control plane） | `httpx`（worker extra のランタイム依存に昇格） | `serve` 経由の実行 |
| **直接 SDK**（スタンドアロン） | Runner 自身 | `boto3` / `google-cloud-storage`（optional） | `bajutsu run --evidence-store` |

#### 3a. Presigned URL モード（serve、推奨）

Server が `ObjectStore` の認証情報を保持し、署名付き PUT URL を発行します。Worker は平文の HTTP PUT で
アップロードするので、SDK も認証情報も要りません。

```
1. Run が Worker 上で完了 → 証跡が runs/<run_id>/ にローカル保存される
2. Worker → Server:  POST /api/runs/<run_id>/upload-urls
                     { "files": ["00-login/step-1/after.png", ...] }
3. Server が各相対パスを検証（空文字、先頭 "/"、".." トラバーサルを拒否）し、
   ファイルごとに presigned PUT URL を生成
   （bucket + prefix + evidence_prefix + run_id + relative_path）
4. Server → Worker:  { "urls": { "00-login/step-1/after.png": "https://...", ... } }
5. Worker が presigned URL に HTTP PUT で各ファイルをアップロード
6. Worker → Server:  アップロード完了を報告
```

Presigned PUT URL は、S3（`generate_presigned_url("put_object", ...)`）と GCS（V4 signed URL）の
どちらでも使えます。TTL のデフォルトは 1 時間です。一般的な run のアーティファクトを一括でアップ
ロードするには十分です。

#### 3b. 直接 SDK モード（スタンドアロン `bajutsu run`）

ローカル実行やスタンドアロン CI（`serve` を介さない場合）では、Runner が SDK 経由で直接アップロード
します。

```bash
bajutsu run --evidence-store gs://bucket/feature/pr-42/ scenarios/
```

このパスでは、`boto3` または `google-cloud-storage` と、対応する認証情報が環境に必要です。処理は
後述の逐次アップロードと同じで、presigned URL を介さずに直接書き込みます。

### 4. run 完了後のアップロードステップ

いずれのモードでも、アップロードは run パイプラインが完了し、判定が確定した**後に**実行します。
このステップは、ローカルの `runs/<run_id>/` ディレクトリツリーを走査し、各ファイルをアップロード
します。設定されたプレフィックスの下に、相対パス構造をそのまま保ちます。

```
ローカル:  runs/20260702-143000/00-login/step-1/after.png
リモート:  s3://bucket/evidence/main/20260702-143000/00-login/step-1/after.png
```

重要な振る舞いは、次のとおりです。

- **アップロードの失敗は、run の判定を変更しません。** run の結果はローカルに書き出し済みで、報告も
  完了しています。アップロードエラーは警告としてログに記録し、run のサマリーに表示します。終了
  コードは、判定のまま維持します。
- **Content-Type は、ファイル拡張子から推定します**（`.png` → `image/png`、`.json` →
  `application/json`、`.html` → `text/html` など）。
- **並列アップロード**は、将来の最適化として許容します。初回実装では必須ではなく、逐次処理で十分
  です。

アップロードは、`runner/pipeline.py` のレポート生成後に、最後のステップとして組み込みます。

### 5. `serve` の設定

`serve` 経由で実行する場合、証跡ストアはジョブごとではなく、サーバーレベルの設定です。`serve`
コマンドは、次のフラグを受け付けます。

```bash
bajutsu serve --evidence-store s3://bucket/evidence/
```

環境変数でも、同等に設定できます。

```bash
BAJUTSU_EVIDENCE_STORE=s3://bucket/evidence/ bajutsu serve ...
```

Server が、SDK の認証情報と `ObjectStore` インスタンスを保持します。この `serve` インスタンスで完了
したすべてのジョブに対して、presigned PUT URL を発行します。run ID は常にパスに含まれるので、run
同士が衝突することはありません。

CI は、run を投入するときにプレフィックスをジョブのパラメータとして渡し、パスを制御します。
`/api/runs` エンドポイントは、オプショナルの `evidence_prefix` を受け付け、サーバーのベース URI に
追記します。追記の前に、Server は `evidence_prefix` を安全な相対パスセグメントとして検証します
（先頭 `/` や `..` トラバーサルを拒否します）。キーエスケープを防ぐためです。

```bash
# CI が serve に run を投入し、プレフィックスを指定する例
curl -X POST https://serve.example.com/api/runs \
  -d '{"config": "...", "evidence_prefix": "main/abc1234/"}'
```

`serve` インスタンスがバケットと認証情報を管理し、CI はパスだけを制御します。関心事が綺麗に分かれ
ます。Worker は、クラウドの認証情報に触れません。

### 6. オプショナル依存

`boto3` と `google-cloud-storage` は、どちらも必須依存にはしません。オプショナルの extras として
宣言します。必要になるのは、**Server（control plane）またはスタンドアロンモードだけ**で、Worker には
不要です。

```toml
[project.optional-dependencies]
s3 = ["boto3"]
gcs = ["google-cloud-storage"]
cloud = ["boto3", "google-cloud-storage"]
```

`uv sync --extra s3` や `uv sync --extra cloud` で、必要なものをインストールします。
`--evidence-store` を指定したのにライブラリが未インストールなら、エラーメッセージにインストール
コマンドを明記します。

### 7. 認証

Bajutsu は認証情報を**管理しません**。各 SDK の標準クレデンシャルチェーンに委譲します。

- **S3**：boto3 のクレデンシャルチェーン（環境変数、`~/.aws/credentials`、IAM ロール、OIDC）
- **GCS**：`google-cloud-storage` の ADC（環境変数、`GOOGLE_APPLICATION_CREDENTIALS`、Workload
  Identity Federation、メタデータサーバー）

`serve` 構成で、これらの認証情報が必要なのは **Server だけ**です。Worker は presigned URL 経由で
アップロードするので、クラウド SDK も認証情報も要りません。エフェメラルなコンテナにシークレットを
配布する必要がなくなります。

GCS の signed URL には、署名用の秘密鍵が要ります。ところが、Workload Identity Federation で解決した
ADC の認証情報は、アクセストークンしか持ちません。Kubernetes サービスアカウント (KSA) が Google
サービスアカウント (GSA) に成りすます KSA→GSA 連携も、この経路の一種です。秘密鍵を持たないので、
ローカルでの署名はエラーになります。

`object_store_from_uri` は、ADC を `cloud-platform` スコープで一度だけ解決し、その認証情報を
`GCSObjectStore` に渡します。署名の経路は、認証情報がローカルで署名できるかどうかで分かれます。

- キーファイルの認証情報や、なりすまし済みの認証情報は、ローカルでの署名をそのまま使います。
- KSA→GSA の認証情報はローカルで署名できません。その `service_account_email` と `access_token` を
  使い、IAM の `signBlob` API 経由で署名します。

運用上の前提が一つ増えるのは、IAM 経由で署名する場合だけです。署名する GSA 自身に、IAM の権限を
一つ追加する必要があります。付与する権限は `roles/iam.serviceAccountTokenCreator`
（または同等の `iam.serviceAccounts.signBlob` 権限）です。

## 検討した代替案

### A. 設定ファイルベースのストレージ指定

```yaml
evidenceStore:
  backend: s3
  bucket: my-bucket
  region: ap-northeast-1
  prefix: evidence/
```

バケット、リージョン、プレフィックスを設定フィールドに分ける方式です。採用しませんでした。URI
一本のほうが直感的で、`aws s3 cp` などのツールと同じ形式なので統一感があります。リージョンと
エンドポイントは、既存の `s3_client_from_env()` が参照する環境変数（`BAJUTSU_S3_REGION` /
`AWS_REGION`、`BAJUTSU_S3_ENDPOINT`）で設定できます。

### B. ダイレクト書き込み `ObjectStoreSink`（ローカル FS を経由しない）

run 中に、ローカルの `FileSink` を経由せず、オブジェクトストレージへ直接書き込む方式です。ローカル
ディスクの往復を省けますが、次の問題があります。

- ネットワーク障害が run 中に起きると、証跡を失う可能性があります。
- レポートジェネレータは、ローカルファイルを読んで `manifest.json` と `report.html` を構築します。
  そのため、パイプラインの変更が大きくなります。
- ローカルコピーがないので、デバッグが困難になります。

run 完了後のアップロードのほうが、シンプルで安全です。既存のパイプラインを変更せずに済みます。

### C. 独立したアップロードコマンド（`bajutsu upload`）

既存の `runs/` ディレクトリをアップロードする、スタンドアロンのコマンドです。統合型のアップロードと
共存できますが、CI パイプラインに余分なステップが増えます。`--evidence-store` フラグなら追加の
ステップは不要で、設定し忘れるリスクもありません。

過去の run を遡ってアップロードしたい場合などに、将来スタンドアロンのコマンドを追加しても、この
設計とは矛盾しません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] URI スキームのパースと `StoreURI` データクラス
- [x] `ObjectStore` プロトコルのトップレベルモジュールへの昇格
- [x] `S3ObjectStore` 実装（既存コードの再利用、`presigned_put_url` と `content_type` 追加）
- [x] `GCSObjectStore` 実装（V4 signed PUT URL を含む）
- [x] Presigned URL アップロードエンドポイント（`POST /api/runs/<run_id>/upload-urls`）
- [x] Worker 側の HTTP PUT アップローダー（presigned URL モード）
- [x] 直接 SDK アップロードのフォールバック（スタンドアロン `bajutsu run` モード）
- [x] run 完了後アップロードステップ（モード選択）— `runner/pipeline.py` ではなく、各モードが実際に使う
      継ぎ目に配置した。スタンドアロンは CLI 層（`run.py`、判定の後、`--zip` と同じ扱い）、serve の
      presigned モードは `bajutsu worker` の HTTP ループで run の後、`console.log` と並べて配線
- [x] `serve` の `--evidence-store` フラグと `BAJUTSU_EVIDENCE_STORE` 環境変数
- [x] `bajutsu run` の `--evidence-store` CLI フラグ（`BAJUTSU_EVIDENCE_STORE` 環境変数対応）
- [x] `/api/run` エンドポイントの `evidence_prefix` パラメータ
- [x] オプショナル依存の宣言（`s3` / `gcs` / `cloud` extras）
- [x] テスト — スタンドアロン（URI パース、S3/GCS ストア（`content_type` と presigned GET/PUT 生成を
      含む）、`upload_tree`）、serve エンドポイント（`generate_upload_urls` のキー生成・検証、両方の
      HTTP シェル、`evidence_prefix` の受け渡し、ストアの配線）、Worker アップローダー（ファイル列挙、
      content-type を合わせた presigned PUT、ファイル単位の失敗のベストエフォート）を実サーバーに対して検証
- [x] ドキュメント — `run --evidence-store` と `serve --evidence-store` を `docs/cli.md` に、presigned
      な serve トポロジを `docs/self-hosting.md` に記載（英語・日本語の両方）

ログ:

- **スライス 1 — 基盤 + スタンドアロンの直接 SDK アップロード**: `ObjectStore` プロトコルと
  `S3ObjectStore` をトップレベルの `bajutsu/object_store.py` へ昇格し（`serve/server/object_store.py`
  はそこから再エクスポート）、`StoreURI` と `parse_store_uri`、`GCSObjectStore`、書き込みメソッドの
  `content_type`、`presigned_put_url`（S3 と GCS の V4）、`object_store_from_uri` ファクトリ、
  `upload_tree` ヘルパを追加した。`bajutsu run --evidence-store` を配線し、判定の後に run ツリーを
  アップロードする（失敗は警告のみで pass/fail を覆さない）。`s3` / `gcs` / `cloud` extras を追加した。
  presigned URL の serve パス（エンドポイント、Worker の HTTP PUT アップローダー、
  `serve --evidence-store`、`evidence_prefix`）は後続のスライスとする。
- **スライス 2a — presigned な serve エンドポイント（サーバー側）**: `POST /api/runs/<run_id>/upload-urls`
  の操作（`generate_upload_urls`）を追加しました。サーバーが証跡ストアの認証情報を保持し、ファイルごとに
  presigned PUT URL を返します。run ID・run ごとの `evidence_prefix`・各ファイルパスを再検証するので、
  Worker が run のキー名前空間の外へ書き込むことはできません（ストア未設定なら空の URL を返すため、
  Worker は常に問い合わせられます）。このルートを stdlib ハンドラと FastAPI アプリの両方に配線しました。
  `serve --evidence-store` フラグと `BAJUTSU_EVIDENCE_STORE` 環境変数（CLI で `EvidenceTarget` に解決し、
  不正な URI や SDK 欠如の場合は早期に失敗）、`/api/run` の `evidence_prefix` パラメータ（検証のうえ `Job` と
  ジョブスペックに載せる）、`EvidenceTarget` と `evidence_target_from_uri` / `content_type_for` ヘルパを
  追加しました。Worker 側の HTTP PUT アップローダーと serve トポロジのドキュメントは次のスライスとします。
- **スライス 2b — Worker の presigned アップローダー + ドキュメント**（項目を完了）: `bajutsu worker` の
  ループを配線し、完了した run の証跡を presigned エンドポイント経由でアップロードするようにしました。run と
  `console.log` の後に run ツリーを列挙し、ファイルごとに PUT URL を要求し、content-type を合わせて平文 HTTP で
  アップロードします（自分のクラウド認証情報は持ちません）。ベストエフォートなので、失敗しても警告のみです。
  `serve --evidence-store` と presigned な serve トポロジを `docs/cli.md` と `docs/self-hosting.md`
  （英語・日本語）に記載しました。これで BE-0110 は完了です。

## 参考

- `bajutsu/evidence.py` — `EvidenceSink` / `FileSink`（現行のローカル書き出しパス）
- `bajutsu/serve/server/object_store.py` — 既存の `ObjectStore` プロトコルと `S3ObjectStore`
- `bajutsu/serve/artifacts.py` — `ArtifactStore` プロトコル（読み出し側）
- `bajutsu/report/archive.py` — run ディレクトリの ZIP アーカイブ
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) — Web UI の公開ホスティング（リモートストレージの動機となるサーバー構成）
- [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model-ja.md) — 完了後ワーカーモデル（アップロードが組み込まれる非同期ジョブパイプライン）
- [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) — run レポートの ZIP エクスポート（関連：ポータブルなアーティファクトパッケージング）
