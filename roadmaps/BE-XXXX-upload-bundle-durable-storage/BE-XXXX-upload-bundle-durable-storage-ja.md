[English](BE-XXXX-upload-bundle-durable-storage.md) · **日本語**

# BE-XXXX — アップロードした zip config バンドルをオブジェクトストレージへ永続化する（ホスト型 serve 向け）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-upload-bundle-durable-storage-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | config の取得元 |
| 関連 | [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)、[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)、[BE-0204](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support-ja.md)、[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)、[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)、[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) は、ブラウザ
の利用者が `.zip`（config、シナリオ、ビルド済みアプリバイナリ）をアップロードし、それを `serve`
の稼働中の config としてバインドできるようにしました。この設計では、アップロードされた内容は
リクエストを受けたプロセスのローカルディスクにしか残りません。`POST /api/upload` は
`state.uploads_dir` 配下の serve 専有ディレクトリへ展開するだけであり、BE-0073 自身の「スコープ
外」節にも「アップロードは一時的なものであり、永続化やバージョン管理は Git source の役割であ
る」と明記されています。

この設計は、BE-0073 が対象とする単一 Mac の Tier A 向け `serve` では妥当です。しかし、ホスト型
でマルチテナントの `server` バックエンド（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）
では成立しません。BE-0015 は、ロードバランサーの背後で**ステートレスかつオートスケールする
レプリカ**として動くことを前提に設計されており、まさにこの理由からセッションとジョブを
Postgres へ移しています（「セッションは再起動を越えて維持され、レプリカをまたいで有効であ
る」）。ところが、アップロードされた zip の内容は、いまだにアップロードを受け付けたレプリカの
ローカルディスクにしか存在しません。本項目は、この残された 1 つの隙間を塞ぎます。アップロードさ
れたバンドルの内容を、BE-0063 の Git source がすでに持っている性質と同じように、**耐久性を持
ち、内容アドレス方式で参照できる**ものにします。これにより、レプリカが再作成されても、あるいは
アップロードを受け付けたレプリカ以外のどのレプリカからでも、内容を解決できるようになります。こ
の設計は、[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)（提案段階）
と直接組み合わさります。BE-0225 は、登録した**プロジェクト**が Git やローカルファイルだけでな
く、アップロードされたバンドルにもバインドできるようにするために、まさにこの種の耐久性を持つロ
ケータを必要としています。

## 動機

BE-0073 の「設計上一時的である」という判断には、デプロイ環境に関する 2 つの事実が異なる角度から
効いてきます。

1. **オートスケールとレプリカの再作成。** `server` バックエンドでは、レプリカが（再デプロイ、ク
   ラッシュ、スケールダウンによって）再作成されることがあります。また、アップロードを受け付けた
   レプリカとは別のレプリカにリクエストが届くこともあります（これは BE-0015 がセッションとジョ
   ブをデータベースへ移した理由そのものです）。現在、展開済みのバンドルは、それを展開したレプリ
   カの `state.uploads_dir`（`ServeState` のフィールドであり、プロセス内のローカルディスク上の実
   体です）にしか存在しません。別のレプリカや、再起動後の同じレプリカに届いた run リクエスト
   は、materialize する対象を何も見つけられず、バインドされた config は何も言わずに解決を止めて
   しまいます。
2. **BE-0225 のマルチプロジェクトハブには、生きたパスではなく耐久性のあるロケータが必要です。**
   BE-0225（提案段階）は、プロジェクトを config source への名前付きバインディングとして登録しま
   す。これは `kind` と小さなロケータのレコードで表され、データベースの `projects` 行（ホスト型
   の場合）かローカルの JSON ストア（データベースがない場合）のどちらかに永続化されます。Git
   source の場合、BE-0225 が保存するロケータは解決済みの commit SHA です。プロジェクトを後で開き
   直すと、必要なレプリカ上でその SHA から再度 clone します。ところが zip source には、今のとこ
   ろこれに相当する耐久性のあるものが何もありません。あるのは、展開したレプリカが失われた瞬間に
   無効になる、一時的なローカルの展開パスだけです。BE-0225 自身の設計も、この先を見越しています
   （「ホスト型バックエンドは、後で〔zip 種別のレコード〕を自前の `ScenarioStore` 経由で解決でき
   る」）。ただし、その `ScenarioStore` はまだ存在しません。本項目が実現するのは、まさにこの見越
   された解決を可能にすることです。

この修正は、コードベースがすでに 2 度採用している形をそのままなぞるため、新しい仕組みを何も追加
しません。

- **内容アドレス方式。** BE-0073 は、証跡のために zip の sha256 をすでに計算しており（`manifest.json`
  に記録されます）、このハッシュは、Git source にとっての解決済み commit SHA と同じ役割を果たす、
  耐久性があり衝突しにくい鍵として、そのまま使えます。
- **オブジェクトストレージの seam。** [BE-0204](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support-ja.md)
  は、すでに `server` バックエンドに認証済みの `ObjectStore`（`BAJUTSU_SERVER_STORE`、`s3://` /
  `gs://`）を与えています。これは、「レプリカより長生きし、どのレプリカからも読み取れる必要があ
  る」という、まさにこの種類のデータのためのものであり、今日は run のアーティファクト、シナリ
  オ、visual baseline に使われています。アップロードされたバンドルの内容もこれと同じ種類のデー
  タであり、4 つ目の独自ストアではなく、同じ seam の背後に置くべきものです。

pass/fail、runner、ドライバ、シナリオスキーマのいずれにも触れません。決定的な `run` より前の取得
段階の配線にすぎないため、prime directive 1〜3（[CLAUDE.md](../../CLAUDE.md)）はそのまま成り立
ちます。また、AI provider・モデル設定の永続化（別に追跡されている、
[BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings-ja.md)
の保留中の hosted DB バックエンド化）にも触れません。

## 詳細設計

作業は、アップロード時の永続化、その永続化されたストアからのオンデマンドな materialize、利用者
（BE-0225）が保存する内容アドレス方式のロケータの形、オブジェクトストレージを設定していない場合
の段階的縮退という 4 つの単位に MECE に分かれます。

### 1. アップロード時に生の zip を内容アドレス方式で永続化する

`bajutsu/serve/operations/upload.py` が `POST /api/upload` を扱う現在の挙動（一時ファイルへスト
リームし、検証し、`state.uploads_dir` 配下の新規ディレクトリへ展開し、稼働中の config としてバイ
ンドする）はそのまま変えません。加えて、**`BAJUTSU_SERVER_STORE` が設定されている場合**には、
BE-0073 がすでに計算している sha256 から導いた鍵（例：`<prefix>uploads/<sha256>.zip`）のもとに、
生の（圧縮されたままの）zip バイトをそのストアへ書き込みます。展開済みツリーではなく生の zip を
書き込むのは、オブジェクトを小さく保つためであり、zip-slip・zip-bomb の検証を、一度だけ信頼して
再利用するものではなく、各回の materialize（後述）で毎回繰り返すステップとして保つためであり、
BE-0060 のエクスポートが run の束をすでに単一の zip アーティファクトとして扱っている形にも合わ
せるためです。内容アドレス方式にすることで、同一バンドルの再アップロードは書き込みを伴わない
no-op になります。鍵はすでに存在しているため、そのリクエストを処理したレプリカでのローカル展開
だけが今日と同じように行われます。

### 2. オンデマンドの materialize、耐久性のあるストアを裏に持つローカルキャッシュ

`UploadBundleSource`（BE-0073）に、想定されるローカル展開ディレクトリが存在しないときに試す、2
番目の解決経路を追加します。内容アドレス方式の鍵でオブジェクトストレージから zip バイトを取得
し、新規アップロードのときとまったく同じように `state.uploads_dir` 配下の新規ディレクトリへロー
カル展開し、BE-0073 がすでに適用している zip-slip・zip-bomb・パス封じ込めの検証を同じく走らせま
す。これにより、ローカルの `uploads_dir` は耐久性のあるオブジェクトストレージの手前に立つキャッ
シュになります。これは、[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)
のローカルで内容アドレス方式の Git checkout キャッシュ（`~/.cache/bajutsu/gitsrc/...`）が、リ
モートの origin に対してすでに持っている関係と同じです。耐久性を持つのは、解決済みの SHA（Git
の場合）や sha256（zip の場合）であり、ローカルディスク上の checkout や展開済みツリーは常に破棄
可能で、そこから再現できます。この仕組みによって、新しく起動した、あるいはこれまでと異なるレプ
リカに届いたリクエストが、自分自身が受け取っていないバンドルでも materialize できるようになりま
す。

### 3. zip という config source 種別のための、内容アドレス方式で耐久性のあるロケータ

config source のレコードの zip 版（BE-0225 がプロジェクトごとに保存する `kind` とロケータの組）
を、ローカルの展開パスではなく `{kind: "zip", sha256, filename, size}` にします。これが、BE-0225
の `ProjectRegistry` が永続化する耐久性のあるポインタになります（データベースがある場合は
`projects` 行、ない場合はローカルの JSON ストア）。本項目はデータベーステーブルを新設しません。
永続化する価値のあるポインタにするだけであり、これは Git source のロケータが checkout パスでは
なく commit SHA であることと、まったく同じ形です。`state.bind_upload`（プロジェクトを介さない、
素の `serve --config` やアップロードのフローにおける、*いま稼働中の* config）には影響がなく、今
日と同じくローカルパスでバインドし続けます。内容アドレス方式のレコードが意味を持つのは、耐久性の
ある何か、つまり BE-0225 のプロジェクトが、再起動を越えてバンドルを名指す必要が生じたときです。

### 4. オブジェクトストレージを設定していない場合の段階的縮退

`BAJUTSU_SERVER_STORE` を設定していないローカル `serve`（デフォルトであり、ホスト型以外のすべて
の Tier A・セルフホストのデプロイ）では、挙動はまったく変わりません。アップロードは、BE-0073 が
出荷したときと同じだけ一時的なままです。これは、任意のストア
（[BE-0204](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support-ja.md)）や
設定（[BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings-ja.md)）
についてすでに確立されているゼロコンフィグの前例と一致します。耐久性を持ち、レプリカをまたいで
解決できるようにする仕組みは加算的であり、運用者がアーティファクト・シナリオ・baseline のために
オブジェクトストレージの seam をすでに選んでいる場合にだけ働きます。

### 保持期間

内容アドレス方式にすることで、自然な重複排除が得られます（同じバイト列の再アップロードは書き込
みを伴わない no-op です）。実際の削除は、バケット自身の lifecycle policy に委ねます。これは、
`bajutsu/object_store.py` のモジュール docstring が、この seam が支えるすべての URI アドレス方式
のストアを統べる保持機構として、すでに明記しているものと同じです。新しい保持や garbage
collection のコードは追加しません。

## 検討した代替案

- **zip（または展開済みツリー）を Postgres の BLOB として保存する。** 見送りました。BE-0015 のス
  キーマは、大きなバイナリのペイロードをすでにデータベースの外に置いています（アーティファクト、
  シナリオ、baseline はいずれも BE-0204 でオブジェクトストレージへ移り、テーブルには入れていませ
  ん）。数メガバイトのアプリバイナリは、まさに Postgres が不向きなペイロードの形です。もう一つの
  インフラ依存を避けることより、既存のストレージ seam との一貫性の方を重視しました。
- **生の zip ではなく展開済みツリーを永続化する。** 見送りました。展開済みツリーはより大きなオブ
  ジェクトになります（1 つではなく多数の小さな鍵になります）。また、将来の materialize が、展開
  時にしか走らない zip-slip・zip-bomb の検証をスキップしてしまう可能性があります。materialize の
  たびに生の zip から再展開すれば、その検証を一度だけ信頼するのではなく、常に効かせ続けられます。
- **永続的なシナリオ保存を、BE-0073 が当初意図していたとおり Git source だけに限定する。** 再利
  用可能でバージョン管理されたスイートを望むチームにとっては、これが今も正しい答えです。この点は
  本項目でも変わりません。しかし、BE-0225 がすでに設計している場合（Git リポジトリを背後に持たな
  い一度限りのアップロードされたバンドルにバインドされたプロジェクトが、それでもレプリカの再起動
  を越えて生き残る必要がある場合）には力になりません。2 つの source は別々の問題を解いており、補
  完的な関係のままです。
- **BE-0225 のプロジェクトレジストリの永続化まで本項目の責務にする。** スコープの広げすぎとして
  見送りました。BE-0225 はすでに「どのプロジェクトがどの source レコードを指すか」と「そのレコー
  ドをどこに保存するか（データベース行かローカル JSON か）」を所有しています。本項目の役割はもっ
  と狭く、zip source そのものを、BE-0225（や将来のほかの利用者）が保存できる安定したポインタを持
  つ、耐久性のある内容アドレス方式のものにすることだけです。両者を束ねると、BE-0225 が単独で発展
  させられるはずのレジストリの関心事に、オブジェクトストレージの関心事を結合してしまいます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 1 — `BAJUTSU_SERVER_STORE` が設定されているとき、アップロードされた生の zip を sha256 で鍵
      付けしてオブジェクトストレージへ永続化する。
- [ ] 2 — `UploadBundleSource` に、ローカルの展開ディレクトリが存在しないときのオブジェクトスト
      レージからの取得・展開フォールバックを追加し、materialize のたびに BE-0073 の
      zip-slip・zip-bomb・パス封じ込めの検証を再実行する。
- [ ] 3 — zip の config source レコードを、ローカルパスではなく内容アドレス方式（`sha256` +
      `filename` + `size`）にし、BE-0225 の `ProjectRegistry`（や将来のほかの利用者）が永続化でき
      る耐久性のあるポインタにする。
- [ ] 4 — `BAJUTSU_SERVER_STORE` を設定していない場合の挙動が変わらないことを確認する。

## 参考

- [BE-0073 — config・シナリオ・アプリバイナリを zip でまとめてアップロードし Web UI から実行す
  る](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) — 本項目が耐久性
  を持たせる、一時的なアップロード経路。内容アドレス方式の鍵として再利用する sha256 の証跡ハッ
  シュ。
- [BE-0063 — Git リポジトリ + ref から config（とシナリオ一式）を読み込
  む](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) — 本項目が zip アップロードに
  対してなぞる、耐久性のある origin の手前にローカルキャッシュを置く形。
- [BE-0204 — サーバー側オブジェクトストレージの GCS 対
  応](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support-ja.md) — 本項目の永
  続化と materialize が読み書きする `BAJUTSU_SERVER_STORE` / `ObjectStore` の seam。
- [BE-0108 — ホスティング時は config の取得元をアップロードと Git だけに絞
  る](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
  — アップロードが、ホスト型デプロイでしっかり支えるべき 2 つの config source のうち 1 つである
  理由。
- [BE-0225 — serve の config プロジェクトハブ（登録・一覧・切替・実
  行）](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) — `ProjectRegistry` に永続
  化するための、耐久性を持ち内容アドレス方式の zip ロケータを必要とする利用者。
- [BE-0015 — Web UI の公開ホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)
  — 本項目のレプリカ間解決が対象とする、ステートレスでオートスケールするレプリカのトポロジ。
- [BE-0184 — serve の AI プロバイダー設定を再起動をまたいで永続化す
  る](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings-ja.md)
  — 設定（シナリオの内容ではなく）のための、別に追跡されている姉妹の隙間。本項目では明示的にスコ
  ープ外です。
- `bajutsu/object_store.py`（`ObjectStore`、`object_store_from_uri`、`upload_tree`）、
  `bajutsu/serve/operations/upload.py`、`bajutsu/serve/uploads.py`（`extract_bundle`）、
  `bajutsu/serve/state.py`（`uploads_dir`、`bind_upload`/`release_upload`）。
